"""Synthetic notification regressions; SQLite memory/local PostgreSQL only.

Reuse the existing plain fixture mixin, never import a discoverable TestCase.
The approval/consent flow uses the real source fixtures and approved trip form.
"""
import json
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.query import QuerySet
from django.db.transaction import TransactionManagementError
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import Role, User
from . import elite_trips as services
from . import trip_notifications as notifications
from .models import EliteTrip, EliteTripAudit, EliteTripNomination, EliteTripNotification, Participant
from .test_elite_trip_consents import EliteTripFixtureMixin


class TripNotificationTests(EliteTripFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seed()

    def setUp(self):
        self.client.force_login(self.general)

    def event(self, trip, participant=None):
        return EliteTripNotification.objects.get(nomination=self.nomination(trip, participant), trip_version=trip.version)

    def status(self, trip, status):
        trip = services.change_trip_status(self.general, trip.pk, status, trip.version, "yes")
        return trip

    def assert_schema(self, response):
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assert_private(response)
        self.assertTrue(response["Content-Type"].startswith("application/json"))
        data = response.json()
        self.assertEqual(set(data), {"ok", "notifications", "unread_count", "attention_count"})
        self.assertIs(data["ok"], True)
        for row in data["notifications"]:
            self.assertEqual(set(row), {"id", "kind", "message", "created_at", "read_at", "url", "read_url"})
        return data

    def test_approval_emits_once_for_selected_and_non_selected_frozen_roster(self):
        trip = self.approved_trip()
        rows = EliteTripNotification.objects.filter(nomination__trip=trip)
        self.assertEqual(rows.count(), len(self.a + self.b))
        self.assertEqual(rows.filter(kind="nominated").count(), 26)
        self.assertEqual(rows.filter(kind="not_nominated").count(), 6)
        self.assertEqual(set(rows.values_list("trip_version", flat=True)), {trip.version})
        for row in rows:
            self.assertIn(trip.snapshot["trip"]["title"], row.message)
            self.assertLessEqual(len(row.message), 600)
            self.assertIsNone(row.read_at)
        before = self.domain_state()
        notifications.emit_trip_notifications(trip)
        notifications.emit_trip_notifications(trip)
        self.assertEqual(self.domain_state(), before)
        self.assertEqual(self.event(trip).kind, "nominated")
        self.assertEqual(self.event(trip, self.a[-1]).kind, "not_nominated")

    def test_draft_save_review_and_cancel_do_not_emit(self):
        trip = self.create_trip()
        self.save_trip(trip)
        for route in ("elite_trip_edit", "elite_trip_review", "elite_trip_responses"):
            self.client.get(self.url(route, trip))
        notifications.emit_trip_notifications(trip)
        self.status(trip, "cancelled")
        self.assertFalse(EliteTripNotification.objects.exists())

    def test_single_scope_has_no_events_for_outside_inactive_ungrouped_or_newcomer(self):
        trip = self.approved_trip(scope_group=self.group_a.pk)
        self.assertEqual(EliteTripNotification.objects.count(), len(self.a))
        for student in self.b + [self.inactive, self.wrong_role, self.ungrouped]:
            self.assertFalse(notifications.own_trip_notifications(student.user).exists())
        newcomer = self.make_student("new-notify", self.group_a, 500)
        self.status(trip, "closed")
        self.assertFalse(notifications.own_trip_notifications(newcomer.user).exists())
        self.assertFalse(EliteTripNotification.objects.filter(nomination__frozen_member=False).exists())

    def test_close_cancel_emit_new_versions_once_without_rewriting_approval(self):
        trip = self.approved_trip(scope_group=self.group_a.pk)
        original = self.event(trip)
        snapshot, fingerprint = trip.snapshot, trip.snapshot_sha256
        trip = self.status(trip, "closed")
        self.assertEqual(self.event(trip).kind, "closed")
        before = self.domain_state()
        self.status(trip, "closed")
        self.assertEqual(self.domain_state(), before)
        trip = self.status(trip, "cancelled")
        self.assertEqual(self.event(trip).kind, "cancelled")
        self.assertEqual(EliteTripNotification.objects.count(), len(self.a) * 3)
        self.assertEqual(trip.snapshot, snapshot)
        self.assertEqual(trip.snapshot_sha256, fingerprint)
        original.refresh_from_db()
        self.assertEqual(original.kind, "nominated")
        self.assertEqual(self.event(trip, self.a[-1]).kind, "cancelled")

    def test_status_message_uses_frozen_title_not_live_trip_title_or_private_fields(self):
        trip = self.approved_trip(title="عنوان تاريخي <script>مصطنع</script>",
            **{f"reason_{self.a[0].pk}": "سبب خاص لا يخرج"})
        frozen_title = trip.snapshot["trip"]["title"]
        EliteTrip.objects.filter(pk=trip.pk).update(title="عنوان حي لا يخرج")
        trip = self.status(trip, "closed")
        message = self.event(trip).message
        self.assertIn(frozen_title, message)
        self.assertNotIn("عنوان حي لا يخرج", message)
        self.assertNotIn("سبب خاص لا يخرج", message)
        self.assertNotIn(self.a[1].user.full_name, message)

    def test_emit_failure_rolls_back_approval_roster_status_and_audit(self):
        trip = self.create_trip()
        self.save_trip(trip)
        before = self.domain_state()
        real_create = QuerySet.get_or_create
        attempts = []
        def fail_second(queryset, *args, **kwargs):
            attempts.append(kwargs)
            if len(attempts) == 2:
                raise IntegrityError("synthetic-notification-save-failure")
            return real_create(queryset, *args, **kwargs)
        with mock.patch("django.db.models.query.QuerySet.get_or_create", autospec=True, side_effect=fail_second):
            with self.assertRaises(IntegrityError):
                services.approve_trip(self.general, trip.pk, trip.version, "yes")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.domain_state(), before)

    def test_emit_failure_rolls_back_status_change_and_audit(self):
        trip = self.approved_trip()
        before = self.domain_state()
        with mock.patch("participants.trip_notifications.emit_trip_notifications", side_effect=RuntimeError("synthetic-failure")):
            with self.assertRaises(RuntimeError):
                self.status(trip, "closed")
        self.assertEqual(self.domain_state(), before)

    def test_repeated_approval_and_stale_status_cannot_duplicate_events(self):
        trip = self.approved_trip()
        before = self.domain_state()
        with self.assertRaises(ValidationError):
            services.approve_trip(self.general, trip.pk, trip.version, "yes")
        with self.assertRaises(ValidationError):
            services.change_trip_status(self.general, trip.pk, "closed", trip.version - 1, "yes")
        self.assertEqual(self.domain_state(), before)

    def test_database_unique_valid_kind_and_protected_nomination(self):
        trip = self.approved_trip()
        event = self.event(trip)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EliteTripNotification.objects.create(nomination=event.nomination, trip_version=event.trip_version,
                                                  kind="closed", message="محاولة تكرار")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EliteTripNotification.objects.create(nomination=event.nomination, trip_version=999,
                                                  kind="invalid", message="نوع غير صالح")
        with self.assertRaises(ProtectedError):
            event.nomination.delete()
        event.message = "ن" * 601
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_own_queries_exclude_drafts_unfrozen_and_unapproved_rows(self):
        draft = self.create_trip()
        draft_n = self.nomination(draft)
        EliteTripNotification.objects.create(nomination=draft_n, trip_version=1, kind="nominated", message="غير منشور")
        approved = self.approved_trip(scope_group=self.group_a.pk)
        outsider = self.nomination(approved, self.b[0])
        EliteTripNotification.objects.create(nomination=outsider, trip_version=approved.version, kind="nominated", message="خارج النطاق")
        self.assertEqual(notifications.own_trip_notifications(self.a[0].user).count(), 1)
        self.assertEqual(notifications.own_trip_notifications(self.b[0].user).count(), 0)
        EliteTrip.objects.filter(pk=approved.pk).update(approved_at=None)
        self.assertEqual(notifications.own_trip_notifications(self.a[0].user).count(), 0)

    def test_frozen_membership_survives_transfer_without_new_live_membership(self):
        trip = self.approved_trip(scope_group=self.group_a.pk)
        Participant.objects.filter(pk=self.a[0].pk).update(group=self.group_b, points=0)
        Participant.objects.filter(pk=self.b[0].pk).update(group=self.group_a, points=9999)
        trip = self.status(trip, "closed")
        self.assertEqual(notifications.own_trip_notifications(self.a[0].user).count(), 2)
        self.assertFalse(notifications.own_trip_notifications(self.b[0].user).exists())

    def test_context_is_plain_owner_only_and_does_not_select_snapshot_or_signature(self):
        trip = self.approved_trip()
        consent, _ = self.final_reply(self.nomination(trip), notes="ملاحظة ولي خاصة")
        selects = []
        def observe(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith("SELECT"):
                selects.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe):
            context = notifications.notification_context(self.a[0].user)
        self.assertEqual(set(context), {"trip_notifications", "trip_notifications_unread", "trip_notifications_attention",
                                       "trip_notifications_url", "trip_notifications_enabled"})
        self.assertIs(context["trip_notifications_enabled"], True)
        serialized = json.dumps(context, ensure_ascii=False)
        for field in ('"snapshot"', '"signature"', '"notes"', "ملاحظة ولي خاصة", self.a[1].user.full_name, consent.guardian_name):
            self.assertNotIn(field, serialized)
        for sql in selects:
            self.assertNotIn('"snapshot"', sql)
            self.assertNotIn('"signature"', sql)
            self.assertNotIn('"notes"', sql)
        row = context["trip_notifications"][0]
        self.assertIs(type(row), dict)
        self.assertEqual(row["url"], self.url("student_trips") + f"#trip-{trip.pk}")
        self.assertEqual(row["read_url"], self.url("trip_notification_read", self.event(trip)))
        self.assertEqual(context["trip_notifications_url"], self.url("trip_notifications"))

    def test_limit_is_latest_twenty_but_unread_and_attention_include_older(self):
        # Historical synthetic rows use the real approved model, without invoking
        # the emitter: this is test data, never a GET/backfill application path.
        for i in range(25):
            trip = EliteTrip.objects.create(title=f"رحلة اختبار {i}", status="closed", approved_at=timezone.now())
            nomination = EliteTripNomination.objects.create(trip=trip, participant=self.a[0], frozen_member=True)
            EliteTripNotification.objects.create(nomination=nomination, trip_version=1, kind="closed", message=f"إشعار {i}")
        context = notifications.notification_context(self.a[0].user)
        self.assertEqual(len(context["trip_notifications"]), 20)
        self.assertEqual(context["trip_notifications"][0]["message"], "إشعار 24")
        self.assertEqual(context["trip_notifications_unread"], 25)
        self.assertEqual(context["trip_notifications_attention"], 25)
        self.assertEqual(len(notifications.notification_context(self.a[0].user, limit=3)["trip_notifications"]), 3)
        self.assertEqual(notifications.notification_context(self.a[0].user, limit=0)["trip_notifications"], [])

    def test_unread_and_pending_attention_count_trip_once(self):
        trip = self.approved_trip()
        owner = self.a[0].user
        self.assertEqual(notifications.trip_attention_count(owner), 1)
        self.assertEqual(services.pending_trip_invitation_count(owner), 1)
        self.assertTrue(notifications.mark_notification_read(owner, self.event(trip).pk))
        self.assertEqual(notifications.trip_attention_count(owner), 1)
        self.final_reply(self.nomination(trip))
        self.assertEqual(notifications.trip_attention_count(owner), 0)
        self.assertEqual(services.pending_trip_invitation_count(owner), 0)
        trip = self.status(trip, "closed")
        self.assertEqual(notifications.trip_attention_count(owner), 1)
        trip = self.status(trip, "cancelled")
        self.assertEqual(notifications.own_trip_notifications(owner).filter(read_at=None).count(), 2)
        self.assertEqual(notifications.trip_attention_count(owner), 1)

    def test_twenty_one_unread_progress_and_older_unread_precedes_newer_read(self):
        trip = self.approved_trip()
        oldest = self.event(trip)
        for i in range(20):
            EliteTripNotification.objects.create(nomination_id=oldest.nomination_id,
                trip_version=trip.version + i + 1, kind="closed", message=f"حدث مصطنع {i}")
        owner = self.login(self.a[0].user)
        initial = self.assert_schema(owner.get(self.url("trip_notifications")))
        self.assertEqual(initial["unread_count"], 21)
        self.assertEqual(len(initial["notifications"]), 20)
        self.assertNotIn(oldest.pk, [row["id"] for row in initial["notifications"]])
        newest_pk = initial["notifications"][0]["id"]
        result = self.assert_schema(owner.post(initial["notifications"][0]["read_url"],
            {"action": "read"}, HTTP_ACCEPT="application/json"))
        self.assertEqual(result["unread_count"], 20)
        self.assertIn(oldest.pk, [row["id"] for row in result["notifications"]])
        self.assertNotIn(newest_pk, [row["id"] for row in result["notifications"]])
        EliteTripNotification.objects.filter(nomination_id=oldest.nomination_id).exclude(pk=oldest.pk).update(read_at=timezone.now())
        context = notifications.notification_context(self.a[0].user)
        self.assertEqual(context["trip_notifications"][0]["id"], oldest.pk)
        self.assertEqual(context["trip_notifications_unread"], 1)

    def test_non_selected_attention_disappears_only_after_all_events_read(self):
        trip = self.approved_trip()
        owner = self.a[-1].user
        self.assertEqual(services.pending_trip_invitation_count(owner), 0)
        self.assertEqual(notifications.trip_attention_count(owner), 1)
        trip = self.status(trip, "closed")
        notifications.mark_notification_read(owner, self.event(trip, self.a[-1]).pk)
        self.assertEqual(notifications.trip_attention_count(owner), 1)
        for pk in notifications.own_trip_notifications(owner).values_list("pk", flat=True):
            notifications.mark_notification_read(owner, pk)
        self.assertEqual(notifications.trip_attention_count(owner), 0)

    def test_mark_read_changes_only_event_first_timestamp_not_legacy_read_or_audit(self):
        trip = self.approved_trip()
        event = self.event(trip)
        nomination = self.nomination(trip)
        audit_before = list(EliteTripAudit.objects.values())
        self.assertTrue(notifications.mark_notification_read(self.a[0].user, event.pk))
        event.refresh_from_db()
        stamp = event.read_at
        self.assertIsNotNone(stamp)
        self.assertFalse(notifications.mark_notification_read(self.a[0].user, event.pk))
        event.refresh_from_db(); nomination.refresh_from_db()
        self.assertEqual(event.read_at, stamp)
        self.assertIsNone(nomination.read_at)
        self.assertEqual(list(EliteTripAudit.objects.values()), audit_before)

    def test_mark_read_rechecks_account_after_lock_wait(self):
        trip = self.approved_trip()
        event = self.event(trip)
        owner = self.a[0].user
        real_lock = services._lock_users
        def changed_before_lock(*args, **kwargs):
            User.objects.filter(pk=owner.pk).update(is_staff=True)
            return real_lock(*args, **kwargs)
        with mock.patch.object(services, "_lock_users", side_effect=changed_before_lock):
            with self.assertRaises(PermissionDenied):
                notifications.mark_notification_read(owner, event.pk)
        event.refresh_from_db()
        self.assertIsNone(event.read_at)

    def test_fresh_queries_and_read_deny_inactive_staff_superuser_and_wrong_role(self):
        trip = self.approved_trip()
        owner = self.a[0].user
        event = self.event(trip)
        for changes in ({"is_active": False}, {"is_staff": True}, {"is_superuser": True}, {"role": Role.GENERAL_SUPERVISOR}):
            with self.subTest(changes=changes):
                User.objects.filter(pk=owner.pk).update(**changes)
                self.assertFalse(notifications.own_trip_notifications(owner).exists())
                self.assertEqual(notifications.trip_attention_count(owner), 0)
                with self.assertRaises(PermissionDenied):
                    notifications.mark_notification_read(owner, event.pk)
                User.objects.filter(pk=owner.pk).update(is_active=True, is_staff=False, is_superuser=False, role=Role.PARTICIPANT)
        for user in (AnonymousUser(), self.general, self.system, self.group_supervisor, self.limited):
            self.assertFalse(notifications.own_trip_notifications(user).exists())
            self.assertEqual(notifications.trip_attention_count(user), 0)

    def test_poll_schema_and_student_context_are_private_and_gets_never_write(self):
        trip = self.approved_trip()
        owner = self.login(self.a[0].user)
        before = self.domain_state()
        for _ in range(2):
            data = self.assert_schema(owner.get(self.url("trip_notifications"), {"action": "read"}))
            self.assertEqual(data["unread_count"], 1)
            self.assertEqual(data["attention_count"], 1)
            page = owner.get(self.url("student_trips"))
            self.assert_private(page)
            self.assertEqual(page.context["trip_notifications"], data["notifications"])
        self.assertEqual(self.domain_state(), before)

    def test_historical_no_event_gets_do_not_backfill_or_mark_read(self):
        trip = self.approved_trip()
        EliteTripNotification.objects.all().delete()
        owner = self.login(self.a[0].user)
        before = self.domain_state()
        data = self.assert_schema(owner.get(self.url("trip_notifications")))
        self.assertEqual(data["notifications"], [])
        self.assertEqual(data["unread_count"], 0)
        self.assertEqual(data["attention_count"], 1)
        owner.get(self.url("student_trips"))
        self.assertEqual(self.domain_state(), before)

    def test_read_post_json_ack_is_idempotent_and_plain_form_redirect_is_fixed(self):
        trip = self.approved_trip()
        event = self.event(trip)
        owner = self.login(self.a[0].user)
        path = self.url("trip_notification_read", event)
        data = self.assert_schema(owner.post(path, {"action": "read", "next": "https://invalid.example"}, HTTP_ACCEPT="application/json"))
        self.assertEqual(data["unread_count"], 0)
        self.assertEqual(data["attention_count"], 1)
        self.assertIsNotNone(data["notifications"][0]["read_at"])
        before = self.domain_state()
        again = self.assert_schema(owner.post(path, {"action": "read"}, HTTP_ACCEPT="application/json"))
        self.assertEqual(again, data)
        result = owner.post(path, {"action": "read", "next": "https://invalid.example"})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], self.url("student_trips"))
        self.assert_private(result)
        self.assertEqual(self.domain_state(), before)

    def test_read_get_is_405_and_owner_authorization_precedes_action_validation(self):
        trip = self.approved_trip()
        event = self.event(trip)
        owner = self.login(self.a[0].user)
        foreign = self.login(self.a[1].user)
        path = self.url("trip_notification_read", event)
        before = self.domain_state()
        response = owner.get(path)
        self.assertEqual(response.status_code, 405)
        self.assert_private(response)
        for data in ({"action": "read"}, {"action": "unknown"}, {}):
            response = foreign.post(path, data, HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 404)
            self.assert_private(response)
        for data in ({"action": "unknown"}, {"action": ["read", "read"]}, {}):
            self.assertEqual(owner.post(path, data).status_code, 400)
        self.assertEqual(owner.post(self.url("trip_notification_read", type("Pk", (), {"pk": 9999999})()), {"action": "read"}).status_code, 404)
        self.assertEqual(owner.post(self.url("trip_notifications"), {"action": "read"}).status_code, 400)
        self.assertEqual(self.domain_state(), before)

    def test_read_csrf_denial_and_auth_redirects_roles_password_setup_are_private(self):
        trip = self.approved_trip()
        event = self.event(trip)
        path = self.url("trip_notification_read", event)
        owner = self.login(self.a[0].user, csrf=True)
        denied_clients = [self.login(user) for user in (self.general, self.system, self.group_supervisor, self.limited)]
        before = self.domain_state()
        denied = owner.post(path, {"action": "read"}, HTTP_ACCEPT="application/json")
        self.assertEqual(denied.status_code, 403)
        self.assert_private(denied)
        for client in denied_clients:
            for response in (client.get(self.url("trip_notifications")), client.post(path, {"action": "read"})):
                self.assertEqual(response.status_code, 403)
                self.assert_private(response)
        for response in (Client().get(self.url("trip_notifications")), Client().post(path, {"action": "read"})):
            self.assertEqual(response.status_code, 302)
            self.assert_private(response)
        self.assertEqual(self.domain_state(), before)
        User.objects.filter(pk=self.a[0].user_id).update(must_set_password=True)
        client = self.login(self.a[0].user)
        response = client.get(self.url("trip_notifications"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/set-password/", response["Location"])
        self.assert_private(response)

    def test_native_csrf_protected_read_accepts_real_cookie_and_returns_full_json_context(self):
        trip = self.approved_trip()
        event = self.event(trip)
        owner = self.login(self.a[0].user, csrf=True)
        page = owner.get(self.url("trip_invitation", self.nomination(trip)))
        self.assertEqual(page.status_code, 200)
        response = owner.post(self.url("trip_notification_read", event),
            {"action": "read", "csrfmiddlewaretoken": owner.cookies["csrftoken"].value},
            HTTP_ACCEPT="application/json", HTTP_ORIGIN="http://testserver")
        data = self.assert_schema(response)
        self.assertEqual(data["unread_count"], 0)
        self.assertEqual(data["attention_count"], 1)
        self.assertIsNotNone(data["notifications"][0]["read_at"])

    def test_save_failure_post_is_not_json_success_and_keeps_unread(self):
        trip = self.approved_trip()
        event = self.event(trip)
        owner = self.login(self.a[0].user)
        with mock.patch("participants.trip_notifications.mark_notification_read", side_effect=RuntimeError("synthetic-private-error")):
            response = owner.post(self.url("trip_notification_read", event), {"action": "read"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 500)
        self.assert_private(response)
        self.assertNotIn(b"synthetic-private-error", response.content)
        event.refresh_from_db()
        self.assertIsNone(event.read_at)


class TripNotificationAtomicContractTests(TransactionTestCase):
    def test_emit_requires_callers_transaction_even_for_unsaved_trip(self):
        self.assertFalse(connection.in_atomic_block)
        with self.assertRaises(TransactionManagementError):
            notifications.emit_trip_notifications(EliteTrip(title="رحلة مصطنعة"))
        self.assertFalse(EliteTripNotification.objects.exists())
