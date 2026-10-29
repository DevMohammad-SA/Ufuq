"""Integration regressions for event-dated points and student-facing ledger privacy."""
import datetime

from django.contrib import messages
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import (
    Group,
    Participant,
    PointsLedgerEntry,
    TaskSubmission,
    WeeklyTask,
)
from .reporting import build_student_report


UTC = datetime.timezone.utc


@override_settings(TIME_ZONE="Asia/Riyadh")
class PointsExperienceTests(TestCase):
    """Exercise the real POSTs and the two very different ledger audiences."""

    def setUp(self):
        self.group_supervisor = User.objects.create_user(
            username="points-experience-group",
            password="test-only-password",
            role=Role.GROUP_SUPERVISOR,
            must_set_password=False,
        )
        self.general_supervisor = User.objects.create_user(
            username="points-experience-general",
            password="test-only-password",
            role=Role.GENERAL_SUPERVISOR,
            must_set_password=False,
        )
        self.group = Group.objects.create(name="بيئة تجربة النقاط")
        self.group.supervisor.add(self.group_supervisor)
        self.other_group = Group.objects.create(name="بيئة خصوصية أخرى")

        self.student = self.make_participant(
            "6100000001", "طالب تجربة النقاط", self.group
        )
        self.classmate = self.make_participant(
            "6100000002", "طالب آخر في البيئة", self.group
        )
        self.privacy_other = self.make_participant(
            "6100000003", "طالب خصوصية آخر", self.other_group
        )
        self.only_non_positive = self.make_participant(
            "6100000004", "طالب بلا إضافات", self.other_group
        )

    @staticmethod
    def make_participant(national_id, full_name, group):
        user = User.objects.create_user(
            national_id=national_id,
            full_name=full_name,
            password="test-only-password",
            role=Role.PARTICIPANT,
            # ForcePasswordSetupMiddleware must not hide the dashboard under test.
            must_set_password=False,
        )
        return Participant.objects.create(
            user=user,
            group=group,
            academic_stage="grade_7",
        )

    def assert_logged_today_with_event_date(self, entry, event_date):
        """The business date is independent of the actual audit-write timestamp."""
        self.assertEqual(entry.event_date, event_date)
        self.assertEqual(timezone.localdate(entry.created_at), timezone.localdate())

    def follow_attendance_success(self, response, expected_url, event_date):
        """Follow the real redirect and prove the flashed success is actually rendered."""
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], expected_url)

        follow = self.client.get(response["Location"])
        self.assertEqual(follow.status_code, 200)
        delivered = [
            str(message)
            for message in get_messages(follow.wsgi_request)
            if message.level == messages.SUCCESS
        ]
        self.assertTrue(delivered)
        visible_date = event_date.strftime("%Y/%m/%d")
        self.assertTrue(
            any(visible_date in message for message in delivered),
            "رسالة النجاح يجب أن تذكر تاريخ الكشف المحفوظ.",
        )
        self.assertContains(follow, 'role="status"')
        for message in delivered:
            self.assertContains(follow, message)
        return follow

    def test_each_attendance_post_uses_selected_event_date_shows_success_and_is_idempotent(self):
        """Meeting, circle, and activity POSTs carry the chosen historic date to the ledger."""
        self.client.force_login(self.group_supervisor)
        cases = [
            (
                "meeting",
                reverse("participants:supervisor_dashboard"),
                datetime.date(2020, 1, 15),
                {
                    f"attended_{self.student.pk}": "on",
                    f"early_{self.student.pk}": "on",
                },
                PointsLedgerEntry.Source.MEETING_ATTENDANCE,
                10,
            ),
            (
                "circle",
                reverse("participants:quran_circle_attendance"),
                datetime.date(2020, 2, 16),
                {
                    f"attended_{self.student.pk}": "on",
                    f"achieved_{self.student.pk}": "on",
                },
                PointsLedgerEntry.Source.QURAN_CIRCLE,
                5,
            ),
            (
                "activity",
                reverse("participants:weekly_activity_attendance"),
                datetime.date(2020, 3, 17),
                {f"attended_{self.student.pk}": "on"},
                PointsLedgerEntry.Source.WEEKLY_ACTIVITY,
                10,
            ),
        ]

        for name, url, selected_date, roster, source, points in cases:
            with self.subTest(attendance=name):
                payload = {
                    "date": selected_date.isoformat(),
                    "group": self.group.pk,
                    **roster,
                }
                expected_url = f"{url}?date={selected_date.isoformat()}&group={self.group.pk}"

                response = self.client.post(url, payload)
                self.follow_attendance_success(response, expected_url, selected_date)

                entry = PointsLedgerEntry.objects.get(
                    participant=self.student,
                    source=source,
                    points_delta=points,
                )
                self.assert_logged_today_with_event_date(entry, selected_date)

                # Re-saving an unchanged roster must not manufacture another audit row.
                repeated = self.client.post(url, payload)
                self.follow_attendance_success(repeated, expected_url, selected_date)
                self.assertEqual(
                    PointsLedgerEntry.objects.filter(
                        participant=self.student,
                        source=source,
                    ).count(),
                    1,
                )

    def test_historic_meeting_correction_is_negative_on_same_event_day_but_logged_today(self):
        self.client.force_login(self.group_supervisor)
        selected_date = datetime.date(2020, 4, 18)
        url = reverse("participants:supervisor_dashboard")
        expected_url = f"{url}?date={selected_date.isoformat()}&group={self.group.pk}"

        first = self.client.post(
            url,
            {
                "date": selected_date.isoformat(),
                "group": self.group.pk,
                f"attended_{self.student.pk}": "on",
                f"early_{self.student.pk}": "on",
            },
        )
        self.follow_attendance_success(first, expected_url, selected_date)

        correction = self.client.post(
            url,
            {
                "date": selected_date.isoformat(),
                "group": self.group.pk,
                f"attended_{self.student.pk}": "on",
            },
        )
        self.follow_attendance_success(correction, expected_url, selected_date)

        entries = list(
            PointsLedgerEntry.objects.filter(
                participant=self.student,
                source=PointsLedgerEntry.Source.MEETING_ATTENDANCE,
            ).order_by("pk")
        )
        self.assertEqual([entry.points_delta for entry in entries], [10, -2])
        for entry in entries:
            self.assert_logged_today_with_event_date(entry, selected_date)
            self.assertNotEqual(entry.event_date, timezone.localdate(entry.created_at))

    def test_no_success_message_when_group_is_missing_or_note_is_too_long(self):
        meeting_url = reverse("participants:supervisor_dashboard")
        selected_date = datetime.date(2020, 5, 19)

        # General supervisors must select a group; this is a non-save, not success.
        self.client.force_login(self.general_supervisor)
        no_group = self.client.post(meeting_url, {"date": selected_date.isoformat()})
        self.assertEqual(no_group.status_code, 200)
        self.assertFalse(
            any(message.level == messages.SUCCESS for message in get_messages(no_group.wsgi_request))
        )
        self.assertFalse(PointsLedgerEntry.objects.exists())

        # A rejected private note must also not look like a successful roster save.
        self.client.force_login(self.group_supervisor)
        note_error = self.client.post(
            meeting_url,
            {
                "date": selected_date.isoformat(),
                "group": self.group.pk,
                "action": "save_note",
                "participant_id": self.student.pk,
                "note_text": "س" * 2001,
            },
        )
        self.assertEqual(note_error.status_code, 400)
        self.assertFalse(
            any(message.level == messages.SUCCESS for message in get_messages(note_error.wsgi_request))
        )
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_task_acceptance_uses_first_submission_timestamp_in_riyadh_at_utc_boundary(self):
        self.client.force_login(self.general_supervisor)
        review_url = reverse("participants:weekly_task_review")
        cases = [
            (
                "قبل منتصف الرياض",
                datetime.datetime(2026, 5, 1, 20, 59, tzinfo=UTC),
                datetime.date(2026, 5, 1),
                False,
                10,
            ),
            (
                "بعد منتصف الرياض",
                datetime.datetime(2026, 5, 1, 21, 0, tzinfo=UTC),
                datetime.date(2026, 5, 2),
                True,
                12,
            ),
        ]

        for index, (title, submitted_at, expected_event_date, featured, points) in enumerate(cases):
            with self.subTest(submission=title):
                task = WeeklyTask.objects.create(
                    title=f"مهمة توقيت {index}",
                    description="اختبار تاريخ الحدث",
                    due_date=timezone.localdate() + datetime.timedelta(days=7),
                    allowed_formats="text",
                    is_active=True,
                    created_by=self.general_supervisor,
                )
                submission = TaskSubmission.objects.create(
                    task=task,
                    participant=self.student,
                    text_content="تسليم اختبار",
                )
                TaskSubmission.objects.filter(pk=submission.pk).update(
                    submitted_at=submitted_at
                )

                response = self.client.post(
                    review_url,
                    {
                        "action": "accept",
                        "submission_id": submission.pk,
                        **({"is_featured": "on"} if featured else {}),
                    },
                )
                self.assertEqual(response.status_code, 302)

                submission.refresh_from_db()
                self.assertEqual(submission.submitted_at, submitted_at)
                self.assertTrue(submission.points_awarded)
                self.assertEqual(submission.is_featured, featured)
                entry = PointsLedgerEntry.objects.get(
                    participant=self.student,
                    source=PointsLedgerEntry.Source.WEEKLY_TASK,
                    description__contains=task.title,
                )
                self.assertEqual(entry.points_delta, points)
                self.assert_logged_today_with_event_date(entry, expected_event_date)

    def test_manual_extra_points_uses_today_as_its_event_date(self):
        self.client.force_login(self.general_supervisor)
        response = self.client.post(
            reverse("participants:extra_points"),
            {
                "participant": self.student.pk,
                "points": 7,
                "reason": "نقاط يدوية لتاريخ اليوم",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = PointsLedgerEntry.objects.get(description="نقاط يدوية لتاريخ اليوم")
        self.assertEqual(entry.source, PointsLedgerEntry.Source.EXTRA)
        self.assert_logged_today_with_event_date(entry, timezone.localdate())

    def test_student_dashboard_exposes_only_latest_fifty_positive_own_entries(self):
        base = timezone.make_aware(
            datetime.datetime(2020, 1, 1, 8, 0), timezone.get_current_timezone()
        )
        for index in range(51):
            entry = PointsLedgerEntry.objects.create(
                participant=self.student,
                points_delta=1,
                source=PointsLedgerEntry.Source.EXTRA,
                description=f"إضافة موجبة {index}",
                event_date=datetime.date(2020, 1, 1),
                granted_by=self.general_supervisor,
            )
            PointsLedgerEntry.objects.filter(pk=entry.pk).update(
                created_at=base + datetime.timedelta(minutes=index)
            )

        own_negative_description = "خصم خاص لا يظهر للطالب"
        own_zero_description = "حركة صفرية لا تظهر للطالب"
        other_negative_description = "خصم طالب آخر لا يظهر للطالب"
        PointsLedgerEntry.objects.create(
            participant=self.student,
            points_delta=-2,
            source=PointsLedgerEntry.Source.EXTRA,
            description=own_negative_description,
            event_date=timezone.localdate(),
            granted_by=self.general_supervisor,
        )
        PointsLedgerEntry.objects.create(
            participant=self.student,
            points_delta=0,
            source=PointsLedgerEntry.Source.EXTRA,
            description=own_zero_description,
            event_date=timezone.localdate(),
            granted_by=self.general_supervisor,
        )
        PointsLedgerEntry.objects.create(
            participant=self.privacy_other,
            points_delta=-3,
            source=PointsLedgerEntry.Source.EXTRA,
            description=other_negative_description,
            event_date=timezone.localdate(),
            granted_by=self.general_supervisor,
        )

        # This is a participant GET, not a context-only unit invocation.
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("participants:dashboard"))
        self.assertEqual(response.status_code, 200)
        displayed = list(response.context["points_ledger"])
        self.assertEqual(len(displayed), 50)
        self.assertTrue(all(entry.points_delta > 0 for entry in displayed))
        self.assertEqual(
            [entry.description for entry in displayed],
            [f"إضافة موجبة {index}" for index in range(50, 0, -1)],
        )

        self.assertContains(response, "النقاط المضافة لك")
        self.assertNotContains(response, "إضافة موجبة 0")
        self.assertNotContains(response, own_negative_description)
        self.assertNotContains(response, own_zero_description)
        self.assertNotContains(response, other_negative_description)

    def test_student_dashboard_has_a_real_empty_state_when_only_zero_or_negative_entries_exist(self):
        negative_description = "خصم الطالب بلا إضافات"
        zero_description = "صفر الطالب بلا إضافات"
        PointsLedgerEntry.objects.create(
            participant=self.only_non_positive,
            points_delta=-1,
            source=PointsLedgerEntry.Source.EXTRA,
            description=negative_description,
            event_date=timezone.localdate(),
            granted_by=self.general_supervisor,
        )
        PointsLedgerEntry.objects.create(
            participant=self.only_non_positive,
            points_delta=0,
            source=PointsLedgerEntry.Source.EXTRA,
            description=zero_description,
            event_date=timezone.localdate(),
            granted_by=self.general_supervisor,
        )

        self.client.force_login(self.only_non_positive.user)
        response = self.client.get(reverse("participants:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["points_ledger"]), [])
        self.assertContains(response, "النقاط المضافة لك")
        dashboard_ledger = response.content.decode().split("النقاط المضافة لك", 1)[1]
        self.assertIn('class="empty-note"', dashboard_ledger)
        self.assertNotContains(response, negative_description)
        self.assertNotContains(response, zero_description)

    def test_supervisor_ledger_keeps_negative_rows_and_shows_a_legacy_unknown_event_date(self):
        legacy_description = "سجل قديم بلا تاريخ حدث"
        negative_description = "خصم إداري يجب أن يبقى مرئيًا"
        legacy = PointsLedgerEntry.objects.create(
            participant=self.student,
            points_delta=4,
            source=PointsLedgerEntry.Source.EXTRA,
            description=legacy_description,
            # Existing rows predate the field and must stay unknown, never inferred.
            event_date=None,
            granted_by=self.general_supervisor,
        )
        PointsLedgerEntry.objects.filter(pk=legacy.pk).update(
            created_at=datetime.datetime(2019, 12, 31, 21, 0, tzinfo=UTC)
        )
        negative = PointsLedgerEntry.objects.create(
            participant=self.student,
            points_delta=-3,
            source=PointsLedgerEntry.Source.EXTRA,
            description=negative_description,
            event_date=datetime.date(2020, 1, 2),
            granted_by=self.general_supervisor,
        )
        legacy.refresh_from_db()
        self.assertIsNone(legacy.event_date)

        self.client.force_login(self.group_supervisor)
        ledger_response = self.client.get(reverse("participants:points_ledger"))
        self.assertEqual(ledger_response.status_code, 200)
        visible_entry_ids = [entry.pk for entry in ledger_response.context["entries"]]
        self.assertIn(legacy.pk, visible_entry_ids)
        self.assertIn(negative.pk, visible_entry_ids)
        self.assertContains(ledger_response, "تاريخ تسجيل النقاط")
        self.assertContains(ledger_response, "تاريخ الحدث")
        self.assertContains(ledger_response, "غير مسجل")
        self.assertContains(ledger_response, negative_description)

        self.client.force_login(self.general_supervisor)
        detail_response = self.client.get(
            reverse("participants:student_detail", kwargs={"pk": self.student.pk})
        )
        self.assertEqual(detail_response.status_code, 200)
        ledger_table = next(
            table for table in detail_response.context["tables"] if table["anchor"] == "ledger"
        )
        self.assertEqual(
            ledger_table["columns"],
            [
                "الوصف",
                "المصدر",
                "التغيير",
                "تاريخ تسجيل النقاط",
                "تاريخ الحدث",
                "مُنح بواسطة",
            ],
        )
        legacy_row = next(row for row in ledger_table["rows"] if row[0] == legacy_description)
        self.assertEqual(legacy_row[4], "غير مسجل")

    def test_report_ledger_filters_remain_based_on_created_at_not_event_date(self):
        event_date = datetime.date(2020, 1, 31)
        recorded_at = datetime.datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
        entry = PointsLedgerEntry.objects.create(
            participant=self.student,
            points_delta=9,
            source=PointsLedgerEntry.Source.EXTRA,
            description="فلاتر التقرير لا تتبع تاريخ الحدث",
            event_date=event_date,
            granted_by=self.general_supervisor,
        )
        PointsLedgerEntry.objects.filter(pk=entry.pk).update(created_at=recorded_at)

        recorded_period = build_student_report(
            self.general_supervisor,
            {
                "q": self.student.user.national_id,
                "start_date": datetime.date(2026, 10, 10),
                "end_date": datetime.date(2026, 10, 10),
            },
        )
        self.assertEqual(recorded_period[0]["net_points"], 9)

        event_period = build_student_report(
            self.general_supervisor,
            {
                "q": self.student.user.national_id,
                "start_date": event_date,
                "end_date": event_date,
            },
        )
        self.assertEqual(event_period[0]["net_points"], 0)
