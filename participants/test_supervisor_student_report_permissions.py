"""Synthetic isolated contract tests; use config.test_settings locally only.

A test-only URL and in-memory template avoid relying on parallel integration of
production routes/templates. No production data, browser or external services.
"""

import importlib
import json
import re
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import AnonymousUser, Group as AuthGroup, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from accounts.models import Role, User
from config.urls import urlpatterns as application_urls
from participants.capabilities import (
    can_follow_tasks, can_manage_store, can_manage_students, can_prepare_reports,
    can_view_report_center, can_view_reports, current_report_access,
    revoke_capabilities, revoke_report_grants, student_management_role,
)
from participants.models import (
    Group, Participant, PointsLedgerEntry, SupervisorCapability,
    SupervisorReportPermission, TaskReviewPermission,
)
from participants.supervisor_permission_forms import SupervisorStudentReportsForm
from participants.supervisor_permissions import SupervisorStudentReportsView


urlpatterns = [
    path("__permission_contract/<int:pk>/", SupervisorStudentReportsView.as_view()),
] + list(application_urls)

TEST_TEMPLATE_NAME = "participants/supervisor_student_reports_form.html"
TEST_TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": False,
    "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", {
        TEST_TEMPLATE_NAME: '<form method="post">{% csrf_token %}{{ form.as_p }}<button type="submit">حفظ</button></form>',
    })]},
}]
TEST_SETTINGS = {"ROOT_URLCONF": __name__, "TEMPLATES": TEST_TEMPLATES}


def synthetic_user(username, role, **fields):
    fields.setdefault("full_name", username)
    return User.objects.create_user(username=username, role=role, password="SyntheticPermissions123!", **fields)


def permission_url(user):
    return f"/__permission_contract/{user.pk}/"


class SupervisorStudentReportCapabilityTests(TestCase):
    def setUp(self):
        self.actor = synthetic_user("policy-manager", Role.GENERAL_SUPERVISOR)
        self.user = synthetic_user("policy-user", Role.GROUP_SUPERVISOR)

    def assert_access(self, user, view, prepare, center):
        with CaptureQueriesContext(connection) as captured:
            access = current_report_access(user)
        self.assertEqual(len(captured), 1)
        self.assertEqual(access, {
            "role": User.objects.get(pk=user.pk).role,
            "can_view": view, "can_prepare": prepare, "can_center": center,
        })
        self.assertEqual(can_view_reports(user), view)
        self.assertEqual(can_prepare_reports(user), prepare)
        self.assertEqual(can_view_report_center(user), center)

    def test_legacy_missing_policy_role_matrix_and_center_exception(self):
        for role, expected in (
            (Role.GROUP_SUPERVISOR, (True, True, False)),
            (Role.GENERAL_SUPERVISOR, (True, True, True)),
            (Role.SUPERADMIN, (True, True, False)),
            (Role.LIMITED_SUPERVISOR, (False, False, False)),
            (Role.PARTICIPANT, (False, False, False)),
        ):
            with self.subTest(role=role):
                User.objects.filter(pk=self.user.pk).update(role=role)
                self.assert_access(self.user, *expected)
        self.assertFalse(SupervisorReportPermission.objects.exists())

    def test_explicit_policy_matrix_including_inconsistent_prepare_without_view(self):
        policy = SupervisorReportPermission.objects.create(supervisor=self.user)
        for role in Role.values + ["unknown_role"]:
            User.objects.filter(pk=self.user.pk).update(role=role)
            for view, prepare in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(role=role, view=view, prepare=prepare):
                    SupervisorReportPermission.objects.filter(pk=policy.pk).update(view_reports=view, prepare_reports=prepare)
                    supervisor = role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)
                    self.assert_access(self.user, view and supervisor, view and prepare and supervisor,
                                       view and supervisor and role != Role.SUPERADMIN)

    def test_single_fresh_join_ignores_cached_policy_and_role_and_activity(self):
        policy = SupervisorReportPermission.objects.create(supervisor=self.user, view_reports=True, prepare_reports=True)
        stale = User.objects.select_related("report_permissions").get(pk=self.user.pk)
        self.assertTrue(stale.report_permissions.view_reports)
        SupervisorReportPermission.objects.filter(pk=policy.pk).update(view_reports=False)
        self.assert_access(stale, False, False, False)
        SupervisorReportPermission.objects.filter(pk=policy.pk).update(view_reports=True)
        User.objects.filter(pk=stale.pk).update(is_active=False, role=Role.GENERAL_SUPERVISOR)
        self.assert_access(stale, False, False, False)
        stale.is_active = False
        stale.role = Role.PARTICIPANT
        User.objects.filter(pk=stale.pk).update(is_active=True, role=Role.LIMITED_SUPERVISOR)
        self.assert_access(stale, True, True, True)

    def test_anonymous_unsaved_and_deleted_id_fail_closed(self):
        for user in (AnonymousUser(), User(role=Role.GENERAL_SUPERVISOR)):
            self.assertEqual(current_report_access(user), {
                "role": None, "can_view": False, "can_prepare": False, "can_center": False,
            })
            self.assertFalse(can_manage_students(user))
            self.assertIsNone(student_management_role(user))
        pk = self.user.pk
        User.objects.filter(pk=pk).delete()
        self.assertIsNone(current_report_access(self.user)["role"])
        self.assertFalse(can_manage_students(self.user))

    def test_student_management_fresh_role_flag_activity_matrix(self):
        capability = SupervisorCapability.objects.create(supervisor=self.user, manage_students=True)
        stale = User.objects.select_related("limited_capabilities").get(pk=self.user.pk)
        for role in Role.values + ["unknown_role"]:
            for active in (False, True):
                for flag in (False, True):
                    with self.subTest(role=role, active=active, flag=flag):
                        User.objects.filter(pk=self.user.pk).update(role=role, is_active=active)
                        SupervisorCapability.objects.filter(pk=capability.pk).update(manage_students=flag)
                        expected = active and (role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN) or (
                            flag and role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR)
                        ))
                        self.assertEqual(can_manage_students(stale), expected)
                        self.assertEqual(student_management_role(stale), role if expected else None)
        stale.is_active = False
        User.objects.filter(pk=stale.pk).update(role=Role.GENERAL_SUPERVISOR, is_active=True)
        self.assertTrue(can_manage_students(stale))

    def test_missing_student_flag_does_not_grant_group_or_limited(self):
        for role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR):
            User.objects.filter(pk=self.user.pk).update(role=role)
            self.assertFalse(can_manage_students(self.user))
        for role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN):
            User.objects.filter(pk=self.user.pk).update(role=role)
            self.assertTrue(can_manage_students(self.user))

    def test_new_grants_do_not_expand_store_or_task_roles(self):
        capability = SupervisorCapability.objects.create(
            supervisor=self.user, manage_store=True, follow_tasks=True, manage_students=True,
        )
        SupervisorReportPermission.objects.create(supervisor=self.user, view_reports=True, prepare_reports=True)
        self.assertFalse(can_manage_store(self.user))
        self.assertFalse(can_follow_tasks(self.user))
        User.objects.filter(pk=self.user.pk).update(role=Role.LIMITED_SUPERVISOR)
        self.assertTrue(can_manage_store(self.user))
        self.assertTrue(can_follow_tasks(self.user))
        SupervisorCapability.objects.filter(pk=capability.pk).update(manage_store=False, follow_tasks=False)
        self.assertFalse(can_manage_store(self.user))
        self.assertFalse(can_follow_tasks(self.user))
        self.assertTrue(can_manage_students(self.user))
        self.assertTrue(can_view_reports(self.user))

    def test_revoke_capabilities_clears_students_and_does_not_regrant_on_activation(self):
        capability = SupervisorCapability.objects.create(
            supervisor=self.user, manage_store=True, follow_tasks=True, manage_students=True,
        )
        before_user = User.objects.filter(pk=self.user.pk).values().get()
        self.assertTrue(revoke_capabilities(self.user, self.actor))
        capability.refresh_from_db()
        self.assertEqual((capability.manage_store, capability.follow_tasks, capability.manage_students), (False, False, False))
        snapshot = SupervisorCapability.objects.values().get(pk=capability.pk)
        self.assertFalse(revoke_capabilities(self.user, self.actor))
        self.assertEqual(SupervisorCapability.objects.values().get(pk=capability.pk), snapshot)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        User.objects.filter(pk=self.user.pk).update(is_active=True)
        self.assertFalse(can_manage_students(self.user))
        self.assertEqual(User.objects.filter(pk=self.user.pk).values().get(), before_user)

    def test_revoke_missing_capabilities_or_report_policy_creates_nothing(self):
        self.assertFalse(revoke_capabilities(self.user, self.actor))
        self.assertFalse(revoke_report_grants(self.user, self.actor))
        self.assertFalse(SupervisorCapability.objects.exists())
        self.assertFalse(SupervisorReportPermission.objects.exists())
        self.assertTrue(can_view_reports(self.user))

    def test_report_revoke_true_grants_and_denied_rows_survive_role_and_activation_changes(self):
        policy = SupervisorReportPermission.objects.create(supervisor=self.user, view_reports=True, prepare_reports=True)
        self.assertTrue(revoke_report_grants(self.user, self.actor))
        policy.refresh_from_db()
        self.assertFalse(policy.view_reports)
        self.assertFalse(policy.prepare_reports)
        self.assertEqual(policy.granted_by_id, self.actor.pk)
        snapshot = SupervisorReportPermission.objects.values().get(pk=policy.pk)
        for role in (Role.PARTICIPANT, Role.LIMITED_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.GROUP_SUPERVISOR):
            for active in (False, True):
                User.objects.filter(pk=self.user.pk).update(role=role, is_active=active)
                self.assertFalse(revoke_report_grants(self.user, self.user))
                self.assertEqual(SupervisorReportPermission.objects.values().get(pk=policy.pk), snapshot)
                self.assert_access(self.user, False, False, False)

    def test_revoke_view_only_or_inconsistent_prepare_only_positive(self):
        for view, prepare in ((True, False), (False, True)):
            policy, _ = SupervisorReportPermission.objects.update_or_create(
                supervisor=self.user, defaults={"view_reports": view, "prepare_reports": prepare},
            )
            self.assertTrue(revoke_report_grants(self.user, self.actor))
            policy.refresh_from_db()
            self.assertEqual((policy.view_reports, policy.prepare_reports), (False, False))


@override_settings(**TEST_SETTINGS)
class SupervisorStudentReportsPermissionViewTests(TestCase):
    def setUp(self):
        navbar = patch("participants.management_views.build_navbar", return_value=[])
        navbar.start()
        self.addCleanup(navbar.stop)
        self.actor = synthetic_user("permissions-general", Role.GENERAL_SUPERVISOR)
        self.target = synthetic_user("permissions-target", Role.GROUP_SUPERVISOR, is_staff=True, must_set_password=True)
        self.general = synthetic_user("permissions-other-general", Role.GENERAL_SUPERVISOR)
        self.limited = synthetic_user("permissions-limited", Role.LIMITED_SUPERVISOR)
        self.root = synthetic_user("permissions-system", Role.SUPERADMIN, is_staff=True, is_superuser=True)
        self.group = Group.objects.create(name="بيئة صلاحيات مصطنعة")
        self.group.supervisor.add(self.target)
        self.other_group = Group.objects.create(name="بيئة ثانية مصطنعة")
        self.other_group.supervisor.add(self.target)
        self.student_user = synthetic_user("permissions-student", Role.PARTICIPANT)
        self.student = Participant.objects.create(user=self.student_user, group=self.group, academic_stage="grade_7", points=31, miles=310, purchase_points=9)
        PointsLedgerEntry.objects.create(participant=self.student, points_delta=1, source=PointsLedgerEntry.Source.EXTRA, granted_by=self.target, description="سجل مصطنع محفوظ")
        self.review = TaskReviewPermission.objects.create(supervisor=self.target, enabled=True, granted_by=self.actor)
        self.auth_group = AuthGroup.objects.create(name="synthetic-django-group")
        self.target.groups.add(self.auth_group)
        self.target.user_permissions.add(Permission.objects.first())
        self.client.force_login(self.actor)

    @staticmethod
    def rows(model):
        return list(model.objects.order_by("pk").values())

    def domain_snapshot(self):
        return {model._meta.label: self.rows(model) for model in (
            User, Group, Group.supervisor.through, Participant, PointsLedgerEntry,
            TaskReviewPermission, AuthGroup, User.groups.through, User.user_permissions.through,
        )}

    def full_snapshot(self):
        return {**self.domain_snapshot(), "capabilities": self.rows(SupervisorCapability),
                "policies": self.rows(SupervisorReportPermission), "audit": self.rows(LogEntry)}

    def save(self, target=None, **flags):
        return self.client.post(permission_url(target or self.target), {"confirm": "yes", **flags})

    def test_get_head_no_writes_and_inherited_initial_including_general_without_policy(self):
        for target in (self.target, self.general, self.limited):
            for method in (self.client.get, self.client.head):
                with self.subTest(target=target.role, method=method.__name__):
                    before = self.full_snapshot()
                    with CaptureQueriesContext(connection) as queries:
                        response = method(permission_url(target), {"confirm": "yes"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.full_snapshot(), before)
                    self.assertFalse(any(re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", q["sql"], re.I) for q in queries))
                    form = response.context["form"]
                    self.assertEqual(form.initial["view_reports"], target.role != Role.LIMITED_SUPERVISOR)
                    self.assertEqual(form.initial["prepare_reports"], target.role != Role.LIMITED_SUPERVISOR)
                    self.assertEqual(form.fields["manage_students"].disabled, target.role == Role.GENERAL_SUPERVISOR)
                    self.assertEqual(response.context["supervisor"].pk, target.pk)
                    self.assertEqual(response.context["is_general"], target.role == Role.GENERAL_SUPERVISOR)
                    self.assertTrue(response.context["can_grant_permissions"])
                    self.assertFalse(response.context["is_inactive"])
                    self.assertIn("no-store", response.headers["Cache-Control"])

    def test_only_current_active_general_actor_and_not_system_group_limited_participant(self):
        for actor in (self.target, self.limited, self.root, self.student_user):
            self.client.force_login(actor)
            before = self.full_snapshot()
            for method in (self.client.get, self.client.head, self.client.post):
                self.assertEqual(method(permission_url(self.general), {"confirm": "yes"}).status_code, 403)
            self.assertEqual(self.full_snapshot(), before)
        self.client.force_login(self.actor)
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        before = self.full_snapshot()
        self.assertIn(self.save().status_code, (302, 403))
        self.assertEqual(self.full_snapshot(), before)

    def test_anonymous_redirect_and_unsupported_methods_do_not_write(self):
        client = Client()
        before = self.full_snapshot()
        for method in (client.get, client.head, client.post):
            response = method(permission_url(self.target), {"confirm": "yes"})
            self.assertEqual(response.status_code, 302)
            self.assertIn("login", response.headers["Location"])
        for method in (self.client.put, self.client.patch, self.client.delete):
            self.assertEqual(method(permission_url(self.target)).status_code, 405)
        self.assertEqual(self.full_snapshot(), before)

    def test_self_system_superuser_participant_backed_and_missing_targets_rejected(self):
        superuser = synthetic_user("permissions-superuser", Role.GROUP_SUPERVISOR, is_superuser=True)
        backed = synthetic_user("permissions-backed", Role.GENERAL_SUPERVISOR)
        Participant.objects.create(user=backed, group=self.group, academic_stage="grade_7")
        for target, status in ((self.actor, 403), (self.root, 404), (superuser, 404), (self.student_user, 404), (backed, 404)):
            for method in (self.client.get, self.client.head, self.client.post):
                before = self.full_snapshot()
                self.assertEqual(method(permission_url(target), {"confirm": "yes"}).status_code, status)
                self.assertEqual(self.full_snapshot(), before)
        self.assertEqual(self.client.post("/__permission_contract/999999999/", {"confirm": "yes"}).status_code, 404)

    def test_grants_and_revocation_preserve_roles_staff_credentials_groups_points_and_legacy_flags(self):
        capability = SupervisorCapability.objects.create(supervisor=self.target, manage_store=True, follow_tasks=True, granted_by=self.target)
        before = self.domain_snapshot()
        response = self.save(manage_students="yes", view_reports="yes", prepare_reports="yes", role=Role.LIMITED_SUPERVISOR,
                             username="injected", password1="Bad123!", is_staff="", is_superuser="yes", is_active="", environments=[],
                             points="99999", miles="99999", purchase_points="99999", supervisor_id=self.student_user.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.domain_snapshot(), before)
        self.assertTrue(can_manage_students(self.target))
        self.assertTrue(can_view_reports(self.target))
        capability.refresh_from_db()
        self.assertTrue(capability.manage_store)
        self.assertTrue(capability.follow_tasks)
        self.assertEqual(LogEntry.objects.count(), 1)
        self.assertEqual(self.save().status_code, 302)
        self.assertEqual(self.domain_snapshot(), before)
        capability.refresh_from_db()
        self.assertFalse(capability.manage_students)
        self.assertTrue(capability.manage_store)
        self.assertTrue(capability.follow_tasks)
        self.assertFalse(can_manage_students(self.target))
        self.assertFalse(can_view_reports(self.target))
        self.assertEqual(LogEntry.objects.count(), 2)

    def test_manage_students_only_does_not_change_group_role_or_assignments(self):
        before = self.domain_snapshot()
        self.assertEqual(self.save(manage_students="yes").status_code, 302)
        self.assertEqual(self.domain_snapshot(), before)
        self.assertTrue(can_manage_students(self.target))
        self.assertFalse(can_view_reports(self.target))
        self.assertFalse(can_manage_store(self.target))
        self.assertFalse(can_follow_tasks(self.target))

    def test_report_only_save_preserves_capability_row_and_legacy_grant_metadata(self):
        capability = SupervisorCapability.objects.create(
            supervisor=self.target, manage_store=True, follow_tasks=True, granted_by=self.target,
        )
        before = SupervisorCapability.objects.values().get(pk=capability.pk)
        self.assertEqual(self.save(view_reports="yes").status_code, 302)
        self.assertEqual(SupervisorCapability.objects.values().get(pk=capability.pk), before)
        self.assertEqual(self.save(view_reports="yes", prepare_reports="yes").status_code, 302)
        self.assertEqual(SupervisorCapability.objects.values().get(pk=capability.pk), before)

    def test_equal_student_and_report_flags_leave_both_rows_and_audit_untouched(self):
        SupervisorCapability.objects.create(supervisor=self.target, manage_students=True, granted_by=self.target)
        SupervisorReportPermission.objects.create(supervisor=self.target, view_reports=True, granted_by=self.target)
        before = self.full_snapshot()
        self.assertEqual(self.save(manage_students="yes", view_reports="yes").status_code, 302)
        self.assertEqual(self.full_snapshot(), before)
        self.assertFalse(LogEntry.objects.exists())

    def test_valid_save_has_no_user_or_group_or_participant_sql_writes(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.save(manage_students="yes", view_reports="yes", prepare_reports="yes").status_code, 302)
        writes = [query["sql"] for query in queries if re.match(
            r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", query["sql"], re.I,
        )]
        self.assertTrue(writes)
        for sql in writes:
            self.assertNotIn('"accounts_user"', sql)
            self.assertNotIn('"accounts_user_groups"', sql)
            self.assertNotIn('"accounts_user_user_permissions"', sql)
            self.assertNotIn('"participants_group"', sql)
            self.assertNotIn('"participants_group_supervisor"', sql)
            self.assertNotIn('"participants_participant"', sql)

    def test_general_report_denial_without_demotion_and_crud_is_inherited_disabled(self):
        existing = SupervisorCapability.objects.create(supervisor=self.general, manage_students=True, manage_store=True, follow_tasks=True)
        before = self.domain_snapshot()
        cap_before = SupervisorCapability.objects.values().get(pk=existing.pk)
        self.assertEqual(self.save(self.general).status_code, 302)
        self.assertFalse(can_view_reports(self.general))
        self.assertFalse(can_prepare_reports(self.general))
        self.assertFalse(can_view_report_center(self.general))
        self.assertTrue(can_manage_students(self.general))
        self.assertEqual(self.domain_snapshot(), before)
        self.assertEqual(SupervisorCapability.objects.values().get(pk=existing.pk), cap_before)
        self.assertEqual(self.save(self.general, view_reports="yes").status_code, 302)
        self.assertTrue(can_view_reports(self.general))
        self.assertFalse(can_prepare_reports(self.general))
        self.assertEqual(self.domain_snapshot(), before)
        self.assertEqual(SupervisorCapability.objects.values().get(pk=existing.pk), cap_before)

    def test_explicit_viewing_only_group_or_limited_and_equal_save_no_second_audit_or_timestamps(self):
        for target in (self.target, self.limited):
            self.assertEqual(self.save(target, view_reports="yes").status_code, 302)
            self.assertTrue(can_view_reports(target))
            self.assertFalse(can_prepare_reports(target))
            self.assertTrue(can_view_report_center(target))
            before = self.full_snapshot()
            self.assertEqual(self.save(target, view_reports="yes").status_code, 302)
            self.assertEqual(self.full_snapshot(), before)
            self.assertFalse(SupervisorCapability.objects.filter(supervisor=target).exists())

    def test_explicit_false_created_even_for_limited_and_repeat_is_exact_noop(self):
        self.assertEqual(self.save(self.limited).status_code, 302)
        policy = SupervisorReportPermission.objects.get(supervisor=self.limited)
        self.assertFalse(policy.view_reports)
        self.assertFalse(policy.prepare_reports)
        self.assertEqual(policy.granted_by_id, self.actor.pk)
        before = self.full_snapshot()
        self.assertEqual(self.save(self.limited).status_code, 302)
        self.assertEqual(self.full_snapshot(), before)

    def test_existing_false_policy_initial_does_not_restore_inheritance(self):
        SupervisorReportPermission.objects.create(supervisor=self.general)
        response = self.client.get(permission_url(self.general))
        self.assertFalse(response.context["form"].initial["view_reports"])
        self.assertFalse(response.context["form"].initial["prepare_reports"])
        self.assertTrue(response.context["form"].initial["manage_students"])

    def test_inactive_targets_can_revoke_or_create_false_policy_only(self):
        for target in (self.target, self.general, self.limited):
            User.objects.filter(pk=target.pk).update(is_active=False)
            target.refresh_from_db()
            SupervisorCapability.objects.create(supervisor=target, manage_students=True)
            SupervisorReportPermission.objects.create(supervisor=target, view_reports=True, prepare_reports=True)
            response = self.client.get(permission_url(target))
            self.assertTrue(response.context["is_inactive"])
            self.assertFalse(response.context["can_grant_permissions"])
            for payload in ({"view_reports": "yes"}, {"view_reports": "yes", "prepare_reports": "yes"}):
                before = self.full_snapshot()
                self.assertEqual(self.save(target, **payload).status_code, 400)
                self.assertEqual(self.full_snapshot(), before)
            if target.role != Role.GENERAL_SUPERVISOR:
                before = self.full_snapshot()
                self.assertEqual(self.save(target, manage_students="yes").status_code, 400)
                self.assertEqual(self.full_snapshot(), before)
            self.assertEqual(self.save(target).status_code, 302)
            self.assertFalse(SupervisorReportPermission.objects.get(supervisor=target).view_reports)
            SupervisorReportPermission.objects.filter(supervisor=target).delete()
            self.assertEqual(self.save(target).status_code, 302)
            self.assertFalse(SupervisorReportPermission.objects.get(supervisor=target).view_reports)
            User.objects.filter(pk=target.pk).update(is_active=True)
            self.assertFalse(can_view_reports(target))
            if target.role != Role.GENERAL_SUPERVISOR:
                self.assertFalse(can_manage_students(target))

    def test_prepare_without_view_returns_400_without_any_partial_save(self):
        before = self.full_snapshot()
        response = self.save(manage_students="yes", prepare_reports="yes")
        self.assertEqual(response.status_code, 400)
        self.assertIn("prepare_reports", response.context["form"].errors)
        self.assertEqual(self.full_snapshot(), before)

    def test_every_checkbox_requires_yes_single_and_confirm_cannot_be_querystring(self):
        before = self.full_snapshot()
        for name in ("manage_students", "view_reports", "prepare_reports", "confirm"):
            for value in ("on", "true", "1", "no", "YES", " yes ", "", ["yes", "yes"], ["no", "yes"], ["yes", "no"]):
                with self.subTest(name=name, value=value):
                    payload = {"confirm": "yes", "view_reports": "yes", name: value}
                    response = self.client.post(permission_url(self.target), payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(name, response.context["form"].errors)
                    self.assertEqual(self.full_snapshot(), before)
        self.assertEqual(self.client.post(permission_url(self.target) + "?confirm=yes", {}).status_code, 400)
        self.assertEqual(self.full_snapshot(), before)

    def test_csrf_rejects_no_invalid_and_accepts_get_cookie_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        before = self.full_snapshot()
        self.assertEqual(client.post(permission_url(self.target), {"confirm": "yes"}).status_code, 403)
        response = client.get(permission_url(self.target))
        self.assertEqual(response.status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertIn('name="csrfmiddlewaretoken"', response.content.decode())
        self.assertEqual(client.post(permission_url(self.target), {"confirm": "yes", "csrfmiddlewaretoken": "x" * 64}).status_code, 403)
        self.assertEqual(self.full_snapshot(), before)
        self.assertEqual(client.post(permission_url(self.target), {"confirm": "yes", "csrfmiddlewaretoken": token}).status_code, 302)

    def test_real_change_audit_fields_actor_and_no_sensitive_payload(self):
        self.assertEqual(self.save(view_reports="yes").status_code, 302)
        log = LogEntry.objects.get()
        self.assertEqual(log.action_flag, CHANGE)
        self.assertEqual(log.user_id, self.actor.pk)
        self.assertEqual(log.object_id, str(self.target.pk))
        self.assertEqual(log.content_type, ContentType.objects.get_for_model(User))
        self.assertEqual(json.loads(log.change_message), [{"changed": {"fields": ["صلاحيات الطلاب والتقارير"]}}])
        for secret in (self.target.username, self.target.password, "SyntheticPermissions123!"):
            self.assertNotIn(secret, log.change_message)

    def test_audit_failure_after_insert_rolls_back_both_rows_and_audit(self):
        from participants import supervisor_permissions
        original = supervisor_permissions.record_management
        before = self.full_snapshot()

        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic audit failure")

        with patch.object(supervisor_permissions, "record_management", side_effect=fail_after_insert):
            with self.assertRaisesRegex(RuntimeError, "synthetic audit failure"):
                self.save(manage_students="yes", view_reports="yes", prepare_reports="yes")
        self.assertEqual(self.full_snapshot(), before)

    def test_audit_failure_rolls_back_existing_row_updates(self):
        SupervisorCapability.objects.create(supervisor=self.target, manage_students=True)
        SupervisorReportPermission.objects.create(supervisor=self.target, view_reports=True, prepare_reports=True)
        before = self.full_snapshot()
        with patch("participants.supervisor_permissions.record_management", side_effect=RuntimeError("synthetic update failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic update failure"):
                self.save()
        self.assertEqual(self.full_snapshot(), before)

    def test_sorted_user_lock_no_key_inside_atomic_and_policy_locks(self):
        from participants import supervisor_permissions
        original = supervisor_permissions.locked_users
        calls = []
        depth = len(connection.atomic_blocks)

        def inspect(*args, **kwargs):
            self.assertGreater(len(connection.atomic_blocks), depth)
            self.assertEqual(args, (self.actor.pk, self.target.pk))
            self.assertEqual(kwargs, {"no_key": True})
            calls.append(True)
            return original(*args, **kwargs)

        with patch.object(supervisor_permissions, "locked_users", side_effect=inspect):
            self.assertEqual(self.save(view_reports="yes").status_code, 302)
        self.assertEqual(calls, [True])

    def test_revalidates_actor_role_activity_under_lock_including_superadmin(self):
        from participants import supervisor_permissions
        original = supervisor_permissions.locked_users
        for fields in ({"role": Role.SUPERADMIN}, {"role": Role.GROUP_SUPERVISOR}, {"is_active": False}):
            before = self.full_snapshot()

            def change(*args, **kwargs):
                User.objects.filter(pk=self.actor.pk).update(**fields)
                return original(*args, **kwargs)

            with patch.object(supervisor_permissions, "locked_users", side_effect=change):
                self.assertEqual(self.save(view_reports="yes").status_code, 403)
            self.assertEqual(self.full_snapshot(), before)

    def test_revalidates_target_superuser_participant_role_and_activity_under_lock(self):
        from participants import supervisor_permissions
        original = supervisor_permissions.locked_users
        for fields, status in (({"role": Role.SUPERADMIN}, 404), ({"role": Role.PARTICIPANT}, 404),
                               ({"is_superuser": True}, 404), ({"participant": True}, 404), ({"is_active": False}, 400)):
            before = self.full_snapshot()

            def change(*args, **kwargs):
                if "participant" in fields:
                    Participant.objects.create(user=self.target, group=self.group, academic_stage="grade_7")
                else:
                    User.objects.filter(pk=self.target.pk).update(**fields)
                return original(*args, **kwargs)

            with patch.object(supervisor_permissions, "locked_users", side_effect=change):
                self.assertEqual(self.save(manage_students="yes", view_reports="yes").status_code, status)
            if status == 400:
                # No exception is raised for form errors; the synthetic activity
                # mutation commits, but grants/audit must remain absent.
                self.assertFalse(SupervisorCapability.objects.exists())
                self.assertFalse(SupervisorReportPermission.objects.exists())
                self.assertFalse(LogEntry.objects.exists())
                User.objects.filter(pk=self.target.pk).update(is_active=True)
            self.assertEqual(self.full_snapshot(), before)

    def test_form_is_rebuilt_from_locked_role_not_stale_general_disabled_field(self):
        from participants import supervisor_permissions
        original = supervisor_permissions.locked_users

        def demote_target(*args, **kwargs):
            User.objects.filter(pk=self.general.pk).update(role=Role.GROUP_SUPERVISOR)
            return original(*args, **kwargs)

        with patch.object(supervisor_permissions, "locked_users", side_effect=demote_target):
            self.assertEqual(self.save(self.general).status_code, 302)
        self.assertFalse(SupervisorCapability.objects.filter(supervisor=self.general).exists())
        self.assertFalse(can_manage_students(self.general))

    def test_fresh_actor_check_ignores_stale_inactive_or_role_on_request_user(self):
        stale = User.objects.get(pk=self.actor.pk)
        stale.role = Role.PARTICIPANT
        stale.is_active = False
        request = RequestFactory().get(permission_url(self.target))
        request.user = stale
        response = SupervisorStudentReportsView.as_view()(request, pk=self.target.pk)
        self.assertEqual(response.status_code, 200)
        stale.role = Role.GENERAL_SUPERVISOR
        stale.is_active = True
        User.objects.filter(pk=stale.pk).update(role=Role.SUPERADMIN)
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            SupervisorStudentReportsView.as_view()(request, pk=self.target.pk)

    def test_form_native_controls_yes_and_only_four_permission_fields(self):
        form = SupervisorStudentReportsForm(supervisor=self.general)
        self.assertEqual(set(form.fields), {"manage_students", "view_reports", "prepare_reports", "confirm"})
        for field in form.fields.values():
            self.assertEqual(field.widget.attrs["value"], "yes")
        self.assertTrue(form.fields["manage_students"].disabled)
        self.assertTrue(form.fields["confirm"].required)
        self.assertIn('type="checkbox"', str(form["view_reports"]))
        # Also exercise strict validation with a plain mapping, not QueryDict.
        form = SupervisorStudentReportsForm({"confirm": ["yes", "yes"]}, supervisor=self.limited)
        self.assertFalse(form.is_valid())
        self.assertIn("confirm", form.errors)


class SupervisorStudentReportMigrationTests(TransactionTestCase):
    def test_0027_is_additive_and_preserves_existing_users_flags_and_inheritance(self):
        migration = importlib.import_module("participants.migrations.0027_supervisor_student_report_permissions")
        self.assertEqual([type(op) for op in migration.Migration.operations], [migrations.AddField, migrations.CreateModel])
        migrate_from = [("participants", "0026_limited_task_grading_label")]
        migrate_to = [("participants", "0027_supervisor_student_report_permissions")]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(migrate_from)
            old_apps = executor.loader.project_state(migrate_from).apps
            old_user = old_apps.get_model("accounts", "User")
            old_capability = old_apps.get_model("participants", "SupervisorCapability")
            user = old_user.objects.create(username="migration-permission-fixture", role=Role.GENERAL_SUPERVISOR,
                                           full_name="مشرف هجرة مصطنع", password="!synthetic-unusable", is_active=True,
                                           is_staff=True, is_superuser=False)
            capability = old_capability.objects.create(supervisor_id=user.pk, manage_store=True, follow_tasks=True)
            old_user_snapshot = old_user.objects.values().get(pk=user.pk)
            old_flag_snapshot = old_capability.objects.values().get(pk=capability.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(migrate_to)
            self.assertEqual(User.objects.values().get(pk=user.pk), old_user_snapshot)
            self.assertEqual(SupervisorCapability.objects.values().get(pk=capability.pk), {
                **old_flag_snapshot, "manage_students": False,
            })
            self.assertFalse(SupervisorReportPermission.objects.exists())
            self.assertTrue(can_view_reports(User.objects.get(pk=user.pk)))
            self.assertTrue(can_prepare_reports(User.objects.get(pk=user.pk)))
            relation = SupervisorReportPermission._meta.get_field("supervisor")
            self.assertTrue(relation.one_to_one)
            self.assertEqual(relation.remote_field.related_name, "report_permissions")
            for name in ("view_reports", "prepare_reports"):
                self.assertIs(SupervisorReportPermission._meta.get_field(name).default, False)
        finally:
            MigrationExecutor(connection).migrate(migrate_to)
