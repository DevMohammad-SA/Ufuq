"""Contract regressions for the explicit, non-transfer GENERAL grant.

Synthetic users only. Run this module with config.test_settings; the separate
concurrency module requires config.test_postgres_settings. Routes/views are
resolved lazily so this module does not depend on another test module.
"""

import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.template.loader import get_template
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from accounts.models import Role, User
from participants.models import (
    CircleAttendance, EliteTripAttendance, Group, MeetingAttendance, MeetingNote,
    Participant, PointsLedgerEntry, PointsResetSnapshot, TaskGradeRevision,
    TaskReviewPermission, TaskSubmission, WeeklyActivityAttendance, WeeklyTask,
)


class GrantMarkup(HTMLParser):
    """Inspect actual controls, not matching arbitrary text in the base layout."""

    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.forms = []
        self.current_form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        element = (tag, dict(attrs))
        self.elements.append(element)
        if tag == "form":
            self.current_form = {"attrs": dict(attrs), "elements": []}
            self.forms.append(self.current_form)
        elif self.current_form is not None:
            self.current_form["elements"].append(element)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None

    def grant_forms(self):
        return [form for form in self.forms if any(
            tag == "input" and attrs.get("name") == "confirm"
            for tag, attrs in form["elements"]
        )]


class SupervisorGrantBase(TestCase):
    password = "SyntheticGrantPass123!"
    audit_label = "منح صلاحية المشرف العام"

    def setUp(self):
        self.actor = self.user("grant-general", Role.GENERAL_SUPERVISOR)
        self.root = self.user("grant-root", Role.SUPERADMIN)
        self.target = self.user(
            "grant-target", Role.GROUP_SUPERVISOR,
            national_id="8200000001", full_name="المشرف المستهدف المصطنع",
            must_set_password=True,
        )
        self.third = self.user("grant-third", Role.GROUP_SUPERVISOR)
        self.other_general = self.user("grant-other-general", Role.GENERAL_SUPERVISOR)
        self.inactive = self.user("grant-inactive", Role.GROUP_SUPERVISOR, is_active=False)
        self.staff = self.user("grant-staff", Role.GROUP_SUPERVISOR, is_staff=True)
        self.superuser = self.user("grant-superuser", Role.GROUP_SUPERVISOR, is_superuser=True)
        self.student_user = self.user(
            "grant-student", Role.PARTICIPANT, national_id="8200000002",
        )
        self.group = Group.objects.create(name="بيئة منح مصطنعة ألف")
        self.other_group = Group.objects.create(name="بيئة منح مصطنعة باء")
        self.group.supervisor.add(self.target, self.third)
        self.other_group.supervisor.add(self.target)
        self.student = Participant.objects.create(
            user=self.student_user, group=self.group, academic_stage="grade_7",
            phone="0500000000", guardian_phone="0511111111",
            points=91, miles=910, purchase_points=19,
        )
        self.participant_backed = self.user("grant-legacy", Role.GROUP_SUPERVISOR)
        Participant.objects.create(
            user=self.participant_backed, group=self.other_group, academic_stage="grade_6",
            points=7, miles=70, purchase_points=3,
        )
        self.delegation = TaskReviewPermission.objects.create(
            supervisor=self.target, enabled=True, granted_by=self.third,
        )
        self.client.force_login(self.actor)
        self.list_url = reverse("participants:supervisor_list")
        self.report_url = reverse("participants:report_center")
        self.url = self.grant_url(self.target)

    def user(self, username, role, **fields):
        fields.setdefault("full_name", username)
        return User.objects.create_user(
            username=username, role=role, password=self.password, **fields,
        )

    @staticmethod
    def grant_url(user):
        return reverse("participants:supervisor_grant_general", kwargs={"pk": user.pk})

    @staticmethod
    def rows(model):
        return list(model.objects.order_by("pk").values())

    def snapshot(self):
        return {
            "users": self.rows(User),
            "groups": self.rows(Group),
            "group_links": self.rows(Group.supervisor.through),
            "participants": self.rows(Participant),
            "delegations": self.rows(TaskReviewPermission),
            "audit": self.rows(LogEntry),
            "attendance": self.rows(MeetingAttendance),
            "circle": self.rows(CircleAttendance),
            "weekly": self.rows(WeeklyActivityAttendance),
            "elite": self.rows(EliteTripAttendance),
            "points": self.rows(PointsLedgerEntry),
            "resets": self.rows(PointsResetSnapshot),
            "tasks": self.rows(WeeklyTask),
            "submissions": self.rows(TaskSubmission),
            "revisions": self.rows(TaskGradeRevision),
            "notes": self.rows(MeetingNote),
        }

    def assert_not_granted(self):
        self.target.refresh_from_db()
        self.delegation.refresh_from_db()
        self.assertEqual(self.target.role, Role.GROUP_SUPERVISOR)
        self.assertTrue(self.delegation.enabled)
        self.assertEqual(set(self.target.group_set.values_list("pk", flat=True)), {
            self.group.pk, self.other_group.pk,
        })
        self.assertFalse(LogEntry.objects.filter(change_message__contains=self.audit_label).exists())

    def grant_audits(self, target=None):
        return LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(User),
            object_id=str((target or self.target).pk),
            change_message__contains=self.audit_label,
        )


class SupervisorGeneralGrantAccessTests(SupervisorGrantBase):
    def test_route_resolves_to_explicit_class_and_expected_path(self):
        self.assertEqual(self.url, f"/participants/supervisors/{self.target.pk}/grant-general/")
        match = resolve(self.url)
        self.assertEqual(match.view_name, "participants:supervisor_grant_general")
        self.assertEqual(match.func.view_class.__name__, "SupervisorGrantGeneralView")

    def test_anonymous_get_and_post_redirect_to_supervisor_login_without_writes(self):
        client = Client()
        before = self.snapshot()
        for method in (client.get, client.post):
            with self.subTest(method=method.__name__):
                response = method(self.url, {"confirm": "yes"})
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login_supervisor"), response["Location"])
        self.assertEqual(self.snapshot(), before)

    def test_active_general_and_role_only_superadmin_can_get_read_only_confirmation(self):
        for actor in (self.actor, self.root):
            with self.subTest(actor=actor.role):
                self.assertFalse(actor.is_staff)
                self.assertFalse(actor.is_superuser)
                self.client.force_login(actor)
                before = self.snapshot()
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "participants/supervisor_grant_general_confirm.html")
                self.assertEqual(self.snapshot(), before)
                writes = [q["sql"] for q in queries.captured_queries if re.match(
                    r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", q["sql"], re.I,
                )]
                self.assertEqual(writes, [], "GET must not perform domain writes")

    def test_group_participant_and_staff_without_manager_role_cannot_get_or_post(self):
        for actor in (self.target, self.student_user, self.staff, self.superuser):
            self.client.force_login(actor)
            before = self.snapshot()
            for method in (self.client.get, self.client.post):
                with self.subTest(actor=actor.username, method=method.__name__):
                    response = method(self.grant_url(self.third), {"confirm": "yes"})
                    self.assertEqual(response.status_code, 403)
            self.assertEqual(self.snapshot(), before)

    def test_inactive_manager_cannot_get_or_post_using_an_existing_session(self):
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        before = self.snapshot()
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                self.assertIn(method(self.url, {"confirm": "yes"}).status_code, (302, 403))
        self.assertEqual(self.snapshot(), before)

    def test_actor_admin_flags_do_not_override_existing_role_based_manager_policy(self):
        for fields in ({"is_staff": True}, {"is_staff": True, "is_superuser": True}):
            with self.subTest(fields=fields):
                manager = self.user(
                    "grant-flagged-" + str(len(fields)), Role.GENERAL_SUPERVISOR, **fields,
                )
                self.client.force_login(manager)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assert_not_granted()

    def test_self_get_is_ineligible_and_confirmed_post_forbidden_without_actor_changes(self):
        before = self.snapshot()
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(self.grant_url(self.actor), {"confirm": "yes"})
                self.assertEqual(response.status_code, 404 if method.__name__ == "get" else 403)
        self.assertEqual(self.snapshot(), before)

    def test_initially_ineligible_targets_are_404_for_get_and_confirmed_post(self):
        inactive_general = self.user(
            "grant-inactive-general", Role.GENERAL_SUPERVISOR, is_active=False,
        )
        for target in (
            self.inactive, self.staff, self.superuser, self.student_user,
            self.participant_backed, self.root, inactive_general,
        ):
            for method in (self.client.get, self.client.post):
                with self.subTest(target=target.username, method=method.__name__):
                    before = self.snapshot()
                    response = method(self.grant_url(target), {"confirm": "yes"})
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(self.snapshot(), before)

    def test_missing_target_is_404(self):
        url = reverse("participants:supervisor_grant_general", kwargs={"pk": 99999999})
        before = self.snapshot()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_existing_other_general_get_is_404_but_confirmed_post_is_informational_noop(self):
        # Even inconsistent old assignments/delegations must not be cleaned by
        # an idempotent request against an already-GENERAL account.
        self.group.supervisor.add(self.other_general)
        TaskReviewPermission.objects.create(supervisor=self.other_general, enabled=True)
        url = self.grant_url(self.other_general)
        before = self.snapshot()
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(url, {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(any(message.level_tag == "info" for message in response.wsgi_request._messages))

    def test_superadmin_can_grant_without_django_admin_flags(self):
        self.client.force_login(self.root)
        before_actor = User.objects.filter(pk=self.root.pk).values().get()
        response = self.client.post(self.url, {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.role, Role.GENERAL_SUPERVISOR)
        self.assertEqual(User.objects.filter(pk=self.root.pk).values().get(), before_actor)
        self.assertEqual(self.grant_audits().get().user_id, self.root.pk)


class SupervisorGeneralGrantConfirmationTests(SupervisorGrantBase):
    def test_missing_false_malformed_and_duplicate_confirmation_never_write(self):
        cases = (
            {}, {"confirm": "no"}, {"confirm": ""}, {"confirm": "YES"},
            {"confirm": " yes "}, {"confirm": "true"},
            {"confirm": ["yes", "yes"]}, {"confirm": ["no", "yes"]},
            {"confirm": ["yes", "no"]}, {"confirm": ["", "yes"]},
        )
        before = self.snapshot()
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload)
                self.assertIn(response.status_code, (200, 400))
                self.assertEqual(self.snapshot(), before)
                self.assert_not_granted()

    def test_querystring_confirmation_does_not_authorize_a_post(self):
        before = self.snapshot()
        response = self.client.post(self.url + "?confirm=yes", {})
        self.assertIn(response.status_code, (200, 400))
        self.assertEqual(self.snapshot(), before)

    def test_csrf_requires_valid_token_obtained_from_get_confirmation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        before = self.snapshot()
        self.assertEqual(client.post(self.url, {"confirm": "yes"}).status_code, 403)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        grant_forms = GrantMarkup(response.content.decode()).grant_forms()
        self.assertEqual(len(grant_forms), 1)
        controls = grant_forms[0]["elements"]
        tokens = [attrs["value"] for tag, attrs in controls if tag == "input"
                  and attrs.get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(client.post(self.url, {"confirm": "yes"}).status_code, 403)
        self.assertEqual(client.post(self.url, {
            "confirm": "yes", "csrfmiddlewaretoken": "x" * 64,
        }).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        granted = client.post(self.url, {"confirm": "yes", "csrfmiddlewaretoken": tokens[0]})
        self.assertEqual(granted.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.role, Role.GENERAL_SUPERVISOR)

    def test_get_confirm_yes_in_query_is_read_only(self):
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url + "?confirm=yes").status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_url_target_only_and_injected_fields_cannot_change_any_other_user_field(self):
        before = self.snapshot()
        response = self.client.post(self.url, {
            "confirm": "yes", "pk": str(self.third.pk), "target": str(self.third.pk),
            "target_id": str(self.third.pk), "user_id": str(self.third.pk),
            "supervisor_id": str(self.third.pk), "actor_id": str(self.root.pk),
            "role": Role.SUPERADMIN, "full_name": "اسم متطفل", "username": "injected-name",
            "national_id": "8999999999", "password": "InjectedPass123!",
            "password1": "InjectedPass123!", "password2": "InjectedPass123!",
            "is_active": "", "is_staff": "on", "is_superuser": "on",
            "must_set_password": "", "environments": [str(self.group.pk)],
            "points": "9999", "miles": "9999", "purchase_points": "9999",
        })
        self.assertEqual(response.status_code, 302)
        after_users = self.rows(User)
        expected_users = before["users"]
        for row in expected_users:
            if row["id"] == self.target.pk:
                row["role"] = Role.GENERAL_SUPERVISOR
        self.assertEqual(after_users, expected_users)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(self.password))
        self.assertEqual(self.rows(Participant), before["participants"])
        self.assertEqual(self.grant_audits().get().user_id, self.actor.pk)

    def test_repeated_confirmed_post_changes_role_and_records_audit_exactly_once(self):
        self.assertEqual(self.client.post(self.url, {"confirm": "yes"}).status_code, 302)
        before_duplicate = self.snapshot()
        response = self.client.post(self.url, {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(), before_duplicate)
        audit = self.grant_audits().get()
        self.assertEqual(audit.action_flag, CHANGE)
        self.assertEqual(audit.user_id, self.actor.pk)
        self.assertEqual(json.loads(audit.change_message), [
            {"changed": {"fields": [self.audit_label]}},
        ])
        for private in (self.password, self.target.password, self.target.username, self.target.national_id):
            self.assertNotIn(private, audit.change_message)
        self.assertTrue(any(message.level_tag == "info" for message in response.wsgi_request._messages))

    def test_confirmation_markup_is_native_post_required_yes_no_inline_javascript_and_44px(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        source = Path(get_template(
            "participants/supervisor_grant_general_confirm.html",
        ).origin.name).read_text()
        markup = GrantMarkup(response.content.decode())
        forms = markup.grant_forms()
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]["attrs"].get("method", "").lower(), "post")
        self.assertIn(forms[0]["attrs"].get("action", ""), ("", self.url))
        confirms = [attrs for tag, attrs in forms[0]["elements"] if tag == "input"
                    and attrs.get("name") == "confirm"]
        self.assertEqual(len(confirms), 1)
        self.assertEqual(confirms[0].get("type"), "checkbox")
        self.assertEqual(confirms[0].get("value"), "yes")
        self.assertIn("required", confirms[0])
        self.assertTrue(any(
            tag == "label" and attrs.get("for") == confirms[0].get("id")
            for tag, attrs in forms[0]["elements"]
        ))
        submits = [attrs for tag, attrs in forms[0]["elements"]
                   if tag == "button" and attrs.get("type") == "submit"]
        self.assertEqual(len(submits), 1)
        self.assertIn("grant-button", submits[0].get("class", "").split())
        self.assertTrue(any(
            tag == "a" and attrs.get("href") == self.list_url
            and "cancel-button" in attrs.get("class", "").split()
            for tag, attrs in forms[0]["elements"]
        ))
        self.assertIn("{% csrf_token %}", source)
        self.assertNotRegex(source, r"(?i)<script\b|\bon(?:click|submit|change)\s*=|javascript:")
        self.assertRegex(source, r"\.confirm-check\s*\{[^}]*min-height\s*:\s*44px")
        self.assertRegex(source, r"\.grant-button\s*,\s*\.cancel-button\s*\{[^}]*min-height\s*:\s*44px")
        self.assertContains(response, self.target.full_name)
        for word in ("صلاحيات", "حسابك", "التقارير", "الطلاب"):
            self.assertContains(response, word)

    def test_list_grant_links_are_only_for_active_other_group_supervisors_and_44px(self):
        response = self.client.get(self.list_url, {"status": "all"})
        self.assertEqual(response.status_code, 200)
        markup = GrantMarkup(response.content.decode())
        grant_links = [attrs for tag, attrs in markup.elements if tag == "a"
                       and attrs.get("href", "").endswith("/grant-general/")]
        self.assertEqual({link["href"] for link in grant_links}, {
            self.grant_url(self.target), self.grant_url(self.third),
        })
        self.assertTrue(all("action-button" in attrs.get("class", "").split() for attrs in grant_links))
        source = Path(get_template("participants/supervisor_list.html").origin.name).read_text()
        self.assertRegex(source, r"\.action-button\s*\{[^}]*min-height\s*:\s*44px")
        self.assertContains(response, reverse("participants:supervisor_edit", kwargs={"pk": self.target.pk}))

    def test_legacy_general_role_edit_form_remains_available_and_functional(self):
        url = reverse("participants:supervisor_edit", kwargs={"pk": self.third.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(Role.GENERAL_SUPERVISOR, [value for value, _ in response.context["form"].fields["role"].choices])
        response = self.client.post(url, {
            "full_name": self.third.full_name, "username": self.third.username,
            "national_id": "", "role": Role.GENERAL_SUPERVISOR,
            "environments": [], "is_active": "on", "password1": "", "password2": "",
        })
        self.assertEqual(response.status_code, 302)
        self.third.refresh_from_db()
        self.assertEqual(self.third.role, Role.GENERAL_SUPERVISOR)


class SupervisorGeneralGrantIntegrityTests(SupervisorGrantBase):
    def test_existing_actor_and_target_sessions_survive_and_have_full_general_powers(self):
        target_client = Client()
        target_client.force_login(self.target)
        self.assertEqual(target_client.get(self.report_url).status_code, 403)
        self.assertEqual(target_client.get(self.list_url).status_code, 403)
        original_actor = User.objects.filter(pk=self.actor.pk).values().get()
        actor_session = (self.client.session.session_key, self.client.session[SESSION_KEY], self.client.session[HASH_SESSION_KEY])
        target_session = (target_client.session.session_key, target_client.session[SESSION_KEY], target_client.session[HASH_SESSION_KEY])
        self.assertEqual(self.client.post(self.url, {"confirm": "yes"}).status_code, 302)
        self.assertEqual(User.objects.filter(pk=self.actor.pk).values().get(), original_actor)
        for client, original_session in ((self.client, actor_session), (target_client, target_session)):
            with self.subTest(session=original_session[1]):
                self.assertEqual((client.session.session_key, client.session[SESSION_KEY], client.session[HASH_SESSION_KEY]), original_session)
                self.assertEqual(client.get(self.list_url).status_code, 200)
                self.assertEqual(client.get(self.report_url).status_code, 200)
                self.assertEqual(client.get(reverse("participants:supervisor_add")).status_code, 200)
                review = client.get(reverse("participants:weekly_task_review"))
                self.assertEqual(review.status_code, 200)
                self.assertTrue(review.context["can_manage_tasks"])
                self.assertTrue(review.context["can_review_submissions"])
        self.delegation.refresh_from_db()
        self.assertFalse(self.delegation.enabled)
        self.assertEqual(target_client.post(self.grant_url(self.third), {"confirm": "yes"}).status_code, 302)
        self.third.refresh_from_db()
        self.assertEqual(self.third.role, Role.GENERAL_SUPERVISOR)
        self.assertEqual(self.grant_audits(self.third).get().user_id, self.target.pk)
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.role, Role.GENERAL_SUPERVISOR)

    def test_all_student_balances_attendance_and_historical_actor_references_are_preserved(self):
        today = date(2026, 10, 29)
        MeetingAttendance.objects.create(participant=self.student, week_start_date=today,
                                         attended=True, is_early=True, recorded_by=self.target)
        CircleAttendance.objects.create(participant=self.student, date=today,
                                        attended=True, achieved=True, recorded_by=self.target)
        WeeklyActivityAttendance.objects.create(participant=self.student, date=today,
                                                attended=True, recorded_by=self.target)
        EliteTripAttendance.objects.create(participant=self.student, date=today,
                                           status="present", recorded_by=self.target)
        PointsLedgerEntry.objects.create(participant=self.student, points_delta=10,
                                         source=PointsLedgerEntry.Source.EXTRA,
                                         description="سجل مصطنع محفوظ", granted_by=self.target)
        PointsResetSnapshot.objects.create(participant=self.student, points_before_reset=99,
                                           reset_by=self.target)
        MeetingNote.objects.create(participant=self.student, meeting_date=today,
                                    text="ملاحظة مصطنعة", created_by=self.target, updated_by=self.target)
        task = WeeklyTask.objects.create(title="مهمة منح مصطنعة", due_date=today,
                                          allowed_formats="text", created_by=self.target)
        submission = TaskSubmission.objects.create(task=task, participant=self.student,
                                                    text_content="تسليم محفوظ", status="accepted",
                                                    reviewed_by=self.target, reviewed_at=timezone.now(),
                                                    points_awarded=True, award_value=10)
        TaskGradeRevision.objects.create(submission=submission, old_status="rejected",
                                          new_status="accepted", old_featured=False, new_featured=False,
                                          old_award=0, new_award=10, reason="تصحيح مصطنع", revised_by=self.target)
        historical_audit = LogEntry.objects.create(
            user=self.target, content_type=ContentType.objects.get_for_model(Participant),
            object_id=str(self.student.pk), object_repr="سجل تاريخي مصطنع", action_flag=CHANGE,
            change_message="مرجع تاريخي محفوظ",
        )
        before = self.snapshot()
        response = self.client.post(self.url, {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        after = self.snapshot()
        for key in ("groups", "participants", "attendance", "circle", "weekly", "elite",
                    "points", "resets", "tasks", "submissions", "revisions", "notes"):
            with self.subTest(table=key):
                self.assertEqual(after[key], before[key])
        self.assertEqual(LogEntry.objects.filter(pk=historical_audit.pk).values().get(), before["audit"][0])
        expected_users = before["users"]
        for row in expected_users:
            if row["id"] == self.target.pk:
                row["role"] = Role.GENERAL_SUPERVISOR
        self.assertEqual(after["users"], expected_users)
        self.assertEqual(after["group_links"], [row for row in before["group_links"] if row["user_id"] != self.target.pk])
        self.delegation.refresh_from_db()
        self.assertEqual(self.delegation.pk, before["delegations"][0]["id"])
        self.assertFalse(self.delegation.enabled)
        self.assertEqual(self.delegation.granted_by_id, self.actor.pk)
        self.assertEqual(self.grant_audits().count(), 1)
        self.assertEqual(len(after["audit"]), len(before["audit"]) + 1)

    def test_missing_or_disabled_delegation_does_not_block_general_grant(self):
        for enabled in (None, False):
            with self.subTest(enabled=enabled):
                target = self.user("grant-no-delegation-" + str(enabled), Role.GROUP_SUPERVISOR)
                if enabled is False:
                    permission = TaskReviewPermission.objects.create(supervisor=target, enabled=False)
                    old = TaskReviewPermission.objects.filter(pk=permission.pk).values().get()
                self.assertEqual(self.client.post(self.grant_url(target), {"confirm": "yes"}).status_code, 302)
                target.refresh_from_db()
                self.assertEqual(target.role, Role.GENERAL_SUPERVISOR)
                if enabled is None:
                    self.assertFalse(TaskReviewPermission.objects.filter(supervisor=target).exists())
                else:
                    self.assertEqual(TaskReviewPermission.objects.filter(pk=permission.pk).values().get(), old)

    def test_locked_users_is_called_with_no_key_inside_atomic_and_both_ids(self):
        from participants import management_views
        original = management_views.locked_users
        calls = []
        initial_atomic_depth = len(connection.atomic_blocks)

        def inspected_lock(*args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            self.assertGreater(len(connection.atomic_blocks), initial_atomic_depth)
            self.assertEqual(args[:2], (self.actor.pk, self.target.pk))
            self.assertIs(kwargs.get("no_key"), True)
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        with patch.object(management_views, "locked_users", side_effect=inspected_lock):
            self.assertEqual(self.client.post(self.url, {"confirm": "yes"}).status_code, 302)
        self.assertEqual(len(calls), 1)

    def test_actor_losing_role_or_activation_after_initial_read_is_denied_under_lock(self):
        from participants import management_views
        original = management_views.locked_users
        for change in ({"role": Role.GROUP_SUPERVISOR}, {"role": Role.PARTICIPANT}, {"is_active": False}):
            with self.subTest(change=change):
                User.objects.filter(pk=self.actor.pk).update(role=Role.GENERAL_SUPERVISOR, is_active=True)
                self.actor.refresh_from_db()
                self.client.force_login(self.actor)

                def changed_actor(*args, **kwargs):
                    User.objects.filter(pk=self.actor.pk).update(**change)
                    return original(*args, **kwargs)

                with patch.object(management_views, "locked_users", side_effect=changed_actor) as locking:
                    response = self.client.post(self.url, {"confirm": "yes"})
                self.assertTrue(locking.called)
                self.assertEqual(response.status_code, 403)
                self.assert_not_granted()
                # The mock mutation is in the request's atomic block, unlike
                # an external committed transaction, so it rolls back too.

    def test_target_becoming_ineligible_after_initial_read_is_denied_under_lock(self):
        from participants import management_views
        original = management_views.locked_users
        cases = ({"is_active": False}, {"is_staff": True}, {"is_superuser": True},
                 {"role": Role.PARTICIPANT}, {"role": Role.SUPERADMIN}, {"participant": True})
        for index, change in enumerate(cases):
            with self.subTest(change=change):
                target = self.user(f"grant-race-target-{index}", Role.GROUP_SUPERVISOR)
                self.group.supervisor.add(target)
                permission = TaskReviewPermission.objects.create(supervisor=target, enabled=True)

                def changed_target(*args, **kwargs):
                    if "participant" in change:
                        Participant.objects.create(user=target, group=self.group, academic_stage="grade_7")
                    else:
                        User.objects.filter(pk=target.pk).update(**change)
                    return original(*args, **kwargs)

                with patch.object(management_views, "locked_users", side_effect=changed_target) as locking:
                    response = self.client.post(self.grant_url(target), {"confirm": "yes"})
                self.assertTrue(locking.called)
                if "is_active" in change:
                    self.assertIn(response.status_code, (403, 404))
                else:
                    self.assertEqual(response.status_code, 404)
                self.assertTrue(self.group.supervisor.filter(pk=target.pk).exists())
                permission.refresh_from_db()
                self.assertTrue(permission.enabled)
                self.assertFalse(self.grant_audits(target).exists())

    def test_target_becoming_general_before_lock_is_idempotent_without_second_audit(self):
        from participants import management_views
        original = management_views.locked_users
        before_groups = self.rows(Group.supervisor.through)
        before_delegation = self.rows(TaskReviewPermission)

        def already_promoted(*args, **kwargs):
            User.objects.filter(pk=self.target.pk).update(role=Role.GENERAL_SUPERVISOR)
            return original(*args, **kwargs)

        with patch.object(management_views, "locked_users", side_effect=already_promoted) as locking:
            response = self.client.post(self.url, {"confirm": "yes"})
        self.assertTrue(locking.called)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows(Group.supervisor.through), before_groups)
        self.assertEqual(self.rows(TaskReviewPermission), before_delegation)
        self.assertFalse(self.grant_audits().exists())

    def test_audit_failure_rolls_back_role_groups_permission_and_created_audit(self):
        from participants import management_views
        original = management_views.record_management
        before = self.snapshot()

        def failed_audit(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Synthetic audit failure after insert")

        with patch.object(management_views, "record_management", side_effect=failed_audit) as recording:
            with self.assertRaisesRegex(RuntimeError, "Synthetic audit failure"):
                self.client.post(self.url, {"confirm": "yes"})
        self.assertEqual(recording.call_count, 1)
        self.assertEqual(self.snapshot(), before)
        self.assert_not_granted()
