"""Student-detail regressions. Run with config.test_settings, never a managed DB."""
import datetime

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import (
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

UTC = datetime.timezone.utc


@override_settings(TIME_ZONE="Asia/Riyadh")
class StudentDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_supervisor = User.objects.create_user(
            username="detail-group", password="test-only", role=Role.GROUP_SUPERVISOR,
        )
        cls.other_supervisor = User.objects.create_user(
            username="detail-other", password="test-only", role=Role.GROUP_SUPERVISOR,
        )
        cls.general = User.objects.create_user(
            username="detail-general", password="test-only", role=Role.GENERAL_SUPERVISOR,
        )
        cls.system = User.objects.create_user(
            username="detail-system", password="test-only", role=Role.SUPERADMIN,
        )
        cls.group = Group.objects.create(name="بيئة التفاصيل")
        cls.other_group = Group.objects.create(name="بيئة خارج النطاق")
        cls.group.supervisor.add(cls.group_supervisor)
        cls.other_group.supervisor.add(cls.other_supervisor)
        cls.student = cls.make_student("7100000001", "الطالب المفصل", cls.group, points=77, miles=770, purchase_points=70)
        cls.other_student = cls.make_student("7100000002", "الطالب الخارجي", cls.other_group)
        cls.quiet_student = cls.make_student("7100000003", "طالب بلا نشاط", cls.group)

        CircleAttendance.objects.create(participant=cls.student, date=datetime.date(2026, 1, 2), attended=True, achieved=True)
        MeetingAttendance.objects.create(participant=cls.student, week_start_date=datetime.date(2026, 1, 3), attended=True, is_early=True, recorded_by=cls.group_supervisor)
        WeeklyActivityAttendance.objects.create(participant=cls.student, date=datetime.date(2026, 1, 4), attended=True, recorded_by=cls.group_supervisor)

        task = WeeklyTask.objects.create(
            title="مهمة التفاصيل", description="وصف المهمة", due_date=datetime.date(2026, 1, 10),
            allowed_formats="text,pdf",
        )
        cls.submission = TaskSubmission.objects.create(
            task=task, participant=cls.student, text_content="نص التسليم", status=TaskSubmission.Status.REJECTED,
            file=ContentFile(b"proof", name="proof.txt"), is_featured=True,
            rejection_reason="سبب الرفض", reviewed_by=cls.general,
            reviewed_at=datetime.datetime(2026, 1, 5, 9, 0, tzinfo=UTC), reopened_for_resubmission=True,
        )
        TaskSubmission.objects.filter(pk=cls.submission.pk).update(
            submitted_at=datetime.datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
        )

        cls.ledger = PointsLedgerEntry.objects.create(
            participant=cls.student, points_delta=-3, source=PointsLedgerEntry.Source.EXTRA,
            description="خصم اختبار", granted_by=cls.general,
        )
        PointsLedgerEntry.objects.filter(pk=cls.ledger.pk).update(
            created_at=datetime.datetime(2026, 1, 5, 10, 0, tzinfo=UTC),
        )
        product = StoreProduct.objects.create(name="منتج التفاصيل", description="", price=12, stock=4)
        cls.order = StoreOrder.objects.create(
            participant=cls.student, product=product, price_at_order=12, status=StoreOrder.Status.REFUNDED,
            completed_at=datetime.datetime(2026, 1, 6, 9, 0, tzinfo=UTC),
            refunded_at=datetime.datetime(2026, 1, 7, 9, 0, tzinfo=UTC),
        )
        StoreOrder.objects.filter(pk=cls.order.pk).update(
            ordered_at=datetime.datetime(2026, 1, 6, 8, 0, tzinfo=UTC),
        )
        cls.snapshot = PointsResetSnapshot.objects.create(
            participant=cls.student, points_before_reset=70, reset_by=cls.general,
        )
        PointsResetSnapshot.objects.filter(pk=cls.snapshot.pk).update(
            reset_at=datetime.datetime(2026, 1, 8, 8, 0, tzinfo=UTC),
        )
        cls.note = MeetingNote.objects.create(
            participant=cls.student, meeting_date=datetime.date(2026, 1, 9), text="ملاحظة خاصة جداً",
            created_by=cls.group_supervisor, updated_by=cls.general,
        )

    @classmethod
    def make_student(cls, national_id, full_name, group, **kwargs):
        user = User.objects.create_user(
            national_id=national_id, full_name=full_name, role=Role.PARTICIPANT,
            password="test-only",
        )
        return Participant.objects.create(user=user, group=group, academic_stage="grade_7", **kwargs)

    def detail_url(self, participant=None):
        return reverse("participants:student_detail", kwargs={"pk": (participant or self.student).pk})

    def get_detail(self, user, participant=None, **params):
        self.client.force_login(user)
        return self.client.get(self.detail_url(participant), params)

    def test_all_three_reporting_roles_obey_their_scopes(self):
        for user in (self.group_supervisor, self.general, self.system):
            with self.subTest(role=user.role):
                response = self.get_detail(user)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "الطالب المفصل")
                self.assertIn("private", response["Cache-Control"])
                self.assertIn("no-store", response["Cache-Control"])

        self.assertEqual(self.get_detail(self.group_supervisor, self.other_student).status_code, 404)
        self.assertEqual(self.get_detail(self.general, self.other_student).status_code, 200)
        self.assertEqual(self.get_detail(self.system, self.other_student).status_code, 200)
        self.assertEqual(self.get_detail(self.other_supervisor, self.student).status_code, 404)

    def test_anonymous_redirects_and_participant_is_forbidden(self):
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login_supervisor"), response["Location"])
        self.client.force_login(self.student.user)
        self.assertEqual(self.client.get(self.detail_url()).status_code, 403)

    def test_quiet_student_keeps_identity_and_zero_summary(self):
        response = self.get_detail(self.general, self.quiet_student)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "طالب بلا نشاط")
        self.assertEqual(response.context["summary"]["attended"], 0)
        self.assertEqual(response.context["summary"]["achieved"], 0)
        self.assertEqual(response.context["summary"]["submitted"], 0)
        self.assertEqual(response.context["summary"]["ledger_total"], 0)
        self.assertTrue(all(not table["rows"] for table in response.context["tables"]))

    def test_invalid_date_window_shows_error_without_history(self):
        response = self.get_detail(self.general, start_date="2026-02-04", end_date="2026-02-01")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["filters_valid"])
        self.assertEqual(response.context["tables"], [])
        self.assertIsNone(response.context["summary"])
        self.assertContains(response, "يجب أن يكون تاريخ البداية")
        self.assertNotContains(response, "مهمة التفاصيل")

    def test_all_history_models_render_with_requested_details(self):
        response = self.get_detail(self.general)
        self.assertEqual(response.status_code, 200)
        for text in (
            "حضور الحلقة القرآنية", "حضور اللقاءات", "فعاليات الأسبوع", "مهمة التفاصيل",
            "وصف المهمة", "نص التسليم", "proof.txt", "سبب الرفض", "خصم اختبار",
            "منتج التفاصيل", "سجل لقطات تصفير النقاط", "ملاحظة خاصة جداً",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertEqual(response.context["summary"]["rejected"], 1)
        self.assertEqual(response.context["summary"]["ledger_total"], -3)
        self.assertEqual(response.context["summary"]["current_balance"], 77)

    def test_each_history_uses_its_own_twenty_row_pagination(self):
        for index in range(20):
            CircleAttendance.objects.create(
                participant=self.student, date=datetime.date(2026, 2, 1) + datetime.timedelta(days=index),
                attended=True,
            )
        response = self.get_detail(self.general, circle_page="2", start_date="2026-01-01", end_date="2026-12-31")
        circle_page = response.context["circle_page"]
        self.assertEqual(circle_page.paginator.per_page, 20)
        self.assertEqual(circle_page.number, 2)
        self.assertEqual(len(circle_page.object_list), 1)
        for page_name in (
            "meeting_page", "activity_page", "task_page", "ledger_page",
            "order_page", "snapshot_page", "note_page",
        ):
            with self.subTest(page_name=page_name):
                self.assertEqual(response.context[page_name].paginator.per_page, 20)
        circle_table = next(table for table in response.context["tables"] if table["anchor"] == "circle")
        self.assertIn("start_date=2026-01-01", circle_table["query"])
        self.assertNotIn("circle_page=", circle_table["query"])

    def test_meeting_notes_are_available_only_to_authorized_scoped_staff(self):
        general = self.get_detail(self.general)
        group = self.get_detail(self.group_supervisor)
        system = self.get_detail(self.system)
        self.assertContains(general, "ملاحظة خاصة جداً")
        self.assertContains(group, "ملاحظة خاصة جداً")
        self.assertNotContains(system, "ملاحظة خاصة جداً")
        self.assertIsNone(system.context.get("note_page"))
        self.client.force_login(self.student.user)
        participant = self.client.get(self.detail_url())
        self.assertEqual(participant.status_code, 403)
        self.assertNotContains(participant, "ملاحظة خاصة جداً", status_code=403)

    def test_snapshots_are_limited_by_the_selected_date_window(self):
        outside = PointsResetSnapshot.objects.create(
            participant=self.student, points_before_reset=999, reset_by=self.general,
        )
        PointsResetSnapshot.objects.filter(pk=outside.pk).update(
            reset_at=datetime.datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
        )
        response = self.get_detail(self.general, start_date="2026-01-08", end_date="2026-01-08")
        self.assertContains(response, "70")
        self.assertEqual(
            [record.points_before_reset for record in response.context["snapshot_page"]],
            [70],
        )
        self.assertEqual(response.context["snapshot_page"].paginator.count, 1)

    def test_timestamp_window_uses_local_calendar_day(self):
        boundary = PointsLedgerEntry.objects.create(
            participant=self.student, points_delta=4, source=PointsLedgerEntry.Source.EXTRA,
            description="بعد منتصف الرياض", granted_by=self.general,
        )
        # 21:30 UTC is 00:30 on the next calendar day in Asia/Riyadh.
        PointsLedgerEntry.objects.filter(pk=boundary.pk).update(
            created_at=datetime.datetime(2026, 5, 1, 21, 30, tzinfo=UTC),
        )
        with timezone.override("Asia/Riyadh"):
            response = self.get_detail(self.general, start_date="2026-05-02", end_date="2026-05-02")
        self.assertContains(response, "بعد منتصف الرياض")
        self.assertEqual(response.context["ledger_page"].paginator.count, 1)

    def test_detail_is_read_only(self):
        self.client.force_login(self.general)
        self.assertEqual(self.client.post(self.detail_url()).status_code, 405)
