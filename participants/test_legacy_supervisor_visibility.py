"""Legacy supervisor visibility is distinct from edit/delete authorization.

All users are synthetic. Run only with config.test_settings or the explicitly
local-socket config.test_postgres_settings; neither uses a production database.
"""

import re
from datetime import date
from html.parser import HTMLParser
from itertools import product
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth.models import Group as AuthGroup, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from participants.models import (
    CircleAttendance, EliteTripAttendance, Group, MeetingAttendance, MeetingNote,
    Participant, PointsLedgerEntry, PointsResetSnapshot, TaskGradeRevision,
    TaskReviewPermission, TaskSubmission, WeeklyActivityAttendance, WeeklyTask,
)
from participants.test_supervisor_general_grant import GrantMarkup


class SupervisorRows(HTMLParser):
    """Keep each rendered table row's own text and links together."""

    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.in_body = False
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self.in_body = True
        elif tag == "tr" and self.in_body:
            self.current = {"text": "", "links": []}
            self.rows.append(self.current)
        elif tag == "a" and self.current is not None:
            self.current["links"].append(attrs.get("href", ""))

    def handle_endtag(self, tag):
        if tag == "tr":
            self.current = None
        elif tag == "tbody":
            self.in_body = False

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def for_user(self, user):
        matches = [row for row in self.rows if user.full_name in row["text"]]
        if len(matches) != 1:
            raise AssertionError(f"Expected one row for {user.username}; got {len(matches)}")
        return matches[0]


class LegacySupervisorBase(TestCase):
    password = "SyntheticLegacyPass123!"
    audit_label = "منح صلاحية المشرف العام"

    def setUp(self):
        # An administrative GENERAL actor must see its own protected row, but
        # authorization still comes from its application role, not admin flags.
        self.actor = self.user(
            "legacy-manager", Role.GENERAL_SUPERVISOR, is_staff=True,
            full_name="الحساب العام الإداري الحالي",
        )
        self.root = self.user("legacy-system", Role.SUPERADMIN)
        self.student_user = self.user("legacy-student", Role.PARTICIPANT)
        self.group = Group.objects.create(name="بيئة قديمة مصطنعة ألف")
        self.other_group = Group.objects.create(name="بيئة قديمة مصطنعة باء")
        self.student = Participant.objects.create(
            user=self.student_user, group=self.group, academic_stage="grade_7",
            points=91, miles=910, purchase_points=19,
        )
        self.matrix = {}
        for role, active, staff, superuser in product(
            (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR),
            (False, True), (False, True), (False, True),
        ):
            key = (role, active, staff, superuser)
            suffix = f"{role}-{int(active)}{int(staff)}{int(superuser)}"
            user = self.user(
                "legacy-" + suffix, role, is_active=active, is_staff=staff,
                is_superuser=superuser, full_name="صف قديم " + suffix,
            )
            self.matrix[key] = user
            self.group.supervisor.add(user)
            self.other_group.supervisor.add(user)
        self.staff = self.matrix[(Role.GROUP_SUPERVISOR, True, True, False)]
        self.staff.national_id = "8300000001"
        self.staff.must_set_password = True
        self.staff.save(update_fields=["national_id", "must_set_password"])
        self.plain = self.matrix[(Role.GROUP_SUPERVISOR, True, False, False)]
        self.participant_backed = []
        for role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR):
            user = self.user("legacy-profile-" + role.split("_")[0], role)
            Participant.objects.create(
                user=user, group=self.group, academic_stage="grade_6",
                points=7, miles=70, purchase_points=3,
            )
            self.participant_backed.append(user)
        self.delegation = TaskReviewPermission.objects.create(
            supervisor=self.staff, enabled=True, granted_by=self.plain,
        )
        self.auth_group = AuthGroup.objects.create(name="Synthetic legacy admin group")
        self.permission = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(User), codename="change_user",
        )
        self.auth_group.permissions.add(self.permission)
        self.staff.groups.add(self.auth_group)
        self.staff.user_permissions.add(self.permission)
        self.client.force_login(self.actor)
        self.list_url = reverse("participants:supervisor_list")

    def user(self, username, role, **fields):
        fields.setdefault("full_name", "اسم مصطنع " + username)
        return User.objects.create_user(
            username=username, password=self.password, role=role, **fields,
        )

    @staticmethod
    def url(action, user):
        return reverse("participants:supervisor_" + action, kwargs={"pk": user.pk})

    @staticmethod
    def rows(model):
        return list(model.objects.order_by("pk").values())

    def snapshot(self):
        return {
            model._meta.label_lower: self.rows(model) for model in (
                User, Group, Group.supervisor.through, Participant,
                AuthGroup, AuthGroup.permissions.through, User.groups.through,
                User.user_permissions.through, TaskReviewPermission, LogEntry,
                MeetingAttendance, CircleAttendance, WeeklyActivityAttendance,
                EliteTripAttendance, PointsLedgerEntry, PointsResetSnapshot,
                MeetingNote, WeeklyTask, TaskSubmission, TaskGradeRevision,
            )
        }

    def visible_ids(self, response):
        self.assertEqual(response.status_code, 200)
        return {user.pk for user in response.context["supervisors"]}

    def grant_audits(self, user=None):
        return LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(User),
            object_id=str((user or self.staff).pk),
            change_message__contains=self.audit_label,
        )

    def seed_history(self):
        today = date(2026, 10, 29)
        MeetingAttendance.objects.create(
            participant=self.student, week_start_date=today, attended=True,
            is_early=True, recorded_by=self.staff,
        )
        CircleAttendance.objects.create(
            participant=self.student, date=today, attended=True, achieved=True,
            recorded_by=self.staff,
        )
        WeeklyActivityAttendance.objects.create(
            participant=self.student, date=today, attended=True, recorded_by=self.staff,
        )
        EliteTripAttendance.objects.create(
            participant=self.student, date=today, status="present", recorded_by=self.staff,
        )
        PointsLedgerEntry.objects.create(
            participant=self.student, points_delta=10,
            source=PointsLedgerEntry.Source.EXTRA, description="سجل قديم محفوظ",
            granted_by=self.staff,
        )
        PointsResetSnapshot.objects.create(
            participant=self.student, points_before_reset=99, reset_by=self.staff,
        )
        MeetingNote.objects.create(
            participant=self.student, meeting_date=today, text="ملاحظة قديمة محفوظة",
            created_by=self.staff, updated_by=self.staff,
        )
        task = WeeklyTask.objects.create(
            title="مهمة قديمة مصطنعة", due_date=today, allowed_formats="text",
            created_by=self.staff,
        )
        submission = TaskSubmission.objects.create(
            task=task, participant=self.student, text_content="تسليم قديم محفوظ",
            status="accepted", reviewed_by=self.staff, reviewed_at=timezone.now(),
            points_awarded=True, award_value=10,
        )
        TaskGradeRevision.objects.create(
            submission=submission, old_status="rejected", new_status="accepted",
            old_featured=False, new_featured=False, old_award=0, new_award=10,
            reason="تصحيح قديم مصطنع", revised_by=self.staff,
        )
        LogEntry.objects.create(
            user=self.staff, content_type=ContentType.objects.get_for_model(Participant),
            object_id=str(self.student.pk), object_repr="سجل تاريخي مصطنع",
            action_flag=CHANGE, change_message="مرجع تاريخي محفوظ",
        )


class LegacySupervisorVisibilityTests(LegacySupervisorBase):
    def test_all_managed_role_flag_and_activation_shapes_are_visible_once(self):
        response = self.client.get(self.list_url, {"status": "all", "role": "all"})
        expected = {user.pk for user in self.matrix.values()}
        expected.update(user.pk for user in self.participant_backed)
        expected.add(self.actor.pk)
        self.assertEqual(self.visible_ids(response), expected)
        markup = SupervisorRows(response.content.decode())
        self.assertEqual(len(markup.rows), len(expected))
        for user in (*self.matrix.values(), *self.participant_backed, self.actor):
            with self.subTest(user=user.username):
                markup.for_user(user)
                self.assertContains(response, user.full_name, count=1)
        self.assertNotContains(response, self.root.full_name)
        self.assertNotContains(response, self.student_user.full_name)

    def test_row_capabilities_and_links_separate_readonly_edit_and_general_grant(self):
        response = self.client.get(self.list_url, {"status": "all"})
        markup = SupervisorRows(response.content.decode())
        backed_ids = {user.pk for user in self.participant_backed}
        for user in response.context["supervisors"]:
            with self.subTest(user=user.username):
                protected = bool(user.is_staff or user.is_superuser or user.pk in backed_ids)
                grantable = bool(
                    user.is_active and user.role == Role.GROUP_SUPERVISOR
                    and not user.is_superuser and user.pk not in backed_ids
                    and user.pk != self.actor.pk
                )
                self.assertEqual(user.can_edit_supervisor, not protected)
                self.assertEqual(user.can_grant_general, grantable)
                row = markup.for_user(user)
                links = set(row["links"])
                self.assertEqual(self.url("edit", user) in links, not protected)
                self.assertEqual(
                    self.url("delete", user) in links,
                    not protected and user.pk != self.actor.pk,
                )
                self.assertEqual(self.url("grant_general", user) in links, grantable)
                if protected:
                    self.assertTrue(user.protection_note)
                    self.assertIn(user.protection_note, row["text"])
                    self.assertRegex(user.protection_note, r"محمي|محمية|ملف طالب")

    def test_status_and_role_filters_include_protected_shapes(self):
        all_users = [*self.matrix.values(), *self.participant_backed, self.actor]
        for status, role in product(
            ("active", "inactive", "all"),
            (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR, "all"),
        ):
            with self.subTest(status=status, role=role):
                expected = {user.pk for user in all_users if (
                    (status == "all" or user.is_active == (status == "active"))
                    and (role == "all" or user.role == role)
                )}
                response = self.client.get(self.list_url, {"status": status, "role": role})
                self.assertEqual(self.visible_ids(response), expected)

    def test_search_by_name_username_or_national_id_finds_protected_staff(self):
        for query in (self.staff.full_name, self.staff.username, self.staff.national_id):
            with self.subTest(query=query):
                response = self.client.get(self.list_url, {
                    "q": "  " + query + "  ", "status": "all", "role": "all",
                })
                self.assertEqual(self.visible_ids(response), {self.staff.pk})
                self.assertEqual(response.context["q"], query)
                self.assertContains(response, self.staff.full_name)

    def test_search_has_no_match_and_invalid_roles_never_expose_system_or_participant(self):
        response = self.client.get(self.list_url, {"status": "all", "q": "no-such-synthetic-person"})
        self.assertEqual(self.visible_ids(response), set())
        self.assertContains(response, "لا يوجد مشرفون")
        for role in (Role.SUPERADMIN, Role.PARTICIPANT, "invalid-role"):
            with self.subTest(role=role):
                response = self.client.get(self.list_url, {"status": "all", "role": role})
                self.assertNotIn(self.root.pk, self.visible_ids(response))
                self.assertNotIn(self.student_user.pk, self.visible_ids(response))

    def test_header_describes_visible_protected_accounts_instead_of_claiming_they_are_hidden(self):
        response = self.client.get(self.list_url, {"status": "all"})
        self.assertContains(response, "إدارة المشرفين")
        self.assertNotContains(response, "لا تظهر حسابات النظام أو حسابات Django الإدارية هنا")
        self.assertContains(response, "بيانات الحسابات الإدارية محمية")

    def test_own_administrative_general_row_is_visible_readonly_with_no_mutation_links(self):
        response = self.client.get(self.list_url, {"status": "all"})
        row = SupervisorRows(response.content.decode()).for_user(self.actor)
        self.assertIn("حسابك", row["text"])
        self.assertIn("بيانات الحساب الإدارية محمية", row["text"])
        for action in ("edit", "delete", "grant_general"):
            self.assertNotIn(self.url(action, self.actor), row["links"])
        before = self.snapshot()
        for action in ("edit", "delete"):
            for method in (self.client.get, self.client.post):
                with self.subTest(action=action, method=method.__name__):
                    response = method(self.url(action, self.actor), {"confirm": "yes"})
                    self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_pagination_has_no_duplicates_and_preserves_search_role_and_status(self):
        added = []
        for index in range(35):
            user = self.user(
                f"legacy-page-{index:02}", Role.GROUP_SUPERVISOR,
                full_name=f"مشرف صفحات مصطنع {index:02}", is_staff=True,
            )
            self.group.supervisor.add(user)
            self.other_group.supervisor.add(user)
            added.append(user)
        filters = {"q": "مشرف صفحات مصطنع", "status": "all", "role": Role.GROUP_SUPERVISOR}
        seen = []
        for page in (1, 2):
            response = self.client.get(self.list_url, {**filters, "page": page})
            self.assertEqual(response.status_code, 200)
            seen.extend(user.pk for user in response.context["supervisors"])
            self.assertEqual(response.context["page_obj"].paginator.count, 35)
            self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
            links = [attrs["href"] for tag, attrs in GrantMarkup(response.content.decode()).elements
                     if tag == "a" and "page-link" in attrs.get("class", "").split()]
            self.assertTrue(links)
            for link in links:
                query = parse_qs(urlsplit(link).query)
                for name, value in filters.items():
                    self.assertEqual(query[name], [value])
                self.assertEqual(query["page"], [str(3 - page)])
        self.assertEqual(len(seen), 35)
        self.assertEqual(len(set(seen)), 35)
        self.assertEqual(set(seen), {user.pk for user in added})

    def test_list_get_performs_no_database_writes_and_queries_remain_bounded(self):
        # Warm permissions/content-type caches, then compare a small list with
        # many rows. Includes actual rendered group labels and row buttons.
        self.client.get(self.list_url, {"status": "all"})
        before = self.snapshot()
        with CaptureQueriesContext(connection) as small_queries:
            response = self.client.get(self.list_url, {"status": "all", "q": self.staff.national_id})
        self.assertEqual(self.visible_ids(response), {self.staff.pk})
        with CaptureQueriesContext(connection) as large_queries:
            response = self.client.get(self.list_url, {"status": "all"})
        self.assertEqual(response.status_code, 200)
        for queries in (small_queries, large_queries):
            writes = [q["sql"] for q in queries.captured_queries if re.match(
                r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", q["sql"], re.I,
            )]
            self.assertEqual(writes, [], "Listing must not normalize flags or mutate legacy rows")
        self.assertLessEqual(len(large_queries), len(small_queries) + 1)
        self.assertLessEqual(len(large_queries), 12, "Avoid per-row Participant/group queries")
        self.assertContains(response, self.group.name)
        self.assertContains(response, self.other_group.name)
        self.assertEqual(self.snapshot(), before)

    def test_visible_staff_superuser_and_participant_backed_rows_still_cannot_be_edited_or_deleted(self):
        protected = [user for user in self.matrix.values() if user.is_staff or user.is_superuser]
        protected.extend(self.participant_backed)
        before = self.snapshot()
        for user in protected:
            for action in ("edit", "delete"):
                for method in (self.client.get, self.client.post):
                    with self.subTest(user=user.username, action=action, method=method.__name__):
                        response = method(self.url(action, user), {
                            "confirm": "yes", "role": Role.SUPERADMIN,
                            "full_name": "اسم لا يحق تغييره", "is_active": "",
                            "is_staff": "", "is_superuser": "", "password1": "InjectedPass123!",
                        })
                        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_group_role_even_with_staff_flags_and_review_delegation_cannot_list_or_grant(self):
        TaskReviewPermission.objects.create(supervisor=self.plain, enabled=True, granted_by=self.actor)
        for actor in (self.plain, self.staff):
            self.client.force_login(actor)
            before = self.snapshot()
            self.assertEqual(self.client.get(self.list_url, {"status": "all"}).status_code, 403)
            self.assertEqual(self.client.get(self.url("grant_general", self.participant_backed[0])).status_code, 403)
            self.assertEqual(self.client.post(self.url("grant_general", self.staff), {"confirm": "yes"}).status_code, 403)
            self.assertEqual(self.snapshot(), before)


class LegacyStaffGeneralGrantTests(LegacySupervisorBase):
    def test_active_staff_group_get_is_readonly_and_requires_csrf_for_confirmed_grant(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        url = self.url("grant_general", self.staff)
        before = self.snapshot()
        self.assertEqual(client.post(url, {"confirm": "yes"}).status_code, 403)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url + "?confirm=yes")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.staff.full_name)
        self.assertContains(response, "يتغير دوره في رحّال فقط")
        self.assertContains(response, "صلاحيات حسابه الإدارية الحالية وكلمة مروره لا تتغير")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(re.match(r"\s*(INSERT|UPDATE|DELETE)\b", q["sql"], re.I)
                                 for q in queries.captured_queries))
        form = GrantMarkup(response.content.decode()).grant_forms()[0]
        token = next(attrs["value"] for tag, attrs in form["elements"]
                     if tag == "input" and attrs.get("name") == "csrfmiddlewaretoken")
        self.assertEqual(client.post(url, {"confirm": "yes"}).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        response = client.post(url, {"confirm": "yes", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, Role.GENERAL_SUPERVISOR)
        self.assertTrue(self.staff.is_staff)
        self.assertFalse(self.staff.is_superuser)
        self.assertEqual(self.grant_audits().count(), 1)

    def test_staff_confirmation_missing_or_duplicate_is_noop(self):
        url = self.url("grant_general", self.staff)
        before = self.snapshot()
        for payload in ({}, {"confirm": "no"}, {"confirm": ["yes", "yes"]},
                        {"confirm": ["no", "yes"]}, {"confirm": ["yes", "no"]}):
            with self.subTest(payload=payload):
                self.assertIn(self.client.post(url, payload).status_code, (200, 400))
                self.assertEqual(self.snapshot(), before)

    def test_staff_grant_changes_only_role_and_expected_links_permission_and_audit(self):
        self.seed_history()
        original_actor_session = (
            self.client.session.session_key, self.client.session[SESSION_KEY],
            self.client.session[HASH_SESSION_KEY],
        )
        target_client = Client()
        target_client.force_login(self.staff)
        original_target_session = (
            target_client.session.session_key, target_client.session[SESSION_KEY],
            target_client.session[HASH_SESSION_KEY],
        )
        before = self.snapshot()
        payload = {
            "confirm": "yes", "role": Role.SUPERADMIN,
            "user_id": str(self.plain.pk), "target_id": str(self.plain.pk),
            "actor_id": str(self.root.pk), "username": "injected-legacy-username",
            "national_id": "8999999999", "full_name": "اسم متطفل",
            "password": "InjectedPass123!", "password1": "InjectedPass123!",
            "password2": "InjectedPass123!", "must_set_password": "",
            "is_staff": "", "is_superuser": "on", "is_active": "",
            "groups": [], "user_permissions": [], "environments": [str(self.group.pk)],
            "points": "9999", "miles": "9999", "purchase_points": "9999",
        }
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url("grant_general", self.staff), payload)
        self.assertEqual(response.status_code, 302)
        user_updates = [q["sql"] for q in queries.captured_queries
                        if re.match(r'\s*UPDATE\s+"accounts_user"\s+SET\b', q["sql"], re.I)]
        self.assertEqual(len(user_updates), 1)
        assignments = re.search(r"\bSET\s+(.*?)\s+WHERE\b", user_updates[0], re.I).group(1)
        self.assertRegex(assignments, r'^"role"\s*=')
        self.assertNotIn(",", assignments, "Only the application role may be written on User")
        auth_tables = {model._meta.db_table for model in (
            AuthGroup, AuthGroup.permissions.through, User.groups.through,
            User.user_permissions.through,
        )}
        auth_writes = [q["sql"] for q in queries.captured_queries
                       if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", q["sql"], re.I)
                       and any(f'"{table}"' in q["sql"] for table in auth_tables)]
        self.assertEqual(auth_writes, [], "Grant must never write Django admin groups or permissions")
        after = self.snapshot()
        expected_users = [dict(row) for row in before["accounts.user"]]
        for row in expected_users:
            if row["id"] == self.staff.pk:
                row["role"] = Role.GENERAL_SUPERVISOR
        self.assertEqual(after["accounts.user"], expected_users)
        links_label = Group.supervisor.through._meta.label_lower
        self.assertEqual(after[links_label], [row for row in before[links_label]
                                             if row["user_id"] != self.staff.pk])
        self.delegation.refresh_from_db()
        self.assertFalse(self.delegation.enabled)
        self.assertEqual(self.delegation.granted_by_id, self.actor.pk)
        expected_delegations = [dict(row) for row in before["participants.taskreviewpermission"]]
        for row in expected_delegations:
            if row["id"] == self.delegation.pk:
                row.update(enabled=False, granted_by_id=self.actor.pk,
                           updated_at=self.delegation.updated_at)
        self.assertEqual(after["participants.taskreviewpermission"], expected_delegations)
        for label in before.keys() - {"accounts.user", links_label,
                                      "participants.taskreviewpermission", "admin.logentry"}:
            with self.subTest(table=label):
                self.assertEqual(after[label], before[label])
        audit = self.grant_audits().get()
        self.assertEqual(audit.user_id, self.actor.pk)
        self.assertEqual(audit.action_flag, CHANGE)
        self.assertEqual(len(after["admin.logentry"]), len(before["admin.logentry"]) + 1)
        self.assertEqual(self.rows(LogEntry)[:-1], before["admin.logentry"])
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_staff)
        self.assertFalse(self.staff.is_superuser)
        self.assertTrue(self.staff.must_set_password)
        self.assertTrue(self.staff.check_password(self.password))
        self.assertEqual(set(self.staff.groups.all()), {self.auth_group})
        self.assertEqual(set(self.staff.user_permissions.all()), {self.permission})
        for client, session in ((self.client, original_actor_session),
                                (target_client, original_target_session)):
            self.assertEqual((client.session.session_key, client.session[SESSION_KEY],
                              client.session[HASH_SESSION_KEY]), session)
            self.assertEqual(client.get(self.list_url).status_code, 200)
            self.assertEqual(client.get(reverse("participants:report_center")).status_code, 200)
        self.assertEqual(User.objects.filter(pk=self.actor.pk).values().get(),
                         next(row for row in before["accounts.user"] if row["id"] == self.actor.pk))
        before_repeat = self.snapshot()
        self.assertEqual(self.client.post(self.url("grant_general", self.staff),
                                         {"confirm": "yes"}).status_code, 302)
        self.assertEqual(self.snapshot(), before_repeat)
        self.assertEqual(self.grant_audits().count(), 1)

    def test_already_general_staff_confirmed_post_is_noop_including_legacy_links_and_permissions(self):
        target = self.matrix[(Role.GENERAL_SUPERVISOR, True, True, False)]
        TaskReviewPermission.objects.create(supervisor=target, enabled=True, granted_by=self.plain)
        target.groups.add(self.auth_group)
        target.user_permissions.add(self.permission)
        before = self.snapshot()
        url = self.url("grant_general", target)
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(url, {"confirm": "yes", "is_staff": "", "role": Role.SUPERADMIN})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.grant_audits(target).exists())
        self.assertTrue(any(message.level_tag == "info" for message in response.wsgi_request._messages))

    def test_superuser_and_participant_backed_group_or_general_never_receive_direct_grants(self):
        protected = [user for user in self.matrix.values() if user.is_superuser]
        protected.extend(self.participant_backed)
        for target in protected:
            with self.subTest(target=target.username):
                before = self.snapshot()
                url = self.url("grant_general", target)
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 404)
                self.assertEqual(self.snapshot(), before)

    def test_staff_inactive_or_system_role_targets_remain_ineligible(self):
        inactive = self.matrix[(Role.GROUP_SUPERVISOR, False, True, False)]
        User.objects.filter(pk=self.root.pk).update(is_staff=True)
        before = self.snapshot()
        for target in (inactive, self.root, self.student_user):
            for method in (self.client.get, self.client.post):
                with self.subTest(target=target.username, method=method.__name__):
                    self.assertEqual(method(self.url("grant_general", target), {"confirm": "yes"}).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_staff_flag_committed_after_get_does_not_block_or_get_reset_by_grant(self):
        # This flag write is outside the request's inner atomic block, so a
        # role-only grant must preserve the already-committed value.
        url = self.url("grant_general", self.plain)
        self.assertEqual(self.client.get(url).status_code, 200)
        User.objects.filter(pk=self.plain.pk).update(is_staff=True)
        before = User.objects.filter(pk=self.plain.pk).values().get()
        response = self.client.post(url, {"confirm": "yes", "is_staff": ""})
        self.assertEqual(response.status_code, 302)
        after = User.objects.filter(pk=self.plain.pk).values().get()
        self.assertEqual(after, {**before, "role": Role.GENERAL_SUPERVISOR})
        self.assertTrue(after["is_staff"])
        self.assertEqual(self.grant_audits(self.plain).count(), 1)

    def test_target_becoming_staff_between_initial_read_and_lock_is_allowed_role_only(self):
        from participants import management_views
        original = management_views.locked_users
        before = User.objects.filter(pk=self.plain.pk).values().get()

        def newly_staff(*args, **kwargs):
            self.assertIs(kwargs.get("no_key"), True)
            User.objects.filter(pk=self.plain.pk).update(is_staff=True)
            return original(*args, **kwargs)

        with patch.object(management_views, "locked_users", side_effect=newly_staff) as locking:
            response = self.client.post(self.url("grant_general", self.plain),
                                        {"confirm": "yes", "is_staff": ""})
        self.assertEqual(locking.call_count, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(pk=self.plain.pk).values().get(), {
            **before, "is_staff": True, "role": Role.GENERAL_SUPERVISOR,
        })
        self.assertEqual(self.grant_audits(self.plain).count(), 1)

    def test_staff_becoming_superuser_or_participant_backed_before_lock_is_denied(self):
        from participants import management_views
        original = management_views.locked_users
        for change in ("superuser", "participant"):
            with self.subTest(change=change):
                before = self.snapshot()

                def newly_protected(*args, **kwargs):
                    if change == "superuser":
                        User.objects.filter(pk=self.staff.pk).update(is_superuser=True)
                    else:
                        Participant.objects.create(
                            user=self.staff, group=self.group, academic_stage="grade_7",
                        )
                    return original(*args, **kwargs)

                with patch.object(management_views, "locked_users", side_effect=newly_protected) as locking:
                    response = self.client.post(self.url("grant_general", self.staff), {"confirm": "yes"})
                self.assertEqual(locking.call_count, 1)
                self.assertEqual(response.status_code, 404)
                # Mutations made by this mock are inside the request atomic
                # block and must roll back with the rejected grant.
                self.assertEqual(self.snapshot(), before)
