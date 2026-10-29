from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from participants.models import Group, Participant, StoreOrder, StoreProduct
from participants.views import build_navbar, get_notification_counts


class StoreManagementPermissionsTests(TestCase):
    """Read-only store management coverage for group supervisors."""

    def setUp(self):
        self.url = reverse("participants:store_management")

        self.group_supervisor = User.objects.create_user(
            username="store-group-supervisor",
            password="pw12345678",
            role=Role.GROUP_SUPERVISOR,
            full_name="مشرف البيئتين",
        )
        self.shared_group_supervisor = User.objects.create_user(
            username="store-shared-supervisor",
            password="pw12345678",
            role=Role.GROUP_SUPERVISOR,
            full_name="مشرف البيئة المشترك",
        )
        self.no_group_supervisor = User.objects.create_user(
            username="store-orphan-supervisor",
            password="pw12345678",
            role=Role.GROUP_SUPERVISOR,
            full_name="مشرف بلا بيئة",
        )
        self.general_supervisor = User.objects.create_user(
            username="store-general-supervisor",
            password="pw12345678",
            role=Role.GENERAL_SUPERVISOR,
            full_name="المشرف العام",
        )
        self.superadmin = User.objects.create_user(
            username="store-superadmin",
            password="pw12345678",
            role=Role.SUPERADMIN,
            full_name="مشرف النظام",
        )

        self.group_alpha = Group.objects.create(name="بيئة المتجر ألف")
        self.group_beta = Group.objects.create(name="بيئة المتجر باء")
        self.group_gamma = Group.objects.create(name="بيئة المتجر جيم")
        self.group_alpha.supervisor.add(self.group_supervisor)
        # beta deliberately has two supervisors.  A group supervisor linked to
        # it must still see each order once, not once per supervisor relation.
        self.group_beta.supervisor.add(
            self.group_supervisor, self.shared_group_supervisor
        )
        self.group_gamma.supervisor.add(self.shared_group_supervisor)

        self.product_alpha = StoreProduct.objects.create(
            name="دفتر الاختبار", description="منتج ألف", price=12, stock=10
        )
        self.product_beta = StoreProduct.objects.create(
            name="قلم الاختبار", description="منتج باء", price=8, stock=20
        )

        # All three statuses are represented in one environment.  There are
        # also pending orders in the second assigned environment, another
        # supervisor's environment, and no environment at all.
        self.alpha_pending_participant = self._participant(
            "7100000001", "طالب ألف معلّق", self.group_alpha, 70, 17, 170
        )
        self.alpha_completed_participant = self._participant(
            "7100000002", "طالب ألف مكتمل", self.group_alpha, 71, 18, 180
        )
        self.alpha_refunded_participant = self._participant(
            "7100000003", "طالب ألف مسترجَع", self.group_alpha, 72, 19, 190
        )
        self.beta_pending_participant = self._participant(
            "7100000004", "طالب باء معلّق", self.group_beta, 80, 20, 200
        )
        self.gamma_pending_participant = self._participant(
            "7100000005", "طالب جيم خارج النطاق", self.group_gamma, 90, 21, 210
        )
        self.unassigned_pending_participant = self._participant(
            "7100000006", "طالب بلا بيئة", None, 100, 22, 220
        )

        now = timezone.now()
        self.alpha_pending = StoreOrder.objects.create(
            participant=self.alpha_pending_participant,
            product=self.product_alpha,
            price_at_order=self.product_alpha.price,
            status=StoreOrder.Status.PENDING,
        )
        self.alpha_completed = StoreOrder.objects.create(
            participant=self.alpha_completed_participant,
            product=self.product_alpha,
            price_at_order=self.product_alpha.price,
            status=StoreOrder.Status.COMPLETED,
            completed_at=now,
        )
        self.alpha_refunded = StoreOrder.objects.create(
            participant=self.alpha_refunded_participant,
            product=self.product_beta,
            price_at_order=self.product_beta.price,
            status=StoreOrder.Status.REFUNDED,
            refunded_at=now,
        )
        self.beta_pending = StoreOrder.objects.create(
            participant=self.beta_pending_participant,
            product=self.product_beta,
            price_at_order=self.product_beta.price,
            status=StoreOrder.Status.PENDING,
        )
        self.gamma_pending = StoreOrder.objects.create(
            participant=self.gamma_pending_participant,
            product=self.product_beta,
            price_at_order=self.product_beta.price,
            status=StoreOrder.Status.PENDING,
        )
        self.unassigned_pending = StoreOrder.objects.create(
            participant=self.unassigned_pending_participant,
            product=self.product_alpha,
            price_at_order=self.product_alpha.price,
            status=StoreOrder.Status.PENDING,
        )

    @staticmethod
    def _participant(national_id, full_name, group, purchase_points, points, miles):
        user = User.objects.create_user(
            national_id=national_id,
            password=national_id,
            role=Role.PARTICIPANT,
            full_name=full_name,
        )
        return Participant.objects.create(
            user=user,
            group=group,
            academic_stage="grade_7",
            purchase_points=purchase_points,
            points=points,
            miles=miles,
        )

    @staticmethod
    def _ids(response, context_key):
        return list(response.context[context_key].values_list("id", flat=True))

    def _store_snapshot(self):
        """All values a forged management POST could mutate, plus row counts."""
        return {
            "orders": list(
                StoreOrder.objects.order_by("id").values_list(
                    "id",
                    "participant_id",
                    "product_id",
                    "price_at_order",
                    "status",
                    "completed_at",
                    "refunded_at",
                )
            ),
            "products": list(
                StoreProduct.objects.order_by("id").values_list(
                    "id", "name", "description", "price", "stock", "image"
                )
            ),
            "participants": list(
                Participant.objects.order_by("id").values_list(
                    "id", "user_id", "group_id", "points", "miles", "purchase_points"
                )
            ),
            "row_counts": {
                "users": User.objects.count(),
                "participants": Participant.objects.count(),
                "products": StoreProduct.objects.count(),
                "orders": StoreOrder.objects.count(),
            },
        }

    def test_group_supervisor_reads_all_and_only_orders_of_assigned_groups(self):
        self.client.force_login(self.group_supervisor)
        response = self.client.get(
            self.url,
            {
                "group": self.group_gamma.id,
                "order_id": self.gamma_pending.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage_store"])

        expected_all = {
            self.alpha_pending.id,
            self.alpha_completed.id,
            self.alpha_refunded.id,
            self.beta_pending.id,
        }
        expected_pending = {self.alpha_pending.id, self.beta_pending.id}
        all_ids = self._ids(response, "all_orders")
        pending_ids = self._ids(response, "pending_orders")
        self.assertEqual(set(all_ids), expected_all)
        self.assertEqual(set(pending_ids), expected_pending)
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(len(pending_ids), len(set(pending_ids)))
        self.assertNotIn(self.gamma_pending.id, all_ids)
        self.assertNotIn(self.unassigned_pending.id, all_ids)

    def test_group_supervisor_page_is_read_only_and_ignores_edit_and_scope_spoofing(self):
        self.client.force_login(self.group_supervisor)
        response = self.client.get(
            self.url,
            {
                "edit": self.product_alpha.id,
                "group": self.group_gamma.id,
                "order_id": self.gamma_pending.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage_store"])
        self.assertNotIn("editing_product", response.context[-1])
        self.assertNotIn("product_form", response.context[-1])
        self.assertEqual(
            set(self._ids(response, "all_orders")),
            {
                self.alpha_pending.id,
                self.alpha_completed.id,
                self.alpha_refunded.id,
                self.beta_pending.id,
            },
        )

        html = response.content.decode()
        # The read-only table retains the assigned students and each order state.
        for name in (
            "طالب ألف معلّق",
            "طالب ألف مكتمل",
            "طالب ألف مسترجَع",
            "طالب باء معلّق",
        ):
            self.assertIn(name, html)
        self.assertIn("قيد التنفيذ", html)
        self.assertIn("مكتمل", html)
        self.assertIn("مسترجَع", html)
        self.assertNotIn("طالب جيم خارج النطاق", html)
        self.assertNotIn("طالب بلا بيئة", html)

        # No management controls may be emitted for a read-only role.
        self.assertNotIn("تحويل إلى مكتمل", html)
        self.assertNotIn("تأكيد استرجاع هذا الطلب", html)
        self.assertNotIn("إضافة منتج جديد", html)
        self.assertNotIn("المنتجات الحالية", html)
        self.assertNotRegex(html, r"name\s*=\s*['\"]action['\"]")
        self.assertNotRegex(html, r"<form[^>]+product-form")

    def test_group_supervisor_with_no_group_reads_an_empty_order_set(self):
        self.client.force_login(self.no_group_supervisor)
        response = self.client.get(
            self.url,
            {"group": self.group_alpha.id, "edit": self.product_alpha.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage_store"])
        self.assertEqual(self._ids(response, "pending_orders"), [])
        self.assertEqual(self._ids(response, "all_orders"), [])
        self.assertNotIn("editing_product", response.context[-1])
        self.assertNotIn("product_form", response.context[-1])

    def test_general_supervisor_and_superadmin_read_every_order(self):
        expected_ids = set(StoreOrder.objects.values_list("id", flat=True))
        for user in (self.general_supervisor, self.superadmin):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(
                    self.url,
                    {"group": self.group_alpha.id, "order_id": self.alpha_pending.id},
                )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["can_manage_store"])
                self.assertEqual(set(self._ids(response, "all_orders")), expected_ids)
                self.assertEqual(
                    set(self._ids(response, "pending_orders")),
                    {
                        self.alpha_pending.id,
                        self.beta_pending.id,
                        self.gamma_pending.id,
                        self.unassigned_pending.id,
                    },
                )
                self.assertIn("products", response.context[-1])
                self.assertIn("product_form", response.context[-1])

    def test_group_supervisor_post_is_always_forbidden_without_any_mutation(self):
        self.client.force_login(self.group_supervisor)
        baseline = self._store_snapshot()
        forged_posts = (
            ("complete", {"order_id": self.alpha_pending.id}),
            ("refund", {"order_id": self.alpha_completed.id}),
            (
                "add_product",
                {
                    "name": "منتج لا ينبغي إضافته",
                    "description": "محاولة محظورة",
                    "price": 99,
                    "stock": 5,
                },
            ),
            (
                "edit_product",
                {
                    "product_id": self.product_alpha.id,
                    "name": "اسم لا ينبغي حفظه",
                    "description": "تعديل محظور",
                    "price": 999,
                    "stock": 0,
                },
            ),
            ("delete_product", {"product_id": self.product_beta.id}),
            (
                "unknown_action",
                {
                    "order_id": self.gamma_pending.id,
                    "product_id": self.product_alpha.id,
                },
            ),
            ("", {"order_id": self.gamma_pending.id, "product_id": self.product_beta.id}),
        )

        for action, payload in forged_posts:
            with self.subTest(action=action or "missing"):
                response = self.client.post(self.url, {"action": action, **payload})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self._store_snapshot(), baseline)

    def test_participant_and_anonymous_users_cannot_open_store_management(self):
        self.client.force_login(self.alpha_pending_participant.user)
        participant_response = self.client.get(self.url)
        self.assertEqual(participant_response.status_code, 403)

        self.client.logout()
        anonymous_response = self.client.get(self.url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertNotEqual(anonymous_response.status_code, 200)

    def test_general_supervisor_and_superadmin_can_complete_and_refund_once(self):
        role_cases = (
            (self.general_supervisor, self.alpha_pending),
            (self.superadmin, self.beta_pending),
        )
        for user, order in role_cases:
            with self.subTest(role=user.role):
                self.client.force_login(user)
                participant = Participant.objects.get(id=order.participant_id)
                product = StoreProduct.objects.get(id=order.product_id)
                before_complete = (
                    participant.points,
                    participant.miles,
                    participant.purchase_points,
                    product.stock,
                )

                complete_response = self.client.post(
                    self.url, {"action": "complete", "order_id": order.id}
                )
                self.assertRedirects(complete_response, self.url)
                order.refresh_from_db()
                participant.refresh_from_db()
                product.refresh_from_db()
                self.assertEqual(order.status, StoreOrder.Status.COMPLETED)
                self.assertIsNotNone(order.completed_at)
                self.assertEqual(
                    (participant.points, participant.miles, participant.purchase_points, product.stock),
                    before_complete,
                )

                before_refund = (
                    participant.points,
                    participant.miles,
                    participant.purchase_points,
                    product.stock,
                )
                refund_response = self.client.post(
                    self.url, {"action": "refund", "order_id": order.id}
                )
                self.assertRedirects(refund_response, self.url)
                order.refresh_from_db()
                participant.refresh_from_db()
                product.refresh_from_db()
                self.assertEqual(order.status, StoreOrder.Status.REFUNDED)
                self.assertIsNotNone(order.refunded_at)
                self.assertEqual(participant.points, before_refund[0])
                self.assertEqual(participant.miles, before_refund[1])
                self.assertEqual(
                    participant.purchase_points, before_refund[2] + order.price_at_order
                )
                self.assertEqual(product.stock, before_refund[3] + 1)

                after_first_refund = (
                    participant.points,
                    participant.miles,
                    participant.purchase_points,
                    product.stock,
                    order.refunded_at,
                )
                second_refund = self.client.post(
                    self.url, {"action": "refund", "order_id": order.id}
                )
                self.assertRedirects(second_refund, self.url)
                order.refresh_from_db()
                participant.refresh_from_db()
                product.refresh_from_db()
                self.assertEqual(
                    (
                        participant.points,
                        participant.miles,
                        participant.purchase_points,
                        product.stock,
                        order.refunded_at,
                    ),
                    after_first_refund,
                )

    def test_group_supervisor_store_nav_item_has_only_its_scoped_pending_badge(self):
        items = build_navbar(self.group_supervisor, "store_management")
        store_item = next(item for item in items if item["key"] == "store_management")

        self.assertEqual(store_item["group"], "store")
        self.assertEqual(store_item["group_label"], "المتجر")
        self.assertFalse(store_item["mobile_primary"])
        self.assertTrue(store_item["active"])
        # alpha + beta are assigned; gamma and the no-group participant are not.
        self.assertEqual(store_item["badge_count"], 2)
        self.assertEqual(
            get_notification_counts(self.group_supervisor), {"store_management": 2}
        )
        self.assertEqual(
            get_notification_counts(self.no_group_supervisor), {"store_management": 0}
        )
