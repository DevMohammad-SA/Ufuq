"""Real local PostgreSQL row-lock regressions for delegated student management.

Run with config.test_postgres_settings and a unique UFUQ_TEST_DB_NAME. Every
worker owns a separate connection; bounded events/timeouts replace sleeps.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.db import close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse

from accounts.models import Role, User
from participants import management_views, participant_lifecycle
from participants.models import Group, Participant, PointsLedgerEntry, SupervisorCapability


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL row locks")
class DelegatedStudentManagementConcurrencyTests(TransactionTestCase):
    event_timeout = 8
    result_timeout = 20

    def setUp(self):
        self.actor = self.user("student-pg-actor", Role.GROUP_SUPERVISOR)
        self.other_actor = self.user("student-pg-other", Role.GROUP_SUPERVISOR)
        self.group = Group.objects.create(name="بيئة تزامن الطلاب")
        self.outside_group = Group.objects.create(name="بيئة خارج تزامن الطلاب")
        self.group.supervisor.add(self.actor, self.other_actor)
        for actor in (self.actor, self.other_actor):
            SupervisorCapability.objects.create(supervisor=actor, manage_students=True)
        self.student_user = self.user("student-pg-target", Role.PARTICIPANT, national_id="9500000001")
        self.student = Participant.objects.create(
            user=self.student_user, group=self.group, academic_stage="grade_7",
            points=93, miles=930, purchase_points=29,
        )
        self.edit_url = reverse("participants:participant_edit", kwargs={"pk": self.student.pk})
        self.delete_url = reverse("participants:participant_delete", kwargs={"pk": self.student.pk})
        self.clients = [Client(), Client()]
        for client, actor in zip(self.clients, (self.actor, self.other_actor)):
            client.force_login(actor)

    @staticmethod
    def user(username, role, **extra):
        return User.objects.create_user(username=username, role=role, full_name=username, password="SyntheticPG123!", **extra)

    def payload(self):
        return {"full_name": "تعديل تزامن", "national_id": "9500000001",
                "group": str(self.group.pk), "academic_stage": "grade_8", "phone": "0500000000"}

    def worker(self, operation):
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
        if not event.wait(self.event_timeout):
            raise AssertionError("Timed out waiting for " + description)

    def assert_balances(self):
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (93, 930, 29))

    def committed_change_before_user_lock(self, action, mutation, *, target=False):
        held, attempted, release = Event(), Event(), Event()
        observed_sql = []
        before_user = User.objects.values().get(pk=self.student_user.pk)
        before_participant = Participant.objects.values().get(pk=self.student.pk)
        modified_user_id = self.student_user.pk if target else self.actor.pk

        def writer():
            with transaction.atomic():
                locked = User.objects.select_for_update(no_key=True).get(pk=modified_user_id)
                mutation(locked)
                held.set()
                self.wait_for(release, "release of committed permission/scope change")
            return "committed"

        def monitor(execute, sql, params, many, context):
            if 'FROM "accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                observed_sql.append(sql)
                attempted.set()
            return execute(sql, params, many, context)

        def request():
            self.wait_for(held, "writer holding User lock")
            with connection.execute_wrapper(monitor):
                if action == "edit":
                    return self.clients[0].post(self.edit_url, self.payload()).status_code
                return self.clients[0].post(self.delete_url, {"confirm": "yes"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            changing = pool.submit(self.worker, writer)
            requesting = pool.submit(self.worker, request)
            try:
                self.wait_for(attempted, "authorized stale request attempting User lock")
                with self.assertRaises(TimeoutError):
                    requesting.result(timeout=0.2)
            finally:
                release.set()
            writer_pid, result = changing.result(timeout=self.result_timeout)
            request_pid, status = requesting.result(timeout=self.result_timeout)
        self.assertNotEqual(writer_pid, request_pid)
        self.assertEqual(result, "committed")
        self.assertIn(status, (403, 404))
        self.assertTrue(observed_sql)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in observed_sql))
        self.assertTrue(all('ORDER BY "accounts_user"."id" ASC' in sql for sql in observed_sql))
        self.assertFalse(LogEntry.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assert_balances()
        actual_user = User.objects.values().get(pk=self.student_user.pk)
        self.assertEqual(actual_user["full_name"], before_user["full_name"])
        self.assertEqual(actual_user["password"], before_user["password"])
        self.assertTrue(actual_user["is_active"])
        self.assertEqual(self.student.academic_stage, before_participant["academic_stage"])
        return status

    def reset_actor(self):
        User.objects.filter(pk=self.actor.pk).update(role=Role.GROUP_SUPERVISOR, is_active=True)
        SupervisorCapability.objects.filter(supervisor=self.actor).update(manage_students=True)
        self.group.supervisor.add(self.actor)

    def test_committed_grant_revocation_is_rechecked_for_edit_and_disable(self):
        def revoke(locked):
            SupervisorCapability.objects.filter(supervisor=locked).update(manage_students=False)
        for action in ("edit", "disable"):
            self.reset_actor()
            with self.subTest(action=action):
                self.assertEqual(self.committed_change_before_user_lock(action, revoke), 403)

    def test_committed_actor_disable_and_demotion_are_rechecked(self):
        for action, changes in (("edit", {"is_active": False}), ("disable", {"role": Role.PARTICIPANT})):
            self.reset_actor()
            def mutate(locked):
                for field, value in changes.items():
                    setattr(locked, field, value)
                locked.save(update_fields=list(changes))
            with self.subTest(action=action):
                self.assertEqual(self.committed_change_before_user_lock(action, mutate), 403)

    def test_committed_assignment_removal_is_rechecked_after_actor_lock(self):
        def revoke_assignment(locked):
            self.group.supervisor.remove(locked)
        for action in ("edit", "disable"):
            self.reset_actor()
            with self.subTest(action=action):
                self.assertEqual(self.committed_change_before_user_lock(action, revoke_assignment), 404)

    def test_committed_target_role_and_staff_protection_cannot_be_overwritten(self):
        for action, changes in (("edit", {"role": Role.GROUP_SUPERVISOR}), ("disable", {"is_staff": True})):
            User.objects.filter(pk=self.student_user.pk).update(role=Role.PARTICIPANT, is_staff=False)
            def mutate(locked):
                for field, value in changes.items():
                    setattr(locked, field, value)
                locked.save(update_fields=list(changes))
            with self.subTest(action=action):
                self.assertEqual(self.committed_change_before_user_lock(action, mutate, target=True), 403)
                current = User.objects.get(pk=self.student_user.pk)
                for field, value in changes.items():
                    self.assertEqual(getattr(current, field), value)

    def test_two_disable_requests_from_open_pages_only_change_and_audit_once(self):
        both_loaded = Barrier(2)
        original_lock = participant_lifecycle.locked_users
        lock_sql = []

        def synchronized_lock(*args, **kwargs):
            both_loaded.wait(timeout=self.event_timeout)
            return original_lock(*args, **kwargs)

        def monitor(execute, sql, params, many, context):
            if '"participants_participant"' in sql and "FOR UPDATE" in sql:
                lock_sql.append(sql)
            return execute(sql, params, many, context)

        def request(client):
            with connection.execute_wrapper(monitor):
                return client.post(self.delete_url, {"confirm": "yes"}).status_code

        with patch.object(participant_lifecycle, "locked_users", side_effect=synchronized_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.worker, lambda client=client: request(client)) for client in self.clients]
                results = [future.result(timeout=self.result_timeout) for future in futures]
        self.assertEqual([status for _, status in results], [302, 302])
        self.assertEqual(len({pid for pid, _ in results}), 2)
        self.student_user.refresh_from_db()
        self.assertFalse(self.student_user.is_active)
        self.assertEqual(LogEntry.objects.count(), 1)
        self.assert_balances()
        self.assertEqual(len(lock_sql), 2)
        self.assertTrue(all('FOR UPDATE OF "participants_participant"' in sql for sql in lock_sql))

    def test_participant_scope_move_while_waiting_on_row_is_rechecked_without_join_locks(self):
        for action, module in (("edit", management_views), ("disable", participant_lifecycle)):
            Participant.objects.filter(pk=self.student.pk).update(group=self.group)
            held, attempted, release = Event(), Event(), Event()
            sql_seen = []

            def writer():
                with transaction.atomic():
                    participant = Participant.objects.select_for_update(of=("self",)).get(pk=self.student.pk)
                    participant.group = self.outside_group
                    participant.save(update_fields=["group"])
                    held.set()
                    self.wait_for(release, "release participant move")
                return "moved"

            def monitor(execute, sql, params, many, context):
                if '"participants_participant"' in sql and "FOR UPDATE" in sql:
                    sql_seen.append(sql)
                    attempted.set()
                return execute(sql, params, many, context)

            def request():
                self.wait_for(held, "writer holding moved Participant")
                with connection.execute_wrapper(monitor):
                    return self.clients[0].post(self.edit_url if action == "edit" else self.delete_url,
                                                self.payload() if action == "edit" else {"confirm": "yes"}).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                moving = pool.submit(self.worker, writer)
                requesting = pool.submit(self.worker, request)
                try:
                    self.wait_for(attempted, "request waiting on scoped Participant lock")
                    with self.assertRaises(TimeoutError):
                        requesting.result(timeout=0.2)
                finally:
                    release.set()
                writer_pid, result = moving.result(timeout=self.result_timeout)
                request_pid, status = requesting.result(timeout=self.result_timeout)
            with self.subTest(action=action):
                self.assertNotEqual(writer_pid, request_pid)
                self.assertEqual(result, "moved")
                self.assertEqual(status, 404)
                self.assertTrue(all('FOR UPDATE OF "participants_participant"' in sql for sql in sql_seen))
                self.student.refresh_from_db()
                self.student_user.refresh_from_db()
                self.assertEqual(self.student.group_id, self.outside_group.pk)
                self.assertTrue(self.student_user.is_active)
                self.assertFalse(LogEntry.objects.exists())
                self.assert_balances()

    def test_historical_actor_and_student_fks_commit_while_management_waits_on_participant(self):
        held, attempted = Event(), Event()
        user_locks = []

        def writer():
            with transaction.atomic():
                participant = Participant.objects.select_for_update(of=("self",)).get(pk=self.student.pk)
                held.set()
                self.wait_for(attempted, "management reaching held Participant after User locks")
                management_views.record_management(self.actor, participant, "تدقيق تاريخي مصطنع")
                PointsLedgerEntry.objects.create(
                    participant=participant, points_delta=0, source=PointsLedgerEntry.Source.EXTRA,
                    description="مرجع محفوظ", granted_by=self.actor,
                )
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return "committed"

        def monitor(execute, sql, params, many, context):
            if 'FROM "accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                user_locks.append(sql)
            if '"participants_participant"' in sql and "FOR UPDATE" in sql:
                attempted.set()
            return execute(sql, params, many, context)

        def request():
            self.wait_for(held, "writer holding Participant")
            with connection.execute_wrapper(monitor):
                return self.clients[0].post(self.edit_url, self.payload()).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(self.worker, writer)
            editing = pool.submit(self.worker, request)
            writer_pid, result = writing.result(timeout=self.result_timeout)
            editor_pid, status = editing.result(timeout=self.result_timeout)
        self.assertNotEqual(writer_pid, editor_pid)
        self.assertEqual(result, "committed")
        self.assertEqual(status, 302)
        self.assertTrue(user_locks)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in user_locks))
        self.assertEqual(LogEntry.objects.count(), 2)
        self.assertEqual(PointsLedgerEntry.objects.count(), 1)
        self.assert_balances()
