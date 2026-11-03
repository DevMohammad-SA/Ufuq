"""Regression tests for reviewing a weekly-task submission after its due date.

The task deadline closes participant uploads. It never silently removes an
already-pending submission from the authorized review queue.
"""

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from participants.models import (
    Group,
    Participant,
    PointsLedgerEntry,
    TaskReviewPermission,
    TaskSubmission,
    WeeklyTask,
)
from participants.views import get_notification_counts


class ReviewAfterDeadlineTests(TestCase):
    def setUp(self):
        self.general = User.objects.create_user(
            username="late-review-general",
            password="pw12345678",
            full_name="المشرف العام",
            role=Role.GENERAL_SUPERVISOR,
        )
        self.group_supervisor = User.objects.create_user(
            username="late-review-group",
            password="pw12345678",
            full_name="مشرف البيئة",
            role=Role.GROUP_SUPERVISOR,
        )
        self.group = Group.objects.create(name="بيئة المراجعة المتأخرة")
        self.group.supervisor.add(self.group_supervisor)
        student_user = User.objects.create_user(
            national_id="8710000001",
            password="pw12345678",
            full_name="طالب المراجعة المتأخرة",
            role=Role.PARTICIPANT,
        )
        self.student = Participant.objects.create(
            user=student_user,
            group=self.group,
            academic_stage="grade_7",
            points=4,
            miles=40,
            purchase_points=4,
        )
        self.expired_task = WeeklyTask.objects.create(
            title="مهمة انتهى موعدها وبانتظار التصحيح",
            description="تسليم قبل الموعد يحتاج قرار مراجعة.",
            due_date=timezone.localdate() - datetime.timedelta(days=1),
            allowed_formats="text",
            is_active=True,
            created_by=self.general,
        )
        self.submission = TaskSubmission.objects.create(
            task=self.expired_task,
            participant=self.student,
            text_content="تسليم وصل قبل نهاية الموعد",
        )
        submitted_at = timezone.now() - datetime.timedelta(days=2)
        TaskSubmission.objects.filter(pk=self.submission.pk).update(
            submitted_at=submitted_at
        )
        self.submission.refresh_from_db()
        self.review_url = reverse("participants:weekly_task_review")

    def balances(self):
        self.student.refresh_from_db()
        return self.student.points, self.student.miles, self.student.purchase_points

    def test_general_sees_expired_pending_submission_with_accept_and_reject_only(self):
        self.client.force_login(self.general)
        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.expired_task.title)
        self.assertContains(response, 'value="accept"')
        self.assertContains(response, 'value="reject"')
        self.assertNotContains(response, 'value="return_for_revision"')
        self.assertContains(response, "انتهى موعد رفع الطالب")
        self.assertEqual(get_notification_counts(self.general)["tasks"], 1)

    def test_general_can_accept_after_deadline_once_using_original_submission_date(self):
        before = self.balances()
        self.client.force_login(self.general)
        response = self.client.post(
            self.review_url,
            {"action": "accept", "submission_id": self.submission.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.ACCEPTED)
        self.assertTrue(self.submission.points_awarded)
        self.assertEqual(self.balances(), (before[0] + 10, before[1] + 100, before[2] + 10))
        ledger = PointsLedgerEntry.objects.get(
            participant=self.student,
            source=PointsLedgerEntry.Source.WEEKLY_TASK,
        )
        self.assertEqual(ledger.event_date, timezone.localdate(self.submission.submitted_at))

        self.client.post(
            self.review_url,
            {"action": "accept", "submission_id": self.submission.pk},
        )
        self.assertEqual(self.balances(), (before[0] + 10, before[1] + 100, before[2] + 10))
        self.assertEqual(
            PointsLedgerEntry.objects.filter(
                participant=self.student,
                source=PointsLedgerEntry.Source.WEEKLY_TASK,
            ).count(),
            1,
        )

    def test_general_can_reject_after_deadline_without_points(self):
        before = self.balances()
        self.client.force_login(self.general)
        response = self.client.post(
            self.review_url,
            {
                "action": "reject",
                "submission_id": self.submission.pk,
                "rejection_reason": "الملف لا يحقق المطلوب.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.REJECTED)
        self.assertEqual(self.submission.rejection_reason, "الملف لا يحقق المطلوب.")
        self.assertEqual(self.balances(), before)
        self.assertFalse(
            PointsLedgerEntry.objects.filter(
                participant=self.student,
                source=PointsLedgerEntry.Source.WEEKLY_TASK,
            ).exists()
        )

    def test_return_for_revision_remains_blocked_after_deadline(self):
        self.client.force_login(self.general)
        response = self.client.post(
            self.review_url,
            {
                "action": "return_for_revision",
                "submission_id": self.submission.pk,
                "revision_note": "عدّل الإجابة ثم أعد الرفع.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.PENDING)
        self.assertEqual(self.submission.revision_note, "")

    def test_general_also_sees_a_pending_submission_when_task_was_deactivated(self):
        self.expired_task.is_active = False
        self.expired_task.save(update_fields=["is_active"])
        self.client.force_login(self.general)

        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.expired_task.title)
        self.assertContains(response, 'value="accept"')
        self.assertContains(response, 'value="reject"')
        self.assertNotContains(response, 'value="return_for_revision"')
        self.assertContains(response, "تفعيل هذه المهمة")

    def test_delegated_group_supervisor_can_review_expired_submission_in_scope(self):
        TaskReviewPermission.objects.create(
            supervisor=self.group_supervisor,
            enabled=True,
            granted_by=self.general,
        )
        self.client.force_login(self.group_supervisor)
        page = self.client.get(self.review_url)
        self.assertContains(page, self.expired_task.title)

        response = self.client.post(
            self.review_url,
            {"action": "accept", "submission_id": self.submission.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.ACCEPTED)
        self.assertEqual(self.submission.reviewed_by, self.group_supervisor)
