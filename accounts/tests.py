from django.test import TestCase
from django.urls import reverse

from accounts.models import PasswordResetRequest, Role, User
from participants.models import Participant


class ParticipantPasswordFlowTests(TestCase):
    def _make_participant(self, national_id="1234567890", full_name="مشارك تجريبي"):
        """Mirror ParticipantImportView's account creation logic."""
        user = User.objects.create_user(
            national_id=national_id,
            full_name=full_name,
            role=Role.PARTICIPANT,
            password=national_id,
        )
        user.must_set_password = True
        user.save(update_fields=["must_set_password"])
        Participant.objects.create(user=user, academic_stage="grade_7")
        return user

    # 1. New participant logs in with national_id as password, then is forced
    #    to the set-password page for every other URL.
    def test_first_login_forces_set_password(self):
        self._make_participant()
        ok = self.client.login(username="1234567890", password="1234567890")
        self.assertTrue(ok)

        # Any other page redirects to set-password.
        resp = self.client.get(reverse("participants:dashboard"))
        self.assertRedirects(resp, reverse("accounts:set_password"))

        # Even a hand-typed store URL.
        resp = self.client.get(reverse("participants:store"))
        self.assertRedirects(resp, reverse("accounts:set_password"))

        # The set-password page itself is reachable.
        resp = self.client.get(reverse("accounts:set_password"))
        self.assertEqual(resp.status_code, 200)

    # 2. After setting an 8-char password the participant reaches their
    #    dashboard and stays logged in.
    def test_set_password_then_normal_access(self):
        self._make_participant()
        self.client.login(username="1234567890", password="1234567890")

        resp = self.client.post(
            reverse("accounts:set_password"),
            {"new_password1": "newpass123", "new_password2": "newpass123"},
        )
        self.assertRedirects(resp, reverse("participants:dashboard"))

        user = User.objects.get(national_id="1234567890")
        self.assertFalse(user.must_set_password)
        self.assertTrue(user.check_password("newpass123"))

        # Session still valid (update_session_auth_hash worked).
        resp = self.client.get(reverse("participants:dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_set_password_rejects_short_password(self):
        self._make_participant()
        self.client.login(username="1234567890", password="1234567890")
        resp = self.client.post(
            reverse("accounts:set_password"),
            {"new_password1": "short", "new_password2": "short"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(User.objects.get(national_id="1234567890").must_set_password)

    # 3. Old national_id password no longer works after the change.
    def test_old_password_fails_after_change(self):
        user = self._make_participant()
        user.set_password("newpass123")
        user.must_set_password = False
        user.save()

        self.assertFalse(self.client.login(username="1234567890", password="1234567890"))
        self.assertTrue(self.client.login(username="1234567890", password="newpass123"))

    # 4. Forgot-password request creates a PasswordResetRequest.
    def test_forgot_password_creates_request(self):
        user = self._make_participant()
        resp = self.client.post(
            reverse("accounts:forgot_password"), {"national_id": "1234567890"}
        )
        self.assertRedirects(resp, reverse("accounts:login_participant"))
        self.assertEqual(
            PasswordResetRequest.objects.filter(user=user, resolved=False).count(), 1
        )

    # 6. Same message + no request row for an unknown national_id.
    def test_forgot_password_unknown_id_is_indistinguishable(self):
        resp = self.client.post(
            reverse("accounts:forgot_password"),
            {"national_id": "9999999999"},
            follow=True,
        )
        self.assertContains(resp, "تم إرسال طلبك")
        self.assertEqual(PasswordResetRequest.objects.count(), 0)

    # 5. General supervisor approval resets password + re-arms must_set_password.
    def test_supervisor_approval_resets_password(self):
        participant = self._make_participant()
        participant.set_password("newpass123")
        participant.must_set_password = False
        participant.save()
        req = PasswordResetRequest.objects.create(user=participant)

        supervisor = User.objects.create_user(
            username="gen", password="superpass1", role=Role.GENERAL_SUPERVISOR
        )
        self.client.force_login(supervisor)

        resp = self.client.post(
            reverse("participants:general_supervisor_dashboard"),
            {"action": "approve_password_reset", "reset_id": req.id},
        )
        self.assertRedirects(
            resp, reverse("participants:general_supervisor_dashboard")
        )

        participant.refresh_from_db()
        req.refresh_from_db()
        self.assertTrue(participant.must_set_password)
        self.assertTrue(participant.check_password("1234567890"))
        self.assertTrue(req.resolved)
        self.assertEqual(req.resolved_by, supervisor)

        # Next login forces the set-password page again.
        self.client.logout()
        self.client.login(username="1234567890", password="1234567890")
        resp = self.client.get(reverse("participants:dashboard"))
        self.assertRedirects(resp, reverse("accounts:set_password"))

    # Middleware must never trap a participant away from logout.
    def test_logout_reachable_while_forced(self):
        self._make_participant()
        self.client.login(username="1234567890", password="1234567890")
        resp = self.client.post(reverse("accounts:logout"))
        self.assertEqual(resp.status_code, 302)


class SupervisorLoginUnaffectedTests(TestCase):
    def test_supervisor_still_needs_correct_password(self):
        User.objects.create_user(
            username="boss", password="rightpass1", role=Role.GENERAL_SUPERVISOR
        )
        self.assertFalse(self.client.login(username="boss", password="wrongpass"))
        self.assertTrue(self.client.login(username="boss", password="rightpass1"))
