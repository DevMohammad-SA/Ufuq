"""Synthetic account-management scope, authority and data-preservation tests."""

from datetime import date
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from accounts.models import Role, User
from participants import capabilities, management_views, participant_lifecycle, student_management
from participants.models import (
    CircleAttendance, Group, MeetingAttendance, MeetingNote, Participant,
    PointsLedgerEntry, PointsResetSnapshot, StoreOrder, StoreProduct,
    SupervisorCapability, SupervisorReportPermission, TaskSubmission,
    WeeklyActivityAttendance, WeeklyTask,
)


class DelegatedStudentManagementTests(TestCase):
    password = "SyntheticStudent123!"

    def setUp(self):
        self.general = self.user("student-mgmt-general", Role.GENERAL_SUPERVISOR)
        self.system = self.user("student-mgmt-system", Role.SUPERADMIN)
        self.delegate = self.user("student-mgmt-group", Role.GROUP_SUPERVISOR)
        self.limited = self.user("student-mgmt-limited", Role.LIMITED_SUPERVISOR)
        self.no_grant = self.user("student-mgmt-ungranted", Role.LIMITED_SUPERVISOR)
        self.group_a = Group.objects.create(name="إدارة ألف")
        self.group_b = Group.objects.create(name="إدارة باء")
        self.outside_group = Group.objects.create(name="بيئة غير مسندة")
        self.group_a.supervisor.add(self.delegate)
        self.group_b.supervisor.add(self.delegate)
        for actor in (self.delegate, self.limited):
            SupervisorCapability.objects.create(supervisor=actor, manage_students=True, granted_by=self.general)
        self.student = self.make_student("9100000001", self.group_a)
        self.second = self.make_student("9100000002", self.group_b)
        self.outside = self.make_student("9100000003", self.outside_group)
        self.ungrouped = self.make_student("9100000004", None)
        self.archived = self.make_student("9100000005", self.group_a, active=False)
        self.edit_url = reverse("participants:participant_edit", kwargs={"pk": self.student.pk})
        self.delete_url = reverse("participants:participant_delete", kwargs={"pk": self.student.pk})
        self.restore_url = reverse("participants:participant_restore", kwargs={"pk": self.student.pk})

    def user(self, username, role, **extra):
        return User.objects.create_user(username=username, role=role, full_name=username, password=self.password, **extra)

    def make_student(self, identity, group, *, active=True):
        user = User.objects.create_user(
            national_id=identity, role=Role.PARTICIPANT, full_name="طالب " + identity,
            password=self.password, is_active=active, must_set_password=False,
        )
        return Participant.objects.create(
            user=user, group=group, academic_stage="grade_7", phone="0501111111",
            guardian_phone="0502222222", points=93, miles=930, purchase_points=29,
        )

    def payload(self, **changes):
        return {"full_name": "الاسم المعدل", "national_id": self.student.user.national_id,
                "group": str(self.group_b.pk), "academic_stage": "grade_8",
                "phone": "0503333333", "guardian_phone": "0504444444", **changes}

    def management_url(self):
        return reverse("participants:student_management")

    def list_context(self, actor=None, **params):
        request = RequestFactory().get("/participants/students/manage/", params)
        request.user = actor or self.delegate
        captured = {}

        def render(request, template, context):
            captured.update(context)
            list(context["students"])
            list(context["groups"])
            return HttpResponse()

        with patch.object(student_management, "render", side_effect=render):
            with patch("participants.views.build_navbar", return_value=[]) as navbar:
                response = student_management.StudentManagementListView.as_view()(request)
                if response.status_code == 200:
                    navbar.assert_called_once_with(request.user, "student_management")
        return response, captured

    def ids(self, context):
        return {student.pk for student in context["students"]}

    def audit_count(self):
        return LogEntry.objects.count()

    def test_groups_include_all_current_assignments_not_prefetched_cache(self):
        cached = User.objects.prefetch_related("group_set").get(pk=self.delegate.pk)
        self.assertEqual(set(student_management.student_management_groups(cached)), {self.group_a, self.group_b})
        self.group_a.supervisor.remove(self.delegate)
        self.assertEqual(list(student_management.student_management_groups(cached)), [self.group_b])
        self.assertEqual(set(student_management.student_management_participants(cached)), {self.second})

    def test_limited_general_and_system_have_all_environments_and_ungrouped_students(self):
        for actor in (self.limited, self.general, self.system):
            with self.subTest(role=actor.role):
                self.assertEqual(student_management.student_management_groups(actor).count(), 3)
                self.assertEqual(student_management.student_management_participants(actor).count(), 5)
                response, context = self.list_context(actor, status="all")
                self.assertEqual(response.status_code, 200)
                self.assertIn(self.ungrouped.pk, self.ids(context))
                self.assertEqual(context["can_restore_students"], actor.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN))

    def test_ungranted_and_anonymous_are_fail_closed(self):
        for actor in (self.no_grant, AnonymousUser()):
            self.assertFalse(student_management.can_manage_students(actor))
            self.assertFalse(student_management.student_management_groups(actor).exists())
            self.assertFalse(student_management.student_management_participants(actor).exists())
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login_supervisor"), response["Location"])
        self.client.force_login(self.no_grant)
        for url in (self.edit_url, self.delete_url, self.restore_url):
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 403)

    def test_group_without_environments_cannot_open_list_or_any_target(self):
        self.group_a.supervisor.remove(self.delegate)
        self.group_b.supervisor.remove(self.delegate)
        self.client.force_login(self.delegate)
        self.assertEqual(self.client.get(self.management_url()).status_code, 403)
        for url in (self.edit_url, self.delete_url):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, self.payload(confirm="yes")).status_code, 404)

    def test_server_status_search_and_group_filters_never_broaden_scope(self):
        _, context = self.list_context(status="all")
        self.assertEqual(self.ids(context), {self.student.pk, self.second.pk, self.archived.pk})
        _, context = self.list_context(status="inactive")
        self.assertEqual(self.ids(context), {self.archived.pk})
        _, context = self.list_context(status="unknown", q=self.second.user.national_id)
        self.assertEqual(self.ids(context), {self.second.pk})
        self.assertEqual(context["status"], "active")
        for group in (str(self.outside_group.pk), "bad-id", "9999999999999999999999999"):
            _, context = self.list_context(group=group, status="all")
            self.assertFalse(self.ids(context))
        _, context = self.list_context(group=str(self.group_b.pk))
        self.assertEqual(self.ids(context), {self.second.pk})

    def test_list_query_and_context_are_basic_only_not_balances_or_program_history(self):
        with CaptureQueriesContext(connection) as queries:
            response, context = self.list_context(self.limited)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(context["can_view_reports"])
        self.assertFalse(context["can_manage_accounts"])
        self.assertTrue(context["can_manage_students"])
        for student in context["students"]:
            self.assertTrue({"points", "miles", "purchase_points"}.issubset(student.get_deferred_fields()))
        forbidden = ("points", "miles", "purchase_points", "attendance", "tasksubmission", "weeklytask", "pointsledger", "pointsreset")
        for query in queries:
            for name in forbidden:
                self.assertNotIn(name, query["sql"].lower())
        for key in ("points", "balances", "attendance", "submissions", "ledger", "reports"):
            self.assertNotIn(key, context)

    def test_list_uses_thirty_item_pagination_and_safe_page_values(self):
        for index in range(30):
            self.make_student(str(9200000000 + index), self.group_a)
        _, first = self.list_context()
        self.assertEqual(len(first["students"]), 30)
        self.assertEqual(first["page_obj"].paginator.count, 32)
        _, invalid = self.list_context(page="bad")
        self.assertEqual(invalid["page_obj"].number, 1)
        _, last = self.list_context(page="9999")
        self.assertEqual(len(last["students"]), 2)

    def test_list_http_methods_cache_and_real_route(self):
        self.client.force_login(self.delegate)
        for method in (self.client.get, self.client.head):
            response = method(self.management_url())
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertIn("private", response["Cache-Control"])
        for method in (self.client.post, self.client.put, self.client.delete, self.client.options):
            self.assertEqual(method(self.management_url()).status_code, 405)

    def test_outside_posted_id_and_ungrouped_are_denied_before_lookup(self):
        self.client.force_login(self.delegate)
        before = Participant.objects.values().get(pk=self.outside.pk)
        for target in (self.outside, self.ungrouped):
            for name in ("participant_edit", "participant_delete"):
                url = reverse("participants:" + name, kwargs={"pk": target.pk})
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, self.payload(confirm="yes")).status_code, 404)
        self.assertEqual(Participant.objects.values().get(pk=self.outside.pk), before)
        self.assertEqual(self.audit_count(), 0)

    def test_foreign_or_empty_posted_group_cannot_move_student(self):
        self.client.force_login(self.delegate)
        for group in (str(self.outside_group.pk), ""):
            response = self.client.post(self.edit_url, self.payload(group=group))
            self.assertEqual(response.status_code, 200)
            self.assertIn("group", response.context["form"].errors)
            self.student.refresh_from_db()
            self.assertEqual(self.student.group_id, self.group_a.pk)
        self.assertEqual(self.audit_count(), 0)

    def test_edit_allows_second_assigned_group_and_preserves_sensitive_fields(self):
        self.client.force_login(self.delegate)
        before_actor = User.objects.values().get(pk=self.delegate.pk)
        before_user = User.objects.values().get(pk=self.student.user_id)
        before_balances = (self.student.points, self.student.miles, self.student.purchase_points)
        response = self.client.get(self.edit_url)
        self.assertEqual(set(response.context["form"].fields["group"].queryset), {self.group_a, self.group_b})
        self.assertFalse(response.context["can_manage_accounts"])
        self.assertTrue(response.context["can_manage_students"])
        response = self.client.post(self.edit_url, self.payload(
            role=Role.SUPERADMIN, is_staff="on", is_superuser="on", is_active="", password="injected",
            must_set_password="on", points="0", miles="0", purchase_points="0", username="injected",
        ))
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        user = User.objects.values().get(pk=self.student.user_id)
        self.assertEqual(user, {**before_user, "full_name": "الاسم المعدل"})
        self.assertEqual(self.student.group_id, self.group_b.pk)
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), before_balances)
        self.assertEqual(User.objects.values().get(pk=self.delegate.pk), before_actor)
        self.assertEqual(set(student_management.student_management_groups(self.delegate)), {self.group_a, self.group_b})
        self.assertEqual(self.audit_count(), 1)

    def test_limited_edit_without_report_grant_redirects_to_management(self):
        self.client.force_login(self.limited)
        response = self.client.post(self.edit_url, self.payload())
        self.assertEqual(response["Location"], self.management_url())
        self.assertFalse(capabilities.can_view_reports(self.limited))

    def test_general_and_system_legacy_edit_and_disable_redirects_are_preserved(self):
        for actor in (self.general, self.system):
            self.client.force_login(actor)
            response = self.client.post(self.edit_url, self.payload())
            self.assertEqual(response["Location"], reverse("participants:student_detail", kwargs={"pk": self.student.pk}))
            response = self.client.post(self.delete_url, {"confirm": "yes"})
            self.assertEqual(response["Location"], reverse("participants:participants_data") + "?status=inactive")

    def test_explicit_report_denial_keeps_management_but_changes_redirect(self):
        SupervisorReportPermission.objects.create(supervisor=self.general, view_reports=False, prepare_reports=False, granted_by=self.system)
        self.client.force_login(self.general)
        response = self.client.post(self.edit_url, self.payload())
        self.assertEqual(response["Location"], self.management_url())
        response = self.client.post(self.delete_url, {"confirm": "yes"})
        self.assertEqual(response["Location"], self.management_url() + "?status=inactive")
        self.assertTrue(capabilities.can_manage_students(self.general))
        self.assertFalse(capabilities.can_view_reports(self.general))

    def test_protected_role_staff_superuser_and_self_targets_never_change(self):
        targets = [self.user("protected-role", Role.GROUP_SUPERVISOR),
                   self.user("protected-staff", Role.PARTICIPANT, is_staff=True),
                   self.user("protected-super", Role.PARTICIPANT, is_superuser=True), self.delegate]
        self.client.force_login(self.delegate)
        for user in targets:
            participant = Participant.objects.create(user=user, group=self.group_a, academic_stage="grade_7")
            before = User.objects.values().get(pk=user.pk)
            for name in ("participant_edit", "participant_delete"):
                url = reverse("participants:" + name, kwargs={"pk": participant.pk})
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, self.payload(confirm="yes")).status_code, 404)
            self.assertEqual(User.objects.values().get(pk=user.pk), before)
        self.assertEqual(self.audit_count(), 0)

    def test_restore_never_delegates_even_with_manage_students(self):
        for actor in (self.delegate, self.limited):
            self.client.force_login(actor)
            self.assertTrue(capabilities.can_manage_students(actor))
            self.assertEqual(self.client.get(self.restore_url).status_code, 403)
            self.assertEqual(self.client.post(self.restore_url, {"confirm": "yes"}).status_code, 403)

    def test_open_page_revocation_activity_role_and_assignment_changes_are_enforced(self):
        for change in ("grant", "activity", "role", "assignment"):
            with self.subTest(change=change):
                User.objects.filter(pk=self.delegate.pk).update(role=Role.GROUP_SUPERVISOR, is_active=True)
                SupervisorCapability.objects.filter(supervisor=self.delegate).update(manage_students=True)
                self.group_a.supervisor.add(self.delegate)
                self.client.force_login(User.objects.get(pk=self.delegate.pk))
                self.assertEqual(self.client.get(self.edit_url).status_code, 200)
                if change == "grant":
                    SupervisorCapability.objects.filter(supervisor=self.delegate).update(manage_students=False)
                elif change == "activity":
                    User.objects.filter(pk=self.delegate.pk).update(is_active=False)
                elif change == "role":
                    User.objects.filter(pk=self.delegate.pk).update(role=Role.PARTICIPANT)
                else:
                    self.group_a.supervisor.remove(self.delegate)
                response = self.client.post(self.edit_url, self.payload())
                self.assertIn(response.status_code, (302, 403, 404))
                if response.status_code == 302:
                    self.assertIn("login", response["Location"])
                self.student.refresh_from_db()
                self.assertEqual(self.student.group_id, self.group_a.pk)
        self.assertEqual(self.audit_count(), 0)

    def test_stale_user_demotion_and_disabled_db_state_fail_closed(self):
        stale = User.objects.get(pk=self.general.pk)
        User.objects.filter(pk=stale.pk).update(role=Role.GROUP_SUPERVISOR)
        self.assertFalse(capabilities.can_manage_students(stale))
        self.assertFalse(student_management.student_management_participants(stale).exists())
        User.objects.filter(pk=self.delegate.pk).update(is_active=False)
        self.assertFalse(capabilities.can_manage_students(self.delegate))
        self.assertFalse(student_management.student_management_groups(self.delegate).exists())

    def test_locked_users_optional_guard_does_not_delegate_supervisor_management(self):
        from django.core.exceptions import PermissionDenied
        from django.db import transaction
        with transaction.atomic():
            with self.assertRaises(PermissionDenied):
                management_views.locked_users(self.delegate.pk)
            actor, target = management_views.locked_users(self.delegate.pk, self.student.user_id, actor_check=capabilities.can_manage_students)
            self.assertEqual(actor.pk, self.delegate.pk)
            self.assertEqual(target.pk, self.student.user_id)
        self.client.force_login(self.delegate)
        for name in ("supervisor_list", "supervisor_add"):
            self.assertEqual(self.client.get(reverse("participants:" + name)).status_code, 403)

    def test_confirm_is_exactly_one_yes_and_get_never_mutates(self):
        self.client.force_login(self.delegate)
        self.assertEqual(self.client.get(self.delete_url).status_code, 200)
        for value in ([], ["yes", "yes"], ["yes", "no"], ["true"], ["1"]):
            response = self.client.post(self.delete_url, {"confirm": value})
            self.assertEqual(response.status_code, 400)
        self.student.user.refresh_from_db()
        self.assertTrue(self.student.user.is_active)
        self.assertEqual(self.audit_count(), 0)

    def test_disable_preserves_all_records_balances_password_and_existing_history(self):
        student = self.student
        CircleAttendance.objects.create(participant=student, date=date(2026, 10, 1), attended=True)
        MeetingAttendance.objects.create(participant=student, week_start_date=date(2026, 10, 2), attended=True, recorded_by=self.delegate)
        WeeklyActivityAttendance.objects.create(participant=student, date=date(2026, 10, 3), attended=True, recorded_by=self.delegate)
        MeetingNote.objects.create(participant=student, meeting_date=date(2026, 10, 2), text="محفوظ", created_by=self.delegate, updated_by=self.general)
        task = WeeklyTask.objects.create(title="محفوظ", description="مهمة", due_date=date(2026, 12, 1), allowed_formats="text")
        TaskSubmission.objects.create(task=task, participant=student, text_content="محفوظ")
        product = StoreProduct.objects.create(name="محفوظ", price=10, stock=2)
        StoreOrder.objects.create(participant=student, product=product, price_at_order=10)
        PointsResetSnapshot.objects.create(participant=student, points_before_reset=90, reset_by=self.general)
        PointsLedgerEntry.objects.create(participant=student, points_delta=3, source=PointsLedgerEntry.Source.EXTRA, description="محفوظ", granted_by=self.general)
        models = (Participant, CircleAttendance, MeetingAttendance, WeeklyActivityAttendance, MeetingNote,
                  TaskSubmission, StoreOrder, PointsResetSnapshot, PointsLedgerEntry)
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        before_user = User.objects.values().get(pk=student.user_id)
        student_client = Client()
        self.assertTrue(student_client.login(username=student.user.national_id, password=self.password))
        before_user = User.objects.values().get(pk=student.user_id)
        self.client.force_login(self.delegate)
        for _ in range(2):
            response = self.client.post(self.delete_url, {"confirm": "yes"})
            self.assertEqual(response["Location"], self.management_url() + "?status=inactive")
        self.assertEqual(User.objects.values().get(pk=student.user_id), {**before_user, "is_active": False})
        for model in models:
            self.assertEqual(list(model.objects.order_by("pk").values()), before[model])
        self.assertEqual(self.audit_count(), 1)
        audit = LogEntry.objects.get()
        self.assertEqual(audit.content_type, ContentType.objects.get_for_model(User))
        self.assertEqual(audit.object_id, str(student.user_id))
        self.assertEqual(student_client.get(reverse("participants:dashboard")).status_code, 302)

    def test_edit_and_disable_audit_failure_rolls_back_whole_transaction(self):
        self.client.force_login(self.delegate)
        before_student = Participant.objects.values().get(pk=self.student.pk)
        before_user = User.objects.values().get(pk=self.student.user_id)
        for module, url, payload in ((management_views, self.edit_url, self.payload()),
                                     (participant_lifecycle, self.delete_url, {"confirm": "yes"})):
            with patch.object(module, "record_management", side_effect=RuntimeError("synthetic audit failure")):
                with self.assertRaises(RuntimeError):
                    self.client.post(url, payload)
            self.assertEqual(Participant.objects.values().get(pk=self.student.pk), before_student)
            self.assertEqual(User.objects.values().get(pk=self.student.user_id), before_user)
            self.assertEqual(self.audit_count(), 0)

    def test_delegated_posts_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.delegate)
        self.assertEqual(client.post(self.edit_url, self.payload()).status_code, 403)
        self.assertEqual(client.post(self.delete_url, {"confirm": "yes"}).status_code, 403)
        self.assertEqual(self.audit_count(), 0)

    def test_management_flag_never_grants_reports_import_or_manual_points(self):
        self.client.force_login(self.limited)
        for name in ("student_report", "participants_data", "participants_data_pdf", "report_center", "import_participants", "extra_points"):
            self.assertEqual(self.client.get(reverse("participants:" + name)).status_code, 403)
        self.assertEqual(self.client.get(reverse("participants:student_detail", kwargs={"pk": self.student.pk})).status_code, 403)
        self.assertFalse(capabilities.can_view_reports(self.limited))
        self.assertFalse(capabilities.can_prepare_reports(self.limited))

    def test_add_integration_accepts_assigned_groups_and_blocks_foreign_group(self):
        self.client.force_login(self.delegate)
        url = reverse("participants:add_participant")
        self.assertEqual(self.client.get(url).status_code, 200)
        before = Participant.objects.count()
        response = self.client.post(url, self.payload(national_id="9300000001", group=str(self.outside_group.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Participant.objects.count(), before)
        response = self.client.post(url, self.payload(national_id="9300000001"))
        self.assertEqual(response.status_code, 302)
        student = Participant.objects.get(user__national_id="9300000001")
        self.assertEqual(student.group_id, self.group_b.pk)
        self.assertEqual(student.user.role, Role.PARTICIPANT)
        self.assertTrue(student.user.must_set_password)
        self.assertTrue(student.user.check_password("9300000001"))
        self.assertEqual((student.points, student.miles, student.purchase_points), (0, 0, 0))
        self.assertEqual(self.delegate.role, Role.GROUP_SUPERVISOR)

    def test_add_integration_limited_all_groups_and_csrf_and_revoked_grant(self):
        self.client.force_login(self.limited)
        url = reverse("participants:add_participant")
        self.assertEqual(self.client.post(url, self.payload(national_id="9300000002", group=str(self.outside_group.pk))).status_code, 302)
        self.assertEqual(Participant.objects.get(user__national_id="9300000002").group_id, self.outside_group.pk)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.limited)
        self.assertEqual(csrf.post(url, self.payload(national_id="9300000003")).status_code, 403)
        SupervisorCapability.objects.filter(supervisor=self.limited).update(manage_students=False)
        self.assertEqual(self.client.post(url, self.payload(national_id="9300000003")).status_code, 403)
        self.assertFalse(User.objects.filter(national_id="9300000003").exists())

    def test_add_audit_failure_rolls_back_user_and_participant(self):
        self.client.force_login(self.delegate)
        before_users = User.objects.count()
        before_students = Participant.objects.count()
        with patch.object(management_views, "record_management", side_effect=RuntimeError("synthetic add audit failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("participants:add_participant"), self.payload(national_id="9300000010"))
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(Participant.objects.count(), before_students)
        self.assertFalse(User.objects.filter(national_id="9300000010").exists())
        self.assertEqual(self.audit_count(), 0)

    def test_add_integrity_conflict_is_friendly_and_leaves_no_orphan_user(self):
        from django.db import IntegrityError
        self.client.force_login(self.delegate)
        before_users = User.objects.count()
        before_students = Participant.objects.count()
        with patch.object(Participant.objects, "create", side_effect=IntegrityError("synthetic record conflict")):
            response = self.client.post(reverse("participants:add_participant"), self.payload(national_id="9300000011"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("national_id", response.context["form"].errors)
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(Participant.objects.count(), before_students)
        self.assertEqual(self.audit_count(), 0)

    def test_list_reloads_stale_actor_role_and_does_not_inherit_general_report_access(self):
        stale = User.objects.get(pk=self.general.pk)
        User.objects.filter(pk=stale.pk).update(role=Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=stale, manage_students=True)
        response, context = self.list_context(stale, status="all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(context["students"]), 5)
        self.assertFalse(context["can_restore_students"])
        self.assertFalse(context["can_manage_accounts"])
        self.assertFalse(context["can_view_reports"])
