import datetime
import importlib
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from accounts.models import Role, User
from .models import Group, Participant, PointsLedgerEntry, TaskSubmission, TaskReviewPermission, WeeklyTask


class TaskAwardOnceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username='award-general', role=Role.GENERAL_SUPERVISOR)
        cls.group_supervisor = User.objects.create_user(username='award-group', role=Role.GROUP_SUPERVISOR)
        cls.group = Group.objects.create(name='بيئة منح النقاط')
        cls.group.supervisor.add(cls.group_supervisor)
        user = User.objects.create_user(national_id='8800000091', full_name='طالب منح النقاط', role=Role.PARTICIPANT)
        cls.student = Participant.objects.create(user=user, group=cls.group, academic_stage='grade_7')
        cls.task = WeeklyTask.objects.create(title='مهمة المنح مرة واحدة', description='اختبار', allowed_formats='text', is_active=True, due_date=datetime.date.today()+datetime.timedelta(days=7))
        cls.submission = TaskSubmission.objects.create(participant=cls.student, task=cls.task, text_content='المحاولة الأولى')
        TaskReviewPermission.objects.create(supervisor=cls.group_supervisor, enabled=True, granted_by=cls.general)

    def test_accepted_resubmission_never_receives_a_second_award(self):
        review = reverse('participants:weekly_task_review')
        self.client.force_login(self.general)
        self.assertEqual(self.client.post(review, {'action':'accept', 'submission_id':self.submission.pk}).status_code, 302)
        self.student.refresh_from_db()
        before = (self.student.points, self.student.miles, self.student.purchase_points)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.points_awarded)
        # Reopening stays available; the permanent award marker must survive
        # the original participant upload form resetting status to pending.
        self.assertEqual(self.client.post(reverse('participants:tasks_archive'), {
            'action':'reopen', 'submission_id':self.submission.pk,
        }).status_code, 302)
        self.client.force_login(self.student.user)
        self.assertEqual(self.client.post(reverse('participants:task_submission'), {
            'task_id':self.task.pk, 'text_content':'المحاولة الثانية', 'chosen_format':'text',
        }).status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.PENDING)
        self.assertTrue(self.submission.points_awarded)
        self.client.force_login(self.group_supervisor)
        self.assertEqual(self.client.post(review, {'action':'accept', 'submission_id':self.submission.pk, 'is_featured':'on'}).status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), before)
        self.assertEqual(PointsLedgerEntry.objects.filter(participant=self.student, source=PointsLedgerEntry.Source.WEEKLY_TASK).count(), 1)

    def test_historical_backfill_marks_known_awards_without_changing_balances(self):
        accepted_task = WeeklyTask.objects.create(title='مهمة قبول تاريخي', description='اختبار', allowed_formats='text', due_date=self.task.due_date)
        accepted = TaskSubmission.objects.create(participant=self.student, task=accepted_task, status='accepted')
        PointsLedgerEntry.objects.create(participant=self.student, source='weekly_task', points_delta=10, description=f'قبول مهمة "{self.task.title}"')
        self.student.refresh_from_db()
        before = (self.student.points, self.student.miles, self.student.purchase_points)
        migration = importlib.import_module('participants.migrations.0019_task_points_once')
        migration.backfill_awards(apps, SimpleNamespace(connection=connection))
        accepted.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertTrue(accepted.points_awarded)
        self.assertIsNone(self.submission.points_awarded)
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), before)

    def test_new_featured_acceptance_still_awards_twelve_once(self):
        self.client.force_login(self.group_supervisor)
        response = self.client.post(reverse('participants:weekly_task_review'), {
            'action':'accept', 'submission_id':self.submission.pk, 'is_featured':'on',
        })
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual(self.student.points, 12)
        self.assertTrue(self.submission.points_awarded)
