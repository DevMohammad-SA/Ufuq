"""Current participation charts exclude archived students; histories remain intact."""
import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import Group, Participant, TaskSubmission, WeeklyTask


class ActiveStudentStatisticsTests(TestCase):
    def setUp(self):
        manager = User.objects.create_user(
            username="active-stats-manager", role=Role.GENERAL_SUPERVISOR, password="test-only-password",
        )
        self.group = Group.objects.create(name="إحصاء الطلاب النشطين")
        self.empty_group = Group.objects.create(name="إحصاء البيئة المؤرشفة")
        active_user = User.objects.create_user(national_id="8340000001", role=Role.PARTICIPANT, password="test-only-password")
        archived_user = User.objects.create_user(national_id="8340000002", role=Role.PARTICIPANT, password="test-only-password", is_active=False)
        self.active = Participant.objects.create(user=active_user, group=self.group, academic_stage="grade_7", points=10)
        self.archived = Participant.objects.create(user=archived_user, group=self.empty_group, academic_stage="grade_7", points=99)
        task = WeeklyTask.objects.create(
            title="إحصاء المهمة الحالية", allowed_formats="text", is_active=True,
            due_date=datetime.date.today() + datetime.timedelta(days=2),
        )
        TaskSubmission.objects.create(task=task, participant=self.active, text_content="اختبار نشط", status="accepted")
        TaskSubmission.objects.create(task=task, participant=self.archived, text_content="اختبار مؤرشف", status="pending")
        self.client.force_login(manager)

    def test_current_totals_group_averages_and_latest_task_share_active_scope(self):
        response = self.client.get(reverse("participants:general_supervisor_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["kpi_total_participants"], 1)
        charts = response.context["charts_data"]
        counts = dict(zip(charts["groupLabels"], charts["groupCounts"]))
        averages = dict(zip(charts["avgLabels"], charts["avgPoints"]))
        self.assertEqual(counts, {self.group.name: 1, self.empty_group.name: 0})
        self.assertEqual(averages, {self.group.name: 10, self.empty_group.name: 0})
        self.assertEqual(charts["taskData"], [1, 0, 0, 0, 0])
        self.assertEqual(TaskSubmission.objects.count(), 2)

    def test_archive_updates_current_statistics_but_preserves_report_histories(self):
        result = self.client.post(reverse("participants:participant_delete", kwargs={"pk": self.active.pk}), {"confirm": "yes"})
        self.assertEqual(result.status_code, 302)
        response = self.client.get(reverse("participants:general_supervisor_dashboard"))
        self.assertEqual(response.context["kpi_total_participants"], 0)
        self.assertEqual(response.context["charts_data"]["groupCounts"], [0, 0])
        self.assertEqual(response.context["charts_data"]["taskData"], [0, 0, 0, 0, 0])
        self.assertEqual(Participant.objects.count(), 2)
        self.assertEqual(TaskSubmission.objects.count(), 2)
        report = self.client.get(reverse("participants:student_report"))
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.context["totals"]["students"], 2)
