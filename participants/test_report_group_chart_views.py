"""Synthetic integration tests for full, private report-group charts."""
import datetime
import json

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import Group, MeetingAttendance, Participant, PointsLedgerEntry


class ReportGroupChartViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username="chart-general", role=Role.GENERAL_SUPERVISOR, password="test-only")
        cls.admin = User.objects.create_user(username="chart-admin", role=Role.SUPERADMIN, password="test-only")
        cls.supervisor = User.objects.create_user(username="chart-supervisor", role=Role.GROUP_SUPERVISOR, password="test-only")
        cls.disabled = User.objects.create_user(username="chart-disabled-general", role=Role.GENERAL_SUPERVISOR, is_active=False, password="test-only")
        cls.first_group = Group.objects.create(name="بيئة ألف")
        cls.second_group = Group.objects.create(name="بيئة باء")
        cls.empty_group = Group.objects.create(name="بيئة بلا طلاب")
        cls.first = cls.student("chart-student-first", cls.first_group, "اسم طالب خاص")
        cls.peer = cls.student("chart-student-peer", cls.first_group, "اسم طالب آخر")
        cls.other = cls.student("chart-student-other", cls.second_group, "اسم طالب بيئة ثانية")
        cls.day = datetime.date(2026, 10, 2)
        for student, attended in [(cls.first, True), (cls.peer, False), (cls.other, True)]:
            MeetingAttendance.objects.create(participant=student, week_start_date=cls.day, attended=attended)
        entry = PointsLedgerEntry.objects.create(participant=cls.first, points_delta=-10, source=PointsLedgerEntry.Source.EXTRA, event_date=cls.day, description="وصف خاص لا يدخل الرسم")
        PointsLedgerEntry.objects.filter(pk=entry.pk).update(created_at=datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.timezone.utc))

    @staticmethod
    def student(username, group, name):
        user = User.objects.create_user(username=username, full_name=name, role=Role.PARTICIPANT, password="test-only")
        return Participant.objects.create(user=user, group=group, academic_stage="grade_7", phone="0500000077", guardian_phone="0550000077")

    def page(self, **extra):
        params = {"period": "custom", "start_date": "2026-10-02", "end_date": "2026-10-03", "reports": ["groups"]}
        params.update(extra)
        self.client.force_login(self.general)
        return self.client.get(reverse("participants:report_center"), params)

    def test_general_dashboard_exposes_comparison_entry_not_admin_dashboard(self):
        url = reverse("participants:general_supervisor_dashboard")
        self.client.force_login(self.general)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "فتح المقارنة البيانية")
        self.assertTrue(response.context["can_compare_groups"])
        self.assertContains(response, "period=month&amp;reports=groups")
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_compare_groups"])
        self.assertNotContains(response, "فتح المقارنة البيانية")

    def test_selected_groups_report_has_complete_chart_with_shared_source_values(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        chart = response.context["group_comparison"]
        self.assertEqual([series["key"] for series in chart["series"]], ["meeting", "circle", "achievement", "activity", "submitters", "net_average"])
        series = {item["key"]: item for item in chart["series"]}
        point = next(item for item in series["meeting"]["points"] if item["name"] == self.first_group.name)
        self.assertEqual((point["value"], point["numerator"], point["denominator"]), (50.0, 1, 2))
        point = next(item for item in series["net_average"]["points"] if item["name"] == self.first_group.name)
        self.assertEqual((point["value"], point["numerator"], point["denominator"]), (-5.0, -10, 2))
        empty = next(item for item in series["meeting"]["points"] if item["name"] == self.empty_group.name)
        self.assertIsNone(empty["value"])
        self.assertEqual(empty["denominator"], 0)
        self.assertContains(response, 'id="report-group-comparison-data"', count=1)
        self.assertContains(response, 'js/report-group-charts.js')
        self.assertContains(response, 'css/report-group-charts.css')
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_no_chart_for_summary_or_invalid_filters(self):
        for params in ({"reports": ["summary"]}, {"end_date": "2026-10-01"}, {"group": "999999"}, {"reports": ["invalid"]}):
            with self.subTest(params=params):
                response = self.page(**params)
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["group_comparison"])
                self.assertNotContains(response, 'id="report-group-comparison-data"')

    def test_period_and_group_filters_are_not_ignored_by_the_chart(self):
        response = self.page(group=self.second_group.pk)
        self.assertTrue(all([point["name"] for point in series["points"]] == [self.second_group.name] for series in response.context["group_comparison"]["series"]))
        response = self.page(start_date="2027-01-01", end_date="2027-01-02")
        series = {item["key"]: item for item in response.context["group_comparison"]["series"]}
        self.assertTrue(all(point["value"] is None for point in series["meeting"]["points"]))
        point = next(item for item in series["net_average"]["points"] if item["name"] == self.first_group.name)
        self.assertEqual(point["value"], 0.0)

    def test_group_chart_is_not_truncated_by_fifty_row_table_pages(self):
        Group.objects.bulk_create([Group(name=f"بيئة اختبار {index:03}") for index in range(53)])
        expected = Group.objects.count()
        for page in (1, 2):
            response = self.page(page=page)
            self.assertTrue(all(len(series["points"]) == expected for series in response.context["group_comparison"]["series"]))
            report = response.context["reports"][0]
            self.assertLessEqual(len(report["sections"][0]["rows"]), 50)
        response = self.page(reports=["summary", "groups", "students"])
        self.assertContains(response, 'id="report-group-comparison-data"', count=1)

    def test_group_name_json_script_is_escaped_and_payload_has_no_student_private_data(self):
        Group.objects.filter(pk=self.first_group.pk).update(name="بيئة </script><script>bad()</script>")
        response = self.page(include_private="on")
        chart = response.context["group_comparison"]
        content = json.dumps(chart, ensure_ascii=False)
        self.assertIn("</script>", content)
        self.assertContains(response, "\\u003C/script\\u003E")
        for private in (self.first.user.full_name, self.peer.user.full_name, self.first.phone, self.first.guardian_phone, "وصف خاص لا يدخل الرسم"):
            self.assertNotIn(private, content)

    def test_roles_cannot_obtain_chart_data_by_requesting_groups_report(self):
        for user in (self.admin, self.supervisor, self.disabled, self.first.user):
            with self.subTest(role=user.role, active=user.is_active):
                self.client.force_login(user)
                response = self.client.get(reverse("participants:report_center"), {"reports": "groups", "period": "all"})
                self.assertIn(response.status_code, (302, 403))
                self.assertNotIn(b'report-group-comparison-data', response.content)
