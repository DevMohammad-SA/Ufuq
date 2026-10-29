"""Regression coverage for safe participant account archiving and restore."""

from datetime import date

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Role, User
from participants.models import (
    CircleAttendance,
    Group,
    MeetingAttendance,
    MeetingNote,
    Participant,
    PointsLedgerEntry,
    PointsResetSnapshot,
    StoreOrder,
    StoreProduct,
    TaskSubmission,
    WeeklyActivityAttendance,
    WeeklyTask,
)
from participants.participant_lifecycle import (
    filter_participant_status,
    participant_status,
)


class ParticipantLifecycleBase(TestCase):
    password = "StudentPass123!"

    def setUp(self):
        self.general = User.objects.create_user(
            username="lifecycle-general",
            password="GeneralPass123!",
            role=Role.GENERAL_SUPERVISOR,
            full_name="المشرف العام للاختبار",
        )
        self.system = User.objects.create_user(
            username="lifecycle-system",
            password="SystemPass123!",
            role=Role.SUPERADMIN,
            full_name="مشرف النظام للاختبار",
        )
        self.group_supervisor = User.objects.create_user(
            username="lifecycle-group",
            password="GroupPass123!",
            role=Role.GROUP_SUPERVISOR,
            full_name="مشرف البيئة للاختبار",
        )
        self.group = Group.objects.create(name="بيئة دورة الحياة")
        self.group.supervisor.add(self.group_supervisor)
        self.student_user = User.objects.create_user(
            national_id="8000000001",
            password=self.password,
            role=Role.PARTICIPANT,
            full_name="المشارك المؤرشف",
            must_set_password=False,
        )
        self.participant = Participant.objects.create(
            user=self.student_user,
            group=self.group,
            academic_stage="grade_7",
            points=72,
            miles=720,
            purchase_points=34,
            phone="0500000000",
            guardian_phone="0511111111",
        )
        self.delete_url = reverse(
            "participants:participant_delete", kwargs={"pk": self.participant.pk}
        )
        self.restore_url = reverse(
            "participants:participant_restore", kwargs={"pk": self.participant.pk}
        )

    def archive(self, actor=None, url=None, **data):
        self.client.force_login(actor or self.general)
        return self.client.post(url or self.delete_url, {"confirm": "yes", **data})


class ParticipantLifecycleAccessTests(ParticipantLifecycleBase):
    def test_only_active_general_and_system_supervisors_can_open_confirmation_pages(self):
        login_url = reverse("accounts:login_supervisor")
        for url in (self.delete_url, self.restore_url):
            with self.subTest(url=url, actor="anonymous"):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(login_url, response["Location"])

        self.student_user.must_set_password = False
        self.student_user.save(update_fields=["must_set_password"])
        for actor in (self.group_supervisor, self.student_user):
            self.client.force_login(actor)
            for url in (self.delete_url, self.restore_url):
                with self.subTest(url=url, actor=actor.role):
                    self.assertEqual(self.client.get(url).status_code, 403)

        for actor in (self.general, self.system):
            self.client.force_login(actor)
            for url in (self.delete_url, self.restore_url):
                with self.subTest(url=url, actor=actor.role):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("private", response["Cache-Control"])
                    self.assertIn("no-store", response["Cache-Control"])

    def test_crafted_participant_records_cannot_archive_supervisor_or_admin_accounts(self):
        protected_rows = (
            User.objects.create_user(
                username="lifecycle-supervisor-target",
                password="SupervisorPass123!",
                role=Role.GROUP_SUPERVISOR,
                full_name="مشرف له صف مشارك",
            ),
            User.objects.create_user(
                username="lifecycle-staff-target",
                password="StaffPass123!",
                role=Role.PARTICIPANT,
                full_name="مشارك بامتياز أدمن",
                is_staff=True,
            ),
            User.objects.create_user(
                username="lifecycle-admin-target",
                password="AdminPass123!",
                role=Role.PARTICIPANT,
                full_name="مشارك بصلاحية أدمن عليا",
                is_superuser=True,
            ),
        )
        self.client.force_login(self.general)
        for user in protected_rows:
            participant = Participant.objects.create(
                user=user, group=self.group, academic_stage="grade_6"
            )
            url = reverse("participants:participant_delete", kwargs={"pk": participant.pk})
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(
                    self.client.post(url, {"confirm": "yes"}).status_code, 404
                )
                user.refresh_from_db()
                self.assertTrue(user.is_active)


class ParticipantLifecycleMutationTests(ParticipantLifecycleBase):
    def test_get_and_missing_confirmation_never_change_account_or_audit_history(self):
        self.client.force_login(self.general)
        user_content_type = ContentType.objects.get_for_model(User)
        before = LogEntry.objects.filter(
            content_type=user_content_type, object_id=str(self.student_user.pk)
        ).count()

        confirmation = self.client.get(self.delete_url)
        self.assertEqual(confirmation.status_code, 200)
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)

        missing = self.client.post(self.delete_url, {})
        self.assertEqual(missing.status_code, 400)
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)
        self.assertEqual(
            LogEntry.objects.filter(
                content_type=user_content_type, object_id=str(self.student_user.pk)
            ).count(),
            before,
        )

    def test_archive_and_restore_are_idempotent_and_audited_only_for_real_transitions(self):
        self.client.force_login(self.general)
        user_content_type = ContentType.objects.get_for_model(User)
        audit_qs = LogEntry.objects.filter(
            content_type=user_content_type,
            object_id=str(self.student_user.pk),
            action_flag=CHANGE,
        )
        before = audit_qs.count()

        archived = self.client.post(self.delete_url, {"confirm": "yes"})
        self.assertEqual(archived.status_code, 302)
        self.student_user.refresh_from_db()
        self.assertFalse(self.student_user.is_active)
        self.assertEqual(audit_qs.count(), before + 1)

        repeated_archive = self.client.post(self.delete_url, {"confirm": "yes"})
        self.assertEqual(repeated_archive.status_code, 302)
        self.student_user.refresh_from_db()
        self.assertFalse(self.student_user.is_active)
        self.assertEqual(audit_qs.count(), before + 1)

        restored = self.client.post(self.restore_url, {"confirm": "yes"})
        self.assertEqual(restored.status_code, 302)
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)
        self.assertEqual(audit_qs.count(), before + 2)

        repeated_restore = self.client.post(self.restore_url, {"confirm": "yes"})
        self.assertEqual(repeated_restore.status_code, 302)
        self.assertEqual(audit_qs.count(), before + 2)

    def test_archive_preserves_program_records_and_invalidates_an_existing_session(self):
        circle = CircleAttendance.objects.create(
            participant=self.participant, date=date(2026, 10, 1), attended=True
        )
        meeting = MeetingAttendance.objects.create(
            participant=self.participant,
            week_start_date=date(2026, 10, 2),
            attended=True,
            recorded_by=self.group_supervisor,
        )
        activity = WeeklyActivityAttendance.objects.create(
            participant=self.participant,
            date=date(2026, 10, 3),
            attended=True,
            recorded_by=self.group_supervisor,
        )
        task = WeeklyTask.objects.create(
            title="مهمة حفظ الأرشيف",
            description="لا تحذف",
            due_date=date(2026, 12, 1),
            allowed_formats="text",
        )
        submission = TaskSubmission.objects.create(
            task=task, participant=self.participant, text_content="تسليم محفوظ"
        )
        note = MeetingNote.objects.create(
            participant=self.participant,
            meeting_date=date(2026, 10, 4),
            text="ملاحظة محفوظة",
            created_by=self.group_supervisor,
            updated_by=self.general,
        )
        product = StoreProduct.objects.create(
            name="منتج محفوظ", description="", price=10, stock=3
        )
        order = StoreOrder.objects.create(
            participant=self.participant, product=product, price_at_order=10
        )
        snapshot = PointsResetSnapshot.objects.create(
            participant=self.participant, points_before_reset=72, reset_by=self.general
        )
        ledger = PointsLedgerEntry.objects.create(
            participant=self.participant,
            points_delta=5,
            source=PointsLedgerEntry.Source.EXTRA,
            description="حركة محفوظة",
            granted_by=self.general,
        )
        balances = (
            self.participant.points,
            self.participant.miles,
            self.participant.purchase_points,
        )

        student_client = Client()
        self.assertTrue(
            student_client.login(username=self.student_user.national_id, password=self.password)
        )
        self.client.force_login(self.general)
        self.assertEqual(
            self.client.post(self.delete_url, {"confirm": "yes"}).status_code, 302
        )

        self.student_user.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertFalse(self.student_user.is_active)
        self.assertTrue(User.objects.filter(pk=self.student_user.pk).exists())
        self.assertTrue(Participant.objects.filter(pk=self.participant.pk).exists())
        self.assertEqual(
            (self.participant.points, self.participant.miles, self.participant.purchase_points),
            balances,
        )
        for model, row in (
            (CircleAttendance, circle),
            (MeetingAttendance, meeting),
            (WeeklyActivityAttendance, activity),
            (TaskSubmission, submission),
            (MeetingNote, note),
            (StoreOrder, order),
            (PointsResetSnapshot, snapshot),
            (PointsLedgerEntry, ledger),
        ):
            with self.subTest(model=model.__name__):
                self.assertTrue(model.objects.filter(pk=row.pk).exists())
                row.refresh_from_db()
                self.assertEqual(row.participant_id, self.participant.pk)

        # The configured custom backend rechecks is_active for an established
        # session, so archiving takes effect on this participant's next request.
        dashboard = student_client.get(reverse("participants:dashboard"))
        self.assertEqual(dashboard.status_code, 302)

    def test_post_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.general)
        response = csrf_client.post(self.delete_url, {"confirm": "yes"})
        self.assertEqual(response.status_code, 403)
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)


class ParticipantStatusHelperTests(ParticipantLifecycleBase):
    def test_status_values_are_manager_only_and_filter_the_linked_user_state(self):
        archived_user = User.objects.create_user(
            national_id="8000000002",
            password=self.password,
            role=Role.PARTICIPANT,
            full_name="المشارك المؤرشف الآخر",
            is_active=False,
        )
        archived = Participant.objects.create(
            user=archived_user, group=self.group, academic_stage="grade_8"
        )
        factory = RequestFactory()

        request = factory.get("/participants/data/", {"status": "inactive"})
        request.user = self.general
        self.assertEqual(participant_status(request), "inactive")
        self.assertEqual(
            list(filter_participant_status(Participant.objects.order_by("pk"), request)),
            [archived],
        )

        request = factory.get("/participants/data/", {"status": "all"})
        request.user = self.group_supervisor
        self.assertEqual(participant_status(request), "active")
        self.assertEqual(
            list(filter_participant_status(Participant.objects.order_by("pk"), request)),
            [self.participant],
        )

        request = factory.get("/participants/data/", {"status": "not-a-status"})
        request.user = self.system
        self.assertEqual(participant_status(request), "active")
