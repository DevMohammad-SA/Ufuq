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
from .models import Group, Participant, WeeklyTask, TaskSubmission, TaskGradeRevision, PointsLedgerEntry
from .views import apply_points_delta


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
class GradeRevisionConcurrencyTests(TransactionTestCase):
    def test_two_old_forms_apply_one_difference_only(self):
        actors = [User.objects.create_user(username=f'concurrent_general_{i}', role=Role.GENERAL_SUPERVISOR, password='synthetic-only') for i in range(2)]
        user = User.objects.create_user(username='concurrent_student', role=Role.PARTICIPANT, password='synthetic-only')
        group = Group.objects.create(name='بيئة تزامن مصطنعة')
        participant = Participant.objects.create(user=user, group=group, points=100, miles=1000, purchase_points=100)
        task = WeeklyTask.objects.create(title='مهمة تزامن مصطنعة', due_date=timezone.localdate() - datetime.timedelta(days=1))
        submission = TaskSubmission.objects.create(task=task, participant=participant, text_content='نص مصطنع', status='accepted', points_awarded=True, award_value=10, reviewed_at=timezone.now())
        version = submission.grade_revision_token
        barrier = Barrier(2)
        url = reverse('participants:weekly_task_review')
        def submit(actor_id):
            close_old_connections()
            try:
                client = Client()
                client.force_login(User.objects.get(pk=actor_id))
                barrier.wait(timeout=10)
                return client.post(url, {'action':'revise_grade', 'submission_id':submission.pk, 'review_version':version, 'grade':'featured', 'reason':'تحقق تزامن مصطنع'}).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, [actor.pk for actor in actors]))
        self.assertEqual(sorted(statuses), [302, 400])
        participant.refresh_from_db()
        self.assertEqual((participant.points, participant.miles, participant.purchase_points), (102, 1020, 102))
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(list(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [2])

    def test_grade_waits_for_reopened_upload_and_cannot_award_stale_decision(self):
        general = User.objects.create_user(username='upload_race_general', role=Role.GENERAL_SUPERVISOR, password='synthetic-only')
        user = User.objects.create_user(username='upload_race_student', role=Role.PARTICIPANT, password='synthetic-only', must_set_password=False)
        participant = Participant.objects.create(user=user, points=100, miles=1000, purchase_points=100)
        task = WeeklyTask.objects.create(title='اختبار قفل الرفع', due_date=timezone.localdate() - datetime.timedelta(days=1), allowed_formats='text')
        submission = TaskSubmission.objects.create(task=task, participant=participant, text_content='النص السابق', status='rejected', points_awarded=False, award_value=0, reviewed_at=timezone.now(), reopened_for_resubmission=True)
        version = submission.grade_revision_token
        upload_validating, release_upload, grade_reading = Event(), Event(), Event()
        original_validation = TaskSubmissionForm.is_valid
        review_url = reverse('participants:weekly_task_review')

        def held_validation(form):
            valid = original_validation(form)
            upload_validating.set()
            if not release_upload.wait(timeout=10):
                raise AssertionError('Timed out waiting to release synthetic upload')
            return valid

        def mark_grade_read(execute, sql, params, many, context):
            if 'participants_tasksubmission' in sql and 'FOR UPDATE' in sql:
                grade_reading.set()
            return execute(sql, params, many, context)

        def request_in_thread(actor_id, upload):
            close_old_connections()
            try:
                client = Client()
                client.force_login(User.objects.get(pk=actor_id))
                if upload:
                    return client.post(reverse('participants:task_submission'), {
                        'task_id': task.pk, 'chosen_format': 'text', 'text_content': 'رفع متزامن مصطنع',
                    }).status_code
                with connection.execute_wrapper(mark_grade_read):
                    return client.post(review_url, {
                        'action': 'revise_grade', 'submission_id': submission.pk,
                        'review_version': version, 'grade': 'accepted', 'reason': 'قرار متزامن مصطنع',
                    }).status_code
            finally:
                close_old_connections()

        with patch('participants.views.TaskSubmissionForm.is_valid', autospec=True, side_effect=held_validation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                upload_future = pool.submit(request_in_thread, user.pk, True)
                try:
                    self.assertTrue(upload_validating.wait(timeout=10))
                    grade_future = pool.submit(request_in_thread, general.pk, False)
                    self.assertTrue(grade_reading.wait(timeout=10))
                    with self.assertRaises(TimeoutError):
                        grade_future.result(timeout=0.25)
                finally:
                    release_upload.set()
                self.assertEqual(upload_future.result(timeout=10), 302)
                self.assertEqual(grade_future.result(timeout=10), 400)
        submission.refresh_from_db()
        self.assertEqual(submission.status, TaskSubmission.Status.PENDING)
        self.assertEqual(submission.text_content, 'رفع متزامن مصطنع')
        self.assertFalse(submission.reopened_for_resubmission)
        self.assertFalse(submission.points_awarded)
        self.assertEqual(submission.award_value, 0)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        client = Client()
        client.force_login(general)
        self.assertEqual(client.post(review_url, {'action': 'accept', 'submission_id': submission.pk}).status_code, 302)
        participant.refresh_from_db()
        self.assertEqual((participant.points, participant.miles, participant.purchase_points), (110, 1100, 110))
        self.assertEqual(list(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [10])

    def test_actor_lock_does_not_block_foreign_key_audit_while_waiting_for_participant(self):
        general = User.objects.create_user(username='fk_race_general', role=Role.GENERAL_SUPERVISOR, password='synthetic-only')
        user = User.objects.create_user(username='fk_race_student', role=Role.PARTICIPANT, password='synthetic-only')
        participant = Participant.objects.create(user=user, points=100, miles=1000, purchase_points=100)
        task = WeeklyTask.objects.create(title='اختبار تزامن التدقيق', due_date=timezone.localdate())
        submission = TaskSubmission.objects.create(task=task, participant=participant, status='rejected', points_awarded=False, award_value=0, reviewed_at=timezone.now())
        version = submission.grade_revision_token
        participant_locked, correction_waiting = Event(), Event()

        def mark_participant_read(execute, sql, params, many, context):
            if 'participants_participant' in sql and 'FOR UPDATE' in sql:
                correction_waiting.set()
            return execute(sql, params, many, context)

        def concurrent_points_write():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    locked = Participant.objects.select_for_update().get(pk=participant.pk)
                    participant_locked.set()
                    if not correction_waiting.wait(timeout=10):
                        raise AssertionError('Correction did not reach participant lock')
                    # The deferred FK check on granted_by needs KEY SHARE on
                    # general at commit, while correction owns the actor lock.
                    apply_points_delta(locked, 1, source=PointsLedgerEntry.Source.EXTRA,
                                       description='تدقيق نقاط مصطنع متزامن', granted_by=general)
            finally:
                close_old_connections()

        def correct_grade():
            close_old_connections()
            try:
                if not participant_locked.wait(timeout=10):
                    raise AssertionError('Concurrent points writer did not lock participant')
                client = Client()
                client.force_login(User.objects.get(pk=general.pk))
                with connection.execute_wrapper(mark_participant_read):
                    return client.post(reverse('participants:weekly_task_review'), {
                        'action': 'revise_grade', 'submission_id': submission.pk,
                        'review_version': version, 'grade': 'accepted', 'reason': 'اختبار عدم تعارض الأقفال',
                    }).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(concurrent_points_write)
            correction = pool.submit(correct_grade)
            writer.result(timeout=15)
            self.assertEqual(correction.result(timeout=15), 302)
        participant.refresh_from_db()
        self.assertEqual((participant.points, participant.miles, participant.purchase_points), (111, 1110, 111))
        self.assertEqual(sorted(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [1, 10])
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
