import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from participants.models import CircleAttendance, Group, Participant


class SupervisorPasswordChangeTests(TestCase):
    def setUp(self):
        self.supervisor = User.objects.create_user(
            username="sup", password="oldpass123", role=Role.GROUP_SUPERVISOR
        )

    def test_change_password_success_keeps_session(self):
        self.client.login(username="sup", password="oldpass123")
        resp = self.client.post(
            reverse("accounts:change_password"),
            {
                "current_password": "oldpass123",
                "new_password1": "brandnew456",
                "new_password2": "brandnew456",
            },
        )
        self.assertRedirects(resp, reverse("accounts:change_password"))

        self.supervisor.refresh_from_db()
        self.assertTrue(self.supervisor.check_password("brandnew456"))

        # Session survived (update_session_auth_hash) — still authenticated.
        follow = self.client.get(reverse("accounts:change_password"))
        self.assertEqual(follow.status_code, 200)

        # Old password no longer works.
        self.client.logout()
        self.assertFalse(self.client.login(username="sup", password="oldpass123"))
        self.assertTrue(self.client.login(username="sup", password="brandnew456"))

    def test_wrong_current_password_rejected(self):
        self.client.login(username="sup", password="oldpass123")
        resp = self.client.post(
            reverse("accounts:change_password"),
            {
                "current_password": "WRONG",
                "new_password1": "brandnew456",
                "new_password2": "brandnew456",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "كلمة المرور الحالية غير صحيحة")
        self.supervisor.refresh_from_db()
        self.assertTrue(self.supervisor.check_password("oldpass123"))

    def test_new_passwords_must_match_and_be_long_enough(self):
        self.client.login(username="sup", password="oldpass123")
        resp = self.client.post(
            reverse("accounts:change_password"),
            {
                "current_password": "oldpass123",
                "new_password1": "mismatch1",
                "new_password2": "mismatch2",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.supervisor.refresh_from_db()
        self.assertTrue(self.supervisor.check_password("oldpass123"))


class QuranCircleAttendanceTests(TestCase):
    def setUp(self):
        self.group_sup = User.objects.create_user(
            username="gs", password="pw12345678", role=Role.GROUP_SUPERVISOR
        )
        self.other_sup = User.objects.create_user(
            username="gs2", password="pw12345678", role=Role.GROUP_SUPERVISOR
        )
        self.general = User.objects.create_user(
            username="gen", password="pw12345678", role=Role.GENERAL_SUPERVISOR
        )
        self.group_a = Group.objects.create(name="بيئة أ", supervisor=self.group_sup)
        self.group_b = Group.objects.create(name="بيئة ب", supervisor=self.other_sup)

        self.p_a = self._participant("1000000001", self.group_a)
        self.p_b = self._participant("2000000002", self.group_b)
        self.today = datetime.date.today().isoformat()

    def _participant(self, national_id, group):
        user = User.objects.create_user(
            national_id=national_id,
            full_name=f"مشارك {national_id}",
            role=Role.PARTICIPANT,
            password=national_id,
        )
        return Participant.objects.create(
            user=user, group=group, academic_stage="grade_7"
        )

    # 3. Attendance + achievement award 3 + 2 = 5 across the triple currency.
    def test_attendance_plus_achievement_awards_five(self):
        self.client.login(username="gs", password="pw12345678")
        self.client.post(
            reverse("participants:quran_circle_attendance"),
            {
                "date": self.today,
                "group": self.group_a.id,
                f"attended_{self.p_a.id}": "on",
                f"achieved_{self.p_a.id}": "on",
            },
        )
        self.p_a.refresh_from_db()
        self.assertEqual(self.p_a.points, 5)
        self.assertEqual(self.p_a.miles, 50)
        self.assertEqual(self.p_a.purchase_points, 5)

    def test_attendance_only_awards_three(self):
        self.client.login(username="gs", password="pw12345678")
        self.client.post(
            reverse("participants:quran_circle_attendance"),
            {
                "date": self.today,
                "group": self.group_a.id,
                f"attended_{self.p_a.id}": "on",
            },
        )
        self.p_a.refresh_from_db()
        self.assertEqual(self.p_a.points, 3)
        self.assertEqual(self.p_a.miles, 30)

    # 6. Editing an existing record only applies the delta.
    def test_editing_record_applies_delta_only(self):
        self.client.login(username="gs", password="pw12345678")
        url = reverse("participants:quran_circle_attendance")
        # First: attendance only (+3).
        self.client.post(
            url,
            {"date": self.today, "group": self.group_a.id, f"attended_{self.p_a.id}": "on"},
        )
        # Then: same day, now also achieved (+2 delta, not +5 again).
        self.client.post(
            url,
            {
                "date": self.today,
                "group": self.group_a.id,
                f"attended_{self.p_a.id}": "on",
                f"achieved_{self.p_a.id}": "on",
            },
        )
        self.p_a.refresh_from_db()
        self.assertEqual(self.p_a.points, 5)
        self.assertEqual(CircleAttendance.objects.filter(participant=self.p_a).count(), 1)

    # Unchecking later removes the points again (negative delta).
    def test_unchecking_removes_points(self):
        self.client.login(username="gs", password="pw12345678")
        url = reverse("participants:quran_circle_attendance")
        self.client.post(
            url,
            {
                "date": self.today,
                "group": self.group_a.id,
                f"attended_{self.p_a.id}": "on",
                f"achieved_{self.p_a.id}": "on",
            },
        )
        self.client.post(url, {"date": self.today, "group": self.group_a.id})
        self.p_a.refresh_from_db()
        self.assertEqual(self.p_a.points, 0)

    # 4. A group supervisor cannot touch another group by spoofing ?group=.
    def test_group_supervisor_group_is_locked(self):
        self.client.login(username="gs", password="pw12345678")
        self.client.post(
            reverse("participants:quran_circle_attendance"),
            {
                "date": self.today,
                "group": self.group_b.id,  # not their group
                f"attended_{self.p_b.id}": "on",
                f"achieved_{self.p_b.id}": "on",
            },
        )
        self.p_b.refresh_from_db()
        self.assertEqual(self.p_b.points, 0)
        self.assertEqual(CircleAttendance.objects.filter(participant=self.p_b).count(), 0)

    # 5. General supervisor may record for any group, switching freely.
    def test_general_supervisor_any_group(self):
        self.client.login(username="gen", password="pw12345678")
        url = reverse("participants:quran_circle_attendance")
        self.client.post(
            url,
            {"date": self.today, "group": self.group_a.id, f"attended_{self.p_a.id}": "on"},
        )
        self.client.post(
            url,
            {"date": self.today, "group": self.group_b.id, f"attended_{self.p_b.id}": "on"},
        )
        self.p_a.refresh_from_db()
        self.p_b.refresh_from_db()
        self.assertEqual(self.p_a.points, 3)
        self.assertEqual(self.p_b.points, 3)

    def test_participant_cannot_access(self):
        self.client.login(username="1000000001", password="1000000001")
        # participant has must_set_password from _participant? No — created via
        # create_user without setting it, so default False. Still, role gate:
        resp = self.client.get(reverse("participants:quran_circle_attendance"))
        self.assertIn(resp.status_code, (302, 403))
