"""Independent grade-edit permission contracts using synthetic local test data."""

import importlib
import json
import re
from unittest.mock import patch

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import AnonymousUser, Group as AuthGroup, Permission
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Role, User
from .access import can_review_task_submissions, can_view_task_submissions
from .capabilities import (
    can_edit_task_grades, can_follow_tasks, can_manage_store, can_manage_students,
    can_prepare_reports, can_view_reports, revoke_task_grade_grants,
)
from .models import (
    Group, Participant, PointsLedgerEntry, SupervisorCapability,
    SupervisorReportPermission, SupervisorTaskGradePermission, TaskReviewPermission,
    TaskSubmission,
)
from .supervisor_permission_forms import SupervisorStudentReportsForm
from .supervisor_permissions import SupervisorStudentReportsView
from .test_supervisor_student_report_permissions import TEST_SETTINGS, permission_url, synthetic_user


class TaskGradeCapabilityTests(TestCase):
    def setUp(self):
        self.actor = synthetic_user("grade-policy-manager", Role.GENERAL_SUPERVISOR)
        self.user = synthetic_user("grade-policy-user", Role.GROUP_SUPERVISOR)

    def test_missing_policy_preserves_only_general_default_without_rows(self):
        for role in Role.values + ["unknown_role"]:
            self.user.role = role
            User.objects.filter(pk=self.user.pk).update(role=role)
            with self.subTest(role=role), CaptureQueriesContext(connection) as queries:
                self.assertEqual(can_edit_task_grades(self.user), role == Role.GENERAL_SUPERVISOR)
            self.assertEqual(len(queries), 1)
        self.assertFalse(SupervisorTaskGradePermission.objects.exists())

    def test_explicit_false_denies_every_role_even_general_with_all_other_grants(self):
        SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=False)
        TaskReviewPermission.objects.create(supervisor=self.user, enabled=True)
        SupervisorCapability.objects.create(supervisor=self.user, follow_tasks=True, manage_store=True, manage_students=True)
        SupervisorReportPermission.objects.create(supervisor=self.user, view_reports=True, prepare_reports=True)
        for role in Role.values + ["unknown_role"]:
            self.user.role = role
            User.objects.filter(pk=self.user.pk).update(role=role)
            self.assertFalse(can_edit_task_grades(self.user), role)

    def test_explicit_true_role_and_operational_prerequisite_matrix(self):
        SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True)
        review = TaskReviewPermission.objects.create(supervisor=self.user, enabled=False)
        capability = SupervisorCapability.objects.create(supervisor=self.user, follow_tasks=False)
        for role in Role.values + ["unknown_role"]:
            self.user.role = role
            User.objects.filter(pk=self.user.pk).update(role=role)
            for review_enabled in (False, True):
                TaskReviewPermission.objects.filter(pk=review.pk).update(enabled=review_enabled)
                for follow in (False, True):
                    SupervisorCapability.objects.filter(pk=capability.pk).update(follow_tasks=follow)
                    with self.subTest(role=role, review=review_enabled, follow=follow):
                        expected = role == Role.GENERAL_SUPERVISOR or (
                            role == Role.GROUP_SUPERVISOR and review_enabled
                        ) or (role == Role.LIMITED_SUPERVISOR and follow)
                        self.assertEqual(can_edit_task_grades(self.user), expected)

    def test_group_and_limited_positive_policy_without_prerequisite_rows_denied(self):
        SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True)
        for role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR):
            self.user.role = role
            User.objects.filter(pk=self.user.pk).update(role=role)
            self.assertFalse(can_edit_task_grades(self.user))

    def test_initial_review_reports_store_and_students_never_implicitly_grant_editing(self):
        TaskReviewPermission.objects.create(supervisor=self.user, enabled=True)
        SupervisorCapability.objects.create(supervisor=self.user, follow_tasks=True, manage_store=True, manage_students=True)
        SupervisorReportPermission.objects.create(supervisor=self.user, view_reports=True, prepare_reports=True)
        for role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR):
            self.user.role = role
            User.objects.filter(pk=self.user.pk).update(role=role)
            self.assertTrue(can_review_task_submissions(self.user))
            self.assertFalse(can_edit_task_grades(self.user))

    def test_grade_policy_itself_never_grants_initial_review_view_reports_or_capabilities(self):
        self.user.role = Role.LIMITED_SUPERVISOR
        self.user.save(update_fields=["role"])
        SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True)
        for guard in (can_review_task_submissions, can_view_task_submissions, can_follow_tasks,
                      can_manage_store, can_manage_students, can_view_reports, can_prepare_reports):
            self.assertFalse(guard(self.user), guard.__name__)

    def test_denial_does_not_revoke_initial_review_or_independent_report_flags(self):
        self.user.role = Role.LIMITED_SUPERVISOR
        self.user.save(update_fields=["role"])
        SupervisorCapability.objects.create(supervisor=self.user, follow_tasks=True, manage_store=True, manage_students=True)
        SupervisorReportPermission.objects.create(supervisor=self.user, view_reports=True, prepare_reports=True)
        SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=False)
        self.assertFalse(can_edit_task_grades(self.user))
        for guard in (can_review_task_submissions, can_view_task_submissions, can_follow_tasks,
                      can_manage_store, can_manage_students, can_view_reports, can_prepare_reports):
            self.assertTrue(guard(self.user), guard.__name__)

    def test_cached_policy_and_review_relation_are_never_authorization(self):
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True)
        review = TaskReviewPermission.objects.create(supervisor=self.user, enabled=True)
        stale = User.objects.select_related("task_grade_permission", "task_review_permission").get(pk=self.user.pk)
        self.assertTrue(can_edit_task_grades(stale))
        TaskReviewPermission.objects.filter(pk=review.pk).update(enabled=False)
        self.assertTrue(stale.task_review_permission.enabled)
        self.assertFalse(can_edit_task_grades(stale))
        TaskReviewPermission.objects.filter(pk=review.pk).update(enabled=True)
        SupervisorTaskGradePermission.objects.filter(pk=policy.pk).update(enabled=False)
        self.assertTrue(stale.task_grade_permission.enabled)
        self.assertFalse(can_edit_task_grades(stale))

    def test_cached_limited_follow_flag_revocation_is_effective_immediately(self):
        self.user.role = Role.LIMITED_SUPERVISOR
        self.user.save(update_fields=["role"])
        SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True)
        capability = SupervisorCapability.objects.create(supervisor=self.user, follow_tasks=True)
        stale = User.objects.select_related("limited_capabilities", "task_grade_permission").get(pk=self.user.pk)
        self.assertTrue(can_edit_task_grades(stale))
        SupervisorCapability.objects.filter(pk=capability.pk).update(follow_tasks=False)
        self.assertTrue(stale.limited_capabilities.follow_tasks)
        self.assertFalse(can_edit_task_grades(stale))

    def test_stale_role_cannot_acquire_or_retain_another_roles_powers(self):
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True)
        TaskReviewPermission.objects.create(supervisor=self.user, enabled=True)
        stale = User.objects.get(pk=self.user.pk)
        self.assertTrue(can_edit_task_grades(stale))
        User.objects.filter(pk=self.user.pk).update(role=Role.GENERAL_SUPERVISOR)
        self.assertFalse(can_edit_task_grades(stale))
        self.assertTrue(can_edit_task_grades(User.objects.get(pk=stale.pk)))
        stale.role = Role.GENERAL_SUPERVISOR
        SupervisorTaskGradePermission.objects.filter(pk=policy.pk).delete()
        User.objects.filter(pk=self.user.pk).update(role=Role.PARTICIPANT)
        self.assertFalse(can_edit_task_grades(stale))
        stale.role = Role.PARTICIPANT
        User.objects.filter(pk=self.user.pk).update(role=Role.GENERAL_SUPERVISOR)
        self.assertFalse(can_edit_task_grades(stale))

    def test_persisted_activity_overrules_stale_flags_without_mutating_object(self):
        self.user.role = Role.GENERAL_SUPERVISOR
        self.user.save(update_fields=["role"])
        stale = User.objects.get(pk=self.user.pk)
        User.objects.filter(pk=stale.pk).update(is_active=False)
        self.assertFalse(can_edit_task_grades(stale))
        stale.is_active = False
        User.objects.filter(pk=stale.pk).update(is_active=True)
        self.assertTrue(can_edit_task_grades(stale))
        self.assertFalse(stale.is_active)

    def test_anonymous_unsaved_deleted_and_unknown_role_fail_closed(self):
        for user in (AnonymousUser(), User(role=Role.GENERAL_SUPERVISOR)):
            self.assertFalse(can_edit_task_grades(user))
        User.objects.filter(pk=self.user.pk).delete()
        self.assertFalse(can_edit_task_grades(self.user))

    def test_historical_staff_does_not_expand_or_block_platform_policy(self):
        self.user.role = Role.GENERAL_SUPERVISOR
        self.user.is_staff = True
        self.user.save(update_fields=["role", "is_staff"])
        self.assertTrue(can_edit_task_grades(self.user))
        self.user.role = Role.SUPERADMIN
        self.user.save(update_fields=["role"])
        SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True)
        self.assertFalse(can_edit_task_grades(self.user))

    def test_revoke_missing_and_explicit_false_are_exact_noops(self):
        self.assertFalse(revoke_task_grade_grants(self.user, self.actor))
        self.assertFalse(SupervisorTaskGradePermission.objects.exists())
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=False, granted_by=self.user)
        before = SupervisorTaskGradePermission.objects.values().get(pk=policy.pk)
        self.assertFalse(revoke_task_grade_grants(self.user, self.actor))
        self.assertEqual(SupervisorTaskGradePermission.objects.values().get(pk=policy.pk), before)

    def test_revoke_positive_is_audited_and_idempotent_without_unrelated_mutations(self):
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True, granted_by=self.user)
        report = SupervisorReportPermission.objects.create(supervisor=self.user, view_reports=True)
        capability = SupervisorCapability.objects.create(supervisor=self.user, follow_tasks=True)
        review = TaskReviewPermission.objects.create(supervisor=self.user, enabled=True)
        before = {
            model._meta.label: list(model.objects.values())
            for model in (User, SupervisorReportPermission, SupervisorCapability, TaskReviewPermission)
        }
        self.assertTrue(revoke_task_grade_grants(self.user, self.actor))
        policy.refresh_from_db()
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.granted_by_id, self.actor.pk)
        frozen = SupervisorTaskGradePermission.objects.values().get(pk=policy.pk)
        self.assertFalse(revoke_task_grade_grants(self.user, self.user))
        self.assertEqual(SupervisorTaskGradePermission.objects.values().get(pk=policy.pk), frozen)
        self.assertEqual(before, {
            model._meta.label: list(model.objects.values())
            for model in (User, SupervisorReportPermission, SupervisorCapability, TaskReviewPermission)
        })

    def test_revoke_holds_policy_lock_inside_atomic(self):
        from . import capabilities
        original = SupervisorTaskGradePermission.objects.select_for_update
        calls = []
        depth = len(connection.atomic_blocks)

        def inspect(*args, **kwargs):
            self.assertGreater(len(connection.atomic_blocks), depth)
            calls.append(True)
            return original(*args, **kwargs)

        with patch.object(capabilities.SupervisorTaskGradePermission.objects, "select_for_update", side_effect=inspect):
            self.assertFalse(revoke_task_grade_grants(self.user, self.actor))
        self.assertEqual(calls, [True])

    def test_policy_is_one_to_one_actor_set_null_and_target_cascade(self):
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True, granted_by=self.actor)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            SupervisorTaskGradePermission.objects.create(supervisor=self.user)
        self.actor.delete()
        policy.refresh_from_db()
        self.assertIsNone(policy.granted_by_id)
        self.assertTrue(policy.enabled)
        self.user.delete()
        self.assertFalse(SupervisorTaskGradePermission.objects.exists())


@override_settings(**TEST_SETTINGS)
class TaskGradePermissionViewTests(TestCase):
    def setUp(self):
        navbar = patch("participants.management_views.build_navbar", return_value=[])
        navbar.start()
        self.addCleanup(navbar.stop)
        self.actor = synthetic_user("grade-manager", Role.GENERAL_SUPERVISOR)
        self.group_user = synthetic_user("grade-group", Role.GROUP_SUPERVISOR, is_staff=True)
        self.general = synthetic_user("grade-general", Role.GENERAL_SUPERVISOR)
        self.limited = synthetic_user("grade-limited", Role.LIMITED_SUPERVISOR)
        self.root = synthetic_user("grade-system", Role.SUPERADMIN, is_superuser=True)
        self.group = Group.objects.create(name="بيئة تقييم مصطنعة")
        self.group.supervisor.add(self.group_user)
        self.student_user = synthetic_user("grade-student", Role.PARTICIPANT)
        self.student = Participant.objects.create(user=self.student_user, group=self.group, academic_stage="grade_7", points=9, miles=90, purchase_points=7)
        self.review = TaskReviewPermission.objects.create(supervisor=self.group_user, enabled=True, granted_by=self.actor)
        self.capability = SupervisorCapability.objects.create(supervisor=self.limited, follow_tasks=True, manage_store=True, granted_by=self.actor)
        self.client.force_login(self.actor)

    def save(self, user=None, **flags):
        return self.client.post(permission_url(user or self.group_user), {
            "confirm": "yes", "task_grade_permissions_present": "yes", **flags,
        })

    def snapshot(self):
        return {model._meta.label: list(model.objects.order_by("pk").values()) for model in (
            User, Group, Group.supervisor.through, Participant, PointsLedgerEntry,
            SupervisorCapability, SupervisorReportPermission, SupervisorTaskGradePermission,
            TaskReviewPermission, LogEntry, AuthGroup, User.groups.through, User.user_permissions.through,
        )}

    def test_get_head_render_initial_marker_and_general_inherited_checked_without_writes(self):
        for user in (self.group_user, self.general, self.limited):
            for method in (self.client.get, self.client.head):
                before = self.snapshot()
                response = method(permission_url(user))
                self.assertEqual(response.status_code, 200)
                form = response.context["form"]
                self.assertEqual(form.initial["edit_task_grades"], user.role == Role.GENERAL_SUPERVISOR)
                self.assertEqual(form.initial["task_grade_permissions_present"], "yes")
                self.assertFalse(form.fields["edit_task_grades"].disabled)
                self.assertEqual(self.snapshot(), before)
                self.assertIn("no-store", response.headers["Cache-Control"])

    def test_explicit_false_initial_is_unchecked_for_another_general(self):
        SupervisorTaskGradePermission.objects.create(supervisor=self.general, enabled=False)
        response = self.client.get(permission_url(self.general))
        self.assertFalse(response.context["form"].initial["edit_task_grades"])

    def test_active_eligible_general_group_limited_can_receive_explicit_grant(self):
        for user in (self.general, self.group_user, self.limited):
            self.assertEqual(self.save(user, edit_task_grades="yes").status_code, 302)
            policy = SupervisorTaskGradePermission.objects.get(supervisor=user)
            self.assertTrue(policy.enabled)
            self.assertEqual(policy.granted_by_id, self.actor.pk)
            self.assertTrue(can_edit_task_grades(user))

    def test_marker_yes_checkbox_absent_creates_explicit_false_even_general(self):
        for user in (self.general, self.group_user, self.limited):
            self.assertEqual(self.save(user).status_code, 302)
            self.assertFalse(SupervisorTaskGradePermission.objects.get(supervisor=user).enabled)
            self.assertFalse(can_edit_task_grades(user))

    def test_general_denial_preserves_role_actor_account_groups_and_report_policy(self):
        SupervisorReportPermission.objects.create(supervisor=self.general, view_reports=True, prepare_reports=True)
        auth_group = AuthGroup.objects.create(name="grade-policy-django-group")
        self.general.groups.add(auth_group)
        self.general.user_permissions.add(Permission.objects.first())
        before = self.snapshot()
        self.assertEqual(self.save(self.general, view_reports="yes", prepare_reports="yes", role=Role.PARTICIPANT,
                                   username="inject", is_active="", is_staff="yes", is_superuser="yes", password1="bad",
                                   environments=[self.group.pk], supervisor_id=self.actor.pk).status_code, 302)
        after = self.snapshot()
        for label in before:
            if label not in (SupervisorTaskGradePermission._meta.label, LogEntry._meta.label):
                self.assertEqual(after[label], before[label], label)
        self.assertTrue(can_view_reports(self.general))
        self.assertTrue(can_prepare_reports(self.general))
        self.assertFalse(can_edit_task_grades(self.general))
        self.assertTrue(can_edit_task_grades(self.actor))

    def test_group_grant_never_changes_historical_staff_environment_or_review(self):
        before = self.snapshot()
        self.assertEqual(self.save(edit_task_grades="yes").status_code, 302)
        after = self.snapshot()
        for model in (User, Group, Group.supervisor.through, Participant, TaskReviewPermission, SupervisorCapability):
            self.assertEqual(after[model._meta.label], before[model._meta.label])
        self.assertTrue(self.group_user.is_staff)

    def test_group_and_limited_without_operational_permission_rejected_atomically(self):
        self.review.enabled = False
        self.review.save(update_fields=["enabled"])
        self.capability.follow_tasks = False
        self.capability.save(update_fields=["follow_tasks"])
        for user in (self.group_user, self.limited):
            before = self.snapshot()
            response = self.save(user, edit_task_grades="yes", manage_students="yes", view_reports="yes")
            self.assertEqual(response.status_code, 400)
            self.assertIn("edit_task_grades", response.context["form"].errors)
            self.assertEqual(self.snapshot(), before)

    def test_old_post_without_marker_and_checkbox_preserves_missing_policy(self):
        for user in (self.general, self.group_user, self.limited):
            self.assertEqual(self.client.post(permission_url(user), {"confirm": "yes", "view_reports": "yes"}).status_code, 302)
        self.assertFalse(SupervisorTaskGradePermission.objects.exists())
        self.assertTrue(can_edit_task_grades(self.general))

    def test_old_post_preserves_existing_true_false_policy_actor_and_timestamp(self):
        for user, enabled in ((self.general, False), (self.group_user, True), (self.limited, True)):
            policy = SupervisorTaskGradePermission.objects.create(supervisor=user, enabled=enabled, granted_by=user)
            before = SupervisorTaskGradePermission.objects.values().get(pk=policy.pk)
            self.assertEqual(self.client.post(permission_url(user), {"confirm": "yes", "view_reports": "yes"}).status_code, 302)
            self.assertEqual(SupervisorTaskGradePermission.objects.values().get(pk=policy.pk), before)

    def test_new_checkbox_without_marker_is_rejected_even_when_yes(self):
        before = self.snapshot()
        response = self.client.post(permission_url(self.group_user), {"confirm": "yes", "edit_task_grades": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("task_grade_permissions_present", response.context["form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_new_checkbox_invalid_or_duplicate_values_always_rejected(self):
        for value in ("on", "true", "1", "no", "YES", " yes ", "", ["yes", "yes"], ["no", "yes"], ["yes", "no"]):
            with self.subTest(value=value):
                before = self.snapshot()
                response = self.save(edit_task_grades=value)
                self.assertEqual(response.status_code, 400)
                self.assertIn("edit_task_grades", response.context["form"].errors)
                self.assertEqual(self.snapshot(), before)

    def test_marker_invalid_empty_or_duplicate_always_rejected_with_or_without_checkbox(self):
        for value in ("on", "true", "1", "no", "YES", " yes ", "", ["yes", "yes"], ["no", "yes"], ["yes", "no"]):
            for checkbox in ({}, {"edit_task_grades": "yes"}):
                with self.subTest(value=value, checkbox=checkbox):
                    before = self.snapshot()
                    response = self.save(task_grade_permissions_present=value, **checkbox)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("task_grade_permissions_present", response.context["form"].errors)
                    self.assertEqual(self.snapshot(), before)

    def test_querystring_marker_does_not_authorize_checkbox_submission(self):
        before = self.snapshot()
        response = self.client.post(permission_url(self.group_user) + "?task_grade_permissions_present=yes", {
            "confirm": "yes", "edit_task_grades": "yes",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_plain_mapping_marker_and_checkbox_strictness(self):
        for payload, field in (({"confirm": "yes", "edit_task_grades": "yes"}, "task_grade_permissions_present"),
                               ({"confirm": "yes", "task_grade_permissions_present": ["yes", "yes"]}, "task_grade_permissions_present"),
                               ({"confirm": "yes", "task_grade_permissions_present": "yes", "edit_task_grades": ["yes", "yes"]}, "edit_task_grades")):
            form = SupervisorStudentReportsForm(payload, supervisor=self.general)
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)
        form = SupervisorStudentReportsForm({"confirm": "yes"}, supervisor=self.general)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["task_grade_permissions_present"], "")

    def test_every_original_boolean_and_confirmation_remain_strict_on_new_page(self):
        for name in ("manage_students", "view_reports", "prepare_reports", "confirm"):
            for value in ("on", "true", "1", "no", "YES", " yes ", "", ["yes", "yes"], ["no", "yes"]):
                before = self.snapshot()
                response = self.save(**{name: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(name, response.context["form"].errors)
                self.assertEqual(self.snapshot(), before)
        before = self.snapshot()
        self.assertEqual(self.client.post(permission_url(self.general), {"task_grade_permissions_present": "yes"}).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_targets_only_allow_withdrawal_and_do_not_regrant_after_activation(self):
        for user in (self.group_user, self.general, self.limited):
            User.objects.filter(pk=user.pk).update(is_active=False)
            user.refresh_from_db()
            policy = SupervisorTaskGradePermission.objects.create(supervisor=user, enabled=True)
            before = self.snapshot()
            self.assertEqual(self.save(user, edit_task_grades="yes").status_code, 400)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.save(user).status_code, 302)
            policy.refresh_from_db()
            self.assertFalse(policy.enabled)
            User.objects.filter(pk=user.pk).update(is_active=True)
            self.assertFalse(can_edit_task_grades(user))

    def test_inactive_target_missing_policy_can_receive_explicit_false_only(self):
        User.objects.filter(pk=self.general.pk).update(is_active=False)
        self.assertEqual(self.save(self.general).status_code, 302)
        self.assertFalse(SupervisorTaskGradePermission.objects.get(supervisor=self.general).enabled)

    def test_target_self_system_superuser_participant_profile_and_missing_rejected(self):
        superuser = synthetic_user("grade-superuser-target", Role.GENERAL_SUPERVISOR, is_superuser=True)
        backed = synthetic_user("grade-profile-target", Role.GROUP_SUPERVISOR)
        Participant.objects.create(user=backed, group=self.group, academic_stage="grade_7")
        for user, status in ((self.actor, 403), (self.root, 404), (superuser, 404), (self.student_user, 404), (backed, 404)):
            before = self.snapshot()
            self.assertEqual(self.save(user, edit_task_grades="yes").status_code, status)
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client.post("/__permission_contract/999999999/", {"confirm": "yes", "task_grade_permissions_present": "yes"}).status_code, 404)

    def test_only_general_actor_can_grant_not_system_group_limited_or_student(self):
        for actor in (self.root, self.group_user, self.limited, self.student_user):
            self.client.force_login(actor)
            before = self.snapshot()
            self.assertEqual(self.save(self.general, edit_task_grades="yes").status_code, 403)
            self.assertEqual(self.snapshot(), before)

    def test_locked_actor_role_activity_and_privilege_changes_rechecked(self):
        from . import supervisor_permissions
        original = supervisor_permissions.locked_users
        for fields in ({"role": Role.SUPERADMIN}, {"role": Role.PARTICIPANT}, {"is_active": False}):
            before = self.snapshot()

            def mutate(*args, **kwargs):
                User.objects.filter(pk=self.actor.pk).update(**fields)
                return original(*args, **kwargs)

            with patch.object(supervisor_permissions, "locked_users", side_effect=mutate):
                self.assertEqual(self.save(self.general, edit_task_grades="yes").status_code, 403)
            self.assertEqual(self.snapshot(), before)

    def test_locked_target_activity_and_operational_revocation_rechecked_before_writes(self):
        from . import supervisor_permissions
        original = supervisor_permissions.locked_users
        for mutation in ("activity", "review", "follow"):
            user = self.limited if mutation == "follow" else self.group_user

            def mutate(*args, **kwargs):
                if mutation == "activity":
                    User.objects.filter(pk=user.pk).update(is_active=False)
                elif mutation == "review":
                    TaskReviewPermission.objects.filter(pk=self.review.pk).update(enabled=False)
                else:
                    SupervisorCapability.objects.filter(pk=self.capability.pk).update(follow_tasks=False)
                return original(*args, **kwargs)

            with patch.object(supervisor_permissions, "locked_users", side_effect=mutate):
                self.assertEqual(self.save(user, edit_task_grades="yes").status_code, 400)
            self.assertFalse(SupervisorTaskGradePermission.objects.exists())
            self.assertFalse(SupervisorReportPermission.objects.exists())
            self.assertFalse(LogEntry.objects.exists())
            User.objects.filter(pk=self.group_user.pk).update(is_active=True)
            TaskReviewPermission.objects.filter(pk=self.review.pk).update(enabled=True)
            SupervisorCapability.objects.filter(pk=self.capability.pk).update(follow_tasks=True)

    def test_equal_policy_flags_are_exact_noop_including_actor_timestamp_and_audit(self):
        SupervisorReportPermission.objects.create(supervisor=self.group_user, view_reports=False, prepare_reports=False, granted_by=self.group_user)
        SupervisorTaskGradePermission.objects.create(supervisor=self.group_user, enabled=True, granted_by=self.group_user)
        before = self.snapshot()
        self.assertEqual(self.save(edit_task_grades="yes").status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_explicit_withdrawal_changes_only_policy_and_one_audit_with_correct_actor(self):
        SupervisorReportPermission.objects.create(supervisor=self.general, view_reports=False, prepare_reports=False, granted_by=self.general)
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.general, enabled=True, granted_by=self.general)
        before = self.snapshot()
        self.assertEqual(self.save(self.general).status_code, 302)
        policy.refresh_from_db()
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.granted_by_id, self.actor.pk)
        log = LogEntry.objects.get()
        self.assertEqual(log.action_flag, CHANGE)
        self.assertEqual(log.user_id, self.actor.pk)
        self.assertEqual(log.object_id, str(self.general.pk))
        self.assertEqual(json.loads(log.change_message), [{"changed": {"fields": ["صلاحيات الطلاب والتقارير والتقييم"]}}])
        self.assertNotIn(self.general.password, log.change_message)
        self.assertNotIn(self.general.username, log.change_message)
        after = self.snapshot()
        for label in before:
            if label not in (SupervisorTaskGradePermission._meta.label, LogEntry._meta.label):
                self.assertEqual(before[label], after[label])
        frozen = self.snapshot()
        self.assertEqual(self.save(self.general).status_code, 302)
        self.assertEqual(self.snapshot(), frozen)

    def test_audit_failure_rolls_back_new_grade_report_and_capability_rows(self):
        before = self.snapshot()
        with patch("participants.supervisor_permissions.record_management", side_effect=RuntimeError("synthetic grade audit failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic grade audit failure"):
                self.save(edit_task_grades="yes", view_reports="yes", manage_students="yes")
        self.assertEqual(self.snapshot(), before)

    def test_audit_failure_rolls_back_existing_grade_policy_updates(self):
        SupervisorReportPermission.objects.create(supervisor=self.general, view_reports=True)
        SupervisorTaskGradePermission.objects.create(supervisor=self.general, enabled=True)
        before = self.snapshot()
        with patch("participants.supervisor_permissions.record_management", side_effect=RuntimeError("synthetic grade update failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic grade update failure"):
                self.save(self.general)
        self.assertEqual(self.snapshot(), before)

    def test_policy_locks_follow_ordered_user_lock_and_remain_in_atomic(self):
        from . import supervisor_permissions
        original_users = supervisor_permissions.locked_users
        original_policy = SupervisorTaskGradePermission.objects.select_for_update
        sequence = []
        depth = len(connection.atomic_blocks)

        def users(*args, **kwargs):
            self.assertEqual(args, (self.actor.pk, self.general.pk))
            self.assertEqual(kwargs, {"no_key": True})
            self.assertGreater(len(connection.atomic_blocks), depth)
            sequence.append("users")
            return original_users(*args, **kwargs)

        def policy(*args, **kwargs):
            self.assertEqual(sequence, ["users"])
            self.assertGreater(len(connection.atomic_blocks), depth)
            sequence.append("policy")
            return original_policy(*args, **kwargs)

        with patch.object(supervisor_permissions, "locked_users", side_effect=users), patch.object(
            supervisor_permissions.SupervisorTaskGradePermission.objects, "select_for_update", side_effect=policy,
        ):
            self.assertEqual(self.save(self.general).status_code, 302)
        self.assertEqual(sequence, ["users", "policy"])

    def test_grade_policy_save_never_writes_user_environment_participant_or_django_auth(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.save(edit_task_grades="yes").status_code, 302)
        writes = [q["sql"] for q in queries if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", q["sql"], re.I)]
        self.assertTrue(writes)
        for sql in writes:
            for table in ("accounts_user", "accounts_user_groups", "accounts_user_user_permissions", "participants_group",
                          "participants_group_supervisor", "participants_participant", "participants_taskreviewpermission"):
                self.assertNotIn('"' + table + '"', sql)

    def test_csrf_and_confirmation_still_protect_new_policy(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        before = self.snapshot()
        self.assertEqual(client.post(permission_url(self.general), {"confirm": "yes", "task_grade_permissions_present": "yes"}).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        response = client.get(permission_url(self.general))
        self.assertEqual(response.status_code, 200)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(permission_url(self.general), {
            "csrfmiddlewaretoken": token, "confirm": "yes", "task_grade_permissions_present": "yes",
        }).status_code, 302)
        self.assertFalse(SupervisorTaskGradePermission.objects.get(supervisor=self.general).enabled)


class TaskGradePermissionLifecycleTests(TestCase):
    def setUp(self):
        self.actor = synthetic_user("grade-lifecycle-manager", Role.GENERAL_SUPERVISOR)
        self.user = synthetic_user("grade-lifecycle-user", Role.GROUP_SUPERVISOR)
        self.group = Group.objects.create(name="بيئة دورة حياة التقييم")
        self.group.supervisor.add(self.user)
        self.other_group = Group.objects.create(name="بيئة دورة حياة أخرى")
        self.review = TaskReviewPermission.objects.create(supervisor=self.user, enabled=True, granted_by=self.actor)
        self.policy = SupervisorTaskGradePermission.objects.create(supervisor=self.user, enabled=True, granted_by=self.user)
        self.client.force_login(self.actor)

    def edit(self, *, role=Role.GROUP_SUPERVISOR, active=True, groups=None):
        if groups is None:
            groups = [self.group.pk] if role == Role.GROUP_SUPERVISOR else []
        return self.client.post(reverse("participants:supervisor_edit", kwargs={"pk": self.user.pk}), {
            "full_name": self.user.full_name, "username": self.user.username, "role": role,
            "is_active": "yes" if active else "", "environments": groups,
        })

    def frozen_policy(self):
        return SupervisorTaskGradePermission.objects.values().get(pk=self.policy.pk)

    def assert_revoked(self):
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.enabled)
        self.assertEqual(self.policy.granted_by_id, self.actor.pk)

    def test_safe_disable_revokes_positive_and_reactivation_never_restores_it(self):
        self.assertEqual(self.client.post(reverse("participants:supervisor_delete", kwargs={"pk": self.user.pk}), {"confirm": "yes"}).status_code, 302)
        self.assert_revoked()
        frozen = self.frozen_policy()
        self.assertEqual(self.edit(active=True).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(can_edit_task_grades(self.user))

    def test_general_disabled_through_edit_cannot_restore_positive_on_reactivation(self):
        User.objects.filter(pk=self.user.pk).update(role=Role.GENERAL_SUPERVISOR)
        self.user.refresh_from_db()
        self.assertEqual(self.edit(role=Role.GENERAL_SUPERVISOR, active=False).status_code, 302)
        self.assert_revoked()
        frozen = self.frozen_policy()
        self.assertEqual(self.edit(role=Role.GENERAL_SUPERVISOR, active=True).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.user.refresh_from_db()
        self.assertFalse(can_edit_task_grades(self.user))

    def test_role_edit_promotion_revokes_positive_not_inherits_over_denial(self):
        self.assertEqual(self.edit(role=Role.GENERAL_SUPERVISOR).status_code, 302)
        self.assert_revoked()
        self.user.refresh_from_db()
        self.assertFalse(can_edit_task_grades(self.user))

    def test_dedicated_general_promotion_revokes_positive_and_repeat_does_not_change_policy(self):
        url = reverse("participants:supervisor_grant_general", kwargs={"pk": self.user.pk})
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 302)
        self.assert_revoked()
        frozen = self.frozen_policy()
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.user.refresh_from_db()
        self.assertFalse(can_edit_task_grades(self.user))

    def test_conversion_to_limited_revokes_positive_even_when_follow_tasks_is_granted(self):
        url = reverse("participants:supervisor_capabilities", kwargs={"pk": self.user.pk})
        self.assertEqual(self.client.post(url, {"confirm": "yes", "follow_tasks": "yes"}).status_code, 302)
        self.assert_revoked()
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.LIMITED_SUPERVISOR)
        self.assertTrue(can_review_task_submissions(self.user))
        self.assertFalse(can_edit_task_grades(self.user))
        self.assertFalse(self.user.group_set.exists())

    def test_assignment_change_withdraws_review_and_effective_edit_without_mutating_policy(self):
        frozen = self.frozen_policy()
        self.assertTrue(can_edit_task_grades(self.user))
        self.assertEqual(self.edit(groups=[self.other_group.pk]).status_code, 302)
        self.review.refresh_from_db()
        self.assertFalse(self.review.enabled)
        self.assertEqual(self.frozen_policy(), frozen)
        self.assertFalse(can_edit_task_grades(self.user))

    def test_assignment_alone_does_not_grant_edit_permission(self):
        self.policy.delete()
        self.assertEqual(self.edit(groups=[self.group.pk, self.other_group.pk]).status_code, 302)
        self.assertFalse(SupervisorTaskGradePermission.objects.filter(supervisor=self.user).exists())
        self.assertFalse(can_edit_task_grades(self.user))

    def test_follow_tasks_withdrawal_disables_intersection_without_changing_grade_policy(self):
        User.objects.filter(pk=self.user.pk).update(role=Role.LIMITED_SUPERVISOR)
        self.user.refresh_from_db()
        SupervisorCapability.objects.create(supervisor=self.user, follow_tasks=True)
        frozen = self.frozen_policy()
        self.assertTrue(can_edit_task_grades(self.user))
        self.assertEqual(self.client.post(reverse("participants:supervisor_capabilities", kwargs={"pk": self.user.pk}), {"confirm": "yes"}).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.assertFalse(can_edit_task_grades(self.user))

    def test_explicit_false_survives_disable_role_changes_conversion_and_promotion(self):
        SupervisorTaskGradePermission.objects.filter(pk=self.policy.pk).update(enabled=False)
        frozen = self.frozen_policy()
        self.assertEqual(self.edit(active=False).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.assertEqual(self.edit(active=True).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.assertEqual(self.client.post(reverse("participants:supervisor_capabilities", kwargs={"pk": self.user.pk}), {"confirm": "yes", "follow_tasks": "yes"}).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.assertEqual(self.client.post(reverse("participants:supervisor_grant_general", kwargs={"pk": self.user.pk}), {"confirm": "yes"}).status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.user.refresh_from_db()
        self.assertFalse(can_edit_task_grades(self.user))

    def test_missing_policy_is_not_created_by_lifecycle_or_promotion(self):
        self.policy.delete()
        self.assertEqual(self.edit(active=False).status_code, 302)
        self.assertEqual(self.edit(active=True).status_code, 302)
        self.assertEqual(self.client.post(reverse("participants:supervisor_grant_general", kwargs={"pk": self.user.pk}), {"confirm": "yes"}).status_code, 302)
        self.assertFalse(SupervisorTaskGradePermission.objects.filter(supervisor=self.user).exists())
        self.user.refresh_from_db()
        self.assertTrue(can_edit_task_grades(self.user))

    def test_no_role_activity_or_assignment_change_preserves_positive(self):
        frozen = self.frozen_policy()
        self.assertEqual(self.edit().status_code, 302)
        self.assertEqual(self.frozen_policy(), frozen)
        self.assertTrue(can_edit_task_grades(self.user))

    def test_lifecycle_revoke_runs_after_user_locks_inside_atomic(self):
        from . import management_views
        original = management_views.revoke_task_grade_grants
        depth = len(connection.atomic_blocks)
        calls = []

        def inspect(user, actor):
            self.assertGreater(len(connection.atomic_blocks), depth)
            self.assertEqual(user.pk, self.user.pk)
            self.assertEqual(actor.pk, self.actor.pk)
            self.assertFalse(User.objects.get(pk=user.pk).is_active)
            calls.append(True)
            return original(user, actor)

        with patch.object(management_views, "revoke_task_grade_grants", side_effect=inspect):
            self.assertEqual(self.edit(active=False).status_code, 302)
        self.assertEqual(calls, [True])
        self.assert_revoked()

    def test_list_displays_effective_state_and_existing_permission_link_substring(self):
        response = self.client.get(reverse("participants:supervisor_list"))
        self.assertEqual(response.status_code, 200)
        rows = {row.pk: row for row in response.context["supervisors"]}
        self.assertTrue(rows[self.user.pk].task_grade_edit_allowed)
        self.assertContains(response, "صلاحيات الطلاب والتقارير والتقييم")
        self.assertContains(response, "تعديل تقييم المهام والتميز")
        TaskReviewPermission.objects.filter(pk=self.review.pk).update(enabled=False)
        response = self.client.get(reverse("participants:supervisor_list"))
        rows = {row.pk: row for row in response.context["supervisors"]}
        self.assertFalse(rows[self.user.pk].task_grade_edit_allowed)

    def test_real_form_template_includes_hidden_marker_and_yes_checkbox(self):
        response = self.client.get(reverse("participants:supervisor_student_reports", kwargs={"pk": self.user.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="task_grade_permissions_present" value="yes"')
        self.assertContains(response, 'name="edit_task_grades" value="yes"')
        self.assertContains(response, "صلاحيات الطلاب والتقارير والتقييم")


class TaskGradePermissionMigrationTests(TransactionTestCase):
    def test_0030_is_additive_no_bulk_policies_or_user_report_capability_changes(self):
        migration = importlib.import_module("participants.migrations.0030_task_grade_permissions")
        self.assertEqual([type(op) for op in migration.Migration.operations], [migrations.CreateModel])
        self.assertFalse(any(isinstance(op, migrations.RunPython) for op in migration.Migration.operations))
        migrate_from = [("participants", "0029_elite_trip_notifications")]
        migrate_to = [("participants", "0030_task_grade_permissions")]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(migrate_from)
            old_apps = executor.loader.project_state(migrate_from).apps
            old_user = old_apps.get_model("accounts", "User")
            old_cap = old_apps.get_model("participants", "SupervisorCapability")
            old_report = old_apps.get_model("participants", "SupervisorReportPermission")
            user = old_user.objects.create(username="grade-migration-fixture", full_name="مشرف هجرة تقييم مصطنع", role=Role.GENERAL_SUPERVISOR,
                                           is_active=True, is_staff=True, password="!synthetic-unusable")
            old_cap.objects.create(supervisor_id=user.pk, follow_tasks=True, manage_store=True, manage_students=True)
            old_report.objects.create(supervisor_id=user.pk, view_reports=False, prepare_reports=False)
            before = {model._meta.label: list(model.objects.values()) for model in (old_user, old_cap, old_report)}
            executor = MigrationExecutor(connection)
            executor.migrate(migrate_to)
            new_apps = executor.loader.project_state(migrate_to).apps
            for name in ("User", "SupervisorCapability", "SupervisorReportPermission"):
                model = new_apps.get_model("accounts" if name == "User" else "participants", name)
                self.assertEqual(list(model.objects.values()), before[model._meta.label])
            policy = new_apps.get_model("participants", "SupervisorTaskGradePermission")
            self.assertFalse(policy.objects.exists())
            self.assertFalse(policy._meta.get_field("enabled").default)
            self.assertEqual(policy._meta.get_field("supervisor").remote_field.related_name, "task_grade_permission")
            self.assertEqual(policy._meta.get_field("granted_by").remote_field.related_name, "+")
            self.assertTrue(can_edit_task_grades(User.objects.get(pk=user.pk)))
        finally:
            MigrationExecutor(connection).migrate(latest)
