"""Synthetic-only contracts for limited, independently revocable capabilities.

Run with config.test_settings. PostgreSQL locking is exercised separately by
opt-in concurrency coverage; these tests also revalidate post-lock state.
"""

import importlib
import json
from datetime import date
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import AnonymousUser, Group as AuthGroup, Permission
from django.db import connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import QueryDict
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Role, User
from participants.capabilities import can_follow_tasks, can_manage_store, revoke_capabilities
from participants.management_forms import SupervisorCapabilitiesForm, SupervisorManagementForm
from participants.models import (
    Group, MeetingAttendance, Participant, PointsLedgerEntry, SupervisorCapability,
    TaskReviewPermission, TaskSubmission, WeeklyTask,
)


class CapabilityFormTests(SimpleTestCase):
    def test_fields_are_exactly_two_independent_flags_and_required_confirmation(self):
        form = SupervisorCapabilitiesForm()
        self.assertEqual(set(form.fields), {"manage_store", "follow_tasks", "confirm"})
        for field in ("manage_store", "follow_tasks"):
            self.assertFalse(form.fields[field].required)
            self.assertEqual(form.fields[field].widget.attrs["value"], "yes")
        self.assertTrue(form.fields["confirm"].required)

    def test_all_four_flag_combinations_are_valid(self):
        for store in (False, True):
            for follow in (False, True):
                data = {"confirm": "yes"}
                if store:
                    data["manage_store"] = "yes"
                if follow:
                    data["follow_tasks"] = "yes"
                form = SupervisorCapabilitiesForm(data)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual((form.cleaned_data["manage_store"], form.cleaned_data["follow_tasks"]), (store, follow))

    def test_malformed_and_duplicate_flags_or_confirm_are_rejected(self):
        for field in ("manage_store", "follow_tasks", "confirm"):
            for value in ("no", "true", "1", "YES", " yes ", ["yes", "yes"], ["no", "yes"]):
                with self.subTest(field=field, value=value):
                    form = SupervisorCapabilitiesForm({"confirm": "yes", field: value})
                    self.assertFalse(form.is_valid())
                    self.assertIn(field, form.errors)
        self.assertFalse(SupervisorCapabilitiesForm({}).is_valid())


class LimitedCapabilityTests(TestCase):
    password = "SyntheticLimitedPass123!"

    def user(self, username, role, **fields):
        return User.objects.create_user(username=username, role=role, password=self.password, full_name=username, **fields)

    def setUp(self):
        self.actor = self.user("limited-manager", Role.GENERAL_SUPERVISOR)
        self.root = self.user("limited-root", Role.SUPERADMIN)
        self.limited = self.user("limited-target", Role.LIMITED_SUPERVISOR, must_set_password=True)
        self.group_user = self.user("limited-group", Role.GROUP_SUPERVISOR)
        self.staff = self.user("limited-staff", Role.GROUP_SUPERVISOR, is_staff=True)
        self.superuser = self.user("limited-protected", Role.LIMITED_SUPERVISOR, is_superuser=True)
        self.student_user = self.user("limited-student", Role.PARTICIPANT)
        self.group = Group.objects.create(name="بيئة صلاحيات مصطنعة")
        self.group.supervisor.add(self.group_user, self.staff)
        self.student = Participant.objects.create(
            user=self.student_user, group=self.group, academic_stage="grade_7",
            points=31, miles=310, purchase_points=17,
        )
        self.legacy = self.user("limited-legacy", Role.LIMITED_SUPERVISOR)
        Participant.objects.create(user=self.legacy, group=self.group, academic_stage="grade_7")
        self.review = TaskReviewPermission.objects.create(supervisor=self.group_user, enabled=True, granted_by=self.actor)
        self.client.force_login(self.actor)

    def url(self, user=None):
        return reverse("participants:supervisor_capabilities", kwargs={"pk": (user or self.limited).pk})

    @staticmethod
    def rows(model):
        return list(model.objects.order_by("pk").values())

    def snapshot(self):
        return {model._meta.label: self.rows(model) for model in (
            User, SupervisorCapability, Group.supervisor.through, TaskReviewPermission,
            Participant, MeetingAttendance, WeeklyTask, TaskSubmission, PointsLedgerEntry,
            User.groups.through, User.user_permissions.through, LogEntry,
        )}

    def grant(self, user=None, **flags):
        return self.client.post(self.url(user), {"confirm": "yes", **flags})

    def capability(self, user=None, **flags):
        return SupervisorCapability.objects.create(supervisor=user or self.limited, **flags)

    def edit_payload(self, user=None, **changes):
        user = user or self.limited
        return {
            "full_name": user.full_name, "username": user.username, "national_id": "",
            "role": user.role, "is_active": "on", "password1": "", "password2": "",
            **changes,
        }

    def test_missing_and_default_rows_fail_closed_without_creation(self):
        before = self.snapshot()
        self.assertFalse(can_manage_store(self.limited))
        self.assertFalse(can_follow_tasks(self.limited))
        self.assertEqual(self.snapshot(), before)
        capability = self.capability()
        self.assertFalse(capability.manage_store)
        self.assertFalse(capability.follow_tasks)
        self.assertIsNone(capability.granted_by)
        self.assertFalse(can_manage_store(self.limited))
        self.assertFalse(can_follow_tasks(self.limited))

    def test_independent_flags_are_fresh_even_after_related_object_was_cached(self):
        capability = self.capability(manage_store=True)
        _cached = self.limited.limited_capabilities
        self.assertTrue(can_manage_store(self.limited))
        self.assertFalse(can_follow_tasks(self.limited))
        SupervisorCapability.objects.filter(pk=capability.pk).update(manage_store=False, follow_tasks=True)
        self.assertFalse(can_manage_store(self.limited))
        self.assertTrue(can_follow_tasks(self.limited))
        SupervisorCapability.objects.filter(pk=capability.pk).delete()
        self.assertFalse(can_follow_tasks(self.limited))

    def test_stale_role_activity_and_flags_cannot_authorize(self):
        self.capability(manage_store=True, follow_tasks=True)
        for change in ({"role": Role.GROUP_SUPERVISOR}, {"role": Role.PARTICIPANT}, {"is_active": False}):
            with self.subTest(change=change):
                User.objects.filter(pk=self.limited.pk).update(role=Role.LIMITED_SUPERVISOR, is_active=True)
                User.objects.filter(pk=self.limited.pk).update(**change)
                self.assertFalse(can_manage_store(self.limited))
                self.assertFalse(can_follow_tasks(self.limited))
        self.assertFalse(can_manage_store(AnonymousUser()))
        self.assertFalse(can_follow_tasks(AnonymousUser()))

    def test_only_active_general_system_or_flagged_limited_manage_store(self):
        for user in (self.group_user, self.staff, self.student_user):
            self.capability(user, manage_store=True, follow_tasks=True)
            self.assertFalse(can_manage_store(user))
            self.assertFalse(can_follow_tasks(user))
        for user in (self.actor, self.root):
            self.assertTrue(can_manage_store(user))
            self.assertFalse(can_follow_tasks(user))
            User.objects.filter(pk=user.pk).update(is_active=False)
            self.assertFalse(can_manage_store(user))

    def test_get_is_readonly_even_when_flags_and_confirm_are_in_querystring(self):
        before = self.snapshot()
        response = self.client.get(self.url(self.group_user), {"confirm": "yes", "manage_store": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(response.context["form"].initial["manage_store"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_general_only_manager_no_admin_flag_override(self):
        for actor in (self.root, self.group_user, self.limited, self.staff, self.superuser, self.student_user):
            self.client.force_login(actor)
            before = self.snapshot()
            for method in (self.client.get, self.client.post):
                with self.subTest(actor=actor.role, method=method.__name__):
                    self.assertEqual(method(self.url(self.group_user), {"confirm": "yes"}).status_code, 403)
            self.assertEqual(self.snapshot(), before)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_protected_targets_get_and_post_cannot_mutate(self):
        for target in (self.actor, self.root, self.superuser, self.student_user, self.legacy):
            for method in (self.client.get, self.client.post):
                with self.subTest(target=target.role, method=method.__name__):
                    before = self.snapshot()
                    self.assertIn(method(self.url(target), {"confirm": "yes"}).status_code, (403, 404))
                    self.assertEqual(self.snapshot(), before)

    def test_confirm_missing_fake_duplicate_and_querystring_never_write(self):
        before = self.snapshot()
        for data in ({}, {"confirm": "no"}, {"confirm": "true"}, {"confirm": ["yes", "yes"]},
                     {"confirm": ["no", "yes"]}, {"confirm": "yes", "manage_store": "bogus"}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url() + "?confirm=yes", data).status_code, 400)
                self.assertEqual(self.snapshot(), before)

    def test_csrf_token_is_required_and_valid_token_permits_grant(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        before = self.snapshot()
        self.assertEqual(client.post(self.url(), {"confirm": "yes", "manage_store": "yes"}).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(client.get(self.url()).status_code, 200)
        response = client.post(self.url(), {"confirm": "yes", "manage_store": "yes"}, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(can_manage_store(self.limited))

    def test_group_conversion_removes_environment_review_only_and_records_one_audit(self):
        user_before = User.objects.filter(pk=self.group_user.pk).values().get()
        actor_before = User.objects.filter(pk=self.actor.pk).values().get()
        student_before = self.rows(Participant)
        self.assertEqual(self.grant(self.group_user, follow_tasks="yes").status_code, 302)
        user_after = User.objects.filter(pk=self.group_user.pk).values().get()
        user_before["role"] = Role.LIMITED_SUPERVISOR
        self.assertEqual(user_after, user_before)
        self.assertEqual(User.objects.filter(pk=self.actor.pk).values().get(), actor_before)
        self.assertEqual(self.rows(Participant), student_before)
        self.assertFalse(self.group_user.group_set.exists())
        self.review.refresh_from_db()
        self.assertFalse(self.review.enabled)
        self.assertTrue(can_follow_tasks(self.group_user))
        self.assertFalse(can_manage_store(self.group_user))
        audit = LogEntry.objects.get()
        self.assertEqual(json.loads(audit.change_message), [{"changed": {"fields": ["صلاحيات المشرف المحددة"]}}])

    def test_staff_conversion_preserves_password_auth_flags_groups_and_permissions(self):
        authgroup = AuthGroup.objects.create(name="existing-synthetic-admin-scope")
        permission = Permission.objects.order_by("pk").first()
        self.staff.groups.add(authgroup)
        self.staff.user_permissions.add(permission)
        before = self.snapshot()
        response = self.grant(self.staff, manage_store="yes", role=Role.SUPERADMIN, is_staff="", is_superuser="yes", password1="InjectedPass!", user_id=self.root.pk, is_active="")
        self.assertEqual(response.status_code, 302)
        after = self.snapshot()
        expected_users = before[User._meta.label]
        for row in expected_users:
            if row["id"] == self.staff.pk:
                row["role"] = Role.LIMITED_SUPERVISOR
        self.assertEqual(after[User._meta.label], expected_users)
        for table in (User.groups.through, User.user_permissions.through, Participant):
            self.assertEqual(after[table._meta.label], before[table._meta.label])
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_staff)
        self.assertTrue(self.staff.check_password(self.password))

    def test_all_capability_combinations_and_repeat_are_idempotent(self):
        for store, follow in ((True, False), (False, True), (True, True), (False, False)):
            flags = {}
            if store:
                flags["manage_store"] = "yes"
            if follow:
                flags["follow_tasks"] = "yes"
            self.assertEqual(self.grant(**flags).status_code, 302)
            self.assertEqual((can_manage_store(self.limited), can_follow_tasks(self.limited)), (store, follow))
            before = self.snapshot()
            self.assertEqual(self.grant(**flags).status_code, 302)
            self.assertEqual(self.snapshot(), before)

    def test_zero_flags_missing_row_is_strict_noop(self):
        before = self.snapshot()
        self.assertEqual(self.grant().status_code, 302)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(SupervisorCapability.objects.filter(supervisor=self.limited).exists())

    def test_group_zero_flags_converts_once_without_creating_default_row(self):
        self.assertEqual(self.grant(self.group_user).status_code, 302)
        self.group_user.refresh_from_db()
        self.assertEqual(self.group_user.role, Role.LIMITED_SUPERVISOR)
        self.assertFalse(SupervisorCapability.objects.filter(supervisor=self.group_user).exists())
        before = self.snapshot()
        self.assertEqual(self.grant(self.group_user).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_limited_can_clear_only_without_reactivation(self):
        capability = self.capability(manage_store=True, follow_tasks=True)
        User.objects.filter(pk=self.limited.pk).update(is_active=False)
        before = self.snapshot()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].initial["manage_store"])
        self.assertFalse(response.context["can_grant_capabilities"])
        self.assertEqual(self.grant(manage_store="yes").status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.grant().status_code, 302)
        capability.refresh_from_db()
        self.limited.refresh_from_db()
        self.assertFalse(capability.manage_store or capability.follow_tasks)
        self.assertFalse(self.limited.is_active)
        before = self.snapshot()
        self.assertEqual(self.grant().status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_group_cannot_convert_even_with_zero_flags(self):
        User.objects.filter(pk=self.group_user.pk).update(is_active=False)
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url(self.group_user)).status_code, 404)
        self.assertEqual(self.grant(self.group_user).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_revoke_missing_or_disabled_does_not_write_and_enabled_changes_once(self):
        before = self.snapshot()
        self.assertFalse(revoke_capabilities(self.limited, self.actor))
        self.assertEqual(self.snapshot(), before)
        capability = self.capability(manage_store=True, follow_tasks=True)
        user_before = User.objects.filter(pk=self.limited.pk).values().get()
        self.assertTrue(revoke_capabilities(self.limited, self.actor))
        capability.refresh_from_db()
        self.assertFalse(capability.manage_store or capability.follow_tasks)
        self.assertEqual(capability.granted_by_id, self.actor.pk)
        self.assertEqual(User.objects.filter(pk=self.limited.pk).values().get(), user_before)
        before = self.snapshot()
        self.assertFalse(revoke_capabilities(self.limited, self.root))
        self.assertEqual(self.snapshot(), before)

    def test_locked_users_no_key_and_actor_rechecked_after_lock(self):
        from participants import management_views
        original = management_views.locked_users
        for change in ({"role": Role.SUPERADMIN}, {"role": Role.GROUP_SUPERVISOR}, {"is_active": False}):
            before = self.snapshot()

            def changed_actor(*args, **kwargs):
                self.assertEqual(args[:2], (self.actor.pk, self.limited.pk))
                self.assertIs(kwargs.get("no_key"), True)
                self.assertTrue(connection.in_atomic_block)
                User.objects.filter(pk=self.actor.pk).update(**change)
                return original(*args, **kwargs)

            with patch.object(management_views, "locked_users", side_effect=changed_actor):
                self.assertEqual(self.grant(manage_store="yes").status_code, 403)
            self.assertEqual(self.snapshot(), before)

    def test_target_eligibility_rechecked_after_lock(self):
        from participants import management_views
        original = management_views.locked_users
        for change in ({"role": Role.GENERAL_SUPERVISOR}, {"role": Role.SUPERADMIN}, {"is_superuser": True}, {"participant": True}):
            before = self.snapshot()

            def changed_target(*args, **kwargs):
                if "participant" in change:
                    Participant.objects.create(user=self.limited, group=self.group, academic_stage="grade_7")
                else:
                    User.objects.filter(pk=self.limited.pk).update(**change)
                return original(*args, **kwargs)

            with patch.object(management_views, "locked_users", side_effect=changed_target):
                self.assertEqual(self.grant(manage_store="yes").status_code, 404)
            self.assertEqual(self.snapshot(), before)

    def test_target_deactivation_after_lock_rejects_true_flags(self):
        from participants import management_views
        original = management_views.locked_users

        def deactivate(*args, **kwargs):
            User.objects.filter(pk=self.limited.pk).update(is_active=False)
            return original(*args, **kwargs)

        with patch.object(management_views, "locked_users", side_effect=deactivate):
            self.assertEqual(self.grant(manage_store="yes").status_code, 400)
        self.assertFalse(SupervisorCapability.objects.filter(supervisor=self.limited).exists())
        self.assertFalse(LogEntry.objects.exists())

    def test_audit_failure_rolls_back_role_groups_review_flags(self):
        from participants import management_views
        before = self.snapshot()
        with patch.object(management_views, "record_management", side_effect=RuntimeError("Synthetic audit failure")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic audit failure"):
                self.grant(self.group_user, manage_store="yes")
        self.assertEqual(self.snapshot(), before)

    def test_new_limited_zero_flags_creation_redirects_general_to_capability_form(self):
        data = self.edit_payload(username="limited-new", full_name="مشرف جديد", role=Role.LIMITED_SUPERVISOR, password1=self.password, password2=self.password)
        response = self.client.post(reverse("participants:supervisor_add"), data)
        user = User.objects.get(username="limited-new")
        self.assertRedirects(response, self.url(user), fetch_redirect_response=False)
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertFalse(user.group_set.exists())
        self.assertFalse(SupervisorCapability.objects.filter(supervisor=user).exists())

    def test_superadmin_creation_cannot_grant_and_returns_list(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse("participants:supervisor_add"), self.edit_payload(username="limited-root-created", role=Role.LIMITED_SUPERVISOR, password1=self.password, password2=self.password, manage_store="yes"))
        self.assertRedirects(response, reverse("participants:supervisor_list"), fetch_redirect_response=False)
        user = User.objects.get(username="limited-root-created")
        self.assertFalse(SupervisorCapability.objects.filter(supervisor=user).exists())

    def test_limited_management_form_rejects_environment_and_preserves_self_protection(self):
        form = SupervisorManagementForm(self.edit_payload(environments=[self.group.pk]), actor=self.actor, supervisor=self.limited)
        self.assertFalse(form.is_valid())
        self.assertIn("environments", form.errors)
        form = SupervisorManagementForm(self.edit_payload(self.actor, role=Role.LIMITED_SUPERVISOR, is_active=""), actor=self.actor, supervisor=self.actor)
        self.assertFalse(form.is_valid())
        self.assertIn("role", form.errors)
        self.assertIn("is_active", form.errors)

    def test_name_only_edit_preserves_capabilities_but_disable_reactivate_revokes_once(self):
        self.capability(manage_store=True, follow_tasks=True)
        before = self.rows(SupervisorCapability)
        edit_url = reverse("participants:supervisor_edit", kwargs={"pk": self.limited.pk})
        self.assertEqual(self.client.post(edit_url, self.edit_payload(full_name="اسم محدث")).status_code, 302)
        self.assertEqual(self.rows(SupervisorCapability), before)
        self.assertEqual(self.client.post(edit_url, self.edit_payload(is_active="")).status_code, 302)
        self.assertFalse(can_manage_store(self.limited))
        cleared = self.rows(SupervisorCapability)
        self.assertEqual(self.client.post(edit_url, self.edit_payload()).status_code, 302)
        self.assertEqual(self.rows(SupervisorCapability), cleared)
        self.assertFalse(can_follow_tasks(self.limited))

    def test_role_change_and_delete_revoke_capabilities(self):
        self.capability(manage_store=True, follow_tasks=True)
        response = self.client.post(reverse("participants:supervisor_edit", kwargs={"pk": self.limited.pk}), self.edit_payload(role=Role.GROUP_SUPERVISOR))
        self.assertEqual(response.status_code, 302)
        capability = SupervisorCapability.objects.get(supervisor=self.limited)
        self.assertFalse(capability.manage_store or capability.follow_tasks)
        capability.manage_store = True
        capability.follow_tasks = True
        capability.save()
        response = self.client.post(reverse("participants:supervisor_delete", kwargs={"pk": self.limited.pk}), {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        capability.refresh_from_db()
        self.limited.refresh_from_db()
        self.assertFalse(capability.manage_store or capability.follow_tasks)
        self.assertFalse(self.limited.is_active)

    def test_limited_to_general_explicit_grant_revokes_flags_once(self):
        self.capability(manage_store=True, follow_tasks=True)
        url = reverse("participants:supervisor_grant_general", kwargs={"pk": self.limited.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 302)
        self.limited.refresh_from_db()
        self.assertEqual(self.limited.role, Role.GENERAL_SUPERVISOR)
        capability = SupervisorCapability.objects.get(supervisor=self.limited)
        self.assertFalse(capability.manage_store or capability.follow_tasks)
        self.assertTrue(can_manage_store(self.limited))
        self.assertFalse(can_follow_tasks(self.limited))
        before = self.snapshot()
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_list_role_filter_labels_protected_staff_and_constant_capability_reads(self):
        self.capability(manage_store=True, follow_tasks=True)
        response = self.client.get(reverse("participants:supervisor_list"), {"role": Role.LIMITED_SUPERVISOR})
        self.assertEqual(response.status_code, 200)
        self.assertIn(Role.LIMITED_SUPERVISOR, [value for value, _ in response.context["role_choices"]])
        row = next(user for user in response.context["supervisors"] if user.pk == self.limited.pk)
        self.assertTrue(row.can_configure_capabilities)
        self.assertTrue(row.can_grant_general)
        self.assertEqual(len(row.capabilities_labels), 2)
        for index in range(12):
            self.capability(self.user(f"limited-list-{index}", Role.LIMITED_SUPERVISOR), follow_tasks=True)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("participants:supervisor_list"), {"status": "all"})
        capability_selects = [entry["sql"] for entry in queries if "participants_supervisorcapability" in entry["sql"] and entry["sql"].lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(capability_selects), 1)
        staff_row = next(user for user in response.context["supervisors"] if user.pk == self.staff.pk)
        self.assertFalse(staff_row.can_edit_supervisor)
        self.assertTrue(staff_row.can_configure_capabilities)
        self.client.force_login(self.root)
        response = self.client.get(reverse("participants:supervisor_list"), {"status": "all"})
        self.assertTrue(all(not user.can_configure_capabilities for user in response.context["supervisors"]))

    def test_historical_balances_and_actor_references_are_unchanged(self):
        attendance = MeetingAttendance.objects.create(participant=self.student, week_start_date=date.today(), attended=True, recorded_by=self.group_user)
        task = WeeklyTask.objects.create(title="مهمة محفوظة", description="نص", due_date=date.today(), allowed_formats="text", created_by=self.group_user)
        TaskSubmission.objects.create(task=task, participant=self.student, status="accepted", points_awarded=True, award_value=10, reviewed_by=self.group_user, text_content="نص محفوظ")
        PointsLedgerEntry.objects.create(participant=self.student, source="weekly_task", points_delta=10, description="سجل محفوظ", granted_by=self.group_user)
        tables = (Participant, MeetingAttendance, WeeklyTask, TaskSubmission, PointsLedgerEntry)
        before = {model: self.rows(model) for model in tables}
        self.assertEqual(self.grant(self.group_user, manage_store="yes").status_code, 302)
        self.assertEqual({model: self.rows(model) for model in tables}, before)
        attendance.refresh_from_db()
        self.assertEqual(attendance.recorded_by_id, self.group_user.pk)

    def test_limited_supervisor_login_and_password_setup_choose_limited_dashboard(self):
        client = Client()
        response = client.post(reverse("accounts:login_supervisor"), {"username": self.limited.username, "password": self.password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("participants:limited_supervisor_dashboard"))
        response = client.post(reverse("accounts:set_password"), {"new_password1": "NewSyntheticLimitedPass123!", "new_password2": "NewSyntheticLimitedPass123!"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("participants:limited_supervisor_dashboard"))
        self.limited.refresh_from_db()
        self.assertFalse(self.limited.must_set_password)
        self.assertTrue(self.limited.check_password("NewSyntheticLimitedPass123!"))
        self.assertEqual(client.get(reverse("accounts:set_password"))["Location"], reverse("participants:limited_supervisor_dashboard"))

    def test_password_setup_redirect_preserves_all_existing_role_routes(self):
        for user, route in ((self.student_user, "participants:dashboard"), (self.group_user, "participants:supervisor_dashboard"), (self.actor, "participants:general_supervisor_dashboard"), (self.root, "participants:general_supervisor_dashboard")):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("accounts:set_password"))["Location"], reverse(route))


class CapabilityMigrationContractTests(SimpleTestCase):
    def test_migrations_contain_only_schema_operations_and_expected_dependencies(self):
        accounts = importlib.import_module("accounts.migrations.0004_alter_user_role").Migration
        participants = importlib.import_module("participants.migrations.0025_supervisor_capability").Migration
        self.assertEqual(len(accounts.operations), 1)
        self.assertIsInstance(accounts.operations[0], migrations.AlterField)
        self.assertEqual(len(participants.operations), 1)
        self.assertIsInstance(participants.operations[0], migrations.CreateModel)
        self.assertIn(("accounts", "0004_alter_user_role"), participants.dependencies)
        self.assertIn(("participants", "0024_task_grade_revision"), participants.dependencies)
        self.assertFalse(any(isinstance(operation, migrations.RunPython) for operation in accounts.operations + participants.operations))


class CapabilityUpgradeTests(TransactionTestCase):
    def test_upgrade_does_not_backfill_or_mutate_existing_accounts_or_students(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old_target = [node for node in latest if node[0] not in ("accounts", "participants")] + [
            ("accounts", "0003_user_must_set_password_passwordresetrequest"),
            ("participants", "0024_task_grade_revision"),
        ]
        try:
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            OldUser = old_apps.get_model("accounts", "User")
            OldParticipant = old_apps.get_model("participants", "Participant")
            supervisor = OldUser.objects.create(username="upgrade-limited-staff", full_name="اسم محفوظ", password="historical-hash", role="group_supervisor", is_staff=True, must_set_password=True)
            student_user = OldUser.objects.create(username="upgrade-limited-student", role="participant", password="student-hash")
            OldParticipant.objects.create(user=student_user, group=None, academic_stage="grade_7", points=38, miles=380, purchase_points=9)
            before_users = list(OldUser.objects.order_by("pk").values())
            before_students = list(OldParticipant.objects.order_by("pk").values())
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            self.assertEqual(list(apps.get_model("accounts", "User").objects.order_by("pk").values()), before_users)
            self.assertEqual(list(apps.get_model("participants", "Participant").objects.order_by("pk").values()), before_students)
            self.assertFalse(apps.get_model("participants", "SupervisorCapability").objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
