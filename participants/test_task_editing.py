"""Regression coverage for editing an existing weekly task in place."""

import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from participants.models import Group, Participant, TaskSubmission, WeeklyTask


class WeeklyTaskEditingTests(TestCase):
    def setUp(self):
        self.general = User.objects.create_user(
            username="task-editor-general",
            password="pw12345678",
            role=Role.GENERAL_SUPERVISOR,
        )
        self.system = User.objects.create_user(
            username="task-editor-system",
            password="pw12345678",
            role=Role.SUPERADMIN,
        )
        self.group_supervisor = User.objects.create_user(
            username="task-editor-group",
            password="pw12345678",
            role=Role.GROUP_SUPERVISOR,
        )
        self.group = Group.objects.create(name="بيئة تعديل المهمة")
        self.group.supervisor.add(self.group_supervisor)
        participant_user = User.objects.create_user(
            national_id="8900000001",
            password="pw12345678",
            role=Role.PARTICIPANT,
        )
        self.participant = Participant.objects.create(
            user=participant_user,
            group=self.group,
            academic_stage="grade_7",
            points=23,
            miles=230,
            purchase_points=23,
        )
        self.task = WeeklyTask.objects.create(
            title="المهمة الأصلية",
            description="الوصف الأصلي",
            due_date=datetime.date.today() + datetime.timedelta(days=4),
            allowed_formats="text",
            require_all_formats=False,
            is_active=False,
            created_by=self.general,
        )
        self.submission = TaskSubmission.objects.create(
            task=self.task,
            participant=self.participant,
            text_content="تسليم سابق محفوظ",
            status=TaskSubmission.Status.ACCEPTED,
            points_awarded=True,
        )
        self.url = reverse("participants:weekly_task_review")

    def update_payload(self, **overrides):
        payload = {
            "action": "update_task",
            "task_id": self.task.pk,
            "title": "المهمة بعد التعديل",
            "description": "وصف محدّث للمهمة نفسها",
            "due_date": (datetime.date.today() + datetime.timedelta(days=12)).isoformat(),
            "allowed_formats": ["pdf", "text"],
            "require_all_formats": "on",
            # The edit form deliberately excludes this field; a crafted value
            # must not change the existing activation control.
            "is_active": "on",
        }
        payload.update(overrides)
        return payload

    def test_general_edits_same_task_without_touching_submission_or_balances(self):
        self.client.force_login(self.general)
        editor = self.client.get(self.url, {"edit_task": self.task.pk})
        self.assertEqual(editor.status_code, 200)
        self.assertContains(editor, "تعديل المهمة: المهمة الأصلية")

        before = (self.participant.points, self.participant.miles, self.participant.purchase_points)
        response = self.client.post(self.url, self.update_payload())
        self.assertRedirects(response, self.url)

        self.task.refresh_from_db()
        self.submission.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertEqual(self.task.pk, self.submission.task_id)
        self.assertEqual(self.task.title, "المهمة بعد التعديل")
        self.assertEqual(self.task.description, "وصف محدّث للمهمة نفسها")
        self.assertEqual(self.task.allowed_formats, "pdf,text")
        self.assertTrue(self.task.require_all_formats)
        self.assertFalse(self.task.is_active)
        self.assertEqual(self.task.created_by, self.general)
        self.assertEqual(TaskSubmission.objects.filter(task=self.task).count(), 1)
        self.assertEqual(self.submission.text_content, "تسليم سابق محفوظ")
        self.assertEqual(self.submission.status, TaskSubmission.Status.ACCEPTED)
        self.assertTrue(self.submission.points_awarded)
        self.assertEqual((self.participant.points, self.participant.miles, self.participant.purchase_points), before)

    def test_system_keeps_existing_task_management_capability(self):
        self.client.force_login(self.system)
        response = self.client.post(self.url, self.update_payload(title="تعديل مشرف النظام"))
        self.assertRedirects(response, self.url)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "تعديل مشرف النظام")

    def test_group_supervisor_cannot_see_or_post_edit_controls(self):
        self.client.force_login(self.group_supervisor)
        page = self.client.get(self.url, {"edit_task": self.task.pk})
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "حفظ تعديلات المهمة")

        response = self.client.post(self.url, self.update_payload())
        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "المهمة الأصلية")

    def test_invalid_formats_do_not_partially_change_task(self):
        self.client.force_login(self.general)
        response = self.client.post(self.url, self.update_payload(allowed_formats=[]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "يجب اختيار صيغة واحدة على الأقل")
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "المهمة الأصلية")
        self.assertEqual(self.task.allowed_formats, "text")
