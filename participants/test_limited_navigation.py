"""Synthetic integration checks for the new role's navigation and login boundary."""
from itertools import product

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import (
    Group, Participant, StoreOrder, StoreProduct, SupervisorCapability,
    TaskSubmission, WeeklyTask,
)
from .views import build_navbar, get_notification_counts


class LimitedNavigationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="synthetic-limited-nav", password="SyntheticNavigationPass123!",
            full_name="مشرف محدود مصطنع", role=Role.LIMITED_SUPERVISOR,
        )
        self.capability = SupervisorCapability.objects.create(supervisor=self.user)
        self.group = Group.objects.create(name="بيئة اختبار التنقل")
        student_user = User.objects.create_user(
            username="synthetic-limited-nav-student", password="SyntheticStudentPass123!",
            full_name="طالب مصطنع للتنقل", role=Role.PARTICIPANT,
        )
        self.student = Participant.objects.create(
            user=student_user, group=self.group, academic_stage="grade_7",
        )
        self.task = WeeklyTask.objects.create(
            title="مهمة اختبار التنقل", description="نص مصطنع",
            due_date=timezone.localdate(), allowed_formats="text", is_active=True,
        )
        self.submission = TaskSubmission.objects.create(
            task=self.task, participant=self.student, text_content="عمل مصطنع",
        )
        product = StoreProduct.objects.create(name="منتج اختبار التنقل", price=3, stock=2)
        StoreOrder.objects.create(participant=self.student, product=product, price_at_order=3)

    def set_flags(self, store=False, tasks=False):
        self.capability.manage_store = store
        self.capability.follow_tasks = tasks
        self.capability.save(update_fields=["manage_store", "follow_tasks", "updated_at"])

    def test_navigation_matrix_never_inherits_general_or_environment_links(self):
        forbidden = {
            "attendance", "quran", "weekly_activity", "elite_trip", "data",
            "student_report", "report_center", "extra_points", "points_ledger",
            "tasks_archive", "task_review_permissions", "import", "add_participant",
            "supervisors", "points_snapshot_history",
        }
        for store, tasks in product((False, True), repeat=2):
            with self.subTest(store=store, tasks=tasks):
                self.set_flags(store, tasks)
                entries = build_navbar(self.user, "home")
                by_key = {entry["key"]: entry for entry in entries}
                expected = {"home", "change_password"}
                if store:
                    expected.add("store_management")
                if tasks:
                    expected.add("tasks")
                    self.assertEqual(by_key["tasks"]["label"], "متابعة وتصحيح المهام")
                self.assertEqual(set(by_key), expected)
                self.assertFalse(set(by_key) & forbidden)
                self.assertEqual(by_key["home"]["url"], reverse("participants:limited_supervisor_dashboard"))
                self.assertTrue(by_key["home"]["active"])
                self.assertTrue(by_key["home"]["mobile_primary"])
                self.assertEqual(by_key["change_password"]["group"], "account")
                self.assertFalse(by_key["change_password"]["mobile_primary"])
                for key in ("tasks", "store_management"):
                    if key in by_key:
                        self.assertTrue(by_key[key]["mobile_primary"])
                        self.assertEqual(by_key[key]["badge_count"], 1)

    def test_revocation_affects_same_user_object_badges_and_links(self):
        self.set_flags(True, True)
        self.assertEqual(get_notification_counts(self.user), {"tasks": 1, "store_management": 1})
        self.set_flags(False, False)
        self.assertEqual(get_notification_counts(self.user), {})
        self.assertEqual({entry["key"] for entry in build_navbar(self.user, "home")}, {"home", "change_password"})

    def test_missing_capability_row_grants_nothing(self):
        self.capability.delete()
        self.assertEqual(get_notification_counts(self.user), {})
        self.assertEqual({entry["key"] for entry in build_navbar(self.user, "home")}, {"home", "change_password"})

    def test_inactive_limited_user_has_no_data_badges(self):
        self.set_flags(True, True)
        self.user.is_active = False
        self.assertEqual(get_notification_counts(self.user), {})
        self.assertEqual({entry["key"] for entry in build_navbar(self.user, "home")}, {"home", "change_password"})

    def test_task_badge_counts_only_student_submissions(self):
        self.set_flags(tasks=True)
        self.student.user.role = Role.GROUP_SUPERVISOR
        self.student.user.save(update_fields=["role"])
        self.assertEqual(get_notification_counts(self.user), {"tasks": 0})

    def test_login_goes_to_limited_home_even_without_any_permissions(self):
        response = self.client.post(reverse("accounts:login_supervisor"), {
            "username": self.user.username, "password": "SyntheticNavigationPass123!",
        })
        self.assertRedirects(response, reverse("participants:limited_supervisor_dashboard"))
        self.assertEqual(response.wsgi_request.user.role, Role.LIMITED_SUPERVISOR)

    def test_dashboard_cards_match_grants_and_do_not_show_student_data(self):
        self.client.force_login(self.user)
        for store, tasks in product((False, True), repeat=2):
            with self.subTest(store=store, tasks=tasks):
                self.set_flags(store, tasks)
                response = self.client.get(reverse("participants:limited_supervisor_dashboard"))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["can_manage_store"], store)
                self.assertEqual(response.context["can_follow_tasks"], tasks)
                if store:
                    self.assertContains(response, reverse("participants:store_management"))
                else:
                    self.assertNotContains(response, reverse("participants:store_management"))
                if tasks:
                    self.assertContains(response, reverse("participants:weekly_task_review"))
                    self.assertContains(response, "متابعة وتصحيح المهام")
                    self.assertContains(response, "معاينة وقبول ورفض")
                    self.assertContains(response, "فتح متابعة وتصحيح المهام")
                    self.assertNotContains(response, "متابعة المهام للقراءة والمعاينة فقط")
                else:
                    self.assertNotContains(response, reverse("participants:weekly_task_review"))
                self.assertNotContains(response, self.student.user.full_name)
                self.assertNotContains(response, reverse("participants:general_supervisor_dashboard"))
                self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_granted_limited_user_is_denied_every_unrelated_admin_surface(self):
        self.set_flags(True, True)
        self.client.force_login(self.user)
        forbidden = (
            "general_supervisor_dashboard", "supervisor_dashboard", "quran_circle_attendance",
            "weekly_activity_attendance", "elite_trip_attendance", "participants_data",
            "participants_data_pdf", "student_report", "report_center", "report_center_export",
            "points_ledger", "extra_points", "points_snapshot_history", "tasks_archive",
            "task_review_permissions", "supervisor_list", "supervisor_add", "import_participants",
            "add_participant", "task_submission", "dashboard", "store",
        )
        for name in forbidden:
            with self.subTest(name=name):
                response = self.client.get(reverse("participants:" + name))
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("participants:student_detail", args=[self.student.pk])).status_code, 403)

    def test_general_and_group_users_cannot_enter_limited_dashboard(self):
        for role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.SUPERADMIN, Role.PARTICIPANT):
            with self.subTest(role=role):
                user = User.objects.create_user(username="nav-" + role, password="SyntheticPass123!", role=role)
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("participants:limited_supervisor_dashboard")).status_code, 403)

    def test_limited_account_does_not_get_django_admin_access(self):
        self.set_flags(True, True)
        self.client.force_login(self.user)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertFalse(self.user.groups.exists())
        self.assertFalse(self.user.user_permissions.exists())
