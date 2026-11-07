import datetime
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class GradeRevisionMigrationTests(TransactionTestCase):
    def test_upgrade_does_not_guess_historical_award_or_modify_balances(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = executor.loader.graph.leaf_nodes('accounts') + [('participants', '0023_elite_trip_general_supervisor')]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            User = apps.get_model('accounts', 'User')
            Participant = apps.get_model('participants', 'Participant')
            Task = apps.get_model('participants', 'WeeklyTask')
            Submission = apps.get_model('participants', 'TaskSubmission')
            user = User.objects.create(username='historical_synthetic_student', full_name='طالب تاريخي مصطنع', role='participant', is_active=True, password='!')
            participant = Participant.objects.create(user_id=user.pk, points=97, miles=970, purchase_points=41)
            task = Task.objects.create(title='مهمة تاريخية مصطنعة', due_date=datetime.date(2026, 1, 1))
            reviewed_at = timezone.now()
            submission = Submission.objects.create(task_id=task.pk, participant_id=participant.pk, status='accepted', points_awarded=True, is_featured=True, text_content='نص سابق', reviewed_at=reviewed_at)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            upgraded = apps.get_model('participants', 'TaskSubmission').objects.get(pk=submission.pk)
            preserved = apps.get_model('participants', 'Participant').objects.get(pk=participant.pk)
            self.assertIsNone(upgraded.award_value)
            self.assertEqual(upgraded.status, 'accepted')
            self.assertTrue(upgraded.is_featured)
            self.assertTrue(upgraded.points_awarded)
            self.assertEqual(upgraded.reviewed_at, reviewed_at)
            self.assertEqual(upgraded.text_content, 'نص سابق')
            self.assertEqual((preserved.points, preserved.miles, preserved.purchase_points), (97, 970, 41))
            self.assertEqual(apps.get_model('participants', 'TaskGradeRevision').objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate(latest)
