"""Security regressions for session revocation and concurrent login identifiers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless

from django.contrib.auth import BACKEND_SESSION_KEY
from django.db import close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from accounts.backends import NationalIDOrUsernameBackend
from accounts.models import Role, User
from .management_locking import lock_login_identifiers


class DeactivatedSessionTests(TestCase):
    def test_existing_custom_backend_session_stops_after_manager_deactivates_supervisor(self):
        manager = User.objects.create_user(
            username="session-manager", password="ManagerPass123!", role=Role.GENERAL_SUPERVISOR,
        )
        supervisor = User.objects.create_user(
            username="session-supervisor", password="SupervisorPass123!", role=Role.GROUP_SUPERVISOR,
        )
        supervisor_client = Client()
        self.assertTrue(supervisor_client.login(username=supervisor.username, password="SupervisorPass123!"))
        self.assertEqual(supervisor_client.session[BACKEND_SESSION_KEY], "accounts.backends.NationalIDOrUsernameBackend")
        data_url = reverse("participants:participants_data")
        self.assertEqual(supervisor_client.get(data_url).status_code, 200)
        self.client.force_login(manager)
        response = self.client.post(
            reverse("participants:supervisor_delete", kwargs={"pk": supervisor.pk}), {"confirm": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        supervisor.refresh_from_db()
        self.assertFalse(supervisor.is_active)
        self.assertIsNone(NationalIDOrUsernameBackend().get_user(supervisor.pk))
        self.assertEqual(supervisor_client.get(data_url).status_code, 302)
        self.assertEqual(supervisor_client.post(reverse("participants:supervisor_dashboard"), {}).status_code, 302)
        fresh = Client()
        self.assertFalse(fresh.login(username=supervisor.username, password="SupervisorPass123!"))

    def test_inactive_general_supervisor_session_cannot_use_management_routes(self):
        user = User.objects.create_user(username="inactive-manager", password="ManagerPass123!", role=Role.GENERAL_SUPERVISOR)
        self.assertTrue(self.client.login(username=user.username, password="ManagerPass123!"))
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(reverse("participants:supervisor_list")).status_code, 302)
        self.assertEqual(self.client.post(reverse("participants:supervisor_add"), {}).status_code, 302)


@skipUnless(connection.vendor == "postgresql", "requires real PostgreSQL advisory transaction locks")
class IdentifierConcurrencyTests(TransactionTestCase):
    def test_username_transaction_blocks_national_id_creation_until_revalidation(self):
        manager = User.objects.create_user(
            username="concurrent-manager", password="ManagerPass123!", role=Role.GENERAL_SUPERVISOR,
        )
        first_locked, release_first, second_started = Event(), Event(), Event()
        identifier = "1000000888"

        def first_writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    lock_login_identifiers({"username": identifier})
                    first_locked.set()
                    if not release_first.wait(10):
                        raise RuntimeError("Timed out waiting to release synthetic transaction")
                    User.objects.create_user(username=identifier, password="FirstPass123!", role=Role.GROUP_SUPERVISOR)
            finally:
                close_old_connections()

        def second_writer():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10000ms'")
                client = Client()
                client.force_login(User.objects.get(pk=manager.pk))
                second_started.set()
                response = client.post(reverse("participants:supervisor_add"), {
                    "full_name": "مشرف لا يجب إنشاؤه",
                    "username": "concurrent-second", "national_id": identifier,
                    "role": Role.GROUP_SUPERVISOR, "environments": [], "is_active": "on",
                    "password1": "StrongPass123!", "password2": "StrongPass123!",
                })
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_writer)
            self.assertTrue(first_locked.wait(5))
            second = pool.submit(second_writer)
            try:
                self.assertTrue(second_started.wait(5))
                # Release the first transaction only after the second has begun.
                # The second must revalidate after the username becomes visible.
            finally:
                release_first.set()
            first.result(timeout=15)
            self.assertEqual(second.result(timeout=15), 200)
        self.assertTrue(User.objects.filter(username=identifier).exists())
        self.assertFalse(User.objects.filter(national_id=identifier).exists())
        self.assertFalse(User.objects.filter(username="concurrent-second").exists())
