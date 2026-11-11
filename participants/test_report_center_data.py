"""Focused report-centre engine tests. Use config.test_settings only.

Fixtures are synthetic; the report builder must never write domain data.
These tests are intentionally separate from endpoint/export integration tests.
"""
import datetime
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import SafeData

from accounts.models import Role, User
from .models import (
    CircleAttendance, Group, MeetingAttendance, MeetingNote, Participant,
    PointsLedgerEntry, TaskSubmission, WeeklyActivityAttendance, WeeklyTask,
)
from .report_center import (
    REPORT_CHOICES, RIYADH, ReportCenterFilterForm, build_filter_labels, build_reports,
)

UTC = datetime.timezone.utc
DAY_2 = datetime.date(2026, 10, 2)
DAY_3 = datetime.date(2026, 10, 3)


@override_settings(TIME_ZONE="Asia/Riyadh")
class ReportCenterDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username="centre-general", role=Role.GENERAL_SUPERVISOR, password="test-only")
        cls.group_supervisor = User.objects.create_user(username="centre-group", role=Role.GROUP_SUPERVISOR, password="test-only")
        cls.admin = User.objects.create_user(username="centre-admin", role=Role.SUPERADMIN, password="test-only")
        cls.disabled_general = User.objects.create_user(username="centre-disabled", role=Role.GENERAL_SUPERVISOR, is_active=False, password="test-only")
        cls.group = Group.objects.create(name="بيئة أولى")
        cls.other_group = Group.objects.create(name="بيئة ثانية")
        cls.empty_group = Group.objects.create(name="بيئة فارغة")
        cls.group.supervisor.add(cls.group_supervisor)
        cls.student = cls.make_student("7100000001", "الطالب الأول", cls.group, points=123, phone="0501234567", guardian_phone="0557654321")
        cls.quiet = cls.make_student("7100000002", "طالب دون نشاط حلقة", cls.group)
        cls.inactive = cls.make_student("7100000003", "طالب معطل", cls.group, active=False, stage="grade_8")
        cls.other = cls.make_student("7100000004", "طالب البيئة الأخرى", cls.other_group)
        cls.ungrouped = cls.make_student("7100000005", "طالب بلا بيئة", None)
        # A malformed non-participant profile must not enter student reporting.
        cls.nonparticipant = Participant.objects.create(user=cls.admin, group=cls.group, academic_stage="grade_7")
        CircleAttendance.objects.create(participant=cls.student, date=DAY_2, attended=True, achieved=False)
        CircleAttendance.objects.create(participant=cls.student, date=DAY_3, attended=False, achieved=True)
        CircleAttendance.objects.create(participant=cls.student, date=datetime.date(2026, 10, 4), attended=True, achieved=True)
        CircleAttendance.objects.create(participant=cls.other, date=DAY_3, attended=True, achieved=True)
        # Evidence on DAY_2 is entirely outside the chosen participant's status:
        CircleAttendance.objects.create(participant=cls.inactive, date=DAY_2, attended=True)
        MeetingAttendance.objects.create(participant=cls.student, week_start_date=DAY_2, attended=False, is_early=True)
        MeetingAttendance.objects.create(participant=cls.quiet, week_start_date=DAY_2, attended=True, is_early=False)
        WeeklyActivityAttendance.objects.create(participant=cls.student, date=DAY_3, attended=True)
        WeeklyActivityAttendance.objects.create(participant=cls.quiet, date=DAY_2, attended=False)
        specs = [
            ("قبول مميز", DAY_3, TaskSubmission.Status.ACCEPTED, True, datetime.datetime(2026, 10, 1, 21, tzinfo=UTC)),
            ("معلق", DAY_3, TaskSubmission.Status.PENDING, False, datetime.datetime(2026, 10, 3, 20, 59, 59, 999999, tzinfo=UTC)),
            ("معاد خارج تاريخ التسليم", DAY_3, TaskSubmission.Status.REVISION_REQUIRED, False, datetime.datetime(2026, 10, 3, 21, tzinfo=UTC)),
            ("مرفوض بعلم قديم", DAY_3, TaskSubmission.Status.REJECTED, True, datetime.datetime(2026, 10, 2, 10, tzinfo=UTC)),
            ("موعد خارج الفترة", datetime.date(2026, 10, 15), TaskSubmission.Status.ACCEPTED, False, datetime.datetime(2026, 10, 2, 10, tzinfo=UTC)),
        ]
        cls.tasks, cls.submissions = [], []
        for title, due, status, featured, stamp in specs:
            task = WeeklyTask.objects.create(title=title, description="بيانات مصطنعة", due_date=due, allowed_formats="text")
            submission = TaskSubmission.objects.create(
                task=task, participant=cls.student, text_content="نص خاص لا يصدر", status=status, is_featured=featured,
                rejection_reason="سبب خاص للاختبار", revision_note="ملاحظة تعديل خاصة للاختبار",
            )
            TaskSubmission.objects.filter(pk=submission.pk).update(submitted_at=stamp)
            cls.tasks.append(task)
            cls.submissions.append(submission)
        ledger_specs = [
            (10, DAY_2, DAY_2, PointsLedgerEntry.Source.EXTRA),
            (-4, DAY_2, DAY_3, PointsLedgerEntry.Source.EXTRA),
            (10, None, DAY_3, PointsLedgerEntry.Source.EXTRA),
            (30, DAY_3, datetime.date(2026, 10, 4), PointsLedgerEntry.Source.WEEKLY_TASK),
            (5, datetime.date(2026, 10, 1), DAY_2, PointsLedgerEntry.Source.QURAN_CIRCLE),
            (0, DAY_2, DAY_2, PointsLedgerEntry.Source.EXTRA),
        ]
        for delta, event_day, recorded_day, source in ledger_specs:
            entry = PointsLedgerEntry.objects.create(participant=cls.student, points_delta=delta, source=source, event_date=event_day, description="وصف دفتر خاص للاختبار")
            PointsLedgerEntry.objects.filter(pk=entry.pk).update(created_at=datetime.datetime.combine(recorded_day, datetime.time(10), UTC))
        MeetingNote.objects.create(participant=cls.student, meeting_date=DAY_2, text="ملاحظة لقاء خاصة للاختبار")

    @classmethod
    def make_student(cls, identity, name, group, *, active=True, stage="grade_7", **kwargs):
        user = User.objects.create_user(national_id=identity, full_name=name, role=Role.PARTICIPANT, is_active=active, password="test-only")
        return Participant.objects.create(user=user, group=group, academic_stage=stage, **kwargs)

    def params(self, **extra):
        params = {"period": "custom", "start_date": "2026-10-02", "end_date": "2026-10-03",
                  "account_status": "active", "detail": "summary", "points_basis": "recorded",
                  "reports": ["summary"]}
        params.update(extra)
        return params

    def filters(self, **extra):
        form = ReportCenterFilterForm(self.params(**extra), user=self.general)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return form.cleaned_data

    def report(self, key, **extra):
        return build_reports(self.general, self.filters(reports=[key], **extra))[0]

    @staticmethod
    def section(report, title):
        return next(section for section in report["sections"] if section["title"] == title)

    @staticmethod
    def metric(report, label):
        return next(metric["value"] for metric in report["metrics"] if metric["label"] == label)

    def test_empty_get_has_bound_valid_safe_defaults_and_riyadh_month(self):
        with mock.patch("participants.report_center.timezone.now", return_value=datetime.datetime(2026, 11, 6, 22, tzinfo=UTC)):
            for params in (None, {}, QueryDict(""), QueryDict("page=2")):
                form = ReportCenterFilterForm(params, user=self.general)
                self.assertTrue(form.is_bound)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["reports"], ["summary"])
                self.assertEqual(form.cleaned_data["period"], "month")
                self.assertEqual(form.cleaned_data["account_status"], "active")
                self.assertEqual(form.cleaned_data["detail"], "summary")
                self.assertEqual(form.cleaned_data["points_basis"], "recorded")
                self.assertFalse(form.cleaned_data["include_private"])
                self.assertEqual(form.cleaned_data["start_date"], datetime.date(2026, 11, 1))
                self.assertEqual(form.cleaned_data["end_date"], datetime.date(2026, 11, 7))

    def test_week_begins_sunday_in_riyadh_and_all_clears_stale_dates(self):
        for stamp, expected in [
            (datetime.datetime(2026, 11, 7, 22, tzinfo=UTC), datetime.date(2026, 11, 8)),
            (datetime.datetime(2026, 11, 6, 22, tzinfo=UTC), datetime.date(2026, 11, 1)),
        ]:
            with self.subTest(stamp=stamp), mock.patch("participants.report_center.timezone.now", return_value=stamp):
                self.assertEqual(self.filters(period="week")["start_date"], expected)
        filters = self.filters(period="all")
        self.assertIsNone(filters["start_date"])
        self.assertIsNone(filters["end_date"])

    def test_custom_requires_both_iso_days_minimum_and_nonreversed_order(self):
        invalid = [
            {"start_date": ""}, {"end_date": ""}, {"start_date": "2026-10-04"},
            {"start_date": "0001-01-01"}, {"end_date": "2026-02-30"},
            {"start_date": "2026-1-02"}, {"end_date": "03/10/2026"},
        ]
        for extra in invalid:
            with self.subTest(extra=extra):
                form = ReportCenterFilterForm(self.params(**extra), user=self.general)
                self.assertFalse(form.is_valid())
                self.assertTrue(form.errors)
        self.assertEqual(self.filters(start_date="2026-10-03")["start_date"], DAY_3)
        self.assertEqual(self.filters(start_date="1000-01-01", end_date="9999-12-31")["end_date"], datetime.date(9999, 12, 31))

    def test_reports_are_checkbox_multiple_choice_with_stable_deduplication(self):
        from django import forms
        self.assertIsInstance(ReportCenterFilterForm(user=self.general).fields["reports"].widget, forms.CheckboxSelectMultiple)
        filters = self.filters(reports=["groups", "summary", "groups", "attendance", "summary"])
        self.assertEqual(filters["reports"], ["groups", "summary", "attendance"])
        self.assertEqual([report["key"] for report in build_reports(self.general, filters)], filters["reports"])
        for values in ([], ["summary", "unknown"]):
            form = ReportCenterFilterForm(self.params(reports=values), user=self.general)
            self.assertFalse(form.is_valid())
            self.assertIn("reports", form.errors)

    def test_unknown_values_and_forged_ids_fail_with_arabic_errors(self):
        for field, value in [
            ("period", "never"), ("account_status", "disabled"), ("detail", "everything"),
            ("points_basis", "auto"), ("academic_stage", "grade_99"),
            ("group", "bad"), ("participant", "999999999"), ("task", "bad"),
            ("include_private", "unknown"),
        ]:
            with self.subTest(field=field):
                form = ReportCenterFilterForm(self.params(**{field: value}), user=self.general)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
                self.assertRegex(str(form.errors[field]), r"[\u0600-\u06ff]")

    def test_participant_choices_include_inactive_names_only_and_role_scope(self):
        field = ReportCenterFilterForm(user=self.general).fields["participant"]
        self.assertIn(self.inactive.pk, list(field.queryset.values_list("pk", flat=True)))
        self.assertNotIn(self.nonparticipant.pk, list(field.queryset.values_list("pk", flat=True)))
        self.assertEqual(field.label_from_instance(self.student), self.student.user.full_name)
        self.assertNotIn(self.student.user.national_id, field.label_from_instance(self.student))
        form = ReportCenterFilterForm(self.params(group=self.other_group.pk), user=self.group_supervisor)
        self.assertFalse(form.is_valid())
        self.assertIn("group", form.errors)

    def test_participant_group_stage_and_status_must_be_consistent(self):
        for extra in [
            {"participant": self.student.pk, "group": self.other_group.pk},
            {"participant": self.student.pk, "academic_stage": "grade_8"},
            {"participant": self.inactive.pk},
        ]:
            form = ReportCenterFilterForm(self.params(**extra), user=self.general)
            self.assertFalse(form.is_valid())
            self.assertIn("participant", form.errors)
        self.assertEqual(self.filters(participant=self.inactive.pk, account_status="inactive")["participant"].pk, self.inactive.pk)
        self.assertEqual(self.filters(participant=self.student.pk, account_status="all")["participant"].pk, self.student.pk)

    def test_task_optional_must_have_due_date_inside_inclusive_period(self):
        self.assertEqual(self.filters(task=self.tasks[0].pk)["task"].pk, self.tasks[0].pk)
        form = ReportCenterFilterForm(self.params(task=self.tasks[4].pk), user=self.general)
        self.assertFalse(form.is_valid())
        self.assertIn("task", form.errors)
        self.assertEqual(self.filters(task=self.tasks[4].pk, period="all")["task"].pk, self.tasks[4].pk)

    def test_engine_denies_legacy_ungranted_roles_after_one_fresh_policy_read(self):
        from django.contrib.auth.models import AnonymousUser
        for user in (self.group_supervisor, self.admin, self.disabled_general, self.student.user, AnonymousUser()):
            expected = 1 if user.is_authenticated else 0
            with self.subTest(user=user), self.assertNumQueries(expected), self.assertRaises(PermissionDenied):
                build_reports(user, {"reports": ["summary"]})

    def test_all_cohort_filters_apply_and_null_group_is_retained(self):
        report = self.report("summary")
        self.assertEqual(self.metric(report, "الطلاب في النطاق الحالي"), 4)
        self.assertEqual(self.metric(self.report("summary", account_status="all"), "الطلاب في النطاق الحالي"), 5)
        self.assertEqual(self.metric(self.report("summary", account_status="inactive"), "الطلاب في النطاق الحالي"), 1)
        self.assertEqual(self.metric(self.report("summary", group=self.group.pk), "الطلاب في النطاق الحالي"), 2)
        self.assertEqual(self.metric(self.report("summary", academic_stage="grade_8", account_status="all"), "الطلاب في النطاق الحالي"), 1)
        rows = self.section(self.report("students"), "ملخص الطلاب")["rows"]
        self.assertIn(self.ungrouped.user.full_name, [row["cells"][0] for row in rows])
        self.assertNotIn(self.inactive.user.full_name, [row["cells"][0] for row in rows])

    def test_counts_do_not_join_fanout_or_recompute_points_and_equal_deltas_survive(self):
        report = self.report("summary", participant=self.student.pk)
        self.assertEqual(self.metric(report, "الحلقة القرآنية — الحاضر"), 1)
        self.assertEqual(self.metric(report, "إنجاز الحلقة"), 1)
        self.assertEqual(self.metric(report, "الحضور المبكر"), 1)
        self.assertEqual(self.metric(report, "اللقاء — الحاضر"), 0)
        self.assertEqual(self.metric(report, "إنجاز الفعالية"), 1)
        self.assertEqual(self.metric(report, "النقاط المكتسبة في الفترة"), 25)
        self.assertEqual(self.metric(report, "النقاط المخصومة في الفترة"), 4)
        self.assertEqual(self.metric(report, "صافي نقاط الفترة"), 21)
        self.assertEqual(self.metric(report, "مجموع رصيد النقاط الحالي (ليس رصيد الفترة)"), 123)
        row = self.section(report, "النقاط حسب المصدر من الدفتر")["rows"][-1]["cells"]
        self.assertEqual(row[1:], [20, 4, 16])

    def test_event_basis_excludes_null_without_guessing_and_warns_whole_scope(self):
        report = self.report("summary", participant=self.student.pk, points_basis="event")
        self.assertEqual(self.metric(report, "النقاط المكتسبة في الفترة"), 40)
        self.assertEqual(self.metric(report, "النقاط المخصومة في الفترة"), 4)
        self.assertEqual(self.metric(report, "صافي نقاط الفترة"), 36)
        self.assertTrue(any("استُبعد 1" in note and "لا يمكن نسبتها للفترة" in note for note in report["notes"]))
        empty_window = self.report("summary", start_date="2027-01-01", end_date="2027-01-02", points_basis="event")
        self.assertEqual(self.metric(empty_window, "صافي نقاط الفترة"), 0)
        self.assertTrue(any("استُبعد 1" in note for note in empty_window["notes"]))
        self.assertEqual(PointsLedgerEntry.objects.filter(event_date__isnull=True).count(), 1)

    def test_timestamp_window_uses_riyadh_even_if_caller_timezone_is_utc(self):
        with timezone.override("UTC"):
            report = self.report("summary", participant=self.student.pk)
            self.assertEqual(self.metric(report, "التسليمات المحفوظة"), 4)
            self.assertEqual(self.metric(report, "المقبولة (تشمل المميزة)"), 2)
            self.assertEqual(self.metric(report, "المميزة من المقبولة"), 1)
            self.assertEqual(self.metric(report, "قيد المراجعة"), 1)
            self.assertEqual(self.metric(report, "المعادة للتعديل"), 0)
            self.assertEqual(self.metric(report, "المرفوضة"), 1)
        single = self.report("summary", participant=self.student.pk, start_date="2026-10-03")
        self.assertEqual(self.metric(single, "التسليمات المحفوظة"), 1)

    def test_tasks_use_due_window_but_saved_submissions_not_submission_window(self):
        report = self.report("tasks", participant=self.student.pk)
        self.assertEqual(self.metric(report, "المهام ذات الموعد المطابق"), 4)
        self.assertEqual(self.metric(report, "التسليمات المحفوظة"), 4)
        self.assertEqual(self.metric(report, "المعادة للتعديل"), 1)
        self.assertEqual(self.metric(report, "المميزة من المقبولة"), 1)
        titles = [row["cells"][0] for row in report["sections"][0]["rows"]]
        self.assertNotIn(self.tasks[4].title, titles)
        self.assertTrue(any("حتى لو كان تاريخ التسليم خارج الفترة" in note for note in report["notes"]))

    def test_task_drill_lists_current_cohort_saved_and_not_saved_once(self):
        report = self.report("tasks", task=self.tasks[2].pk, group=self.group.pk)
        saved = self.section(report, "المسلّمون — كل التسليمات المحفوظة للمهام المختارة")["rows"]
        missing = self.section(report, "طلاب النطاق الحالي الذين لا يملكون تسليمًا للمهمة المختارة")["rows"]
        self.assertEqual([row["cells"][0] for row in saved], [self.student.user.full_name])
        self.assertEqual([row["cells"][0] for row in missing], [self.quiet.user.full_name])
        self.assertNotIn(self.inactive.user.full_name, repr(report))
        self.assertEqual(saved[0]["detail_url"], reverse("participants:student_detail", kwargs={"pk": self.student.pk}))
        self.assertEqual(self.metric(report, "المعادة للتعديل"), 1)

    def test_attendance_denominator_is_saved_present_plus_explicit_absent_only(self):
        report = self.report("attendance", group=self.group.pk)
        self.assertEqual(self.metric(report, "الحلقة القرآنية — حاضر"), 1)
        self.assertEqual(self.metric(report, "الحلقة القرآنية — غياب صريح"), 1)
        self.assertEqual(self.metric(report, "الحلقة القرآنية — مقام النسبة"), 2)
        self.assertEqual(self.metric(report, "الحلقة القرآنية — نسبة الحضور %"), 50.0)
        rows = self.section(report, "الحلقة القرآنية — ملخص الطلاب")["rows"]
        quiet = next(row["cells"] for row in rows if row["cells"][0] == self.quiet.user.full_name)
        self.assertEqual(quiet[2:6], [0, 0, 0, "—"])

    def test_detailed_missing_rows_only_observed_group_dates_not_calendar_or_absence(self):
        report = self.report("attendance", detail="detailed", participant=self.quiet.pk)
        missing = self.section(report, "الحلقة القرآنية — صفوف غير مسجلة على تواريخ ملاحظة فقط")["rows"]
        self.assertEqual([row["cells"][2] for row in missing], [DAY_2, DAY_3])
        self.assertTrue(all("ليس غيابًا" in row["cells"][3] for row in missing))
        self.assertEqual(self.metric(report, "الحلقة القرآنية — مقام النسبة"), 0)
        self.assertTrue(any("ليس حضورًا متوقعًا" in note for note in report["notes"]))
        orphan = self.report("attendance", detail="detailed", participant=self.ungrouped.pk)
        self.assertEqual(self.section(orphan, "الحلقة القرآنية — صفوف غير مسجلة على تواريخ ملاحظة فقط")["rows"], [])

    def test_group_comparison_shows_sizes_denominators_and_null_bucket(self):
        report = self.report("groups")
        rows = self.section(report, "حجم البيئة والنقاط — حسب العضوية الحالية")["rows"]
        first = next(row["cells"] for row in rows if row["cells"][0] == self.group.name)
        self.assertEqual(first[1:4], [2, 2, 0])
        self.assertEqual(first[4:9], [25, 4, 21, 2, 10.5])
        empty = next(row["cells"] for row in rows if row["cells"][0] == self.empty_group.name)
        self.assertEqual(empty[1], 0)
        self.assertEqual(empty[8], "—")
        self.assertIn("بدون بيئة", [row["cells"][0] for row in rows])
        attendance = self.section(report, "الحلقة القرآنية — مقارنة البيئات")["rows"]
        first = next(row["cells"] for row in attendance if row["cells"][0] == self.group.name)
        self.assertEqual(first[1:6], [2, 1, 1, 2, 50.0])

    def test_private_contacts_and_notes_are_excluded_by_default_including_task_drill(self):
        for key, _ in REPORT_CHOICES:
            with self.subTest(key=key):
                filters = self.filters(reports=[key], detail="detailed", task=self.tasks[0].pk)
                with CaptureQueriesContext(connection) as captured:
                    report = build_reports(self.general, filters)[0]
                self.assertFalse(any("participants_meetingnote" in query["sql"].lower() for query in captured))
                text = repr(report)
                for private in (self.student.user.national_id, self.student.phone, self.student.guardian_phone,
                                "سبب خاص للاختبار", "ملاحظة تعديل خاصة للاختبار", "ملاحظة لقاء خاصة للاختبار", "وصف دفتر خاص للاختبار"):
                    self.assertNotIn(private, text)
                self.assertNotIn("نص خاص لا يصدر", text)
        report = self.report("tasks", task=self.tasks[0].pk, include_private="on")
        private = self.section(report, "بيانات الهوية والتواصل الخاصة — تضمين صريح")
        self.assertEqual(len(private["columns"]), 4)
        self.assertIn(self.student.phone, repr(private))
        self.assertIn("ملاحظة لقاء خاصة للاختبار", repr(report))
        self.assertIn("سبب خاص للاختبار", repr(report))

    def test_detailed_mode_really_adds_sections_rows_and_no_safe_html_cells(self):
        for key, _ in REPORT_CHOICES:
            with self.subTest(key=key):
                summary = self.report(key)
                detailed = self.report(key, detail="detailed")
                self.assertGreater(len(detailed["sections"]), len(summary["sections"]))
        filters = self.filters(reports=[key for key, _ in REPORT_CHOICES], detail="detailed")
        reports = build_reports(self.general, filters)
        for report in reports:
            self.assertEqual(set(report), {"key", "title", "description", "metrics", "notes", "sections"})
            for section in report["sections"]:
                self.assertEqual(set(section), {"title", "columns", "rows"})
                for row in section["rows"]:
                    self.assertEqual(len(row["cells"]), len(section["columns"]))
                    for cell in row["cells"]:
                        self.assertIsInstance(cell, (str, int, float, datetime.date))
                        self.assertNotIsInstance(cell, SafeData)
        self.assertFalse(any(isinstance(cell, str) and cell.startswith("=")
                             for report in reports for section in report["sections"]
                             for row in section["rows"] for cell in row["cells"]))

    def test_filter_labels_are_plain_strings_and_do_not_leak_private_identifiers(self):
        labels = build_filter_labels(self.filters(participant=self.student.pk, task=self.tasks[0].pk))
        self.assertTrue(labels)
        self.assertTrue(all(set(item) == {"label", "value"} for item in labels))
        self.assertTrue(all(type(value) is str for item in labels for value in item.values()))
        self.assertIn(self.student.user.full_name, repr(labels))
        self.assertNotIn(self.student.user.national_id, repr(labels))
        self.assertNotIn(self.student.phone, repr(labels))

    def test_no_hidden_row_cap_and_query_count_does_not_grow_per_student(self):
        filters = self.filters(reports=[key for key, _ in REPORT_CHOICES], detail="detailed")
        with CaptureQueriesContext(connection) as baseline:
            build_reports(self.general, filters)
        for index in range(53):
            self.make_student(str(7100000200 + index), f"طالب إضافي {index:02}", self.group)
        with CaptureQueriesContext(connection) as larger:
            reports = build_reports(self.general, filters)
        self.assertEqual(len(larger), len(baseline))
        students = next(report for report in reports if report["key"] == "students")
        self.assertEqual(len(self.section(students, "ملخص الطلاب")["rows"]), 57)
        self.assertLess(len(larger), 30)

    def test_build_is_read_only_preserves_balances_status_first_date_and_null_event(self):
        filters = self.filters(reports=[key for key, _ in REPORT_CHOICES], detail="detailed", include_private="on")
        first_date = TaskSubmission.objects.get(pk=self.submissions[0].pk).submitted_at
        with CaptureQueriesContext(connection) as captured:
            build_reports(self.general, filters)
        self.assertFalse(any(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for query in captured))
        self.student.refresh_from_db()
        self.assertEqual(self.student.points, 123)
        self.assertEqual(TaskSubmission.objects.get(pk=self.submissions[0].pk).submitted_at, first_date)
        self.assertEqual(PointsLedgerEntry.objects.filter(event_date__isnull=True).count(), 1)

    def test_empty_windows_have_explicit_zero_metrics_and_empty_saved_sections(self):
        report = self.report("students", detail="detailed", start_date="2027-01-01", end_date="2027-01-02")
        self.assertEqual(self.metric(report, "صافي نقاط الفترة"), 0)
        self.assertEqual(self.section(report, "تفاصيل التسليمات بحسب تاريخ الرفع الأول")["rows"], [])
        empty = self.report("summary", group=self.empty_group.pk)
        self.assertEqual(self.metric(empty, "الطلاب في النطاق الحالي"), 0)
        self.assertTrue(any("لا يوجد طلاب" in note for note in empty["notes"]))
