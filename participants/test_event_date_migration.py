"""Synthetic 0019 -> latest upgrade: unknown historical event dates stay unknown."""
import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class EventDateUpgradeTests(TransactionTestCase):
    def test_upgrade_preserves_dates_balances_and_legacy_ledger(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old_target = [node for node in latest if node[0] != "participants"] + [
            ("participants", "0019_task_points_once"),
        ]
        try:
            executor.migrate(old_target)
            old = executor.loader.project_state(old_target).apps
            User = old.get_model("accounts", "User")
            Student = old.get_model("participants", "Participant")
            Ledger = old.get_model("participants", "PointsLedgerEntry")
            user = User.objects.create(national_id="8300000001", role="participant", full_name="تاريخ مصطنع")
            student = Student.objects.create(
                user=user, academic_stage="grade_7", points=37, miles=810, purchase_points=19,
            )
            created = datetime.datetime(2026, 9, 12, 17, 41, tzinfo=datetime.timezone.utc)
            addition = Ledger.objects.create(
                participant=student, source="quran_circle", points_delta=5,
                description="حضور وإنجاز يوم 2026-09-10؛ لا يُستنتج منه تاريخ",
            )
            deduction = Ledger.objects.create(
                participant=student, source="extra", points_delta=-2, description="تصحيح قديم محفوظ",
            )
            Ledger.objects.filter(pk__in=[addition.pk, deduction.pk]).update(created_at=created)
            old.get_model("participants", "CircleAttendance").objects.create(
                participant=student, date=datetime.date(2026, 9, 10), attended=True, achieved=True,
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            new = executor.loader.project_state(latest).apps
            migrated = new.get_model("participants", "Participant").objects.get(pk=student.pk)
            self.assertEqual((migrated.points, migrated.miles, migrated.purchase_points), (37, 810, 19))
            rows = list(new.get_model("participants", "PointsLedgerEntry").objects.filter(
                participant_id=student.pk,
            ).order_by("pk"))
            self.assertEqual([row.points_delta for row in rows], [5, -2])
            self.assertTrue(all(row.event_date is None for row in rows))
            self.assertTrue(all(row.created_at == created for row in rows))
            self.assertEqual(rows[0].description, addition.description)
            attendance = new.get_model("participants", "CircleAttendance").objects.get(participant_id=student.pk)
            self.assertEqual((attendance.date, attendance.attended, attendance.achieved),
                             (datetime.date(2026, 9, 10), True, True))
            self.assertTrue(new.get_model("accounts", "User").objects.get(pk=user.pk).is_active)
        finally:
            MigrationExecutor(connection).migrate(latest)
