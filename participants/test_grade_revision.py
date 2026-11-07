import datetime
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, Role
from .forms import TaskSubmissionForm
from .models import Group, Participant, WeeklyTask, TaskSubmission, PointsLedgerEntry, TaskGradeRevision


class GradeRevisionTests(TestCase):
    def setUp(self):
        self.general = User.objects.create_user(username='synthetic_general', role=Role.GENERAL_SUPERVISOR, password='Synthetic_2026!')
        self.environment = User.objects.create_user(username='synthetic_environment', role=Role.GROUP_SUPERVISOR, password='Synthetic_2026!')
        self.system = User.objects.create_user(username='synthetic_system', role=Role.SUPERADMIN, password='Synthetic_2026!')
        self.student = User.objects.create_user(username='synthetic_student', national_id='9000000001', role=Role.PARTICIPANT, password='Synthetic_2026!', must_set_password=False)
        self.group = Group.objects.create(name='بيئة اختبار التقييم')
        self.group.supervisor.add(self.environment)
        self.participant = Participant.objects.create(user=self.student, group=self.group, points=100, miles=1000, purchase_points=100)
        self.task = WeeklyTask.objects.create(title='مهمة مصطنعة', description='وصف مصطنع', due_date=timezone.localdate() - datetime.timedelta(days=3), is_active=False, allowed_formats='text', created_by=self.general)
        self.submission = TaskSubmission.objects.create(task=self.task, participant=self.participant, text_content='نص مصطنع ثابت', status=TaskSubmission.Status.ACCEPTED, points_awarded=True, award_value=10, reviewed_at=timezone.now(), reviewed_by=self.general)
        self.url = reverse('participants:weekly_task_review')
        self.client.force_login(self.general)

    def post_grade(self, evaluation, **extra):
        self.submission.refresh_from_db()
        data = {'action': 'revise_grade', 'submission_id': str(self.submission.pk), 'review_version': self.submission.grade_revision_token, 'grade': evaluation, 'reason': 'سبب تعديل مصطنع'}
        data.update(extra)
        return self.client.post(self.url, data)

    def assert_balances(self, points, miles, purchase):
        self.participant.refresh_from_db()
        self.assertEqual((self.participant.points, self.participant.miles, self.participant.purchase_points), (points, miles, purchase))

    def test_upgrade_to_featured_adds_only_two(self):
        uploaded = self.submission.submitted_at
        response = self.post_grade('featured')
        self.assertEqual(response.status_code, 302)
        self.assert_balances(102, 1020, 102)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.award_value, 12)
        self.assertTrue(self.submission.is_featured)
        self.assertEqual(self.submission.submitted_at, uploaded)
        self.assertEqual(self.submission.text_content, 'نص مصطنع ثابت')
        entry = PointsLedgerEntry.objects.get()
        self.assertEqual(entry.points_delta, 2)
        self.assertEqual(entry.event_date, timezone.localdate(uploaded))
        revision = TaskGradeRevision.objects.get()
        self.assertEqual((revision.old_award, revision.new_award), (10, 12))
        self.assertEqual(revision.revised_by, self.general)

    def test_downgrade_subtracts_only_two(self):
        self.submission.award_value = 12
        self.submission.is_featured = True
        self.submission.save()
        self.post_grade('accepted')
        self.assert_balances(98, 980, 98)
        self.assertEqual(PointsLedgerEntry.objects.get().points_delta, -2)

    def test_reject_voids_award_and_reaccept_restores(self):
        self.post_grade('rejected', rejection_reason='سبب مصطنع للطالب')
        self.assert_balances(90, 900, 90)
        self.submission.refresh_from_db()
        self.assertFalse(self.submission.points_awarded)
        self.assertEqual(self.submission.award_value, 0)
        self.post_grade('accepted')
        self.assert_balances(100, 1000, 100)
        self.assertEqual(TaskGradeRevision.objects.count(), 2)
        self.assertEqual(list(PointsLedgerEntry.objects.order_by('pk').values_list('points_delta', flat=True)), [-10, 10])

    def test_same_grade_does_not_reaward(self):
        self.assertEqual(self.post_grade('accepted').status_code, 302)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_stale_request_cannot_overwrite_new_grade(self):
        old_version = self.submission.grade_revision_token
        self.post_grade('featured')
        response = self.post_grade('rejected', review_version=old_version)
        self.assertEqual(response.status_code, 400)
        self.assert_balances(102, 1020, 102)
        self.assertEqual(TaskGradeRevision.objects.count(), 1)

    def test_environment_system_and_student_cannot_edit(self):
        for actor in (self.environment, self.system, self.student):
            with self.subTest(role=actor.role):
                self.client.force_login(actor)
                self.assertEqual(self.post_grade('featured').status_code, 403)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_pending_or_returned_requires_regular_review(self):
        for status in (TaskSubmission.Status.PENDING, TaskSubmission.Status.REVISION_REQUIRED):
            self.submission.status = status
            self.submission.save()
            self.assertEqual(self.post_grade('featured').status_code, 400)
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_historical_award_is_not_guessed(self):
        self.submission.award_value = None
        self.submission.save()
        self.assertEqual(self.post_grade('featured').status_code, 400)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertEqual(self.post_grade('featured', previous_award='10').status_code, 302)
        self.assert_balances(102, 1020, 102)

    def test_historical_unknown_boolean_requires_confirmation(self):
        self.submission.award_value = None
        self.submission.points_awarded = None
        self.submission.save()
        self.assertEqual(self.post_grade('rejected').status_code, 400)
        self.assertEqual(self.post_grade('rejected', previous_award='12').status_code, 302)
        self.assert_balances(88, 880, 88)

    def test_legacy_unpaid_rejection_can_be_accepted(self):
        self.submission.status = TaskSubmission.Status.REJECTED
        self.submission.award_value = None
        self.submission.points_awarded = False
        self.submission.save()
        self.assertEqual(self.post_grade('accepted').status_code, 302)
        self.assert_balances(110, 1100, 110)

    def test_invalid_grade_reason_version_have_no_side_effect(self):
        for data in ({'grade': 'arbitrary'}, {'reason': ''}, {'review_version': 'bad-time'}, {'previous_award': '999'}):
            self.assertEqual(self.post_grade('featured', **data).status_code, 400)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_atomic_failure_preserves_decision_and_audit(self):
        with patch('participants.views.apply_points_delta', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.post_grade('featured')
        self.submission.refresh_from_db()
        self.assertFalse(self.submission.is_featured)
        self.assertEqual(self.submission.award_value, 10)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assert_balances(100, 1000, 100)

    def test_form_has_exact_version_and_only_general_can_see_it(self):
        response = self.client.get(self.url, {'task': self.task.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.submission.grade_revision_token)
        self.assertContains(response, 'value="revise_grade"')
        for actor in (self.environment, self.system):
            self.client.force_login(actor)
            response = self.client.get(self.url, {'task': self.task.pk})
            self.assertNotContains(response, 'value="revise_grade"')

    def test_original_acceptance_records_exact_award_value(self):
        self.submission.status = TaskSubmission.Status.PENDING
        self.submission.points_awarded = False
        self.submission.award_value = None
        self.submission.save()
        response = self.client.post(self.url, {'action': 'accept', 'submission_id': self.submission.pk, 'is_featured': 'on'})
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.award_value, 12)
        self.assert_balances(112, 1120, 112)

    def test_reopened_upload_preserves_corrected_award_and_review_does_not_repeat_it(self):
        self.submission.reopened_for_resubmission = True
        self.submission.save(update_fields=['reopened_for_resubmission'])
        self.assertEqual(self.post_grade('featured').status_code, 302)
        uploaded_at = self.submission.submitted_at
        student_client = Client()
        student_client.force_login(self.student)
        response = student_client.post(reverse('participants:task_submission'), {
            'task_id': self.task.pk, 'chosen_format': 'text', 'text_content': 'نص معاد مصطنع',
        })
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.PENDING)
        self.assertEqual(self.submission.text_content, 'نص معاد مصطنع')
        self.assertEqual(self.submission.submitted_at, uploaded_at)
        self.assertFalse(self.submission.reopened_for_resubmission)
        self.assertIsNone(self.submission.reviewed_at)
        self.assertIsNone(self.submission.reviewed_by_id)
        self.assertEqual(self.submission.award_value, 12)
        self.assertTrue(self.submission.points_awarded)
        self.assert_balances(102, 1020, 102)
        self.client.post(self.url, {'action': 'accept', 'submission_id': self.submission.pk})
        self.assert_balances(102, 1020, 102)
        self.assertEqual(list(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [2])
        self.assertEqual(TaskGradeRevision.objects.count(), 1)

    def test_upload_save_fields_protect_award_from_stale_form_instance(self):
        self.submission.status = TaskSubmission.Status.REJECTED
        self.submission.award_value = 0
        self.submission.points_awarded = False
        self.submission.reopened_for_resubmission = True
        self.submission.save()
        original_validation = TaskSubmissionForm.is_valid

        def validate_then_correct(form):
            valid = original_validation(form)
            # A controlled same-connection interleaving isolates the defensive
            # update_fields guarantee. Real cross-connection locking is tested
            # separately on PostgreSQL, where a reviewer must wait for upload.
            self.assertEqual(form.instance.award_value, 0)
            self.assertFalse(form.instance.points_awarded)
            self.assertEqual(self.post_grade('accepted').status_code, 302)
            return valid

        student_client = Client()
        student_client.force_login(self.student)
        with patch('participants.views.TaskSubmissionForm.is_valid', autospec=True,
                   side_effect=validate_then_correct):
            response = student_client.post(reverse('participants:task_submission'), {
                'task_id': self.task.pk, 'chosen_format': 'text', 'text_content': 'رفع مصطنع متداخل',
            })
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.PENDING)
        self.assertEqual(self.submission.award_value, 10)
        self.assertTrue(self.submission.points_awarded)
        self.assertEqual(self.submission.text_content, 'رفع مصطنع متداخل')
        self.assertFalse(self.submission.reopened_for_resubmission)
        self.client.post(self.url, {'action': 'accept', 'submission_id': self.submission.pk})
        self.assert_balances(110, 1100, 110)
        self.assertEqual(list(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [10])
        self.assertEqual(TaskGradeRevision.objects.count(), 1)

    def test_csrf_blocks_grade_change(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.general)
        response = client.post(self.url, {'action': 'revise_grade', 'submission_id': self.submission.pk, 'grade': 'featured', 'reason': 'سبب'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TaskGradeRevision.objects.exists())
