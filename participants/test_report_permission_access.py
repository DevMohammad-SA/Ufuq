"""Fresh report-policy, scope and read-only regressions on synthetic data only."""
import datetime
from contextlib import ExitStack
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Role, User
from .capabilities import (
    can_prepare_reports, can_view_report_center, can_view_reports, current_report_access,
)
from .models import (
    CircleAttendance, Group, MeetingAttendance, MeetingNote, Participant,
    PointsLedgerEntry, SupervisorCapability, SupervisorReportPermission,
    TaskSubmission, WeeklyTask,
)
from .report_center import REPORT_CHOICES, ReportCenterFilterForm, build_reports
from .report_center_views import ReportCenterExportView, ReportCenterView
from .report_group_charts import build_group_comparison
from .report_views import StudentReportView
from .reporting import StudentReportFilterForm, allowed_groups, build_student_report, scoped_participants
from .student_views import StudentDetailView


class ReportPermissionAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = cls.actor("general", Role.GENERAL_SUPERVISOR)
        cls.system = cls.actor("system", Role.SUPERADMIN)
        cls.supervisor = cls.actor("group", Role.GROUP_SUPERVISOR)
        cls.limited = cls.actor("limited", Role.LIMITED_SUPERVISOR)
        cls.disabled = cls.actor("disabled", Role.GENERAL_SUPERVISOR, is_active=False)
        cls.group = Group.objects.create(name="البيئة المسموحة الأولى")
        cls.group2 = Group.objects.create(name="البيئة المسموحة الثانية")
        cls.outside = Group.objects.create(name="بيئة خارج النطاق")
        cls.group.supervisor.add(cls.supervisor)
        cls.group2.supervisor.add(cls.supervisor)
        cls.first = cls.student("first", cls.group)
        cls.second = cls.student("second", cls.group2)
        cls.foreign = cls.student("foreign", cls.outside)
        cls.ungrouped = cls.student("ungrouped", None)
        cls.nonstudent = Participant.objects.create(user=cls.system, group=cls.group)
        cls.day = datetime.date(2026, 10, 2)
        cls.task = WeeklyTask.objects.create(
            title="مهمة مصطنعة", description="اختبار فقط", due_date=cls.day, allowed_formats="text",
        )
        cls.submission = TaskSubmission.objects.create(
            participant=cls.first, task=cls.task, text_content="نص محفوظ", file="tasks/private-proof.pdf",
        )
        for student in (cls.first, cls.second, cls.foreign):
            CircleAttendance.objects.create(participant=student, date=cls.day, attended=True, achieved=True)
            MeetingAttendance.objects.create(participant=student, week_start_date=cls.day, attended=True)
            MeetingNote.objects.create(
                participant=student, meeting_date=cls.day, text="سر اللقاء " + student.user.username,
                created_by=cls.general, updated_by=cls.general,
            )
        PointsLedgerEntry.objects.create(
            participant=cls.first, points_delta=7, source=PointsLedgerEntry.Source.EXTRA,
            description="حركة مصطنعة", event_date=cls.day, granted_by=cls.general,
        )

    @classmethod
    def actor(cls, name, role, **extra):
        return User.objects.create_user(username="report-policy-" + name, role=role, password="test-only", **extra)

    @classmethod
    def student(cls, name, group):
        user = cls.actor(name, Role.PARTICIPANT, full_name="طالب " + name)
        return Participant.objects.create(
            user=user, group=group, academic_stage="grade_7", points=71, miles=710, purchase_points=65,
            phone="0501112222", guardian_phone="0503334444",
        )

    def setUp(self):
        self.factory = RequestFactory()
        # Navigation/templates/ParticipantsData/PDF belong to the parent agent;
        # exercise the owned report views without relying on concurrent UI work.
        for module in ("report_views", "student_views", "report_center_views"):
            self.enterContext(mock.patch("participants." + module + ".build_navbar", return_value=[]))

    def policy(self, actor, view=True, prepare=False):
        return SupervisorReportPermission.objects.update_or_create(
            supervisor=actor, defaults={"view_reports": view, "prepare_reports": prepare, "granted_by": self.general},
        )[0]

    def params(self, **extra):
        data = {"period": "all", "reports": [key for key, _ in REPORT_CHOICES],
                "account_status": "all", "detail": "detailed", "points_basis": "recorded"}
        data.update(extra)
        return data

    def filters(self, actor, **extra):
        form = ReportCenterFilterForm(self.params(**extra), user=actor)
        self.assertTrue(form.is_valid(), form.errors)
        return form.cleaned_data

    def response(self, view, actor, *, method="get", pk=None, **params):
        request = getattr(self.factory, method)("/synthetic-reports/", params)
        request.user = actor
        return view.as_view()(request, **({"pk": pk} if pk is not None else {}))

    def assert_private(self, response):
        self.assertIn("private", response.get("Cache-Control", ""))
        self.assertIn("no-store", response.get("Cache-Control", ""))

    def assert_no_report_queries(self, captured):
        for query in captured:
            sql = query["sql"].lower()
            for table in ("participants_participant", "participants_meetingnote", "participants_group",
                          "participants_weeklytask", "participants_tasksubmission", "participants_pointsledgerentry"):
                self.assertNotIn('from "' + table + '"', sql, sql)

    def test_all_role_defaults_preserve_legacy_access_and_only_general_center(self):
        cases = ((self.general, True, True, True), (self.system, True, True, False),
                 (self.supervisor, True, True, False), (self.limited, False, False, False),
                 (self.first.user, False, False, False), (self.disabled, False, False, False),
                 (AnonymousUser(), False, False, False))
        for actor, view, prepare, center in cases:
            with self.subTest(role=getattr(actor, "role", None)):
                self.assertEqual(can_view_reports(actor), view)
                self.assertEqual(can_prepare_reports(actor), prepare)
                self.assertEqual(can_view_report_center(actor), center)
                response = self.response(StudentReportView, actor)
                self.assertEqual(response.status_code, 200 if view else 302 if not actor.is_authenticated else 403)
                self.assert_private(response)
                response = self.response(ReportCenterView, actor)
                self.assertEqual(response.status_code, 200 if center else 302 if not actor.is_authenticated else 403)
                self.assert_private(response)
        for actor in (self.general, self.system, self.supervisor):
            self.assertEqual(self.response(StudentDetailView, actor, pk=self.first.pk).status_code, 200)

    def test_explicit_denials_block_all_owned_reads_and_clear_scopes(self):
        for actor in (self.general, self.supervisor, self.limited):
            self.policy(actor, view=False, prepare=False)
            with self.subTest(role=actor.role):
                self.assertFalse(can_view_reports(actor))
                self.assertEqual(list(allowed_groups(actor)), [])
                self.assertEqual(list(scoped_participants(actor)), [])
                with CaptureQueriesContext(connection) as captured:
                    self.assertEqual(build_student_report(actor, {}), [])
                    with self.assertRaises(PermissionDenied):
                        build_reports(actor, {"reports": ["summary"]})
                self.assert_no_report_queries(captured)
                for view in (StudentReportView, StudentDetailView, ReportCenterView, ReportCenterExportView):
                    response = self.response(view, actor, pk=self.first.pk if view == StudentDetailView else None, format="xlsx")
                    self.assertEqual(response.status_code, 403)
                    self.assert_private(response)

    def test_view_only_can_filter_read_but_pdf_xlsx_rejected_before_data_or_imports(self):
        for actor in (self.general, self.supervisor, self.limited):
            self.policy(actor)
            with self.subTest(role=actor.role):
                self.assertTrue(can_view_report_center(actor))
                self.assertFalse(can_prepare_reports(actor))
                response = self.response(ReportCenterView, actor, **self.params(group=self.group.pk))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context_data["filters_valid"])
                self.assertFalse(response.context_data["can_prepare_reports"])
                self.assertNotIn(self.foreign.user.full_name, repr(response.context_data["reports"]))
                for fmt in ("pdf", "xlsx"):
                    with ExitStack() as stack:
                        form = stack.enter_context(mock.patch("participants.report_center_views.ReportCenterFilterForm"))
                        builder = stack.enter_context(mock.patch("participants.report_center_views.build_reports"))
                        importer = stack.enter_context(mock.patch("builtins.__import__", wraps=__import__))
                        captured = stack.enter_context(CaptureQueriesContext(connection))
                        response = self.response(ReportCenterExportView, actor, format=fmt, **self.params())
                    self.assertEqual(response.status_code, 403)
                    self.assert_private(response)
                    form.assert_not_called()
                    builder.assert_not_called()
                    self.assertFalse(any(call.args and "report_center_exports" in call.args[0] for call in importer.call_args_list))
                    self.assert_no_report_queries(captured)

    def test_prepare_requires_view_even_for_inconsistent_policy_row(self):
        self.policy(self.general, view=False, prepare=True)
        self.assertFalse(can_prepare_reports(self.general))
        self.assertFalse(can_view_report_center(self.general))
        self.assertEqual(self.response(ReportCenterExportView, self.general, format="pdf").status_code, 403)

    def test_explicit_system_grant_never_enables_center_or_meeting_notes(self):
        self.policy(self.system, view=True, prepare=True)
        self.assertTrue(can_view_reports(self.system))
        self.assertFalse(can_view_report_center(self.system))
        response = self.response(StudentDetailView, self.system, pk=self.first.pk)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("سر اللقاء", repr(response.context_data))
        self.assertEqual(self.response(ReportCenterExportView, self.system, format="xlsx").status_code, 403)

    def test_stale_prefetched_policy_and_fake_legacy_flag_do_not_survive_revoke(self):
        policy = self.policy(self.limited, view=True, prepare=True)
        stale = User.objects.select_related("report_permissions").get(pk=self.limited.pk)
        self.assertTrue(stale.report_permissions.view_reports)
        stale.canReports = True
        self.assertTrue(can_prepare_reports(stale))
        SupervisorReportPermission.objects.filter(pk=policy.pk).update(view_reports=False, prepare_reports=False)
        self.assertFalse(can_view_reports(stale))
        self.assertEqual(list(scoped_participants(stale)), [])
        with mock.patch("participants.report_center_views.build_reports") as builder:
            response = self.response(ReportCenterExportView, stale, format="xlsx")
        self.assertEqual(response.status_code, 403)
        builder.assert_not_called()

    def test_stale_general_demotion_cannot_keep_all_scope_or_foreign_detail(self):
        self.policy(self.general, view=True, prepare=True)
        stale = User.objects.get(pk=self.general.pk)
        User.objects.filter(pk=stale.pk).update(role=Role.GROUP_SUPERVISOR)
        self.group.supervisor.add(stale)
        self.group2.supervisor.add(stale)
        self.assertEqual(stale.role, Role.GENERAL_SUPERVISOR)
        self.assertEqual(set(scoped_participants(stale).values_list("pk", flat=True)), {self.first.pk, self.second.pk})
        self.assertEqual(self.response(StudentDetailView, stale, pk=self.foreign.pk).status_code, 404)
        reports = build_reports(stale, self.filters(stale))
        self.assertNotIn(self.foreign.user.full_name, repr(reports))
        graph = build_group_comparison(next(report for report in reports if report["key"] == "groups"))
        self.assertTrue(all({p["name"] for p in series["points"]} == {self.group.name, self.group2.name}
                            for series in graph["series"]))

    def test_stale_activity_rechecked_in_both_directions(self):
        stale = User.objects.get(pk=self.general.pk)
        User.objects.filter(pk=stale.pk).update(is_active=False)
        self.assertFalse(can_view_reports(stale))
        self.assertEqual(self.response(StudentReportView, stale).status_code, 403)
        stale.is_active = False
        User.objects.filter(pk=stale.pk).update(is_active=True)
        self.assertTrue(can_view_reports(stale))

    def test_group_assignments_are_fresh_and_include_all_current_environments(self):
        self.policy(self.supervisor)
        stale = User.objects.prefetch_related("group_set").get(pk=self.supervisor.pk)
        self.assertEqual({group.pk for group in stale.group_set.all()}, {self.group.pk, self.group2.pk})
        self.group.supervisor.remove(stale)
        self.outside.supervisor.add(stale)
        self.assertEqual(set(allowed_groups(stale).values_list("pk", flat=True)), {self.group2.pk, self.outside.pk})
        self.assertEqual(set(scoped_participants(stale).values_list("pk", flat=True)), {self.second.pk, self.foreign.pk})
        self.assertEqual(self.response(StudentDetailView, stale, pk=self.first.pk).status_code, 404)

    def test_forged_group_participant_queries_never_expand_report_scope(self):
        self.policy(self.supervisor)
        for params in ({"group": self.outside.pk}, {"participant": self.foreign.pk},
                       {"group": self.group.pk, "participant": self.second.pk}):
            with self.subTest(params=params):
                form = ReportCenterFilterForm(self.params(**params), user=self.supervisor)
                self.assertFalse(form.is_valid())
                response = self.response(ReportCenterView, self.supervisor, **self.params(**params))
                self.assertEqual(response.context_data["reports"], [])
                self.assertIsNone(response.context_data["group_comparison"])
        form = StudentReportFilterForm({"group": self.outside.pk}, user=self.supervisor)
        self.assertFalse(form.is_valid())
        self.assertEqual(build_student_report(self.supervisor, {"group": self.outside}), [])
        for key in ("group", "participant"):
            filters = self.filters(self.supervisor)
            filters[key] = self.outside if key == "group" else self.foreign
            self.assertNotIn(self.foreign.user.full_name, repr(build_reports(self.supervisor, filters)))
        self.assertEqual(self.response(StudentDetailView, self.supervisor, pk=self.foreign.pk,
                                      task_page=1, ledger_page=1, note_page=1).status_code, 404)

    def test_limited_report_grant_covers_program_students_only_without_management_grant(self):
        self.policy(self.limited)
        self.assertEqual(set(scoped_participants(self.limited).values_list("pk", flat=True)),
                         {self.first.pk, self.second.pk, self.foreign.pk, self.ungrouped.pk})
        for student in (self.first, self.second, self.foreign, self.ungrouped):
            response = self.response(StudentDetailView, self.limited, pk=student.pk)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context_data["can_manage_students"])
            self.assertFalse(response.context_data["can_manage_accounts"])
        self.assertEqual(self.response(StudentDetailView, self.limited, pk=self.nonstudent.pk).status_code, 404)
        SupervisorCapability.objects.create(supervisor=self.limited, manage_students=True)
        self.assertTrue(self.response(StudentDetailView, self.limited, pk=self.first.pk).context_data["can_manage_students"])

    def test_limited_and_stale_role_cannot_read_meeting_note_text_even_include_private(self):
        for actor in (self.limited, self.general):
            self.policy(actor)
            if actor == self.general:
                # Cached GENERAL must not expose notes after persisted demotion.
                User.objects.filter(pk=actor.pk).update(role=Role.LIMITED_SUPERVISOR)
            with self.subTest(actor=actor.pk):
                with CaptureQueriesContext(connection) as captured:
                    reports = build_reports(actor, self.filters(actor, include_private="on"))
                    response = self.response(StudentDetailView, actor, pk=self.first.pk)
                self.assertFalse(any("participants_meetingnote" in q["sql"].lower() for q in captured))
                self.assertNotIn("سر اللقاء", repr(reports) + repr(response.context_data))
                self.assertIn(self.first.phone, repr(reports))

    def test_group_and_general_private_notes_obey_existing_scope(self):
        for actor in (self.supervisor, self.general):
            self.policy(actor)
            with self.subTest(role=actor.role):
                reports = build_reports(actor, self.filters(actor, include_private="on"))
                self.assertIn("سر اللقاء " + self.first.user.username, repr(reports))
                self.assertEqual("سر اللقاء " + self.foreign.user.username in repr(reports), actor == self.general)

    def test_detail_builder_cannot_be_forced_to_show_private_notes_or_foreign_history(self):
        self.policy(self.limited)
        view = StudentDetailView()
        view.request = self.factory.get("/synthetic-detail/")
        view.request.user = self.limited
        with CaptureQueriesContext(connection) as captured:
            _, _, tables = view._build_tables(self.first, None, None, True)
        self.assertNotIn("سر اللقاء", repr(tables))
        self.assertFalse(any("participants_meetingnote" in q["sql"].lower() for q in captured))
        self.policy(self.supervisor)
        view.request.user = self.supervisor
        with CaptureQueriesContext(connection) as captured, self.assertRaises(PermissionDenied):
            view._build_tables(self.foreign, None, None, True)
        self.assertFalse(any("participants_tasksubmission" in q["sql"].lower() for q in captured))

    def test_denied_source_contexts_empty_before_forms_builders_and_navigation(self):
        self.policy(self.general, view=False)
        cases = ((ReportCenterView, "report_center_views", "ReportCenterFilterForm", "reports"),
                 (StudentReportView, "report_views", "StudentReportFilterForm", "top_students"),
                 (StudentDetailView, "student_views", "StudentDetailFilterForm", "tables"))
        for view_class, module, form_name, rows_key in cases:
            view = view_class()
            view.request = self.factory.get("/synthetic-source/", {"include_private": "on"})
            view.request.user = self.general
            view.kwargs = {"pk": self.foreign.pk}
            with mock.patch("participants." + module + "." + form_name) as form, \
                    mock.patch("participants." + module + ".build_navbar") as navbar:
                context = view.get_context_data()
            form.assert_not_called()
            navbar.assert_not_called()
            self.assertEqual(context[rows_key], [])
            self.assertEqual(context["navbar_items"], [])
            self.assertFalse(context["can_prepare_reports"])

    def test_get_head_options_rejections_and_missing_detail_are_private(self):
        self.policy(self.general, view=False)
        for method in ("get", "head", "options"):
            for view in (StudentReportView, StudentDetailView, ReportCenterView, ReportCenterExportView):
                response = self.response(view, self.general, method=method,
                                         pk=self.first.pk if view == StudentDetailView else None, format="pdf")
                self.assertEqual(response.status_code, 403)
                self.assert_private(response)
        response = self.response(StudentDetailView, self.supervisor, pk=self.foreign.pk)
        self.assertEqual(response.status_code, 404)
        self.assert_private(response)
        for method in ("head", "options"):
            response = self.response(ReportCenterView, self.supervisor, method=method)
            self.assertEqual(response.status_code, 403)
            self.assert_private(response)

    def test_preparer_exports_authorized_scope_and_export_errors_are_private(self):
        self.policy(self.supervisor, view=True, prepare=True)
        for fmt in ("pdf", "xlsx"):
            with mock.patch("participants.report_center_exports.export_reports_" + fmt,
                            return_value=b"synthetic-report") as exporter:
                response = self.response(ReportCenterExportView, self.supervisor, format=fmt, **self.params())
            self.assertEqual(response.status_code, 200)
            self.assert_private(response)
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertNotIn(self.foreign.user.full_name, repr(exporter.call_args.args[0]))
            with mock.patch("participants.report_center_exports.export_reports_" + fmt,
                            side_effect=OSError("secret payload")):
                response = self.response(ReportCenterExportView, self.supervisor, format=fmt, **self.params())
            self.assertEqual(response.status_code, 503)
            self.assert_private(response)
            self.assertNotIn("secret payload", response.content.decode())
            self.assertNotIn("Content-Disposition", response)
        response = self.response(ReportCenterExportView, self.supervisor, format="csv")
        self.assertEqual(response.status_code, 400)
        self.assert_private(response)

    def test_report_reads_and_mock_exports_do_not_write_or_change_domain_data(self):
        self.policy(self.general, view=True, prepare=True)
        models = (User, Participant, Group, CircleAttendance, MeetingAttendance, MeetingNote, WeeklyTask,
                  TaskSubmission, PointsLedgerEntry, SupervisorReportPermission, SupervisorCapability)
        snapshot = lambda: {model: list(model.objects.order_by("pk").values()) for model in models}
        before = snapshot()
        filters = self.filters(self.general, include_private="on")
        with CaptureQueriesContext(connection) as captured:
            build_reports(self.general, filters)
            build_student_report(self.general, {})
            self.response(StudentDetailView, self.general, pk=self.first.pk)
            for fmt in ("pdf", "xlsx"):
                with mock.patch("participants.report_center_exports.export_reports_" + fmt, return_value=b"synthetic-report"):
                    self.assertEqual(self.response(ReportCenterExportView, self.general, format=fmt,
                                                   **self.params(include_private="on")).status_code, 200)
        for query in captured:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")))
        self.assertEqual(before, snapshot())

    def test_fresh_checks_and_report_query_counts_are_constant_not_per_row(self):
        self.policy(self.supervisor)
        filters = self.filters(self.supervisor, include_private="on")
        with self.assertNumQueries(1):
            state = current_report_access(self.supervisor)
        self.assertTrue(state["can_view"])
        with CaptureQueriesContext(connection) as baseline:
            build_student_report(self.supervisor, {})
        self.assertEqual(len(baseline), 7)  # Previous six bulk queries + ONE fresh policy/role read.
        with CaptureQueriesContext(connection) as center_baseline:
            build_reports(self.supervisor, filters)
        for index in range(25):
            self.student("bulk-" + str(index), self.group2)
        with CaptureQueriesContext(connection) as larger:
            build_student_report(self.supervisor, {})
        with CaptureQueriesContext(connection) as center_larger:
            build_reports(self.supervisor, filters)
        self.assertEqual(len(larger), len(baseline))
        self.assertEqual(len(center_larger), len(center_baseline))
        self.assertEqual(sum("participants_supervisorreportpermission" in q["sql"].lower() for q in larger), 1)
        self.assertEqual(sum("participants_supervisorreportpermission" in q["sql"].lower() for q in center_larger), 1)
        self.assertLess(len(center_larger), 30)

    def test_integrated_participants_table_and_pdf_scope_use_fresh_persisted_role(self):
        from .views import ParticipantsDataPDFExportView, ParticipantsDataView
        self.policy(self.general, view=True, prepare=True)
        stale = User.objects.get(pk=self.general.pk)
        User.objects.filter(pk=stale.pk).update(role=Role.GROUP_SUPERVISOR)
        self.group.supervisor.add(stale)
        self.group2.supervisor.add(stale)
        request = self.factory.get("/synthetic-data/", {"group": self.outside.name})
        request.user = stale
        table = ParticipantsDataView()
        table.request = request
        self.assertEqual(set(table.get_queryset().values_list("pk", flat=True)), {self.first.pk, self.second.pk})
        pdf = ParticipantsDataPDFExportView()
        self.assertEqual(set(pdf._get_participants(request).values_list("pk", flat=True)), {self.first.pk, self.second.pk})

    def test_integrated_table_pdf_and_snapshot_reject_explicit_deny_before_data(self):
        from .views import ParticipantsDataPDFExportView, ParticipantsDataView, PointsSnapshotHistoryView
        for actor in (self.general, self.supervisor, self.limited):
            self.policy(actor, view=False, prepare=False)
            request = self.factory.get("/synthetic-data/")
            request.user = actor
            with self.subTest(role=actor.role):
                for view_class in (ParticipantsDataView, ParticipantsDataPDFExportView, PointsSnapshotHistoryView):
                    view = view_class()
                    view.request = request
                    self.assertFalse(view.test_func())
                table = ParticipantsDataView()
                table.request = request
                with CaptureQueriesContext(connection) as captured, self.assertRaises(PermissionDenied):
                    table.get_queryset()
                self.assert_no_report_queries(captured)
                with CaptureQueriesContext(connection) as captured, self.assertRaises(PermissionDenied):
                    ParticipantsDataPDFExportView()._get_participants(request)
                self.assert_no_report_queries(captured)

    def test_integrated_view_only_table_allowed_pdf_denied_before_weasyprint(self):
        from .views import ParticipantsDataPDFExportView, ParticipantsDataView
        for actor in (self.general, self.supervisor, self.limited):
            self.policy(actor)
            request = self.factory.get("/synthetic-data/")
            request.user = actor
            table = ParticipantsDataView()
            table.request = request
            self.assertTrue(table.test_func())
            self.assertTrue(table.get_queryset().exists())
            pdf = ParticipantsDataPDFExportView()
            pdf.request = request
            with mock.patch("builtins.__import__", wraps=__import__) as importer, \
                    CaptureQueriesContext(connection) as captured:
                self.assertFalse(pdf.test_func())
                with self.assertRaises(PermissionDenied):
                    pdf._get_participants(request)
            self.assert_no_report_queries(captured)
            self.assertFalse(any(call.args and call.args[0] == "weasyprint" for call in importer.call_args_list))

    def test_integrated_dashboard_explicit_deny_has_no_chart_payload_or_chart_queries(self):
        from .views import GeneralSupervisorDashboardView
        self.policy(self.general, view=False)
        view = GeneralSupervisorDashboardView()
        view.request = self.factory.get("/synthetic-general-dashboard/")
        view.request.user = self.general
        with mock.patch("participants.views.build_navbar", return_value=[]), CaptureQueriesContext(connection) as captured:
            context = view.get_context_data()
        self.assertFalse(context.get("charts_data"))
        self.assertFalse(context["can_compare_groups"])
        # Simple administrative headline KPIs remain permitted; chart aggregate
        # queries (GROUP BY / AVG) and active-task chart construction do not.
        for query in captured:
            sql = query["sql"].upper()
            self.assertNotIn("GROUP BY", sql)
            self.assertNotIn("AVG(", sql)
        self.assertIn("kpi_total_participants", context)

    def test_integrated_snapshot_access_preserves_general_system_only_with_view(self):
        from .views import PointsSnapshotHistoryView
        for actor, expected in ((self.general, True), (self.system, True),
                                (self.supervisor, False), (self.limited, False)):
            self.policy(actor)
            request = self.factory.get("/synthetic-snapshot-history/")
            request.user = actor
            view = PointsSnapshotHistoryView()
            view.request = request
            self.assertEqual(view.test_func(), expected)

    def test_actual_routes_explicit_denial_blocks_html_detail_history_and_all_files(self):
        routes = (
            ("student_report", {}, {}),
            ("student_detail", {"pk": self.first.pk}, {"task_page": 1, "note_page": 1}),
            ("participants_data", {}, {}),
            ("participants_data_pdf", {}, {}),
            ("report_center", {}, self.params()),
            ("report_center_export", {}, self.params(format="pdf")),
            ("report_center_export", {}, self.params(format="xlsx")),
            ("points_snapshot_history", {}, {}),
        )
        for actor in (self.general, self.supervisor, self.limited):
            self.policy(actor, view=False)
            self.client.force_login(actor)
            for name, kwargs, params in routes:
                with self.subTest(role=actor.role, route=name, fmt=params.get("format")):
                    with CaptureQueriesContext(connection) as captured:
                        response = self.client.get(reverse("participants:" + name, kwargs=kwargs), params)
                    self.assertEqual(response.status_code, 403)
                    self.assert_no_report_queries(captured)
                    self.assertNotIn("Content-Disposition", response)
                    self.assert_private(response)

    def test_actual_view_only_routes_read_and_filter_but_never_export(self):
        for actor in (self.general, self.supervisor, self.limited):
            self.policy(actor)
            self.client.force_login(actor)
            with mock.patch("participants.views.build_navbar", return_value=[]):
                for name, kwargs, params in (("student_report", {}, {}),
                                              ("student_detail", {"pk": self.first.pk}, {}),
                                              ("participants_data", {}, {}),
                                              ("report_center", {}, self.params(group=self.group.pk))):
                    with self.subTest(role=actor.role, route=name):
                        response = self.client.get(reverse("participants:" + name, kwargs=kwargs), params)
                        self.assertEqual(response.status_code, 200)
                        if name == "report_center":
                            self.assertFalse(response.context["can_prepare_reports"])
                            self.assertNotContains(response, 'formaction="' + reverse("participants:report_center_export") + '"')
            for name, params in (("participants_data_pdf", {}),
                                 ("report_center_export", self.params(format="pdf")),
                                 ("report_center_export", self.params(format="xlsx"))):
                with self.subTest(role=actor.role, route=name), CaptureQueriesContext(connection) as captured:
                    response = self.client.get(reverse("participants:" + name), params)
                    self.assertEqual(response.status_code, 403)
                    self.assert_no_report_queries(captured)
                    self.assert_private(response)
