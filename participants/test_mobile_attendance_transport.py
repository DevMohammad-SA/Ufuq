"""Synthetic mobile-roster transport regressions.

Run only with: python manage.py test participants.test_mobile_attendance_transport
--settings=config.test_settings. No production fixtures or external services.
"""
import datetime
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import (
    CircleAttendance,
    EliteTripAttendance,
    Group,
    MeetingAttendance,
    MeetingNote,
    Participant,
    PointsLedgerEntry,
    WeeklyActivityAttendance,
)
from .views import apply_points_delta


class MobileAttendanceTransportTests(TestCase):
    DATE = datetime.date(2026, 9, 15)
    INITIAL_BALANCES = (30, 800, 25)
    VARIANTS = (
        ("supervisor_dashboard", MeetingAttendance, "week_start_date", 10,
         PointsLedgerEntry.Source.MEETING_ATTENDANCE),
        ("quran_circle_attendance", CircleAttendance, "date", 5,
         PointsLedgerEntry.Source.QURAN_CIRCLE),
        ("weekly_activity_attendance", WeeklyActivityAttendance, "date", 10,
         PointsLedgerEntry.Source.WEEKLY_ACTIVITY),
        ("elite_trip_attendance", EliteTripAttendance, "date", 0, None),
    )

    @classmethod
    def setUpTestData(cls):
        cls.supervisor = User.objects.create_user(
            username="mobile-roster-supervisor", role=Role.GROUP_SUPERVISOR,
            password="synthetic-test-password", must_set_password=False,
        )
        cls.general = User.objects.create_user(
            username="mobile-roster-general", role=Role.GENERAL_SUPERVISOR,
            password="synthetic-test-password", must_set_password=False,
        )
        cls.group = Group.objects.create(name="Synthetic mobile roster")
        cls.group.supervisor.add(cls.supervisor)
        cls.foreign_group = Group.objects.create(name="Synthetic foreign roster")
        cls.students = [
            cls.make_student("8391000001", "First synthetic student", cls.group),
            cls.make_student("8391000002", "Second synthetic student", cls.group),
            cls.make_student("8391000003", "Third synthetic student", cls.group),
        ]
        cls.foreign = cls.make_student("8391000004", "Foreign student", cls.foreign_group)
        cls.inactive = cls.make_student("8391000005", "Inactive student", cls.group)
        cls.inactive.user.is_active = False
        cls.inactive.user.save(update_fields=["is_active"])
        cls.wrong_role = cls.make_student("8391000006", "Wrong-role student", cls.group)
        cls.wrong_role.user.role = Role.GROUP_SUPERVISOR
        cls.wrong_role.user.save(update_fields=["role"])

    @classmethod
    def make_student(cls, national_id, name, group):
        user = User.objects.create_user(
            national_id=national_id, full_name=name, role=Role.PARTICIPANT,
            password="synthetic-test-password", must_set_password=False,
        )
        return Participant.objects.create(
            user=user, group=group, academic_stage="grade_7",
            points=cls.INITIAL_BALANCES[0], miles=cls.INITIAL_BALANCES[1],
            purchase_points=cls.INITIAL_BALANCES[2],
        )

    def setUp(self):
        self.client.force_login(self.supervisor)

    def url(self, variant):
        return reverse("participants:" + variant[0])

    def payload(self, variant, student=None, positive=True, prepared=True):
        data = {"group": str(self.group.pk), "date": self.DATE.isoformat(), "partial_roster": "1"}
        if student is not None:
            if prepared:
                data[f"prepared_{student.pk}"] = "1"
            data.update(self.status_fields(variant, student, positive))
        return data

    def status_fields(self, variant, student, positive):
        if variant[1] is EliteTripAttendance:
            return {f"status_{student.pk}": (
                EliteTripAttendance.Status.PRESENT if positive else EliteTripAttendance.Status.ABSENT
            )}
        value = "on" if positive else "0"
        data = {f"attended_{student.pk}": value}
        if variant[1] is MeetingAttendance:
            data[f"early_{student.pk}"] = value
        elif variant[1] is CircleAttendance:
            data[f"achieved_{student.pk}"] = value
        return data

    def post_json(self, variant, data, client=None):
        return (client or self.client).post(self.url(variant), data, HTTP_ACCEPT="application/json")

    def records(self, variant):
        return variant[1].objects.filter(**{variant[2]: self.DATE})

    def balances(self, student):
        student.refresh_from_db()
        return (student.points, student.miles, student.purchase_points)

    def shifted_balances(self, before, delta):
        return tuple(value + delta * rate for value, rate in zip(before, (1, 10, 1)))

    def assert_saved(self, response, attendance_ids=(), note_ids=()):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        body = response.json()
        self.assertIs(body["success"], True)
        self.assertEqual(body["saved_ids"], sorted(attendance_ids))
        self.assertEqual(body["saved_note_ids"], sorted(note_ids))
        self.assertRegex(body["saved_at"], r"^\d{2}:\d{2}$")
        self.assertEqual(body["date"], self.DATE.isoformat())
        self.assertEqual(body["group_id"], self.group.pk)
        return body

    def assert_status(self, record, positive):
        if isinstance(record, EliteTripAttendance):
            self.assertEqual(record.status, (
                EliteTripAttendance.Status.PRESENT if positive else EliteTripAttendance.Status.ABSENT
            ))
        else:
            self.assertIs(record.attended, positive)
            if isinstance(record, MeetingAttendance):
                self.assertIs(record.is_early, positive)
            elif isinstance(record, CircleAttendance):
                self.assertIs(record.achieved, positive)

    def test_partial_one_student_never_creates_other_absences_in_all_four_views(self):
        first = self.students[0]
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                before = self.balances(first)
                response = self.post_json(variant, self.payload(variant, first))
                self.assert_saved(response, [first.pk])
                self.assertEqual(set(self.records(variant).values_list("participant_id", flat=True)), {first.pk})
                record = self.records(variant).get(participant=first)
                self.assert_status(record, True)
                self.assertEqual(record.recorded_by, self.supervisor)
                self.assertEqual(self.balances(first), self.shifted_balances(before, variant[3]))
                for untouched in self.students[1:]:
                    self.assertEqual(self.balances(untouched), self.INITIAL_BALANCES)

    def test_partial_without_prepared_marker_ignores_posted_status_and_does_not_write(self):
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                data = self.payload(variant, self.students[0], prepared=False)
                self.assert_saved(self.post_json(variant, data))
                self.assertFalse(self.records(variant).exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(MeetingNote.objects.exists())
        for student in self.students:
            self.assertEqual(self.balances(student), self.INITIAL_BALANCES)

    def test_explicit_prepared_negative_zero_creates_a_record_without_rewards(self):
        first = self.students[0]
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                self.assert_saved(self.post_json(variant, self.payload(variant, first, positive=False)), [first.pk])
                self.assertEqual(self.records(variant).count(), 1)
                self.assert_status(self.records(variant).get(participant=first), False)
                self.assertEqual(self.balances(first), self.INITIAL_BALANCES)
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_get_recorded_is_database_existence_even_for_false_or_absent_status(self):
        first, second, third = self.students
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                empty = self.client.get(self.url(variant), {"group": self.group.pk, "date": self.DATE.isoformat()})
                self.assertEqual(empty.status_code, 200)
                self.assertTrue(all(entry["recorded"] is False for entry in empty.context["roster"]))
                fields = {"status": EliteTripAttendance.Status.ABSENT} if variant[1] is EliteTripAttendance else {"attended": False}
                variant[1].objects.create(participant=first, recorded_by=self.supervisor, **{variant[2]: self.DATE}, **fields)
                variant[1].objects.create(
                    participant=second, recorded_by=self.supervisor,
                    **{variant[2]: self.DATE + datetime.timedelta(days=1)}, **fields,
                )
                if variant[1] is MeetingAttendance:
                    MeetingNote.objects.create(participant=third, meeting_date=self.DATE, text="Note, not attendance")
                page = self.client.get(self.url(variant), {"group": self.group.pk, "date": self.DATE.isoformat()})
                self.assertEqual(page.status_code, 200)
                roster = {entry["participant"].pk: entry for entry in page.context["roster"]}
                self.assertEqual(set(roster), {student.pk for student in self.students})
                self.assertIs(roster[first.pk]["recorded"], True)
                self.assertIs(roster[second.pk]["recorded"], False)
                self.assertIs(roster[third.pk]["recorded"], False)
                if variant[1] is EliteTripAttendance:
                    self.assertEqual(roster[first.pk]["status"], EliteTripAttendance.Status.ABSENT)
                else:
                    self.assertIs(roster[first.pk]["attended"], False)

    def test_repeated_positive_save_is_idempotent_for_records_and_rewards(self):
        first = self.students[0]
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                data = self.payload(variant, first)
                before = self.balances(first)
                self.assert_saved(self.post_json(variant, data), [first.pk])
                record_pk = self.records(variant).get(participant=first).pk
                ledger_count = PointsLedgerEntry.objects.count()
                self.assert_saved(self.post_json(variant, data), [first.pk])
                self.assertEqual(self.records(variant).count(), 1)
                self.assertEqual(self.records(variant).get(participant=first).pk, record_pk)
                self.assertEqual(PointsLedgerEntry.objects.count(), ledger_count)
                self.assertEqual(self.balances(first), self.shifted_balances(before, variant[3]))

    def test_legacy_full_post_still_saves_entire_active_roster_and_redirects(self):
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                data = self.payload(variant, self.students[0], prepared=False)
                del data["partial_roster"]
                response = self.client.post(self.url(variant), data)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], f"{self.url(variant)}?date={self.DATE.isoformat()}&group={self.group.pk}")
                self.assertEqual(set(self.records(variant).values_list("participant_id", flat=True)), {student.pk for student in self.students})
                self.assert_status(self.records(variant).get(participant=self.students[0]), True)
                for omitted in self.students[1:]:
                    self.assert_status(self.records(variant).get(participant=omitted), False)

    def test_saved_positive_is_untouched_when_partial_prepared_marker_is_missing(self):
        first = self.students[0]
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                self.assert_saved(self.post_json(variant, self.payload(variant, first)), [first.pk])
                record = self.records(variant).get(participant=first)
                before = self.balances(first)
                ledger_count = PointsLedgerEntry.objects.count()
                data = self.payload(variant, first, positive=False, prepared=False)
                self.assert_saved(self.post_json(variant, data))
                record.refresh_from_db()
                self.assert_status(record, True)
                self.assertEqual(self.records(variant).count(), 1)
                self.assertEqual(self.balances(first), before)
                self.assertEqual(PointsLedgerEntry.objects.count(), ledger_count)

    def test_note_only_post_acknowledges_note_without_attendance_or_points(self):
        variant = self.VARIANTS[0]
        first = self.students[0]
        data = self.payload(variant, first, prepared=False)
        data[f"note_{first.pk}"] = "  Synthetic mobile note  "
        self.assert_saved(self.post_json(variant, data), note_ids=[first.pk])
        self.assertFalse(MeetingAttendance.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertEqual(self.balances(first), self.INITIAL_BALANCES)
        note = MeetingNote.objects.get(participant=first, meeting_date=self.DATE)
        self.assertEqual(note.text, "Synthetic mobile note")
        self.assertEqual(note.created_by, self.supervisor)
        self.assertEqual(note.updated_by, self.supervisor)

    def test_note_only_update_and_delete_preserve_existing_attendance_and_rewards(self):
        variant = self.VARIANTS[0]
        first = self.students[0]
        self.assert_saved(self.post_json(variant, self.payload(variant, first)), [first.pk])
        note = MeetingNote.objects.create(
            participant=first, meeting_date=self.DATE, text="Old synthetic note",
            created_by=self.general, updated_by=self.general,
        )
        before = self.balances(first)
        ledger_count = PointsLedgerEntry.objects.count()
        for text in ("Changed synthetic note", ""):
            with self.subTest(text=text):
                data = self.payload(variant, first, positive=False, prepared=False)
                data[f"note_{first.pk}"] = text
                self.assert_saved(self.post_json(variant, data), note_ids=[first.pk])
                self.assert_status(MeetingAttendance.objects.get(participant=first, week_start_date=self.DATE), True)
                self.assertEqual(self.balances(first), before)
                self.assertEqual(PointsLedgerEntry.objects.count(), ledger_count)
                if text:
                    note.refresh_from_db()
                    self.assertEqual(note.text, text)
                    self.assertEqual(note.created_by, self.general)
                    self.assertEqual(note.updated_by, self.supervisor)
                else:
                    self.assertFalse(MeetingNote.objects.filter(pk=note.pk).exists())

    def test_combined_save_acknowledges_only_attendance_and_explicit_changed_notes(self):
        variant = self.VARIANTS[0]
        first, second, third = self.students
        untouched = MeetingNote.objects.create(participant=third, meeting_date=self.DATE, text="Preserve omitted note")
        data = self.payload(variant, first)
        data[f"note_{first.pk}"] = "Attendance and note"
        data[f"note_{second.pk}"] = "Note only"
        self.assert_saved(self.post_json(variant, data), [first.pk], [first.pk, second.pk])
        self.assertEqual(set(MeetingAttendance.objects.values_list("participant_id", flat=True)), {first.pk})
        self.assertEqual(MeetingNote.objects.get(participant=second, meeting_date=self.DATE).text, "Note only")
        untouched.refresh_from_db()
        self.assertEqual(untouched.text, "Preserve omitted note")
        self.assertEqual(self.balances(second), self.INITIAL_BALANCES)
        self.assertEqual(self.balances(third), self.INITIAL_BALANCES)
        self.assertFalse(PointsLedgerEntry.objects.exclude(participant=first).exists())

    def test_foreign_inactive_and_wrong_role_ids_are_neither_written_nor_acknowledged(self):
        first = self.students[0]
        forbidden = (self.foreign, self.inactive, self.wrong_role)
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                data = self.payload(variant, first)
                for student in forbidden:
                    data[f"prepared_{student.pk}"] = "1"
                    data.update(self.status_fields(variant, student, True))
                    data[f"note_{student.pk}"] = "Must never be saved"
                if variant[1] is MeetingAttendance:
                    data[f"note_{first.pk}"] = "Allowed note"
                self.assert_saved(self.post_json(variant, data), [first.pk], [first.pk] if variant[1] is MeetingAttendance else [])
                self.assertEqual(set(self.records(variant).values_list("participant_id", flat=True)), {first.pk})
                self.assertFalse(MeetingNote.objects.filter(participant__in=forbidden).exists())
                self.assertFalse(PointsLedgerEntry.objects.filter(participant__in=forbidden).exists())
                for student in forbidden:
                    self.assertEqual(self.balances(student), self.INITIAL_BALANCES)

    def test_invalid_or_duplicate_partial_marker_is_400_before_any_write(self):
        for variant in self.VARIANTS:
            for value in ("0", "", "true", ["1", "1"], ["0", "1"]):
                with self.subTest(route=variant[0], value=value):
                    data = self.payload(variant, self.students[0])
                    data["partial_roster"] = value
                    data[f"note_{self.students[0].pk}"] = "Do not commit"
                    response = self.post_json(variant, data)
                    self.assertEqual(response.status_code, 400)
                    self.assertIs(response.json()["success"], False)
                    self.assertFalse(self.records(variant).exists())
                    self.assertFalse(MeetingNote.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_invalid_or_duplicate_prepared_marker_is_400_before_any_write(self):
        first, second = self.students[:2]
        for variant in self.VARIANTS:
            for value in ("0", "", "on", ["1", "1"], ["1", "0"]):
                with self.subTest(route=variant[0], value=value):
                    data = self.payload(variant, first)
                    data[f"prepared_{second.pk}"] = value
                    data.update(self.status_fields(variant, second, True))
                    data[f"note_{first.pk}"] = "Do not commit"
                    response = self.post_json(variant, data)
                    self.assertEqual(response.status_code, 400)
                    self.assertIs(response.json()["success"], False)
                    self.assertFalse(self.records(variant).exists())
                    self.assertFalse(MeetingNote.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_elite_prepared_without_status_is_400_and_does_not_save_valid_sibling(self):
        variant = self.VARIANTS[3]
        data = self.payload(variant, self.students[0])
        data[f"prepared_{self.students[1].pk}"] = "1"
        response = self.post_json(variant, data)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EliteTripAttendance.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_elite_invalid_or_duplicate_status_is_400_without_partial_commit(self):
        variant = self.VARIANTS[3]
        first, second = self.students[:2]
        for value in ("tampered", "", ["present", "absent"], ["present", "present"]):
            with self.subTest(value=value):
                data = self.payload(variant, first)
                data[f"prepared_{second.pk}"] = "1"
                data[f"status_{second.pk}"] = value
                response = self.post_json(variant, data)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(EliteTripAttendance.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_all_four_elite_statuses_save_without_points_or_ledger(self):
        variant = self.VARIANTS[3]
        first = self.students[0]
        record_pk = None
        for status in EliteTripAttendance.Status.values:
            with self.subTest(status=status):
                data = self.payload(variant, first)
                data[f"status_{first.pk}"] = status
                with patch("participants.views.apply_points_delta") as apply_points:
                    self.assert_saved(self.post_json(variant, data), [first.pk])
                apply_points.assert_not_called()
                record = EliteTripAttendance.objects.get(participant=first, date=self.DATE)
                self.assertEqual(record.status, status)
                if record_pk is not None:
                    self.assertEqual(record.pk, record_pk)
                record_pk = record.pk
                self.assertEqual(EliteTripAttendance.objects.count(), 1)
                self.assertFalse(PointsLedgerEntry.objects.exists())
                for student in self.students:
                    self.assertEqual(self.balances(student), self.INITIAL_BALANCES)

    def test_csrf_enforcement_rejects_json_post_without_token_in_all_four_views(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.supervisor)
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                response = self.post_json(variant, self.payload(variant, self.students[0]), client=csrf_client)
                self.assertEqual(response.status_code, 403)
                self.assertFalse(self.records(variant).exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(MeetingNote.objects.exists())

    def test_missing_group_for_general_supervisor_returns_json_400_without_write(self):
        self.client.force_login(self.general)
        for variant in self.VARIANTS:
            with self.subTest(route=variant[0]):
                data = self.payload(variant, self.students[0])
                del data["group"]
                response = self.post_json(variant, data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
                self.assertIs(response.json()["success"], False)
                self.assertFalse(self.records(variant).exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(MeetingNote.objects.exists())

    def test_injected_points_failure_atomically_rolls_back_and_never_reports_success(self):
        first, second = self.students[:2]
        for variant in self.VARIANTS[:3]:
            with self.subTest(route=variant[0]):
                data = self.payload(variant, first)
                data[f"prepared_{second.pk}"] = "1"
                data.update(self.status_fields(variant, second, True))
                data[f"note_{first.pk}"] = "Rollback this note too"
                calls = []

                def write_then_fail(*args, **kwargs):
                    calls.append(args[0].pk)
                    if len(calls) == 2:
                        raise RuntimeError("synthetic mobile save failure")
                    return apply_points_delta(*args, **kwargs)

                with patch("participants.views.apply_points_delta", side_effect=write_then_fail), patch(
                    "participants.attendance_transport.roster_success"
                ) as success:
                    with self.assertRaisesMessage(RuntimeError, "synthetic mobile save failure"):
                        self.post_json(variant, data)
                    success.assert_not_called()
                self.assertEqual(calls, sorted([first.pk, second.pk]))
                self.assertFalse(self.records(variant).exists())
                self.assertFalse(PointsLedgerEntry.objects.exists())
                self.assertFalse(MeetingNote.objects.exists())
                for student in self.students:
                    self.assertEqual(self.balances(student), self.INITIAL_BALANCES)
                page = self.client.get(self.url(variant), {"group": self.group.pk, "date": self.DATE.isoformat()})
                self.assertEqual(page.status_code, 200)
                self.assertFalse(any(message.level_tag == "success" for message in get_messages(page.wsgi_request)))

    def test_negative_review_reverses_exact_points_miles_and_purchase_points_once(self):
        first = self.students[0]
        for variant in self.VARIANTS[:3]:
            with self.subTest(route=variant[0]):
                before = self.balances(first)
                self.assert_saved(self.post_json(variant, self.payload(variant, first)), [first.pk])
                self.assertEqual(self.balances(first), self.shifted_balances(before, variant[3]))
                negative = self.payload(variant, first, positive=False)
                self.assert_saved(self.post_json(variant, negative), [first.pk])
                self.assertEqual(self.balances(first), before)
                self.assert_status(self.records(variant).get(participant=first), False)
                entries = PointsLedgerEntry.objects.filter(participant=first, source=variant[4]).order_by("pk")
                self.assertEqual(list(entries.values_list("points_delta", flat=True)), [variant[3], -variant[3]])
                self.assertTrue(all(entry.event_date == self.DATE and entry.granted_by_id == self.supervisor.pk for entry in entries))
                ledger_count = PointsLedgerEntry.objects.count()
                self.assert_saved(self.post_json(variant, negative), [first.pk])
                self.assertEqual(self.balances(first), before)
                self.assertEqual(PointsLedgerEntry.objects.count(), ledger_count)
                self.assertEqual(self.records(variant).count(), 1)
