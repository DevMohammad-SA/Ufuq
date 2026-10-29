"""A failed roster must not persist half of the students or a success message."""
import datetime
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import CircleAttendance, Group, MeetingAttendance, Participant, PointsLedgerEntry, WeeklyActivityAttendance
from .views import apply_points_delta


class AtomicRosterSaveTests(TestCase):
    def setUp(self):
        self.supervisor = User.objects.create_user(
            username="atomic-roster-supervisor", role=Role.GROUP_SUPERVISOR, password="test-only-password",
        )
        self.group = Group.objects.create(name="بيئة اختبار الحفظ الذري")
        self.group.supervisor.add(self.supervisor)
        self.students = []
        for identity in ("8320000001", "8320000002"):
            user = User.objects.create_user(national_id=identity, role=Role.PARTICIPANT, password="test-only-password")
            self.students.append(Participant.objects.create(
                user=user, group=self.group, academic_stage="grade_7", points=30, miles=800, purchase_points=25,
            ))
        self.client.force_login(self.supervisor)

    def test_failure_rolls_back_entire_roster_and_has_no_success_message(self):
        variants = (
            ("supervisor_dashboard", MeetingAttendance),
            ("quran_circle_attendance", CircleAttendance),
            ("weekly_activity_attendance", WeeklyActivityAttendance),
        )
        for route, model in variants:
            with self.subTest(route=route):
                calls = []
                def write_then_fail(*args, **kwargs):
                    calls.append(args[0].pk)
                    if len(calls) == 2:
                        raise RuntimeError("synthetic failure; no production data")
                    return apply_points_delta(*args, **kwargs)
                payload = {"group": str(self.group.pk), "date": "2026-09-15"}
                payload.update({f"attended_{student.pk}": "on" for student in self.students})
                with patch("participants.views.apply_points_delta", side_effect=write_then_fail):
                    with self.assertRaisesMessage(RuntimeError, "synthetic failure"):
                        self.client.post(reverse("participants:" + route), payload)
                self.assertEqual(len(calls), 2)
                self.assertFalse(model.objects.exists())
                self.assertFalse(PointsLedgerEntry.objects.exists())
                for student in self.students:
                    student.refresh_from_db()
                    self.assertEqual((student.points, student.miles, student.purchase_points), (30, 800, 25))
                page = self.client.get(reverse("participants:" + route), {
                    "group": self.group.pk, "date": "2026-09-15",
                })
                self.assertEqual(page.status_code, 200)
                self.assertFalse(any(message.level_tag == "success" for message in get_messages(page.wsgi_request)))
