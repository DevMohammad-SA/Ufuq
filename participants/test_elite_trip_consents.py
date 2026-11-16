"""Synthetic consent regressions; use config.test_settings, never a live database.

All ordinary cases use the real ORM, middleware and application endpoints. Only
PDF engine failure/HTML capture tests mock a renderer. Native PDF rendering runs
in a fresh bounded subprocess so Pango is never imported into the test runner.
No TestCase is imported into another test module (Django discovers imported ones).
"""
import base64
import datetime
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from html import unescape
from pathlib import Path
from types import ModuleType
from unittest import mock

from PIL import Image, ImageDraw, PngImagePlugin
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import (
    CircleAttendance, EliteTripAttendance, Group, MeetingAttendance, Participant,
    PointsLedgerEntry, SupervisorReportPermission, WeeklyActivityAttendance,
)


def signature_png(*, blank=False, alpha=False, size=(400, 160), metadata=False):
    image = Image.new("RGBA" if alpha else "RGB", size,
                      (255, 255, 255, 0) if alpha else "white")
    if not blank:
        ImageDraw.Draw(image).line([(30, 100), (90, 40), (160, 115), (220, 50), (330, 90)],
                                  fill=(0, 0, 0, 255) if alpha else "black", width=5)
    stream = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text("sensitive", "must-be-stripped")
    image.save(stream, format="PNG", pnginfo=info)
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def hidden_value(response, name):
    html = response.content.decode("utf-8")
    for tag in re.findall(r"<input\b[^>]*>", html, flags=re.I):
        attrs = dict(re.findall(r'([\w-]+)=[\"\']([^\"\']*)[\"\']', tag))
        if attrs.get("name") == name:
            return unescape(attrs.get("value", ""))
    raise AssertionError(f"Response lacks hidden field {name!r}")


def trip_models():
    return tuple(apps.get_model("participants", name) for name in (
        "EliteTrip", "EliteTripNomination", "GuardianTripConsent", "EliteTripAudit"))


class EliteTripFixtureMixin:
    """Plain helper, not a TestCase, reusable by TransactionTestCase."""
    @classmethod
    def seed(cls, *, roster_size=16):
        cls.general = cls.make_user("general", Role.GENERAL_SUPERVISOR)
        cls.general2 = cls.make_user("general-two", Role.GENERAL_SUPERVISOR)
        cls.system = cls.make_user("system", Role.SUPERADMIN)
        cls.group_supervisor = cls.make_user("group-supervisor", Role.GROUP_SUPERVISOR)
        cls.limited = cls.make_user("limited", Role.LIMITED_SUPERVISOR)
        cls.group_a = Group.objects.create(name="بيئة أ مصطنعة")
        cls.group_b = Group.objects.create(name="بيئة ب مصطنعة")
        cls.group_a.supervisor.add(cls.group_supervisor)
        cls.a = [cls.make_student(f"a-{i}", cls.group_a, 100 if i < 14 else 1)
                 for i in range(roster_size)]
        cls.b = [cls.make_student(f"b-{i}", cls.group_b, 80 if i < 14 else 0)
                 for i in range(roster_size)]
        cls.ungrouped = cls.make_student("ungrouped", None, 9000)
        cls.inactive = cls.make_student("inactive", cls.group_a, 9999)
        User.objects.filter(pk=cls.inactive.user_id).update(is_active=False)
        cls.wrong_role = cls.make_student("wrong-role", cls.group_a, 9999)
        User.objects.filter(pk=cls.wrong_role.user_id).update(role=Role.LIMITED_SUPERVISOR)
        SupervisorReportPermission.objects.create(
            supervisor=cls.limited, view_reports=True, prepare_reports=True,
            granted_by=cls.general)

    @staticmethod
    def make_user(name, role):
        username = f"syn-consent-{name}"
        assert len(username) <= User._meta.get_field("username").max_length
        return User.objects.create_user(username=username,
            full_name=f"اسم مصطنع {name}", role=role, password="SyntheticOnly123!",
            must_set_password=False)

    @classmethod
    def make_student(cls, name, group, points):
        user = cls.make_user(name, Role.PARTICIPANT)
        return Participant.objects.create(user=user, group=group, academic_stage="grade_7",
            points=points, miles=points * 7, purchase_points=points + 3)

    def url(self, name, obj=None):
        return reverse("participants:" + name, kwargs={"pk": obj.pk} if obj else None)

    def login(self, user, *, csrf=False):
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(user)
        return client

    def create_trip(self, client=None):
        Trip, _, _, _ = trip_models()
        client = client or self.client
        before = set(Trip.objects.values_list("pk", flat=True))
        result = client.post(self.url("elite_trip_list"), {"action": "create"})
        self.assertIn(result.status_code, (200, 302))
        created = Trip.objects.exclude(pk__in=before).get()
        self.assertEqual(created.status, "draft")
        return created

    def draft_data(self, trip, **changes):
        data = {
            "action": "save", "version": str(trip.version),
            "title": "رحلة مصطنعة آمنة", "date": "2027-02-12", "time": "10:00",
            "location": "الموقع المصطنع", "fee": "0.00", "meeting_point": "نقطة تجمع مصطنعة",
            "departure_time": "09:00", "return_time": "18:00",
            "instructions": "تعليمات تركيبية فقط", "details": "تفاصيل تركيبية فقط",
            "scope_group": "", "selected_ids": [str(p.pk) for p in self.a[:13] + self.b[:13]],
        }
        data.update(changes)
        return data

    def save_trip(self, trip, **changes):
        response = self.client.post(self.url("elite_trip_edit", trip), self.draft_data(trip, **changes))
        self.assertIn(response.status_code, (200, 302), response.content[:500])
        trip.refresh_from_db()
        return response

    def approve_trip(self, trip, **changes):
        data = {"action": "approve", "confirm": "yes", "version": str(trip.version)}
        data.update(changes)
        response = self.client.post(self.url("elite_trip_review", trip), data)
        self.assertIn(response.status_code, (200, 302), response.content[:500])
        trip.refresh_from_db()
        self.assertEqual(trip.status, "approved")
        return response

    def approved_trip(self, **changes):
        trip = self.create_trip()
        self.save_trip(trip, **changes)
        self.approve_trip(trip)
        return trip

    def nomination(self, trip, participant=None):
        _, Nomination, _, _ = trip_models()
        return Nomination.objects.get(trip=trip, participant=participant or self.a[0])

    def consent_data(self, **changes):
        data = {"action": "review", "decision": "yes", "guardian_name": "ولي مصطنع",
                "guardian_relation": "father", "notes": "", "declaration_accepted": "on",
                "signature": signature_png()}
        data.update(changes)
        return data

    def review_reply(self, nomination, client=None, **changes):
        client = client or self.login(nomination.participant.user)
        payload = self.consent_data(**changes)
        response = client.post(self.url("trip_invitation", nomination), payload)
        self.assertEqual(response.status_code, 200, response.content[:500])
        payload.update(action="submit", confirm="yes", review_token=hidden_value(response, "review_token"))
        return client, payload, response

    def final_reply(self, nomination, **changes):
        _, _, Consent, _ = trip_models()
        client, payload, _ = self.review_reply(nomination, **changes)
        response = client.post(self.url("trip_invitation", nomination), payload)
        self.assertIn(response.status_code, (200, 302), response.content[:500])
        return Consent.objects.get(nomination=nomination), response

    def assert_private(self, response):
        self.assertIn("no-store", response.get("Cache-Control", ""))
        self.assertIn("private", response.get("Cache-Control", ""))
        self.assertIn("no-store", response.get("CDN-Cache-Control", ""))
        self.assertEqual(response.get("Referrer-Policy"), "same-origin")

    def domain_state(self):
        result = {}
        for label in ("accounts", "participants"):
            for model in apps.get_app_config(label).get_models():
                if not model._meta.proxy:
                    result[model._meta.label] = list(model.objects.order_by("pk").values())
        return result


class EliteTripFixtureTransportTests(TestCase):
    """Fixture regressions only; existing consent/privacy assertions stay intact."""

    def test_real_client_session_cookie_omits_absent_attributes_on_wire(self):
        from http.cookies import SimpleCookie
        from scripts.elite_trip_fixture import transport_cookie
        user = EliteTripFixtureMixin.make_user("cookie", Role.GENERAL_SUPERVISOR)
        self.client.force_login(user)
        source = self.client.cookies[settings.SESSION_COOKIE_NAME]
        wire = transport_cookie(source)
        self.assertNotIn("=None", wire)
        parsed = SimpleCookie()
        parsed.load(wire)
        self.assertEqual(parsed[source.key].value, source.value)
        for attribute in ("domain", "expires", "max-age"):
            if source[attribute] is None or source[attribute] == "":
                self.assertEqual(parsed[source.key][attribute], "")
        self.assertEqual(parsed[source.key]["path"], "/")

    def test_transport_preserves_encoded_value_and_present_security_attributes(self):
        from http.cookies import SimpleCookie
        from scripts.elite_trip_fixture import transport_cookie
        source = SimpleCookie()
        source["sessionid"] = "encoded;value with spaces"
        cookie = source["sessionid"]
        attributes = {"path": "/private/", "domain": "localhost", "max-age": 123,
                      "expires": "Wed, 01 Jan 2031 00:00:00 GMT", "secure": True,
                      "httponly": True, "samesite": "Strict"}
        for key, value in attributes.items():
            cookie[key] = value
        wire = transport_cookie(cookie)
        self.assertIn(cookie.coded_value, wire)
        parsed = SimpleCookie()
        parsed.load(wire)
        self.assertEqual(parsed["sessionid"].value, cookie.value)
        for attribute, value in attributes.items():
            self.assertEqual(str(parsed["sessionid"][attribute]), str(value))


class EliteTripDatabaseTests(EliteTripFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seed()

    def setUp(self):
        self.client.force_login(self.general)

    def test_create_initializes_top_thirteen_per_group_by_current_points_then_id(self):
        trip = self.create_trip()
        _, Nomination, Consent, _ = trip_models()
        rows = Nomination.objects.filter(trip=trip)
        self.assertEqual(set(rows.values_list("participant_id", flat=True)),
                         {p.pk for p in self.a + self.b})
        for group, students in ((self.group_a, self.a), (self.group_b, self.b)):
            selected = rows.filter(participant__group=group, is_selected=True)
            self.assertEqual(set(selected.values_list("participant_id", flat=True)),
                             {p.pk for p in students[:13]})
        self.assertFalse(Consent.objects.exists())
        self.assertFalse(rows.exclude(notified_at=None).exists())
        self.assertIsNone(trip.fee)
        self.assertIsNone(trip.date)

    def test_current_points_not_miles_reset_snapshots_or_legacy_attendance_drive_selection(self):
        Participant.objects.filter(pk=self.a[-1].pk).update(points=500, miles=0)
        EliteTripAttendance.objects.create(participant=self.a[-2], date=datetime.date(2027, 1, 1),
                                          status="present", recorded_by=self.general)
        trip = self.create_trip()
        self.assertTrue(self.nomination(trip, self.a[-1]).is_selected)
        self.assertFalse(self.nomination(trip, self.a[-2]).is_selected)
        self.assertFalse(trip_models()[2].objects.exists())

    def test_gets_create_and_approve_nothing_and_draft_never_notifies(self):
        Trip, Nomination, Consent, Audit = trip_models()
        self.client.get(self.url("elite_trip_list"), {"action": "create"})
        self.assertFalse(Trip.objects.exists())
        trip = self.create_trip()
        before = self.domain_state()
        for name in ("elite_trip_edit", "elite_trip_review", "elite_trip_responses"):
            response = self.client.get(self.url(name, trip), {"action": "approve", "confirm": "yes"})
            self.assertIn(response.status_code, (200, 400, 404))
            self.assert_private(response)
        self.assertEqual(self.domain_state(), before)
        self.save_trip(trip)
        self.assertFalse(Nomination.objects.filter(trip=trip, notified_at__isnull=False).exists())
        self.assertFalse(Consent.objects.exists())
        self.assertNotContains(self.login(self.a[0].user).get(self.url("student_trips")), trip.title)

    def test_manual_add_exclude_and_reason_and_version_are_preserved(self):
        trip = self.create_trip()
        selected = [self.a[-1].pk, self.b[-1].pk]
        self.save_trip(trip, selected_ids=selected, **{f"reason_{self.a[-1].pk}": "ترشيح يدوي مصطنع"})
        self.assertEqual(trip.version, 2)
        self.assertTrue(self.nomination(trip, self.a[-1]).is_selected)
        self.assertFalse(self.nomination(trip, self.a[0]).is_selected)
        self.assertEqual(self.nomination(trip, self.a[-1]).reason, "ترشيح يدوي مصطنع")
        self.save_trip(trip, scope_group=str(self.group_a.pk), selected_ids=selected)
        self.assertTrue(self.nomination(trip, self.b[-1]).is_selected,
                        "Changing draft scope must not erase other environments' choices")

    def test_single_group_scope_freezes_only_in_scope_invites_and_retains_other_draft_choices(self):
        trip = self.approved_trip(scope_group=str(self.group_a.pk))
        _, Nomination, _, _ = trip_models()
        for p in self.b:
            response = self.login(p.user).get(self.url("student_trips"))
            self.assertNotContains(response, trip.title)
        self.assertFalse(Nomination.objects.filter(trip=trip, participant__in=self.b,
                                                   notified_at__isnull=False).exists())
        self.assertContains(self.login(self.a[0].user).get(self.url("student_trips")), trip.title)
        self.assertNotContains(self.login(self.ungrouped.user).get(self.url("student_trips")), trip.title)

    def test_all_scope_notifies_only_frozen_active_eligible_roster(self):
        trip = self.approved_trip()
        for p in (self.a[0], self.b[0], self.a[-1]):
            self.assertContains(self.login(p.user).get(self.url("student_trips")), trip.title)
        self.assertNotContains(self.login(self.ungrouped.user).get(self.url("student_trips")), trip.title)
        self.assertFalse(trip_models()[1].objects.filter(trip=trip,
            participant__in=[self.inactive, self.wrong_role, self.ungrouped],
            notified_at__isnull=False).exists())
        self.assertEqual(len(trip.snapshot_sha256), 64)
        self.assertIsNotNone(trip.approved_at)

    def test_invalid_draft_data_and_duplicate_or_injected_ids_are_atomic(self):
        trip = self.create_trip()
        cases = [
            {"fee": "-0.01"}, {"fee": "NaN"}, {"fee": "Infinity"},
            {"fee": "0.001"}, {"date": "2027-02-30"}, {"date": "not-date"},
            {"time": "25:00"}, {"departure_time": "11:00", "time": "10:00"},
            {"return_time": "10:00", "time": "10:00"}, {"title": "x" * 501},
            {"scope_group": "99999999"}, {"scope_group": "1 OR 1=1"},
            {"selected_ids": [self.ungrouped.pk]}, {"selected_ids": [self.inactive.pk]},
            {"selected_ids": [99999999]}, {"selected_ids": ["1); DROP TABLE accounts_user;--"]},
            {"selected_ids": [self.a[0].pk, self.a[0].pk]},
            {"version": [str(trip.version), str(trip.version)]},
            {"scope_group": [str(self.group_a.pk), str(self.group_b.pk)]},
            {f"reason_{self.a[0].pk}": "x" * 251},
        ]
        for change in cases:
            with self.subTest(change=change):
                before = self.domain_state()
                response = self.client.post(self.url("elite_trip_edit", trip), self.draft_data(trip, **change))
                self.assertIn(response.status_code, (200, 400, 404, 409))
                if response.status_code == 200:
                    self.assertTrue(response.context["form"].errors)
                self.assert_private(response)
                self.assertEqual(self.domain_state(), before)

    def test_zero_fee_valid_and_departure_may_equal_start_but_return_must_be_later(self):
        trip = self.create_trip()
        self.save_trip(trip, fee="0", departure_time="10:00", time="10:00", return_time="10:01")
        self.approve_trip(trip)
        self.assertEqual(trip.fee, 0)

    def test_approval_requires_complete_nonempty_selection_explicit_single_confirmation_and_version(self):
        trip = self.create_trip()
        for changes in ({}, {"confirm": ""}, {"confirm": "no"},
                        {"confirm": ["yes", "yes"]}, {"version": "0"}, {"version": "invalid"}):
            with self.subTest(changes=changes):
                before = self.domain_state()
                data = {"action": "approve", "version": str(trip.version), "confirm": "yes", **changes}
                response = self.client.post(self.url("elite_trip_review", trip), data)
                self.assertIn(response.status_code, (400, 409))
                self.assertEqual(self.domain_state(), before)
        response = self.client.post(self.url("elite_trip_edit", trip), self.draft_data(trip, selected_ids=[]))
        self.assertEqual(response.status_code, 400)
        self.save_trip(trip, selected_ids=[self.a[0].pk], scope_group=str(self.group_b.pk))
        before = self.domain_state()
        response = self.client.post(self.url("elite_trip_review", trip),
                                   {"action": "approve", "version": trip.version, "confirm": "yes"})
        self.assertIn(response.status_code, (400, 409))
        self.assertEqual(self.domain_state(), before)

    def test_save_and_approval_stale_versions_cannot_overwrite_newer_draft(self):
        trip = self.create_trip()
        old = trip.version
        self.save_trip(trip)
        before = self.domain_state()
        for name, data in (("elite_trip_edit", self.draft_data(trip, version=old, title="تعديل قديم")),
                           ("elite_trip_review", {"action": "approve", "confirm": "yes", "version": old})):
            response = self.client.post(self.url(name, trip), data)
            self.assertIn(response.status_code, (400, 409))
            self.assertEqual(self.domain_state(), before)

    def test_snapshot_frozen_after_profile_transfer_points_change_and_deactivation(self):
        trip = self.approved_trip(scope_group=str(self.group_a.pk))
        n = self.nomination(trip)
        original = {field: getattr(n, field) for field in (
            "student_name", "group_name", "group_snapshot_id", "points_snapshot", "rank_snapshot")}
        frozen, fingerprint = trip.snapshot, trip.snapshot_sha256
        Participant.objects.filter(pk=self.a[0].pk).update(group=self.group_b, points=777)
        User.objects.filter(pk=self.a[0].user_id).update(full_name="اسم حالي مختلف", is_active=False)
        Group.objects.filter(pk=self.group_a.pk).update(name="بيئة أعيد تسميتها")
        trip.refresh_from_db(); n.refresh_from_db()
        self.assertEqual(trip.snapshot, frozen)
        self.assertEqual(trip.snapshot_sha256, fingerprint)
        self.assertEqual({field: getattr(n, field) for field in original}, original)
        response = self.client.get(self.url("elite_trip_responses", trip))
        self.assertContains(response, original["student_name"])
        self.assertNotContains(response, "اسم حالي مختلف")

    def test_transferred_or_deactivated_candidate_before_approval_is_revalidated(self):
        trip = self.create_trip()
        self.save_trip(trip, scope_group=str(self.group_a.pk), selected_ids=[self.a[0].pk, self.a[1].pk])
        Participant.objects.filter(pk=self.a[0].pk).update(group=self.group_b)
        User.objects.filter(pk=self.a[1].user_id).update(is_active=False)
        response = self.client.post(self.url("elite_trip_review", trip),
            {"action": "approve", "confirm": "yes", "version": trip.version})
        self.assertIn(response.status_code, (400, 409), "No current in-scope selection remains")
        trip.refresh_from_db()
        self.assertEqual(trip.status, "draft")
        self.assertFalse(trip_models()[1].objects.filter(trip=trip, notified_at__isnull=False).exists())

    def test_approved_content_cannot_be_edited_or_approved_twice(self):
        trip = self.approved_trip()
        before = self.domain_state()
        response = self.client.post(self.url("elite_trip_edit", trip), self.draft_data(trip, title="تغيير ممنوع"))
        self.assertIn(response.status_code, (400, 403, 409))
        self.assertEqual(self.domain_state(), before)
        response = self.client.post(self.url("elite_trip_review", trip),
            {"action": "approve", "confirm": "yes", "version": trip.version})
        self.assertIn(response.status_code, (200, 302, 400, 409))
        self.assertEqual(self.domain_state(), before)

    def test_only_active_general_can_administer_even_if_limited_has_report_permission(self):
        trip = self.create_trip()
        routes = [("elite_trip_list", None), ("elite_trip_edit", trip),
                  ("elite_trip_review", trip), ("elite_trip_responses", trip)]
        for user in (self.system, self.group_supervisor, self.limited, self.a[0].user):
            client = self.login(user)
            before = self.domain_state()
            for name, obj in routes:
                with self.subTest(user=user.role, route=name):
                    response = client.get(self.url(name, obj))
                    self.assertIn(response.status_code, (403, 404))
                    self.assert_private(response)
                    response = client.post(self.url(name, obj), {"action": "create", "confirm": "yes"})
                    self.assertIn(response.status_code, (403, 404))
                    self.assert_private(response)
            self.assertEqual(self.domain_state(), before)
        client = self.login(self.general)
        User.objects.filter(pk=self.general.pk).update(is_active=False)
        self.assertNotEqual(client.get(self.url("elite_trip_list")).status_code, 200)

    def test_anonymous_redirect_denial_missing_object_and_invalid_post_are_private(self):
        trip = self.create_trip()
        paths = [self.url("elite_trip_list"), self.url("elite_trip_edit", trip),
                 self.url("student_trips"), self.url("trip_invitation", self.nomination(trip))]
        for path in paths:
            response = Client().get(path)
            self.assertIn(response.status_code, (302, 403))
            self.assert_private(response)
        response = self.client.get(self.url("elite_trip_edit", type("Pk", (), {"pk": 9999999})()))
        self.assertEqual(response.status_code, 404)
        self.assert_private(response)

    def test_csrf_is_required_for_creation_save_approve_review_and_final(self):
        trip = self.approved_trip()
        n = self.nomination(trip)
        admin = self.login(self.general, csrf=True)
        student = self.login(self.a[0].user, csrf=True)
        paths = [(admin, self.url("elite_trip_list"), {"action": "create"}),
                 (admin, self.url("elite_trip_edit", trip), self.draft_data(trip)),
                 (admin, self.url("elite_trip_review", trip), {"action": "approve", "confirm": "yes"}),
                 (student, self.url("trip_invitation", n), self.consent_data()),
                 (student, self.url("trip_invitation", n), self.consent_data(action="submit", confirm="yes"))]
        before = self.domain_state()
        for client, path, data in paths:
            response = client.post(path, data)
            self.assertEqual(response.status_code, 403)
            self.assert_private(response)
        self.assertEqual(self.domain_state(), before)
        fresh = self.login(self.general, csrf=True)
        page = fresh.get(self.url("elite_trip_list"))
        token = hidden_value(page, "csrfmiddlewaretoken")
        response = fresh.post(self.url("elite_trip_list"), {"action": "create", "csrfmiddlewaretoken": token})
        self.assertIn(response.status_code, (200, 302))

    def test_non_candidate_has_red_motivation_without_detail_form_or_other_student_data(self):
        trip = self.approved_trip()
        client = self.login(self.a[-1].user)
        response = client.get(self.url("student_trips"))
        self.assertContains(response, "غير مرشح لهذه الرحلة")
        self.assertContains(response, "الفرصة أمامك")
        for p in self.a[:-1] + self.b:
            self.assertNotContains(response, p.user.full_name)
        self.assertNotContains(response, 'name="guardian_name"')
        for method in (client.get, client.post):
            result = method(self.url("trip_invitation", self.nomination(trip, self.a[-1])), self.consent_data())
            self.assertIn(result.status_code, (403, 404))
            self.assert_private(result)

    def test_student_invitation_idor_and_draft_or_out_of_scope_are_rejected(self):
        draft = self.create_trip()
        approved = self.approved_trip(scope_group=str(self.group_a.pk))
        client = self.login(self.a[0].user)
        for n in (self.nomination(draft), self.nomination(approved, self.a[1]),
                  self.nomination(approved, self.b[0])):
            for method in (client.get, client.post):
                response = method(self.url("trip_invitation", n), self.consent_data())
                self.assertIn(response.status_code, (403, 404))
                self.assert_private(response)
        response = client.get(self.url("trip_invitation", self.nomination(approved)))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.a[1].user.full_name)

    def test_review_does_not_persist_and_decision_has_no_default(self):
        trip = self.approved_trip()
        n = self.nomination(trip)
        client = self.login(self.a[0].user)
        page = client.get(self.url("trip_invitation", n))
        self.assertNotRegex(page.content.decode(), r'<input[^>]*name="decision"[^>]*checked')
        _, _, Consent, _ = trip_models()
        _, _, review = self.review_reply(n, client=client)
        self.assertFalse(Consent.objects.exists())
        self.assertContains(review, "لم يُتحقق")
        response = client.post(self.url("trip_invitation", n), self.consent_data(action="submit", confirm="yes"))
        self.assertIn(response.status_code, (400, 409))
        self.assertFalse(Consent.objects.exists())

    def test_invalid_guardian_fields_limits_and_duplicate_scalars_do_not_persist(self):
        trip = self.approved_trip()
        n = self.nomination(trip)
        client = self.login(self.a[0].user)
        cases = [{"decision": ""}, {"decision": "maybe"}, {"guardian_name": ""},
                 {"guardian_name": " "}, {"guardian_name": "x" * 101},
                 {"guardian_relation": ""}, {"guardian_relation": "uncle"},
                 {"notes": "x" * 1501}, {"declaration_accepted": ""},
                 {"decision": ["yes", "no"]}, {"guardian_name": ["أ", "ب"]}]
        for changes in cases:
            with self.subTest(changes=changes):
                response = client.post(self.url("trip_invitation", n), self.consent_data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assert_private(response)
        self.assertFalse(trip_models()[2].objects.exists())

    def test_yes_and_no_replies_persist_account_declaration_with_server_time_reference_and_hashes(self):
        trip = self.approved_trip()
        before = timezone.now()
        for decision, participant, relation in (("yes", self.a[0], "father"), ("no", self.a[1], "mother")):
            consent, response = self.final_reply(self.nomination(trip, participant), decision=decision,
                guardian_name="اسم ولي مصطنع", guardian_relation=relation, notes="سطر أول\nسطر ثان")
            self.assertEqual(consent.decision, decision)
            self.assertEqual(consent.verification_method, "account_declaration")
            self.assertTrue(consent.declaration_accepted)
            self.assertEqual(consent.submitted_by_id, participant.user_id)
            self.assertGreaterEqual(consent.submitted_at, before)
            self.assertEqual(str(consent.reference).count("-"), 4)
            self.assertEqual(consent.trip_sha256, trip.snapshot_sha256)
            self.assertEqual(consent.signature_sha256, hashlib.sha256(bytes(consent.signature)).hexdigest())
            self.assertEqual(len(consent.content_sha256), 64)
            self.assert_private(response)
        self.assertEqual(trip_models()[2].objects.count(), 2)

    def test_signed_review_token_rejects_payload_edit_wrong_user_trip_bogus_or_expired(self):
        trip = self.approved_trip()
        n = self.nomination(trip)
        client, payload, _ = self.review_reply(n)
        cases = [{"decision": "no"}, {"notes": "تعديل بعد المراجعة"},
                 {"guardian_name": "اسم آخر"}, {"guardian_relation": "guardian"},
                 {"signature": signature_png(size=(500, 160))}, {"review_token": "bogus"},
                 {"confirm": ""}, {"confirm": ["yes", "yes"]}]
        for change in cases:
            with self.subTest(change=change):
                response = client.post(self.url("trip_invitation", n), {**payload, **change})
                self.assertIn(response.status_code, (400, 409))
        wrong_user = self.login(self.a[1].user)
        self.assertIn(wrong_user.post(self.url("trip_invitation", self.nomination(trip, self.a[1])), payload).status_code,
                      (400, 403, 404, 409))
        other_trip = self.approved_trip()
        self.assertIn(client.post(self.url("trip_invitation", self.nomination(other_trip)), payload).status_code,
                      (400, 403, 404, 409))
        import time
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 901):
            response = client.post(self.url("trip_invitation", n), payload)
        self.assertIn(response.status_code, (400, 409))
        self.assertFalse(trip_models()[2].objects.exists())

    def test_duplicate_identical_final_is_idempotent_different_decision_cannot_replace(self):
        trip = self.approved_trip()
        n = self.nomination(trip)
        client, yes, _ = self.review_reply(n)
        _, no, _ = self.review_reply(n, client=client, decision="no")
        self.assertIn(client.post(self.url("trip_invitation", n), yes).status_code, (200, 302))
        consent = trip_models()[2].objects.get(nomination=n)
        before = self.domain_state()
        self.assertIn(client.post(self.url("trip_invitation", n), yes).status_code, (200, 302))
        self.assertEqual(self.domain_state(), before)
        self.assertIn(client.post(self.url("trip_invitation", n), no).status_code, (400, 409))
        consent.refresh_from_db()
        self.assertEqual(consent.decision, "yes")
        self.assertEqual(trip_models()[2].objects.filter(nomination=n).count(), 1)

    def test_closed_or_cancelled_trip_blocks_outstanding_token_but_keeps_historical_consent(self):
        for status in ("closed", "cancelled"):
            with self.subTest(status=status):
                trip = self.approved_trip()
                existing, _ = self.final_reply(self.nomination(trip, self.a[1]))
                n = self.nomination(trip)
                client, payload, _ = self.review_reply(n)
                response = self.client.post(self.url("elite_trip_responses", trip),
                    {"action": "close" if status == "closed" else "cancel", "confirm": "yes", "version": trip.version})
                self.assertIn(response.status_code, (200, 302))
                trip.refresh_from_db(); self.assertEqual(trip.status, status)
                response = client.post(self.url("trip_invitation", n), payload)
                self.assertIn(response.status_code, (400, 403, 404, 409))
                self.assertFalse(trip_models()[2].objects.filter(nomination=n).exists())
                owner = self.login(self.a[1].user)
                self.assertEqual(owner.get(self.url("trip_consent_detail", existing)).status_code, 200)

    def test_close_cancel_are_confirmed_post_only(self):
        trip = self.approved_trip()
        before = self.domain_state()
        for action in ("close", "cancel"):
            self.client.get(self.url("elite_trip_responses", trip), {"action": action, "confirm": "yes"})
            response = self.client.post(self.url("elite_trip_responses", trip),
                {"action": action, "confirm": "no", "version": trip.version})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.domain_state(), before)

    def test_signature_rejects_blank_svg_url_corrupt_oversize_dimensions_and_bombs(self):
        trip = self.approved_trip()
        n = self.nomination(trip)
        client = self.login(self.a[0].user)
        invalid = ["", "https://invalid.example/signature.png", "file:///etc/passwd",
                   "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode(),
                   "data:image/png;base64,AAAA", "data:image/png;base64,not base64!",
                   "data:image/png;base64," + "A" * (250 * 1024 + 1),
                   signature_png(blank=True), signature_png(blank=True, alpha=True),
                   signature_png(size=(99, 50)), signature_png(size=(100, 49)),
                   signature_png(size=(1601, 100)), signature_png(size=(400, 601))]
        # Valid PNG container with an advertised huge IHDR; rejects without
        # allocating a decompression bomb. CRC is rebuilt, not merely corruption.
        import struct, zlib
        raw = base64.b64decode(signature_png().split(",")[1])
        ihdr = struct.pack(">IIBBBBB", 100000, 100000, 8, 2, 0, 0, 0)
        bomb = raw[:8] + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)) + raw[33:]
        invalid.append("data:image/png;base64," + base64.b64encode(bomb).decode())
        for value in invalid:
            with self.subTest(signature=value[:60]):
                response = client.post(self.url("trip_invitation", n), self.consent_data(signature=value))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assert_private(response)
        self.assertFalse(trip_models()[2].objects.exists())

    def test_valid_alpha_png_normalized_metadata_stripped_and_no_media_writes(self):
        trip = self.approved_trip()
        with tempfile.TemporaryDirectory(prefix="elite-consent-media-") as directory:
            with override_settings(MEDIA_ROOT=directory):
                consent, _ = self.final_reply(self.nomination(trip), signature=signature_png(alpha=True, metadata=True))
            self.assertEqual(list(Path(directory).rglob("*")), [])
        image = Image.open(io.BytesIO(bytes(consent.signature)))
        image.load()
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertNotIn("sensitive", image.info)
        self.assertFalse(any(field.__class__.__name__ in {"FileField", "ImageField"}
                             for field in consent._meta.fields))
        page = self.login(self.a[0].user).get(self.url("trip_consent_detail", consent))
        self.assertNotContains(page, settings.MEDIA_URL)
        self.assertContains(page, "data:image/png;base64,")

    def test_responses_yes_no_pending_notes_escaped_multiline_and_snapshot_filters(self):
        trip = self.approved_trip()
        yes, _ = self.final_reply(self.nomination(trip), notes='<script>window.bad=1</script>\nسطر ثان')
        no, _ = self.final_reply(self.nomination(trip, self.a[1]), decision="no")
        response = self.client.get(self.url("elite_trip_responses", trip))
        self.assertContains(response, "&lt;script&gt;window.bad=1&lt;/script&gt;")
        self.assertNotContains(response, "<script>window.bad=1</script>")
        self.assertContains(response, "سطر ثان")
        self.assertContains(response, "لا توجد ملاحظات")
        self.assertContains(response, "بانتظار رد ولي الأمر")
        for status, included, excluded in (("yes", self.a[0], self.a[1]), ("no", self.a[1], self.a[0]),
                                          ("pending", self.a[2], self.a[0])):
            filtered = self.client.get(self.url("elite_trip_responses", trip), {"status": status})
            self.assertContains(filtered, included.user.full_name)
            self.assertNotContains(filtered, excluded.user.full_name)
        response = self.client.get(self.url("elite_trip_responses", trip), {"group": self.group_b.pk})
        self.assertContains(response, self.b[0].user.full_name)
        self.assertNotContains(response, self.a[0].user.full_name)
        response = self.client.get(self.url("elite_trip_responses", trip), {"q": self.a[0].user.full_name})
        self.assertContains(response, self.a[0].user.full_name)
        self.assertNotContains(response, self.b[0].user.full_name)
        # Search must not treat an internal ORM path/SQL as code or expose all.
        response = self.client.get(self.url("elite_trip_responses", trip), {"q": "user__national_id"})
        self.assertNotContains(response, self.a[0].user.full_name)

    def test_invalid_and_duplicate_response_filters_are_rejected_without_scope_expansion(self):
        trip = self.approved_trip(scope_group=str(self.group_a.pk))
        cases = [{"status": "invalid"}, {"status": ["yes", "no"]}, {"group": "9999999"},
                 {"group": self.group_b.pk}, {"group": "1 OR 1=1"},
                 {"group": [self.group_a.pk, self.group_b.pk]}, {"q": ["one", "two"]}]
        before = self.domain_state()
        for data in cases:
            with self.subTest(data=data):
                response = self.client.get(self.url("elite_trip_responses", trip), data)
                self.assertIn(response.status_code, (200, 400, 404))
                if response.status_code == 200:
                    self.assertTrue(response.context["error"])
                    self.assertEqual(response.context["rows"], [])
                self.assert_private(response)
        self.assertEqual(self.domain_state(), before)

    def test_consent_detail_and_pdf_idor_permissions_before_renderer(self):
        trip = self.approved_trip()
        consent, _ = self.final_reply(self.nomination(trip))
        for user in (self.a[1].user, self.b[0].user, self.system, self.limited, self.group_supervisor):
            client = self.login(user)
            for route in ("trip_consent_detail", "trip_consent_pdf"):
                with self.subTest(role=user.role, route=route):
                    engine = mock.MagicMock()
                    with mock.patch.dict(sys.modules, {"weasyprint": engine}):
                        response = client.get(self.url(route, consent))
                    self.assertIn(response.status_code, (403, 404))
                    self.assert_private(response)
                    engine.HTML.assert_not_called()
        for user in (self.general, self.a[0].user):
            response = self.login(user).get(self.url("trip_consent_detail", consent))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "لم يُتحقق")
            self.assert_private(response)

    def test_operational_trip_management_is_independent_of_explicit_report_deny(self):
        SupervisorReportPermission.objects.update_or_create(supervisor=self.general,
            defaults={"view_reports": False, "prepare_reports": False})
        response = self.client.get(self.url("elite_trip_list"))
        self.assertEqual(response.status_code, 200)
        trip = self.approved_trip()
        self.assertEqual(self.client.get(self.url("elite_trip_responses", trip)).status_code, 200)

    def test_domain_rewards_attendance_and_competitive_top_ten_are_untouched(self):
        from .competition import ELITE_LIMIT
        self.assertEqual(ELITE_LIMIT, 10)
        models = (Participant, PointsLedgerEntry, EliteTripAttendance, CircleAttendance,
                  MeetingAttendance, WeeklyActivityAttendance)
        before = {m._meta.label: list(m.objects.order_by("pk").values()) for m in models}
        trip = self.approved_trip()
        self.final_reply(self.nomination(trip), decision="no")
        self.client.get(self.url("elite_trip_responses", trip))
        self.assertEqual({m._meta.label: list(m.objects.order_by("pk").values()) for m in models}, before)

    def test_invitation_badge_counts_only_frozen_selected_open_unanswered_and_archives_survive(self):
        from .elite_trips import pending_trip_invitation_count, student_trip_cards
        draft = self.create_trip()
        self.assertEqual(pending_trip_invitation_count(self.a[0].user), 0)
        first = self.approved_trip(scope_group=self.group_a.pk)
        second = self.approved_trip()
        self.assertEqual(pending_trip_invitation_count(self.a[0].user), 2)
        self.assertEqual(pending_trip_invitation_count(self.b[0].user), 1)
        self.assertEqual(pending_trip_invitation_count(self.a[-1].user), 0)
        self.final_reply(self.nomination(first))
        self.assertEqual(pending_trip_invitation_count(self.a[0].user), 1)
        response = self.client.post(self.url("elite_trip_responses", second),
            {"action": "close", "confirm": "yes", "version": second.version})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pending_trip_invitation_count(self.a[0].user), 0)
        self.assertEqual({card["trip_id"] for card in student_trip_cards(self.a[0].user)}, {first.pk, second.pk})
        self.assertFalse(any(card["can_reply"] for card in student_trip_cards(self.a[0].user)))

    def test_normalized_final_accepts_maximum_name_notes_and_never_trusts_posted_student_identity(self):
        trip = self.approved_trip()
        nomination = self.nomination(trip)
        consent, _ = self.final_reply(nomination, guardian_name="و" * 100, notes="ن" * 1500,
            guardian_relation="guardian", student_name="اسم محقون", participant_id=self.b[0].pk,
            nomination_id=self.nomination(trip, self.b[0]).pk, trip_id="999999")
        self.assertEqual(consent.nomination_id, nomination.pk)
        self.assertEqual(consent.guardian_name, "و" * 100)
        self.assertEqual(len(consent.notes), 1500)
        page = self.client.get(self.url("trip_consent_detail", consent))
        self.assertContains(page, nomination.student_name)
        self.assertNotContains(page, "اسم محقون")

    def test_response_kpis_remain_whole_trip_and_follow_list_does_not_select_private_blob(self):
        trip = self.approved_trip()
        self.final_reply(self.nomination(trip))
        self.final_reply(self.nomination(trip, self.a[1]), decision="no")
        sqls = []
        def observe(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith("SELECT") and "participants_guardiantripconsent" in sql:
                sqls.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe):
            page = self.client.get(self.url("elite_trip_responses", trip), {"group": self.group_a.pk, "status": "yes"})
        self.assertEqual(page.context["kpis"], {"selected": 26, "yes": 1, "no": 1, "pending": 24})
        self.assertEqual(page.context["filtered_count"], 1)
        self.assertTrue(sqls)
        for sql in sqls:
            self.assertNotIn('"signature"', sql)
        row = page.context["rows"][0]
        self.assertNotIn("signature", row)
        self.assertNotIn("national_id", row)
        self.assertNotIn("phone", row)

    def test_single_scope_is_historical_after_selected_student_transfers_and_newcomer_has_no_invite(self):
        from .elite_trips import student_trip_cards
        trip = self.approved_trip(scope_group=self.group_a.pk)
        Participant.objects.filter(pk=self.a[0].pk).update(group=self.group_b)
        Participant.objects.filter(pk=self.b[0].pk).update(group=self.group_a)
        self.assertEqual([c["trip_id"] for c in student_trip_cards(self.a[0].user)], [trip.pk])
        self.assertEqual(student_trip_cards(self.b[0].user), [])
        self.assertTrue(self.nomination(trip).frozen_member)
        self.assertFalse(self.nomination(trip, self.b[0]).frozen_member)

    def test_oversized_decoded_png_non_png_animated_and_tiny_ink_are_rejected(self):
        import random
        trip = self.approved_trip()
        nomination = self.nomination(trip)
        client = self.login(self.a[0].user)
        bad = []
        # The encoded payload is under 250 KiB but decoded is over 180 KiB.
        random_bytes = random.Random(733).randbytes(600 * 105 * 3)
        image = Image.frombytes("RGB", (600, 105), random_bytes)
        stream = io.BytesIO(); image.save(stream, format="PNG")
        body = stream.getvalue()
        self.assertGreater(len(body), 180 * 1024)
        bad.append("data:image/png;base64," + base64.b64encode(body).decode())
        for format in ("GIF", "JPEG"):
            stream = io.BytesIO()
            Image.new("RGB", (400, 160), "black").save(stream, format=format)
            bad.append("data:image/png;base64," + base64.b64encode(stream.getvalue()).decode())
        tiny = Image.new("RGB", (400, 160), "white")
        ImageDraw.Draw(tiny).rectangle((30, 30, 31, 31), fill="black")
        stream = io.BytesIO(); tiny.save(stream, format="PNG")
        bad.append("data:image/png;base64," + base64.b64encode(stream.getvalue()).decode())
        stream = io.BytesIO()
        frame = Image.new("RGB", (400, 160), "black")
        frame.save(stream, format="PNG", save_all=True, append_images=[Image.new("RGB", (400, 160), "gray")])
        bad.append("data:image/png;base64," + base64.b64encode(stream.getvalue()).decode())
        for value in bad:
            page = client.post(self.url("trip_invitation", nomination), self.consent_data(signature=value))
            self.assertEqual(page.status_code, 200)
            self.assertTrue(page.context["form"].errors)
        self.assertFalse(trip_models()[2].objects.exists())


# The child renders the *actual* endpoint HTML with its captured allowlisted
# resource bodies, no inherited Django connection, local-file permission or network.
_NATIVE_SCRIPT = r'''
import base64, json, sys
payload = json.load(sys.stdin)
def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise AssertionError("PDF must never access the network")
sys.addaudithook(deny_network)
from weasyprint import HTML
from weasyprint.urls import URLFetcherResponse
from weasyprint.text.fonts import FontConfiguration
loaded = []
def fetch(url):
    if url not in payload["resources"]:
        raise AssertionError("Unallowlisted PDF resource: " + url)
    entry = payload["resources"][url]
    loaded.append(url)
    return URLFetcherResponse(url, body=base64.b64decode(entry["body"]),
                              headers={"Content-Type": entry["mime"]})
fetch._fail_on_errors = True
pdf = HTML(string=payload["html"], base_url=payload["base_url"], url_fetcher=fetch).write_pdf(font_config=FontConfiguration())
assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
print(json.dumps({"pdf":base64.b64encode(pdf).decode(), "loaded":loaded}))
'''


class EliteTripPDFTests(EliteTripFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seed()

    def setUp(self):
        # No installed weasyprint.* submodule may import Pango into this process.
        self.engine = mock.MagicMock()
        self.engine.HTML.return_value.write_pdf.return_value = b"%PDF-1.7 synthetic\n%%EOF"
        urls = ModuleType("weasyprint.urls")
        class Resource:
            def __init__(self, url, body=None, headers=None):
                self.url, self.body, self.headers = url, body, headers or {}
        urls.URLFetcherResponse = Resource
        fonts = ModuleType("weasyprint.text.fonts")
        fonts.FontConfiguration = lambda: None
        patcher = mock.patch.dict(sys.modules, {"weasyprint": self.engine,
            "weasyprint.urls": urls, "weasyprint.text": ModuleType("weasyprint.text"),
            "weasyprint.text.fonts": fonts})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.general)
        self.trip = self.approved_trip()
        self.consent, _ = self.final_reply(self.nomination(self.trip), notes="ملاحظات مصطنعة\nسطر ثان")

    def captured_pdf(self):
        self.engine.reset_mock()
        response = self.client.get(self.url("trip_consent_pdf", self.consent))
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assert_private(response)
        self.engine.HTML.assert_called_once()
        return response, self.engine.HTML.call_args.kwargs

    def test_pdf_has_frozen_identity_decision_disclosure_signature_and_reference_filename(self):
        old_name = self.nomination(self.trip).student_name
        User.objects.filter(pk=self.a[0].user_id).update(full_name="اسم جديد غير تاريخي")
        response, kwargs = self.captured_pdf()
        html = kwargs["string"]
        self.assertIn(old_name, html)
        self.assertNotIn("اسم جديد غير تاريخي", html)
        for text in (str(self.consent.reference), self.trip.title, "لم يُتحقق", "ملاحظات مصطنعة"):
            self.assertIn(text, html)
        self.assertIn("data:image/png;base64,", html)
        disposition = response["Content-Disposition"]
        self.assertIn(str(self.consent.reference), disposition)
        self.assertNotIn(old_name, disposition)
        self.assertNotIn("اسم جديد", disposition)

    def test_actual_pdf_fetcher_rejects_all_arbitrary_network_file_svg_and_unapproved_data(self):
        _, kwargs = self.captured_pdf()
        fetcher = kwargs.get("url_fetcher")
        self.assertTrue(callable(fetcher), "Renderer must get a strict resource allowlist")
        for url in ("https://example.com/static/images/letterhead.png", "http://127.0.0.1:8000/",
                    "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd",
                    "file://localhost/etc/passwd", "ftp://example.com/private", "data:text/html,<script/>",
                    "data:image/svg+xml;base64,PHN2Zy8+", "data:image/png;base64,AAAA",
                    signature_png(size=(500, 150))):
            with self.subTest(url=url[:80]):
                with self.assertRaises(Exception):
                    fetcher(url)

    def test_missing_renderer_and_render_error_return_private_arabic_503_without_notes(self):
        with mock.patch.dict(sys.modules, {"weasyprint": None}):
            response = self.client.get(self.url("trip_consent_pdf", self.consent))
        self.assertEqual(response.status_code, 503)
        self.assert_private(response)
        self.assertNotContains(response, "ملاحظات مصطنعة", status_code=503)
        engine = mock.MagicMock()
        engine.HTML.return_value.write_pdf.side_effect = RuntimeError("sensitive-renderer-internals")
        with mock.patch.dict(sys.modules, {"weasyprint": engine}):
            response = self.client.get(self.url("trip_consent_pdf", self.consent))
        self.assertEqual(response.status_code, 503)
        self.assert_private(response)
        self.assertNotContains(response, "sensitive-renderer-internals", status_code=503)
        self.assertNotContains(response, "ملاحظات مصطنعة", status_code=503)

    def test_pdf_and_detail_gets_do_not_write_domain_state(self):
        before = self.domain_state()
        self.captured_pdf()
        self.client.get(self.url("trip_consent_detail", self.consent))
        self.assertEqual(self.domain_state(), before)

    def test_native_actual_pdf_in_bounded_fresh_interpreter_no_pango_in_runner(self):
        _, kwargs = self.captured_pdf()
        html, fetcher = kwargs["string"], kwargs["url_fetcher"]
        # Allow only resources explicitly emitted by the real consent template.
        urls = set(re.findall(r'(?:src|href)=["\']([^"\']+)', html))
        urls.update(re.findall(r'url\(["\']?([^\)"\']+)', html))
        from urllib.parse import urljoin
        base_url = kwargs.get("base_url", "")
        resources = {}
        for raw in urls:
            url = urljoin(base_url, raw)
            result = fetcher(url)
            if isinstance(result, dict):
                body = result.get("string")
                if body is None:
                    file_obj = result["file_obj"]
                    try:
                        body = file_obj.read()
                    finally:
                        file_obj.close()
                mime = result.get("mime_type", "application/octet-stream")
            else:
                body = getattr(result, "body", None)
                if body is None:
                    body = result.file_obj.read()
                mime = "image/png" if raw.startswith("data:image/png") else "application/octet-stream"
            if isinstance(body, str):
                body = body.encode()
            self.assertTrue(body, f"Empty allowed PDF resource: {url[:100]}")
            resources[url] = {"body": base64.b64encode(body).decode(), "mime": mime}
        payload = {"html": html, "base_url": base_url, "resources": resources}
        before = {name for name in sys.modules if name == "weasyprint" or name.startswith("weasyprint.")}
        try:
            process = subprocess.run([sys.executable, "-I", "-c", _NATIVE_SCRIPT], input=json.dumps(payload),
                                     text=True, capture_output=True, timeout=60, cwd=settings.BASE_DIR)
        except subprocess.TimeoutExpired:
            self.fail("Native consent PDF subprocess exceeded 60 seconds")
        self.assertEqual(process.returncode, 0, process.stderr)
        output = json.loads(process.stdout)
        self.assertTrue(base64.b64decode(output["pdf"]).startswith(b"%PDF-"))
        self.assertTrue(resources)
        self.assertTrue(output["loaded"])
        self.assertEqual({name for name in sys.modules if name == "weasyprint" or name.startswith("weasyprint.")}, before)
