"""Isolated integration checks for the general-supervisor report centre."""
import datetime
from io import BytesIO
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import Role, User
from .models import (
    CircleAttendance, Group, MeetingAttendance, MeetingNote, Participant,
    PointsLedgerEntry, TaskSubmission, WeeklyActivityAttendance, WeeklyTask,
)
from .report_center_views import ReportCenterView, ReportCenterExportView
from .views import build_navbar


class ReportCenterIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username='centre-general', role=Role.GENERAL_SUPERVISOR, password='synthetic-only')
        cls.admin = User.objects.create_user(username='centre-system', role=Role.SUPERADMIN, password='synthetic-only')
        cls.supervisor = User.objects.create_user(username='centre-group', role=Role.GROUP_SUPERVISOR, password='synthetic-only')
        cls.inactive_general = User.objects.create_user(username='centre-inactive', role=Role.GENERAL_SUPERVISOR, password='synthetic-only', is_active=False)
        cls.group = Group.objects.create(name='بيئة مركز التقارير الأولى')
        cls.other_group = Group.objects.create(name='بيئة مركز التقارير الثانية')
        cls.group.supervisor.add(cls.supervisor)
        cls.student = cls.make_student('8888880001', 'طالب المركز الأول', cls.group)
        cls.other = cls.make_student('8888880002', 'طالب المركز الآخر', cls.other_group)
        cls.inactive = cls.make_student('8888880003', 'طالب معطل في المركز', cls.group, active=False)
        cls.day = datetime.date(2026, 10, 2)
        cls.task = WeeklyTask.objects.create(title='مهمة مركز التقارير', description='وصف اختباري فقط', due_date=cls.day, allowed_formats='text', is_active=False)
        cls.submission = TaskSubmission.objects.create(participant=cls.student, task=cls.task, text_content='نص تسليم مصطنع', status=TaskSubmission.Status.ACCEPTED, is_featured=True, points_awarded=True, award_value=12)
        TaskSubmission.objects.filter(pk=cls.submission.pk).update(submitted_at=datetime.datetime(2026, 10, 2, 10, tzinfo=datetime.timezone.utc))
        CircleAttendance.objects.create(participant=cls.student, date=cls.day, attended=True, achieved=True)
        MeetingAttendance.objects.create(participant=cls.student, week_start_date=cls.day, attended=False, is_early=True)
        WeeklyActivityAttendance.objects.create(participant=cls.student, date=cls.day, attended=True)
        MeetingNote.objects.create(participant=cls.student, meeting_date=cls.day, text='ملاحظة خاصة مصطنعة لا تصدر افتراضيًا', created_by=cls.general, updated_by=cls.general)
        cls.entry = PointsLedgerEntry.objects.create(participant=cls.student, points_delta=12, source=PointsLedgerEntry.Source.WEEKLY_TASK, description='بيانات اختبار', event_date=cls.day, granted_by=cls.general)
        PointsLedgerEntry.objects.filter(pk=cls.entry.pk).update(created_at=datetime.datetime(2026, 10, 2, 10, tzinfo=datetime.timezone.utc))

    @classmethod
    def make_student(cls, identity, name, group, *, active=True):
        user = User.objects.create_user(national_id=identity, full_name=name, role=Role.PARTICIPANT, password='synthetic-only', is_active=active)
        return Participant.objects.create(user=user, group=group, academic_stage='grade_7', points=123, miles=1230, purchase_points=123, phone='0555557788', guardian_phone='0555558899')

    def setUp(self):
        self.client.force_login(self.general)

    def params(self, **extra):
        data = {'period': 'custom', 'start_date': '2026-10-01', 'end_date': '2026-10-03', 'account_status': 'active', 'detail': 'detailed', 'points_basis': 'recorded', 'reports': ['summary', 'students', 'attendance', 'tasks', 'groups']}
        data.update(extra)
        return data

    def page(self, **extra):
        return self.client.get(reverse('participants:report_center'), self.params(**extra))

    def export(self, **extra):
        data = self.params(format='xlsx')
        data.update(extra)
        return self.client.get(reverse('participants:report_center_export'), data)

    @staticmethod
    def result_text(response):
        return str(response.context['reports'])

    @staticmethod
    def workbook_text(response):
        workbook = load_workbook(BytesIO(response.content))
        return '\n'.join(str(cell.value) for sheet in workbook for row in sheet for cell in row if cell.value is not None)

    def test_general_has_the_only_new_nav_link_and_active_state(self):
        for actor in (self.general, self.admin, self.supervisor, self.student.user, self.inactive_general):
            with self.subTest(role=actor.role, active=actor.is_active):
                items = build_navbar(actor, 'report_center')
                centre = [item for item in items if item['key'] == 'report_center']
                self.assertEqual(len(centre), int(actor == self.general))
                if centre:
                    self.assertTrue(centre[0]['active'])
                    self.assertEqual(centre[0]['url'], reverse('participants:report_center'))
                    self.assertEqual(centre[0]['group'], 'students')
                    self.assertFalse(centre[0]['mobile_primary'])

    def test_anonymous_both_endpoints_redirect_to_supervisor_login(self):
        self.client.logout()
        for route in ('report_center', 'report_center_export'):
            response = self.client.get(reverse('participants:' + route), self.params(format='xlsx'))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse('accounts:login_supervisor'), response['Location'])

    def test_other_roles_are_forbidden_from_html_and_both_exports(self):
        for actor in (self.admin, self.supervisor, self.student.user):
            self.client.force_login(actor)
            for route, fmt in (('report_center', 'xlsx'), ('report_center_export', 'xlsx'), ('report_center_export', 'pdf')):
                with self.subTest(role=actor.role, route=route, fmt=fmt):
                    response = self.client.get(reverse('participants:' + route), self.params(format=fmt))
                    self.assertEqual(response.status_code, 403)

    def test_inactive_actor_is_rejected_even_as_authenticated_request_user(self):
        for view in (ReportCenterView, ReportCenterExportView):
            request = RequestFactory().get('/participants/reports/', self.params(format='xlsx'))
            request.user = self.inactive_general
            response = view.as_view()(request)
            self.assertEqual(response.status_code, 403)
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])

    def test_invalidated_inactive_session_cannot_read_exports(self):
        self.client.force_login(self.inactive_general)
        self.assertIn(self.export().status_code, (302, 403))

    def test_read_only_methods_reject_post_on_both_routes(self):
        for route in ('report_center', 'report_center_export'):
            response = self.client.post(reverse('participants:' + route), self.params(format='xlsx'))
            self.assertEqual(response.status_code, 405)

    def test_all_five_reports_render_and_share_applied_settings(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['filters_valid'])
        self.assertEqual([r['key'] for r in response.context['reports']], ['summary', 'students', 'attendance', 'tasks', 'groups'])
        self.assertContains(response, 'التقارير')
        self.assertContains(response, 'report-center.css')
        self.assertContains(response, 'report-center.js')
        self.assertTrue(response.context['filter_labels'])
        self.assertTrue(response.context['generated_at'])

    def test_only_requested_report_is_built(self):
        response = self.page(reports=['students'])
        self.assertEqual([r['key'] for r in response.context['reports']], ['students'])

    def test_summary_participation_counts_each_student_once_not_all_current_accounts(self):
        CircleAttendance.objects.create(participant=self.other, date=self.day, attended=False, achieved=False)
        response = self.page(reports=['summary'])
        metrics = {metric['label']: metric['value'] for metric in response.context['reports'][0]['metrics']}
        self.assertEqual(metrics['الطلاب في النطاق الحالي'], 2)
        self.assertEqual(metrics['طلاب لديهم مشاركة مسجلة في الفترة'], 1)
        response = self.page(reports=['summary'], start_date='2026-11-01', end_date='2026-11-02')
        metrics = {metric['label']: metric['value'] for metric in response.context['reports'][0]['metrics']}
        self.assertEqual(metrics['الطلاب في النطاق الحالي'], 2)
        self.assertEqual(metrics['طلاب لديهم مشاركة مسجلة في الفترة'], 0)

    def test_default_page_and_all_time_are_valid(self):
        default = self.client.get(reverse('participants:report_center'))
        self.assertEqual(default.status_code, 200)
        self.assertTrue(default.context['filters_valid'])
        self.assertEqual([r['key'] for r in default.context['reports']], ['summary'])
        self.assertTrue(self.page(period='all').context['filters_valid'])

    def test_group_and_student_filter_apply_to_result_rows_not_merely_ui(self):
        response = self.page(group=self.group.pk, participant=self.student.pk)
        self.assertIn(self.student.user.full_name, self.result_text(response))
        self.assertNotIn(self.other.user.full_name, self.result_text(response))
        self.assertNotIn(self.inactive.user.full_name, self.result_text(response))

    def test_invalid_filters_never_return_broad_results_or_files(self):
        for invalid in ({'start_date': '2026-10-04', 'end_date': '2026-10-01'}, {'period': 'unexpected'}, {'group': 'not-an-id'}, {'participant': 9999999}, {'reports': ['invented']}, {'account_status': 'invalid'}, {'detail': 'invalid'}, {'points_basis': 'invalid'}, {'task': 9999999}, {'group': self.other_group.pk, 'participant': self.student.pk}):
            with self.subTest(invalid=invalid):
                response = self.page(**invalid)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['filters_valid'])
                self.assertEqual(response.context['reports'], [])
                export = self.export(**invalid)
                self.assertEqual(export.status_code, 400)
                self.assertNotEqual(export.get('Content-Type'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_sensitive_identity_contact_and_notes_are_hidden_by_default(self):
        response = self.page(task=self.task.pk)
        for secret in (self.student.user.national_id, self.student.phone, self.student.guardian_phone, 'ملاحظة خاصة مصطنعة لا تصدر افتراضيًا'):
            self.assertNotContains(response, secret)
            self.assertNotIn(secret, self.workbook_text(self.export(task=self.task.pk)))

    def test_explicit_private_option_includes_identity_and_contacts(self):
        response = self.page(include_private='on', reports=['students'])
        text = self.result_text(response)
        self.assertIn(self.student.user.national_id, text)
        self.assertIn(self.student.phone, text)
        workbook = self.workbook_text(self.export(include_private='on', reports=['students']))
        self.assertIn(self.student.user.national_id, workbook)
        self.assertIn(self.student.phone, workbook)

    def test_filters_and_repeated_report_choices_survive_pagination(self):
        response = self.page(group=self.group.pk, reports=['students', 'tasks'], page=2)
        query = response.context['pagination_query']
        self.assertIn(f'group={self.group.pk}', query)
        self.assertIn('reports=students', query)
        self.assertIn('reports=tasks', query)
        self.assertNotIn('page=', query)

    def test_xlsx_is_downloadable_private_and_contains_all_selected_sheets(self):
        response = self.export()
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        workbook = load_workbook(BytesIO(response.content))
        self.assertEqual(len(workbook.sheetnames), 6)
        self.assertTrue(all(sheet.sheet_view.rightToLeft for sheet in workbook))
        for result in (self.page(), response):
            self.assertIn('private', result['Cache-Control'])
            self.assertIn('no-store', result['Cache-Control'])

    def test_pdf_endpoint_uses_same_filters_and_download_headers(self):
        with mock.patch('participants.report_center_exports.export_reports_pdf', return_value=b'%PDF-1.7\nsynthetic') as render:
            response = self.export(format='pdf', reports=['students'], group=self.group.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        args = render.call_args.args
        self.assertEqual([r['key'] for r in args[0]], ['students'])
        self.assertNotIn(self.other.user.full_name, str(args[0]))

    def test_unknown_export_format_is_rejected(self):
        for fmt in ('csv', 'html', 'xml', 'invalid'):
            self.assertEqual(self.export(format=fmt).status_code, 400)

    def test_export_failures_are_arabic_private_and_not_partial_downloads(self):
        for fmt in ('pdf', 'xlsx'):
            with mock.patch('participants.report_center_exports.export_reports_' + fmt, side_effect=OSError('sensitive test-only detail')):
                response = self.export(format=fmt)
            self.assertEqual(response.status_code, 503)
            self.assertIn('تعذر إنشاء ملف التقرير كاملًا', response.content.decode())
            self.assertNotIn('sensitive test-only detail', response.content.decode())
            self.assertNotIn('Content-Disposition', response)
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])

    def test_saved_attendance_is_one_section_per_source_even_with_multiple_rows(self):
        CircleAttendance.objects.create(participant=self.other, date=self.day, attended=False, achieved=True)
        response = self.page(reports=['attendance'])
        sections = response.context['reports'][0]['sections']
        saved = [section for section in sections if section['title'].endswith(' — السجلات المحفوظة')]
        self.assertEqual(len(saved), 3)
        self.assertEqual(len({section['title'] for section in saved}), 3)
        self.assertEqual(len(saved[0]['rows']), 2)
        workbook = load_workbook(BytesIO(self.export(reports=['attendance']).content))
        texts = [cell.value for sheet in workbook for row in sheet for cell in row if cell.value]
        for section in saved:
            self.assertEqual(texts.count(section['title']), 1)

    def test_gets_and_exports_do_not_change_domain_records_or_awards(self):
        def snapshot():
            return (
                tuple(Participant.objects.order_by('pk').values_list('pk', 'points', 'miles', 'purchase_points')),
                tuple(TaskSubmission.objects.order_by('pk').values_list('pk', 'status', 'points_awarded', 'award_value', 'submitted_at', 'reviewed_at')),
                tuple(PointsLedgerEntry.objects.order_by('pk').values_list('pk', 'points_delta', 'event_date', 'created_at')),
                CircleAttendance.objects.count(), MeetingAttendance.objects.count(), WeeklyActivityAttendance.objects.count(), MeetingNote.objects.count(),
            )
        before = snapshot()
        for params in ({}, {'include_private': 'on'}, {'points_basis': 'event'}, {'account_status': 'all'}, {'task': self.task.pk}):
            self.assertEqual(self.page(**params).status_code, 200)
            self.assertEqual(self.export(**params).status_code, 200)
        self.assertEqual(snapshot(), before)

    def test_export_is_not_limited_to_current_page(self):
        for index in range(51):
            self.make_student(str(8888881000 + index), f'طالب تصدير محفوظ {index:02d}', self.group)
        response = self.page(reports=['students'], group=self.group.pk)
        sections = response.context['reports'][0]['sections']
        self.assertTrue(any(section.get('page_obj') and section['page_obj'].paginator.count > 50 for section in sections))
        workbook = self.workbook_text(self.export(reports=['students'], group=self.group.pk, page=2))
        for index in range(51):
            self.assertIn(f'طالب تصدير محفوظ {index:02d}', workbook)

    def test_escaped_untrusted_names_titles_and_notes_do_not_execute_html(self):
        bad = '<script>window.reportLeak=1</script>'
        self.student.user.full_name = bad
        self.student.user.save(update_fields=['full_name'])
        self.task.title = bad
        self.task.save(update_fields=['title'])
        self.submission.status = TaskSubmission.Status.REJECTED
        self.submission.rejection_reason = bad
        self.submission.save(update_fields=['status', 'rejection_reason'])
        response = self.page(task=self.task.pk, include_private='on')
        self.assertNotContains(response, bad)
        self.assertContains(response, '&lt;script&gt;window.reportLeak=1&lt;/script&gt;')

    def test_old_student_report_access_is_preserved_for_existing_supervisor_roles(self):
        for actor in (self.general, self.admin, self.supervisor):
            self.client.force_login(actor)
            self.assertEqual(self.client.get(reverse('participants:student_report')).status_code, 200)
