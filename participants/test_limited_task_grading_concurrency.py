"""LOCAL PostgreSQL tests for limited grading's User-first authorization.

Use config.test_postgres_settings and a disposable local test database only.
Real capability-management POSTs are used; no production services are touched.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch
import datetime

from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection, transaction
from django.test import Client, RequestFactory, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import (
    Participant, PointsLedgerEntry, SupervisorCapability, TaskGradeRevision,
    TaskSubmission, WeeklyTask,
)
from .views import WeeklyTaskReviewView


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class LimitedTaskGradingConcurrencyTests(TransactionTestCase):
    event_timeout = 8
    result_timeout = 20

    def setUp(self):
        self.general = self.user("pg-limited-grading-general", Role.GENERAL_SUPERVISOR)
        self.reviewer = self.user("pg-limited-grading-reviewer", Role.LIMITED_SUPERVISOR)
        self.other = self.user("pg-limited-grading-other", Role.LIMITED_SUPERVISOR)
        self.capability = SupervisorCapability.objects.create(
            supervisor=self.reviewer, follow_tasks=True, granted_by=self.general,
        )
        SupervisorCapability.objects.create(supervisor=self.other, follow_tasks=True)
        self.participant = Participant.objects.create(
            user=self.user("pg-limited-grading-student", Role.PARTICIPANT),
            points=41, miles=410, purchase_points=23,
        )
        self.task = WeeklyTask.objects.create(
            title="مهمة تصحيح PostgreSQL مصطنعة", description="عمل محفوظ",
            due_date=timezone.localdate() - datetime.timedelta(days=2),
            is_active=False, allowed_formats="pdf,text", created_by=self.general,
        )
        self.submission = TaskSubmission.objects.create(
            participant=self.participant, task=self.task,
            text_content="عمل الطالب الأصلي", file="task_submissions/pg-original.pdf",
        )
        self.original_upload = timezone.now() - datetime.timedelta(days=6)
        TaskSubmission.objects.filter(pk=self.submission.pk).update(submitted_at=self.original_upload)
        self.url = reverse("participants:weekly_task_review")
        self.grant_url = reverse("participants:supervisor_capabilities", kwargs={"pk": self.reviewer.pk})
        self.review_client = Client()
        self.review_client.force_login(self.reviewer)
        self.other_client = Client()
        self.other_client.force_login(self.other)
        self.grant_client = Client()
        self.grant_client.force_login(self.general)

    @staticmethod
    def user(username, role):
        return User.objects.create_user(username=username, full_name=username, role=role)

    def worker(self, operation):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '4s'")
                cursor.execute("SET statement_timeout = '12s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            return pid, operation()
        finally:
            connection.close()

    def wait_for(self, event, description):
        if not event.wait(timeout=self.event_timeout):
            raise AssertionError(f"Timed out waiting for {description}")

    def review(self, client=None, action="accept", featured=False):
        data = {"action": action, "submission_id": self.submission.pk}
        if featured:
            data["is_featured"] = "on"
        return (client or self.review_client).post(self.url, data).status_code

    def assert_original(self):
        self.submission.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertEqual(self.submission.status, "pending")
        self.assertIsNone(self.submission.reviewed_by_id)
        self.assertIsNone(self.submission.reviewed_at)
        self.assertFalse(self.submission.points_awarded)
        self.assertIsNone(self.submission.award_value)
        self.assertEqual(self.submission.submitted_at, self.original_upload)
        self.assertEqual(self.submission.text_content, "عمل الطالب الأصلي")
        self.assertEqual(self.submission.file.name, "task_submissions/pg-original.pdf")
        self.assertEqual((self.participant.points, self.participant.miles, self.participant.purchase_points), (41, 410, 23))
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def assert_one_award(self, expected_actor=None):
        self.submission.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertEqual(self.submission.status, "accepted")
        self.assertTrue(self.submission.points_awarded)
        points = self.submission.award_value
        self.assertIn(points, (10, 12))
        self.assertEqual(self.submission.is_featured, points == 12)
        self.assertEqual((self.participant.points, self.participant.miles, self.participant.purchase_points),
                         (41 + points, 410 + points * 10, 23 + points))
        entry = PointsLedgerEntry.objects.get()
        self.assertEqual(entry.points_delta, points)
        self.assertEqual(entry.source, PointsLedgerEntry.Source.WEEKLY_TASK)
        self.assertEqual(entry.granted_by_id, self.submission.reviewed_by_id)
        self.assertEqual(entry.event_date, timezone.localdate(self.original_upload))
        self.assertEqual(self.submission.submitted_at, self.original_upload)
        if expected_actor is not None:
            self.assertEqual(entry.granted_by_id, expected_actor.pk)
        else:
            self.assertIn(entry.granted_by_id, (self.reviewer.pk, self.other.pk))
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_actual_revocation_holds_user_stale_grading_waits_then_denied_403(self):
        from . import management_views
        self.assertEqual(self.review_client.get(self.url).status_code, 200)
        revocation_held, release_revocation, review_waiting = Event(), Event(), Event()
        original_record = management_views.record_management
        review_lock_sql = []
        review_mutation_sql = []

        def hold_revocation(*args, **kwargs):
            result = original_record(*args, **kwargs)
            revocation_held.set()
            self.wait_for(release_revocation, "release of actual revocation User/capability transaction")
            return result

        def mark_review_lock(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                review_lock_sql.append(sql)
                review_waiting.set()
            if '"participants_tasksubmission"' in sql and ("FOR UPDATE" in sql or sql.lstrip().startswith("UPDATE")):
                review_mutation_sql.append(sql)
            return execute(sql, params, many, context)

        def stale_review():
            self.wait_for(revocation_held, "actual revocation holding the target User")
            with connection.execute_wrapper(mark_review_lock):
                return self.review(featured=True)

        with patch.object(management_views, "record_management", side_effect=hold_revocation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                revoke = pool.submit(self.worker, lambda: self.grant_client.post(self.grant_url, {"confirm": "yes"}).status_code)
                grading = pool.submit(self.worker, stale_review)
                try:
                    self.wait_for(review_waiting, "grading attempting held User lock")
                    with self.assertRaises(TimeoutError):
                        grading.result(timeout=0.2)
                    self.assertEqual(review_mutation_sql, [])
                finally:
                    release_revocation.set()
                revoke_pid, revoke_status = revoke.result(timeout=self.result_timeout)
                grade_pid, grade_status = grading.result(timeout=self.result_timeout)
        self.assertNotEqual(revoke_pid, grade_pid)
        self.assertEqual((revoke_status, grade_status), (302, 403))
        self.assertTrue(review_lock_sql)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in review_lock_sql))
        self.assertEqual(review_mutation_sql, [])
        self.capability.refresh_from_db()
        self.assertFalse(self.capability.follow_tasks)
        self.assert_original()

    def test_actual_grant_waits_for_user_decision_before_locking_capability(self):
        review_held, release_review, grant_waiting = Event(), Event(), Event()
        original_post = WeeklyTaskReviewView._post_authorized
        grant_user_sql, grant_capability_sql = [], []

        def hold_decision(view, request, *args, **kwargs):
            result = original_post(view, request, *args, **kwargs)
            review_held.set()
            self.wait_for(release_review, "release of validated grading actor transaction")
            return result

        def mark_grant_lock(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                grant_user_sql.append(sql)
                grant_waiting.set()
            if '"participants_supervisorcapability"' in sql and "FOR UPDATE" in sql:
                grant_capability_sql.append(sql)
            return execute(sql, params, many, context)

        def grant_store_flag():
            self.wait_for(review_held, "grading retaining its User lock through decision commit")
            with connection.execute_wrapper(mark_grant_lock):
                return self.grant_client.post(self.grant_url, {
                    "confirm": "yes", "follow_tasks": "yes", "manage_store": "yes",
                }).status_code

        with patch.object(WeeklyTaskReviewView, "_post_authorized", new=hold_decision):
            with ThreadPoolExecutor(max_workers=2) as pool:
                grading = pool.submit(self.worker, lambda: self.review(featured=True))
                granting = pool.submit(self.worker, grant_store_flag)
                try:
                    self.wait_for(grant_waiting, "actual capability grant attempting the held actor lock")
                    with self.assertRaises(TimeoutError):
                        granting.result(timeout=0.2)
                    self.assertEqual(grant_capability_sql, [], "Capability must not be locked before target User")
                finally:
                    release_review.set()
                grade_pid, grade_status = grading.result(timeout=self.result_timeout)
                grant_pid, grant_status = granting.result(timeout=self.result_timeout)
        self.assertNotEqual(grade_pid, grant_pid)
        self.assertEqual((grade_status, grant_status), (302, 302))
        self.assertTrue(grant_user_sql)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in grant_user_sql))
        self.assertTrue(grant_capability_sql)
        self.capability.refresh_from_db()
        self.assertTrue(self.capability.follow_tasks)
        self.assertTrue(self.capability.manage_store)
        self.assert_one_award(expected_actor=self.reviewer)
        self.assertEqual(self.submission.award_value, 12)

    def test_two_distinct_granted_actors_accept_same_pending_award_exactly_once(self):
        both_authorized = Barrier(2)
        lock_chains = {}

        def review_synchronized(client, featured, name):
            chain = lock_chains.setdefault(name, [])
            def observe(execute, sql, params, many, context):
                if "FOR " in sql and "UPDATE" in sql:
                    if 'FROM "accounts_user"' in sql:
                        self.assertIn("FOR NO KEY UPDATE", sql)
                        chain.append("user")
                    elif 'FROM "participants_tasksubmission"' in sql:
                        self.assertIn('FOR UPDATE OF "participants_tasksubmission"', sql)
                        chain.append("submission")
                        # Both have fresh grants and distinct User locks before
                        # contending for the exact same pending submission.
                        both_authorized.wait(timeout=self.event_timeout)
                    elif 'FROM "participants_participant"' in sql:
                        chain.append("participant")
                if (sql.lstrip().startswith(("UPDATE", "INSERT")) and any(
                    table in sql for table in ('"participants_tasksubmission"',
                                              '"participants_participant"',
                                              '"participants_pointsledgerentry"')
                )):
                    chain.append("write")
                return execute(sql, params, many, context)
            with connection.execute_wrapper(observe):
                return self.review(client=client, featured=featured)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, lambda: review_synchronized(self.review_client, False, "first"))
            second = pool.submit(self.worker, lambda: review_synchronized(self.other_client, True, "second"))
            results = [future.result(timeout=self.result_timeout) for future in (first, second)]
        self.assertEqual([status for _, status in results], [302, 302])
        self.assertEqual(len({pid for pid, _ in results}), 2)
        self.assert_one_award()
        winner = "first" if self.submission.reviewed_by_id == self.reviewer.pk else "second"
        loser = "second" if winner == "first" else "first"
        self.assertEqual(lock_chains[winner][:4], ["user", "submission", "participant", "write"])
        self.assertEqual(lock_chains[loser], ["user", "submission"])

    def test_grading_does_not_lock_joined_student_user_during_profile_management(self):
        user_held, release_user = Event(), Event()
        submission_sql = []

        def hold_management_users():
            with transaction.atomic():
                # Same User-first order as ParticipantEditView: delay before
                # acquiring Participant, without changing profile or balances.
                list(User.objects.filter(
                    pk__in=(self.general.pk, self.participant.user_id),
                ).order_by("pk").select_for_update())
                user_held.set()
                self.wait_for(release_user, "release of profile-management User locks")
                return Participant.objects.select_for_update().get(
                    pk=self.participant.pk,
                ).points

        def review_while_student_locked():
            self.wait_for(user_held, "profile-management User locks")
            def observe(execute, sql, params, many, context):
                if 'FROM "participants_tasksubmission"' in sql and "FOR UPDATE" in sql:
                    submission_sql.append(sql)
                    self.assertIn('JOIN "accounts_user"', sql)
                    self.assertIn('FOR UPDATE OF "participants_tasksubmission"', sql)
                return execute(sql, params, many, context)
            with connection.execute_wrapper(observe):
                return self.review(featured=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            management = pool.submit(self.worker, hold_management_users)
            grading = pool.submit(self.worker, review_while_student_locked)
            try:
                # Grading must finish while the student's User remains locked;
                # a bare FOR UPDATE over the joins would block here.
                grade_pid, grade_status = grading.result(timeout=self.event_timeout)
                self.assertEqual(grade_status, 302)
                self.assertFalse(management.done())
            finally:
                release_user.set()
            management_pid, points_seen = management.result(timeout=self.result_timeout)
        self.assertNotEqual(grade_pid, management_pid)
        self.assertEqual(points_seen, 53)
        self.assertTrue(submission_sql)
        self.assert_one_award(expected_actor=self.reviewer)
        self.assertEqual(self.submission.award_value, 12)

    def test_stale_rejection_waiting_for_accept_cannot_overwrite_decision(self):
        accept_held, release_accept, reject_waiting = Event(), Event(), Event()
        original_post = WeeklyTaskReviewView._post_authorized

        def hold_accept(view, request, *args, **kwargs):
            response = original_post(view, request, *args, **kwargs)
            if request.user.pk == self.reviewer.pk:
                accept_held.set()
                self.wait_for(release_accept, "release of accepted pending submission")
            return response

        def observe_rejection(execute, sql, params, many, context):
            if '"participants_tasksubmission"' in sql and "FOR UPDATE" in sql:
                reject_waiting.set()
            return execute(sql, params, many, context)

        def reject_stale():
            self.wait_for(accept_held, "accept holding the submission before commit")
            with connection.execute_wrapper(observe_rejection):
                return self.review(client=self.other_client, action="reject")

        with patch.object(WeeklyTaskReviewView, "_post_authorized", new=hold_accept):
            with ThreadPoolExecutor(max_workers=2) as pool:
                accepting = pool.submit(self.worker, lambda: self.review())
                rejecting = pool.submit(self.worker, reject_stale)
                try:
                    self.wait_for(reject_waiting, "stale reject attempting held submission lock")
                    with self.assertRaises(TimeoutError):
                        rejecting.result(timeout=0.2)
                finally:
                    release_accept.set()
                results = [future.result(timeout=self.result_timeout) for future in (accepting, rejecting)]
        self.assertEqual([status for _, status in results], [302, 302])
        self.assert_one_award(expected_actor=self.reviewer)
        self.assertEqual(self.submission.award_value, 10)
        self.assertEqual(self.submission.rejection_reason, "")

    def test_private_helper_cannot_be_used_without_current_transaction(self):
        request = RequestFactory().post(self.url, {"action": "accept", "submission_id": self.submission.pk})
        request.user = self.reviewer
        view = WeeklyTaskReviewView()
        view.setup(request)
        self.assertFalse(connection.in_atomic_block)
        with self.assertRaises(PermissionDenied):
            view._post_authorized(request)
        self.assert_original()
