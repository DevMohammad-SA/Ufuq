import datetime

from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import Group, Participant, PointsLedgerEntry, WeeklyActivityAttendance
from .views import ParticipantsDataPDFExportView


class LinkedPageMultiGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.supervisor = User.objects.create_user(username="multi-group", role=Role.GROUP_SUPERVISOR)
        cls.groups = [Group.objects.create(name=f"بيئة نطاق {index}") for index in range(3)]
        cls.students = []
        for index, group in enumerate(cls.groups):
            if index < 2:
                group.supervisor.add(cls.supervisor)
            user = User.objects.create_user(national_id=f"850000000{index}", full_name=f"طالب نطاق {index}", role=Role.PARTICIPANT)
            student = Participant.objects.create(user=user, group=group, academic_stage="grade_7")
            cls.students.append(student)
            PointsLedgerEntry.objects.create(participant=student, source="extra", points_delta=1, description=f"حركة {index}")

    def setUp(self):
        self.client.force_login(self.supervisor)

    def test_roster_and_ledger_include_all_and_only_assigned_groups(self):
        data = self.client.get(reverse("participants:participants_data"))
        self.assertEqual({item.pk for item in data.context["participants"]}, {item.pk for item in self.students[:2]})
        ledger = self.client.get(reverse("participants:points_ledger"))
        self.assertEqual({item.participant_id for item in ledger.context["entries"]}, {item.pk for item in self.students[:2]})

    def test_pdf_queryset_includes_both_groups_ignoring_foreign_name(self):
        request = RequestFactory().get("/", {"group": self.groups[2].name})
        request.user = self.supervisor
        rows = ParticipantsDataPDFExportView()._get_participants(request)
        self.assertEqual({item.pk for item in rows}, {item.pk for item in self.students[:2]})

    def test_activity_can_select_and_save_second_assigned_group_only(self):
        url = reverse("participants:weekly_activity_attendance")
        response = self.client.get(url, {"group": self.groups[1].pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_group_locked"])
        self.assertEqual(response.context["group"].pk, self.groups[1].pk)
        self.assertEqual({item.pk for item in response.context["all_groups"]}, {item.pk for item in self.groups[:2]})
        response = self.client.post(url, {
            "group": self.groups[1].pk, "date": datetime.date.today().isoformat(),
            f"attended_{self.students[1].pk}": "on",
            f"attended_{self.students[2].pk}": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(WeeklyActivityAttendance.objects.values_list("participant_id", flat=True)), [self.students[1].pk])
        self.students[2].refresh_from_db()
        self.assertEqual(self.students[2].points, 0)

    def test_activity_rejects_foreign_group_on_get_and_post(self):
        url = reverse("participants:weekly_activity_attendance")
        self.assertEqual(self.client.get(url, {"group": self.groups[2].pk}).status_code, 404)
        self.assertEqual(self.client.post(url, {
            "group": self.groups[2].pk, f"attended_{self.students[2].pk}": "on",
        }).status_code, 404)
        self.assertFalse(WeeklyActivityAttendance.objects.exists())
