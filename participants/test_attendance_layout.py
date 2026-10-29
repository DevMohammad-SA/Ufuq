"""Server-rendered attendance layout and privacy contracts without a browser."""
from html.parser import HTMLParser
from django.test import TestCase
from django.urls import reverse
from accounts.models import Role, User
from .models import Group, Participant


class FirstRosterRow(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_roster = False
        self.in_body = False
        self.in_row = False
        self.done = False
        self.cells = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody" and attrs.get("id") == "roster-body":
            self.in_body = True
        if tag == "tr" and self.in_body and not self.done:
            self.in_row = True
        if tag == "td" and self.in_row:
            self.cells.append({"attrs": attrs, "inputs": [], "links": []})
        if tag == "input" and self.in_row:
            self.cells[-1]["inputs"].append(attrs)
        if tag == "a" and self.in_row:
            self.cells[-1]["links"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "tr" and self.in_row:
            self.done = True
            self.in_row = False
        if tag == "tbody":
            self.in_body = False


class AttendanceLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_supervisor = User.objects.create_user(username="layout-group", role=Role.GROUP_SUPERVISOR)
        cls.general = User.objects.create_user(username="layout-general", role=Role.GENERAL_SUPERVISOR)
        cls.system = User.objects.create_user(username="layout-system", role=Role.SUPERADMIN)
        cls.group = Group.objects.create(name="بيئة تخطيط الحضور")
        cls.group.supervisor.add(cls.group_supervisor)
        user = User.objects.create_user(national_id="8000999001", full_name="طالب التحضير", role=Role.PARTICIPANT)
        cls.student = Participant.objects.create(user=user, group=cls.group, academic_stage="grade_7")

    def assert_layout(self, response, expected_checks):
        self.assertEqual(response.status_code, 200)
        parser = FirstRosterRow()
        parser.feed(response.content.decode())
        self.assertEqual(len(parser.cells), 8)
        self.assertIn(reverse("participants:student_detail", args=[self.student.pk]), [link["href"] for link in parser.cells[0]["links"]])
        for index, name in enumerate(expected_checks, 1):
            self.assertIn(name, [item.get("data-col") for item in parser.cells[index]["inputs"]])
            self.assertNotIn("hidden", parser.cells[index]["attrs"])
        for cell in parser.cells[3:]:
            self.assertIn("hidden", cell["attrs"])
            self.assertIn("student-extra", cell["attrs"]["class"])
        self.assertContains(response, 'id="student-data-toggle"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, "js/attendance-roster")

    def test_meeting_checks_follow_name_then_hidden_data(self):
        self.client.force_login(self.group_supervisor)
        response = self.client.get(reverse("participants:supervisor_dashboard"))
        self.assert_layout(response, ["early", "attended"])
        self.assertContains(response, f'name="note_{self.student.pk}"')
        self.assertContains(response, 'maxlength="2000"')
        self.assertContains(response, 'class="note-save-button"')
        self.assertContains(response, "حفظ الملاحظة فقط")

    def test_circle_checks_follow_name_then_hidden_data(self):
        self.client.force_login(self.group_supervisor)
        response = self.client.get(reverse("participants:quran_circle_attendance"))
        self.assert_layout(response, ["attended", "achieved"])
        self.assertNotContains(response, f'name="note_{self.student.pk}"')

    def test_general_sees_notes_and_system_never_gets_note_controls(self):
        for user in [self.general, self.system]:
            self.client.force_login(user)
            response = self.client.get(reverse("participants:supervisor_dashboard"), {"group": self.group.pk})
            self.assert_layout(response, ["early", "attended"])
            if user == self.general:
                self.assertContains(response, f'name="note_{self.student.pk}"')
            else:
                self.assertNotContains(response, f'name="note_{self.student.pk}"')
                self.assertNotIn("note", response.context["roster"][0])
