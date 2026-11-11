"""Synthetic operational/report independence; isolated SQLite or local PG only."""
import datetime
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from . import access, reporting
from .capabilities import can_manage_students, can_view_reports
from .models import (
    CircleAttendance, EliteTripAttendance, Group, MeetingAttendance, MeetingNote,
    Participant, PointsLedgerEntry, StoreOrder, StoreProduct, SupervisorCapability,
    SupervisorReportPermission, TaskReviewPermission, TaskSubmission,
    WeeklyActivityAttendance, WeeklyTask,
)
from .views import (
    PointsLedgerView, QuranCircleAttendanceView, StoreManagementView,
    SupervisorDashboardView, WeeklyActivityAttendanceView, WeeklyTaskReviewView,
    store_orders_for_user,
)


class OperationalReportIndependenceTests(TestCase):
    DATE = datetime.date(2026, 10, 4)
    ROSTERS = (
        ("supervisor_dashboard", MeetingAttendance, "week_start_date", 10,
         PointsLedgerEntry.Source.MEETING_ATTENDANCE),
        ("quran_circle_attendance", CircleAttendance, "date", 5,
         PointsLedgerEntry.Source.QURAN_CIRCLE),
        ("weekly_activity_attendance", WeeklyActivityAttendance, "date", 10,
         PointsLedgerEntry.Source.WEEKLY_ACTIVITY),
        ("elite_trip_attendance", EliteTripAttendance, "date", 0, None),
    )

    @classmethod
    def setUpTestData(cls):
        cls.general = cls.actor("general", Role.GENERAL_SUPERVISOR)
        cls.supervisor = cls.actor("group", Role.GROUP_SUPERVISOR)
        cls.system = cls.actor("system", Role.SUPERADMIN)
        cls.limited = cls.actor("limited", Role.LIMITED_SUPERVISOR)
        for actor in (cls.general, cls.supervisor, cls.system, cls.limited):
            SupervisorReportPermission.objects.create(
                supervisor=actor, view_reports=False, prepare_reports=False,
                granted_by=cls.general,
            )
        cls.groups = [Group.objects.create(name="Synthetic operational group " + str(i)) for i in range(3)]
        for group in cls.groups[:2]:
            group.supervisor.add(cls.supervisor)
        cls.first = cls.student("first", cls.groups[0])
        cls.second = cls.student("second", cls.groups[1])
        cls.foreign = cls.student("foreign", cls.groups[2])
        cls.ungrouped = cls.student("ungrouped", None)
        cls.inactive = cls.student("inactive", cls.groups[0])
        User.objects.filter(pk=cls.inactive.user_id).update(is_active=False)
        cls.nonstudent = Participant.objects.create(user=cls.system, group=cls.groups[0])
        cls.students = (cls.first, cls.second, cls.foreign, cls.ungrouped, cls.inactive)
        cls.active_task = WeeklyTask.objects.create(
            title="Synthetic active operational task", description="Synthetic only",
            due_date=timezone.localdate() + datetime.timedelta(days=2),
            allowed_formats="text", is_active=True, created_by=cls.general,
        )
        cls.expired_task = WeeklyTask.objects.create(
            title="Synthetic expired operational task", description="Synthetic only",
            due_date=timezone.localdate() - datetime.timedelta(days=1),
            allowed_formats="text", is_active=True, created_by=cls.general,
        )
        for task in (cls.active_task, cls.expired_task):
            for student in cls.students:
                submission = TaskSubmission.objects.create(
                    task=task, participant=student, text_content="Synthetic saved submission",
                )
                TaskSubmission.objects.filter(pk=submission.pk).update(
                    submitted_at=timezone.now() - datetime.timedelta(days=3),
                )
        cls.delegation = TaskReviewPermission.objects.create(
            supervisor=cls.supervisor, enabled=True, granted_by=cls.general,
        )
        cls.product = StoreProduct.objects.create(name="Synthetic scoped product", price=7, stock=9)
        for student in cls.students:
            StoreOrder.objects.create(participant=student, product=cls.product, price_at_order=7)
            PointsLedgerEntry.objects.create(
                participant=student, source=PointsLedgerEntry.Source.EXTRA,
                points_delta=1, description="Synthetic scoped ledger", granted_by=cls.general,
            )

    @staticmethod
    def actor(name, role):
        return User.objects.create_user(
            username="operational-report-" + name, full_name="Synthetic " + name,
            password="synthetic-only", role=role, must_set_password=False,
        )

    @classmethod
    def student(cls, name, group):
        return Participant.objects.create(
            user=cls.actor(name, Role.PARTICIPANT), group=group, academic_stage="grade_7",
            points=20, miles=200, purchase_points=20,
        )

    def setUp(self):
        # Navigation and its templates are owned elsewhere; focus on operational
        # endpoints, persisted data and report guards, not concurrent UI changes.
        self.enterContext(patch("participants.views.build_navbar", return_value=[]))
        self.factory = RequestFactory()

    @staticmethod
    def ids(queryset):
        return set(queryset.values_list("pk", flat=True))

    def balances(self, student):
        student.refresh_from_db()
        return (student.points, student.miles, student.purchase_points)

    def roster_payload(self, variant, student=None, group=None):
        student = student or self.second
        data = {
            "group": (group or student.group).pk, "date": self.DATE.isoformat(),
            "partial_roster": "1", f"prepared_{student.pk}": "1",
            f"attended_{student.pk}": "on",
        }
        if variant[1] is MeetingAttendance:
            data[f"early_{student.pk}"] = "on"
        elif variant[1] is CircleAttendance:
            data[f"achieved_{student.pk}"] = "on"
        elif variant[1] is EliteTripAttendance:
            data[f"status_{student.pk}"] = EliteTripAttendance.Status.PRESENT
        return data

    def assert_rosters_work(self, actor, *, include_elite=True):
        self.client.force_login(actor)
        for variant in self.ROSTERS if include_elite else self.ROSTERS[:3]:
            with self.subTest(actor=actor.role, roster=variant[0]):
                url = reverse("participants:" + variant[0])
                response = self.client.get(url, {"group": self.second.group_id, "date": self.DATE.isoformat()})
                self.assertEqual(response.status_code, 200)
                self.assertEqual({row["participant"].pk for row in response.context["roster"]}, {self.second.pk})
                expected_groups = self.groups[:2] if actor.role == Role.GROUP_SUPERVISOR else self.groups
                self.assertEqual({group.pk for group in response.context["all_groups"]}, {group.pk for group in expected_groups})
                before = self.balances(self.second)
                foreign_before = self.balances(self.foreign)
                data = self.roster_payload(variant)
                # A forged participant from another group is never a selected
                # row even for General: saving this roster stays group-local.
                data.update({f"prepared_{self.foreign.pk}": "1", f"attended_{self.foreign.pk}": "on"})
                response = self.client.post(url, data, HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["saved_ids"], [self.second.pk])
                self.assertEqual(self.balances(self.second), tuple(value + variant[3] * rate for value, rate in zip(before, (1, 10, 1))))
                self.assertEqual(self.balances(self.foreign), foreign_before)
                rows = variant[1].objects.filter(**{variant[2]: self.DATE})
                self.assertEqual(set(rows.values_list("participant_id", flat=True)), {self.second.pk})
                after = self.balances(self.second)
                ledger_count = PointsLedgerEntry.objects.count()
                repeat = self.client.post(url, data, HTTP_ACCEPT="application/json")
                self.assertEqual(repeat.status_code, 200)
                self.assertEqual(self.balances(self.second), after)
                self.assertEqual(PointsLedgerEntry.objects.count(), ledger_count)
                if variant[4]:
                    entry = PointsLedgerEntry.objects.get(participant=self.second, source=variant[4])
                    self.assertEqual((entry.points_delta, entry.event_date), (variant[3], self.DATE))

    def test_general_denied_reports_retains_all_roster_get_partial_post_and_points(self):
        self.assert_rosters_work(self.general)

    def test_group_denied_reports_retains_assigned_roster_get_partial_post_and_points(self):
        self.assert_rosters_work(self.supervisor)

    def test_system_denied_reports_retains_standard_rosters_but_not_elite(self):
        self.assert_rosters_work(self.system, include_elite=False)
        self.assertEqual(self.client.get(reverse("participants:elite_trip_attendance")).status_code, 403)

    def test_group_rosters_block_outside_groups_on_get_and_post(self):
        self.client.force_login(self.supervisor)
        for variant in self.ROSTERS:
            with self.subTest(roster=variant[0]):
                url = reverse("participants:" + variant[0])
                self.assertEqual(self.client.get(url, {"group": self.foreign.group_id}).status_code, 404)
                self.assertEqual(self.client.post(url, self.roster_payload(variant, self.foreign)).status_code, 404)
                self.assertFalse(variant[1].objects.exists())
        self.assertEqual(self.balances(self.foreign), (20, 200, 20))

    def test_partial_rosters_never_create_inactive_nonstudent_or_unprepared_rows(self):
        self.client.force_login(self.supervisor)
        for variant in self.ROSTERS:
            with self.subTest(roster=variant[0]):
                data = self.roster_payload(variant, self.first)
                for student in (self.inactive, self.nonstudent):
                    data.update({f"prepared_{student.pk}": "1", f"attended_{student.pk}": "on"})
                response = self.client.post(reverse("participants:" + variant[0]), data, HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["saved_ids"], [self.first.pk])
                self.assertEqual(set(variant[1].objects.values_list("participant_id", flat=True)), {self.first.pk})

    def test_operational_helpers_do_not_call_report_policy_or_reporting_scopes(self):
        with patch("participants.reporting.allowed_groups", side_effect=AssertionError("report scope consulted")), \
             patch("participants.reporting.scoped_participants", side_effect=AssertionError("report scope consulted")), \
             patch("participants.capabilities.current_report_access", side_effect=AssertionError("report policy consulted")):
            for actor in (self.general, self.supervisor, self.system):
                with self.subTest(actor=actor.role):
                    self.assertTrue(access.allowed_groups(actor).exists())
                    self.assertTrue(access.scoped_participants(actor).exists())
                    self.assertTrue(access.scoped_submissions(actor).exists())
                    self.assertTrue(access.task_participants(actor).exists())
                    self.assertTrue(store_orders_for_user(actor).exists())
                    self.assertTrue(access.can_view_task_submissions(actor))

    def assert_task_review(self, actor):
        self.client.force_login(actor)
        url = reverse("participants:weekly_task_review")
        response = self.client.get(url, {"status": "pending"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_review_submissions"])
        self.assertFalse(response.context["can_view_student_detail"])
        visible = {submission.participant_id for row in response.context["tasks_with_submissions"] for submission in row["submissions"]}
        expected = {student.pk for student in self.students}
        if actor.role == Role.GROUP_SUPERVISOR:
            expected = {self.first.pk, self.second.pk, self.inactive.pk}
            self.assertFalse(response.context["can_manage_tasks"])
        self.assertEqual(visible, expected)
        for task in (self.active_task, self.expired_task):
            for action, student in (("accept", self.first), ("reject", self.second)):
                with self.subTest(actor=actor.role, task=task.pk, action=action):
                    submission = TaskSubmission.objects.get(task=task, participant=student)
                    before = self.balances(student)
                    response = self.client.post(url, {
                        "submission_id": submission.pk, "action": action,
                        "rejection_reason": "Synthetic reason",
                    })
                    self.assertEqual(response.status_code, 302)
                    submission.refresh_from_db()
                    self.assertEqual(submission.status, TaskSubmission.Status.ACCEPTED if action == "accept" else TaskSubmission.Status.REJECTED)
                    self.assertEqual(submission.reviewed_by_id, actor.pk)
                    delta = 10 if action == "accept" else 0
                    self.assertEqual(self.balances(student), tuple(value + delta * rate for value, rate in zip(before, (1, 10, 1))))
                    if action == "accept":
                        entry = PointsLedgerEntry.objects.filter(participant=student, source=PointsLedgerEntry.Source.WEEKLY_TASK).latest("pk")
                        self.assertEqual((entry.points_delta, entry.event_date), (10, timezone.localdate(submission.submitted_at)))
                        count = PointsLedgerEntry.objects.count()
                        self.client.post(url, {"submission_id": submission.pk, "action": "accept"})
                        self.assertEqual(PointsLedgerEntry.objects.count(), count)

    def test_general_denied_reports_retains_task_get_accept_reject_after_deadline(self):
        self.assert_task_review(self.general)

    def test_delegated_group_denied_reports_retains_task_get_accept_reject_after_deadline(self):
        self.assert_task_review(self.supervisor)

    def test_group_task_scope_blocks_foreign_review_and_manager_actions(self):
        self.client.force_login(self.supervisor)
        url = reverse("participants:weekly_task_review")
        foreign = TaskSubmission.objects.get(task=self.expired_task, participant=self.foreign)
        for action in ("accept", "reject", "return_for_revision"):
            self.assertEqual(self.client.post(url, {"action": action, "submission_id": foreign.pk}).status_code, 404)
        for action in ("create_task", "toggle_active", "update_task", "cancel_grade", "revise_grade"):
            self.assertEqual(self.client.post(url, {"action": action, "task_id": self.active_task.pk}).status_code, 403)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, TaskSubmission.Status.PENDING)
        self.assertEqual(self.balances(self.foreign), (20, 200, 20))

    def test_group_review_still_requires_fresh_task_delegation(self):
        self.client.force_login(self.supervisor)
        TaskReviewPermission.objects.filter(pk=self.delegation.pk).update(enabled=False)
        self.assertFalse(access.can_review_task_submissions(self.supervisor))
        url = reverse("participants:weekly_task_review")
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["can_review_submissions"])
        for action in ("accept", "reject"):
            submission = TaskSubmission.objects.get(task=self.active_task, participant=self.first)
            self.assertEqual(self.client.post(url, {"action": action, "submission_id": submission.pk}).status_code, 403)
            submission.refresh_from_db()
            self.assertEqual(submission.status, TaskSubmission.Status.PENDING)

    def test_report_configuration_denial_preserves_task_store_and_student_flags(self):
        SupervisorReportPermission.objects.filter(supervisor=self.supervisor).update(view_reports=True, prepare_reports=True)
        capability = SupervisorCapability.objects.create(
            supervisor=self.supervisor, follow_tasks=True, manage_store=True, manage_students=True,
        )
        original_delegation = TaskReviewPermission.objects.get(pk=self.delegation.pk)
        self.client.force_login(self.general)
        response = self.client.post(reverse("participants:supervisor_student_reports", kwargs={"pk": self.supervisor.pk}), {
            "confirm": "yes", "manage_students": "yes",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(can_view_reports(self.supervisor))
        capability.refresh_from_db()
        self.assertTrue(capability.follow_tasks and capability.manage_store and capability.manage_students)
        self.delegation.refresh_from_db()
        self.assertEqual((self.delegation.enabled, self.delegation.granted_by_id, self.delegation.updated_at),
                         (original_delegation.enabled, original_delegation.granted_by_id, original_delegation.updated_at))
        self.assertTrue(access.can_review_task_submissions(self.supervisor))
        self.assertTrue(can_manage_students(self.supervisor))

    def test_store_and_points_ledger_keep_general_all_and_group_assigned_history(self):
        for actor in (self.general, self.supervisor, self.system):
            with self.subTest(actor=actor.role):
                self.client.force_login(actor)
                expected = {student.pk for student in self.students}
                if actor.role == Role.GROUP_SUPERVISOR:
                    expected = {self.first.pk, self.second.pk, self.inactive.pk}
                store = self.client.get(reverse("participants:store_management"), {"group": self.foreign.group_id, "edit": self.product.pk})
                self.assertEqual(store.status_code, 200)
                self.assertEqual({order.participant_id for order in store.context["all_orders"]}, expected)
                self.assertEqual(store.context["can_manage_store"], actor.role != Role.GROUP_SUPERVISOR)
                if actor.role == Role.GROUP_SUPERVISOR:
                    self.assertNotIn("products", store.context)
                    self.assertNotIn("product_form", store.context)
                ledger = self.client.get(reverse("participants:points_ledger"), {"group": self.foreign.group_id})
                self.assertEqual(ledger.status_code, 200)
                self.assertEqual({entry.participant_id for entry in ledger.context["entries"]}, expected)

    def test_group_store_stays_read_only_even_with_unrelated_flags(self):
        SupervisorCapability.objects.create(supervisor=self.supervisor, manage_store=True, follow_tasks=True, manage_students=True)
        self.client.force_login(self.supervisor)
        order = StoreOrder.objects.get(participant=self.first)
        for action in ("complete", "refund", "add_product", "edit_product", "delete_product"):
            self.assertEqual(self.client.post(reverse("participants:store_management"), {
                "action": action, "order_id": order.pk, "product_id": self.product.pk,
            }).status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, StoreOrder.Status.PENDING)
        self.assertEqual(self.balances(self.first), (20, 200, 20))

    def test_general_store_writes_remain_independent_of_report_denial(self):
        self.client.force_login(self.general)
        url = reverse("participants:store_management")
        order = StoreOrder.objects.get(participant=self.foreign)
        before = self.balances(self.foreign)
        for action in ("complete", "refund"):
            self.assertEqual(self.client.post(url, {"action": action, "order_id": order.pk}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, StoreOrder.Status.REFUNDED)
        self.assertEqual(self.balances(self.foreign), (before[0], before[1], before[2] + 7))

    def test_report_helpers_and_endpoints_stay_closed_and_noncacheable(self):
        for actor in (self.general, self.supervisor, self.limited):
            with self.subTest(actor=actor.role):
                self.assertFalse(reporting.allowed_groups(actor).exists())
                self.assertFalse(reporting.scoped_participants(actor).exists())
                self.assertEqual(reporting.build_student_report(actor, {}), [])
                self.client.force_login(actor)
                urls = [reverse("participants:" + name) for name in (
                    "participants_data", "participants_data_pdf", "student_report",
                    "report_center", "report_center_export", "points_snapshot_history",
                )]
                urls.append(reverse("participants:student_detail", kwargs={"pk": self.first.pk}))
                for url in urls:
                    with self.subTest(url=url):
                        response = self.client.get(url)
                        self.assertEqual(response.status_code, 403)
                        self.assertIn("no-store", response.get("Cache-Control", ""))
        self.assertNotIn(Role.LIMITED_SUPERVISOR, reporting.REPORT_ROLES)

    def test_limited_only_explicit_task_store_flags_never_attendance_ledger_or_notes(self):
        capability = SupervisorCapability.objects.create(supervisor=self.limited, manage_students=True)
        # Even report grants and obsolete group delegation cannot create an
        # attendance/ledger/task/store power for LIMITED.
        SupervisorReportPermission.objects.filter(supervisor=self.limited).update(view_reports=True, prepare_reports=True)
        self.groups[0].supervisor.add(self.limited)
        TaskReviewPermission.objects.create(supervisor=self.limited, enabled=True)
        self.client.force_login(self.limited)
        task_url, store_url = reverse("participants:weekly_task_review"), reverse("participants:store_management")
        for task_flag, store_flag in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(task=task_flag, store=store_flag):
                SupervisorCapability.objects.filter(pk=capability.pk).update(follow_tasks=task_flag, manage_store=store_flag)
                self.assertEqual(self.client.get(task_url).status_code, 200 if task_flag else 403)
                self.assertEqual(self.client.get(store_url).status_code, 200 if store_flag else 403)
                self.assertEqual(access.scoped_submissions(self.limited).count(), TaskSubmission.objects.count() if task_flag else 0)
                self.assertEqual(store_orders_for_user(self.limited).count(), StoreOrder.objects.count() if store_flag else 0)
                self.assertFalse(access.allowed_groups(self.limited).exists())
                self.assertFalse(access.scoped_participants(self.limited).exists())
                self.assertFalse(access.can_manage_tasks(self.limited))
                self.assertFalse(access.can_view_meeting_notes(self.limited))
                self.assertFalse(access.can_prepare_elite_trip(self.limited))
                for name in [variant[0] for variant in self.ROSTERS] + ["points_ledger"]:
                    self.assertEqual(self.client.get(reverse("participants:" + name), {"group": self.first.group_id}).status_code, 403)
                submission = TaskSubmission.objects.get(task=self.active_task, participant=self.first)
                for action in ("create_task", "return_for_revision", "cancel_grade"):
                    self.assertEqual(self.client.post(task_url, {"action": action, "submission_id": submission.pk}).status_code, 403)

    def test_limited_denied_reports_with_task_store_flags_retains_allowed_operations(self):
        SupervisorCapability.objects.create(supervisor=self.limited, follow_tasks=True, manage_store=True)
        self.client.force_login(self.limited)
        page = self.client.get(reverse("participants:weekly_task_review"))
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["can_review_submissions"])
        self.assertFalse(page.context["can_return_submissions"])
        self.assertFalse(page.context["can_manage_tasks"])
        self.assertFalse(page.context["can_view_student_detail"])
        for action, student in (("accept", self.foreign), ("reject", self.second)):
            submission = TaskSubmission.objects.get(task=self.expired_task, participant=student)
            self.assertEqual(self.client.post(reverse("participants:weekly_task_review"), {
                "action": action, "submission_id": submission.pk,
            }).status_code, 302)
            submission.refresh_from_db()
            self.assertEqual(submission.status, TaskSubmission.Status.ACCEPTED if action == "accept" else TaskSubmission.Status.REJECTED)
        self.assertEqual(self.client.get(reverse("participants:store_management")).status_code, 200)
        order = StoreOrder.objects.get(participant=self.foreign)
        self.assertEqual(self.client.post(reverse("participants:store_management"), {"action": "complete", "order_id": order.pk}).status_code, 302)

    def test_fresh_group_m2m_scope_ignores_prefetched_assignment_after_removal(self):
        cached = User.objects.prefetch_related("group_set").get(pk=self.supervisor.pk)
        self.assertEqual(len(cached.group_set.all()), 2)
        self.groups[1].supervisor.remove(self.supervisor)
        self.assertEqual(self.ids(access.allowed_groups(cached)), {self.groups[0].pk})
        self.assertEqual(self.ids(access.task_participants(cached)), {self.first.pk, self.inactive.pk})
        self.assertEqual(set(store_orders_for_user(cached).values_list("participant_id", flat=True)), {self.first.pk, self.inactive.pk})
        request = self.factory.get("/synthetic-roster/", {"group": self.groups[1].pk})
        request.user = cached
        with self.assertRaises(Http404):
            access.attendance_group(request)
        request = self.factory.get("/synthetic-ledger/")
        request.user = cached
        view = PointsLedgerView()
        view.setup(request)
        self.assertEqual({entry.participant_id for entry in view.get_context_data()["entries"]}, {self.first.pk, self.inactive.pk})

    def test_fresh_role_demotions_and_deactivation_close_operational_helpers_and_views(self):
        cached = User.objects.get(pk=self.general.pk)
        for role, active in ((Role.GROUP_SUPERVISOR, True), (Role.LIMITED_SUPERVISOR, True),
                             (Role.PARTICIPANT, True), (Role.GENERAL_SUPERVISOR, False)):
            with self.subTest(role=role, active=active):
                User.objects.filter(pk=cached.pk).update(role=role, is_active=active)
                self.assertEqual(cached.role, Role.GENERAL_SUPERVISOR)
                self.assertFalse(access.allowed_groups(cached).exists())
                self.assertFalse(access.scoped_participants(cached).exists())
                self.assertFalse(access.scoped_submissions(cached).exists())
                self.assertFalse(access.task_participants(cached).exists())
                self.assertFalse(store_orders_for_user(cached).exists())
                self.assertFalse(access.can_manage_tasks(cached))
                self.assertFalse(access.can_review_task_submissions(cached))
                request = self.factory.get("/synthetic-operations/")
                request.user = cached
                for cls in (SupervisorDashboardView, QuranCircleAttendanceView, WeeklyActivityAttendanceView, PointsLedgerView):
                    view = cls()
                    view.setup(request)
                    self.assertFalse(view.test_func())
                for cls in (WeeklyTaskReviewView, StoreManagementView):
                    view = cls()
                    view.setup(request)
                    self.assertFalse(view.test_func())
        request = self.factory.get("/synthetic-operations/")
        request.user = cached
        with self.assertRaises(PermissionDenied):
            WeeklyTaskReviewView.as_view()(request)

    def test_stale_general_demoted_to_limited_has_only_current_task_flags_in_context(self):
        cached = User.objects.get(pk=self.general.pk)
        User.objects.filter(pk=cached.pk).update(role=Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=cached, follow_tasks=True)
        request = self.factory.get("/synthetic-task-review/")
        request.user = cached
        with self.assertRaises(PermissionDenied):
            WeeklyTaskReviewView.as_view()(request)
        request.user = User.objects.get(pk=cached.pk)
        response = WeeklyTaskReviewView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["limited_task_followup"])
        self.assertFalse(response.context_data["can_manage_tasks"])
        self.assertFalse(response.context_data["can_return_submissions"])
        self.assertFalse(response.context_data["can_revise_grades"])

    def test_stale_limited_promotion_requires_fresh_identity_before_inherited_operations(self):
        cached = User.objects.get(pk=self.limited.pk)
        User.objects.filter(pk=cached.pk).update(role=Role.GENERAL_SUPERVISOR)
        self.assertFalse(access.can_review_task_submissions(cached))
        self.assertFalse(access.can_view_task_submissions(cached))
        self.assertFalse(access.allowed_groups(cached).exists())
        self.assertFalse(access.scoped_submissions(cached).exists())
        self.assertFalse(store_orders_for_user(cached).exists())
        fresh = User.objects.get(pk=cached.pk)
        self.assertTrue(access.can_review_task_submissions(fresh))
        self.assertEqual(self.ids(access.allowed_groups(fresh)), {group.pk for group in self.groups})
        self.assertEqual(access.scoped_submissions(fresh).count(), TaskSubmission.objects.count())

    def test_anonymous_and_missing_accounts_get_empty_operational_scopes(self):
        missing = self.actor("deleted", Role.GENERAL_SUPERVISOR)
        missing.pk = 99999999
        for actor in (AnonymousUser(), missing):
            self.assertFalse(access.allowed_groups(actor).exists())
            self.assertFalse(access.scoped_participants(actor).exists())
            self.assertFalse(access.scoped_submissions(actor).exists())
            self.assertFalse(access.task_participants(actor).exists())
            self.assertFalse(store_orders_for_user(actor).exists())
            self.assertFalse(access.can_manage_tasks(actor))
            self.assertFalse(access.can_view_task_submissions(actor))
            self.assertFalse(access.can_review_task_submissions(actor))
