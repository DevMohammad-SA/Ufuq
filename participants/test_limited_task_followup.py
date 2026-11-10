"""Limited task follow-up permits pending accept/reject, not task management."""
import re
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
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
        # A stale/forged legacy delegation must confer no additional powers;
        # only the fresh follow_tasks capability authorizes limited grading.
        TaskReviewPermission.objects.create(supervisor=cls.limited, enabled=True, granted_by=cls.general)

    def snapshot(self):
        models = (User, Participant, WeeklyTask, TaskSubmission, TaskGradeRevision, PointsLedgerEntry, SupervisorCapability, TaskReviewPermission, StoreProduct, StoreOrder)
        return {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}

    def direct_request(self, user=None, **query):
        request = RequestFactory().get(self.url, query)
        request.user = user or self.limited
        return request

    @staticmethod
    def rendered_forms(response):
        forms = []
        for html in re.findall(r"<form\b[^>]*>.*?</form\s*>", response.content.decode(), flags=re.IGNORECASE | re.DOTALL):
            markup = FilterMarkup(html)
            attrs = next(attrs for tag, attrs in markup.elements if tag == "form")
            fields = {attrs["name"]: attrs for tag, attrs in markup.elements if tag in ("input", "textarea", "select") and "name" in attrs}
            forms.append((attrs, fields))
        return forms

    def test_helpers_grant_only_task_scope_without_expanding_reports(self):
        self.assertTrue(can_view_task_submissions(self.limited))
        self.assertFalse(can_manage_tasks(self.limited))
        self.assertTrue(can_review_task_submissions(self.limited))
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
        self.assertTrue(response.context["can_review_submissions"])
        for key in ("can_manage_tasks", "can_revise_grades", "can_view_student_detail", "can_return_submissions"):
            self.assertFalse(response.context[key])
        self.assertNotIn("edit_task", response.context)
        self.assertNotIn("edit_form", response.context)
        self.assertNotIn("create_form", response.context)
        self.assertEqual(self.task_ids(response), {self.current.pk, self.expired_pending.pk, self.old_rejected.pk, self.old_accepted.pk, self.foreign_only.pk, self.empty_old.pk})
        current = self.entry(response, self.current)
        self.assertEqual({s.participant_id for s in current["submissions"]}, {self.students[state].pk for state in ("pending", "accepted", "rejected", "returned", "inactive-submitted", "foreign-submitted")})
        self.assertEqual({p.pk for p in current["not_submitted"]}, {self.students["missing"].pk, self.students["foreign-missing"].pk, self.ungrouped.pk})
        self.assertEqual(current["group_names"], sorted([self.alpha.name, self.beta.name, self.foreign.name]))
        self.assertContains(response, "متابعة وتصحيح المهام")
        self.assertContains(response, "المعاينة والقبول والرفض")
        self.assertNotContains(response, "الاطلاع والمعاينة فقط")
        self.assertNotContains(response, "هذه المعاينة للقراءة فقط")
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

    def test_rendered_markup_has_pending_native_review_forms_but_no_profile_or_management_fields(self):
        # Unknown historical awards still require the general supervisor before
        # acceptance; rejection remains available on this pending student row.
        TaskSubmission.objects.filter(pk=self.submissions["inactive-submitted"].pk).update(points_awarded=None)
        response = self.page(self.limited, task=self.current.pk, edit_task=self.current.pk)
        markup = FilterMarkup(response.content.decode())
        self.assertTrue(any(tag == "button" and "preview-btn" in attrs.get("class", "").split() for tag, attrs in markup.elements))
        forms = self.rendered_forms(response)
        filter_forms = [(attrs, fields) for attrs, fields in forms if "task-status-filter" in attrs.get("class", "").split()]
        self.assertEqual(len(filter_forms), 1)
        self.assertEqual(filter_forms[0][0].get("method", "get").lower(), "get")
        self.assertEqual(filter_forms[0][0]["action"], self.url)
        self.assertEqual(set(filter_forms[0][1]), {"status", "task"})
        # The shared app shell still offers its independently authorized logout.
        review_forms = [(attrs, fields) for attrs, fields in forms if attrs.get("method", "get").lower() == "post" and attrs.get("action") != reverse("accounts:logout")]
        self.assertEqual({fields["action"]["value"] for _, fields in review_forms}, {"accept", "reject"})
        for attrs, fields in review_forms:
            self.assertIn("csrfmiddlewaretoken", fields)
            self.assertTrue(fields["csrfmiddlewaretoken"].get("value"))
            self.assertEqual(fields["submission_id"]["type"], "hidden")
            if fields["action"]["value"] == "accept":
                self.assertIn("accept-form", attrs.get("class", "").split())
                self.assertEqual(fields["is_featured"]["type"], "checkbox")
                self.assertEqual(fields["is_featured"]["value"], "on")
            else:
                self.assertIn("reject-form", attrs.get("class", "").split())
                self.assertEqual(fields["rejection_reason"]["type"], "text")
        accept_ids = {int(fields["submission_id"]["value"]) for _, fields in review_forms if fields["action"]["value"] == "accept"}
        reject_ids = {int(fields["submission_id"]["value"]) for _, fields in review_forms if fields["action"]["value"] == "reject"}
        self.assertEqual(accept_ids, {self.submissions["pending"].pk})
        self.assertEqual(reject_ids, {self.submissions["pending"].pk, self.submissions["inactive-submitted"].pk})
        forbidden_fields = {"task_id", "review_version", "previous_award", "historical_award", "revision_note"}
        self.assertFalse(any(attrs.get("name") in forbidden_fields for tag, attrs in markup.elements if tag in ("input", "textarea", "select")))
        self.assertFalse(any(tag == "form" and {"return-form", "grade-cancellation-form"} & set(attrs.get("class", "").split()) for tag, attrs in markup.elements))
        for participant in list(self.students.values()) + [self.ungrouped]:
            self.assertNotContains(response, reverse("participants:student_detail", args=[participant.pk]))
        self.assertNotContains(response, reverse("participants:tasks_archive"))
        self.assertContains(response, '<th>إجراء</th>')
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
        self.assertContains(response, 'aria-label="قرار مراجعة التسليم"')
        self.assertNotContains(response, 'aria-label="حالة التسليم للقراءة فقط"')

    def test_get_and_preview_never_change_business_data_or_balances(self):
        self.client.force_login(self.limited)
        before = self.snapshot()
        for query in ({}, {"status": "pending"}, {"task": self.old_rejected.pk, "status": "rejected"}, {"edit_task": self.current.pk}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(self.url, query).status_code, 200)
                self.assertEqual(self.snapshot(), before)

    def test_unsupported_post_actions_are_denied_and_change_zero_rows_even_with_legacy_grant(self):
        self.client.force_login(self.limited)
        # Store management is independent and cannot authorize extra task actions.
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(manage_store=True, follow_tasks=True)
        before = self.snapshot()
        for action in ("return_for_revision", "cancel_grade", "revise_grade", "create_task", "update_task", "toggle_active", "reopen", "unsupported", ""):
            for identifier in (str(self.submissions["pending"].pk), str(self.foreign_submission.pk), str(self.staff_submission.pk), "malformed-id"):
                with self.subTest(action=action, identifier=identifier):
                    response = self.client.post(self.url + "?status=pending&task=" + str(self.current.pk), {
                        "action": action, "submission_id": identifier, "task_id": identifier,
                        "is_featured": "on", "historical_award": "not_awarded", "revision_note": "ملاحظة", "confirm": "yes",
                    })
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(self.snapshot(), before)

    def test_accept_and_reject_pending_real_participants_are_allowed_across_all_groups(self):
        self.client.force_login(self.limited)
        # Unlike a group reviewer, LIMITED includes foreign environments and
        # already-received submissions on expired tasks and inactive students.
        submissions = (self.submissions["pending"], self.submissions["inactive-submitted"], self.foreign_submission)
        url = self.url + "?status=pending&task=" + str(self.current.pk)
        for action in ("accept", "reject"):
            for submission in submissions:
                with self.subTest(action=action, submission=submission.pk):
                    TaskSubmission.objects.filter(pk=submission.pk).update(
                        status="pending", points_awarded=False, award_value=None,
                        is_featured=False, reviewed_by=None, reviewed_at=None, rejection_reason="",
                    )
                    before = self.snapshot()
                    participant_before = next(row for row in before[Participant._meta.label] if row["id"] == submission.participant_id)
                    featured = submission.pk == self.submissions["pending"].pk
                    payload = {"action": action, "submission_id": submission.pk, "rejection_reason": "  سبب رفض <script>  "}
                    if featured:
                        payload["is_featured"] = "on"
                    self.assertRedirects(self.client.post(url, payload), url, fetch_redirect_response=False)
                    submission.refresh_from_db()
                    self.assertEqual(submission.status, "accepted" if action == "accept" else "rejected")
                    self.assertEqual(submission.reviewed_by_id, self.limited.pk)
                    self.assertIsNotNone(submission.reviewed_at)
                    self.assertEqual(submission.is_featured, action == "accept" and featured)
                    self.assertEqual(submission.rejection_reason, "" if action == "accept" else "سبب رفض <script>")
                    delta = (12 if featured else 10) if action == "accept" else 0
                    participant = Participant.objects.get(pk=submission.participant_id)
                    self.assertEqual(participant.points, participant_before["points"] + delta)
                    self.assertEqual(participant.miles, participant_before["miles"] + delta * 10)
                    self.assertEqual(participant.purchase_points, participant_before["purchase_points"] + delta)
                    self.assertEqual(submission.points_awarded, action == "accept")
                    self.assertEqual(submission.award_value, delta if action == "accept" else None)
                    after = self.snapshot()
                    for model in (User, WeeklyTask, TaskGradeRevision, SupervisorCapability, TaskReviewPermission, StoreProduct, StoreOrder):
                        self.assertEqual(after[model._meta.label], before[model._meta.label])
                    for model, target_id in ((Participant, submission.participant_id), (TaskSubmission, submission.pk)):
                        self.assertEqual(
                            [row for row in after[model._meta.label] if row["id"] != target_id],
                            [row for row in before[model._meta.label] if row["id"] != target_id],
                        )
                    old_ledger_ids = {row["id"] for row in before[PointsLedgerEntry._meta.label]}
                    self.assertEqual([row for row in after[PointsLedgerEntry._meta.label] if row["id"] in old_ledger_ids], before[PointsLedgerEntry._meta.label])
                    new_entries = [row for row in after[PointsLedgerEntry._meta.label] if row["id"] not in old_ledger_ids]
                    self.assertEqual(len(new_entries), 1 if action == "accept" else 0)
                    if action == "accept":
                        self.assertEqual(new_entries[0]["participant_id"], submission.participant_id)
                        self.assertEqual(new_entries[0]["points_delta"], delta)
                        self.assertEqual(new_entries[0]["granted_by_id"], self.limited.pk)
                        self.assertEqual(new_entries[0]["source"], PointsLedgerEntry.Source.WEEKLY_TASK)

    def test_accept_reject_excluded_malformed_or_final_submission_never_changes_rows(self):
        self.client.force_login(self.limited)
        before = self.snapshot()
        for action in ("accept", "reject"):
            for identifier, expected_status in ((str(self.staff_submission.pk), 404), ("malformed-id", 404), (str(self.foreign_submission.pk), 302)):
                with self.subTest(action=action, identifier=identifier):
                    response = self.client.post(self.url, {"action": action, "submission_id": identifier, "is_featured": "on", "rejection_reason": "سبب مصطنع"})
                    self.assertEqual(response.status_code, expected_status)
                    self.assertEqual(self.snapshot(), before)

    def test_missing_or_disabled_followup_denies_before_body_or_legacy_review_authorization(self):
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(follow_tasks=False)
        for user in (self.no_grant, self.store_only, self.limited):
            with self.subTest(user=user.username):
                request = RequestFactory().post(self.url)
                request.user = user
                request._post = Mock()
                request._files = {}
                view = WeeklyTaskReviewView()
                view.setup(request)
                # Fresh User/capability authorization may query the database;
                # it must still deny before parsing POST or legacy delegation.
                with patch("participants.access.can_manage_tasks", side_effect=AssertionError("must not consult task management")), \
                        patch.object(TaskReviewPermission.objects, "select_for_update", side_effect=AssertionError("must not lock a legacy review grant")), \
                        patch.object(TaskReviewPermission.objects, "filter", side_effect=AssertionError("must not consult a legacy review grant")), \
                        patch.object(WeeklyTaskReviewView, "_post_authorized", side_effect=AssertionError("must authorize before dispatch")), \
                        CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(PermissionDenied):
                        view.post(request)
                request.POST.get.assert_not_called()
                self.assertFalse(any("participants_taskreviewpermission" in query["sql"].lower() for query in queries))

    def test_missing_disabled_or_store_only_capability_cannot_open_tasks(self):
        for user in (self.no_grant, self.store_only):
            with self.subTest(user=user.username):
                response = self.page(user)
                self.assertEqual(response.status_code, 403)
                self.assertFalse(can_view_task_submissions(user))
                self.assertFalse(can_review_task_submissions(user))
                self.assertFalse(scoped_submissions(user).exists())
                self.assertFalse(task_participants(user).exists())
        SupervisorCapability.objects.filter(pk=self.grant.pk).update(follow_tasks=False)
        self.assertEqual(self.page(self.limited).status_code, 403)
        self.assertFalse(can_review_task_submissions(self.limited))

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
