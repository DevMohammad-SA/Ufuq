"""Regression coverage for returning a weekly task to the participant for correction.

All cases use the isolated test database.  They exercise the real review and
submission routes rather than mutating production-like state directly.
"""

import datetime

from django.test import TestCase
from django.urls import reverse

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


class TaskReturnForRevisionTests(TestCase):
    def setUp(self):
        self.general = User.objects.create_user(
            username="return-general",
            password="pw12345678",
            full_name="المشرف العام",
            role=Role.GENERAL_SUPERVISOR,
        )
        self.group_supervisor = User.objects.create_user(
            username="return-group-supervisor",
            password="pw12345678",
            full_name="مشرف البيئة",
            role=Role.GROUP_SUPERVISOR,
        )
        self.other_supervisor = User.objects.create_user(
            username="return-other-supervisor",
            password="pw12345678",
            full_name="مشرف بيئة أخرى",
            role=Role.GROUP_SUPERVISOR,
        )
        self.group = Group.objects.create(name="بيئة إعادة التعديل")
        self.other_group = Group.objects.create(name="بيئة خارج النطاق")
        self.group.supervisor.add(self.group_supervisor)
        self.other_group.supervisor.add(self.other_supervisor)

        self.student_user = User.objects.create_user(
            national_id="8700000001",
            password="pw12345678",
            full_name="طالب إعادة التعديل",
            role=Role.PARTICIPANT,
        )
        self.student = Participant.objects.create(
            user=self.student_user,
            group=self.group,
            academic_stage="grade_7",
            points=14,
            miles=140,
            purchase_points=14,
        )
        other_user = User.objects.create_user(
            national_id="8700000002",
            password="pw12345678",
            full_name="طالب خارج النطاق",
            role=Role.PARTICIPANT,
        )
        self.other_student = Participant.objects.create(
            user=other_user,
            group=self.other_group,
            academic_stage="grade_7",
        )
        self.task = WeeklyTask.objects.create(
            title="مهمة تحتاج تعديل",
            description="اختبار إعادة التعديل",
            due_date=datetime.date.today() + datetime.timedelta(days=3),
            allowed_formats="text",
            is_active=True,
            created_by=self.general,
        )
        self.submission = TaskSubmission.objects.create(
            task=self.task,
            participant=self.student,
            text_content="المحاولة الأولى",
        )
        self.other_submission = TaskSubmission.objects.create(
            task=self.task,
            participant=self.other_student,
            text_content="تسليم خارج النطاق",
        )
        self.review_url = reverse("participants:weekly_task_review")
        self.submit_url = reverse("participants:task_submission")

    def balances(self):
        self.student.refresh_from_db()
        return self.student.points, self.student.miles, self.student.purchase_points

    def return_submission(self, user=None, note="أضف أمثلة أوضح ثم أعد الإرسال."):
        self.client.force_login(user or self.general)
        return self.client.post(
            self.review_url,
            {
                "action": "return_for_revision",
                "submission_id": self.submission.pk,
                "revision_note": note,
            },
        )

    def test_note_is_required_bounded_and_return_never_changes_balances(self):
        self.client.force_login(self.general)
        before = self.balances()

        for note in ("   ", "ط" * 2001):
            with self.subTest(note_length=len(note)):
                response = self.client.post(
                    self.review_url,
                    {
                        "action": "return_for_revision",
                        "submission_id": self.submission.pk,
                        "revision_note": note,
                    },
                )
                self.assertEqual(response.status_code, 302)
                self.submission.refresh_from_db()
                self.assertEqual(self.submission.status, TaskSubmission.Status.PENDING)
                self.assertEqual(self.submission.revision_note, "")

        response = self.return_submission(note="  أضف أمثلة أوضح ثم أعد الإرسال.  ")
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.REVISION_REQUIRED)
        self.assertEqual(self.submission.revision_note, "أضف أمثلة أوضح ثم أعد الإرسال.")
        self.assertEqual(self.submission.reviewed_by, self.general)
        self.assertIsNotNone(self.submission.reviewed_at)
        self.assertFalse(self.submission.reopened_for_resubmission)
        self.assertEqual(self.balances(), before)
        self.assertFalse(
            PointsLedgerEntry.objects.filter(
                participant=self.student,
                source=PointsLedgerEntry.Source.WEEKLY_TASK,
            ).exists()
        )

        # A crafted later acceptance cannot act on a non-pending return.
        self.client.post(
            self.review_url,
            {"action": "accept", "submission_id": self.submission.pk},
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.REVISION_REQUIRED)
        self.assertEqual(self.balances(), before)

    def test_student_sees_note_and_resubmits_same_row_before_due_date(self):
        self.return_submission()
        self.client.force_login(self.student_user)

        page = self.client.get(self.submit_url)
        self.assertContains(page, "أضف أمثلة أوضح ثم أعد الإرسال.")
        self.assertContains(page, "إعادة إرسال المهمة")
        self.assertEqual(get_notification_counts(self.student_user)["tasks"], 1)

        response = self.client.post(
            self.submit_url,
            {
                "task_id": self.task.pk,
                "chosen_format": "text",
                "text_content": "محاولة مصححة قبل الموعد",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.PENDING)
        self.assertEqual(self.submission.text_content, "محاولة مصححة قبل الموعد")
        self.assertEqual(self.submission.revision_note, "أضف أمثلة أوضح ثم أعد الإرسال.")
        self.assertIsNone(self.submission.reviewed_by)
        self.assertIsNone(self.submission.reviewed_at)
        self.assertEqual(TaskSubmission.objects.filter(task=self.task, participant=self.student).count(), 1)
        self.assertEqual(get_notification_counts(self.student_user)["tasks"], 0)

    def test_return_does_not_extend_due_date_but_archive_reopen_still_can(self):
        past_task = WeeklyTask.objects.create(
            title="مهمة انتهى موعدها",
            description="اختبار الموعد",
            due_date=datetime.date.today() - datetime.timedelta(days=1),
            allowed_formats="text",
            is_active=True,
            created_by=self.general,
        )
        returned = TaskSubmission.objects.create(
            task=past_task,
            participant=self.student,
            text_content="تسليم يحتاج تعديل",
        )
        self.client.force_login(self.general)
        blocked_return = self.client.post(
            self.review_url,
            {
                "action": "return_for_revision",
                "submission_id": returned.pk,
                "revision_note": "عدّل المحتوى.",
            },
        )
        self.assertEqual(blocked_return.status_code, 302)
        returned.refresh_from_db()
        self.assertEqual(returned.status, TaskSubmission.Status.PENDING)
        self.assertEqual(returned.revision_note, "")

        # Simulate a legacy/crafted returned state to prove it still does not
        # create an implicit extension after the original due date.
        returned.status = TaskSubmission.Status.REVISION_REQUIRED
        returned.revision_note = "عدّل المحتوى."
        returned.save(update_fields=["status", "revision_note"])
        self.client.force_login(self.student_user)
        response = self.client.post(
            self.submit_url,
            {"task_id": past_task.pk, "chosen_format": "text", "text_content": "محاولة متأخرة"},
        )
        self.assertEqual(response.status_code, 302)
        returned.refresh_from_db()
        self.assertEqual(returned.status, TaskSubmission.Status.REVISION_REQUIRED)
        self.assertEqual(returned.text_content, "تسليم يحتاج تعديل")

        returned.reopened_for_resubmission = True
        returned.save(update_fields=["reopened_for_resubmission"])
        page = self.client.get(self.submit_url)
        self.assertContains(page, past_task.title)
        response = self.client.post(
            self.submit_url,
            {"task_id": past_task.pk, "chosen_format": "text", "text_content": "محاولة بتفويض إعادة الفتح"},
        )
        self.assertEqual(response.status_code, 302)
        returned.refresh_from_db()
        self.assertEqual(returned.status, TaskSubmission.Status.PENDING)
        self.assertEqual(returned.text_content, "محاولة بتفويض إعادة الفتح")
        self.assertFalse(returned.reopened_for_resubmission)

    def test_archive_shows_returned_status_and_revision_note(self):
        self.return_submission(note="عدّل الجزء الثاني ثم أعد الإرسال.")
        self.client.force_login(self.general)
        page = self.client.get(
            reverse("participants:tasks_archive"), {"task": self.task.pk}
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "مُعادة للتعديل")
        self.assertContains(page, "عدّل الجزء الثاني ثم أعد الإرسال.")

    def test_delegated_group_supervisor_is_scoped_and_rechecked(self):
        self.client.force_login(self.group_supervisor)
        denied = self.client.post(
            self.review_url,
            {
                "action": "return_for_revision",
                "submission_id": self.submission.pk,
                "revision_note": "عدّل المثال.",
            },
        )
        self.assertEqual(denied.status_code, 403)

        TaskReviewPermission.objects.create(
            supervisor=self.group_supervisor,
            enabled=True,
            granted_by=self.general,
        )
        own = self.return_submission(self.group_supervisor, "عدّل المثال ثم أرسله.")
        self.assertEqual(own.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, TaskSubmission.Status.REVISION_REQUIRED)
        self.assertEqual(self.submission.reviewed_by, self.group_supervisor)

        foreign = self.client.post(
            self.review_url,
            {
                "action": "return_for_revision",
                "submission_id": self.other_submission.pk,
                "revision_note": "لن يصل هذا السجل.",
            },
        )
        self.assertEqual(foreign.status_code, 404)
        self.other_submission.refresh_from_db()
        self.assertEqual(self.other_submission.status, TaskSubmission.Status.PENDING)

    def test_dashboard_counts_returned_separately_from_not_submitted(self):
        self.return_submission()
        self.client.force_login(self.general)
        response = self.client.get(reverse("participants:general_supervisor_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["charts_data"]["taskData"], [0, 0, 1, 1, 0])
