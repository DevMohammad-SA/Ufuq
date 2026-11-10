"""Store-only LIMITED grants do not widen any other student/report scope."""

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from participants.capabilities import can_manage_store
from participants.models import (
    Group, Participant, PointsLedgerEntry, PointsResetSnapshot,
    StoreOrder, StoreProduct, SupervisorCapability,
)
from participants.views import (
    StoreManagementView, build_navbar, get_notification_counts, store_orders_for_user,
)


class LimitedStoreManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = cls.user("limited-store-manager", Role.LIMITED_SUPERVISOR)
        cls.general = cls.user("limited-store-general", Role.GENERAL_SUPERVISOR)
        cls.capability = SupervisorCapability.objects.create(
            supervisor=cls.manager, manage_store=True, follow_tasks=False,
            granted_by=cls.general,
        )
        cls.group_supervisor = cls.user("limited-store-group", Role.GROUP_SUPERVISOR)
        cls.groups = [Group.objects.create(name=f"Store scope {i}") for i in range(2)]
        cls.groups[0].supervisor.add(cls.group_supervisor)
        cls.product = StoreProduct.objects.create(
            name="منتج محدود", description="اختبار", price=12, stock=7,
        )
        cls.deletable = StoreProduct.objects.create(name="بلا طلبات", price=4, stock=2)
        cls.students = []
        cls.orders = []
        for i, group in enumerate([*cls.groups, None]):
            user = cls.user(f"limited-store-student-{i}", Role.PARTICIPANT)
            student = Participant.objects.create(
                user=user, group=group, academic_stage="grade_7",
                points=70 + i, miles=700 + i, purchase_points=40 + i,
            )
            cls.students.append(student)
            cls.orders.append(StoreOrder.objects.create(
                participant=student, product=cls.product, price_at_order=12,
            ))
        cls.url = reverse("participants:store_management")

    @staticmethod
    def user(username, role, **kwargs):
        return User.objects.create_user(
            username=username, password="SyntheticLimitedStore123!",
            role=role, full_name=username, **kwargs,
        )

    @staticmethod
    def snapshot():
        """Include currencies, history and every order/product field."""
        return {
            "products": list(StoreProduct.objects.order_by("pk").values()),
            "orders": list(StoreOrder.objects.order_by("pk").values()),
            "participants": list(Participant.objects.order_by("pk").values()),
            "ledger": list(PointsLedgerEntry.objects.order_by("pk").values()),
            "snapshots": list(PointsResetSnapshot.objects.order_by("pk").values()),
        }

    def action_payloads(self):
        return [
            {"action": "add_product", "name": "forged", "price": 3, "stock": 4},
            {"action": "edit_product", "product_id": self.product.pk,
             "name": "forged edit", "description": "changed", "price": 91, "stock": 92},
            {"action": "delete_product", "product_id": self.deletable.pk},
            {"action": "complete", "order_id": self.orders[0].pk},
            {"action": "refund", "order_id": self.orders[1].pk},
            {"action": "unknown", "order_id": "not-an-id", "product_id": "not-an-id"},
            {"order_id": "not-an-id", "product_id": "not-an-id"},
        ]

    def assert_writes_denied(self, actor):
        self.client.force_login(actor)
        baseline = self.snapshot()
        for payload in self.action_payloads():
            with self.subTest(actor=actor.role, action=payload.get("action")):
                self.assertEqual(self.client.post(self.url, payload).status_code, 403)
                self.assertEqual(self.snapshot(), baseline)

    def test_store_only_grant_sees_every_order_and_global_pending_badge(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.url, {
            "group": self.groups[0].pk,
            "participant_id": self.students[0].pk,
            "order_id": self.orders[0].pk,
            "edit": self.product.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_manage_store"])
        expected_ids = {order.pk for order in self.orders}
        self.assertEqual(set(response.context["all_orders"].values_list("pk", flat=True)), expected_ids)
        self.assertEqual(set(response.context["pending_orders"].values_list("pk", flat=True)), expected_ids)
        self.assertEqual(response.context["editing_product"].pk, self.product.pk)
        self.assertIn("product_form", response.context[-1])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(get_notification_counts(self.manager), {"store_management": 3})
        items = build_navbar(self.manager, "store_management")
        store_item = next(item for item in items if item["key"] == "store_management")
        self.assertEqual(store_item["badge_count"], 3)
        self.assertFalse(any(item["key"] == "tasks" for item in items))
        self.assertFalse(self.manager.group_set.exists())
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.role, Role.LIMITED_SUPERVISOR)
        self.assertFalse(self.manager.is_staff)
        self.assertFalse(self.manager.is_superuser)

    def test_all_product_actions_work_without_changing_currencies_or_history(self):
        self.client.force_login(self.manager)
        before = self.snapshot()
        self.assertEqual(self.client.post(self.url, {
            "action": "add_product", "name": "new product", "description": "new",
            "price": 20, "stock": 5,
        }).status_code, 302)
        created = StoreProduct.objects.get(name="new product")
        self.assertEqual(self.client.post(self.url, {
            "action": "edit_product", "product_id": created.pk, "name": "edited product",
            "description": "edited", "price": 25, "stock": 8,
        }).status_code, 302)
        created.refresh_from_db()
        self.assertEqual((created.name, created.description, created.price, created.stock),
                         ("edited product", "edited", 25, 8))
        self.assertEqual(self.client.post(self.url, {
            "action": "delete_product", "product_id": created.pk,
        }).status_code, 302)
        self.assertFalse(StoreProduct.objects.filter(pk=created.pk).exists())
        after = self.snapshot()
        for key in ("orders", "participants", "ledger", "snapshots"):
            self.assertEqual(after[key], before[key])

    def test_complete_and_refund_once_only_affect_purchase_currency_and_stock(self):
        self.client.force_login(self.manager)
        order = self.orders[1]  # A different environment, never assigned to manager.
        student = self.students[1]
        before = self.snapshot()
        self.assertEqual(self.client.post(self.url, {
            "action": "complete", "order_id": order.pk,
        }).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, StoreOrder.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)
        after_complete = self.snapshot()
        for key in ("products", "participants", "ledger", "snapshots"):
            self.assertEqual(after_complete[key], before[key])
        self.assertEqual(self.client.post(self.url, {
            "action": "refund", "order_id": order.pk,
        }).status_code, 302)
        order.refresh_from_db()
        student.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, StoreOrder.Status.REFUNDED)
        self.assertIsNotNone(order.refunded_at)
        self.assertEqual((student.points, student.miles, student.purchase_points), (71, 701, 53))
        self.assertEqual(self.product.stock, 8)
        after_refund = self.snapshot()
        self.assertEqual(self.client.post(self.url, {
            "action": "refund", "order_id": order.pk,
        }).status_code, 302)
        self.assertEqual(self.snapshot(), after_refund)
        for key in ("ledger", "snapshots"):
            self.assertEqual(after_refund[key], before[key])
        self.assertEqual(get_notification_counts(self.manager)["store_management"], 2)

    def test_delete_preserves_existing_nonrefunded_protection_and_refunded_history_policy(self):
        self.client.force_login(self.manager)
        before = self.snapshot()
        response = self.client.post(self.url, {"action": "delete_product", "product_id": self.product.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["delete_error"])
        self.assertEqual(self.snapshot(), before)
        refunded_only = StoreProduct.objects.create(name="refunded only", price=5, stock=3)
        history = StoreOrder.objects.create(
            participant=self.students[0], product=refunded_only, price_at_order=5,
            status=StoreOrder.Status.REFUNDED, refunded_at=timezone.now(),
        )
        currencies = list(Participant.objects.order_by("pk").values())
        self.assertEqual(self.client.post(self.url, {
            "action": "delete_product", "product_id": refunded_only.pk,
        }).status_code, 302)
        self.assertFalse(StoreProduct.objects.filter(pk=refunded_only.pk).exists())
        self.assertFalse(StoreOrder.objects.filter(pk=history.pk).exists())
        self.assertEqual(list(Participant.objects.order_by("pk").values()), currencies)

    def test_revocation_after_get_denies_all_forms_and_direct_posts_without_mutation(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        SupervisorCapability.objects.filter(pk=self.capability.pk).update(manage_store=False)
        self.assertFalse(can_manage_store(self.manager))
        self.assertFalse(store_orders_for_user(self.manager).exists())
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assert_writes_denied(self.manager)
        self.assertEqual(get_notification_counts(self.manager), {})
        self.assertFalse(any(item["key"] == "store_management" for item in build_navbar(self.manager, "")))

    def test_cached_capability_relation_cannot_keep_revoked_store_rights(self):
        cached_actor = User.objects.get(pk=self.manager.pk)
        self.assertTrue(cached_actor.limited_capabilities.manage_store)
        self.assertTrue(can_manage_store(cached_actor))
        SupervisorCapability.objects.filter(supervisor=cached_actor).update(manage_store=False)
        self.assertTrue(cached_actor.limited_capabilities.manage_store)  # Intentionally stale.
        self.assertFalse(can_manage_store(cached_actor))
        self.assertFalse(store_orders_for_user(cached_actor).exists())
        baseline = self.snapshot()
        for payload in self.action_payloads():
            with self.subTest(action=payload.get("action")):
                request = RequestFactory().post(self.url, payload)
                request.user = cached_actor
                view = StoreManagementView()
                view.setup(request)
                with self.assertRaises(PermissionDenied):
                    view.post(request)
                self.assertEqual(self.snapshot(), baseline)

    def test_missing_row_and_task_only_grant_deny_get_and_every_write(self):
        no_row = self.user("limited-store-no-row", Role.LIMITED_SUPERVISOR)
        tasks_only = self.user("limited-store-tasks-only", Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=tasks_only, follow_tasks=True, manage_store=False)
        for actor in (no_row, tasks_only):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                self.assertEqual(self.client.get(self.url, {"edit": self.product.pk}).status_code, 403)
                self.assertFalse(can_manage_store(actor))
                self.assertFalse(store_orders_for_user(actor).exists())
                self.assert_writes_denied(actor)

    def test_flags_on_other_roles_or_staff_superuser_bits_do_not_grant_store_management(self):
        cases = [
            (Role.PARTICIPANT, False, False),
            (Role.GROUP_SUPERVISOR, False, False),
            ("invalid-role", False, False),
            (Role.LIMITED_SUPERVISOR, True, True),
        ]
        for i, (role, staff, superuser) in enumerate(cases):
            actor = self.user(f"limited-store-wrong-{i}", role, is_staff=staff, is_superuser=superuser)
            SupervisorCapability.objects.create(
                supervisor=actor, manage_store=role != Role.LIMITED_SUPERVISOR,
                follow_tasks=True,
            )
            with self.subTest(role=role, staff=staff):
                self.assertFalse(can_manage_store(actor))
                self.client.force_login(actor)
                self.assertEqual(self.client.get(self.url).status_code,
                                 200 if role == Role.GROUP_SUPERVISOR else 403)
                self.assert_writes_denied(actor)

    def test_stale_role_or_active_state_is_reloaded_before_all_actions(self):
        """Call post directly to prove its own guard, not only mixin dispatch."""
        stale_actor = User.objects.get(pk=self.manager.pk)
        baseline = self.snapshot()
        for changes in ({"role": Role.PARTICIPANT}, {"is_active": False}, {"role": "invalid-role"}):
            User.objects.filter(pk=self.manager.pk).update(**changes)
            for payload in self.action_payloads():
                with self.subTest(changes=changes, action=payload.get("action")):
                    request = RequestFactory().post(self.url, payload)
                    request.user = stale_actor
                    view = StoreManagementView()
                    view.setup(request)
                    with self.assertRaises(PermissionDenied):
                        view.post(request)
                    self.assertEqual(self.snapshot(), baseline)
            User.objects.filter(pk=self.manager.pk).update(role=Role.LIMITED_SUPERVISOR, is_active=True)

    def test_inactive_roles_fail_the_view_gate_even_with_legacy_or_limited_rights(self):
        for i, role in enumerate((Role.GENERAL_SUPERVISOR, Role.SUPERADMIN, Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR)):
            actor = self.user(f"store-inactive-{i}", role, is_active=False)
            if role == Role.LIMITED_SUPERVISOR:
                SupervisorCapability.objects.create(supervisor=actor, manage_store=True)
            request = RequestFactory().get(self.url)
            request.user = actor
            view = StoreManagementView()
            view.setup(request)
            with self.subTest(role=role):
                self.assertFalse(view.test_func())
                self.assertFalse(view.can_manage_store())
                self.assertFalse(can_manage_store(actor))

    def test_group_supervisor_remains_read_only_and_scoped_with_flags_present(self):
        SupervisorCapability.objects.create(supervisor=self.group_supervisor, manage_store=True, follow_tasks=True)
        self.client.force_login(self.group_supervisor)
        response = self.client.get(self.url, {"edit": self.product.pk, "group": self.groups[1].pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage_store"])
        self.assertEqual(list(response.context["all_orders"].values_list("pk", flat=True)), [self.orders[0].pk])
        self.assertNotIn("product_form", response.context[-1])
        self.assertNotIn("editing_product", response.context[-1])
        self.assertEqual(get_notification_counts(self.group_supervisor)["store_management"], 1)
        self.assert_writes_denied(self.group_supervisor)

    def test_store_grant_does_not_extend_reports_student_scope_or_other_pages(self):
        self.client.force_login(self.manager)
        routes = [
            ("participants_data", {}), ("participants_data_pdf", {}),
            ("student_report", {}), ("report_center", {}), ("report_center_export", {}),
            ("student_detail", {"pk": self.students[1].pk}),
            ("student_detail", {"pk": self.students[2].pk}),
            ("extra_points", {}), ("points_ledger", {}),
            ("general_supervisor_dashboard", {}), ("weekly_task_review", {}),
            ("supervisor_add", {}), ("import_participants", {}),
        ]
        for route, kwargs in routes:
            with self.subTest(route=route, kwargs=kwargs):
                response = self.client.get(reverse(f"participants:{route}", kwargs=kwargs), {
                    "participant_id": self.students[1].pk,
                    "student": self.students[2].pk,
                    "group": self.groups[1].pk,
                })
                self.assertEqual(response.status_code, 403)
