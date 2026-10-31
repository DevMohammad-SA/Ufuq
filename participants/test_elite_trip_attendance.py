"""Regression tests for data-only elite-trip preparation."""
import datetime

from django.contrib import messages
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import EliteTripAttendance, Group, Participant, PointsLedgerEntry
from .views import build_navbar


class EliteTripAttendanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.supervisor = User.objects.create_user(
            username="elite-group-supervisor",
            password="test-only-password",
            role=Role.GROUP_SUPERVISOR,
            must_set_password=False,
        )
        cls.general = User.objects.create_user(
            username="elite-general-supervisor",
            password="test-only-password",
            role=Role.GENERAL_SUPERVISOR,
            must_set_password=False,
        )
        cls.system = User.objects.create_user(
            username="elite-system-supervisor",
            password="test-only-password",
            role=Role.SUPERADMIN,
            must_set_password=False,
        )
        cls.first_group = Group.objects.create(name="بيئة رحلة أولى")
        cls.second_group = Group.objects.create(name="بيئة رحلة ثانية")
        cls.foreign_group = Group.objects.create(name="بيئة رحلة غير متاحة")
        cls.first_group.supervisor.add(cls.supervisor)
        cls.second_group.supervisor.add(cls.supervisor)
        cls.first_student = cls.make_participant("8300000001", "طالب رحلة أول", cls.first_group)
        cls.second_student = cls.make_participant("8300000002", "طالب رحلة ثان", cls.second_group)
        cls.foreign_student = cls.make_participant("8300000003", "طالب رحلة أجنبي", cls.foreign_group)
        cls.archived_student = cls.make_participant("8300000004", "طالب رحلة مؤرشف", cls.first_group)
        cls.archived_student.user.is_active = False
        cls.archived_student.user.save(update_fields=["is_active"])

    @staticmethod
    def make_participant(national_id, full_name, group):
        user = User.objects.create_user(
            national_id=national_id,
            full_name=full_name,
            password="test-only-password",
            role=Role.PARTICIPANT,
            must_set_password=False,
        )
        return Participant.objects.create(
            user=user,
            group=group,
            academic_stage="grade_7",
            points=17,
            miles=170,
            purchase_points=17,
        )

    @property
    def url(self):
        return reverse("participants:elite_trip_attendance")

    def setUp(self):
        self.client.force_login(self.supervisor)

    def test_only_group_supervisor_sees_own_active_students_and_own_groups(self):
        response = self.client.get(self.url, {"group": self.second_group.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["group"], self.second_group)
        self.assertTrue(response.context["show_group_selector"])
        self.assertEqual(
            {group.pk for group in response.context["all_groups"]},
            {self.first_group.pk, self.second_group.pk},
        )
        self.assertEqual(
            [entry["participant"].pk for entry in response.context["roster"]],
            [self.second_student.pk],
        )
        self.assertContains(response, "حاضر")
        self.assertContains(response, "معتذر")
        self.assertContains(response, "غائب")
        self.assertContains(response, "غير مرشح")
        self.assertContains(response, 'name="status_%s"' % self.second_student.pk)
        self.assertContains(response, 'class="student-extra" hidden')
        self.assertContains(response, "الإحصاءات تعرض السجلات المحفوظة")

    def test_save_supports_all_statuses_and_never_mutates_rewards_or_ledger(self):
        selected_date = datetime.date(2026, 10, 31)
        before = {
            participant.pk: (participant.points, participant.miles, participant.purchase_points)
            for participant in [self.first_student, self.second_student, self.foreign_student]
        }
        response = self.client.post(
            self.url,
            {
                "group": self.first_group.pk,
                "date": selected_date.isoformat(),
                f"status_{self.first_student.pk}": EliteTripAttendance.Status.PRESENT,
                f"status_{self.foreign_student.pk}": EliteTripAttendance.Status.NOT_NOMINATED,
            },
        )
        expected = f"{self.url}?date={selected_date.isoformat()}&group={self.first_group.pk}"
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], expected)
        record = EliteTripAttendance.objects.get(participant=self.first_student, date=selected_date)
        self.assertEqual(record.status, EliteTripAttendance.Status.PRESENT)
        self.assertEqual(record.recorded_by, self.supervisor)
        self.assertFalse(EliteTripAttendance.objects.filter(participant=self.foreign_student).exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        for participant in [self.first_student, self.second_student, self.foreign_student]:
            participant.refresh_from_db()
            self.assertEqual(
                (participant.points, participant.miles, participant.purchase_points),
                before[participant.pk],
            )

        follow = self.client.get(expected)
        delivered = [
            str(message)
            for message in get_messages(follow.wsgi_request)
            if message.level == messages.SUCCESS
        ]
        self.assertTrue(any(selected_date.strftime("%Y/%m/%d") in message for message in delivered))
        self.assertEqual(follow.context["statistics"], {
            "total": 1,
            "recorded": 1,
            "present": 1,
            "excused": 0,
            "absent": 0,
            "not_nominated": 0,
        })

    def test_re_save_updates_same_record_and_statistics_without_duplicates(self):
        selected_date = datetime.date(2026, 11, 1)
        first = self.client.post(
            self.url,
            {
                "group": self.first_group.pk,
                "date": selected_date.isoformat(),
                f"status_{self.first_student.pk}": EliteTripAttendance.Status.EXCUSED,
            },
        )
        self.assertEqual(first.status_code, 302)
        second = self.client.post(
            self.url,
            {
                "group": self.first_group.pk,
                "date": selected_date.isoformat(),
                f"status_{self.first_student.pk}": EliteTripAttendance.Status.NOT_NOMINATED,
            },
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(EliteTripAttendance.objects.filter(participant=self.first_student, date=selected_date).count(), 1)
        record = EliteTripAttendance.objects.get(participant=self.first_student, date=selected_date)
        self.assertEqual(record.status, EliteTripAttendance.Status.NOT_NOMINATED)
        response = self.client.get(second["Location"])
        self.assertEqual(response.context["statistics"]["not_nominated"], 1)
        self.assertEqual(response.context["statistics"]["excused"], 0)

    def test_invalid_status_or_multiple_status_values_are_rejected_before_any_record_is_saved(self):
        invalid = self.client.post(
            self.url,
            {
                "group": self.first_group.pk,
                "date": datetime.date(2026, 11, 2).isoformat(),
                f"status_{self.first_student.pk}": "tampered",
            },
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, "حالة التحضير غير صالحة", status_code=400)
        multiple = self.client.post(
            self.url,
            {
                "group": self.first_group.pk,
                "date": datetime.date(2026, 11, 2).isoformat(),
                f"status_{self.first_student.pk}": [
                    EliteTripAttendance.Status.PRESENT,
                    EliteTripAttendance.Status.ABSENT,
                ],
            },
        )
        self.assertEqual(multiple.status_code, 400)
        self.assertFalse(EliteTripAttendance.objects.exists())

    def test_missing_or_invalid_date_is_rejected_without_a_write(self):
        for raw_date in ["", "not-a-date"]:
            with self.subTest(raw_date=raw_date):
                response = self.client.post(
                    self.url,
                    {
                        "group": self.first_group.pk,
                        "date": raw_date,
                        f"status_{self.first_student.pk}": EliteTripAttendance.Status.PRESENT,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "أدخل تاريخ تحضير صحيحًا", status_code=400)
        self.assertFalse(EliteTripAttendance.objects.exists())

    def test_database_constraint_rejects_status_outside_the_four_choices(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EliteTripAttendance.objects.create(
                participant=self.first_student,
                date=datetime.date(2026, 11, 3),
                status="not-a-status",
                recorded_by=self.supervisor,
            )
        self.assertFalse(EliteTripAttendance.objects.exists())

    def test_foreign_group_is_rejected_on_get_and_post(self):
        self.assertEqual(self.client.get(self.url, {"group": self.foreign_group.pk}).status_code, 404)
        self.assertEqual(
            self.client.post(
                self.url,
                {
                    "group": self.foreign_group.pk,
                    "date": datetime.date.today().isoformat(),
                    f"status_{self.foreign_student.pk}": EliteTripAttendance.Status.PRESENT,
                },
            ).status_code,
            404,
        )
        self.assertFalse(EliteTripAttendance.objects.exists())

    def test_general_supervisor_can_select_any_group_and_save_without_points(self):
        selected_date = datetime.date(2026, 11, 4)
        self.client.force_login(self.general)
        response = self.client.get(self.url, {"group": self.foreign_group.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["group"], self.foreign_group)
        self.assertTrue(response.context["show_group_selector"])
        self.assertEqual(
            {group.pk for group in response.context["all_groups"]},
            {self.first_group.pk, self.second_group.pk, self.foreign_group.pk},
        )
        self.assertEqual(
            [entry["participant"].pk for entry in response.context["roster"]],
            [self.foreign_student.pk],
        )
        before = (
            self.foreign_student.points,
            self.foreign_student.miles,
            self.foreign_student.purchase_points,
        )
        saved = self.client.post(
            self.url,
            {
                "group": self.foreign_group.pk,
                "date": selected_date.isoformat(),
                f"status_{self.foreign_student.pk}": EliteTripAttendance.Status.EXCUSED,
            },
        )
        self.assertEqual(saved.status_code, 302)
        record = EliteTripAttendance.objects.get(
            participant=self.foreign_student,
            date=selected_date,
        )
        self.assertEqual(record.recorded_by, self.general)
        self.assertEqual(record.status, EliteTripAttendance.Status.EXCUSED)
        self.foreign_student.refresh_from_db()
        self.assertEqual(
            (
                self.foreign_student.points,
                self.foreign_student.miles,
                self.foreign_student.purchase_points,
            ),
            before,
        )
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertIn("elite_trip", [item["key"] for item in build_navbar(self.general, "elite_trip")])

    def test_system_and_participant_are_forbidden_and_do_not_receive_the_link(self):
        for user in [self.system, self.first_student.user]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertNotIn("elite_trip", [item["key"] for item in build_navbar(user, "home")])

        self.assertIn("elite_trip", [item["key"] for item in build_navbar(self.supervisor, "elite_trip")])

    def test_inactive_group_supervisor_cannot_open_page_or_receive_its_link(self):
        self.supervisor.is_active = False
        self.supervisor.save(update_fields=["is_active"])
        self.client.force_login(self.supervisor)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.assertNotIn("elite_trip", [item["key"] for item in build_navbar(self.supervisor, "elite_trip")])
