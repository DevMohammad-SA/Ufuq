"""Actual local PostgreSQL lock tests, never production student records."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import (
    Group, Participant, PointsLedgerEntry, SupervisorCapability,
    SupervisorTaskGradePermission, TaskGradeRevision, TaskReviewPermission,
    TaskSubmission, WeeklyTask,
)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class TaskFeaturedConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.general = User.objects.create_user(username="feature_lock_general", role=Role.GENERAL_SUPERVISOR)
        self.other_general = User.objects.create_user(username="feature_lock_other", role=Role.GENERAL_SUPERVISOR)
        self.group = Group.objects.create(name="بيئة قفل مصطنعة")
        self.other_group = Group.objects.create(name="بيئة أخرى مصطنعة")
        self.student = User.objects.create_user(username="feature_lock_student", role=Role.PARTICIPANT)
        self.participant = Participant.objects.create(
            user=self.student, group=self.group, points=100, miles=1000, purchase_points=100,
        )
        self.task = WeeklyTask.objects.create(title="تميز متزامن مصطنع", due_date=timezone.localdate())
        self.submission = TaskSubmission.objects.create(
            task=self.task, participant=self.participant, status=TaskSubmission.Status.ACCEPTED,
            points_awarded=True, award_value=10, reviewed_at=timezone.now(), reviewed_by=self.general,
            text_content="عمل مصطنع ثابت",
        )
        self.url = reverse("participants:weekly_task_review")
        self.data = {
            "action": "set_featured", "submission_id": self.submission.pk,
            "review_version": self.submission.grade_revision_token, "is_featured": "yes",
        }

    def post_in_thread(self, actor_id, data, *, barrier=None, wrapper=None, authenticated_client=None):
        close_old_connections()
        try:
            client = authenticated_client or Client()
            if authenticated_client is None:
                client.force_login(User.objects.get(pk=actor_id))
            if barrier is not None:
                barrier.wait(timeout=10)
            if wrapper is not None:
                with connection.execute_wrapper(wrapper):
                    return client.post(self.url, data).status_code
            return client.post(self.url, data).status_code
        finally:
            close_old_connections()

    def assert_unchanged(self):
        self.participant.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual((self.participant.points, self.participant.miles, self.participant.purchase_points), (100, 1000, 100))
        self.assertEqual((self.submission.status, self.submission.award_value, self.submission.is_featured), ("accepted", 10, False))
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_two_featured_requests_apply_only_one_two_point_difference(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.post_in_thread, actor.pk, self.data, barrier=barrier)
                       for actor in (self.general, self.other_general)]
            statuses = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(statuses), [302, 400])
        self.participant.refresh_from_db()
        self.assertEqual((self.participant.points, self.participant.miles, self.participant.purchase_points), (102, 1020, 102))
        self.assertEqual(list(PointsLedgerEntry.objects.values_list("points_delta", flat=True)), [2])
        self.assertEqual(TaskGradeRevision.objects.count(), 1)

    def test_featured_and_direct_revision_cannot_both_commit_same_version(self):
        barrier = Barrier(2)
        revision = dict(self.data, action="revise_grade", grade="rejected", reason="تصحيح متزامن مصطنع")
        revision.pop("is_featured")
        with ThreadPoolExecutor(max_workers=2) as pool:
            quick = pool.submit(self.post_in_thread, self.general.pk, self.data, barrier=barrier)
            full = pool.submit(self.post_in_thread, self.other_general.pk, revision, barrier=barrier)
            statuses = [quick.result(timeout=15), full.result(timeout=15)]
        self.assertEqual(sorted(statuses), [302, 400])
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(PointsLedgerEntry.objects.count(), 1)
        self.submission.refresh_from_db()
        delta = 2 if self.submission.status == "accepted" else -10
        self.assertEqual(PointsLedgerEntry.objects.get().points_delta, delta)
        self.participant.refresh_from_db()
        self.assertEqual((self.participant.points, self.participant.miles, self.participant.purchase_points),
                         (100 + delta, 1000 + delta * 10, 100 + delta))

    def run_with_actor_locked(self, actor, withdraw):
        actor_locked, actor_waiting, release_actor = Event(), Event(), Event()
        # force_login writes last_login on User. Complete that setup before
        # the revoker owns User, so this test observes the POST's actor lock.
        authenticated_client = Client()
        authenticated_client.force_login(actor)

        def mark_actor_lock(execute, sql, params, many, context):
            if "accounts_user" in sql and "FOR NO KEY UPDATE" in sql:
                actor_waiting.set()
            return execute(sql, params, many, context)

        def revoke_in_thread():
            close_old_connections()
            try:
                with transaction.atomic():
                    User.objects.select_for_update(no_key=True).get(pk=actor.pk)
                    withdraw()
                    actor_locked.set()
                    if not release_actor.wait(timeout=10):
                        raise AssertionError("Timed out releasing synthetic permission writer")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            revocation = pool.submit(revoke_in_thread)
            try:
                self.assertTrue(actor_locked.wait(timeout=10))
                change = pool.submit(
                    self.post_in_thread, actor.pk, self.data,
                    wrapper=mark_actor_lock, authenticated_client=authenticated_client,
                )
                self.assertTrue(actor_waiting.wait(timeout=10))
                with self.assertRaises(TimeoutError):
                    change.result(timeout=0.25)
            finally:
                release_actor.set()
            revocation.result(timeout=15)
            self.assertEqual(change.result(timeout=15), 403)
        self.assert_unchanged()

    def test_explicit_grade_denial_committed_while_request_waits_is_effective(self):
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.general, enabled=True, granted_by=self.other_general)
        self.run_with_actor_locked(self.general, lambda: SupervisorTaskGradePermission.objects.filter(pk=policy.pk).update(enabled=False))

    def test_group_review_revocation_blocks_an_independently_granted_grade_edit(self):
        actor = User.objects.create_user(username="feature_lock_group", role=Role.GROUP_SUPERVISOR)
        self.group.supervisor.add(actor)
        policy = TaskReviewPermission.objects.create(supervisor=actor, enabled=True, granted_by=self.general)
        SupervisorTaskGradePermission.objects.create(supervisor=actor, enabled=True, granted_by=self.general)
        self.run_with_actor_locked(actor, lambda: TaskReviewPermission.objects.filter(pk=policy.pk).update(enabled=False))

    def test_limited_task_revocation_blocks_an_independently_granted_grade_edit(self):
        actor = User.objects.create_user(username="feature_lock_limited", role=Role.LIMITED_SUPERVISOR)
        capability = SupervisorCapability.objects.create(supervisor=actor, follow_tasks=True, granted_by=self.general)
        SupervisorTaskGradePermission.objects.create(supervisor=actor, enabled=True, granted_by=self.general)
        self.run_with_actor_locked(actor, lambda: SupervisorCapability.objects.filter(pk=capability.pk).update(follow_tasks=False))

    def test_group_edit_rechecks_student_scope_after_waiting_for_participant(self):
        actor = User.objects.create_user(username="feature_lock_scope", role=Role.GROUP_SUPERVISOR)
        self.group.supervisor.add(actor)
        TaskReviewPermission.objects.create(supervisor=actor, enabled=True, granted_by=self.general)
        SupervisorTaskGradePermission.objects.create(supervisor=actor, enabled=True, granted_by=self.general)
        participant_locked, reviewer_waiting, release_move = Event(), Event(), Event()

        def mark_participant_lock(execute, sql, params, many, context):
            if "participants_participant" in sql and "FOR UPDATE" in sql and "participants_tasksubmission" not in sql:
                reviewer_waiting.set()
            return execute(sql, params, many, context)

        def move_student():
            close_old_connections()
            try:
                with transaction.atomic():
                    participant = Participant.objects.select_for_update().get(pk=self.participant.pk)
                    participant.group = self.other_group
                    participant.save(update_fields=["group"])
                    participant_locked.set()
                    if not release_move.wait(timeout=10):
                        raise AssertionError("Timed out releasing synthetic student move")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            mover = pool.submit(move_student)
            try:
                self.assertTrue(participant_locked.wait(timeout=10))
                change = pool.submit(self.post_in_thread, actor.pk, self.data, wrapper=mark_participant_lock)
                self.assertTrue(reviewer_waiting.wait(timeout=10))
                with self.assertRaises(TimeoutError):
                    change.result(timeout=0.25)
            finally:
                release_move.set()
            mover.result(timeout=15)
            self.assertEqual(change.result(timeout=15), 404)
        self.assert_unchanged()
        self.assertEqual(self.participant.group_id, self.other_group.pk)
