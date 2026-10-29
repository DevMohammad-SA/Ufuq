"""Conservative legacy-award handling without guessing from task titles."""
import datetime
import importlib
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import Group, Participant, PointsLedgerEntry, TaskReviewPermission, TaskSubmission, WeeklyTask


class HistoricalAwardReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username="history-general", role=Role.GENERAL_SUPERVISOR)
        cls.system = User.objects.create_user(username="history-system", role=Role.SUPERADMIN)
        cls.supervisor = User.objects.create_user(username="history-group", role=Role.GROUP_SUPERVISOR)
        cls.group = Group.objects.create(name="بيئة تاريخية مصطنعة")
        cls.group.supervisor.add(cls.supervisor)
        user = User.objects.create_user(national_id="8700000001", role=Role.PARTICIPANT)
        cls.student = Participant.objects.create(
            user=user, group=cls.group, academic_stage="grade_7",
            points=100, miles=1000, purchase_points=75,
        )
        cls.task = WeeklyTask.objects.create(
            title="مهمة قديمة", description="بيانات اختبار فقط", allowed_formats="text",
            is_active=True, due_date=datetime.date.today() + datetime.timedelta(days=7),
        )
        cls.submission = TaskSubmission.objects.create(
            task=cls.task, participant=cls.student, points_awarded=None,
        )
        TaskReviewPermission.objects.create(supervisor=cls.supervisor, enabled=True, granted_by=cls.general)

    def _accept(self, user, **extra):
        self.client.force_login(user)
        return self.client.post(reverse("participants:weekly_task_review"), {
            "action": "accept", "submission_id": self.submission.pk, **extra,
        })

    def _assert_unchanged(self):
        self.student.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (100, 1000, 75))
        self.assertEqual(self.submission.status, "pending")
        self.assertIsNone(self.submission.points_awarded)
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_delegated_supervisor_cannot_resolve_legacy_award(self):
        response = self._accept(self.supervisor, historical_award="not_awarded")
        self.assertEqual(response.status_code, 403)
        self._assert_unchanged()

    def test_general_cannot_accept_without_valid_explicit_decision(self):
        for value in (None, "", "false", "forged"):
            extra = {} if value is None else {"historical_award": value}
            self.assertEqual(self._accept(self.general, **extra).status_code, 302)
            self._assert_unchanged()

    def test_confirming_previous_award_never_changes_balances(self):
        self.assertEqual(self._accept(self.general, historical_award="already_awarded").status_code, 302)
        self.student.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (100, 1000, 75))
        self.assertEqual(self.submission.status, "accepted")
        self.assertTrue(self.submission.points_awarded)
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_verified_unpaid_legacy_submission_awards_once(self):
        self._accept(self.general, historical_award="not_awarded")
        self._accept(self.general, historical_award="not_awarded")
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (110, 1100, 85))
        self.assertEqual(PointsLedgerEntry.objects.count(), 1)

    def test_system_keeps_existing_administrative_review_rights(self):
        self.assertEqual(self._accept(self.system, historical_award="already_awarded").status_code, 302)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.points_awarded)

    def test_new_submission_does_not_trust_forged_historical_control(self):
        self.submission.points_awarded = False
        self.submission.save(update_fields=["points_awarded"])
        self._accept(self.supervisor, historical_award="already_awarded")
        self.student.refresh_from_db()
        self.assertEqual(self.student.points, 110)
        self.assertEqual(PointsLedgerEntry.objects.count(), 1)

    def test_only_administrators_see_explicit_legacy_decision_control(self):
        self.client.force_login(self.general)
        self.assertContains(self.client.get(reverse("participants:weekly_task_review")), 'name="historical_award"')
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("participants:weekly_task_review"))
        self.assertNotContains(response, 'name="historical_award"')
        self.assertNotContains(response, 'name="action" value="accept"')
        self.assertContains(response, "يلزم تحقق المشرف العام")

    def test_backfill_never_uses_duplicate_or_renamed_titles_as_proof(self):
        duplicate = WeeklyTask.objects.create(
            title=self.task.title, description="عنوان مكرر", allowed_formats="text", due_date=self.task.due_date,
        )
        accepted = TaskSubmission.objects.create(task=duplicate, participant=self.student, status="accepted")
        PointsLedgerEntry.objects.create(
            participant=self.student, source="weekly_task", points_delta=10,
            description='قبول مهمة "عنوان قبل تغييره"',
        )
        migration = importlib.import_module("participants.migrations.0019_task_points_once")
        migration.backfill_awards(apps, SimpleNamespace(connection=connection))
        self.submission.refresh_from_db()
        accepted.refresh_from_db()
        self.student.refresh_from_db()
        self.assertIsNone(self.submission.points_awarded)
        self.assertTrue(accepted.points_awarded)
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (100, 1000, 75))
