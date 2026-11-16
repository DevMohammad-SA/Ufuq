"""Real two-backend races, skipped on SQLite (no select_for_update support).

Use config.test_postgres_settings: only a local Unix socket is allowed. Workers
own and close connections; every barrier/event/statement/lock has a timeout.
Synchronization observes real SQL before row locks, never mocks ORM lock results.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from accounts.models import User
from .models import Participant, PointsLedgerEntry
from .test_elite_trip_consents import EliteTripFixtureMixin, trip_models


@skipUnless(connection.vendor == "postgresql" and connection.features.has_select_for_update,
            "Requires PostgreSQL real row locks; SQLite cannot prove concurrency safety")
class EliteTripConcurrencyTests(EliteTripFixtureMixin, TransactionTestCase):
    timeout = 8
    result_timeout = 20

    def setUp(self):
        self.seed(roster_size=14)
        self.client.force_login(self.general)
        self.trip = self.create_trip()
        self.save_trip(self.trip)

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

    def wait(self, event, description):
        if not event.wait(timeout=self.timeout):
            raise AssertionError("Timed out: " + description)

    def concurrent(self, operations):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.worker, operation) for operation in operations]
            results = [job.result(timeout=self.result_timeout) for job in jobs]
        self.assertEqual(len({pid for pid, _ in results}), 2,
                         "Race must use distinct real PostgreSQL backends")
        return [result for _, result in results]

    def racing_requests(self, clients, path, payloads):
        barrier = Barrier(2)
        sql_by_worker = [[], []]
        def request(index):
            reached = False
            def observe(execute, sql, params, many, context):
                nonlocal reached
                if "FOR " in sql and "UPDATE" in sql:
                    sql_by_worker[index].append(sql)
                    # Both requests have passed outer auth and are attempting
                    # their first real transaction row lock before one wins.
                    if not reached:
                        reached = True
                        barrier.wait(timeout=self.timeout)
                return execute(sql, params, many, context)
            with connection.execute_wrapper(observe):
                return clients[index].post(path, payloads[index]).status_code
        statuses = self.concurrent([lambda: request(0), lambda: request(1)])
        self.assertTrue(all(sql_by_worker), "Both workers must execute actual row locks")
        return statuses, sql_by_worker

    def test_two_old_approval_forms_freeze_once_and_increment_version_once(self):
        clients = [self.login(self.general), self.login(self.general2)]
        version = self.trip.version
        payload = {"action": "approve", "confirm": "yes", "version": version}
        statuses, locks = self.racing_requests(clients, self.url("elite_trip_review", self.trip), [payload, payload])
        self.assertEqual(sum(status in (200, 302) for status in statuses), 1)
        self.assertTrue(all(status in (200, 302, 400, 409) for status in statuses))
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, "approved")
        self.assertEqual(self.trip.version, version + 1)
        _, _, Consent, Audit = trip_models()
        self.assertEqual(Audit.objects.filter(trip=self.trip, event="approved").count(), 1)
        self.assertFalse(Consent.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())
        for worker_locks in locks:
            self.assertTrue(any("participants_elitetrip" in sql for sql in worker_locks))
            user_locks = [sql for sql in worker_locks if '"accounts_user"' in sql]
            self.assertTrue(user_locks)
            for sql in user_locks:
                self.assertRegex(sql, r'ORDER BY "accounts_user"\."id" ASC')

    def test_two_old_draft_saves_do_not_overwrite_winning_choices_or_version(self):
        clients = [self.login(self.general), self.login(self.general2)]
        version = self.trip.version
        payloads = [self.draft_data(self.trip, title=f"مسودة متزامنة {i}", selected_ids=[self.a[i].pk])
                    for i in range(2)]
        statuses, _ = self.racing_requests(clients, self.url("elite_trip_edit", self.trip), payloads)
        self.assertEqual(sum(status in (200, 302) for status in statuses), 1)
        winner = next(i for i, status in enumerate(statuses) if status in (200, 302))
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.title, payloads[winner]["title"])
        self.assertEqual(self.trip.version, version + 1)
        selected = set(trip_models()[1].objects.filter(trip=self.trip, is_selected=True)
                       .values_list("participant_id", flat=True))
        self.assertEqual(selected, {self.a[winner].pk})

    def test_two_identical_final_requests_create_one_response_and_one_reference(self):
        self.approve_trip(self.trip)
        nomination = self.nomination(self.trip)
        clients = [self.login(self.a[0].user), self.login(self.a[0].user)]
        payloads = [self.review_reply(nomination, client=client)[1] for client in clients]
        statuses, locks = self.racing_requests(clients, self.url("trip_invitation", nomination), payloads)
        self.assertTrue(all(status in (200, 302) for status in statuses))
        Consent = trip_models()[2]
        self.assertEqual(Consent.objects.filter(nomination=nomination).count(), 1)
        self.assertEqual(Consent.objects.get(nomination=nomination).decision, "yes")
        for sqls in locks:
            self.assertTrue(any("participants_elitetripnomination" in sql for sql in sqls),
                            "Lock the existing nomination, not a missing consent row")
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_two_conflicting_final_decisions_have_one_winner_no_second_response(self):
        self.approve_trip(self.trip)
        nomination = self.nomination(self.trip)
        clients = [self.login(self.a[0].user), self.login(self.a[0].user)]
        payloads = [self.review_reply(nomination, client=clients[i], decision=decision)[1]
                    for i, decision in enumerate(("yes", "no"))]
        statuses, _ = self.racing_requests(clients, self.url("trip_invitation", nomination), payloads)
        self.assertEqual(sum(status in (200, 302) for status in statuses), 1)
        winner = next(i for i, status in enumerate(statuses) if status in (200, 302))
        consent = trip_models()[2].objects.get(nomination=nomination)
        self.assertEqual(consent.decision, payloads[winner]["decision"])
        self.assertEqual(trip_models()[2].objects.count(), 1)

    def committed_change_while_request_waits(self, model, pk, changes, client, path, payload):
        held, waiting, release = Event(), Event(), Event()
        def modifier():
            with transaction.atomic():
                row = model.objects.select_for_update().get(pk=pk)
                for key, value in changes.items():
                    setattr(row, key, value)
                row.save(update_fields=list(changes))
                held.set()
                self.wait(release, "release committed eligibility/lifecycle change")
            return "committed"
        def request():
            self.wait(held, "modifier owns target row")
            def observe(execute, sql, params, many, context):
                if model._meta.db_table in sql and "FOR " in sql and "UPDATE" in sql:
                    waiting.set()
                return execute(sql, params, many, context)
            with connection.execute_wrapper(observe):
                return client.post(path, payload).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            change = pool.submit(self.worker, modifier)
            post = pool.submit(self.worker, request)
            try:
                self.wait(waiting, "stale request reaching held row lock")
                with self.assertRaises(TimeoutError):
                    post.result(timeout=0.2)
            finally:
                release.set()
            change_pid, result = change.result(timeout=self.result_timeout)
            post_pid, status = post.result(timeout=self.result_timeout)
        self.assertNotEqual(change_pid, post_pid)
        self.assertEqual(result, "committed")
        return status

    def test_pending_creation_locks_current_candidate_user_and_rechecks_deactivation(self):
        self.save_trip(self.trip, scope_group=self.group_a.pk, selected_ids=[self.a[0].pk])
        version = self.trip.version
        status = self.committed_change_while_request_waits(User, self.a[0].user_id,
            {"is_active": False}, self.login(self.general), self.url("elite_trip_review", self.trip),
            {"action": "approve", "confirm": "yes", "version": version})
        self.assertIn(status, (400, 409))
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, "draft")
        self.assertEqual(self.trip.version, version)
        self.assertFalse(trip_models()[1].objects.filter(trip=self.trip, notified_at__isnull=False).exists())

    def test_approval_rechecks_candidate_group_after_real_participant_lock_wait(self):
        self.save_trip(self.trip, scope_group=self.group_a.pk, selected_ids=[self.a[0].pk])
        status = self.committed_change_while_request_waits(Participant, self.a[0].pk,
            {"group_id": self.group_b.pk}, self.login(self.general), self.url("elite_trip_review", self.trip),
            {"action": "approve", "confirm": "yes", "version": self.trip.version})
        self.assertIn(status, (400, 409))
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, "draft")

    def test_approval_rechecks_general_role_after_real_user_lock_wait(self):
        client = self.login(self.general)
        status = self.committed_change_while_request_waits(User, self.general.pk,
            {"role": "limited_supervisor"}, client, self.url("elite_trip_review", self.trip),
            {"action": "approve", "confirm": "yes", "version": self.trip.version})
        self.assertIn(status, (403, 404))
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, "draft")

    def test_pending_final_response_locks_existing_nomination_and_rechecks_closed_trip(self):
        self.approve_trip(self.trip)
        nomination = self.nomination(self.trip)
        client, payload, _ = self.review_reply(nomination)
        Trip = trip_models()[0]
        status = self.committed_change_while_request_waits(Trip, self.trip.pk, {"status": "closed"},
            client, self.url("trip_invitation", nomination), payload)
        self.assertIn(status, (400, 403, 404, 409))
        self.assertFalse(trip_models()[2].objects.filter(nomination=nomination).exists())
