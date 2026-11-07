import datetime
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Role
from .models import (
    Group, Participant, WeeklyTask, TaskSubmission, TaskGradeRevision,
    PointsLedgerEntry, PointsResetSnapshot, StoreProduct, StoreOrder,
    TaskReviewPermission,
)
from .task_grading import GradeCancellationForm, cancel_submission_grade
from .views import apply_points_delta


class GradeCancellationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(
            username='cancel_general', role=Role.GENERAL_SUPERVISOR, password='synthetic-only',
        )
        cls.environment = User.objects.create_user(
            username='cancel_environment', role=Role.GROUP_SUPERVISOR, password='synthetic-only',
        )
        cls.system = User.objects.create_user(
            username='cancel_system', role=Role.SUPERADMIN, password='synthetic-only',
        )
        cls.student = User.objects.create_user(
            username='cancel_student', role=Role.PARTICIPANT,
            password='synthetic-only', must_set_password=False,
        )
        cls.group = Group.objects.create(name='بيئة إلغاء مصطنعة')
        cls.group.supervisor.add(cls.environment)
        TaskReviewPermission.objects.create(
            supervisor=cls.environment, enabled=True, granted_by=cls.general,
        )
        cls.participant = Participant.objects.create(
            user=cls.student, group=cls.group, points=100, miles=1000, purchase_points=100,
        )
        cls.task = WeeklyTask.objects.create(
            title='مهمة إلغاء مصطنعة', description='وصف لا يتغير',
            due_date=timezone.localdate() - datetime.timedelta(days=3),
            is_active=False, allowed_formats='pdf,text', require_all_formats=True,
            created_by=cls.general,
        )
        cls.submission = TaskSubmission.objects.create(
            task=cls.task, participant=cls.participant,
            file='task_submissions/synthetic.pdf', text_content='عمل الطالب الثابت',
            status=TaskSubmission.Status.ACCEPTED, points_awarded=True, award_value=10,
            reviewed_by=cls.general, reviewed_at=timezone.now(),
        )

    def setUp(self):
        self.url = reverse('participants:weekly_task_review')
        self.client.force_login(self.general)
        self.factory = RequestFactory()

    def cancellation_data(self, **extra):
        self.submission.refresh_from_db()
        data = {
            'action': 'cancel_grade', 'submission_id': str(self.submission.pk),
            'review_version': self.submission.grade_revision_token, 'confirm': 'yes',
        }
        data.update(extra)
        return data

    def cancel(self, actor=None, **extra):
        request = self.factory.post(self.url, self.cancellation_data(**extra))
        request.user = actor if actor is not None else self.general
        return cancel_submission_grade(request)

    def set_review(self, **fields):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(**fields)
        self.submission.refresh_from_db()

    def assert_balances(self, points, miles, purchase):
        self.participant.refresh_from_db()
        self.assertEqual(
            (self.participant.points, self.participant.miles, self.participant.purchase_points),
            (points, miles, purchase),
        )

    def assert_pending(self):
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.PENDING)
        self.assertFalse(self.submission.is_featured)
        self.assertFalse(self.submission.points_awarded)
        self.assertEqual(self.submission.award_value, 0)
        self.assertIsNone(self.submission.reviewed_by_id)
        self.assertIsNone(self.submission.reviewed_at)
        self.assertEqual(self.submission.rejection_reason, '')
        self.assertEqual(self.submission.revision_note, '')
        self.assertFalse(self.submission.reopened_for_resubmission)

    def test_all_four_reviewed_states_cancel_after_deadline(self):
        cases = [
            (TaskSubmission.Status.ACCEPTED, False, 10),
            (TaskSubmission.Status.ACCEPTED, True, 12),
            (TaskSubmission.Status.REJECTED, False, 0),
            (TaskSubmission.Status.REVISION_REQUIRED, False, 0),
        ]
        for index, (status, featured, award) in enumerate(cases, start=1):
            with self.subTest(status=status, featured=featured):
                self.set_review(
                    status=status, is_featured=featured, award_value=award,
                    points_awarded=award > 0, reviewed_at=timezone.now(),
                    reviewed_by=self.general, rejection_reason='سبب سابق',
                    revision_note='ملاحظة سابقة', reopened_for_resubmission=True,
                )
                Participant.objects.filter(pk=self.participant.pk).update(
                    points=100, miles=1000, purchase_points=100,
                )
                saved, message = self.cancel()
                self.assertTrue(saved)
                self.assertIn('قيد المراجعة', message)
                self.assert_pending()
                self.assert_balances(100 - award, 1000 - award * 10, 100 - award)
                audit = TaskGradeRevision.objects.latest('pk')
                self.assertEqual((audit.old_status, audit.new_status), (status, 'pending'))
                self.assertEqual((audit.old_featured, audit.new_featured), (featured, False))
                self.assertEqual((audit.old_award, audit.new_award), (award, 0))
                self.assertEqual(audit.revised_by_id, self.general.pk)
                self.assertEqual(TaskGradeRevision.objects.count(), index)
        self.assertEqual(
            list(PointsLedgerEntry.objects.order_by('pk').values_list('points_delta', flat=True)),
            [-10, -12],
        )

    def test_endpoint_redirects_to_same_inactive_task_and_records_event_date(self):
        self.set_review(submitted_at=timezone.now() - datetime.timedelta(days=11))
        uploaded_at = self.submission.submitted_at
        response = self.client.post(self.url, self.cancellation_data())
        self.assertRedirects(response, f'{self.url}?task={self.task.pk}')
        self.assert_pending()
        entry = PointsLedgerEntry.objects.get()
        self.assertEqual(entry.points_delta, -10)
        self.assertEqual(entry.source, PointsLedgerEntry.Source.WEEKLY_TASK)
        self.assertEqual(entry.granted_by_id, self.general.pk)
        self.assertEqual(entry.event_date, timezone.localdate(uploaded_at))
        self.assertNotEqual(entry.event_date, timezone.localdate())
        self.assertGreaterEqual(entry.created_at, uploaded_at)
        # Pending work remains reviewable even though the student's task is closed.
        response = self.client.get(self.url)
        self.assertContains(response, self.task.title)

    def test_new_regular_acceptance_awards_ten_or_twelve_once(self):
        for featured, award in ((False, 10), (True, 12)):
            with self.subTest(featured=featured):
                self.set_review(
                    status='accepted', award_value=10, points_awarded=True,
                    reviewed_at=timezone.now(), is_featured=False,
                )
                before_entries = PointsLedgerEntry.objects.count()
                self.assertTrue(self.cancel()[0])
                data = {'action': 'accept', 'submission_id': self.submission.pk}
                if featured:
                    data['is_featured'] = 'on'
                self.assertEqual(self.client.post(self.url, data).status_code, 302)
                self.submission.refresh_from_db()
                self.assertEqual(self.submission.status, TaskSubmission.Status.ACCEPTED)
                self.assertEqual(self.submission.is_featured, featured)
                self.assertEqual(self.submission.award_value, award)
                self.assertTrue(self.submission.points_awarded)
                self.assertEqual(self.client.post(self.url, data).status_code, 302)
                entries = list(PointsLedgerEntry.objects.order_by('pk').values_list('points_delta', flat=True))
                self.assertEqual(entries[before_entries:], [-10, award])
        self.assert_balances(102, 1020, 102)

    def test_regular_rejection_and_return_after_cancel_keep_award_zero(self):
        for action, expected in (('reject', 'rejected'), ('return_for_revision', 'returned')):
            with self.subTest(action=action):
                self.set_review(status='accepted', award_value=10, points_awarded=True, reviewed_at=timezone.now())
                WeeklyTask.objects.filter(pk=self.task.pk).update(
                    is_active=True, due_date=timezone.localdate() + datetime.timedelta(days=2),
                )
                self.assertTrue(self.cancel()[0])
                response = self.client.post(self.url, {
                    'action': action, 'submission_id': self.submission.pk,
                    'rejection_reason': 'رفض مصطنع', 'revision_note': 'أضف شرحًا للعمل',
                })
                self.assertEqual(response.status_code, 302)
                self.submission.refresh_from_db()
                self.assertEqual(self.submission.status, expected)
                self.assertEqual(self.submission.award_value, 0)
                self.assertFalse(self.submission.points_awarded)
                self.assertFalse(self.submission.reopened_for_resubmission)
        self.assert_balances(80, 800, 80)
        self.assertEqual(list(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [-10, -10])

    def test_regular_return_still_denied_after_deadline_or_when_inactive(self):
        self.assertTrue(self.cancel()[0])
        for active, due_date in (
            (True, timezone.localdate() - datetime.timedelta(days=1)),
            (False, timezone.localdate() + datetime.timedelta(days=1)),
        ):
            with self.subTest(active=active):
                WeeklyTask.objects.filter(pk=self.task.pk).update(is_active=active, due_date=due_date)
                self.assertEqual(self.client.post(self.url, {
                    'action': 'return_for_revision', 'submission_id': self.submission.pk,
                    'revision_note': 'ملاحظة لا تمنح تمديدًا',
                }).status_code, 302)
                self.assert_pending()
        self.assert_balances(90, 900, 90)
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(PointsLedgerEntry.objects.count(), 1)

    def test_entire_previous_context_and_existing_history_are_preserved(self):
        rejection_reason = 'سبب رفض تاريخي ' + 'ر' * 2400
        revision_note = 'ملاحظة إعادة تاريخية ' + 'ع' * 2400
        self.set_review(rejection_reason=rejection_reason, revision_note=revision_note)
        old_reviewed_at = self.submission.reviewed_at
        prior_audit = TaskGradeRevision.objects.create(
            submission=self.submission, old_status='rejected', new_status='accepted',
            old_featured=False, new_featured=False, old_award=0, new_award=10,
            reason='أثر تدقيق سابق لا يحذف', revised_by=self.system,
        )
        prior_entry = PointsLedgerEntry.objects.create(
            participant=self.participant, points_delta=10,
            source=PointsLedgerEntry.Source.WEEKLY_TASK,
            description='منحة تاريخية محفوظة', granted_by=self.system,
        )
        snapshot = PointsResetSnapshot.objects.create(
            participant=self.participant, points_before_reset=45, reset_by=self.general,
        )
        product = StoreProduct.objects.create(name='منتج مصطنع', price=5, stock=2)
        order = StoreOrder.objects.create(participant=self.participant, product=product, price_at_order=5)
        preserved = [
            (model, pk, model.objects.filter(pk=pk).values().get())
            for model, pk in (
                (TaskGradeRevision, prior_audit.pk), (PointsLedgerEntry, prior_entry.pk),
                (PointsResetSnapshot, snapshot.pk), (StoreOrder, order.pk),
                (StoreProduct, product.pk), (WeeklyTask, self.task.pk),
            )
        ]
        old_submission = TaskSubmission.objects.filter(pk=self.submission.pk).values().get()
        self.assertTrue(self.cancel()[0])
        audit = TaskGradeRevision.objects.latest('pk')
        self.assertTrue(audit.reason.startswith('إلغاء التقييم لإعادة المراجعة'))
        self.assertIn(rejection_reason, audit.reason)
        self.assertIn(revision_note, audit.reason)
        self.assertIn(str(self.general.pk), audit.reason)
        self.assertIn(old_reviewed_at.isoformat(), audit.reason)
        self.assertGreater(len(audit.reason), 4800)
        for model, pk, old_values in preserved:
            self.assertEqual(model.objects.filter(pk=pk).values().get(), old_values)
        self.submission.refresh_from_db()
        for field in ('id', 'task_id', 'participant_id', 'file', 'text_content', 'submitted_at'):
            self.assertEqual(TaskSubmission.objects.filter(pk=self.submission.pk).values(field).get()[field], old_submission[field])
        ledger_description = PointsLedgerEntry.objects.latest('pk').description
        self.assertNotIn(rejection_reason, ledger_description)
        self.assertNotIn(revision_note, ledger_description)

    def test_existing_zero_floor_is_applied_without_historical_restore(self):
        self.set_review(award_value=12, is_featured=True)
        Participant.objects.filter(pk=self.participant.pk).update(points=3, miles=20, purchase_points=1)
        self.assertTrue(self.cancel()[0])
        self.assert_balances(0, 0, 0)
        self.assertEqual(PointsLedgerEntry.objects.get().points_delta, -12)
        self.assertEqual(TaskGradeRevision.objects.get().old_award, 12)
        self.assertEqual(self.client.post(self.url, {'action': 'accept', 'submission_id': self.submission.pk}).status_code, 302)
        self.assert_balances(10, 100, 10)
        self.assertEqual(list(PointsLedgerEntry.objects.order_by('pk').values_list('points_delta', flat=True)), [-12, 10])

    def test_historical_true_or_unknown_requires_explicit_award_not_status_guess(self):
        for flag in (True, None):
            for status in ('accepted', 'rejected', 'returned'):
                with self.subTest(flag=flag, status=status):
                    self.set_review(award_value=None, points_awarded=flag, status=status, is_featured=True)
                    self.assertFalse(self.cancel()[0])
                    self.assertEqual(self.client.post(self.url, self.cancellation_data()).status_code, 400)
                    self.assertEqual(TaskSubmission.objects.get(pk=self.submission.pk).status, status)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_explicit_historical_zero_ten_and_twelve_are_respected(self):
        for flag in (True, None):
            for award in (0, 10, 12):
                with self.subTest(flag=flag, award=award):
                    self.set_review(
                        award_value=None, points_awarded=flag, status='rejected',
                        is_featured=False, reviewed_at=timezone.now(),
                    )
                    Participant.objects.filter(pk=self.participant.pk).update(points=100, miles=1000, purchase_points=100)
                    self.assertTrue(self.cancel(previous_award=str(award))[0])
                    self.assert_pending()
                    self.assert_balances(100 - award, 1000 - award * 10, 100 - award)
                    self.assertEqual(TaskGradeRevision.objects.latest('pk').old_award, award)
        self.assertEqual(sorted(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [-12, -12, -10, -10])

    def test_legacy_false_flag_means_zero_and_empty_version_matches_none(self):
        self.set_review(status='rejected', award_value=None, points_awarded=False, reviewed_at=None, reviewed_by=None)
        self.assertTrue(self.cancel(review_version='', previous_award='12')[0])
        self.assert_pending()
        self.assert_balances(100, 1000, 100)
        self.assertEqual(TaskGradeRevision.objects.get().old_award, 0)
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_stored_award_is_authoritative_even_when_flags_disagree(self):
        self.set_review(status='rejected', award_value=12, points_awarded=False, is_featured=False)
        self.assertTrue(self.cancel(previous_award='0')[0])
        self.assert_balances(88, 880, 88)
        self.assertEqual(TaskGradeRevision.objects.get().old_award, 12)

    def test_missing_invalid_and_inexact_versions_are_noops(self):
        original = TaskSubmission.objects.filter(pk=self.submission.pk).values().get()
        versions = [
            'bad-time', '2026-99-99T99:99:99+03:00',
            self.submission.reviewed_at.replace(tzinfo=None).isoformat(), '',
            (self.submission.reviewed_at + datetime.timedelta(microseconds=1)).isoformat(),
        ]
        for version in versions:
            with self.subTest(version=version):
                self.assertEqual(self.client.post(self.url, self.cancellation_data(review_version=version)).status_code, 400)
        data = self.cancellation_data()
        del data['review_version']
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertEqual(TaskSubmission.objects.filter(pk=self.submission.pk).values().get(), original)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_old_form_repeat_and_stale_form_after_rereview_cannot_cancel_again(self):
        old_form = self.cancellation_data()
        self.assertEqual(self.client.post(self.url, old_form).status_code, 302)
        self.assertEqual(self.client.post(self.url, old_form).status_code, 400)
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(self.client.post(self.url, {'action': 'accept', 'submission_id': self.submission.pk, 'is_featured': 'on'}).status_code, 302)
        self.assertEqual(self.client.post(self.url, old_form).status_code, 400)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, 'accepted')
        self.assertTrue(self.submission.is_featured)
        self.assertEqual(self.submission.award_value, 12)
        self.assert_balances(102, 1020, 102)
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(list(PointsLedgerEntry.objects.order_by('pk').values_list('points_delta', flat=True)), [-10, 12])

    def test_confirmation_and_previous_award_choices_have_arabic_errors(self):
        for confirmation in ('', 'no', 'on', 'true'):
            with self.subTest(confirmation=confirmation):
                form = GradeCancellationForm({'confirm': confirmation})
                self.assertFalse(form.is_valid())
                self.assertIn('تأكيد إلغاء التقييم', form.errors['confirm'][0])
                self.assertEqual(self.client.post(self.url, self.cancellation_data(confirm=confirmation)).status_code, 400)
        data = self.cancellation_data()
        del data['confirm']
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        for invalid_award in ('999', '-10', '10.0'):
            self.assertFalse(self.cancel(previous_award=invalid_award)[0])
        self.assertTrue(GradeCancellationForm({'confirm': 'yes'}).is_valid())
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_only_active_general_can_cancel_and_actor_is_rechecked(self):
        disabled = User.objects.create_user(username='cancel_disabled', role=Role.GENERAL_SUPERVISOR, is_active=False)
        for actor in (self.environment, self.system, self.student, disabled, AnonymousUser()):
            with self.subTest(role=getattr(actor, 'role', 'anonymous')):
                with self.assertRaises(PermissionDenied):
                    self.cancel(actor=actor)
        for actor in (self.environment, self.system, self.student):
            self.client.force_login(actor)
            self.assertEqual(self.client.post(self.url, self.cancellation_data()).status_code, 403)
        # A stale request.user must not bypass a role/activity change in the DB.
        for fields in ({'is_active': False}, {'role': Role.SUPERADMIN}):
            User.objects.filter(pk=self.general.pk).update(**fields)
            with self.assertRaises(PermissionDenied):
                self.cancel(actor=self.general)
            User.objects.filter(pk=self.general.pk).update(is_active=True, role=Role.GENERAL_SUPERVISOR)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_csrf_rejects_cancellation_before_any_write(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.general)
        self.assertEqual(client.post(self.url, self.cancellation_data()).status_code, 403)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_bad_and_missing_submission_lookups_are_404_not_overflow(self):
        for raw_id in ('', 'abc', '-1', '1.0', '9' * 5000, '9223372036854775808', '9223372036854775807'):
            with self.subTest(raw_id=raw_id[:30]):
                with self.assertRaises(Http404):
                    self.cancel(submission_id=raw_id)
                self.assertEqual(self.client.post(self.url, self.cancellation_data(submission_id=raw_id)).status_code, 404)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_failure_after_points_write_rolls_back_balances_ledger_and_audit(self):
        original = TaskSubmission.objects.filter(pk=self.submission.pk).values().get()

        def write_then_fail(*args, **kwargs):
            apply_points_delta(*args, **kwargs)
            raise RuntimeError('synthetic rollback')

        with patch('participants.views.apply_points_delta', side_effect=write_then_fail):
            with self.assertRaises(RuntimeError):
                self.cancel()
        self.assertEqual(TaskSubmission.objects.filter(pk=self.submission.pk).values().get(), original)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_failure_saving_submission_rolls_back_prior_delta_and_audit(self):
        original = TaskSubmission.objects.filter(pk=self.submission.pk).values().get()
        with patch('participants.models.TaskSubmission.save', side_effect=RuntimeError('synthetic save failure')):
            with self.assertRaises(RuntimeError):
                self.cancel()
        self.assertEqual(TaskSubmission.objects.filter(pk=self.submission.pk).values().get(), original)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())

    def test_cancel_returned_submission_does_not_allow_reupload_or_extend_task(self):
        student_client = Client()
        student_client.force_login(self.student)
        uploaded_at = self.submission.submitted_at
        for active in (True, False):
            with self.subTest(active=active):
                due_date = timezone.localdate() + datetime.timedelta(days=1)
                WeeklyTask.objects.filter(pk=self.task.pk).update(
                    is_active=active, due_date=due_date,
                    allowed_formats='text', require_all_formats=False,
                )
                self.set_review(
                    status='returned', award_value=0, points_awarded=False,
                    reviewed_at=timezone.now(), revision_note='عدل العمل',
                    reopened_for_resubmission=True,
                )
                self.assertTrue(self.cancel()[0])
                self.assertEqual(student_client.post(reverse('participants:task_submission'), {
                    'task_id': self.task.pk, 'chosen_format': 'text', 'text_content': 'محاولة رفع غير مصرح بها',
                }).status_code, 302)
                self.assert_pending()
                self.assertEqual(self.submission.text_content, 'عمل الطالب الثابت')
                self.assertEqual(self.submission.file.name, 'task_submissions/synthetic.pdf')
                self.assertEqual(self.submission.submitted_at, uploaded_at)
                self.assertEqual(WeeklyTask.objects.get(pk=self.task.pk).due_date, due_date)
        self.assert_balances(100, 1000, 100)
        self.assertFalse(PointsLedgerEntry.objects.exists())
