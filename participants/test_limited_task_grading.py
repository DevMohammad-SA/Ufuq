"""Synthetic limited grading: the existing follow_tasks flag grants accept/reject only."""
import datetime
from unittest.mock import Mock, patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .access import (
    can_manage_tasks, can_review_task_submissions, can_view_task_submissions,
    scoped_submissions, task_participants,
)
from .capabilities import can_follow_tasks, can_manage_store
from .models import (
    Group, Participant, PointsLedgerEntry, SupervisorCapability, TaskGradeRevision,
    TaskReviewPermission, TaskSubmission, WeeklyTask,
)
from .reporting import REPORT_ROLES, scoped_participants
from .task_grading import can_revise_grades
from .views import WeeklyTaskReviewView, apply_points_delta


class LimitedTaskGradingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = cls.user("limited-grade-general", Role.GENERAL_SUPERVISOR)
        cls.limited = cls.user("limited-grade-reviewer", Role.LIMITED_SUPERVISOR)
        cls.other = cls.user("limited-grade-other", Role.LIMITED_SUPERVISOR)
        cls.grant = SupervisorCapability.objects.create(
            supervisor=cls.limited, follow_tasks=True, granted_by=cls.general,
        )
        SupervisorCapability.objects.create(supervisor=cls.other, follow_tasks=True)
        cls.store_only = cls.user("limited-grade-store", Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=cls.store_only, manage_store=True)
        cls.no_grant = cls.user("limited-grade-no-grant", Role.LIMITED_SUPERVISOR)
        # A legacy group permission must neither be needed nor expand powers.
        TaskReviewPermission.objects.create(supervisor=cls.store_only, enabled=True)
        cls.alpha = Group.objects.create(name="بيئة التصحيح الأولى")
        cls.beta = Group.objects.create(name="بيئة التصحيح الأخرى")
        cls.participant = cls.student("limited-grade-student", cls.alpha)
        cls.foreign = cls.student("limited-grade-foreign", cls.beta)
        cls.ungrouped = cls.student("limited-grade-ungrouped", None)
        cls.inactive = cls.student("limited-grade-inactive", cls.beta)
        User.objects.filter(pk=cls.inactive.user_id).update(is_active=False)
        cls.staff = Participant.objects.create(
            user=cls.user("limited-grade-staff", Role.GROUP_SUPERVISOR), group=cls.alpha,
        )
        cls.task = WeeklyTask.objects.create(
            title="مهمة تصحيح مصطنعة", description="وصف محفوظ", allowed_formats="pdf,text",
            due_date=timezone.localdate() + datetime.timedelta(days=2), is_active=True,
            created_by=cls.general,
        )
        cls.submission = cls.upload(cls.participant)
        cls.foreign_submission = cls.upload(cls.foreign)
        cls.ungrouped_submission = cls.upload(cls.ungrouped)
        cls.inactive_submission = cls.upload(cls.inactive)
        cls.staff_submission = cls.upload(cls.staff)
        cls.original_upload = timezone.now() - datetime.timedelta(days=9)
        TaskSubmission.objects.filter(pk=cls.submission.pk).update(submitted_at=cls.original_upload)
        cls.submission.refresh_from_db()

    @staticmethod
    def user(username, role):
        return User.objects.create_user(username=username, full_name=username, role=role)

    @classmethod
    def student(cls, username, group):
        return Participant.objects.create(
            user=cls.user(username, Role.PARTICIPANT), group=group,
            points=31, miles=310, purchase_points=17,
        )

    @classmethod
    def upload(cls, participant, **extra):
        return TaskSubmission.objects.create(
            task=cls.task, participant=participant, file="task_submissions/synthetic-original.pdf",
            text_content="عمل الطالب محفوظ <script>ولا يُنفّذ</script>", **extra,
        )

    def setUp(self):
        self.url = reverse("participants:weekly_task_review")
        self.client.force_login(self.limited)

    def snapshot(self):
        models = (User, SupervisorCapability, TaskReviewPermission, WeeklyTask,
                  TaskSubmission, Participant, PointsLedgerEntry, TaskGradeRevision)
        return {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}

    def post(self, action="accept", submission=None, **extra):
        data = {"action": action, "submission_id": (submission or self.submission).pk}
        data.update(extra)
        return self.client.post(self.url, data)

    def assert_balances(self, participant=None, delta=0):
        participant = participant or self.participant
        participant.refresh_from_db()
        self.assertEqual((participant.points, participant.miles, participant.purchase_points),
                         (31 + delta, 310 + delta * 10, 17 + delta))

    def assert_award(self, points, featured=False, participant=None, submission=None):
        participant = participant or self.participant
        submission = submission or self.submission
        submission.refresh_from_db()
        self.assertEqual(submission.status, TaskSubmission.Status.ACCEPTED)
        self.assertEqual(submission.is_featured, featured)
        self.assertTrue(submission.points_awarded)
        self.assertEqual(submission.award_value, points)
        self.assertEqual(submission.reviewed_by_id, self.limited.pk)
        self.assertIsNotNone(submission.reviewed_at)
        self.assertEqual(submission.file.name, "task_submissions/synthetic-original.pdf")
        self.assertEqual(submission.text_content, "عمل الطالب محفوظ <script>ولا يُنفّذ</script>")
        self.assert_balances(participant, delta=points)
        entry = PointsLedgerEntry.objects.get(participant=participant)
        self.assertEqual(entry.points_delta, points)
        self.assertEqual(entry.source, PointsLedgerEntry.Source.WEEKLY_TASK)
        self.assertEqual(entry.granted_by_id, self.limited.pk)
        self.assertEqual(entry.event_date, timezone.localdate(submission.submitted_at))
        self.assertIn(self.task.title, entry.description)
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_existing_grant_accepts_normal_using_original_upload_date(self):
        with patch("participants.views.apply_points_delta", wraps=apply_points_delta) as apply:
            self.assertEqual(self.post().status_code, 302)
        self.assertEqual(apply.call_count, 1)
        self.assert_award(10)
        self.assertEqual(self.submission.submitted_at, self.original_upload)
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.granted_by_id, self.general.pk)
        self.limited.refresh_from_db()
        self.assertEqual(self.limited.role, Role.LIMITED_SUPERVISOR)

    def test_featured_checkbox_grants_twelve_and_clears_rejection(self):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(rejection_reason="سبب سابق")
        self.assertEqual(self.post(is_featured="on").status_code, 302)
        self.assert_award(12, featured=True)
        self.assertEqual(self.submission.rejection_reason, "")

    def test_featured_requires_explicit_checkbox_value(self):
        self.assertEqual(self.post(is_featured="true").status_code, 302)
        self.assert_award(10)

    def test_pending_zero_award_reuses_normal_award_path(self):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(points_awarded=False, award_value=0)
        self.assertEqual(self.post(is_featured="on").status_code, 302)
        self.assert_award(12, featured=True)

    def test_already_awarded_pending_does_not_award_again(self):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(points_awarded=True, award_value=10)
        with patch("participants.views.apply_points_delta") as apply:
            self.assertEqual(self.post(is_featured="on").status_code, 302)
        apply.assert_not_called()
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, "accepted")
        self.assertEqual(self.submission.award_value, 10)
        self.assert_balances()
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_reject_trims_arabic_reason_and_never_grants_points(self):
        with patch("participants.views.apply_points_delta") as apply:
            self.assertEqual(self.post("reject", rejection_reason="  يحتاج شرحًا أوضح\n", is_featured="on").status_code, 302)
        apply.assert_not_called()
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, "rejected")
        self.assertEqual(self.submission.rejection_reason, "يحتاج شرحًا أوضح")
        self.assertFalse(self.submission.is_featured)
        self.assertFalse(self.submission.points_awarded)
        self.assertEqual(self.submission.reviewed_by_id, self.limited.pk)
        self.assertEqual(self.submission.submitted_at, self.original_upload)
        self.assert_balances()
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_rejection_reason_optional_and_unknown_award_not_resolved(self):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(points_awarded=None)
        self.assertEqual(self.post("reject", historical_award="not_awarded").status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.rejection_reason, "")
        self.assertIsNone(self.submission.points_awarded)
        self.assertIsNone(self.submission.award_value)
        self.assert_balances()
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_all_real_participants_including_foreign_ungrouped_inactive_are_in_scope(self):
        for submission in (self.foreign_submission, self.ungrouped_submission, self.inactive_submission):
            with self.subTest(participant=submission.participant_id):
                self.assertEqual(self.post(submission=submission).status_code, 302)
                submission.refresh_from_db()
                self.assertEqual(submission.reviewed_by_id, self.limited.pk)
                self.assert_balances(submission.participant, delta=10)
        self.assertEqual(PointsLedgerEntry.objects.count(), 3)
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_expired_inactive_task_decisions_follow_same_review_flow(self):
        WeeklyTask.objects.filter(pk=self.task.pk).update(
            is_active=False, due_date=timezone.localdate() - datetime.timedelta(days=5),
        )
        for action, submission in (("accept", self.submission), ("reject", self.foreign_submission)):
            with self.subTest(action=action):
                self.assertEqual(self.post(action, submission=submission).status_code, 302)
                submission.refresh_from_db()
                self.assertEqual(submission.status, "accepted" if action == "accept" else "rejected")
        self.assert_award(10)
        self.assert_balances(self.foreign)
        self.task.refresh_from_db()
        self.assertFalse(self.task.is_active)
        self.assertEqual(self.task.due_date, timezone.localdate() - datetime.timedelta(days=5))

    def test_accept_historical_unknown_denied_even_with_valid_override(self):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(points_awarded=None)
        before = self.snapshot()
        for historical in (None, "already_awarded", "not_awarded", "0", "12"):
            with self.subTest(historical=historical):
                extra = {} if historical is None else {"historical_award": historical}
                self.assertEqual(self.post(is_featured="on", **extra).status_code, 403)
                self.assertEqual(self.snapshot(), before)

    def test_duplicate_and_stale_final_or_returned_posts_are_noops(self):
        self.assertEqual(self.post().status_code, 302)
        for actor in (self.limited, self.other):
            self.client.force_login(actor)
            before = self.snapshot()
            for action in ("accept", "reject"):
                with self.subTest(actor=actor.username, action=action):
                    self.assertEqual(self.post(action, is_featured="on", rejection_reason="قديم").status_code, 302)
                    self.assertEqual(self.snapshot(), before)
        for state in ("rejected", "returned"):
            TaskSubmission.objects.filter(pk=self.foreign_submission.pk).update(status=state)
            before = self.snapshot()
            self.assertEqual(self.post(submission=self.foreign_submission).status_code, 302)
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(PointsLedgerEntry.objects.count(), 1)

    def test_forbidden_actions_denied_before_any_identifiers_or_legacy_permission(self):
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(manage_store=True)
        TaskReviewPermission.objects.create(supervisor=self.limited, enabled=True)
        before = self.snapshot()
        actions = ("return_for_revision", "create_task", "update_task", "toggle_active",
                   "reopen", "cancel_grade", "revise_grade", "unsupported", "", None)
        for action in actions:
            for identifier in (str(self.submission.pk), str(self.staff_submission.pk), "malformed", "9" * 70):
                with self.subTest(action=action, identifier=identifier):
                    data = {"submission_id": identifier, "task_id": identifier, "is_featured": "on",
                            "historical_award": "not_awarded", "confirm": "yes", "revision_note": "ملاحظة"}
                    if action is not None:
                        data["action"] = action
                    self.assertEqual(self.client.post(self.url, data).status_code, 403)
                    self.assertEqual(self.snapshot(), before)
        request = RequestFactory().post(self.url)
        request.user = self.limited
        request._post = Mock()
        request._post.get.return_value = "unsupported"
        request._files = {}
        view = WeeklyTaskReviewView()
        view.setup(request)
        with patch("participants.models.TaskReviewPermission.objects.select_for_update", side_effect=AssertionError("legacy grant must not be read")):
            with self.assertRaises(PermissionDenied):
                view.post(request)
        request.POST.get.assert_called_once_with("action")

    def test_invalid_missing_oversized_and_staff_ids_are_404_without_changes(self):
        before = self.snapshot()
        for identifier in ("", "bad", "-1", "0", "999999", "9" * 70,
                           "9223372036854775808", str(self.staff_submission.pk)):
            for action in ("accept", "reject"):
                with self.subTest(identifier=identifier, action=action):
                    self.assertEqual(self.client.post(self.url, {"action": action, "submission_id": identifier}).status_code, 404)
                    self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client.post(self.url, {"action": "accept"}).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_filters_preserved_and_arbitrary_return_url_ignored(self):
        response = self.client.post(self.url + f"?status=pending&task={self.task.pk}&next=https://example.invalid/", {
            "action": "accept", "submission_id": self.submission.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url + f"?status=pending&task={self.task.pk}")

    def test_fresh_helpers_and_context_do_not_expand_other_powers(self):
        self.assertTrue(can_follow_tasks(self.limited))
        self.assertTrue(can_review_task_submissions(self.limited))
        self.assertTrue(can_view_task_submissions(self.limited))
        self.assertFalse(can_manage_tasks(self.limited))
        self.assertFalse(can_revise_grades(self.limited))
        self.assertNotIn(Role.LIMITED_SUPERVISOR, REPORT_ROLES)
        self.assertFalse(scoped_participants(self.limited).exists())
        expected = {self.participant.pk, self.foreign.pk, self.ungrouped.pk, self.inactive.pk}
        self.assertEqual(set(task_participants(self.limited).values_list("pk", flat=True)), expected)
        self.assertNotIn(self.staff_submission.pk, scoped_submissions(self.limited).values_list("pk", flat=True))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_review_submissions"])
        for name in ("can_manage_tasks", "can_return_submissions", "can_revise_grades", "can_view_student_detail"):
            self.assertFalse(response.context[name])

    def test_store_only_missing_and_false_grants_do_not_allow_grading(self):
        self.assertTrue(can_manage_store(self.store_only))
        for actor in (self.store_only, self.no_grant):
            self.client.force_login(actor)
            before = self.snapshot()
            self.assertFalse(can_review_task_submissions(actor))
            for action in ("accept", "reject"):
                self.assertEqual(self.post(action).status_code, 403)
            self.assertEqual(self.snapshot(), before)
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(follow_tasks=False)
        self.client.force_login(self.limited)
        before = self.snapshot()
        self.assertFalse(can_review_task_submissions(self.limited))
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_revoked_warmed_flag_denies_open_page_and_private_helper(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertTrue(self.limited.limited_capabilities.follow_tasks)
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(follow_tasks=False)
        before = self.snapshot()
        self.assertFalse(can_follow_tasks(self.limited))
        self.assertFalse(can_review_task_submissions(self.limited))
        self.assertFalse(scoped_submissions(self.limited).exists())
        self.assertEqual(self.post().status_code, 403)
        request = RequestFactory().post(self.url, {"action": "accept", "submission_id": self.submission.pk})
        request.user = self.limited
        view = WeeklyTaskReviewView()
        view.setup(request)
        with self.assertRaises(PermissionDenied):
            view._post_authorized(request)
        self.assertEqual(self.snapshot(), before)

    def test_disabled_and_changed_persisted_limited_roles_reject_stale_objects(self):
        for change in ({"is_active": False}, {"role": Role.PARTICIPANT}, {"role": Role.GENERAL_SUPERVISOR}):
            with self.subTest(change=change):
                User.objects.filter(pk=self.limited.pk).update(role=Role.LIMITED_SUPERVISOR, is_active=True)
                self.assertTrue(can_review_task_submissions(self.limited))
                User.objects.filter(pk=self.limited.pk).update(**change)
                self.assertTrue(self.limited.is_active)
                self.assertEqual(self.limited.role, Role.LIMITED_SUPERVISOR)
                self.assertFalse(can_review_task_submissions(self.limited))
                before = self.snapshot()
                request = RequestFactory().post(self.url, {"action": "accept", "submission_id": self.submission.pk})
                request.user = self.limited
                with self.assertRaises(PermissionDenied):
                    WeeklyTaskReviewView.as_view()(request)
                self.assertEqual(self.snapshot(), before)

    def test_stale_general_demoted_to_limited_cannot_bypass_action_boundaries(self):
        User.objects.filter(pk=self.general.pk).update(role=Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=self.general, follow_tasks=True)
        before = self.snapshot()
        for action in ("create_task", "return_for_revision", "revise_grade", "accept"):
            request = RequestFactory().post(self.url, {"action": action, "submission_id": self.submission.pk})
            request.user = self.general
            view = WeeklyTaskReviewView()
            view.setup(request)
            with self.assertRaises(PermissionDenied):
                view.post(request)
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_direct_post_is_denied(self):
        request = RequestFactory().post(self.url, {"action": "accept", "submission_id": self.submission.pk})
        request.user = AnonymousUser()
        view = WeeklyTaskReviewView()
        view.setup(request)
        with self.assertRaises(PermissionDenied):
            view.post(request)

    def test_csrf_required_and_valid_token_allows_decision(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.limited)
        before = self.snapshot()
        data = {"action": "accept", "submission_id": self.submission.pk}
        self.assertEqual(csrf_client.post(self.url, data).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(csrf_client.get(self.url).status_code, 200)
        data["csrfmiddlewaretoken"] = csrf_client.cookies["csrftoken"].value
        self.assertEqual(csrf_client.post(self.url, data).status_code, 302)
        self.assert_award(10)

    def test_failure_after_points_write_rolls_back_decision_balances_and_ledger(self):
        before = self.snapshot()
        def write_then_fail(*args, **kwargs):
            apply_points_delta(*args, **kwargs)
            raise RuntimeError("synthetic grading failure")
        with patch("participants.views.apply_points_delta", side_effect=write_then_fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic grading failure"):
                self.post(is_featured="on")
        self.assertEqual(self.snapshot(), before)

    def test_ledger_failure_rolls_back_submission_and_participant(self):
        before = self.snapshot()
        with patch("participants.models.PointsLedgerEntry.objects.create", side_effect=RuntimeError("synthetic ledger failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic ledger failure"):
                self.post()
        self.assertEqual(self.snapshot(), before)

    def test_prior_cross_user_grade_revision_not_rewritten_or_recreated(self):
        audit = TaskGradeRevision.objects.create(
            submission=self.submission, old_status="rejected", new_status="pending",
            old_featured=False, new_featured=False, old_award=0, new_award=0,
            reason="إلغاء سابق محفوظ", revised_by=self.general,
        )
        previous = list(TaskGradeRevision.objects.values())
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(list(TaskGradeRevision.objects.values()), previous)
        self.assertEqual(TaskGradeRevision.objects.get().pk, audit.pk)
        self.assertEqual(PointsLedgerEntry.objects.get().granted_by_id, self.limited.pk)

    def test_actor_then_submission_then_participant_locked_before_first_write(self):
        for action, submission in (("accept", self.submission), ("reject", self.foreign_submission)):
            with self.subTest(action=action):
                sequence = []
                def record(execute, sql, params, many, context):
                    if sql.lstrip().startswith("UPDATE") or sql.lstrip().startswith("INSERT"):
                        sequence.append("write")
                    return execute(sql, params, many, context)
                user_lock = User.objects.select_for_update
                participant_lock = Participant.objects.select_for_update
                def user_locked(*args, **kwargs):
                    self.assertEqual(kwargs, {"no_key": True})
                    self.assertTrue(connection.in_atomic_block)
                    sequence.append("user")
                    return user_lock(*args, **kwargs)
                def participant_locked(*args, **kwargs):
                    sequence.append("participant")
                    return participant_lock(*args, **kwargs)
                from django.db.models import QuerySet
                original_queryset_lock = QuerySet.select_for_update
                # Preserve the scoped queryset instead of replacing it with a manager.
                def queryset_locked(queryset, *args, **kwargs):
                    if queryset.model is TaskSubmission:
                        sequence.append("submission")
                    return original_queryset_lock(queryset, *args, **kwargs)
                with patch.object(User.objects, "select_for_update", side_effect=user_locked), patch.object(
                    Participant.objects, "select_for_update", side_effect=participant_locked,
                ), patch.object(QuerySet, "select_for_update", new=queryset_locked), connection.execute_wrapper(record):
                    self.assertEqual(self.post(action, submission=submission).status_code, 302)
                self.assertEqual(sequence[:4], ["user", "submission", "participant", "write"])
