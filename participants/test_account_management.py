"""Focused regression tests for general-supervisor account management.

Run this module with ``config.test_settings`` (and ``config.test_postgres_settings``
for the one PostgreSQL locking regression).  The module deliberately imports no
management forms or views: those modules are implemented alongside these tests,
and routes are resolved only inside test setup.
"""

from datetime import date
from unittest import skipUnless

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, User
from participants.models import (
    Group,
    MeetingAttendance,
    Participant,
    TaskReviewPermission,
)


class AccountManagementBase(TestCase):
    """Small, explicit fixture covering every protected account shape."""

    password = "CurrentPass123!"
    changed_password = "ChangedPass456!"

    def setUp(self):
        # ``SUPERADMIN`` itself is sufficient for access; it must not depend on
        # Django's is_staff/is_superuser flags.
        self.general = self.make_supervisor(
            "accounts-general", Role.GENERAL_SUPERVISOR, "المشرف العام"
        )
        self.root = self.make_supervisor(
            "accounts-root", Role.SUPERADMIN, "مشرف النظام"
        )
        self.group_supervisor = self.make_supervisor(
            "accounts-group", Role.GROUP_SUPERVISOR, "مشرف البيئة"
        )
        self.other_group_supervisor = self.make_supervisor(
            "accounts-other-group", Role.GROUP_SUPERVISOR, "مشرف البيئة الآخر"
        )
        self.privileged_group_user = self.make_supervisor(
            "accounts-is-staff", Role.GROUP_SUPERVISOR, "حساب أدمن مستبعد",
            is_staff=True,
        )
        self.privileged_root_user = self.make_supervisor(
            "accounts-is-superuser", Role.SUPERADMIN, "حساب النظام المستبعد",
            is_staff=True,
            is_superuser=True,
        )

        self.group_a = Group.objects.create(name="بيئة الحسابات ألف")
        self.group_b = Group.objects.create(name="بيئة الحسابات باء")
        # Group.supervisor is many-to-many: attach synthetic supervisors via
        # ``add`` rather than attempting to pass it to Group.objects.create().
        self.group_a.supervisor.add(self.group_supervisor)
        self.group_b.supervisor.add(self.other_group_supervisor)

        self.participant_user = User.objects.create_user(
            username="accounts-student",
            national_id="1000000001",
            password=self.password,
            role=Role.PARTICIPANT,
            full_name="المشارك الأصلي",
            must_set_password=True,
        )
        self.participant = Participant.objects.create(
            user=self.participant_user,
            group=self.group_a,
            academic_stage="grade_7",
            phone="0500000000",
            guardian_phone="0511111111",
            points=91,
            miles=910,
            purchase_points=19,
        )

        # This deliberately inconsistent legacy-shaped row confirms that a
        # User with a Participant record is never manageable as a supervisor.
        self.participant_backed_supervisor = self.make_supervisor(
            "accounts-participant-backed", Role.GROUP_SUPERVISOR, "مشرف له ملف مشارك"
        )
        Participant.objects.create(
            user=self.participant_backed_supervisor,
            group=self.group_a,
            academic_stage="grade_6",
        )

        # A national ID which must not be usable as another account's username.
        self.identity_owner = User.objects.create_user(
            username="accounts-identity-owner",
            national_id="1000000099",
            password=self.password,
            role=Role.PARTICIPANT,
            full_name="مالك هوية أخرى",
        )

        # Resolve after Django has loaded the URLconf and after the parent has
        # added the management routes; never resolve a route at import time.
        self.participant_edit_url = reverse(
            "participants:participant_edit", kwargs={"pk": self.participant.pk}
        )
        self.supervisor_list_url = reverse("participants:supervisor_list")
        self.supervisor_add_url = reverse("participants:supervisor_add")
        self.supervisor_edit_url = lambda user: reverse(
            "participants:supervisor_edit", kwargs={"pk": user.pk}
        )
        self.supervisor_delete_url = lambda user: reverse(
            "participants:supervisor_delete", kwargs={"pk": user.pk}
        )

    @classmethod
    def make_supervisor(cls, username, role, full_name, **extra_fields):
        return User.objects.create_user(
            username=username,
            password=cls.password,
            role=role,
            full_name=full_name,
            **extra_fields,
        )

    def participant_payload(self, **changes):
        payload = {
            "full_name": "المشارك المعدّل",
            "national_id": "1000000002",
            "group": str(self.group_b.pk),
            "academic_stage": "grade_8",
            "phone": "0555555555",
            "guardian_phone": "0566666666",
        }
        payload.update(changes)
        return payload

    def supervisor_payload(self, user=None, **changes):
        """Payload valid for both creation and an edit of ``user``.

        Passwords are mandatory only on creation.  An edit starts with empty
        password values to assert that its existing hash is retained.
        """
        user = user or self.group_supervisor
        payload = {
            "full_name": "مشرف معدّل",
            "username": user.username,
            "national_id": user.national_id or "",
            "role": user.role,
            "environments": [str(group.pk) for group in user.group_set.all()],
            "is_active": "on" if user.is_active else "",
            "password1": "",
            "password2": "",
        }
        payload.update(changes)
        return payload

    def assert_successful_post(self, response):
        """Management forms may redirect or render their success state."""
        self.assertIn(response.status_code, (200, 302), response.content.decode())


class AccountManagementAccessTests(AccountManagementBase):
    def test_anonymous_management_routes_redirect_to_supervisor_login(self):
        urls = (
            self.participant_edit_url,
            self.supervisor_list_url,
            self.supervisor_add_url,
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_delete_url(self.group_supervisor),
        )
        login_url = reverse("accounts:login_supervisor")
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(login_url, response["Location"])

    def test_general_and_role_only_superadmin_can_reach_management_pages(self):
        urls = (
            self.participant_edit_url,
            self.supervisor_list_url,
            self.supervisor_add_url,
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_delete_url(self.group_supervisor),
        )
        for user in (self.general, self.root):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(actor=user.role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_group_supervisor_and_participant_are_forbidden_on_every_route(self):
        # Test role authorization after the existing first-password middleware.
        self.participant_user.must_set_password = False
        self.participant_user.save(update_fields=["must_set_password"])
        urls = (
            self.participant_edit_url,
            self.supervisor_list_url,
            self.supervisor_add_url,
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_delete_url(self.group_supervisor),
        )
        for user in (self.group_supervisor, self.participant_user):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(actor=user.role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_data_and_detail_only_expose_participant_edit_to_account_managers(self):
        data_url = reverse("participants:participants_data")
        detail_url = reverse(
            "participants:student_detail", kwargs={"pk": self.participant.pk}
        )
        for user, expected in ((self.general, True), (self.group_supervisor, False)):
            self.client.force_login(user)
            for url in (data_url, detail_url):
                with self.subTest(actor=user.role, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["can_manage_accounts"], expected)
                    if expected:
                        self.assertContains(response, self.participant_edit_url)
                    else:
                        self.assertNotContains(response, self.participant_edit_url)


class ParticipantEditTests(AccountManagementBase):
    def test_edit_updates_exactly_six_participant_fields_and_preserves_protected_state(self):
        original_hash = self.participant_user.password
        original_balances = (
            self.participant.points,
            self.participant.miles,
            self.participant.purchase_points,
        )
        self.client.force_login(self.general)
        response = self.client.post(
            self.participant_edit_url,
            self.participant_payload(
                # Crafted fields must be ignored because the form has an
                # explicit allowlist rather than binding model fields broadly.
                password="AttackerPass123!",
                role=Role.SUPERADMIN,
                must_set_password="",
                points="9999",
                miles="9999",
                purchase_points="9999",
                is_staff="on",
                is_superuser="on",
            ),
        )
        self.assert_successful_post(response)

        self.participant.refresh_from_db()
        self.participant_user.refresh_from_db()
        self.assertEqual(self.participant_user.full_name, "المشارك المعدّل")
        self.assertEqual(self.participant_user.national_id, "1000000002")
        self.assertEqual(self.participant.group, self.group_b)
        self.assertEqual(self.participant.academic_stage, "grade_8")
        self.assertEqual(self.participant.phone, "0555555555")
        self.assertEqual(self.participant.guardian_phone, "0566666666")
        self.assertEqual(
            (
                self.participant.points,
                self.participant.miles,
                self.participant.purchase_points,
            ),
            original_balances,
        )
        self.assertEqual(self.participant_user.password, original_hash)
        self.assertTrue(self.participant_user.check_password(self.password))
        self.assertEqual(self.participant_user.role, Role.PARTICIPANT)
        self.assertTrue(self.participant_user.must_set_password)
        self.assertFalse(self.participant_user.is_staff)
        self.assertFalse(self.participant_user.is_superuser)

    def test_participant_group_may_be_cleared(self):
        self.client.force_login(self.root)
        response = self.client.post(
            self.participant_edit_url, self.participant_payload(group="")
        )
        self.assert_successful_post(response)
        self.participant.refresh_from_db()
        self.assertIsNone(self.participant.group)

    def test_participant_identity_validation_is_atomic_for_bad_duplicate_and_username_collisions(self):
        self.client.force_login(self.general)
        original = {
            "full_name": self.participant_user.full_name,
            "national_id": self.participant_user.national_id,
            "group_id": self.participant.group_id,
            "academic_stage": self.participant.academic_stage,
            "phone": self.participant.phone,
            "guardian_phone": self.participant.guardian_phone,
            "balances": (
                self.participant.points,
                self.participant.miles,
                self.participant.purchase_points,
            ),
        }
        invalid_ids = (
            "",
            "123456789",
            "12345678901",
            "123456789x",
            self.identity_owner.national_id,
            self.identity_owner.username,
        )
        for national_id in invalid_ids:
            with self.subTest(national_id=national_id):
                response = self.client.post(
                    self.participant_edit_url,
                    self.participant_payload(
                        national_id=national_id,
                        full_name="اسم يجب ألا يحفظ",
                        group=str(self.group_b.pk),
                    ),
                )
                self.assertEqual(response.status_code, 200)
                self.participant.refresh_from_db()
                self.participant_user.refresh_from_db()
                self.assertEqual(self.participant_user.full_name, original["full_name"])
                self.assertEqual(self.participant_user.national_id, original["national_id"])
                self.assertEqual(self.participant.group_id, original["group_id"])
                self.assertEqual(self.participant.academic_stage, original["academic_stage"])
                self.assertEqual(self.participant.phone, original["phone"])
                self.assertEqual(self.participant.guardian_phone, original["guardian_phone"])
                self.assertEqual(
                    (
                        self.participant.points,
                        self.participant.miles,
                        self.participant.purchase_points,
                    ),
                    original["balances"],
                )

    @skipUnless(
        connection.vendor == "postgresql",
        "requires PostgreSQL SELECT ... FOR UPDATE semantics",
    )
    def test_postgres_ungrouped_participant_can_be_locked_and_updated(self):
        """A nullable Group join must never be locked by the update queryset."""
        self.participant.group = None
        self.participant.save(update_fields=["group"])
        self.client.force_login(self.general)
        response = self.client.post(
            self.participant_edit_url, self.participant_payload(group="")
        )
        self.assert_successful_post(response)
        self.participant.refresh_from_db()
        self.assertIsNone(self.participant.group)


class SupervisorManagementTests(AccountManagementBase):
    def test_supervisor_list_excludes_system_privileged_and_participant_backed_accounts(self):
        self.client.force_login(self.general)
        response = self.client.get(self.supervisor_list_url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for visible in (self.group_supervisor, self.other_group_supervisor, self.general):
            with self.subTest(visible=visible.username):
                self.assertIn(visible.full_name, html)
        for hidden in (
            self.root,
            self.privileged_group_user,
            self.privileged_root_user,
            self.participant_backed_supervisor,
        ):
            with self.subTest(hidden=hidden.username):
                self.assertNotIn(hidden.full_name, html)
                self.assertIn(
                    self.client.get(self.supervisor_edit_url(hidden)).status_code,
                    (403, 404),
                )

    def test_create_group_supervisor_hashes_password_assigns_groups_and_keeps_django_flags_false(self):
        self.client.force_login(self.general)
        response = self.client.post(
            self.supervisor_add_url,
            {
                "full_name": "مشرف جديد",
                "username": "new_group_supervisor",
                "national_id": "",
                "role": Role.GROUP_SUPERVISOR,
                "environments": [str(self.group_a.pk), str(self.group_b.pk)],
                "is_active": "on",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                # These are never bindable fields, even when sent by hand.
                "is_staff": "on",
                "is_superuser": "on",
                "must_set_password": "on",
            },
        )
        self.assert_successful_post(response)
        supervisor = User.objects.get(username="new_group_supervisor")
        self.assertEqual(supervisor.full_name, "مشرف جديد")
        self.assertIsNone(supervisor.national_id)
        self.assertEqual(supervisor.role, Role.GROUP_SUPERVISOR)
        self.assertTrue(supervisor.is_active)
        self.assertTrue(supervisor.check_password("StrongPass123!"))
        self.assertNotEqual(supervisor.password, "StrongPass123!")
        self.assertFalse(supervisor.is_staff)
        self.assertFalse(supervisor.is_superuser)
        self.assertFalse(supervisor.must_set_password)
        self.assertEqual(set(supervisor.group_set.all()), {self.group_a, self.group_b})

    def test_create_group_supervisor_without_environment_is_valid(self):
        self.client.force_login(self.general)
        response = self.client.post(
            self.supervisor_add_url,
            {
                "full_name": "مشرف بلا بيئة",
                "username": "group_without_environment",
                "national_id": "",
                "role": Role.GROUP_SUPERVISOR,
                "environments": [],
                "is_active": "on",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assert_successful_post(response)
        supervisor = User.objects.get(username="group_without_environment")
        self.assertEqual(supervisor.role, Role.GROUP_SUPERVISOR)
        self.assertFalse(supervisor.group_set.exists())

    def test_create_general_supervisor_rejects_any_environment(self):
        self.client.force_login(self.root)
        valid = self.client.post(
            self.supervisor_add_url,
            {
                "full_name": "مشرف عام جديد",
                "username": "valid_general_no_group",
                "national_id": "",
                "role": Role.GENERAL_SUPERVISOR,
                "environments": [],
                "is_active": "on",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assert_successful_post(valid)
        general = User.objects.get(username="valid_general_no_group")
        self.assertEqual(general.role, Role.GENERAL_SUPERVISOR)
        self.assertFalse(general.group_set.exists())

        response = self.client.post(
            self.supervisor_add_url,
            {
                "full_name": "مشرف عام غير صالح",
                "username": "invalid_general_environment",
                "national_id": "",
                "role": Role.GENERAL_SUPERVISOR,
                "environments": [str(self.group_a.pk)],
                "is_active": "on",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="invalid_general_environment").exists())

    def test_create_rejects_missing_weak_or_mismatched_passwords_and_bad_usernames(self):
        self.client.force_login(self.general)
        invalid_cases = (
            {"username": "", "password1": "StrongPass123!", "password2": "StrongPass123!"},
            {"username": "invalid username!", "password1": "StrongPass123!", "password2": "StrongPass123!"},
            {"username": "x" * 31, "password1": "StrongPass123!", "password2": "StrongPass123!"},
            {"username": "no-password", "password1": "", "password2": ""},
            {"username": "mismatched-password", "password1": "StrongPass123!", "password2": "OtherPass123!"},
            {"username": "weak-password", "password1": "weak", "password2": "weak"},
        )
        for changes in invalid_cases:
            username = changes["username"]
            with self.subTest(username=username):
                payload = {
                    "full_name": "مشرف غير صالح",
                    "username": "temporary-username",
                    "national_id": "",
                    "role": Role.GROUP_SUPERVISOR,
                    "environments": [],
                    "is_active": "on",
                    "password1": "StrongPass123!",
                    "password2": "StrongPass123!",
                }
                payload.update(changes)
                response = self.client.post(self.supervisor_add_url, payload)
                self.assertEqual(response.status_code, 200)
                if username:
                    self.assertFalse(User.objects.filter(username=username).exists())

    def test_supervisor_username_and_identity_collisions_reject_without_partial_create(self):
        self.client.force_login(self.general)
        cases = (
            {
                "username": self.group_supervisor.username,
                "national_id": "",
            },
            {
                # A username cannot impersonate an existing national ID.
                "username": self.identity_owner.national_id,
                "national_id": "",
            },
            {
                "username": "new-duplicate-national-id",
                "national_id": self.participant_user.national_id,
            },
        )
        for fields in cases:
            with self.subTest(fields=fields):
                response = self.client.post(
                    self.supervisor_add_url,
                    {
                        "full_name": "يجب عدم إنشائه",
                        "role": Role.GROUP_SUPERVISOR,
                        "environments": [str(self.group_a.pk)],
                        "is_active": "on",
                        "password1": "StrongPass123!",
                        "password2": "StrongPass123!",
                        **fields,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(
                    User.objects.filter(full_name="يجب عدم إنشائه").exists()
                )

    def test_supervisor_edit_keeps_optional_password_and_is_atomic_when_identity_conflicts(self):
        original_hash = self.group_supervisor.password
        original_groups = set(self.group_supervisor.group_set.all())
        self.client.force_login(self.general)

        # No submitted replacement password preserves the stored hash.
        success = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(
                self.group_supervisor,
                full_name="اسم المشرف الجديد",
                environments=[str(self.group_a.pk), str(self.group_b.pk)],
            ),
        )
        self.assert_successful_post(success)
        self.group_supervisor.refresh_from_db()
        self.assertEqual(self.group_supervisor.password, original_hash)
        self.assertTrue(self.group_supervisor.check_password(self.password))
        self.assertEqual(
            set(self.group_supervisor.group_set.all()), {self.group_a, self.group_b}
        )

        # A later cross-identity collision must not partially change the name
        # or relationship set that was valid before the failed submission.
        failed = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(
                self.group_supervisor,
                full_name="اسم لا يجب حفظه",
                username=self.identity_owner.national_id,
                environments=[str(self.group_a.pk)],
            ),
        )
        self.assertEqual(failed.status_code, 200)
        self.group_supervisor.refresh_from_db()
        self.assertEqual(self.group_supervisor.full_name, "اسم المشرف الجديد")
        self.assertEqual(
            set(self.group_supervisor.group_set.all()), {self.group_a, self.group_b}
        )
        self.assertNotEqual(original_groups, set(self.group_supervisor.group_set.all()))

    def test_role_environment_and_inactive_changes_revoke_review_permission_without_autogrant(self):
        grant = TaskReviewPermission.objects.create(
            supervisor=self.group_supervisor, enabled=True, granted_by=self.general
        )
        self.client.force_login(self.general)

        changed_environment = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(
                self.group_supervisor, environments=[str(self.group_b.pk)]
            ),
        )
        self.assert_successful_post(changed_environment)
        grant.refresh_from_db()
        self.assertFalse(grant.enabled)

        grant.enabled = True
        grant.save(update_fields=["enabled"])
        changed_role = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(
                self.group_supervisor,
                role=Role.GENERAL_SUPERVISOR,
                environments=[],
            ),
        )
        self.assert_successful_post(changed_role)
        grant.refresh_from_db()
        self.assertFalse(grant.enabled)

        # Restoring GROUP plus an environment does not restore delegation.
        restored_group = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(
                self.group_supervisor,
                role=Role.GROUP_SUPERVISOR,
                environments=[str(self.group_a.pk)],
            ),
        )
        self.assert_successful_post(restored_group)
        grant.refresh_from_db()
        self.assertFalse(grant.enabled)

        grant.enabled = True
        grant.save(update_fields=["enabled"])
        deactivated = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(self.group_supervisor, is_active=""),
        )
        self.assert_successful_post(deactivated)
        self.group_supervisor.refresh_from_db()
        grant.refresh_from_db()
        self.assertFalse(self.group_supervisor.is_active)
        self.assertFalse(grant.enabled)

        # Reactivation alone must not silently re-grant review authority.
        reactivated = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(self.group_supervisor, is_active="on"),
        )
        self.assert_successful_post(reactivated)
        grant.refresh_from_db()
        self.assertFalse(grant.enabled)

    def test_self_password_change_requires_current_password_and_keeps_session(self):
        self.client.force_login(self.general)
        url = self.supervisor_edit_url(self.general)
        without_current = self.client.post(
            url,
            self.supervisor_payload(
                self.general,
                password1=self.changed_password,
                password2=self.changed_password,
                current_password="",
            ),
        )
        self.assertEqual(without_current.status_code, 200)
        self.general.refresh_from_db()
        self.assertTrue(self.general.check_password(self.password))

        wrong_current = self.client.post(
            url,
            self.supervisor_payload(
                self.general,
                password1=self.changed_password,
                password2=self.changed_password,
                current_password="incorrect-current-password",
            ),
        )
        self.assertEqual(wrong_current.status_code, 200)
        self.general.refresh_from_db()
        self.assertTrue(self.general.check_password(self.password))

        changed = self.client.post(
            url,
            self.supervisor_payload(
                self.general,
                password1=self.changed_password,
                password2=self.changed_password,
                current_password=self.password,
            ),
        )
        self.assert_successful_post(changed)
        self.general.refresh_from_db()
        self.assertTrue(self.general.check_password(self.changed_password))
        # update_session_auth_hash() must keep the currently authenticated
        # browser able to reach the protected management list.
        self.assertEqual(self.client.get(self.supervisor_list_url).status_code, 200)

    def test_self_role_deactivation_and_delete_are_denied(self):
        self.client.force_login(self.general)
        edit = self.client.post(
            self.supervisor_edit_url(self.general),
            self.supervisor_payload(
                self.general,
                role=Role.GROUP_SUPERVISOR,
                environments=[str(self.group_a.pk)],
                is_active="",
            ),
        )
        self.assertEqual(edit.status_code, 200)
        self.general.refresh_from_db()
        self.assertEqual(self.general.role, Role.GENERAL_SUPERVISOR)
        self.assertTrue(self.general.is_active)

        deletion = self.client.post(
            self.supervisor_delete_url(self.general), {"confirm": "yes"}
        )
        self.assertIn(deletion.status_code, (400, 403))
        self.general.refresh_from_db()
        self.assertTrue(self.general.is_active)

    def test_invalid_superadmin_role_errors_but_crafted_django_flags_are_ignored(self):
        self.client.force_login(self.general)
        safe = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(
                self.group_supervisor,
                full_name="مشرف مع حقول متطفلة",
                is_staff="on",
                is_superuser="on",
                must_set_password="on",
            ),
        )
        self.assert_successful_post(safe)
        self.group_supervisor.refresh_from_db()
        self.assertEqual(self.group_supervisor.full_name, "مشرف مع حقول متطفلة")
        self.assertFalse(self.group_supervisor.is_staff)
        self.assertFalse(self.group_supervisor.is_superuser)
        self.assertFalse(self.group_supervisor.must_set_password)

        invalid_role = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(
                self.group_supervisor,
                full_name="لا يحفظ مع ترقية النظام",
                role=Role.SUPERADMIN,
                is_staff="on",
                is_superuser="on",
            ),
        )
        self.assertEqual(invalid_role.status_code, 200)
        self.group_supervisor.refresh_from_db()
        self.assertEqual(self.group_supervisor.role, Role.GROUP_SUPERVISOR)
        self.assertEqual(self.group_supervisor.full_name, "مشرف مع حقول متطفلة")
        self.assertFalse(self.group_supervisor.is_staff)
        self.assertFalse(self.group_supervisor.is_superuser)

    def test_delete_get_is_read_only_and_confirmed_post_soft_deletes_with_a_safe_audit_entry(self):
        TaskReviewPermission.objects.create(
            supervisor=self.group_supervisor, enabled=True, granted_by=self.general
        )
        attendance = MeetingAttendance.objects.create(
            participant=self.participant,
            week_start_date=date(2026, 10, 1),
            attended=True,
            recorded_by=self.group_supervisor,
        )
        self.client.force_login(self.general)
        delete_url = self.supervisor_delete_url(self.group_supervisor)

        confirmation = self.client.get(delete_url)
        self.assertEqual(confirmation.status_code, 200)
        self.group_supervisor.refresh_from_db()
        self.assertTrue(self.group_supervisor.is_active)
        self.assertTrue(
            TaskReviewPermission.objects.get(supervisor=self.group_supervisor).enabled
        )

        no_confirmation = self.client.post(delete_url, {"confirm": "no"})
        self.assertIn(no_confirmation.status_code, (200, 400))
        self.group_supervisor.refresh_from_db()
        self.assertTrue(self.group_supervisor.is_active)

        deleted = self.client.post(delete_url, {"confirm": "yes"})
        self.assert_successful_post(deleted)
        self.group_supervisor.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=self.group_supervisor.pk).exists())
        self.assertFalse(self.group_supervisor.is_active)
        # Soft deletion preserves historical actor FKs and M2M assignments.
        attendance.refresh_from_db()
        self.assertEqual(attendance.recorded_by, self.group_supervisor)
        self.assertTrue(self.group_a.supervisor.filter(pk=self.group_supervisor.pk).exists())
        grant = TaskReviewPermission.objects.get(supervisor=self.group_supervisor)
        self.assertFalse(grant.enabled)

        user_content_type = ContentType.objects.get_for_model(User)
        audit = LogEntry.objects.filter(
            content_type=user_content_type,
            object_id=str(self.group_supervisor.pk),
            action_flag=CHANGE,
        ).latest("action_time")
        self.assertNotIn("password", audit.change_message.lower())
        self.assertNotIn(self.group_supervisor.password, audit.change_message)

    def test_inactive_supervisor_filter_and_reactivation_keep_review_permission_revoked(self):
        grant = TaskReviewPermission.objects.create(
            supervisor=self.group_supervisor, enabled=True, granted_by=self.general
        )
        self.group_supervisor.is_active = False
        self.group_supervisor.save(update_fields=["is_active"])
        grant.enabled = False
        grant.save(update_fields=["enabled"])

        self.client.force_login(self.general)
        inactive = self.client.get(self.supervisor_list_url, {"status": "inactive"})
        self.assertEqual(inactive.status_code, 200)
        self.assertContains(inactive, self.group_supervisor.full_name)

        reactivated = self.client.post(
            self.supervisor_edit_url(self.group_supervisor),
            self.supervisor_payload(self.group_supervisor, is_active="on"),
        )
        self.assert_successful_post(reactivated)
        self.group_supervisor.refresh_from_db()
        grant.refresh_from_db()
        self.assertTrue(self.group_supervisor.is_active)
        self.assertFalse(grant.enabled)

    def test_management_post_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.general)
        before = User.objects.count()
        response = csrf_client.post(
            self.supervisor_add_url,
            {
                "full_name": "مشرف CSRF",
                "username": "csrf-supervisor",
                "national_id": "",
                "role": Role.GROUP_SUPERVISOR,
                "environments": [],
                "is_active": "on",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), before)
