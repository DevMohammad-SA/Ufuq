"""PostgreSQL-only regressions for GENERAL grants and historical actor FKs.

Use config.test_postgres_settings, which forces a local Unix socket. Each test
uses TransactionTestCase, separate thread-owned database connections, bounded
barriers/events, a 4-second lock timeout and a 12-second statement timeout.
No real account or production database is involved.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse

from accounts.models import Role, User
from participants.models import Group, Participant, PointsLedgerEntry, TaskReviewPermission


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL real row locks and FK KEY SHARE")
class SupervisorGeneralGrantConcurrencyTests(TransactionTestCase):
    event_timeout = 8
    result_timeout = 20
    audit_label = "منح صلاحية المشرف العام"

    def setUp(self):
        # Deliberately create the target first: the view receives actor,target
        # in descending PK order but its SQL must lock ascending PK order.
        self.target = self.user("grant-pg-target", Role.GROUP_SUPERVISOR)
        self.actors = [self.user(f"grant-pg-actor-{i}", Role.GENERAL_SUPERVISOR) for i in range(2)]
        self.group = Group.objects.create(name="بيئة منح PostgreSQL مصطنعة")
        self.group.supervisor.add(self.target)
        self.permission = TaskReviewPermission.objects.create(
            supervisor=self.target, enabled=True, granted_by=self.actors[0],
        )
        self.student_user = self.user("grant-pg-student", Role.PARTICIPANT)
        self.student = Participant.objects.create(
            user=self.student_user, group=self.group, academic_stage="grade_7",
            points=91, miles=910, purchase_points=19,
        )
        self.url = reverse("participants:supervisor_grant_general", kwargs={"pk": self.target.pk})
        self.user_content_type_id = ContentType.objects.get_for_model(User).pk

    @staticmethod
    def user(username, role):
        return User.objects.create_user(
            username=username, role=role, full_name=username,
            password="SyntheticPostgresGrant123!",
        )

    def worker(self, operation):
        """Never share Django's parent/thread-local connection with workers."""
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '4s'")
                cursor.execute("SET statement_timeout = '12s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            return pid, operation()
        finally:
            connection.close()

    def wait_for(self, event, description):
        if not event.wait(timeout=self.event_timeout):
            raise AssertionError(f"Timed out waiting for {description}")

    def audits(self):
        return LogEntry.objects.filter(
            content_type_id=self.user_content_type_id, object_id=str(self.target.pk),
            change_message__contains=self.audit_label,
        )

    def assert_granted_once(self):
        self.target.refresh_from_db()
        self.permission.refresh_from_db()
        self.assertEqual(self.target.role, Role.GENERAL_SUPERVISOR)
        self.assertTrue(self.target.is_active)
        self.assertFalse(self.target.is_staff)
        self.assertFalse(self.target.is_superuser)
        self.assertFalse(self.target.group_set.exists())
        self.assertFalse(self.permission.enabled)
        self.assertEqual(self.audits().count(), 1)

    def test_two_requests_for_same_target_both_redirect_but_only_one_grant_and_audit(self):
        from participants import management_views
        original_lock = management_views.locked_users
        both_loaded = Barrier(2)
        captured_lock_sql = []
        before_actors = list(User.objects.filter(pk__in=[u.pk for u in self.actors]).order_by("pk").values())
        original_password = self.target.password

        def synchronize_before_lock(*args, **kwargs):
            # Both requests have authorized and read their GROUP target before
            # either obtains User locks. This is not merely concurrent startup.
            self.assertIs(kwargs.get("no_key"), True)
            both_loaded.wait(timeout=self.event_timeout)
            return original_lock(*args, **kwargs)

        def record_lock_sql(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                captured_lock_sql.append(sql)
            return execute(sql, params, many, context)

        # Login writes last_login. Do it before snapshots and worker locks so
        # that it cannot mask the specific grant race we are testing.
        clients = [Client(), Client()]
        for client, actor in zip(clients, self.actors):
            client.force_login(actor)
        before_actors = list(User.objects.filter(pk__in=[u.pk for u in self.actors]).order_by("pk").values())

        def request(client):
            with connection.execute_wrapper(record_lock_sql):
                return client.post(self.url, {"confirm": "yes"}).status_code

        with patch.object(management_views, "locked_users", side_effect=synchronize_before_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.worker, lambda client=client: request(client)) for client in clients]
                results = [future.result(timeout=self.result_timeout) for future in futures]
        self.assertEqual([status for _, status in results], [302, 302])
        self.assertEqual(len({pid for pid, _ in results}), 2, "Workers must use separate PostgreSQL backends")
        self.assert_granted_once()
        self.assertEqual(self.target.password, original_password)
        self.assertEqual(list(User.objects.filter(pk__in=[u.pk for u in self.actors]).order_by("pk").values()), before_actors)
        self.assertEqual(len(captured_lock_sql), 2)
        for sql in captured_lock_sql:
            with self.subTest(sql=sql):
                self.assertIn("FOR NO KEY UPDATE", sql)
                self.assertRegex(sql, r'ORDER BY "accounts_user"\."id" ASC FOR NO KEY UPDATE')
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (91, 910, 19))

    def test_target_holding_review_permission_can_commit_audit_and_points_fks_while_grant_waits(self):
        from participants.management_views import record_management
        from participants.views import apply_points_delta
        permission_held = Event()
        grant_waiting_on_permission = Event()
        captured_user_locks = []
        grant_client = Client()
        grant_client.force_login(self.actors[0])

        def mark_grant_lock(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                captured_user_locks.append(sql)
            if '"participants_taskreviewpermission"' in sql and "FOR UPDATE" in sql:
                # The grant already owns both User locks and has reached the
                # opposite side of the cycle: it now waits for our permission.
                grant_waiting_on_permission.set()
            return execute(sql, params, many, context)

        def historical_writer():
            with transaction.atomic():
                TaskReviewPermission.objects.select_for_update().get(pk=self.permission.pk)
                participant = Participant.objects.select_for_update().get(pk=self.student.pk)
                permission_held.set()
                self.wait_for(grant_waiting_on_permission, "grant reaching held review-permission row")
                # Both FKs refer to the target, whose User row is held by grant.
                # Force deferred constraints *before* releasing permission so
                # a plain FOR UPDATE regression cannot pass by lucky ordering.
                record_management(self.target, participant, "تدقيق متزامن مصطنع")
                apply_points_delta(
                    participant, 1, source=PointsLedgerEntry.Source.EXTRA,
                    description="نقاط متزامنة مصطنعة", granted_by=self.target,
                )
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return "committed"

        def grant():
            self.wait_for(permission_held, "writer holding review permission")
            with connection.execute_wrapper(mark_grant_lock):
                return grant_client.post(self.url, {"confirm": "yes"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(self.worker, historical_writer)
            granting = pool.submit(self.worker, grant)
            writer_pid, writer_result = writer.result(timeout=self.result_timeout)
            grant_pid, grant_status = granting.result(timeout=self.result_timeout)
        self.assertNotEqual(writer_pid, grant_pid)
        self.assertEqual(writer_result, "committed")
        self.assertEqual(grant_status, 302)
        self.assertTrue(grant_waiting_on_permission.is_set())
        self.assertTrue(captured_user_locks)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in captured_user_locks))
        self.assert_granted_once()
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (92, 920, 20))
        ledger = PointsLedgerEntry.objects.get()
        self.assertEqual(ledger.granted_by_id, self.target.pk)
        self.assertEqual(ledger.points_delta, 1)
        self.assertEqual(LogEntry.objects.filter(user_id=self.target.pk, change_message__contains="تدقيق متزامن مصطنع").count(), 1)
        self.assertEqual(LogEntry.objects.count(), 2)

    def committed_change_during_grant(self, user_id, changes):
        """A real committed change after request's stale read, before its lock."""
        changed_row_held = Event()
        grant_attempting_user_lock = Event()
        release_change = Event()
        grant_client = Client()
        grant_client.force_login(self.actors[0])

        def modifier():
            with transaction.atomic():
                locked = User.objects.select_for_update(no_key=True).get(pk=user_id)
                for field, value in changes.items():
                    setattr(locked, field, value)
                locked.save(update_fields=list(changes))
                changed_row_held.set()
                self.wait_for(release_change, "parent releasing committed eligibility change")
            return "committed"

        def mark_user_lock(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                grant_attempting_user_lock.set()
            return execute(sql, params, many, context)

        def granting():
            self.wait_for(changed_row_held, "modifier holding changed User row")
            with connection.execute_wrapper(mark_user_lock):
                return grant_client.post(self.url, {"confirm": "yes"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            changing = pool.submit(self.worker, modifier)
            grant = pool.submit(self.worker, granting)
            try:
                self.wait_for(grant_attempting_user_lock, "authorized stale request attempting User lock")
                with self.assertRaises(TimeoutError):
                    grant.result(timeout=0.2)
            finally:
                release_change.set()
            modifier_pid, result = changing.result(timeout=self.result_timeout)
            grant_pid, status = grant.result(timeout=self.result_timeout)
        self.assertNotEqual(modifier_pid, grant_pid)
        self.assertEqual(result, "committed")
        actual = User.objects.get(pk=user_id)
        for field, value in changes.items():
            self.assertEqual(getattr(actual, field), value)
        self.target.refresh_from_db()
        self.permission.refresh_from_db()
        self.assertEqual(self.target.role, Role.GROUP_SUPERVISOR)
        self.assertTrue(self.group.supervisor.filter(pk=self.target.pk).exists())
        self.assertTrue(self.permission.enabled)
        self.assertFalse(self.audits().exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        return status

    def test_committed_actor_demotion_while_request_waits_is_rechecked_and_forbidden(self):
        status = self.committed_change_during_grant(self.actors[0].pk, {"role": Role.GROUP_SUPERVISOR})
        self.assertEqual(status, 403)

    def test_committed_target_deactivation_while_request_waits_cannot_be_promoted(self):
        status = self.committed_change_during_grant(self.target.pk, {"is_active": False})
        self.assertIn(status, (403, 404))
