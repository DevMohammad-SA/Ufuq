"""LOCAL PostgreSQL regressions: User-first grants and fresh store POSTs.

Run with config.test_postgres_settings. No server or real store is involved;
all clients, users, orders, grants and balances belong to the disposable DB.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.urls import reverse

from accounts.models import Role, User
from participants.models import Participant, StoreOrder, StoreProduct, SupervisorCapability
from participants.views import StoreManagementView


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class LimitedStoreConcurrencyTests(TransactionTestCase):
    event_timeout = 8
    result_timeout = 20

    def setUp(self):
        self.manager = self.user("pg-limited-store", Role.LIMITED_SUPERVISOR)
        self.general = self.user("pg-store-general", Role.GENERAL_SUPERVISOR)
        self.capability = SupervisorCapability.objects.create(
            supervisor=self.manager, manage_store=True, granted_by=self.general,
        )
        self.student = Participant.objects.create(
            user=self.user("pg-store-student", Role.PARTICIPANT), group=None,
            academic_stage="grade_7", points=91, miles=910, purchase_points=19,
        )
        self.product = StoreProduct.objects.create(name="Synthetic PG product", price=7, stock=4)
        self.order = StoreOrder.objects.create(
            participant=self.student, product=self.product, price_at_order=7,
        )
        self.url = reverse("participants:store_management")
        self.grant_url = reverse("participants:supervisor_capabilities", kwargs={"pk": self.manager.pk})
        self.store_client = Client()
        self.store_client.force_login(self.manager)
        self.grant_client = Client()
        self.grant_client.force_login(self.general)

    @staticmethod
    def user(username, role):
        return User.objects.create_user(
            username=username, full_name=username, role=role,
            password="SyntheticLimitedStorePG123!",
        )

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
        if not event.wait(timeout=self.event_timeout):
            raise AssertionError(f"Timed out waiting for {description}")

    def assert_original_store_values(self):
        self.order.refresh_from_db()
        self.student.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, StoreOrder.Status.PENDING)
        self.assertIsNone(self.order.completed_at)
        self.assertIsNone(self.order.refunded_at)
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (91, 910, 19))
        self.assertEqual(self.product.stock, 4)
        self.assertEqual(StoreOrder.objects.count(), 1)
        self.assertEqual(StoreProduct.objects.count(), 1)

    def test_revoke_commits_while_stale_store_post_waits_then_post_returns_403(self):
        from participants import management_views

        self.assertEqual(self.store_client.get(self.url).status_code, 200)
        revocation_held, release_revocation, store_waiting = Event(), Event(), Event()
        original_record = management_views.record_management
        store_lock_sql = []

        def hold_revocation(*args, **kwargs):
            result = original_record(*args, **kwargs)
            # Real capability view owns target User then capability; it changed
            # the flag, but no other connection can see that until commit.
            revocation_held.set()
            self.wait_for(release_revocation, "release of real revocation transaction")
            return result

        def mark_store_lock(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                store_lock_sql.append(sql)
                store_waiting.set()
            return execute(sql, params, many, context)

        def stored_post():
            self.wait_for(revocation_held, "revocation holding User and capability")
            with connection.execute_wrapper(mark_store_lock):
                return self.store_client.post(self.url, {
                    "action": "refund", "order_id": self.order.pk,
                }).status_code

        with patch.object(management_views, "record_management", side_effect=hold_revocation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                revoke = pool.submit(self.worker, lambda: self.grant_client.post(
                    self.grant_url, {"confirm": "yes"},
                ).status_code)
                storing = pool.submit(self.worker, stored_post)
                try:
                    self.wait_for(store_waiting, "stale store POST attempting held User lock")
                    with self.assertRaises(TimeoutError):
                        storing.result(timeout=0.2)
                finally:
                    release_revocation.set()
                revoke_pid, revoke_status = revoke.result(timeout=self.result_timeout)
                store_pid, store_status = storing.result(timeout=self.result_timeout)
        self.assertNotEqual(revoke_pid, store_pid)
        self.assertEqual(revoke_status, 302)
        self.assertEqual(store_status, 403)
        self.assertTrue(store_lock_sql)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in store_lock_sql))
        self.capability.refresh_from_db()
        self.assertFalse(self.capability.manage_store)
        self.assert_original_store_values()

    def test_actual_capability_update_waits_on_store_user_before_locking_capability(self):
        store_held, release_store, grant_waiting = Event(), Event(), Event()
        original_post = StoreManagementView._post_authorized
        grant_capability_sql = []
        grant_user_sql = []

        def hold_store(view, request, *args, **kwargs):
            response = original_post(view, request, *args, **kwargs)
            store_held.set()
            self.wait_for(release_store, "release of authorized store transaction")
            return response

        def mark_grant_lock(execute, sql, params, many, context):
            if '"accounts_user"' in sql and "FOR " in sql and "UPDATE" in sql:
                grant_user_sql.append(sql)
                grant_waiting.set()
            if '"participants_supervisorcapability"' in sql and "FOR UPDATE" in sql:
                grant_capability_sql.append(sql)
            return execute(sql, params, many, context)

        def revoke():
            self.wait_for(store_held, "store keeping validated User lock until commit")
            with connection.execute_wrapper(mark_grant_lock):
                return self.grant_client.post(self.grant_url, {"confirm": "yes"}).status_code

        with patch.object(StoreManagementView, "_post_authorized", new=hold_store):
            with ThreadPoolExecutor(max_workers=2) as pool:
                storing = pool.submit(self.worker, lambda: self.store_client.post(
                    self.url, {"action": "complete", "order_id": self.order.pk},
                ).status_code)
                revoking = pool.submit(self.worker, revoke)
                try:
                    self.wait_for(grant_waiting, "real capability view attempting target User lock")
                    with self.assertRaises(TimeoutError):
                        revoking.result(timeout=0.2)
                    self.assertEqual(grant_capability_sql, [], "Grant must not lock capability before target User")
                finally:
                    release_store.set()
                store_pid, store_status = storing.result(timeout=self.result_timeout)
                grant_pid, grant_status = revoking.result(timeout=self.result_timeout)
        self.assertNotEqual(store_pid, grant_pid)
        self.assertEqual((store_status, grant_status), (302, 302))
        self.assertTrue(grant_user_sql)
        self.assertTrue(all("FOR NO KEY UPDATE" in sql for sql in grant_user_sql))
        self.assertTrue(grant_capability_sql)
        self.capability.refresh_from_db()
        self.assertFalse(self.capability.manage_store)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, StoreOrder.Status.COMPLETED)
        self.student.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (91, 910, 19))
        self.assertEqual(self.product.stock, 4)

    def test_distinct_granted_actors_refunding_same_order_credit_only_once(self):
        other = self.user("pg-other-limited-store", Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=other, manage_store=True)
        other_client = Client()
        other_client.force_login(other)
        both_read_order = Barrier(2)

        def synchronize_order_read(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if '"participants_storeorder"' in sql and sql.lstrip().startswith("SELECT") and "FOR UPDATE" not in sql:
                # Both actors read PENDING before either takes the refund's
                # existing Order -> Participant -> Product lock chain.
                both_read_order.wait(timeout=self.event_timeout)
            return result

        def refund(client):
            with connection.execute_wrapper(synchronize_order_read):
                return client.post(self.url, {"action": "refund", "order_id": self.order.pk}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, lambda client=client: refund(client))
                       for client in (self.store_client, other_client)]
            results = [future.result(timeout=self.result_timeout) for future in futures]
        self.assertEqual([status for _, status in results], [302, 302])
        self.assertEqual(len({pid for pid, _ in results}), 2)
        self.student.refresh_from_db()
        self.product.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, StoreOrder.Status.REFUNDED)
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (91, 910, 26))
        self.assertEqual(self.product.stock, 5)

    def test_stale_completion_cannot_undo_refund_or_enable_second_credit(self):
        other = self.user("pg-other-completion-refund", Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=other, manage_store=True)
        other_client = Client()
        other_client.force_login(other)
        completion_read, release_completion = Event(), Event()

        def hold_completion_read(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if (not completion_read.is_set() and '"participants_storeorder"' in sql
                    and sql.lstrip().startswith("SELECT") and "FOR UPDATE" not in sql):
                # The result contains PENDING. A distinct operator commits a
                # real refund before this old result reaches the completion.
                completion_read.set()
                self.wait_for(release_completion, "release of stale completion")
            return result

        def complete():
            with connection.execute_wrapper(hold_completion_read):
                return self.store_client.post(self.url, {
                    "action": "complete", "order_id": self.order.pk,
                }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            completing = pool.submit(self.worker, complete)
            try:
                self.wait_for(completion_read, "completion reading PENDING before refund")
                refunding = pool.submit(self.worker, lambda: other_client.post(self.url, {
                    "action": "refund", "order_id": self.order.pk,
                }).status_code)
                refund_pid, refund_status = refunding.result(timeout=self.result_timeout)
                self.assertEqual(refund_status, 302)
                self.order.refresh_from_db()
                self.assertEqual(self.order.status, StoreOrder.Status.REFUNDED)
            finally:
                release_completion.set()
            completion_pid, completion_status = completing.result(timeout=self.result_timeout)
        self.assertNotEqual(completion_pid, refund_pid)
        self.assertEqual(completion_status, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, StoreOrder.Status.REFUNDED)
        self.assertIsNone(self.order.completed_at)
        self.assertIsNotNone(self.order.refunded_at)
        refunded_at = self.order.refunded_at
        self.student.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (91, 910, 26))
        self.assertEqual(self.product.stock, 5)

        self.assertEqual(other_client.post(self.url, {
            "action": "refund", "order_id": self.order.pk,
        }).status_code, 302)
        self.order.refresh_from_db()
        self.student.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, StoreOrder.Status.REFUNDED)
        self.assertEqual(self.order.refunded_at, refunded_at)
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), (91, 910, 26))
        self.assertEqual(self.product.stock, 5)
