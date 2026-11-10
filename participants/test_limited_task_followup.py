"""Limited task follow-up is read/preview only, even with legacy delegation."""
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Role, User
from .access import (
    can_manage_tasks, can_review_task_submissions, can_view_task_submissions,
    scoped_submissions, task_participants,
)
from .models import (
    Participant, PointsLedgerEntry, StoreOrder, StoreProduct, SupervisorCapability,
    TaskGradeRevision, TaskReviewPermission, TaskSubmission, WeeklyTask,
)
from .reporting import REPORT_ROLES, scoped_participants
from .test_task_status_filters import FilterMarkup, TaskFilterFixture
from .views import WeeklyTaskReviewView


class LimitedTaskFollowupTests(TaskFilterFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.limited = User.objects.create_user(username="limited-follow", role=Role.LIMITED_SUPERVISOR)
        cls.grant = SupervisorCapability.objects.create(supervisor=cls.limited, follow_tasks=True, granted_by=cls.general)
        cls.store_only = User.objects.create_user(username="limited-store-only", role=Role.LIMITED_SUPERVISOR)
        SupervisorCapability.objects.create(supervisor=cls.store_only, manage_store=True, follow_tasks=False)
        cls.no_grant = User.objects.create_user(username="limited-no-grant", role=Role.LIMITED_SUPERVISOR)
        cls.ungrouped = Participant.objects.create(
            user=User.objects.create_user(username="limited-ungrouped-student", full_name="طالب بلا بيئة", role=Role.PARTICIPANT),
            group=None, academic_stage="grade_7",
        )
        cls.staff_profile = Participant.objects.create(
            user=User.objects.create_user(username="limited-staff-profile", full_name="ملف مشرف لا يجوز عرضه", role=Role.GROUP_SUPERVISOR),
            group=cls.alpha, academic_stage="grade_7", guardian_phone="staff-profile-secret",
        )
        cls.staff_submission = TaskSubmission.objects.create(
            task=cls.current, participant=cls.staff_profile, text_content="تسليم ملف مشرف لا يجوز عرضه",
            file="task_submissions/staff-profile-secret.jpg", status="pending",
        )
        # Deliberately bypass choice validation to reproduce a stale/forged
        # legacy delegation row; it must confer no grading power on LIMITED.
        TaskReviewPermission.objects.create(supervisor=cls.limited, enabled=True, granted_by=cls.general)

    def snapshot(self):
        models = (User, Participant, WeeklyTask, TaskSubmission, TaskGradeRevision, PointsLedgerEntry, SupervisorCapability, TaskReviewPermission, StoreProduct, StoreOrder)
        return {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}

    def direct_request(self, user=None, **query):
        request = RequestFactory().get(self.url, query)
        request.user = user or self.limited
        return request

    def test_helpers_grant_only_task_scope_without_expanding_reports(self):
        self.assertTrue(can_view_task_submissions(self.limited))
        self.assertFalse(can_manage_tasks(self.limited))
        self.assertFalse(can_review_task_submissions(self.limited))
        self.assertNotIn(Role.LIMITED_SUPERVISOR, REPORT_ROLES)
        self.assertFalse(scoped_participants(self.limited).exists())
        expected_roster = {p.pk for p in self.students.values()} | {self.ungrouped.pk}
        self.assertEqual(set(task_participants(self.limited).values_list("pk", flat=True)), expected_roster)
        expected_submissions = set(TaskSubmission.objects.filter(participant__user__role=Role.PARTICIPANT).values_list("pk", flat=True))
        self.assertEqual(set(scoped_submissions(self.limited).values_list("pk", flat=True)), expected_submissions)
        self.assertNotIn(self.staff_submission.pk, expected_submissions)

    def test_get_observes_all_groups_all_states_and_archived_tasks(self):
        response = self.page(self.limited, edit_task="malformed")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["limited_task_followup"])
        for key in ("can_manage_tasks", "can_review_submissions", "can_revise_grades", "can_view_student_detail"):
            self.assertFalse(response.context[key])
        self.assertNotIn("edit_task", response.context)
        self.assertNotIn("edit_form", response.context)
        self.assertNotIn("create_form", response.context)
        self.assertEqual(self.task_ids(response), {self.current.pk, self.expired_pending.pk, self.old_rejected.pk, self.old_accepted.pk, self.foreign_only.pk, self.empty_old.pk})
        current = self.entry(response, self.current)
        self.assertEqual({s.participant_id for s in current["submissions"]}, {self.students[state].pk for state in ("pending", "accepted", "rejected", "returned", "inactive-submitted", "foreign-submitted")})
        self.assertEqual({p.pk for p in current["not_submitted"]}, {self.students["missing"].pk, self.students["foreign-missing"].pk, self.ungrouped.pk})
        self.assertEqual(current["group_names"], sorted([self.alpha.name, self.beta.name, self.foreign.name]))
        self.assertContains(response, "الاطلاع والمعاينة فقط")
        self.assertContains(response, "هذه المعاينة للقراءة فقط")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_limited_filtered_scope_covers_all_groups_without_false_missing(self):
        response = self.page(self.limited, status="rejected", group=self.alpha.pk, participant=self.students["pending"].pk)
        self.assertEqual(self.task_ids(response), {self.current.pk, self.old_rejected.pk, self.foreign_only.pk})
        current = self.entry(response, self.current)
        self.assertEqual({s.pk for s in current["submissions"]}, {self.submissions["rejected"].pk, self.submissions["foreign-submitted"].pk})
        self.assertEqual({p.pk for p in current["not_submitted"]}, {self.students["missing"].pk, self.students["foreign-missing"].pk, self.ungrouped.pk})
        self.assertNotContains(response, "من لم يسلّموا")
        for entry in response.context["tasks_with_submissions"]:
            self.assertEqual({s.status for s in entry["submissions"]}, {"rejected"})
        inactive = self.page(self.limited, status="pending", task=self.current.pk)
        self.assertIn(self.students["inactive-submitted"].pk, {s.participant_id for s in self.entry(inactive, self.current)["submissions"]})

    def test_rendered_markup_has_only_get_forms_and_no_profile_links_or_grading_fields(self):
        response = self.page(self.limited, task=self.current.pk, edit_task=self.current.pk)
        markup = FilterMarkup(response.content.decode())
        self.assertTrue(any(tag == "button" and "preview-btn" in attrs.get("class", "").split() for tag, attrs in markup.elements))
        # The shared app shell still offers its authorized logout POST.
        post_forms = [attrs for tag, attrs in markup.elements if tag == "form" and attrs.get("method", "get").lower() == "post"]
        self.assertTrue(all(attrs.get("action") == reverse("accounts:logout") for attrs in post_forms))
        forbidden_fields = {"action", "submission_id", "task_id", "is_featured", "review_version", "previous_award", "historical_award", "rejection_reason", "revision_note"}
        self.assertFalse(any(attrs.get("name") in forbidden_fields for tag, attrs in markup.elements if tag in ("input", "textarea", "select")))
        for participant in list(self.students.values()) + [self.ungrouped]:
            self.assertNotContains(response, reverse("participants:student_detail", args=[participant.pk]))
        self.assertNotContains(response, reverse("participants:tasks_archive"))
        self.assertNotContains(response, '<th>إجراء</th>')
        self.assertNotContains(response, "ملف مشرف لا يجوز عرضه")
        self.assertNotContains(response, "staff-profile-secret")
        for student in self.students.values():
            self.assertNotContains(response, student.phone)
            self.assertNotContains(response, student.guardian_phone)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<img src=x onerror="unsafe()">')

    def test_file_and_text_preview_metadata_remains_escaped_and_available(self):
        response = self.page(self.limited, status="pending", task=self.current.pk)
        markup = FilterMarkup(response.content.decode())
        payload = next(attrs for tag, attrs in markup.elements if tag == "button" and attrs.get("data-url", "").endswith("/pending.jpg"))
        self.assertEqual(payload["data-type"], "image")
        self.assertEqual(payload["data-content"], self.submissions["pending"].text_content)
        self.assertEqual(payload["data-name"], self.students["pending"].user.full_name)
        self.assertEqual(payload["data-status"], self.submissions["pending"].get_status_display())
        self.assertEqual(payload["aria-controls"], "preview-modal")
        self.assertContains(response, 'aria-label="حالة التسليم للقراءة فقط"')

    def test_get_and_preview_never_change_business_data_or_balances(self):
        self.client.force_login(self.limited)
        before = self.snapshot()
        for query in ({}, {"status": "pending"}, {"task": self.old_rejected.pk, "status": "rejected"}, {"edit_task": self.current.pk}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(self.url, query).status_code, 200)
                self.assertEqual(self.snapshot(), before)

    def test_every_post_action_is_denied_and_changes_zero_rows_even_with_legacy_grant(self):
        self.client.force_login(self.limited)
        # Store management is independent: granting both flags still cannot
        # authorize a single task mutation, including foreign/invalid IDs.
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(manage_store=True, follow_tasks=True)
        before = self.snapshot()
        for action in ("accept", "reject", "return_for_revision", "cancel_grade", "revise_grade", "create_task", "update_task", "toggle_active", "reopen", "unsupported", ""):
            for identifier in (str(self.submissions["pending"].pk), str(self.foreign_submission.pk), str(self.staff_submission.pk), "malformed-id"):
                with self.subTest(action=action, identifier=identifier):
                    response = self.client.post(self.url + "?status=pending&task=" + str(self.current.pk), {
                        "action": action, "submission_id": identifier, "task_id": identifier,
                        "is_featured": "on", "historical_award": "not_awarded", "revision_note": "ملاحظة", "confirm": "yes",
                    })
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(self.snapshot(), before)

    def test_direct_post_denies_before_reading_action_ids_or_any_review_grant(self):
        request = RequestFactory().post(self.url)
        request.user = self.limited
        request._post = Mock()
        request._files = {}
        view = WeeklyTaskReviewView()
        view.setup(request)
        with patch("participants.access.can_manage_tasks", side_effect=AssertionError("must not consult task management")):
            with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                view.post(request)
        request.POST.get.assert_not_called()

    def test_missing_disabled_or_store_only_capability_cannot_open_tasks(self):
        for user in (self.no_grant, self.store_only):
            with self.subTest(user=user.username):
                response = self.page(user)
                self.assertEqual(response.status_code, 403)
                self.assertFalse(can_view_task_submissions(user))
                self.assertFalse(scoped_submissions(user).exists())
                self.assertFalse(task_participants(user).exists())
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(follow_tasks=False)
        self.assertEqual(self.page(self.limited).status_code, 403)

    def test_revocation_after_open_preview_uses_fresh_flag_and_blocks_get_and_post(self):
        self.assertEqual(self.page(self.limited).status_code, 200)
        # Warm the reverse-relation cache on purpose: it is not authorization.
        self.assertTrue(self.limited.limited_capabilities.follow_tasks)
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(follow_tasks=False)
        self.assertFalse(can_view_task_submissions(self.limited))
        self.assertFalse(scoped_submissions(self.limited).exists())
        self.assertFalse(task_participants(self.limited).exists())
        with self.assertRaises(PermissionDenied):
            WeeklyTaskReviewView.as_view()(self.direct_request())
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"action": "accept", "submission_id": self.submissions["pending"].pk}).status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_deactivation_with_stale_user_denies_direct_get_and_post(self):
        self.assertTrue(can_view_task_submissions(self.limited))
        User.objects.filter(pk=self.limited.pk).update(is_active=False)
        self.assertTrue(self.limited.is_active)
        self.assertFalse(can_view_task_submissions(self.limited))
        self.assertFalse(scoped_submissions(self.limited).exists())
        self.assertFalse(task_participants(self.limited).exists())
        with self.assertRaises(PermissionDenied):
            WeeklyTaskReviewView.as_view()(self.direct_request())
        request = RequestFactory().post(self.url, {"action": "accept", "submission_id": self.submissions["pending"].pk})
        request.user = self.limited
        with self.assertRaises(PermissionDenied):
            WeeklyTaskReviewView.as_view()(request)

    def test_role_change_with_stale_limited_user_revokes_task_followup(self):
        self.assertTrue(can_view_task_submissions(self.limited))
        User.objects.filter(pk=self.limited.pk).update(role=Role.PARTICIPANT)
        self.assertFalse(can_view_task_submissions(self.limited))
        self.assertFalse(scoped_submissions(self.limited).exists())
        self.assertFalse(task_participants(self.limited).exists())
        with self.assertRaises(PermissionDenied):
            WeeklyTaskReviewView.as_view()(self.direct_request())

    def test_limited_task_followup_does_not_grant_student_detail_or_archive(self):
        self.client.force_login(self.limited)
        for url in (reverse("participants:student_detail", args=[self.students["pending"].pk]), reverse("participants:tasks_archive")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
