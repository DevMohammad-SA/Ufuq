"""Compact roster regressions using synthetic participants and an isolated DB."""
import datetime
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import (
    CircleAttendance, Group, MeetingAttendance, MeetingNote, Participant,
    PointsLedgerEntry, WeeklyActivityAttendance,
)
from .test_attendance_layout import FirstRosterRow


class RosterHeaders(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.headers = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "roster-table":
            self.in_table = True
        if tag == "th" and self.in_table:
            self.headers.append(attrs)

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False


class CompactRosterSourceTests(SimpleTestCase):
    def test_fixed_layout_has_no_large_collapsed_minimum_or_name_clipping(self):
        css = (Path(settings.BASE_DIR) / "static/css/attendance-roster.css").read_text()
        self.assertIn("table-layout:fixed", css)
        self.assertIn("overflow-wrap:anywhere", css)
        self.assertIn("white-space:normal", css)
        self.assertIn("width:44px", css)
        self.assertIn("min-height:44px", css)
        self.assertIn(".student-data-visible", css)
        self.assertNotIn("min-width:610px", css)
        self.assertNotIn("min-width:575px", css)
        self.assertNotIn("min-width:255px", css)
        self.assertNotIn("text-overflow:ellipsis", css)
        self.assertNotIn("line-clamp", css)
        self.assertIn("flex:0 0 42px", css)
        self.assertIn("height:42px", css)

    def test_all_rosters_use_shared_assets_without_inline_width_overrides(self):
        for name in ("supervisor_dashboard", "quran_circle_attendance", "weekly_activity_attendance"):
            with self.subTest(template=name):
                html = (Path(settings.BASE_DIR) / f"participants/templates/participants/{name}.html").read_text()
                self.assertIn("css/attendance-roster.css", html)
                self.assertIn("js/attendance-roster.js", html)
                self.assertIn("<colgroup>", html)
                self.assertNotIn("{% block extra_style %}", html)
                self.assertNotRegex(html, r'style="[^"]*(?:min-width|width)\s*:')
                self.assertNotIn("querySelectorAll", html)


class CompactRosterContractsTests(TestCase):
    date = datetime.date(2026, 10, 23)
    roster_urls = ("supervisor_dashboard", "quran_circle_attendance", "weekly_activity_attendance")

    @classmethod
    def setUpTestData(cls):
        cls.supervisor = User.objects.create_user(username="compact-supervisor", role=Role.GROUP_SUPERVISOR)
        cls.general = User.objects.create_user(username="compact-general", role=Role.GENERAL_SUPERVISOR)
        cls.system = User.objects.create_user(username="compact-system", role=Role.SUPERADMIN)
        cls.group = Group.objects.create(name="بيئة اختبار الكشف المضغوط")
        cls.group.supervisor.add(cls.supervisor)
        cls.long_name = "عبد الرحمن بن عبد العزيز الطالب ذو الاسم الطويل بالكامل"
        cls.student = Participant.objects.create(
            user=User.objects.create_user(national_id="8000888001", full_name=cls.long_name, role=Role.PARTICIPANT),
            group=cls.group, phone="0500000001", academic_stage="grade_7",
        )
        other_group = Group.objects.create(name="بيئة أخرى مصطنعة")
        cls.outsider = Participant.objects.create(
            user=User.objects.create_user(national_id="8000888002", full_name="طالب بيئة أخرى", role=Role.PARTICIPANT),
            group=other_group,
        )
        cls.inactive = Participant.objects.create(
            user=User.objects.create_user(national_id="8000888003", full_name="طالب مؤرشف مصطنع", role=Role.PARTICIPANT, is_active=False),
            group=cls.group,
        )
        MeetingNote.objects.create(
            participant=cls.student, meeting_date=cls.date, text="نص خاص بالمشرفين لا يظهر للنظام",
            created_by=cls.supervisor, updated_by=cls.supervisor,
        )

    def setUp(self):
        self.client.force_login(self.supervisor)

    def url(self, name):
        return reverse(f"participants:{name}")

    def test_full_names_dates_and_group_scope_are_preserved_in_all_rosters(self):
        for name in self.roster_urls:
            with self.subTest(roster=name):
                response = self.client.get(self.url(name), {"date": self.date.isoformat()})
                self.assertContains(response, self.long_name)
                self.assertContains(response, 'name="date" value="2026-10-23"')
                self.assertContains(response, f'name="group" value="{self.group.pk}"')
                self.assertContains(response, self.student.user.national_id)
                self.assertNotContains(response, self.outsider.user.full_name)
                self.assertNotContains(response, self.inactive.user.full_name)
                self.assertContains(response, 'class="roster-check-label"')

    def test_every_numeric_sort_index_matches_its_real_cell_with_and_without_notes(self):
        for user in (self.supervisor, self.general, self.system):
            self.client.force_login(user)
            for name in self.roster_urls:
                with self.subTest(role=user.role, roster=name):
                    response = self.client.get(self.url(name), {"group": self.group.pk, "date": self.date.isoformat()})
                    headers, row = RosterHeaders(), FirstRosterRow()
                    headers.feed(response.content.decode())
                    row.feed(response.content.decode())
                    self.assertEqual(len(headers.headers), len(row.cells))
                    for header in headers.headers:
                        if "sortable" in header.get("class", ""):
                            index = int(header["data-col"])
                            self.assertEqual(headers.headers[index], header)
                            self.assertIn("data-sort-value", row.cells[index]["attrs"])
                            self.assertEqual(header["tabindex"], "0")

    def test_private_note_text_never_reaches_system_or_circle_or_activity(self):
        self.client.force_login(self.system)
        response = self.client.get(self.url("supervisor_dashboard"), {"group": self.group.pk, "date": self.date.isoformat()})
        self.assertNotContains(response, "نص خاص بالمشرفين")
        self.assertNotContains(response, "الملاحظات")
        self.assertNotContains(response, 'name="note_')
        self.client.force_login(self.supervisor)
        for name in ("quran_circle_attendance", "weekly_activity_attendance"):
            response = self.client.get(self.url(name), {"date": self.date.isoformat()})
            self.assertNotContains(response, "نص خاص بالمشرفين")
            self.assertNotContains(response, 'name="note_')

    def assert_balances(self, points):
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (points, points * 10, points))

    def test_existing_post_fields_points_dates_and_idempotent_saves(self):
        cases = (
            ("supervisor_dashboard", {"attended": "on", "early": "on"}, MeetingAttendance, "week_start_date", 10),
            ("quran_circle_attendance", {"attended": "on", "achieved": "on"}, CircleAttendance, "date", 5),
            ("weekly_activity_attendance", {"attended": "on"}, WeeklyActivityAttendance, "date", 10),
        )
        total = 0
        for name, checks, model, date_field, award in cases:
            with self.subTest(roster=name):
                payload = {"date": self.date.isoformat(), "group": self.group.pk}
                payload.update({f"{key}_{self.student.pk}": value for key, value in checks.items()})
                payload[f"attended_{self.outsider.pk}"] = "on"
                payload[f"attended_{self.inactive.pk}"] = "on"
                expected_redirect = f"{self.url(name)}?date={self.date.isoformat()}&group={self.group.pk}"
                response = self.client.post(self.url(name), payload, follow=True)
                self.assertRedirects(response, expected_redirect)
                self.assertContains(response, "تم حفظ تحضير")
                self.assertContains(response, "بنجاح")
                total += award
                self.assert_balances(total)
                record = model.objects.get(participant=self.student)
                self.assertTrue(record.attended)
                self.assertEqual(getattr(record, date_field), self.date)
                if "early" in checks:
                    self.assertTrue(record.is_early)
                if "achieved" in checks:
                    self.assertTrue(record.achieved)
                self.assertFalse(model.objects.filter(participant__in=[self.outsider, self.inactive]).exists())
                ledger_count = PointsLedgerEntry.objects.count()
                self.client.post(self.url(name), payload)
                self.assert_balances(total)
                self.assertEqual(PointsLedgerEntry.objects.count(), ledger_count)
        self.assertEqual(list(PointsLedgerEntry.objects.values_list("event_date", flat=True)), [self.date] * 3)

    def test_note_only_save_preserves_attendance_and_balances(self):
        self.client.post(self.url("supervisor_dashboard"), {
            "date": self.date.isoformat(), "group": self.group.pk,
            f"attended_{self.student.pk}": "on", f"early_{self.student.pk}": "on",
        })
        ledger_count = PointsLedgerEntry.objects.count()
        response = self.client.post(self.url("supervisor_dashboard"), {
            "action": "save_note", "participant_id": self.student.pk, "note_text": "ملاحظة مستقلة جديدة",
            "date": self.date.isoformat(), "group": self.group.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assert_balances(10)
        record = MeetingAttendance.objects.get(participant=self.student, week_start_date=self.date)
        self.assertTrue(record.attended)
        self.assertTrue(record.is_early)
        self.assertEqual(PointsLedgerEntry.objects.count(), ledger_count)
        self.assertEqual(MeetingNote.objects.get(participant=self.student, meeting_date=self.date).text, "ملاحظة مستقلة جديدة")

    def test_circle_achievement_and_weekly_activity_are_independent(self):
        self.client.post(self.url("quran_circle_attendance"), {
            "date": self.date.isoformat(), "group": self.group.pk, f"achieved_{self.student.pk}": "on",
        })
        record = CircleAttendance.objects.get(participant=self.student, date=self.date)
        self.assertFalse(record.attended)
        self.assertTrue(record.achieved)
        self.assert_balances(2)
        self.assertFalse(WeeklyActivityAttendance.objects.exists())

    def test_activity_unchecking_reverses_existing_award_without_a_new_task(self):
        payload = {"date": self.date.isoformat(), "group": self.group.pk, f"attended_{self.student.pk}": "on"}
        self.client.post(self.url("weekly_activity_attendance"), payload)
        self.assert_balances(10)
        del payload[f"attended_{self.student.pk}"]
        self.client.post(self.url("weekly_activity_attendance"), payload)
        self.assert_balances(0)
        self.assertFalse(WeeklyActivityAttendance.objects.get(participant=self.student).attended)
        self.assertEqual(sorted(PointsLedgerEntry.objects.values_list("points_delta", flat=True)), [-10, 10])
        self.client.post(self.url("weekly_activity_attendance"), payload)
        self.assertEqual(PointsLedgerEntry.objects.count(), 2)


def inspect_compact_rosters_in_browser(output_dir=None):
    """Optional visual QA; requires Playwright/Chromium, never accesses a DB.

    Run after django.setup() with config.test_settings. This helper is separate
    from Django's portable test suite so Playwright is not an app dependency.
    """
    from types import SimpleNamespace
    from django.template.loader import render_to_string
    from playwright.sync_api import sync_playwright

    base = Path(settings.BASE_DIR)
    css = (base / "static/css/attendance-roster.css").read_text()
    js = (base / "static/js/attendance-roster.js").read_text()
    entries = []
    long_name = "عبد الرحمن بن عبد العزيز الطالب ذو الاسم الطويل بالكامل"
    for index, name in enumerate((long_name, "أحمد الطالب", "خالد الطالب"), 12):
        student = SimpleNamespace(
            id=index, pk=index, phone="0500000001", miles=index * 10, points=index,
            get_academic_stage_display=lambda: "الأول المتوسط",
            user=SimpleNamespace(full_name=name, national_id=f"80000000{index}"),
        )
        entries.append({"participant": student, "attended": False, "is_early": False, "achieved": False, "note": "ملاحظة مصطنعة"})
    group = SimpleNamespace(id=3, pk=3, name="بيئة اختبار مصطنعة")
    summary = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        try:
            for width in (360, 390, 1280):
                for template, notes in (("supervisor_dashboard", True), ("supervisor_dashboard", False), ("quran_circle_attendance", False), ("weekly_activity_attendance", False)):
                    page = browser.new_page(viewport={"width": width, "height": 1100}, device_scale_factor=1)
                    page.route("**/*", lambda route: route.abort())
                    html = render_to_string(f"participants/{template}.html", {
                        "request": SimpleNamespace(user=SimpleNamespace(full_name="مشرف اختبار", role=Role.GROUP_SUPERVISOR)),
                        "group": group, "all_groups": [group], "show_group_selector": True,
                        "is_group_locked": False, "can_view_notes": notes,
                        "roster": entries, "selected_date": datetime.date(2026, 10, 23),
                        "navbar_items": [], "messages": [],
                    })
                    html = html.replace('<link rel="stylesheet" href="/static/css/attendance-roster.css">', f"<style>{css}</style>")
                    html = html.replace('<script src="/static/js/attendance-roster.js" defer></script>', f"<script>{js}</script>")
                    page.set_content(html, wait_until="domcontentloaded")
                    metrics = page.evaluate("""() => {
                        const scroll = document.querySelector('.roster-scroll');
                        const cells = [...document.querySelector('#roster-body tr').cells].filter(cell => !cell.hidden);
                        const name = document.querySelector('.student-name-link');
                        const touch = document.querySelector('.roster-check-label').getBoundingClientRect();
                        const search = document.querySelector('#roster-search').getBoundingClientRect();
                        return {pageWidth:document.documentElement.scrollWidth, scrollWidth:scroll.scrollWidth,
                            clientWidth:scroll.clientWidth, nameWidth:cells[0].getBoundingClientRect().width,
                            nameText:name.textContent.trim(), nameScroll:name.scrollWidth, nameClient:name.clientWidth,
                            touchWidth:touch.width, touchHeight:touch.height, searchHeight:search.height,
                            rtl:cells.every((cell, index) => !index || cells[index - 1].getBoundingClientRect().x > cell.getBoundingClientRect().x)};
                    }""")
                    assert metrics["pageWidth"] <= width + 1, (template, width, metrics)
                    assert metrics["scrollWidth"] <= metrics["clientWidth"] + 1, (template, width, metrics)
                    assert metrics["nameText"] == long_name
                    assert metrics["nameScroll"] <= metrics["nameClient"] + 1
                    assert metrics["nameWidth"] <= 225
                    assert metrics["touchWidth"] >= 43 and metrics["touchHeight"] >= 44
                    assert metrics["rtl"]
                    if width < 620:
                        assert metrics["searchHeight"] == 42
                    if notes:
                        page.locator('.attendance-note summary').first.click()
                        assert page.locator('.note-save-button').first.bounding_box()["height"] >= 44
                        assert page.evaluate("document.querySelector('.roster-scroll').scrollWidth <= document.querySelector('.roster-scroll').clientWidth + 1")
                        if output_dir and width == 360:
                            Path(output_dir).mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(Path(output_dir) / "meeting-note-360.png"), full_page=True)
                        page.locator('.attendance-note summary').first.click()
                    if output_dir and width == 360:
                        Path(output_dir).mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(Path(output_dir) / f"{template}-{'notes' if notes else 'plain'}-360.png"), full_page=True)
                    page.locator('#roster-search').fill('8000000012')
                    assert page.locator('#roster-body tr:visible').count() == 1
                    page.locator('.select-all[data-target="attended"]').click()
                    assert page.locator('input[data-col="attended"]:checked').count() == 3
                    page.locator('#roster-search').fill('')
                    page.locator('#student-data-toggle').click()
                    assert page.locator('#student-data-toggle').get_attribute('aria-expanded') == 'true'
                    assert page.locator('.student-extra:visible').count() > 0
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    if width < 620:
                        assert page.evaluate("document.querySelector('.roster-scroll').scrollWidth > document.querySelector('.roster-scroll').clientWidth")
                    page.locator('#student-data-toggle').click()
                    assert page.evaluate("document.querySelector('.roster-scroll').scrollWidth <= document.querySelector('.roster-scroll').clientWidth + 1")
                    summary.append({"template": template, "notes": notes, "viewport": width, **metrics})
                    page.close()
        finally:
            browser.close()
    return summary
