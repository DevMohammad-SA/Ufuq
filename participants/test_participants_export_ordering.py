"""Synthetic, isolated regression coverage for the roster's selected PDF sort.

Run only this module with config.test_settings. The usual export assertions
inspect the actual template HTML passed to the view's lazily imported PDF
engine. A separate native smoke test uses local static assets, never the network.
No TestCase subclasses are imported from other test modules.
"""

import base64
import datetime
import json
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import urljoin, urlsplit

from django.apps import apps
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.templatetags.static import static
from django.urls import reverse

from accounts.models import Role, User
from .capabilities import can_prepare_reports
from .models import (
    CircleAttendance,
    Group,
    MeetingAttendance,
    Participant,
    SupervisorReportPermission,
)
from .views import ParticipantsDataPDFExportView


# Executed by a fresh interpreter, not imported by the test runner. Restoring
# sys.modules after importing WeasyPrint cannot restore Pango's process-global
# GObject registrations, so native imports must never happen in this process.
# The child receives only the view's HTML/base URL and explicit local asset
# paths: no Django setup, database connection, fixture copy or network access.
_NATIVE_PDF_SMOKE_SCRIPT = r"""
import base64
import json
import logging
import mimetypes
import sys
import traceback
from pathlib import Path

payload = json.load(sys.stdin)
network_attempts = []

def no_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        network_attempts.append(event)
        raise RuntimeError("Native smoke test forbids network access")

sys.addaudithook(no_network)

try:
    from weasyprint import HTML
except OSError as exc:
    # Only a missing native shared library is skippable. Import/API bugs,
    # incompatible libraries and all failures after import remain failures.
    frames = traceback.extract_tb(exc.__traceback__)
    native_loader = any(
        frame.name == "_dlopen" and
        frame.filename.replace("\\", "/").endswith("/weasyprint/text/ffi.py")
        for frame in frames
    )
    missing_library = any(message in str(exc) for message in (
        "No such file or directory", "image not found",
        "The specified module could not be found",
    ))
    if native_loader and "cannot load library" in str(exc) and missing_library:
        print(f"WeasyPrint native library unavailable: {exc}", file=sys.stderr)
        sys.exit(77)
    raise

from weasyprint.urls import URLFetcherResponse

render_errors = []

class RenderLogGuard(logging.Handler):
    def emit(self, record):
        message = record.getMessage()
        if record.levelno >= logging.ERROR or (
            "Font-face" in message and "cannot be loaded" in message
        ):
            render_errors.append(message)

logger = logging.getLogger("weasyprint")
logger.setLevel(logging.WARNING)
logger.addHandler(RenderLogGuard())
requested = []
blocked = []
loaded = []

def local_fetch(url):
    requested.append(url)
    # Match complete URLs, not just /static/ paths on an arbitrary host. Only
    # the four bundled assets are allowed, including for file/data/FTP URLs.
    if url not in payload["assets"]:
        blocked.append(url)
        raise ValueError("Native smoke test rejects non-local-static resources")
    path = Path(payload["assets"][url])
    body = path.read_bytes()
    if not body:
        raise ValueError(f"Empty bundled asset: {path.name}")
    loaded.append(url)
    return URLFetcherResponse(
        url, body=body,
        headers={"Content-Type": mimetypes.guess_type(path.name)[0]
                 or "application/octet-stream"},
    )

local_fetch._fail_on_errors = True

# Prove the fetcher rejects external hosts even if their paths look like our
# assets; also reject arbitrary local files, data URLs and unknown resources.
for url in payload["rejected_urls"]:
    try:
        local_fetch(url)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Disallowed resource was accepted: {url}")
requested.clear()
blocked.clear()

pdf = HTML(**payload["html_kwargs"], url_fetcher=local_fetch).write_pdf()
if render_errors:
    raise AssertionError(f"Native renderer errors: {render_errors}")
if blocked or network_attempts:
    raise AssertionError(f"Forbidden resources: {blocked}; network: {network_attempts}")
if set(loaded) != set(payload["assets"]):
    raise AssertionError(f"Bundled fonts/letterhead were not all loaded: {loaded}")
print(json.dumps({
    "pdf": base64.b64encode(pdf).decode("ascii"),
    "requested": requested, "loaded": loaded, "blocked": blocked,
    "network_attempts": network_attempts,
}))
"""


class _RosterTableParser(HTMLParser):
    """Capture cells only inside tbody, not names in metadata or headings."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.in_body = False
        self.in_cell = False
        self.row = []
        self.cell = []

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self.in_body = True
        elif self.in_body and tag == "tr":
            self.row = []
        elif self.in_body and tag == "td":
            self.in_cell = True
            self.cell = []

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.in_cell:
            self.row.append("".join(self.cell).strip())
            self.in_cell = False
        elif tag == "tr" and self.in_body:
            self.rows.append(self.row)
        elif tag == "tbody":
            self.in_body = False


class ParticipantsExportOrderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(
            username="synthetic-sort-general", role=Role.GENERAL_SUPERVISOR,
        )
        cls.supervisor = User.objects.create_user(
            username="synthetic-sort-group", role=Role.GROUP_SUPERVISOR,
        )
        cls.limited = User.objects.create_user(
            username="synthetic-sort-limited", role=Role.LIMITED_SUPERVISOR,
        )
        cls.group_a = Group.objects.create(name="بيئة أ")
        cls.group_a.supervisor.add(cls.supervisor)
        cls.group_b = Group.objects.create(name="بيئة ب")

        # Deliberately neither insertion order nor any currency has the same
        # ranking. Small non-proportional values catch the original miles sort.
        cls.c = cls._participant(
            "8800000003", "خالد", cls.group_b, "grade_6", 100, 30, 20,
        )
        cls.a = cls._participant(
            "8800000001", "أحمد", cls.group_a, "grade_5", 200, 10, 30,
        )
        cls.b = cls._participant(
            "8800000002", "بدر", cls.group_a, "grade_7", 300, 20, 10,
        )
        cls._attendance(cls.a, circles=2, meetings=3)
        cls._attendance(cls.b, circles=3, meetings=1)
        cls._attendance(cls.c, circles=1, meetings=2)

    @staticmethod
    def _participant(national_id, name, group, stage, miles, points, purchase,
                     *, active=True):
        user = User.objects.create_user(
            national_id=national_id, full_name=name, role=Role.PARTICIPANT,
            is_active=active,
        )
        return Participant.objects.create(
            user=user, group=group, academic_stage=stage, miles=miles,
            points=points, purchase_points=purchase,
        )

    @staticmethod
    def _attendance(participant, *, circles, meetings):
        start = datetime.date(2026, 1, 1)
        CircleAttendance.objects.bulk_create([
            CircleAttendance(
                participant=participant, date=start + datetime.timedelta(days=i),
                attended=i < circles,
            )
            for i in range(circles + 1)
        ])
        MeetingAttendance.objects.bulk_create([
            MeetingAttendance(
                participant=participant,
                week_start_date=start + datetime.timedelta(weeks=i),
                attended=i < meetings,
            )
            for i in range(meetings + 1)
        ])

    def setUp(self):
        self.engine = mock.MagicMock(name="synthetic_weasyprint")
        self.engine.HTML.return_value.write_pdf.return_value = b"%PDF-1.7 synthetic"
        patcher = mock.patch.dict(sys.modules, {"weasyprint": self.engine})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.general)
        self.url = reverse("participants:participants_data_pdf")
        self.data_url = reverse("participants:participants_data")

    def _pdf_rows(self, params=None):
        self.engine.reset_mock()
        response = self.client.get(self.url, params or {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.engine.HTML.assert_called_once()
        parser = _RosterTableParser()
        parser.feed(self.engine.HTML.call_args.kwargs["string"])
        self.assertEqual(
            [row[0] for row in parser.rows],
            [str(index) for index in range(1, len(parser.rows) + 1)],
        )
        return parser.rows

    def _assert_pdf_order(self, names, **params):
        self.assertEqual([row[1] for row in self._pdf_rows(params)], names)

    def test_points_ascending_pdf_row_order(self):
        self._assert_pdf_order(["أحمد", "بدر", "خالد"], sort_by="points", sort_dir="asc")

    def test_points_descending_pdf_row_order(self):
        self._assert_pdf_order(["خالد", "بدر", "أحمد"], sort_by="points", sort_dir="desc")

    def test_purchase_points_ascending_pdf_row_order(self):
        self._assert_pdf_order(["بدر", "خالد", "أحمد"], sort_by="purchase_points", sort_dir="asc")

    def test_purchase_points_descending_pdf_row_order(self):
        self._assert_pdf_order(["أحمد", "خالد", "بدر"], sort_by="purchase_points", sort_dir="desc")

    def test_miles_both_directions_pdf_row_order(self):
        for direction, expected in (
            ("asc", ["خالد", "أحمد", "بدر"]),
            ("desc", ["بدر", "أحمد", "خالد"]),
        ):
            with self.subTest(direction=direction):
                self._assert_pdf_order(expected, sort_by="miles", sort_dir=direction)

    def test_missing_or_invalid_sort_configuration_defaults_to_miles_desc(self):
        cases = (
            {}, {"sort_by": "points"}, {"sort_dir": "asc"},
            {"sort_by": "", "sort_dir": "asc"},
            {"sort_by": "points", "sort_dir": ""},
            {"sort_by": "points", "sort_dir": "ascending"},
            {"sort_by": "points", "sort_dir": "ASC"},
            {"sort_by": "purchase_points", "sort_dir": "-1"},
            {"sort_by": "not-a-column", "sort_dir": "asc"},
        )
        for params in cases:
            with self.subTest(params=params):
                self._assert_pdf_order(["بدر", "أحمد", "خالد"], **params)

    def test_unsafe_sort_keys_are_not_orm_expressions(self):
        for key in (
            "user__password", "__class__", "-points", "points__gte", "?",
            "points); DROP TABLE participants_participant; --",
        ):
            with self.subTest(key=key):
                self._assert_pdf_order(["بدر", "أحمد", "خالد"], sort_by=key, sort_dir="asc")
        self.assertEqual(Participant.objects.count(), 3)

    def test_numeric_ties_keep_full_name_baseline_in_both_directions(self):
        for key in ("points", "purchase_points", "miles"):
            Participant.objects.update(**{key: 7})
            for direction in ("asc", "desc"):
                with self.subTest(key=key, direction=direction):
                    self._assert_pdf_order(["أحمد", "بدر", "خالد"], sort_by=key, sort_dir=direction)

    def test_identical_names_and_numeric_ties_keep_pk_baseline(self):
        User.objects.filter(pk__in=[self.a.user_id, self.b.user_id, self.c.user_id]).update(full_name="اسم مكرر")
        Participant.objects.update(points=7)
        # c was inserted first, then a, then b; use another printed currency
        # to identify otherwise indistinguishable names in actual PDF rows.
        for direction in ("asc", "desc"):
            with self.subTest(direction=direction):
                rows = self._pdf_rows({"sort_by": "points", "sort_dir": direction})
                self.assertEqual([row[4] for row in rows], ["100", "200", "300"])

    def test_attendance_counts_are_distinct_across_both_joins(self):
        from .roster_ordering import annotate_roster_attendance

        counts = {
            row.pk: (row.circle_days_attended, row.meetings_attended)
            for row in annotate_roster_attendance(Participant.objects.all())
        }
        expected = {self.a.pk: (2, 3), self.b.pk: (3, 1), self.c.pk: (1, 2)}
        self.assertEqual(counts, expected)
        response = self.client.get(self.data_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {p.pk: (p.circle_days_attended, p.meetings_attended)
             for p in response.context["participants"]},
            expected,
        )

    def test_circle_attendance_numeric_sort_both_directions(self):
        for direction, names in (
            ("asc", ["خالد", "أحمد", "بدر"]),
            ("desc", ["بدر", "أحمد", "خالد"]),
        ):
            with self.subTest(direction=direction):
                self._assert_pdf_order(names, sort_by="circle_days_attended", sort_dir=direction)

    def test_meeting_attendance_numeric_sort_both_directions(self):
        for direction, names in (
            ("asc", ["بدر", "خالد", "أحمد"]),
            ("desc", ["أحمد", "خالد", "بدر"]),
        ):
            with self.subTest(direction=direction):
                self._assert_pdf_order(names, sort_by="meetings_attended", sort_dir=direction)

    def test_no_attendance_sorts_as_zero_without_disappearing(self):
        self._participant("8800000004", "سعد", self.group_a, "grade_7", 0, 0, 0)
        for key in ("circle_days_attended", "meetings_attended"):
            with self.subTest(key=key):
                rows = self._pdf_rows({"sort_by": key, "sort_dir": "asc"})
                self.assertEqual(rows[0][1], "سعد")
                self.assertEqual(len(rows), 4)

    def test_double_digit_attendance_counts_sort_numerically_not_lexically(self):
        CircleAttendance.objects.filter(participant=self.c).delete()
        MeetingAttendance.objects.filter(participant=self.c).delete()
        self._attendance(self.c, circles=10, meetings=12)
        for key, ascending, descending in (
            ("circle_days_attended", ["أحمد", "بدر", "خالد"], ["خالد", "بدر", "أحمد"]),
            ("meetings_attended", ["بدر", "أحمد", "خالد"], ["خالد", "أحمد", "بدر"]),
        ):
            for direction, names in (("asc", ascending), ("desc", descending)):
                with self.subTest(key=key, direction=direction):
                    self._assert_pdf_order(names, sort_by=key, sort_dir=direction)

    def test_full_name_text_sort_both_directions(self):
        self._assert_pdf_order(["أحمد", "بدر", "خالد"], sort_by="full_name", sort_dir="asc")
        self._assert_pdf_order(["خالد", "بدر", "أحمد"], sort_by="full_name", sort_dir="desc")

    def test_group_text_sort_keeps_ties_stable_both_directions(self):
        self._assert_pdf_order(["أحمد", "بدر", "خالد"], sort_by="group", sort_dir="asc")
        self._assert_pdf_order(["خالد", "أحمد", "بدر"], sort_by="group", sort_dir="desc")

    def test_academic_stage_sorts_arabic_display_labels_not_stored_codes(self):
        # Labels are أول متوسط / خامس ابتدائي / سادس ابتدائي. Ordering
        # grade_5 / grade_6 / grade_7 would yield a different sequence.
        self._assert_pdf_order(["بدر", "أحمد", "خالد"], sort_by="academic_stage", sort_dir="asc")
        self._assert_pdf_order(["خالد", "أحمد", "بدر"], sort_by="academic_stage", sort_dir="desc")

    def test_text_comparison_strips_and_lowercases_before_stable_sort(self):
        for participant, name in ((self.a, " alpha "), (self.b, "ALPHA"), (self.c, "Beta")):
            User.objects.filter(pk=participant.user_id).update(full_name=name)
        # The raw-name baseline is a (space alpha), b (ALPHA), c (Beta).
        for direction, expected in (
            ("asc", ["alpha", "ALPHA", "Beta"]),
            ("desc", ["Beta", "alpha", "ALPHA"]),
        ):
            with self.subTest(direction=direction):
                self._assert_pdf_order(expected, sort_by="full_name", sort_dir=direction)

    def test_text_comparison_uses_javascript_utf16_order(self):
        # UTF-16 orders the supplementary-plane character before U+E000,
        # whereas Python's default Unicode code-point ordering does not.
        for participant, name in ((self.a, "\ue000"), (self.b, "\U00010000"), (self.c, "Z")):
            User.objects.filter(pk=participant.user_id).update(full_name=name)
        self._assert_pdf_order(["Z", "\U00010000", "\ue000"], sort_by="full_name", sort_dir="asc")
        self._assert_pdf_order(["\ue000", "\U00010000", "Z"], sort_by="full_name", sort_dir="desc")

    def test_text_trimming_matches_javascript_bom_and_control_characters(self):
        from .roster_ordering import _text_key

        self.assertEqual(_text_key("\ufeffA\ufeff"), "a".encode("utf-16-be"))
        self.assertEqual(_text_key("\x85A"), "\x85a".encode("utf-16-be"))
        for participant, name in ((self.a, "\ufeffA"), (self.b, "B"), (self.c, "C")):
            User.objects.filter(pk=participant.user_id).update(full_name=name)
        self._assert_pdf_order(["\ufeffA", "B", "C"], sort_by="full_name", sort_dir="asc")
        self._assert_pdf_order(["C", "B", "\ufeffA"], sort_by="full_name", sort_dir="desc")

    def test_missing_group_and_blank_name_sort_with_display_emdash(self):
        Participant.objects.filter(pk=self.c.pk).update(group=None)
        User.objects.filter(pk=self.c.user_id).update(full_name="")
        self._assert_pdf_order(["أحمد", "بدر", "—"], sort_by="group", sort_dir="asc")
        self._assert_pdf_order(["—", "أحمد", "بدر"], sort_by="group", sort_dir="desc")
        self._assert_pdf_order(["أحمد", "بدر", "—"], sort_by="full_name", sort_dir="asc")

    def test_general_selected_group_filters_apply_before_ordering(self):
        self._assert_pdf_order(["بدر", "أحمد"], group=self.group_a.name, sort_by="points", sort_dir="desc")
        self._assert_pdf_order(
            ["بدر", "خالد", "أحمد"], group=[self.group_a.name, self.group_b.name],
            sort_by="purchase_points", sort_dir="asc",
        )
        self._assert_pdf_order([], group="بيئة غير موجودة", sort_by="points", sort_dir="asc")

    def test_group_supervisor_ignores_foreign_group_filter_and_keeps_all_assignments(self):
        group_c = Group.objects.create(name="بيئة ج")
        group_c.supervisor.add(self.supervisor)
        self._participant("8800000004", "سعد", group_c, "grade_7", 400, 5, 50)
        self._participant("8800000005", "طالب مؤرشف", self.group_a, "grade_7", 999, 99, 99, active=False)
        self.client.force_login(self.supervisor)
        for status in ("active", "inactive", "all"):
            with self.subTest(status=status):
                self._assert_pdf_order(
                    ["سعد", "أحمد", "بدر"], group=self.group_b.name, status=status,
                    sort_by="points", sort_dir="asc",
                )

    def test_general_status_filters_preserve_archive_scope_and_sort(self):
        self._participant("8800000004", "سعد", self.group_a, "grade_7", 400, 5, 50, active=False)
        self._assert_pdf_order(["أحمد", "بدر", "خالد"], sort_by="points", sort_dir="asc")
        self._assert_pdf_order(["سعد"], status="inactive", sort_by="points", sort_dir="asc")
        self._assert_pdf_order(["سعد", "أحمد", "بدر", "خالد"], status="all", sort_by="points", sort_dir="asc")
        self._assert_pdf_order(["أحمد", "بدر", "خالد"], status="invalid", sort_by="points", sort_dir="asc")

    def test_nonparticipant_profiles_never_enter_sorted_export(self):
        User.objects.filter(pk=self.c.user_id).update(role=Role.LIMITED_SUPERVISOR)
        self._assert_pdf_order(["بدر", "أحمد"], status="all", sort_by="points", sort_dir="desc")

    def test_ungranted_limited_supervisor_and_participant_cannot_export(self):
        for actor in (self.limited, self.a.user):
            with self.subTest(role=actor.role):
                self.client.force_login(actor)
                self.engine.reset_mock()
                response = self.client.get(self.url, {"sort_by": "points", "sort_dir": "asc"})
                self.assertEqual(response.status_code, 403)
                self.engine.HTML.assert_not_called()

    def test_explicit_limited_report_grant_allows_sorted_existing_scope(self):
        SupervisorReportPermission.objects.create(
            supervisor=self.limited, view_reports=True, prepare_reports=True,
            granted_by=self.general,
        )
        self.client.force_login(self.limited)
        self._assert_pdf_order(["بدر", "خالد", "أحمد"], sort_by="purchase_points", sort_dir="asc")

    def test_current_explicit_deny_rejects_pdf_before_queryset_or_engine(self):
        for actor in (self.general, self.supervisor):
            with self.subTest(role=actor.role):
                SupervisorReportPermission.objects.update_or_create(
                    supervisor=actor, defaults={"view_reports": False, "prepare_reports": False},
                )
                self.client.force_login(actor)
                self.engine.reset_mock()
                with mock.patch.object(ParticipantsDataPDFExportView, "_get_participants") as build:
                    response = self.client.get(self.url, {"sort_by": "points", "sort_dir": "asc"})
                self.assertEqual(response.status_code, 403)
                build.assert_not_called()
                self.engine.HTML.assert_not_called()

    def test_view_only_grant_allows_data_but_blocks_sorted_pdf(self):
        SupervisorReportPermission.objects.create(
            supervisor=self.general, view_reports=True, prepare_reports=False,
        )
        response = self.client.get(self.data_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_prepare_reports"])
        self.engine.reset_mock()
        with mock.patch.object(ParticipantsDataPDFExportView, "_get_participants") as build:
            response = self.client.get(self.url, {"sort_by": "purchase_points", "sort_dir": "desc"})
        self.assertEqual(response.status_code, 403)
        build.assert_not_called()
        self.engine.HTML.assert_not_called()

    def test_cached_grant_cannot_bypass_current_revocation_or_inconsistent_policy(self):
        policy = SupervisorReportPermission.objects.create(
            supervisor=self.general, view_reports=True, prepare_reports=True,
        )
        stale = User.objects.select_related("report_permissions").get(pk=self.general.pk)
        self.assertTrue(can_prepare_reports(stale))
        request = RequestFactory().get(self.url, {"sort_by": "points", "sort_dir": "desc"})
        request.user = stale
        for view, prepare in ((True, False), (False, False), (False, True)):
            with self.subTest(view=view, prepare=prepare):
                SupervisorReportPermission.objects.filter(pk=policy.pk).update(
                    view_reports=view, prepare_reports=prepare,
                )
                with self.assertRaises(PermissionDenied):
                    ParticipantsDataPDFExportView()._get_participants(request)
                self.engine.reset_mock()
                # Even if the initial dispatch check observed the older grant,
                # the fresh check inside the builder still stops generation.
                with mock.patch("participants.capabilities.can_prepare_reports", return_value=True), \
                     mock.patch("participants.views.render_to_string") as render:
                    response = self.client.get(self.url, {"sort_by": "points", "sort_dir": "desc"})
                self.assertEqual(response.status_code, 403)
                render.assert_not_called()
                self.engine.HTML.assert_not_called()

    def test_data_queryset_baseline_is_name_then_pk(self):
        response = self.client.get(self.data_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p.pk for p in response.context["participants"]], [self.a.pk, self.b.pk, self.c.pk])
        User.objects.filter(pk__in=[self.a.user_id, self.b.user_id, self.c.user_id]).update(full_name="اسم مكرر")
        response = self.client.get(self.data_url)
        self.assertEqual([p.pk for p in response.context["participants"]], [self.c.pk, self.a.pk, self.b.pk])

    def test_sorted_gets_do_not_mutate_accounts_participants_or_history(self):
        def snapshot():
            models = [model for label in ("accounts", "participants")
                      for model in apps.get_app_config(label).get_models()
                      if not model._meta.proxy]
            # Include the group-supervisor assignment table as well.
            models.append(Group.supervisor.through)
            return {model._meta.label: list(model.objects.order_by("pk").values())
                    for model in models}

        before = snapshot()
        writes = []

        def watch(execute, sql, params, many, context):
            if sql.lstrip().split(None, 1)[0].upper() in {
                "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP",
            }:
                writes.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(watch):
            response = self.client.get(self.data_url, {"sort_by": "points", "sort_dir": "asc"})
            self.assertEqual(response.status_code, 200)
            self._assert_pdf_order(["خالد", "بدر", "أحمد"], sort_by="points", sort_dir="desc")
            self._assert_pdf_order(["بدر", "خالد", "أحمد"], sort_by="purchase_points", sort_dir="asc")
        self.assertEqual(writes, [])
        self.assertEqual(snapshot(), before)

    def test_native_pdf_smoke_with_local_assets_and_no_remote_fetch(self):
        # Use the normal permission/filter/queryset/template path with the
        # synthetic fixtures and the fake engine. Send exactly the HTML and
        # base URL that this view passes to HTML(), not a smoke-only template.
        self._assert_pdf_order(
            ["بدر", "خالد", "أحمد"], sort_by="purchase_points", sort_dir="asc",
        )
        html_kwargs = dict(self.engine.HTML.call_args.kwargs)
        native_modules_before = {
            name: module for name, module in sys.modules.copy().items()
            if name == "weasyprint" or name.startswith("weasyprint.")
        }
        base_url = html_kwargs["base_url"]
        base_origin = urlsplit(base_url)
        assets = {}
        for relative in (
            "fonts/tajawal/Tajawal-Regular.ttf",
            "fonts/tajawal/Tajawal-Bold.ttf",
            "fonts/tajawal/Tajawal-ExtraBold.ttf",
            "images/letterhead.png",
        ):
            filename = finders.find(relative)
            self.assertIsNotNone(filename, f"Missing bundled PDF asset: {relative}")
            path = Path(filename).resolve()
            self.assertTrue(path.is_file(), f"Missing bundled PDF asset: {relative}")
            asset_url = urljoin(base_url, static(relative))
            asset_origin = urlsplit(asset_url)
            self.assertEqual(
                (asset_origin.scheme, asset_origin.netloc),
                (base_origin.scheme, base_origin.netloc),
                "Native smoke test must not allow externally hosted static assets",
            )
            assets[asset_url] = str(path)

        payload = {
            "html_kwargs": html_kwargs,
            "assets": assets,
            "rejected_urls": [
                "https://remote.invalid/static/images/letterhead.png",
                "http://remote.invalid/static/fonts/tajawal/Tajawal-Regular.ttf",
                urljoin(base_url, "static/not-a-bundled-asset.png"),
                urljoin(base_url, "static/../private.png"),
                "file:///etc/passwd", "data:image/png;base64,AAAA",
                "ftp://remote.invalid/static/images/letterhead.png",
            ],
        }
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-c", _NATIVE_PDF_SMOKE_SCRIPT],
                input=json.dumps(payload), capture_output=True, text=True,
                encoding="utf-8", cwd=settings.BASE_DIR, timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(f"Native PDF subprocess exceeded 60s: {exc.stderr!r}")

        self.assertEqual(
            {name: module for name, module in sys.modules.copy().items()
             if name == "weasyprint" or name.startswith("weasyprint.")},
            native_modules_before,
            "Native smoke must leave the test runner's module state unchanged",
        )
        if result.returncode == 77:
            self.assertIn("WeasyPrint native library unavailable:", result.stderr)
            self.skipTest(result.stderr.strip())
        self.assertEqual(
            result.returncode, 0,
            f"Native PDF subprocess failed:\n{result.stdout}\n{result.stderr}",
        )
        rendered = json.loads(result.stdout)
        pdf = base64.b64decode(rendered["pdf"], validate=True)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        self.assertEqual(set(rendered["requested"]), set(assets))
        self.assertEqual(set(rendered["loaded"]), set(assets))
        self.assertEqual(rendered["blocked"], [], "No unrecognized resource may be fetched")
        self.assertEqual(rendered["network_attempts"], [], "No network access is allowed")
