"""Actual-route coverage for independent student/report UI integration."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .capabilities import can_manage_students, current_report_access
from .models import Group, Participant, SupervisorCapability, SupervisorReportPermission
from .views import build_navbar


class StudentReportNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username="nav-policy-general", role=Role.GENERAL_SUPERVISOR)
        cls.limited = User.objects.create_user(username="nav-policy-limited", role=Role.LIMITED_SUPERVISOR)
        cls.group_user = User.objects.create_user(username="nav-policy-group", role=Role.GROUP_SUPERVISOR)
        cls.group = Group.objects.create(name="بيئة اختبار منح وروابط")
        cls.group.supervisor.add(cls.group_user)

    def policy_url(self, user):
        return reverse("participants:supervisor_student_reports", kwargs={"pk": user.pk})

    def test_actual_policy_form_has_independent_yes_fields_and_native_confirmation(self):
        self.client.force_login(self.general)
        response = self.client.get(self.policy_url(self.group_user))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="manage_students"')
        self.assertContains(response, 'name="view_reports"')
        self.assertContains(response, 'name="prepare_reports"')
        self.assertContains(response, 'name="confirm"')
        self.assertContains(response, 'required')
        self.assertNotIn("role", response.context["form"].fields)
        self.assertFalse(SupervisorReportPermission.objects.exists())

    def test_manager_without_reports_has_only_basic_students_links(self):
        SupervisorCapability.objects.create(supervisor=self.limited, manage_students=True)
        self.client.force_login(self.limited)
        response = self.client.get(reverse("participants:limited_supervisor_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("participants:student_management"))
        self.assertNotContains(response, reverse("participants:report_center"))
        keys = {item["key"] for item in response.context["navbar_items"]}
        self.assertIn("student_management", keys)
        self.assertNotIn("participants_data", keys)
        self.assertNotIn("report_center", keys)

    def test_view_only_report_navigation_is_consecutive_and_export_is_absent(self):
        SupervisorCapability.objects.create(supervisor=self.limited, manage_students=True)
        SupervisorReportPermission.objects.create(supervisor=self.limited, view_reports=True)
        self.client.force_login(self.limited)
        response = self.client.get(reverse("participants:report_center"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_prepare_reports"])
        self.assertNotContains(response, 'formaction="' + reverse("participants:report_center_export"))
        groups = [item["group"] for item in build_navbar(self.limited, "home")]
        runs = [value for i, value in enumerate(groups) if i == 0 or value != groups[i - 1]]
        self.assertEqual(len(runs), len(set(runs)))

    def test_old_capability_route_keeps_student_checkbox_and_saves_it(self):
        SupervisorCapability.objects.create(supervisor=self.limited, manage_students=True)
        self.client.force_login(self.general)
        url = reverse("participants:supervisor_capabilities", kwargs={"pk": self.limited.pk})
        response = self.client.get(url)
        self.assertTrue(response.context["form"]["manage_students"].value())
        self.assertContains(response, "إدارة الطلاب")
        self.assertEqual(self.client.post(url, {"manage_students": "yes", "confirm": "yes"}).status_code, 302)
        self.assertTrue(can_manage_students(self.limited))
        self.assertFalse(current_report_access(self.limited)["can_view"])
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 302)
        self.assertFalse(can_manage_students(self.limited))

    def test_group_conversion_revokes_positive_report_grants_but_preserves_denial(self):
        policy = SupervisorReportPermission.objects.create(supervisor=self.group_user, view_reports=True, prepare_reports=True)
        self.client.force_login(self.general)
        url = reverse("participants:supervisor_capabilities", kwargs={"pk": self.group_user.pk})
        self.assertEqual(self.client.post(url, {"manage_students": "yes", "confirm": "yes"}).status_code, 302)
        self.group_user.refresh_from_db()
        policy.refresh_from_db()
        self.assertEqual(self.group_user.role, Role.LIMITED_SUPERVISOR)
        self.assertTrue(can_manage_students(self.group_user))
        self.assertFalse(policy.view_reports)
        self.assertFalse(policy.prepare_reports)
        stamp = policy.updated_at
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 302)
        policy.refresh_from_db()
        self.assertEqual(policy.updated_at, stamp)

    def test_regular_supervisor_disable_clears_explicit_positive_students_and_reports(self):
        capability = SupervisorCapability.objects.create(supervisor=self.limited, manage_students=True)
        policy = SupervisorReportPermission.objects.create(supervisor=self.limited, view_reports=True, prepare_reports=True)
        self.client.force_login(self.general)
        url = reverse("participants:supervisor_delete", kwargs={"pk": self.limited.pk})
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 302)
        capability.refresh_from_db()
        policy.refresh_from_db()
        self.limited.refresh_from_db()
        self.assertFalse(self.limited.is_active)
        self.assertFalse(capability.manage_students)
        self.assertFalse(policy.view_reports)
        self.assertFalse(policy.prepare_reports)

    def test_list_exposes_new_policy_action_only_to_general_for_eligible_other_account(self):
        self.client.force_login(self.general)
        response = self.client.get(reverse("participants:supervisor_list"))
        self.assertContains(response, self.policy_url(self.limited))
        self.assertContains(response, self.policy_url(self.group_user))
        self.assertNotContains(response, self.policy_url(self.general))

    def test_delegated_archived_student_detail_never_advertises_restore(self):
        SupervisorCapability.objects.create(supervisor=self.limited, manage_students=True)
        SupervisorReportPermission.objects.create(supervisor=self.limited, view_reports=True)
        student_user = User.objects.create_user(
            national_id="8400000001", role=Role.PARTICIPANT, is_active=False,
        )
        student = Participant.objects.create(user=student_user, group=self.group)
        self.client.force_login(self.limited)
        response = self.client.get(reverse("participants:student_detail", kwargs={"pk": student.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_manage_students"])
        self.assertFalse(response.context["can_manage_accounts"])
        self.assertNotContains(response, reverse("participants:participant_restore", kwargs={"pk": student.pk}))

    def test_rosters_keep_names_without_links_to_denied_reports(self):
        student_user = User.objects.create_user(
            national_id="8400000002", role=Role.PARTICIPANT, full_name="طالب اختبار روابط التحضير",
        )
        student = Participant.objects.create(user=student_user, group=self.group)
        detail_href = 'href="' + reverse("participants:student_detail", kwargs={"pk": student.pk}) + '"'
        data_href = 'href="' + reverse("participants:participants_data") + '"'
        routes = (
            "supervisor_dashboard", "quran_circle_attendance",
            "weekly_activity_attendance", "elite_trip_attendance",
        )
        for actor in (self.general, self.group_user):
            policy = SupervisorReportPermission.objects.create(supervisor=actor, view_reports=False)
            self.client.force_login(actor)
            for allowed in (False, True):
                policy.view_reports = allowed
                policy.save(update_fields=["view_reports"])
                for route in routes:
                    with self.subTest(role=actor.role, reports=allowed, route=route):
                        response = self.client.get(reverse("participants:" + route), {"group": self.group.pk})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.context["can_view_student_detail"], allowed)
                        self.assertContains(response, student_user.full_name)
                        if allowed:
                            self.assertContains(response, detail_href)
                        else:
                            self.assertNotContains(response, detail_href)
                            self.assertNotContains(response, data_href)
                        self.assertContains(response, "data-roster-form")

    def test_anonymous_add_student_uses_real_supervisor_login_route(self):
        url = reverse("participants:add_participant")
        before = (User.objects.count(), Participant.objects.count())
        response = self.client.get(url)
        self.assertRedirects(
            response, reverse("accounts:login_supervisor") + "?next=" + url,
        )
        self.assertEqual((User.objects.count(), Participant.objects.count()), before)
