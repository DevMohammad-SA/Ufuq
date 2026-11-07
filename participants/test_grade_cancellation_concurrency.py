import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, close_old_connections, transaction
from django.test import TransactionTestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Role
from .forms import TaskSubmissionForm
from .models import Participant, WeeklyTask, TaskSubmission, TaskGradeRevision, PointsLedgerEntry
from .views import apply_points_delta


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
class GradeCancellationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actors = [
            User.objects.create_user(
                username=f'cancel_race_general_{index}', role=Role.GENERAL_SUPERVISOR,
                password='synthetic-only',
            )
            for index in range(2)
        ]
        self.student = User.objects.create_user(
            username='cancel_race_student', role=Role.PARTICIPANT,
            password='synthetic-only', must_set_password=False,
        )
        self.participant = Participant.objects.create(
            user=self.student, points=100, miles=1000, purchase_points=100,
        )
        self.task = WeeklyTask.objects.create(
            title='مهمة تزامن الإلغاء المصطنعة', allowed_formats='text',
            due_date=timezone.localdate() - datetime.timedelta(days=1), is_active=False,
        )
        self.submission = TaskSubmission.objects.create(
            task=self.task, participant=self.participant, text_content='عمل ثابت مصطنع',
            status='accepted', award_value=10, points_awarded=True,
            reviewed_by=self.actors[0], reviewed_at=timezone.now(),
        )
        self.url = reverse('participants:weekly_task_review')
        self.old_form = {
            'action': 'cancel_grade', 'submission_id': self.submission.pk,
            'review_version': self.submission.grade_revision_token, 'confirm': 'yes',
        }

    def post_in_thread(self, actor_id, data, *, wrapper=None, barrier=None, url=None):
        close_old_connections()
        try:
            client = Client()
            client.force_login(User.objects.get(pk=actor_id))
            if barrier is not None:
                barrier.wait(timeout=10)
            if wrapper is not None:
                with connection.execute_wrapper(wrapper):
                    return client.post(url or self.url, data).status_code
            return client.post(url or self.url, data).status_code
        finally:
            close_old_connections()

    def assert_balances(self, points, miles, purchase):
        self.participant.refresh_from_db()
        self.assertEqual(
            (self.participant.points, self.participant.miles, self.participant.purchase_points),
            (points, miles, purchase),
        )

    def test_two_actors_same_old_form_cancel_and_deduct_only_once(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.post_in_thread, actor.pk, self.old_form, barrier=barrier)
                for actor in self.actors
            ]
            statuses = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(statuses), [302, 400])
        self.assert_balances(90, 900, 90)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, 'pending')
        self.assertEqual(self.submission.award_value, 0)
        self.assertFalse(self.submission.points_awarded)
        self.assertIsNone(self.submission.reviewed_at)
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(list(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [-10])
        audit = TaskGradeRevision.objects.get()
        self.assertEqual((audit.old_status, audit.new_status, audit.old_award, audit.new_award), ('accepted', 'pending', 10, 0))
        self.assertIn(audit.revised_by_id, [actor.pk for actor in self.actors])

    def test_stale_cancel_waits_for_new_review_and_cannot_erase_it(self):
        client = Client()
        client.force_login(self.actors[0])
        self.assertEqual(client.post(self.url, self.old_form).status_code, 302)
        review_writing, release_review, cancellation_reading = Event(), Event(), Event()

        def held_award(*args, **kwargs):
            review_writing.set()
            if not release_review.wait(timeout=10):
                raise AssertionError('Timed out waiting to release synthetic review')
            apply_points_delta(*args, **kwargs)

        def mark_submission_lock(execute, sql, params, many, context):
            if 'participants_tasksubmission' in sql and 'FOR UPDATE' in sql:
                cancellation_reading.set()
            return execute(sql, params, many, context)

        with patch('participants.views.apply_points_delta', side_effect=held_award):
            with ThreadPoolExecutor(max_workers=2) as pool:
                review = pool.submit(self.post_in_thread, self.actors[0].pk, {
                    'action': 'accept', 'submission_id': self.submission.pk, 'is_featured': 'on',
                })
                try:
                    self.assertTrue(review_writing.wait(timeout=10))
                    cancellation = pool.submit(
                        self.post_in_thread, self.actors[1].pk, self.old_form,
                        wrapper=mark_submission_lock,
                    )
                    self.assertTrue(cancellation_reading.wait(timeout=10))
                    with self.assertRaises(TimeoutError):
                        cancellation.result(timeout=0.25)
                finally:
                    release_review.set()
                self.assertEqual(review.result(timeout=10), 302)
                self.assertEqual(cancellation.result(timeout=10), 400)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, 'accepted')
        self.assertEqual(self.submission.award_value, 12)
        self.assertTrue(self.submission.points_awarded)
        self.assertTrue(self.submission.is_featured)
        self.assertIsNotNone(self.submission.reviewed_at)
        self.assert_balances(102, 1020, 102)
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(list(PointsLedgerEntry.objects.order_by('pk').values_list('points_delta', flat=True)), [-10, 12])

    def test_cancel_waits_for_reopened_upload_and_rejects_old_review(self):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(reopened_for_resubmission=True)
        upload_validating, release_upload, cancellation_reading = Event(), Event(), Event()
        original_validation = TaskSubmissionForm.is_valid
        uploaded_at = self.submission.submitted_at

        def held_validation(form):
            valid = original_validation(form)
            upload_validating.set()
            if not release_upload.wait(timeout=10):
                raise AssertionError('Timed out waiting to release synthetic upload')
            return valid

        def mark_submission_lock(execute, sql, params, many, context):
            if 'participants_tasksubmission' in sql and 'FOR UPDATE' in sql:
                cancellation_reading.set()
            return execute(sql, params, many, context)

        with patch('participants.views.TaskSubmissionForm.is_valid', autospec=True, side_effect=held_validation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                upload = pool.submit(self.post_in_thread, self.student.pk, {
                    'task_id': self.task.pk, 'chosen_format': 'text',
                    'text_content': 'رفع مصطنع متزامن',
                }, url=reverse('participants:task_submission'))
                try:
                    self.assertTrue(upload_validating.wait(timeout=10))
                    cancellation = pool.submit(
                        self.post_in_thread, self.actors[1].pk, self.old_form,
                        wrapper=mark_submission_lock,
                    )
                    self.assertTrue(cancellation_reading.wait(timeout=10))
                    with self.assertRaises(TimeoutError):
                        cancellation.result(timeout=0.25)
                finally:
                    release_upload.set()
                self.assertEqual(upload.result(timeout=10), 302)
                self.assertEqual(cancellation.result(timeout=10), 400)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, 'pending')
        self.assertEqual(self.submission.text_content, 'رفع مصطنع متزامن')
        self.assertEqual(self.submission.submitted_at, uploaded_at)
        self.assertFalse(self.submission.reopened_for_resubmission)
        self.assertIsNone(self.submission.reviewed_at)
        self.assertEqual(self.submission.award_value, 10)
        self.assertTrue(self.submission.points_awarded)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        client = Client()
        client.force_login(self.actors[0])
        self.assertEqual(client.post(self.url, {'action': 'accept', 'submission_id': self.submission.pk}).status_code, 302)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_no_key_actor_lock_allows_concurrent_points_audit_to_commit(self):
        participant_locked, cancellation_waiting = Event(), Event()
        general = self.actors[0]

        def mark_participant_lock(execute, sql, params, many, context):
            if 'participants_participant' in sql and 'FOR UPDATE' in sql:
                cancellation_waiting.set()
            return execute(sql, params, many, context)

        def write_points():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    locked = Participant.objects.select_for_update().get(pk=self.participant.pk)
                    participant_locked.set()
                    if not cancellation_waiting.wait(timeout=10):
                        raise AssertionError('Cancellation did not reach participant lock')
                    apply_points_delta(
                        locked, 1, source=PointsLedgerEntry.Source.EXTRA,
                        description='منحة مصطنعة متزامنة', granted_by=general,
                    )
            finally:
                close_old_connections()

        def cancel_after_points_lock():
            if not participant_locked.wait(timeout=10):
                raise AssertionError('Points writer did not lock participant')
            return self.post_in_thread(general.pk, self.old_form, wrapper=mark_participant_lock)

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(write_points)
            cancellation = pool.submit(cancel_after_points_lock)
            writer.result(timeout=15)
            self.assertEqual(cancellation.result(timeout=15), 302)
        self.assert_balances(91, 910, 91)
        self.assertEqual(sorted(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [-10, 1])
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
