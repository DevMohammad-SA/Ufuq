"""Real PostgreSQL row-lock regressions on config.test_postgres_settings only.

Synthetic fixtures, thread-owned connections, bounded events/barriers and local
Unix socket. Run only with UFUQ_TEST_DB_NAME=ufuq_permissions_agent_base.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase, override_settings

from accounts.models import Role, User
from participants.capabilities import (
    can_manage_students, current_report_access, revoke_capabilities, revoke_report_grants,
)
from participants.models import Group, Participant, SupervisorCapability, SupervisorReportPermission
from participants.test_supervisor_student_report_permissions import TEST_SETTINGS, permission_url, synthetic_user


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL row locks")
@override_settings(**TEST_SETTINGS)
class SupervisorStudentReportPermissionConcurrencyTests(TransactionTestCase):
    event_timeout = 8
    result_timeout = 20

    def setUp(self):
        navbar = patch("participants.management_views.build_navbar", return_value=[])
        navbar.start()
        self.addCleanup(navbar.stop)
        # Reverse argument order relative to PK order; locks still must sort.
        self.target = synthetic_user("permission-pg-target", Role.GROUP_SUPERVISOR, is_staff=True)
        self.actors = [synthetic_user(f"permission-pg-actor-{i}", Role.GENERAL_SUPERVISOR) for i in range(2)]
        self.group = Group.objects.create(name="بيئة أذونات PostgreSQL مصطنعة")
        self.group.supervisor.add(self.target)
        self.student_user = synthetic_user("permission-pg-student", Role.PARTICIPANT)
        self.student = Participant.objects.create(user=self.student_user, group=self.group, academic_stage="grade_7",
                                                points=31, miles=310, purchase_points=9)
        self.user_content_type_id = ContentType.objects.get_for_model(User).pk
        self.url = permission_url(self.target)
        self.clients = [Client(), Client()]
        for client, actor in zip(self.clients, self.actors):
            client.force_login(actor)

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
            raise AssertionError(f"Timeout waiting for {description}")

    def audit_count(self):
        return LogEntry.objects.filter(
            content_type_id=self.user_content_type_id, object_id=str(self.target.pk),
            change_message__contains="صلاحيات الطلاب والتقارير",
        ).count()

    def domain_snapshot(self):
        return {model._meta.label: list(model.objects.order_by("pk").values()) for model in (
            User, Group, Group.supervisor.through, Participant,
        )}

    def test_two_stale_requests_create_grants_and_audit_once_with_sorted_no_key_locks(self):
        from participants import supervisor_permissions
        original = supervisor_permissions.locked_users
        both_read = Barrier(2)
        captured = []
        before = self.domain_snapshot()

        def synchronized(*args, **kwargs):
            both_read.wait(timeout=self.event_timeout)
            return original(*args, **kwargs)

        def record(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                captured.append(sql)
            return execute(sql, params, many, context)

        def request(client):
            with connection.execute_wrapper(record):
                return client.post(self.url, {"confirm": "yes", "manage_students": "yes",
                                              "view_reports": "yes", "prepare_reports": "yes"}).status_code

        with patch.object(supervisor_permissions, "locked_users", side_effect=synchronized):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.worker, lambda client=client: request(client)) for client in self.clients]
                results = [future.result(self.result_timeout) for future in futures]
        self.assertEqual([status for _, status in results], [302, 302])
        self.assertEqual(len({pid for pid, _ in results}), 2)
        self.assertEqual(self.domain_snapshot(), before)
        self.assertEqual(SupervisorCapability.objects.count(), 1)
        self.assertEqual(SupervisorReportPermission.objects.count(), 1)
        self.assertTrue(can_manage_students(self.target))
        self.assertTrue(current_report_access(self.target)["can_prepare"])
        self.assertEqual(self.audit_count(), 1)
        self.assertEqual(len(captured), 2)
        for sql in captured:
            self.assertIn("FOR NO KEY UPDATE", sql)
            self.assertRegex(sql, r'ORDER BY "accounts_user"\."id" ASC FOR NO KEY UPDATE')

    def committed_user_change_during_request(self, user, changes, expected_status):
        changed_held = Event()
        request_attempting_lock = Event()
        release_change = Event()

        def modifier():
            with transaction.atomic():
                locked = User.objects.select_for_update(no_key=True).get(pk=user.pk)
                for name, value in changes.items():
                    setattr(locked, name, value)
                locked.save(update_fields=list(changes))
                changed_held.set()
                self.wait_for(release_change, "release eligibility change")
            return "committed"

        def mark(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                request_attempting_lock.set()
            return execute(sql, params, many, context)

        def request():
            self.wait_for(changed_held, "uncommitted changed User row")
            with connection.execute_wrapper(mark):
                return self.clients[0].post(self.url, {"confirm": "yes", "manage_students": "yes", "view_reports": "yes"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            changing = pool.submit(self.worker, modifier)
            requesting = pool.submit(self.worker, request)
            try:
                self.wait_for(request_attempting_lock, "stale authorized request waiting on User lock")
                with self.assertRaises(TimeoutError):
                    requesting.result(timeout=0.2)
            finally:
                release_change.set()
            modifier_pid, result = changing.result(self.result_timeout)
            request_pid, status = requesting.result(self.result_timeout)
        self.assertNotEqual(modifier_pid, request_pid)
        self.assertEqual(result, "committed")
        self.assertEqual(status, expected_status)
        user.refresh_from_db()
        for name, value in changes.items():
            self.assertEqual(getattr(user, name), value)
        self.assertFalse(SupervisorCapability.objects.exists())
        self.assertFalse(SupervisorReportPermission.objects.exists())
        self.assertEqual(self.audit_count(), 0)
        self.assertTrue(self.group.supervisor.filter(pk=self.target.pk).exists())
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (31, 310, 9))

    def test_committed_actor_demotion_while_request_waits_is_denied(self):
        self.committed_user_change_during_request(self.actors[0], {"role": Role.GROUP_SUPERVISOR}, 403)

    def test_committed_actor_becoming_system_while_request_waits_is_still_denied(self):
        self.committed_user_change_during_request(self.actors[0], {"role": Role.SUPERADMIN}, 403)

    def test_committed_actor_deactivation_while_request_waits_is_denied(self):
        self.committed_user_change_during_request(self.actors[0], {"is_active": False}, 403)

    def test_committed_target_deactivation_while_request_waits_cannot_receive_positives(self):
        self.committed_user_change_during_request(self.target, {"is_active": False}, 400)

    def test_committed_target_superuser_flag_while_request_waits_is_protected(self):
        self.committed_user_change_during_request(self.target, {"is_superuser": True}, 404)

    def test_committed_target_system_role_while_request_waits_is_protected(self):
        self.committed_user_change_during_request(self.target, {"role": Role.SUPERADMIN}, 404)

    def test_committed_target_participant_profile_while_request_waits_is_protected(self):
        held = Event()
        attempting = Event()
        release = Event()

        def modifier():
            with transaction.atomic():
                User.objects.select_for_update(no_key=True).get(pk=self.target.pk)
                Participant.objects.create(user_id=self.target.pk, group=self.group, academic_stage="grade_7")
                held.set()
                self.wait_for(release, "release participant-backed target")
            return "committed"

        def mark(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                attempting.set()
            return execute(sql, params, many, context)

        def request():
            self.wait_for(held, "target row and new participant profile")
            with connection.execute_wrapper(mark):
                return self.clients[0].post(self.url, {"confirm": "yes", "view_reports": "yes"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            modifier_future = pool.submit(self.worker, modifier)
            request_future = pool.submit(self.worker, request)
            try:
                self.wait_for(attempting, "permission request attempting target lock")
                with self.assertRaises(TimeoutError):
                    request_future.result(timeout=0.2)
            finally:
                release.set()
            modifier_pid, modifier_result = modifier_future.result(self.result_timeout)
            request_pid, status = request_future.result(self.result_timeout)
        self.assertNotEqual(modifier_pid, request_pid)
        self.assertEqual(modifier_result, "committed")
        self.assertEqual(status, 404)
        self.assertTrue(Participant.objects.filter(user_id=self.target.pk).exists())
        self.assertFalse(SupervisorReportPermission.objects.exists())
        self.assertEqual(self.audit_count(), 0)

    def test_revocation_serializes_a_stale_authorization_then_fresh_check_denies(self):
        SupervisorCapability.objects.create(supervisor=self.target, manage_students=True)
        SupervisorReportPermission.objects.create(supervisor=self.target, view_reports=True, prepare_reports=True)
        revocation_held = Event()
        stale_read = Event()
        attempting_lock = Event()
        release_revocation = Event()

        def revoker():
            self.wait_for(stale_read, "worker loading granted user before revoke")
            with transaction.atomic():
                User.objects.select_for_update(no_key=True).get(pk=self.target.pk)
                self.assertTrue(revoke_capabilities(self.target, self.actors[0]))
                self.assertTrue(revoke_report_grants(self.target, self.actors[0]))
                revocation_held.set()
                self.wait_for(release_revocation, "release committed revocation")
            return "revoked"

        def mark(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                attempting_lock.set()
            return execute(sql, params, many, context)

        def authorizing_writer():
            stale = User.objects.select_related("report_permissions", "limited_capabilities").get(pk=self.target.pk)
            self.assertTrue(stale.report_permissions.prepare_reports)
            self.assertTrue(stale.limited_capabilities.manage_students)
            stale_read.set()
            self.wait_for(revocation_held, "revoker owning User lock")
            with connection.execute_wrapper(mark), transaction.atomic():
                User.objects.select_for_update(no_key=True).get(pk=stale.pk)
                return current_report_access(stale), can_manage_students(stale)

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer_future = pool.submit(self.worker, authorizing_writer)
            revoker_future = pool.submit(self.worker, revoker)
            try:
                self.wait_for(attempting_lock, "stale authorized writer waiting on revoked User")
                with self.assertRaises(TimeoutError):
                    writer_future.result(timeout=0.2)
            finally:
                release_revocation.set()
            revoke_pid, revoke_result = revoker_future.result(self.result_timeout)
            writer_pid, (access, manage_students) = writer_future.result(self.result_timeout)
        self.assertNotEqual(revoke_pid, writer_pid)
        self.assertEqual(revoke_result, "revoked")
        self.assertFalse(manage_students)
        self.assertEqual(access, {"role": Role.GROUP_SUPERVISOR, "can_view": False, "can_prepare": False, "can_center": False})
        self.assertEqual(self.audit_count(), 0)

    def test_no_key_user_locks_allow_historical_audit_fk_while_capability_is_held(self):
        from participants.management_views import record_management
        capability = SupervisorCapability.objects.create(supervisor=self.target, manage_students=False)
        permission_held = Event()
        grant_waiting_on_capability = Event()
        user_locks = []

        def writer():
            with transaction.atomic():
                SupervisorCapability.objects.select_for_update().get(pk=capability.pk)
                permission_held.set()
                self.wait_for(grant_waiting_on_capability, "grant owns User locks and waits on capability")
                record_management(self.target, self.student, "تدقيق تاريخي مصطنع متزامن")
                # Force FK KEY SHARE while grant owns target User NO KEY UPDATE.
                # Plain FOR UPDATE would deadlock, rather than pass by timing.
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return "committed"

        def mark(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                user_locks.append(sql)
            if '"participants_supervisorcapability"' in sql and "FOR UPDATE" in sql:
                grant_waiting_on_capability.set()
            return execute(sql, params, many, context)

        def grant():
            self.wait_for(permission_held, "historical writer holding capability")
            with connection.execute_wrapper(mark):
                return self.clients[0].post(self.url, {"confirm": "yes", "manage_students": "yes", "view_reports": "yes"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(self.worker, writer)
            granting = pool.submit(self.worker, grant)
            writer_pid, result = writing.result(self.result_timeout)
            grant_pid, status = granting.result(self.result_timeout)
        self.assertNotEqual(writer_pid, grant_pid)
        self.assertEqual(result, "committed")
        self.assertEqual(status, 302)
        self.assertTrue(user_locks)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in user_locks))
        self.assertEqual(LogEntry.objects.count(), 2)
        self.assertEqual(self.audit_count(), 1)
        self.assertTrue(can_manage_students(self.target))
        self.assertTrue(current_report_access(self.target)["can_view"])

    def test_missing_policy_is_locked_via_user_before_insert_and_concurrent_denial_is_idempotent(self):
        from participants import supervisor_permissions
        original = supervisor_permissions.locked_users
        both_read = Barrier(2)

        def synchronized(*args, **kwargs):
            both_read.wait(timeout=self.event_timeout)
            return original(*args, **kwargs)

        with patch.object(supervisor_permissions, "locked_users", side_effect=synchronized):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.worker, lambda client=client: client.post(self.url, {"confirm": "yes"}).status_code)
                           for client in self.clients]
                results = [future.result(self.result_timeout) for future in futures]
        self.assertEqual([status for _, status in results], [302, 302])
        self.assertEqual(len({pid for pid, _ in results}), 2)
        self.assertEqual(SupervisorReportPermission.objects.count(), 1)
        self.assertFalse(SupervisorReportPermission.objects.get(supervisor=self.target).view_reports)
        self.assertFalse(SupervisorCapability.objects.exists())
        self.assertEqual(self.audit_count(), 1)
