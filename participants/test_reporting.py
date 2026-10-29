"""Reporting regression tests. Run with config.test_settings, not the managed DB."""
import datetime
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from .models import (
    CircleAttendance, Group, MeetingAttendance, Participant, PointsLedgerEntry,
    TaskSubmission, WeeklyActivityAttendance, WeeklyTask,
)
from .reporting import StudentReportFilterForm, build_student_report

UTC = datetime.timezone.utc


@override_settings(TIME_ZONE="Asia/Riyadh")
class StudentReportingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username="report-general", role=Role.GENERAL_SUPERVISOR, password="test-only")
        cls.admin = User.objects.create_user(username="report-admin", role=Role.SUPERADMIN, password="test-only")
        cls.supervisor = User.objects.create_user(username="report-group", role=Role.GROUP_SUPERVISOR, password="test-only")
        cls.orphan = User.objects.create_user(username="report-orphan", role=Role.GROUP_SUPERVISOR, password="test-only")
        cls.group = Group.objects.create(name="بيئة التقرير الأولى")
        cls.other_group = Group.objects.create(name="بيئة التقرير الثانية")
        cls.group.supervisor.add(cls.supervisor)
        cls.student = cls.make_student("8000000001", "الطالب الأول", cls.group, points=123)
        cls.other = cls.make_student("8000000002", "الطالب الآخر", cls.other_group)
        cls.quiet = cls.make_student("8000000003", "طالب دون نشاط", cls.group)
        cls.ungrouped = cls.make_student("8000000004", "طالب دون بيئة", None)
        for day, attended, achieved in [(1, True, True), (2, True, False), (3, False, True), (4, True, True)]:
            CircleAttendance.objects.create(participant=cls.student, date=datetime.date(2026, 10, day), attended=attended, achieved=achieved)
        CircleAttendance.objects.create(participant=cls.other, date=datetime.date(2026, 10, 2), attended=True, achieved=True)
        MeetingAttendance.objects.create(participant=cls.student, week_start_date=datetime.date(2026, 10, 2), attended=True)
        WeeklyActivityAttendance.objects.create(participant=cls.student, date=datetime.date(2026, 10, 3), attended=True)
        specs = [
            (1, 21, 0, 0, TaskSubmission.Status.ACCEPTED),
            (3, 20, 59, 59, TaskSubmission.Status.PENDING),
            (2, 9, 0, 0, TaskSubmission.Status.REJECTED),
            (3, 21, 0, 0, TaskSubmission.Status.ACCEPTED),
            (1, 20, 59, 59, TaskSubmission.Status.PENDING),
        ]
        for index, (day, hour, minute, second, status) in enumerate(specs):
            task = WeeklyTask.objects.create(title=f"مهمة تقرير {index}", description="اختبار", due_date=datetime.date(2026, 10, 15), allowed_formats="text")
            submission = TaskSubmission.objects.create(participant=cls.student, task=task, text_content="نص اختباري", status=status)
            TaskSubmission.objects.filter(pk=submission.pk).update(submitted_at=datetime.datetime(2026, 10, day, hour, minute, second, tzinfo=UTC))
        cls.deltas = [5, -2, 12, 8, 10, 7, 7, -4]
        sources = [
            PointsLedgerEntry.Source.QURAN_CIRCLE, PointsLedgerEntry.Source.QURAN_CIRCLE,
            PointsLedgerEntry.Source.WEEKLY_TASK, PointsLedgerEntry.Source.MEETING_ATTENDANCE,
            PointsLedgerEntry.Source.WEEKLY_ACTIVITY, PointsLedgerEntry.Source.EXTRA,
            PointsLedgerEntry.Source.EXTRA, PointsLedgerEntry.Source.EXTRA,
        ]
        for delta, source in zip(cls.deltas, sources):
            entry = PointsLedgerEntry.objects.create(participant=cls.student, points_delta=delta, source=source, description="اختبار")
            PointsLedgerEntry.objects.filter(pk=entry.pk).update(created_at=datetime.datetime(2026, 10, 2, 10, tzinfo=UTC))
        outside = PointsLedgerEntry.objects.create(participant=cls.student, points_delta=100, source=PointsLedgerEntry.Source.EXTRA, description="خارج الفترة")
        PointsLedgerEntry.objects.filter(pk=outside.pk).update(created_at=datetime.datetime(2026, 10, 3, 21, tzinfo=UTC))

    @classmethod
    def make_student(cls, identity, name, group, **kwargs):
        user = User.objects.create_user(national_id=identity, full_name=name, role=Role.PARTICIPANT, password="test-only")
        return Participant.objects.create(user=user, group=group, academic_stage="grade_7", **kwargs)

    def filters(self, **extra):
        filters = {"start_date": datetime.date(2026, 10, 2), "end_date": datetime.date(2026, 10, 3)}
        filters.update(extra)
        return filters

    def get_report(self, user=None, **params):
        self.client.force_login(user or self.general)
        return self.client.get(reverse("participants:student_report"), params)

    def test_circle_attendance_and_achievement_are_independent(self):
        row = next(row for row in build_student_report(self.general, self.filters()) if row["participant"].pk == self.student.pk)
        self.assertEqual(row["circle_attended"], 1)
        self.assertEqual(row["circle_achieved"], 1)

    def test_tasks_use_local_inclusive_days_and_include_all_statuses(self):
        row = build_student_report(self.general, self.filters(q="الطالب الأول"))[0]
        self.assertEqual(row["tasks_submitted"], 3)
        self.assertEqual([row["tasks_accepted"], row["tasks_pending"], row["tasks_rejected"]], [1, 1, 1])

    def test_single_day_covers_local_midnight_not_utc_date(self):
        rows = build_student_report(self.general, self.filters(end_date=datetime.date(2026, 10, 2), q="الطالب الأول"))
        self.assertEqual(rows[0]["tasks_submitted"], 2)

    def test_real_signed_ledger_sums_do_not_multiply_or_drop_equal_deltas(self):
        row = build_student_report(self.general, self.filters(q="الطالب الأول"))[0]
        self.assertEqual(row["net_points"], sum(self.deltas))
        self.assertEqual(row["points_circle"], 3)
        self.assertEqual(row["points_tasks"], 12)
        self.assertEqual(row["points_meetings"], 8)
        self.assertEqual(row["points_activities"], 10)
        self.assertEqual(row["points_extra"], 10)
        self.assertEqual(row["participant"].points, 123)

    def test_zero_activity_students_stay_visible_with_zero_counts(self):
        rows = build_student_report(self.general, self.filters(q="طالب دون نشاط"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["circle_attended"], 0)
        self.assertEqual(rows[0]["tasks_submitted"], 0)
        self.assertEqual(rows[0]["net_points"], 0)

    def test_current_balance_remains_independent_of_window_and_reading_does_not_write(self):
        before = (Participant.objects.count(), TaskSubmission.objects.count(), PointsLedgerEntry.objects.count())
        row = build_student_report(self.general, self.filters(start_date=datetime.date(2027, 1, 1), end_date=None, q="الطالب الأول"))[0]
        self.assertEqual(row["net_points"], 0)
        self.assertEqual(row["participant"].points, 123)
        self.assertEqual(before, (Participant.objects.count(), TaskSubmission.objects.count(), PointsLedgerEntry.objects.count()))

    def test_blank_and_one_sided_date_filters(self):
        self.assertEqual(build_student_report(self.general, {"q": "الطالب الأول"})[0]["tasks_submitted"], 5)
        self.assertEqual(build_student_report(self.general, {"q": "الطالب الأول", "end_date": datetime.date(2026, 10, 1)})[0]["tasks_submitted"], 1)
        self.assertEqual(build_student_report(self.general, {"q": "الطالب الأول", "start_date": datetime.date(2026, 10, 4)})[0]["tasks_submitted"], 1)

    def test_general_and_system_roles_see_every_student_and_active_nav_link(self):
        for user in [self.general, self.admin]:
            with self.subTest(role=user.role):
                response = self.get_report(user)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["totals"]["students"], 4)
                self.assertEqual([item["key"] for item in response.context["navbar_items"] if item["active"]], ["student_report"])
                self.assertIn("private", response.headers["Cache-Control"])
                self.assertIn("no-store", response.headers["Cache-Control"])

    def test_group_supervisor_is_scoped_and_cannot_spoof_another_group(self):
        response = self.get_report(self.supervisor)
        self.assertEqual(response.context["totals"]["students"], 2)
        self.assertNotContains(response, "الطالب الآخر")
        forged = self.get_report(self.supervisor, group=self.other_group.pk)
        self.assertFalse(forged.context["filters_valid"])
        self.assertEqual(forged.context["totals"]["students"], 0)
        self.assertNotContains(forged, "الطالب الآخر")
        self.assertNotContains(forged, self.other_group.name)

    def test_multiple_assigned_groups_are_visible_and_unassigned_are_not(self):
        self.other_group.supervisor.add(self.supervisor)
        response = self.get_report(self.supervisor)
        self.assertEqual(response.context["totals"]["students"], 3)
        self.assertNotContains(response, "طالب دون بيئة")

    def test_supervisor_without_environment_sees_nothing(self):
        response = self.get_report(self.orphan)
        self.assertEqual(response.context["totals"]["students"], 0)
        self.assertNotContains(response, "الطالب الأول")

    def test_anonymous_is_redirected_and_participant_is_forbidden(self):
        self.assertEqual(self.client.get(reverse("participants:student_report")).status_code, 302)
        self.assertEqual(self.get_report(self.student.user).status_code, 403)

    def test_invalid_and_reversed_dates_show_errors_not_unfiltered_results(self):
        for params in [{"start_date": "nonsense"}, {"end_date": "2026-02-30"}, {"start_date": "2026-10-04", "end_date": "2026-10-01"}]:
            with self.subTest(params=params):
                response = self.get_report(**params)
                self.assertFalse(response.context["filters_valid"])
                self.assertEqual(response.context["totals"]["students"], 0)
                self.assertNotContains(response, "الطالب الأول")
                self.assertContains(response, "صحح الفلاتر")

    def test_search_and_group_filters_apply_to_counts_top_list_and_rows(self):
        response = self.get_report(group=self.group.pk, q="8000000001", start_date="2026-10-02", end_date="2026-10-03")
        self.assertEqual(response.context["totals"]["students"], 1)
        self.assertEqual(response.context["totals"]["submitted"], 3)
        self.assertEqual(response.context["totals"]["net_points"], sum(self.deltas))
        self.assertEqual(len(response.context["top_students"]), 1)
        self.assertNotContains(response, "الطالب الآخر")

    def test_top_fifteen_and_pagination_keep_filters_and_all_student_totals(self):
        for i in range(16):
            student = self.make_student(str(8000000100 + i), f"طالب ترتيب {i}", self.group)
            PointsLedgerEntry.objects.create(participant=student, points_delta=i + 1, source=PointsLedgerEntry.Source.EXTRA, description="اختبار ترتيب")
        for i in range(36):
            self.make_student(str(8000000200 + i), f"طالب صفحة {i}", self.group)
        response = self.get_report(group=self.group.pk, page="2")
        self.assertEqual(response.context["totals"]["students"], 54)
        self.assertEqual(len(response.context["page_obj"].object_list), 4)
        self.assertEqual(len(response.context["top_students"]), 15)
        self.assertGreaterEqual(response.context["top_students"][0]["net_points"], response.context["top_students"][-1]["net_points"])
        self.assertIn(f"group={self.group.pk}", response.context["pagination_query"])
        self.assertNotIn("page=", response.context["pagination_query"])
        self.assertContains(response, "أبرز 15 طالبًا")

    def test_sql_query_count_is_constant_not_per_student(self):
        with self.assertNumQueries(6):
            rows = build_student_report(self.general, self.filters())
        self.assertEqual(len(rows), 4)

    def test_unknown_and_oversized_filters_fail_safely(self):
        for params in [{"group": "not-an-id"}, {"q": "x" * 101}]:
            response = self.get_report(**params)
            self.assertFalse(response.context["filters_valid"])
            self.assertEqual(response.context["totals"]["students"], 0)

    def test_edge_dates_do_not_overflow_end_day(self):
        form = StudentReportFilterForm({"end_date": "9999-12-31"}, user=self.general)
        self.assertTrue(form.is_valid())
        self.assertEqual(len(build_student_report(self.general, form.cleaned_data)), 4)

    def test_dates_before_database_range_are_reported_as_filter_errors(self):
        for field in ["start_date", "end_date"]:
            with self.subTest(field=field):
                response = self.get_report(**{field: "0001-01-01"})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["filters_valid"])
                self.assertEqual(response.context["totals"]["students"], 0)
                self.assertContains(response, "يجب ألا يكون التاريخ قبل 1000/01/01.")

    def test_report_never_accepts_post_requests(self):
        self.client.force_login(self.general)
        self.assertEqual(self.client.post(reverse("participants:student_report")).status_code, 405)

    def test_reopened_task_remains_one_submission_with_its_first_date(self):
        submission = TaskSubmission.objects.filter(participant=self.student).first()
        first_date = submission.submitted_at
        submission.reopened_for_resubmission = True
        submission.save()
        row = build_student_report(self.general, {"q": "الطالب الأول"})[0]
        self.assertEqual(row["tasks_submitted"], 5)
        submission.refresh_from_db()
        self.assertEqual(submission.submitted_at, first_date)
