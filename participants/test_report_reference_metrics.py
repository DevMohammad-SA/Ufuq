"""Reference-image indicators must come from saved rows, never copied figures.

Run only with config.test_settings (synthetic in-memory SQLite/media):
    SECRET_KEY=test-only .venv/bin/python manage.py test participants.test_report_reference_metrics --settings=config.test_settings
Existing report-centre tests cover the privilege boundary and export endpoints.
"""
import datetime
from io import BytesIO
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import Role, User
from .models import (
    EliteTripAttendance, Group, MeetingAttendance, Participant, PointsLedgerEntry,
    PointsResetSnapshot, TaskSubmission, WeeklyActivityAttendance, WeeklyTask,
)
from .report_center import (
    RIYADH, ReportCenterFilterForm, build_filter_labels, build_reports,
)
from .report_center_exports import export_reports_pdf, export_reports_xlsx

UTC = datetime.timezone.utc
DAY_1 = datetime.date(2026, 10, 1)
DAY_2 = datetime.date(2026, 10, 2)
DAY_3 = datetime.date(2026, 10, 3)
DAY_4 = datetime.date(2026, 10, 4)
MEETING_SECTION = "حصيلة اللقاءات حسب البيئة الحالية"
UNAVAILABLE_SECTION = "مؤشرات تحتاج بيانات غير محفوظة"
GROUPS = "البيئات ذات حضور لقاء مسجل"
MEETINGS = "اللقاءات ذات حضور مسجل"
MEETING_TOTAL = "إجمالي حضور اللقاء التراكمي"
MEETING_AVERAGE = "متوسط حضور لقاءات البيئات"
UNGROUPED = "حضور لقاء غير منسوب لبيئة"
TASKS = "المهام ذات موعد في الفترة"
SUBMISSIONS = "إجمالي تسليمات المهام في الفترة"
MILES = "إجمالي الأميال المكتسبة في الفترة"
MILES_AVERAGE = "متوسط الأميال المكتسبة لكل طالب بالنطاق"
ELITE_DAYS = "أيام رحلة النخبة ذات حضور مسجل"
ELITE_TOTAL = "إجمالي حضور رحلة النخبة التراكمي"
REFERENCE_LABELS = (
    GROUPS, MEETINGS, MEETING_TOTAL, MEETING_AVERAGE, UNGROUPED,
    TASKS, SUBMISSIONS, MILES, MILES_AVERAGE, ELITE_DAYS, ELITE_TOTAL,
)


@override_settings(TIME_ZONE="Asia/Riyadh")
class ReportReferenceMetricsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(
            username="reference-general", role=Role.GENERAL_SUPERVISOR,
            password="test-only",
        )
        # Reverse creation order and student names: group rows must sort by name.
        cls.group_b = Group.objects.create(name="بيئة باء")
        cls.group_a = Group.objects.create(name="بيئة ألف")
        cls.empty_group = Group.objects.create(name="بيئة خالية")
        cls.first = cls.make_student("7200000001", "طالب زاي", cls.group_a,
                                     points=123, miles=9876, purchase_points=31)
        cls.peer = cls.make_student("7200000002", "طالب ألف", cls.group_a)
        cls.quiet = cls.make_student("7200000003", "طالب هادئ", cls.group_a)
        cls.other = cls.make_student("7200000004", "طالب باء", cls.group_b)
        cls.ungrouped = cls.make_student("7200000005", "طالب بلا بيئة", None)
        cls.early = cls.make_student("7200000006", "طالب مبكر فقط", cls.group_a,
                                     stage="grade_8")
        cls.inactive = cls.make_student("7200000007", "طالب معطل", cls.group_b,
                                        active=False, stage="grade_8")
        for participant, day in [
            (cls.first, DAY_2), (cls.peer, DAY_2), (cls.first, DAY_3),
            (cls.other, DAY_2), (cls.ungrouped, DAY_2), (cls.ungrouped, DAY_3),
            (cls.inactive, DAY_3), (cls.first, DAY_1), (cls.first, DAY_4),
        ]:
            MeetingAttendance.objects.create(
                participant=participant, week_start_date=day, attended=True,
            )
        for participant, day, early in [
            (cls.early, DAY_2, True), (cls.early, DAY_3, False),
            (cls.other, DAY_3, False),
        ]:
            MeetingAttendance.objects.create(
                participant=participant, week_start_date=day,
                attended=False, is_early=early,
            )
        WeeklyActivityAttendance.objects.create(
            participant=cls.first, date=DAY_2, attended=True,
        )
        WeeklyActivityAttendance.objects.create(
            participant=cls.peer, date=DAY_3, attended=True,
        )
        cls.task_start = cls.make_task("منافسة رياضية", DAY_2, active=False)
        cls.task_end = cls.make_task("نشاط ثقافي", DAY_3, active=True)
        cls.task_extra = cls.make_task("مسابقة حركية ورحلة", DAY_3, active=False)
        cls.task_before = cls.make_task("موعد قبل الفترة", DAY_1)
        cls.task_after = cls.make_task("موعد بعد الفترة", DAY_4)
        cls.first_submission = cls.make_submission(
            cls.task_start, cls.first, TaskSubmission.Status.ACCEPTED,
            datetime.datetime(2026, 10, 1, 21, tzinfo=UTC), featured=True,
        )
        cls.make_submission(cls.task_start, cls.peer, TaskSubmission.Status.PENDING,
                            datetime.datetime(2026, 10, 2, 10, tzinfo=UTC))
        cls.make_submission(cls.task_end, cls.first, TaskSubmission.Status.REJECTED,
                            datetime.datetime(2026, 10, 3, 20, 59, 59, 999999, tzinfo=UTC))
        cls.make_submission(cls.task_end, cls.early, TaskSubmission.Status.REVISION_REQUIRED,
                            datetime.datetime(2026, 10, 3, 21, tzinfo=UTC))
        cls.make_submission(cls.task_extra, cls.first, TaskSubmission.Status.REVISION_REQUIRED,
                            datetime.datetime(2026, 10, 2, 10, tzinfo=UTC))
        cls.make_submission(cls.task_after, cls.first, TaskSubmission.Status.REVISION_REQUIRED,
                            datetime.datetime(2026, 10, 2, 10, tzinfo=UTC))
        cls.make_submission(cls.task_before, cls.peer, TaskSubmission.Status.PENDING,
                            datetime.datetime(2026, 10, 1, 10, tzinfo=UTC))
        cls.make_submission(cls.task_start, cls.inactive, TaskSubmission.Status.ACCEPTED,
                            datetime.datetime(2026, 10, 2, 10, tzinfo=UTC))
        for participant, delta, event_day, recorded_day in [
            (cls.first, 10, DAY_2, DAY_2), (cls.first, -4, DAY_3, DAY_2),
            (cls.first, 7, None, DAY_3), (cls.first, 30, DAY_3, DAY_4),
            (cls.first, 5, DAY_1, DAY_2), (cls.first, 0, DAY_2, DAY_2),
            (cls.ungrouped, 2, DAY_2, DAY_2), (cls.inactive, 11, DAY_3, DAY_3),
        ]:
            entry = PointsLedgerEntry.objects.create(
                participant=participant, points_delta=delta,
                source=PointsLedgerEntry.Source.EXTRA, event_date=event_day,
                description="وصف اصطناعي لا يحدد نوع نشاط أو تاريخًا",
            )
            PointsLedgerEntry.objects.filter(pk=entry.pk).update(
                created_at=datetime.datetime.combine(recorded_day, datetime.time(10), UTC),
            )
        for participant, day, status in [
            (cls.first, DAY_2, EliteTripAttendance.Status.PRESENT),
            (cls.peer, DAY_2, EliteTripAttendance.Status.PRESENT),
            (cls.other, DAY_2, EliteTripAttendance.Status.PRESENT),
            (cls.quiet, DAY_2, EliteTripAttendance.Status.ABSENT),
            (cls.early, DAY_2, EliteTripAttendance.Status.NOT_NOMINATED),
            (cls.ungrouped, DAY_2, EliteTripAttendance.Status.EXCUSED),
            (cls.ungrouped, DAY_3, EliteTripAttendance.Status.PRESENT),
            (cls.first, DAY_3, EliteTripAttendance.Status.EXCUSED),
            (cls.peer, DAY_3, EliteTripAttendance.Status.ABSENT),
            (cls.early, DAY_3, EliteTripAttendance.Status.ABSENT),
            (cls.other, DAY_3, EliteTripAttendance.Status.EXCUSED),
            (cls.inactive, DAY_3, EliteTripAttendance.Status.PRESENT),
            (cls.first, DAY_4, EliteTripAttendance.Status.PRESENT),
        ]:
            EliteTripAttendance.objects.create(participant=participant, date=day, status=status)

    @classmethod
    def make_student(cls, identity, name, group, *, active=True, stage="grade_7", **balances):
        user = User.objects.create_user(
            national_id=identity, full_name=name, role=Role.PARTICIPANT,
            is_active=active, password="test-only",
        )
        return Participant.objects.create(user=user, group=group, academic_stage=stage, **balances)

    @staticmethod
    def make_task(title, due, *, active=False):
        return WeeklyTask.objects.create(title=title, description="بيانات اصطناعية",
                                         due_date=due, allowed_formats="text", is_active=active)

    @staticmethod
    def make_submission(task, student, status, stamp, *, featured=False):
        submission = TaskSubmission.objects.create(
            task=task, participant=student, status=status, is_featured=featured,
            text_content="تسليم اصطناعي", reopened_for_resubmission=True,
        )
        TaskSubmission.objects.filter(pk=submission.pk).update(submitted_at=stamp)
        return submission

    def filters(self, **extra):
        params = {"period": "custom", "start_date": "2026-10-02", "end_date": "2026-10-03",
                  "account_status": "active", "detail": "summary", "points_basis": "recorded",
                  "reports": ["summary"]}
        params.update(extra)
        form = ReportCenterFilterForm(params, user=self.general)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return form.cleaned_data

    def report(self, **extra):
        return build_reports(self.general, self.filters(**extra))[0]

    def metric(self, report, label):
        matching = [metric["value"] for metric in report["metrics"] if metric["label"] == label]
        self.assertEqual(len(matching), 1, f"Expected one metric: {label}")
        return matching[0]

    def section(self, report, title):
        matching = [section for section in report["sections"] if section["title"] == title]
        self.assertEqual(len(matching), 1, f"Expected one section: {title}")
        return matching[0]

    def assert_metrics(self, report, expected):
        for label, value in expected.items():
            with self.subTest(label=label):
                self.assertEqual(self.metric(report, label), value)

    def test_meetings_deduplicate_group_date_not_students_or_global_day(self):
        report = self.report()
        self.assert_metrics(report, {
            GROUPS: 2, MEETINGS: 3, MEETING_TOTAL: 6, UNGROUPED: 2,
            MEETING_AVERAGE: 1.33,
        })
        section = self.section(report, MEETING_SECTION)
        self.assertEqual(section["columns"], ["البيئة الحالية", "اللقاءات ذات حضور",
                                              "الحضور التراكمي", "متوسط الحضور للقاء"])
        self.assertEqual([row["cells"] for row in section["rows"]], [
            [self.group_a.name, 2, 3, 1.5], [self.group_b.name, 1, 1, 1.0],
        ])
        self.assertNotIn("بدون بيئة", [row["cells"][0] for row in section["rows"]])

    def test_current_cohort_filters_apply_without_wider_peer_date_evidence(self):
        # Tuples: non-null groups, group-days, all true rows, orphan rows, mean.
        cases = [
            ({"group": self.group_a.pk}, (1, 2, 3, 0, 1.5)),
            ({"group": self.group_b.pk}, (1, 1, 1, 0, 1.0)),
            ({"participant": self.peer.pk}, (1, 1, 1, 0, 1.0)),
            ({"participant": self.early.pk}, (0, 0, 0, 0, "—")),
            ({"academic_stage": "grade_8"}, (0, 0, 0, 0, "—")),
            ({"account_status": "inactive"}, (1, 1, 1, 0, 1.0)),
            ({"account_status": "all"}, (2, 4, 7, 2, 1.25)),
            ({"academic_stage": "grade_8", "account_status": "all"}, (1, 1, 1, 0, 1.0)),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                self.assert_metrics(self.report(**filters), dict(zip(
                    (GROUPS, MEETINGS, MEETING_TOTAL, UNGROUPED, MEETING_AVERAGE), expected,
                )))

    def test_activity_dates_are_inclusive_and_all_ignores_stale_custom_dates(self):
        cases = [
            ({"end_date": "2026-10-02"}, {MEETINGS: 2, MEETING_TOTAL: 4, UNGROUPED: 1,
                                         MEETING_AVERAGE: 1.5, ELITE_DAYS: 1, ELITE_TOTAL: 3}),
            ({"start_date": "2026-10-03"}, {GROUPS: 1, MEETINGS: 1, MEETING_TOTAL: 2,
                                           UNGROUPED: 1, MEETING_AVERAGE: 1.0,
                                           ELITE_DAYS: 1, ELITE_TOTAL: 1}),
            ({"period": "all"}, {GROUPS: 2, MEETINGS: 5, MEETING_TOTAL: 8, UNGROUPED: 2,
                                 MEETING_AVERAGE: 1.2, TASKS: 5, ELITE_DAYS: 3, ELITE_TOTAL: 5}),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                self.assert_metrics(self.report(**filters), expected)

    def test_current_null_membership_changes_attribution_not_cumulative_total(self):
        # A fixture mutation, deliberately OUTSIDE the read-only build assertion.
        Participant.objects.filter(pk=self.first.pk).update(group=None)
        report = self.report()
        self.assert_metrics(report, {GROUPS: 2, MEETINGS: 2, MEETING_TOTAL: 6,
                                     UNGROUPED: 4, MEETING_AVERAGE: 1.0})
        self.assertEqual([row["cells"] for row in self.section(report, MEETING_SECTION)["rows"]], [
            [self.group_a.name, 1, 1, 1.0], [self.group_b.name, 1, 1, 1.0],
        ])
        orphan = self.report(participant=self.ungrouped.pk)
        self.assert_metrics(orphan, {GROUPS: 0, MEETINGS: 0, MEETING_TOTAL: 2,
                                     UNGROUPED: 2, MEETING_AVERAGE: "—"})
        self.assertEqual(self.section(orphan, MEETING_SECTION)["rows"], [])

    def test_quiet_nonempty_scope_has_zero_miles_mean_but_empty_denominators_dash(self):
        for filters, size, miles_mean in [
            ({"participant": self.quiet.pk}, 1, 0.0),
            ({"group": self.empty_group.pk}, 0, "—"),
        ]:
            with self.subTest(filters=filters):
                report = self.report(**filters)
                self.assert_metrics(report, {
                    "الطلاب في النطاق الحالي": size, GROUPS: 0, MEETINGS: 0,
                    MEETING_TOTAL: 0, UNGROUPED: 0, MEETING_AVERAGE: "—",
                    MILES: 0, MILES_AVERAGE: miles_mean, ELITE_DAYS: 0, ELITE_TOTAL: 0,
                    TASKS: 3, SUBMISSIONS: 0,
                })
        report = self.report(start_date="2027-01-01", end_date="2027-01-02")
        self.assert_metrics(report, {MEETINGS: 0, MEETING_TOTAL: 0, MEETING_AVERAGE: "—",
                                     TASKS: 0, SUBMISSIONS: 0, MILES: 0, MILES_AVERAGE: 0.0,
                                     ELITE_DAYS: 0, ELITE_TOTAL: 0})

    def test_task_due_count_and_submission_first_stamp_are_independent(self):
        with timezone.override("UTC"):
            report = self.report()
        self.assert_metrics(report, {TASKS: 3, SUBMISSIONS: 5, "المقبولة (تشمل المميزة)": 1,
                                     "المميزة من المقبولة": 1, "قيد المراجعة": 1,
                                     "المرفوضة": 1, "المعادة للتعديل": 2})
        cases = [
            ({"task": self.task_start.pk}, {TASKS: 1, SUBMISSIONS: 2}),
            ({"task": self.task_end.pk}, {TASKS: 1, SUBMISSIONS: 1}),
            ({"task": self.task_start.pk, "account_status": "all"}, {TASKS: 1, SUBMISSIONS: 3}),
            ({"period": "all", "task": self.task_after.pk}, {TASKS: 1, SUBMISSIONS: 1}),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                filtered = self.report(**filters)
                self.assert_metrics(filtered, expected)
                # A task selection must not narrow attendance or earned points.
                self.assertTrue(any("عدّ المهام ذات الموعد والتسليمات" in note for note in filtered["notes"]))
                self.assertFalse(any("عدادات التسليمات فقط" in note for note in filtered["notes"]))
                self.assertEqual(self.metric(filtered, GROUPS), 2)
                if filters.get("account_status", "active") == "active" and filters.get("period") != "all":
                    self.assert_metrics(filtered, {MEETING_TOTAL: 6, MILES: 240})

    def test_earned_miles_use_positive_ledger_basis_and_every_scoped_student(self):
        cases = [
            ({}, 240, 40.0),
            ({"points_basis": "event"}, 420, 70.0),
            ({"participant": self.first.pk}, 220, 220.0),
            ({"participant": self.first.pk, "points_basis": "event"}, 400, 400.0),
            ({"group": self.group_a.pk}, 220, 55.0),
            ({"account_status": "all"}, 350, 50.0),
            ({"account_status": "inactive"}, 110, 110.0),
            ({"period": "all", "points_basis": "event"}, 470, 78.33),
        ]
        for filters, total, average in cases:
            with self.subTest(filters=filters):
                report = self.report(**filters)
                self.assert_metrics(report, {MILES: total, MILES_AVERAGE: average})
                self.assertEqual(total, self.metric(report, "النقاط المكتسبة في الفترة") * 10)
        report = self.report()
        self.assertNotEqual(self.metric(report, MILES), self.first.miles)
        self.assertNotEqual(self.metric(report, MILES), self.metric(report, "صافي نقاط الفترة") * 10)
        event = self.report(points_basis="event")
        self.assertTrue(any("استُبعد 1" in note for note in event["notes"]))
        self.assertEqual(PointsLedgerEntry.objects.filter(event_date__isnull=True).count(), 1)

    def test_elite_only_present_rows_count_global_dates_across_current_groups(self):
        cases = [
            ({}, (2, 4)),
            ({"group": self.group_a.pk}, (1, 2)),
            ({"group": self.group_b.pk}, (1, 1)),
            ({"participant": self.first.pk}, (1, 1)),
            ({"participant": self.early.pk}, (0, 0)),
            ({"participant": self.quiet.pk}, (0, 0)),
            ({"account_status": "inactive"}, (1, 1)),
            ({"account_status": "all"}, (2, 5)),
            ({"academic_stage": "grade_8"}, (0, 0)),
            ({"academic_stage": "grade_8", "account_status": "all"}, (1, 1)),
        ]
        for filters, (days, attendance) in cases:
            with self.subTest(filters=filters):
                self.assert_metrics(self.report(**filters), {ELITE_DAYS: days, ELITE_TOTAL: attendance})

    def test_unclassified_activity_indicators_are_unavailable_not_inferred_zeroes(self):
        report = self.report()
        self.assert_metrics(report, {"فعالية الأسبوع — الحاضر": 2, TASKS: 3})
        section = self.section(report, UNAVAILABLE_SECTION)
        self.assertEqual(section["columns"], ["المؤشر", "حالة البيانات"])
        labels = [row["cells"][0] for row in section["rows"]]
        self.assertGreaterEqual(len(labels), 3)
        for category in ("رياض", "ثقاف", "رحل"):
            self.assertTrue(any(category in label for label in labels), category)
        for row in section["rows"]:
            self.assertEqual(len(row["cells"]), 2)
            # The state remains unavailable; its visible explanation tells the
            # supervisor why it is not zero or an inferred count from a title.
            self.assertTrue(row["cells"][1].startswith("غير متاح — "))
            self.assertIn("لا يوجد تصنيف" if "المنافسات" in row["cells"][0] else "لا سجل تنفيذ", row["cells"][1])
        self.assertTrue(all(label not in REFERENCE_LABELS for label in labels))

    def test_summary_only_extra_bulk_queries_are_constant_as_students_grow(self):
        summary_filters = self.filters()
        student_filters = self.filters(reports=["students"])
        with CaptureQueriesContext(connection) as students_only:
            build_reports(self.general, student_filters)
        with CaptureQueriesContext(connection) as baseline:
            build_reports(self.general, summary_filters)
        self.assertLessEqual(len(baseline) - len(students_only), 3)
        for table in ("participants_weeklytask", "participants_elitetripattendance"):
            marker = f'from "{table}"'
            self.assertEqual(sum(marker in query["sql"].lower() for query in baseline), 1, table)
            self.assertFalse(any(marker in query["sql"].lower() for query in students_only), table)
        for index in range(12):
            student = self.make_student(str(7200000100 + index), f"طالب إضافي {index:02}", self.group_a)
            MeetingAttendance.objects.create(participant=student, week_start_date=DAY_2, attended=True)
            EliteTripAttendance.objects.create(participant=student, date=DAY_2,
                                                status=EliteTripAttendance.Status.PRESENT)
        with CaptureQueriesContext(connection) as larger:
            build_reports(self.general, summary_filters)
        self.assertEqual(len(larger), len(baseline))

    def test_build_has_no_domain_writes_or_balance_ledger_status_backfill(self):
        models = (User, Participant, MeetingAttendance, EliteTripAttendance, WeeklyTask,
                  TaskSubmission, WeeklyActivityAttendance, PointsLedgerEntry, PointsResetSnapshot)
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        filters = self.filters(reports=["summary", "students", "attendance", "tasks", "groups"],
                               detail="detailed")
        with CaptureQueriesContext(connection) as captured:
            build_reports(self.general, filters)
        for query in captured:
            self.assertFalse(query["sql"].lstrip().upper().startswith(
                ("INSERT", "UPDATE", "DELETE", "REPLACE")), query["sql"])
        self.assertEqual(before, {model: list(model.objects.order_by("pk").values()) for model in models})

    def test_actual_xlsx_and_mock_pdf_render_real_labels_scalars_without_queries(self):
        generated_at = datetime.datetime(2026, 11, 7, 14, 30, tzinfo=RIYADH)
        for extra in ({}, {"group": self.empty_group.pk}, {"participant": self.quiet.pk}):
            with self.subTest(filters=extra):
                filters = self.filters(**extra)
                report = build_reports(self.general, filters)[0]
                labels = build_filter_labels(filters)
                with self.assertNumQueries(0):
                    data = export_reports_xlsx([report], labels, generated_at)
                workbook = load_workbook(BytesIO(data))
                if not extra:
                    with self.assertNumQueries(0):
                        actual_pdf = export_reports_pdf([report], labels, generated_at)
                    self.assertTrue(actual_pdf.startswith(b"%PDF"))
                    self.assertGreater(len(actual_pdf), 1000)
                sheet = workbook.worksheets[1]
                self.assertTrue(sheet.sheet_view.rightToLeft)
                label_cells = {cell.value: cell for row in sheet for cell in row
                               if isinstance(cell.value, str)}
                for label in REFERENCE_LABELS:
                    cell = label_cells[label]
                    value_cell = sheet.cell(cell.row, cell.column + 1)
                    expected = self.metric(report, label)
                    self.assertEqual(value_cell.value, expected)
                    self.assertEqual(value_cell.data_type, "s" if isinstance(expected, str) else "n")
                self.assertIn(MEETING_SECTION, label_cells)
                self.assertIn(UNAVAILABLE_SECTION, label_cells)
                # Confirm the actual group-table numeric cells, not only cards.
                group_headers = [row for row in sheet if row[1].value == "البيئة الحالية"
                                 and row[2].value == "اللقاءات ذات حضور"]
                self.assertEqual(len(group_headers), 1)
                first_row = group_headers[0][1].row + 1
                for offset, row in enumerate(self.section(report, MEETING_SECTION)["rows"]):
                    for column, expected in enumerate(row["cells"], 2):
                        cell = sheet.cell(first_row + offset, column)
                        self.assertEqual(cell.value, expected)
                        self.assertEqual(cell.data_type, "s" if isinstance(expected, str) else "n")
                with self.assertNumQueries(0), mock.patch("weasyprint.HTML") as html:
                    html.return_value.write_pdf.return_value = b"%PDF-reference-test"
                    self.assertEqual(export_reports_pdf([report], labels, generated_at),
                                     b"%PDF-reference-test")
                rendered = html.call_args.kwargs["string"]
                for label in REFERENCE_LABELS:
                    self.assertIn(label, rendered)
                self.assertIn(MEETING_SECTION, rendered)
                self.assertIn(UNAVAILABLE_SECTION, rendered)
                self.assertIn("غير متاح", rendered)
                self.assertTrue(callable(html.call_args.kwargs["url_fetcher"]))
