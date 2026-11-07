"""Synthetic group-chart checks. Run with config.test_settings only."""
import datetime
import json
from copy import deepcopy
from unittest import mock

from django.db import connection
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.html import json_script
from django.utils.safestring import SafeData, mark_safe

from accounts.models import Role, User
from .models import (
    CircleAttendance, Group, MeetingAttendance, MeetingNote, Participant,
    PointsLedgerEntry, TaskSubmission, WeeklyActivityAttendance, WeeklyTask,
)
from .report_center import RIYADH, ReportCenterFilterForm, build_reports
from .report_group_charts import build_group_comparison


UTC = datetime.timezone.utc
DAY_2 = datetime.date(2026, 10, 2)
DAY_3 = datetime.date(2026, 10, 3)
KEYS = ["meeting", "circle", "achievement", "activity", "submitters", "net_average"]
POINT_TITLE = "حجم البيئة والنقاط — حسب العضوية الحالية"
TASK_TITLE = "تسليمات الفترة — مقام الطلاب ظاهر"


def complete_report(buckets):
    """Independent complete-contract fixture; no ORM or adapter constants."""
    point_rows, task_rows = [], []
    sources = [
        ("circle", "الحلقة القرآنية", "إنجاز الحلقة"),
        ("meeting", "اللقاء", "الحضور المبكر"),
        ("activity", "فعالية الأسبوع", "إنجاز الفعالية"),
    ]
    attendance_rows = {key: [] for key, *_ in sources}
    for bucket in buckets:
        name, size = bucket["name"], bucket.get("size", 0)
        earned, deducted = bucket.get("earned", 0), bucket.get("deducted", 0)
        net = earned - deducted
        point_rows.append({"cells": [name, size, size, 0, earned, deducted, net, size,
                                     round(net / size, 2) if size else "—", bucket.get("balance", 0)]})
        submitted = bucket.get("submitted", 0)
        submitters = bucket.get("submitters", 0)
        task_rows.append({"cells": [name, size, submitted, 0, 0, submitted, 0, 0, submitters,
                                    round(submitters * 100 / size, 2) if size else "—"]})
        for key, *_ in sources:
            present, absent, extra = bucket.get(key, (0, 0, 0))
            recorded = present + absent
            attendance_rows[key].append({"cells": [name, size, present, absent, recorded,
                                                   round(present * 100 / recorded, 2) if recorded else "—", extra]})
    sections = [{"title": POINT_TITLE, "columns": [
        "البيئة الحالية", "طلاب النطاق", "النشطون", "المعطلون", "المكتسب", "الخصم", "صافي الفترة",
        "مقام متوسط الصافي (طلاب النطاق)", "متوسط الصافي لكل طالب", "مجموع الرصيد الحالي (ليس رصيد الفترة)",
    ], "rows": point_rows}, {"title": TASK_TITLE, "columns": [
        "البيئة الحالية", "طلاب النطاق (مقام نسبة الطلاب المسلّمين)", "التسليمات المحفوظة",
        "المقبولة (تشمل المميزة)", "المميزة من المقبولة", "قيد المراجعة", "المعادة للتعديل", "المرفوضة",
        "طلاب لديهم تسليم واحد على الأقل في الفترة", "نسبة الطلاب ذوي التسليم %",
    ], "rows": task_rows}]
    for key, label, extra_label in sources:
        sections.append({"title": label + " — مقارنة البيئات", "columns": [
            "البيئة الحالية", "طلاب النطاق", "الحاضر", "الغياب الصريح", "مقام الحضور (السجلات المحفوظة)",
            "نسبة الحضور %", extra_label,
        ], "rows": attendance_rows[key]})
    return {
        "key": "groups", "title": "مقارنة البيئات", "description": "مصدر اختبار مصطنع",
        "metrics": [{"label": "البيئات والتجميعات المعروضة", "value": len(buckets)},
                    {"label": "طلاب النطاق الحالي", "value": sum(bucket.get("size", 0) for bucket in buckets)}],
        "notes": ["ملاحظة مصدر خاصة لا تنسخ"], "sections": sections,
    }


@override_settings(TIME_ZONE="Asia/Riyadh")
class ReportGroupChartsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username="charts-general", role=Role.GENERAL_SUPERVISOR, password="synthetic-only")
        cls.group = Group.objects.create(name="بيئة المقارنة الأولى")
        cls.other_group = Group.objects.create(name="بيئة المقارنة الثانية")
        cls.empty_group = Group.objects.create(name="بيئة فارغة")
        cls.student = cls.make_student("7600000001", "طالب الهوية الخاص", cls.group, points=900, miles=9000, purchase_points=800)
        cls.quiet = cls.make_student("7600000002", "طالب مرحلة أخرى", cls.group, stage="grade_8")
        cls.silent = cls.make_student("7600000003", "طالب بلا سجلات", cls.group)
        cls.inactive = cls.make_student("7600000004", "طالب معطل", cls.group, active=False, stage="grade_8")
        cls.other = cls.make_student("7600000005", "طالب الخصم", cls.other_group)
        cls.ungrouped = cls.make_student("7600000006", "طالب بلا بيئة", None)
        for participant, day, attended, achieved in [
            (cls.student, DAY_2, False, True), (cls.student, DAY_3, False, True),
            (cls.student, datetime.date(2026, 10, 4), True, True), (cls.quiet, DAY_2, True, False),
            (cls.inactive, DAY_2, True, True), (cls.other, DAY_3, True, False),
        ]:
            CircleAttendance.objects.create(participant=participant, date=day, attended=attended, achieved=achieved)
        MeetingAttendance.objects.create(participant=cls.student, week_start_date=DAY_2, attended=False, is_early=True)
        MeetingAttendance.objects.create(participant=cls.quiet, week_start_date=DAY_3, attended=True)
        MeetingAttendance.objects.create(participant=cls.other, week_start_date=DAY_3, attended=True)
        WeeklyActivityAttendance.objects.create(participant=cls.student, date=DAY_3, attended=True)
        WeeklyActivityAttendance.objects.create(participant=cls.quiet, date=DAY_2, attended=False)
        cls.tasks, cls.submissions = [], []
        stamps = [
            datetime.datetime(2026, 10, 1, 21, tzinfo=UTC),
            datetime.datetime(2026, 10, 3, 20, 59, 59, 999999, tzinfo=UTC),
            datetime.datetime(2026, 10, 3, 21, tzinfo=UTC),
            datetime.datetime(2026, 10, 1, 20, 59, 59, 999999, tzinfo=UTC),
        ]
        for index, stamp in enumerate(stamps):
            task = WeeklyTask.objects.create(title="مهمة مصطنعة " + str(index), description="خاص", due_date=DAY_3, allowed_formats="text")
            submission = TaskSubmission.objects.create(participant=cls.student, task=task, text_content="محتوى التسليم الخاص", status=TaskSubmission.Status.PENDING)
            TaskSubmission.objects.filter(pk=submission.pk).update(submitted_at=stamp)
            cls.tasks.append(task)
            cls.submissions.append(submission)
        other_submission = TaskSubmission.objects.create(participant=cls.other, task=cls.tasks[0], status=TaskSubmission.Status.REJECTED, rejection_reason="سبب الرفض الخاص")
        TaskSubmission.objects.filter(pk=other_submission.pk).update(submitted_at=datetime.datetime(2026, 10, 3, 10, tzinfo=UTC))
        for participant, delta, event, recorded in [
            (cls.student, 10, DAY_2, DAY_2), (cls.student, -4, DAY_3, DAY_3),
            (cls.student, 10, None, DAY_3), (cls.student, 30, DAY_3, datetime.date(2026, 10, 4)),
            (cls.student, 5, datetime.date(2026, 10, 1), DAY_2), (cls.other, -7, DAY_3, DAY_3),
        ]:
            entry = PointsLedgerEntry.objects.create(participant=participant, points_delta=delta, event_date=event, source=PointsLedgerEntry.Source.EXTRA, description="وصف دفتر خاص")
            PointsLedgerEntry.objects.filter(pk=entry.pk).update(created_at=datetime.datetime.combine(recorded, datetime.time(10), UTC))
        MeetingNote.objects.create(participant=cls.student, meeting_date=DAY_2, text="ملاحظة لقاء خاصة")

    @classmethod
    def make_student(cls, identity, name, group, *, active=True, stage="grade_7", **balances):
        user = User.objects.create_user(national_id=identity, full_name=name, role=Role.PARTICIPANT, is_active=active, password="synthetic-only")
        return Participant.objects.create(user=user, group=group, academic_stage=stage, phone="0501122334", guardian_phone="0554433221", **balances)

    def filters(self, **extra):
        params = {"period": "custom", "start_date": "2026-10-02", "end_date": "2026-10-03", "reports": ["groups"]}
        params.update(extra)
        form = ReportCenterFilterForm(params, user=self.general)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return form.cleaned_data

    def report(self, **extra):
        return build_reports(self.general, self.filters(**extra))[0]

    @staticmethod
    def section(report, title):
        return next(section for section in report["sections"] if section["title"] == title)

    @staticmethod
    def series(payload, key):
        return next(series for series in payload["series"] if series["key"] == key)

    def point(self, payload, key, name=None):
        return next(point for point in self.series(payload, key)["points"] if point["name"] == (self.group.name if name is None else name))

    def assert_point(self, payload, key, numerator, denominator, value, name=None):
        self.assertEqual(self.point(payload, key, name), {"name": self.group.name if name is None else name,
                                                       "numerator": numerator, "denominator": denominator, "value": value})

    def test_exact_api_order_units_and_raw_integer_precision(self):
        report = complete_report([{"name": "ج", "size": 3, "earned": 1, "meeting": (1, 2, 0),
                                   "circle": (0, 3, 2), "activity": (2, 1, 2), "submitted": 4, "submitters": 1},
                                  {"name": "أ", "size": 1, "deducted": 7}, {"name": "بدون بيئة", "size": 0}])
        # Valid but deliberately different display values must not drive charts.
        report["sections"][0]["rows"][0]["cells"][8] = 99.0
        report["sections"][1]["rows"][0]["cells"][9] = 88.0
        report["sections"][2]["rows"][0]["cells"][5] = 77.0
        payload = build_group_comparison(report)
        self.assertEqual(set(payload), {"series", "scope_note"})
        self.assertEqual([series["key"] for series in payload["series"]], KEYS)
        self.assertEqual([series["unit"] for series in payload["series"]], ["percent"] * 5 + ["points"])
        for series in payload["series"]:
            self.assertEqual(set(series), {"key", "label", "unit", "description", "points"})
            self.assertRegex(series["label"], r"[\u0600-\u06ff]")
            self.assertEqual([point["name"] for point in series["points"]], ["ج", "أ", "بدون بيئة"])
            for point in series["points"]:
                self.assertEqual(set(point), {"name", "value", "numerator", "denominator"})
                self.assertIs(type(point["numerator"]), int)
                self.assertIs(type(point["denominator"]), int)
                if point["value"] is not None:
                    self.assertIs(type(point["value"]), float)
        for key, numerator, denominator, value in [("meeting", 1, 3, 33.33), ("circle", 0, 3, 0.0),
                                                  ("achievement", 2, 3, 66.67), ("activity", 2, 3, 66.67),
                                                  ("submitters", 1, 3, 33.33), ("net_average", 1, 3, 0.33)]:
            self.assert_point(payload, key, numerator, denominator, value, "ج")
        self.assert_point(payload, "net_average", -7, 1, -7.0, "أ")

    def test_zero_denominators_are_none_not_zero_or_missing_buckets(self):
        payload = build_group_comparison(complete_report([{"name": "فارغة"}, {"name": "بلا سجل", "size": 2}]))
        for series in payload["series"]:
            self.assert_point(payload, series["key"], 0, 0, None, "فارغة")
        for key in KEYS[:4]:
            self.assert_point(payload, key, 0, 0, None, "بلا سجل")
        for key in KEYS[4:]:
            self.assert_point(payload, key, 0, 2, 0.0, "بلا سجل")
        self.assertIn("الحالية", payload["scope_note"])
        self.assertIn("التحضير الجزئي", payload["scope_note"])
        self.assertIn("ليس غيابًا", payload["scope_note"])
        empty = build_group_comparison(complete_report([]))
        self.assertTrue(all(series["points"] == [] for series in empty["series"]))

    def test_unknown_or_malformed_contract_fails_without_source_values(self):
        original = complete_report([{"name": "اسم خاص لا يظهر", "size": 1}])
        invalid = [None, [], {}, {**original, "key": "students"}, {**original, "key": "unknown"},
                   {**original, "extra": "هوية خاصة"}, {**original, "sections": None}]
        for mutation in ("missing", "duplicate", "title", "columns", "row_length", "page_obj", "metric"):
            report = deepcopy(original)
            if mutation == "missing":
                report["sections"].pop()
            elif mutation == "duplicate":
                report["sections"].append(deepcopy(report["sections"][0]))
            elif mutation == "title":
                report["sections"][0]["title"] = "عنوان مختلف"
            elif mutation == "columns":
                report["sections"][0]["columns"][4:6] = reversed(report["sections"][0]["columns"][4:6])
            elif mutation == "row_length":
                report["sections"][0]["rows"][0]["cells"].append(0)
            elif mutation == "page_obj":
                report["sections"][0]["page_obj"] = object()
            else:
                report["metrics"][0]["value"] = "1"
            invalid.append(report)
        for report in invalid:
            with self.subTest(kind=type(report).__name__), self.assertNumQueries(0), self.assertRaises(ValueError) as caught:
                build_group_comparison(report)
            self.assertNotIn("اسم خاص", str(caught.exception))
            self.assertNotIn("هوية خاصة", str(caught.exception))

    def test_every_source_column_type_and_count_relationship_is_validated(self):
        original = complete_report([{"name": "مصطنعة", "size": 2, "earned": 3, "submitted": 2, "submitters": 1,
                                     "meeting": (1, 1, 1), "circle": (1, 1, 2), "activity": (1, 1, 1)}])
        invalid_cells = [(0, 1, True), (0, 2, -1), (0, 3, "0"), (0, 4, 3.0), (0, 5, None),
                         (0, 6, 99), (0, 7, 3), (0, 8, float("inf")), (0, 9, -1),
                         (1, 2, 0), (1, 3, 3), (1, 4, 1), (1, 5, -1), (1, 6, "0"),
                         (1, 7, None), (1, 8, 3), (1, 9, "50.0"),
                         (2, 2, -1), (2, 3, 0), (2, 4, 3), (2, 5, 101.0), (2, 6, 3),
                         (3, 5, float("nan")), (4, 6, 0), (4, 0, object())]
        for section, column, value in invalid_cells:
            report = deepcopy(original)
            report["sections"][section]["rows"][0]["cells"][column] = value
            with self.subTest(section=section, column=column), self.assertNumQueries(0), self.assertRaises(ValueError):
                build_group_comparison(report)
        for section in range(5):
            report = complete_report([{"name": "فارغة"}])
            display_index = 8 if section == 0 else 9 if section == 1 else 5
            report["sections"][section]["rows"][0]["cells"][display_index] = 0.0
            with self.subTest(empty_section=section), self.assertRaises(ValueError):
                build_group_comparison(report)

    def test_bucket_alignment_and_complete_metric_totals_prevent_silent_truncation(self):
        original = complete_report([{"name": "أ", "size": 2}, {"name": "ب", "size": 1}])
        invalid = []
        for index in range(1, 5):
            report = deepcopy(original)
            report["sections"][index]["rows"].reverse()
            invalid.append(report)
        report = deepcopy(original)
        for section in report["sections"]:
            section["rows"].pop()
        invalid.append(report)
        report = deepcopy(original)
        report["metrics"][1]["value"] = 4
        invalid.append(report)
        for report in invalid:
            with self.assertRaises(ValueError):
                build_group_comparison(report)
        original["sections"].reverse()
        self.assertEqual([p["name"] for p in build_group_comparison(original)["series"][0]["points"]], ["أ", "ب"])

    def test_plain_json_escaped_group_name_and_source_copy_are_safe(self):
        bad = '</script><script>window.groupLeak=1</script>&'
        report = complete_report([{"name": mark_safe(bad), "size": 1}])
        before = deepcopy(report)
        with self.assertNumQueries(0):
            payload = build_group_comparison(report)
        self.assertEqual(report, before)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        self.assertNotIn(report["notes"][0], encoded)
        for series in payload["series"]:
            name = series["points"][0]["name"]
            self.assertIs(type(name), str)
            self.assertNotIsInstance(name, SafeData)
            self.assertEqual(name, bad)
        script = str(json_script(payload, "group-comparison-data"))
        self.assertNotIn(bad, script)
        self.assertIn(r"\u003C/script\u003E", script)
        self.assertIn(r"\u0026", script)
        self.assertEqual(json.loads(script.split(">", 1)[1].rsplit("</script>", 1)[0]), payload)
        payload["series"][0]["points"][0]["name"] = "تعديل الناتج"
        self.assertEqual(report, before)

    def test_duplicate_display_names_keep_distinct_buckets_in_source_order(self):
        # A real group's free-text name may coincide with the ungrouped label.
        report = complete_report([{"name": "بدون بيئة", "size": 2}, {"name": "بدون بيئة", "size": 1}])
        payload = build_group_comparison(report)
        self.assertEqual([p["denominator"] for p in self.series(payload, "submitters")["points"]], [2, 1])
        self.assertEqual(len(self.series(payload, "meeting")["points"]), 2)

    def test_more_than_fifty_complete_group_buckets_are_never_capped(self):
        names = ["بيئة مصطنعة " + str(index).zfill(2) for index in range(57)] + ["بدون بيئة"]
        report = complete_report([{"name": name, "size": 1} for name in names])
        with self.assertNumQueries(0):
            payload = build_group_comparison(report)
        for series in payload["series"]:
            self.assertEqual([p["name"] for p in series["points"]], names)

    def test_engine_attendance_sources_and_achievement_are_independent(self):
        report = self.report()
        payload = build_group_comparison(report)
        self.assert_point(payload, "meeting", 1, 2, 50.0)
        self.assert_point(payload, "circle", 1, 3, 33.33)
        self.assert_point(payload, "achievement", 2, 3, 66.67)
        self.assert_point(payload, "activity", 1, 2, 50.0)
        self.assert_point(payload, "net_average", 21, 3, 7.0)
        self.assert_point(payload, "net_average", -7, 1, -7.0, self.other_group.name)
        names = [row["cells"][0] for row in self.section(report, POINT_TITLE)["rows"]]
        for series in payload["series"]:
            self.assertEqual([p["name"] for p in series["points"]], names)

    def test_submitters_are_unique_students_with_inclusive_first_submission_dates(self):
        with timezone.override("UTC"):
            report = self.report()
        row = next(row["cells"] for row in self.section(report, TASK_TITLE)["rows"] if row["cells"][0] == self.group.name)
        self.assertEqual(row[2], 2)
        self.assertEqual(row[8], 1)
        payload = build_group_comparison(report)
        self.assert_point(payload, "submitters", 1, 3, 33.33)
        self.assert_point(payload, "submitters", 1, 1, 100.0, self.other_group.name)
        single = build_group_comparison(self.report(start_date="2026-10-03"))
        self.assert_point(single, "submitters", 1, 3, 33.33)
        excluded = build_group_comparison(self.report(task=self.tasks[3].pk))
        self.assert_point(excluded, "submitters", 0, 3, 0.0)
        selected = build_group_comparison(self.report(task=self.tasks[0].pk))
        self.assert_point(selected, "submitters", 1, 3, 33.33)
        for key in KEYS[:4] + ["net_average"]:
            self.assertEqual(self.series(selected, key), self.series(payload, key))

    def test_current_group_stage_status_and_participant_filters_define_denominators(self):
        for extra, size, circle in [
            ({"account_status": "all"}, 4, (2, 4, 50.0)),
            ({"account_status": "inactive"}, 1, (1, 1, 100.0)),
            ({"academic_stage": "grade_8"}, 1, (1, 1, 100.0)),
            ({"participant": self.student.pk}, 1, (0, 2, 0.0)),
            ({"group": self.group.pk}, 3, (1, 3, 33.33)),
        ]:
            with self.subTest(extra=extra):
                payload = build_group_comparison(self.report(**extra))
                self.assertEqual(self.point(payload, "submitters")["denominator"], size)
                self.assert_point(payload, "circle", *circle)
                if "group" in extra:
                    self.assertEqual([p["name"] for p in payload["series"][0]["points"]], [self.group.name])
        Participant.objects.filter(pk=self.student.pk).update(group=self.other_group)
        moved = build_group_comparison(self.report())
        self.assert_point(moved, "circle", 1, 1, 100.0)
        self.assert_point(moved, "achievement", 2, 3, 66.67, self.other_group.name)
        self.assert_point(moved, "net_average", 14, 2, 7.0, self.other_group.name)

    def test_existing_form_defaults_week_and_all_are_reused_without_adapter_filters(self):
        stamp = datetime.datetime(2026, 10, 3, 22, tzinfo=UTC)
        with mock.patch("participants.report_center.timezone.now", return_value=stamp):
            for query in ("reports=groups", "reports=groups&period=week", "reports=groups&period=all"):
                form = ReportCenterFilterForm(QueryDict(query), user=self.general)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["account_status"], "active")
                self.assertEqual(form.cleaned_data["points_basis"], "recorded")
                filters = deepcopy(form.cleaned_data)
                report = build_reports(self.general, form.cleaned_data)[0]
                with self.assertNumQueries(0):
                    payload = build_group_comparison(report)
                self.assertEqual(form.cleaned_data, filters)
                self.assertEqual(self.point(payload, "submitters")["denominator"], 3)
                if form.cleaned_data["period"] == "all":
                    self.assertIsNone(form.cleaned_data["start_date"])
                    self.assertIsNone(form.cleaned_data["end_date"])
                    self.assert_point(payload, "net_average", 51, 3, 17.0)
                elif form.cleaned_data["period"] == "week":
                    self.assertEqual(form.cleaned_data["start_date"], datetime.date(2026, 10, 4))
                else:
                    self.assertEqual(form.cleaned_data["start_date"], datetime.date(2026, 10, 1))

    def test_recorded_and_event_basis_use_signed_ledger_not_balances_or_guessed_nulls(self):
        recorded = build_group_comparison(self.report())
        event = build_group_comparison(self.report(points_basis="event"))
        self.assert_point(recorded, "net_average", 21, 3, 7.0)
        self.assert_point(event, "net_average", 36, 3, 12.0)
        for key in KEYS[:5]:
            self.assertEqual(self.series(recorded, key), self.series(event, key))
        self.assertEqual(PointsLedgerEntry.objects.filter(event_date__isnull=True).count(), 1)
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (900, 9000, 800))

    def test_empty_scope_null_bucket_and_empty_period_have_explicit_unavailable_values(self):
        payload = build_group_comparison(self.report())
        for key in KEYS:
            self.assert_point(payload, key, 0, 0, None, self.empty_group.name)
        self.assert_point(payload, "submitters", 0, 1, 0.0, "بدون بيئة")
        self.assert_point(payload, "meeting", 0, 0, None, "بدون بيئة")
        empty = build_group_comparison(self.report(group=self.empty_group.pk))
        self.assertTrue(all(len(series["points"]) == 1 for series in empty["series"]))
        future = build_group_comparison(self.report(start_date="2027-01-01", end_date="2027-01-02"))
        for key in KEYS[:4]:
            self.assert_point(future, key, 0, 0, None)
        self.assert_point(future, "submitters", 0, 3, 0.0)
        self.assert_point(future, "net_average", 0, 3, 0.0)

    def test_private_and_detailed_reports_produce_only_identical_group_payloads(self):
        plain = build_group_comparison(self.report())
        report = self.report(detail="detailed", include_private="on")
        before = deepcopy(report)
        self.assertIn(self.student.user.national_id, repr(report))
        self.assertIn("ملاحظة لقاء خاصة", repr(report))
        with self.assertNumQueries(0):
            payload = build_group_comparison(report)
        self.assertEqual(payload, plain)
        self.assertEqual(report, before)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        for private in (self.student.user.full_name, self.student.user.national_id, self.student.phone,
                        self.student.guardian_phone, "ملاحظة لقاء خاصة", "وصف دفتر خاص", "محتوى التسليم الخاص",
                        "detail_url", "participant_id", "/student/"):
            self.assertNotIn(private, encoded)
        self.assertEqual(set(report), {"key", "title", "description", "metrics", "notes", "sections"})

    def test_adapter_adds_zero_queries_and_no_writes_or_source_mutation(self):
        filters = self.filters(detail="detailed", include_private="on")
        with CaptureQueriesContext(connection) as baseline:
            source = build_reports(self.general, filters)[0]
        before = deepcopy(source)
        first_date = TaskSubmission.objects.get(pk=self.submissions[0].pk).submitted_at
        with CaptureQueriesContext(connection) as adapted:
            repeated = build_reports(self.general, filters)[0]
            build_group_comparison(repeated)
        self.assertEqual([q["sql"] for q in adapted], [q["sql"] for q in baseline])
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for q in adapted))
        with self.assertNumQueries(0):
            build_group_comparison(source)
            build_group_comparison(source)
        self.assertEqual(source, before)
        self.assertEqual(repeated, before)
        self.assertEqual(TaskSubmission.objects.get(pk=self.submissions[0].pk).submitted_at, first_date)
        self.assertEqual(PointsLedgerEntry.objects.filter(event_date__isnull=True).count(), 1)
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (900, 9000, 800))
