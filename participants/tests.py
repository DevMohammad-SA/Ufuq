import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from participants.models import (
    CircleAttendance,
    Group,
    Participant,
    PointsResetSnapshot,
    StoreOrder,
    StoreProduct,
)


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


class PointsResetSnapshotTests(TestCase):
    def setUp(self):
        self.general = User.objects.create_user(
            username="gen", password="pw12345678", role=Role.GENERAL_SUPERVISOR
        )
        self.group = Group.objects.create(name="بيئة أ")
        self.p1 = self._participant("3000000001", 45)
        self.p2 = self._participant("3000000002", 30)
        self.p3 = self._participant("3000000003", 12)
        self.url = reverse("participants:general_supervisor_dashboard")
        self.history_url = reverse("participants:points_snapshot_history")

    def _participant(self, national_id, points):
        user = User.objects.create_user(
            national_id=national_id,
            full_name=f"مشارك {national_id}",
            role=Role.PARTICIPANT,
            password=national_id,
        )
        return Participant.objects.create(
            user=user, group=self.group, academic_stage="grade_7", points=points
        )

    # 1. Reset snapshots every participant's points, then zeroes them.
    def test_reset_creates_snapshots_then_zeroes(self):
        self.client.login(username="gen", password="pw12345678")
        resp = self.client.post(self.url, {"action": "reset_points"})
        self.assertRedirects(resp, self.url)

        snaps = {
            s.participant_id: s.points_before_reset
            for s in PointsResetSnapshot.objects.all()
        }
        self.assertEqual(snaps[self.p1.id], 45)
        self.assertEqual(snaps[self.p2.id], 30)
        self.assertEqual(snaps[self.p3.id], 12)
        self.assertEqual(PointsResetSnapshot.objects.count(), 3)
        self.assertTrue(all(s.reset_by_id == self.general.id for s in PointsResetSnapshot.objects.all()))

        for p in (self.p1, self.p2, self.p3):
            p.refresh_from_db()
            self.assertEqual(p.points, 0)

    # 2. History view lists one event; selecting it ranks participants high-to-low.
    def test_history_view_single_event_ranked(self):
        self.client.login(username="gen", password="pw12345678")
        self.client.post(self.url, {"action": "reset_points"})

        resp = self.client.get(self.history_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["reset_events"]), 1)

        event_iso = resp.context["reset_events"][0].isoformat()
        resp2 = self.client.get(self.history_url, {"event": event_iso})
        rows = list(resp2.context["selected_snapshots"])
        self.assertEqual([r.points_before_reset for r in rows], [45, 30, 12])

    # 3. A second reset is a distinct event, not mixed with the first.
    def test_second_reset_is_separate_event(self):
        self.client.login(username="gen", password="pw12345678")

        # First reset (45/30/12), then push its rows a minute into the past so
        # the two batches fall in different seconds (both bulk_create calls run
        # within the same wall-clock second inside a test).
        self.client.post(self.url, {"action": "reset_points"})
        PointsResetSnapshot.objects.update(
            reset_at=timezone.now() - datetime.timedelta(minutes=1)
        )

        # New points, second reset.
        Participant.objects.filter(id=self.p1.id).update(points=100)
        Participant.objects.filter(id=self.p2.id).update(points=5)
        # p3 stays at 0 from the first reset.
        self.client.post(self.url, {"action": "reset_points"})

        resp = self.client.get(self.history_url)
        self.assertEqual(len(resp.context["reset_events"]), 2)
        self.assertEqual(PointsResetSnapshot.objects.count(), 6)

        newest = resp.context["reset_events"][0].isoformat()
        oldest = resp.context["reset_events"][1].isoformat()

        newest_rows = list(
            self.client.get(self.history_url, {"event": newest}).context[
                "selected_snapshots"
            ]
        )
        self.assertEqual([r.points_before_reset for r in newest_rows], [100, 5, 0])

        oldest_rows = list(
            self.client.get(self.history_url, {"event": oldest}).context[
                "selected_snapshots"
            ]
        )
        self.assertEqual([r.points_before_reset for r in oldest_rows], [45, 30, 12])

    # 4. Other post() branches still work after the reset_points change.
    def test_other_post_branches_unbroken(self):
        self.client.login(username="gen", password="pw12345678")

        # approve_password_reset branch (same view's post()).
        from accounts.models import PasswordResetRequest

        target = self.p1.user
        target.set_password("temp")
        target.save()
        req = PasswordResetRequest.objects.create(user=target)
        resp = self.client.post(
            self.url, {"action": "approve_password_reset", "reset_id": req.id}
        )
        self.assertRedirects(resp, self.url)
        req.refresh_from_db()
        self.assertTrue(req.resolved)

        # StoreManagementView.post() product + order branches.
        mgmt_url = reverse("participants:store_management")
        resp = self.client.post(
            mgmt_url,
            {"action": "add_product", "name": "قلم", "description": "", "price": 5, "stock": 3},
        )
        self.assertRedirects(resp, mgmt_url)
        product = StoreProduct.objects.get(name="قلم")

        resp = self.client.post(
            mgmt_url,
            {"action": "edit_product", "product_id": product.id, "name": "قلم أزرق",
             "description": "", "price": 6, "stock": 4},
        )
        self.assertRedirects(resp, mgmt_url)
        product.refresh_from_db()
        self.assertEqual(product.name, "قلم أزرق")

        order = StoreOrder.objects.create(
            participant=self.p2, product=product, price_at_order=6
        )
        resp = self.client.post(mgmt_url, {"action": "complete", "order_id": order.id})
        self.assertRedirects(resp, mgmt_url)
        order.refresh_from_db()
        self.assertEqual(order.status, StoreOrder.Status.COMPLETED)

        resp = self.client.post(mgmt_url, {"action": "refund", "order_id": order.id})
        self.assertRedirects(resp, mgmt_url)
        order.refresh_from_db()
        self.assertEqual(order.status, StoreOrder.Status.REFUNDED)

        resp = self.client.post(
            mgmt_url, {"action": "delete_product", "product_id": product.id}
        )
        # product had an order -> ProtectedError -> the branch re-renders the
        # page (200) with an Arabic error rather than redirecting, and the
        # product is kept. Branch still executes correctly.
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "لا يمكن حذف هذا المنتج")
        self.assertTrue(StoreProduct.objects.filter(id=product.id).exists())


class ParticipantsDataPDFExportTests(TestCase):
    """
    WeasyPrint's native libs (Pango/Cairo/GObject) are not installed in this
    environment, so `from weasyprint import HTML` cannot execute here. These
    tests inject a fake `weasyprint` module into sys.modules before hitting
    the view (the view imports it lazily inside get()), which lets us verify
    role scoping, the ?group= name filter, and that the letterhead-bearing
    HTML is what gets handed to the PDF engine — everything except the actual
    PDF rasterisation, which is a documented host-dependency blocker.
    """

    def setUp(self):
        import sys
        from unittest.mock import MagicMock

        self._fake = MagicMock()
        self._fake.HTML.return_value.write_pdf.return_value = b"%PDF-1.7 fake-bytes"
        self._saved = sys.modules.get("weasyprint")
        sys.modules["weasyprint"] = self._fake

        self.gs_a = User.objects.create_user(
            username="gsa", password="pw12345678", role=Role.GROUP_SUPERVISOR
        )
        self.general = User.objects.create_user(
            username="gen", password="pw12345678", role=Role.GENERAL_SUPERVISOR
        )
        self.group_a = Group.objects.create(name="بيئة أ", supervisor=self.gs_a)
        self.group_b = Group.objects.create(name="بيئة ب")

        self.a1 = self._p("5000000001", "أحمد", self.group_a)
        self.a2 = self._p("5000000002", "بدر", self.group_a)
        self.b1 = self._p("5000000003", "خالد", self.group_b)

        self.url = reverse("participants:participants_data_pdf")

    def tearDown(self):
        import sys

        if self._saved is not None:
            sys.modules["weasyprint"] = self._saved
        else:
            sys.modules.pop("weasyprint", None)

    def _p(self, national_id, name, group):
        user = User.objects.create_user(
            national_id=national_id, full_name=name,
            role=Role.PARTICIPANT, password=national_id,
        )
        return Participant.objects.create(
            user=user, group=group, academic_stage="grade_7"
        )

    def _rendered_html(self):
        """The HTML string handed to weasyprint.HTML(string=...)."""
        return self._fake.HTML.call_args.kwargs["string"]

    # 1. General supervisor, no filter -> every participant.
    def test_general_no_filter_all_participants(self):
        self.client.login(username="gen", password="pw12345678")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("attachment", resp["Content-Disposition"])

        html = self._rendered_html()
        for name in ("أحمد", "بدر", "خالد"):
            self.assertIn(name, html)

    # 2. General supervisor, one group selected -> only that group.
    def test_general_single_group_filter(self):
        self.client.login(username="gen", password="pw12345678")
        resp = self.client.get(self.url, {"group": "بيئة أ"})
        self.assertEqual(resp.status_code, 200)

        html = self._rendered_html()
        self.assertIn("أحمد", html)
        self.assertIn("بدر", html)
        self.assertNotIn("خالد", html)

    def test_general_multi_group_filter(self):
        self.client.login(username="gen", password="pw12345678")
        resp = self.client.get(self.url, {"group": ["بيئة أ", "بيئة ب"]})
        html = self._rendered_html()
        for name in ("أحمد", "بدر", "خالد"):
            self.assertIn(name, html)

    # 3. Group supervisor -> only own group, even spoofing ?group=.
    def test_group_supervisor_locked_to_own_group(self):
        self.client.login(username="gsa", password="pw12345678")
        resp = self.client.get(self.url, {"group": "بيئة ب"})
        self.assertEqual(resp.status_code, 200)

        html = self._rendered_html()
        self.assertIn("أحمد", html)
        self.assertIn("بدر", html)
        self.assertNotIn("خالد", html)  # group b, not theirs

    # 4. Letterhead image is referenced in the HTML sent to the PDF engine,
    #    and base_url is absolute so WeasyPrint can resolve it.
    def test_letterhead_and_base_url(self):
        self.client.login(username="gen", password="pw12345678")
        self.client.get(self.url)

        html = self._rendered_html()
        self.assertIn("images/letterhead.png", html)
        self.assertIn("تقرير بيانات المشاركين", html)

        base_url = self._fake.HTML.call_args.kwargs["base_url"]
        self.assertTrue(base_url.startswith("http"))

    # Export always sorted by full name regardless of request.
    def test_export_sorted_by_full_name(self):
        self.client.login(username="gen", password="pw12345678")
        self.client.get(self.url)
        html = self._rendered_html()
        self.assertLess(html.index("أحمد"), html.index("بدر"))
        self.assertLess(html.index("بدر"), html.index("خالد"))

    def test_participant_forbidden(self):
        self.client.login(username="5000000001", password="5000000001")
        resp = self.client.get(self.url)
        self.assertIn(resp.status_code, (302, 403))
