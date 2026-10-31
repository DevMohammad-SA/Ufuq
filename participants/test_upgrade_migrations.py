"""Exercise an actual 0017 -> latest upgrade using synthetic historical data."""
import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class StudentFeaturesUpgradeTests(TransactionTestCase):
    def test_real_upgrade_preserves_existing_records_and_balances(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old_target = [node for node in latest if node[0] != "participants"] + [
            ("participants", "0017_weeklytask_require_all_formats_and_more"),
        ]
        try:
            executor.migrate(old_target)
            old = executor.loader.project_state(old_target).apps
            User = old.get_model("accounts", "User")
            Group = old.get_model("participants", "Group")
            Participant = old.get_model("participants", "Participant")
            Task = old.get_model("participants", "WeeklyTask")
            Submission = old.get_model("participants", "TaskSubmission")
            Circle = old.get_model("participants", "CircleAttendance")
            Product = old.get_model("participants", "StoreProduct")
            Order = old.get_model("participants", "StoreOrder")
            Ledger = old.get_model("participants", "PointsLedgerEntry")
            Snapshot = old.get_model("participants", "PointsResetSnapshot")
            supervisor = User.objects.create(username="migration-supervisor", role="group_supervisor")
            group = Group.objects.create(name="بيئة اختبار الترقية")
            group.supervisor.add(supervisor)
            user = User.objects.create(national_id="8600000001", role="participant")
            student = Participant.objects.create(
                user=user, group=group, academic_stage="grade_7", points=37, miles=910, purchase_points=12,
            )
            due = datetime.date.today()
            statuses = ("accepted", "pending", "rejected")
            ids = []
            for status in statuses:
                task = Task.objects.create(title="عنوان مكرر قديم", description="اختبار", due_date=due, allowed_formats="text")
                ids.append(Submission.objects.create(task=task, participant=student, status=status, text_content="نص محفوظ").pk)
            Circle.objects.create(participant=student, date=due, attended=True, achieved=False, recorded_by=supervisor)
            product = Product.objects.create(name="منتج مصطنع", price=3, stock=4)
            order = Order.objects.create(participant=student, product=product, price_at_order=3, status="completed")
            Ledger.objects.create(participant=student, source="weekly_task", points_delta=10, description='قبول مهمة "عنوان مكرر قديم"')
            Snapshot.objects.create(participant=student, points_before_reset=60)

            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            new = executor.loader.project_state(latest).apps
            migrated = new.get_model("participants", "Participant").objects.get(pk=student.pk)
            self.assertEqual((migrated.points, migrated.miles, migrated.purchase_points), (37, 910, 12))
            self.assertEqual(new.get_model("participants", "Group").objects.get(pk=group.pk).supervisor.get().pk, supervisor.pk)
            Submission = new.get_model("participants", "TaskSubmission")
            self.assertIs(Submission.objects.get(pk=ids[0]).points_awarded, True)
            self.assertIsNone(Submission.objects.get(pk=ids[1]).points_awarded)
            self.assertIsNone(Submission.objects.get(pk=ids[2]).points_awarded)
            for pk, status in zip(ids, statuses):
                row = Submission.objects.get(pk=pk)
                self.assertEqual((row.status, row.text_content), (status, "نص محفوظ"))
                self.assertEqual(row.revision_note, "")
            attendance = new.get_model("participants", "CircleAttendance").objects.get(participant_id=student.pk)
            self.assertEqual((attendance.attended, attendance.achieved, attendance.recorded_by_id), (True, False, supervisor.pk))
            self.assertEqual(new.get_model("participants", "StoreOrder").objects.get(pk=order.pk).status, "completed")
            self.assertEqual(new.get_model("participants", "StoreProduct").objects.get(pk=product.pk).stock, 4)
            self.assertEqual(new.get_model("participants", "PointsLedgerEntry").objects.get(participant_id=student.pk).points_delta, 10)
            self.assertEqual(new.get_model("participants", "PointsResetSnapshot").objects.get(participant_id=student.pk).points_before_reset, 60)
            self.assertFalse(new.get_model("participants", "MeetingNote").objects.exists())
            self.assertFalse(new.get_model("participants", "TaskReviewPermission").objects.exists())
            self.assertFalse(new.get_model("participants", "EliteTripAttendance").objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
