"""Isolated review-list/preview regressions; use config.test_settings only."""
import datetime
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import Group, Participant, PointsLedgerEntry, TaskReviewPermission, TaskSubmission, WeeklyTask
from .views import WeeklyTaskReviewView


class PreviewMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class TaskReviewListsPreviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username="preview-general", full_name="المشرف العام", role=Role.GENERAL_SUPERVISOR)
        cls.system = User.objects.create_user(username="preview-system", full_name="مشرف النظام", role=Role.SUPERADMIN)
        cls.reviewer = User.objects.create_user(username="preview-reviewer", role=Role.GROUP_SUPERVISOR)
        cls.no_groups = User.objects.create_user(username="preview-no-groups", role=Role.GROUP_SUPERVISOR)
        cls.alpha = Group.objects.create(name="بيئة ألف المسموحة")
        cls.beta = Group.objects.create(name="بيئة باء المسموحة")
        cls.foreign_group = Group.objects.create(name="بيئة سرية خارج النطاق")
        cls.alpha.supervisor.add(cls.reviewer)
        cls.beta.supervisor.add(cls.reviewer)
        cls.students = []
        for i, state in enumerate(("pending", "accepted", "rejected", "returned", "resubmitted", "missing", "inactive-missing", "inactive-submitted", "foreign-submitted", "foreign-missing")):
            group = cls.foreign_group if state.startswith("foreign") else (cls.beta if i % 2 else cls.alpha)
            user = User.objects.create_user(
                username=f"preview-student-{i}", full_name=f"طالب اختبار {state}",
                role=Role.PARTICIPANT, is_active=not state.startswith("inactive"),
            )
            student = Participant.objects.create(user=user, group=group, academic_stage="grade_7")
            cls.students.append(student)
        cls.task = WeeklyTask.objects.create(
            title="مهمة الصورة والنص", description="بيانات اختبار مصطنعة", allowed_formats="image,text",
            require_all_formats=True, is_active=True, due_date=datetime.date.today() + datetime.timedelta(days=5),
        )
        cls.expired = WeeklyTask.objects.create(
            title="مهمة قديمة منتهية", description="اختبار فقط", allowed_formats="text",
            is_active=False, due_date=datetime.date.today() - datetime.timedelta(days=5),
        )
        cls.empty_history = WeeklyTask.objects.create(
            title="مهمة سابقة بلا تسليمات", description="اختبار فقط", allowed_formats="text",
            is_active=False, due_date=datetime.date.today() - datetime.timedelta(days=10),
        )
        cls.submissions = []
        for i in (0, 1, 2, 3, 4, 7, 8):
            status = ("pending", "accepted", "rejected", "returned", "pending")[i] if i < 5 else "pending"
            cls.submissions.append(TaskSubmission.objects.create(
                task=cls.task, participant=cls.students[i], status=status,
                file=f"task_submissions/{'foreign-secret' if i == 8 else 'preview-' + str(i)}.jpg",
                text_content=f'نص طالب {i} <img src=x onerror="alert(1)"> & حرف',
                revision_note="آخر تعديل قبل إعادة الرفع" if i in (3, 4) else "",
                points_awarded=i == 4,
            ))
        cls.late_submission = TaskSubmission.objects.create(task=cls.expired, participant=cls.students[0], text_content="نص متأخر للتصحيح")
        cls.original_date = timezone.now() - datetime.timedelta(days=9)
        TaskSubmission.objects.filter(pk=cls.late_submission.pk).update(submitted_at=cls.original_date)
        cls.url = reverse("participants:weekly_task_review")

    def page(self, user=None, **query):
        self.client.force_login(user or self.reviewer)
        return self.client.get(self.url, query)

    def enable(self):
        return TaskReviewPermission.objects.create(supervisor=self.reviewer, enabled=True, granted_by=self.general)

    @staticmethod
    def entry(response, task):
        return next(row for row in response.context["tasks_with_submissions"] if row["task"].pk == task.pk)

    @staticmethod
    def submission_row(response, submission):
        return re.search(r'<tr[^>]+data-submission="' + str(submission.pk) + r'"[^>]*>(.*?)</tr>', response.content.decode(), re.S).group(1)

    def assert_unawarded(self, submission):
        submission.refresh_from_db()
        submission.participant.refresh_from_db()
        self.assertEqual(submission.status, "pending")
        self.assertEqual((submission.participant.points, submission.participant.miles, submission.participant.purchase_points), (0, 0, 0))
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_each_task_has_all_scoped_submission_states_and_only_active_missing_students(self):
        response = self.page()
        entry = self.entry(response, self.task)
        self.assertEqual({s.participant_id for s in entry["submissions"]}, {self.students[i].pk for i in (0, 1, 2, 3, 4, 7)})
        self.assertEqual({p.pk for p in entry["not_submitted"]}, {self.students[5].pk})
        self.assertEqual({s.status for s in entry["submissions"]}, {"pending", "accepted", "rejected", "returned"})
        self.assertContains(response, "من سلّموا")
        self.assertContains(response, "من لم يسلّموا")
        self.assertContains(response, 'data-count="submitted">6</span>', html=False)
        self.assertContains(response, 'data-count="not-submitted">1</span>', html=False)

    def test_scope_excludes_foreign_names_counts_files_and_filter_options_even_if_forged(self):
        response = self.page(group=self.foreign_group.pk, status="all")
        for forbidden in (self.foreign_group.name, self.students[8].user.full_name, self.students[9].user.full_name, "foreign-secret.jpg"):
            self.assertNotContains(response, forbidden)
        entry = self.entry(response, self.task)
        self.assertEqual(entry["group_names"], sorted([self.alpha.name, self.beta.name]))
        self.assertNotContains(response, self.students[6].user.full_name)

    def test_group_supervisor_keeps_all_previous_tasks_and_missing_lists(self):
        response = self.page()
        self.assertEqual({entry["task"].pk for entry in response.context["tasks_with_submissions"]}, {self.task.pk, self.expired.pk, self.empty_history.pk})
        historical = self.entry(response, self.empty_history)
        self.assertEqual(len(historical["submissions"]), 0)
        self.assertEqual({p.pk for p in historical["not_submitted"]}, {self.students[i].pk for i in range(6)})

    def test_manager_keeps_expired_pending_task_and_explicit_task_filter(self):
        response = self.page(self.general)
        self.assertEqual({row["task"].pk for row in response.context["tasks_with_submissions"]}, {self.task.pk, self.expired.pk})
        filtered = self.page(self.general, task=self.empty_history.pk)
        self.assertEqual([row["task"].pk for row in filtered.context["tasks_with_submissions"]], [self.empty_history.pk])
        self.assertEqual(len(self.entry(filtered, self.empty_history)["not_submitted"]), 8)
        self.assertEqual(self.page(self.reviewer, task="forged").status_code, 404)

    def test_supervisor_without_assignments_has_zero_counts_and_no_student_data(self):
        response = self.page(self.no_groups)
        for row in response.context["tasks_with_submissions"]:
            self.assertEqual(row["submissions"], [])
            self.assertEqual(row["not_submitted"], [])
            self.assertEqual(row["group_names"], [])
        for student in self.students:
            self.assertNotContains(response, student.user.full_name)

    def test_roster_and_submissions_queries_do_not_grow_per_task(self):
        factory = RequestFactory()
        def query_count():
            request = factory.get(self.url)
            request.user = self.reviewer
            view = WeeklyTaskReviewView()
            view.setup(request)
            with CaptureQueriesContext(connection) as queries:
                context = view.get_context_data()
                for row in context["tasks_with_submissions"]:
                    for student in row["not_submitted"]:
                        _ = student.user.full_name, student.group.name
                    for submission in row["submissions"]:
                        _ = submission.participant.user.full_name, submission.participant.group.name
            return len(queries)
        before = query_count()
        for number in range(8):
            WeeklyTask.objects.create(title=f"مهمة تجميع {number}", description="اختبار", allowed_formats="text", due_date=self.task.due_date)
        self.assertEqual(query_count(), before)

    def test_file_and_text_are_in_one_escaped_preview_payload_with_accessible_dialog(self):
        response = self.page()
        markup = PreviewMarkup(response.content.decode())
        previews = [attrs for tag, attrs in markup.elements if tag == "button" and "preview-btn" in attrs.get("class", "").split()]
        both = next(attrs for attrs in previews if "preview-0.jpg" in attrs.get("data-url", ""))
        self.assertEqual(both["data-type"], "image")
        self.assertEqual(both["data-content"], self.submissions[0].text_content)
        self.assertEqual(both["aria-haspopup"], "dialog")
        dialogs = [attrs for tag, attrs in markup.elements if tag == "dialog"]
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0]["role"], "dialog")
        self.assertEqual(dialogs[0]["aria-modal"], "true")
        self.assertEqual(dialogs[0]["aria-labelledby"], "preview-title")
        self.assertEqual(dialogs[0]["aria-describedby"], "preview-help")
        self.assertFalse(any(tag == "img" and attrs.get("onerror") for tag, attrs in markup.elements))
        self.assertContains(response, "css/task-review-preview.css")
        self.assertContains(response, "js/task-review-preview.js")
        self.assertContains(response, 'id="modal-review-actions"')

    def test_read_only_preview_has_no_decision_forms(self):
        response = self.page()
        for value in ("accept", "reject", "return_for_revision"):
            self.assertNotContains(response, f'name="action" value="{value}"')
        self.assertContains(response, 'class="btn-inline preview-btn"')
        self.assertContains(response, "الاطلاع فقط")

    def test_delegated_pending_has_original_forms_but_nonpending_has_none(self):
        self.enable()
        response = self.page()
        pending = self.submission_row(response, self.submissions[0])
        for action in ("accept", "reject", "return_for_revision"):
            self.assertIn(f'name="action" value="{action}"', pending)
        self.assertIn('class="accept-form"', pending)
        self.assertIn('name="is_featured"', pending)
        for submission in self.submissions[1:4]:
            row = self.submission_row(response, submission)
            self.assertIn("preview-btn", row)
            self.assertNotIn('name="action"', row)
        late = self.submission_row(response, self.late_submission)
        self.assertIn('value="accept"', late)
        self.assertIn('value="reject"', late)
        self.assertNotIn('value="return_for_revision"', late)

    def test_missing_only_environment_still_appears_in_filter_options(self):
        for submission in self.submissions:
            if submission.participant.group_id == self.beta.pk:
                submission.delete()
        response = self.page()
        entry = self.entry(response, self.task)
        self.assertIn(self.beta.name, entry["group_names"])
        self.assertContains(response, f'value="{self.beta.name}" checked')

    def test_preview_featured_accept_uses_existing_post_awards_twelve_exactly_once(self):
        self.enable()
        self.client.force_login(self.reviewer)
        payload = {"action": "accept", "submission_id": self.submissions[0].pk, "is_featured": "on"}
        response = self.client.post(self.url, payload, follow=True)
        self.assertContains(response, "تم قبول التسليم كمتميز.")
        self.submissions[0].refresh_from_db()
        self.students[0].refresh_from_db()
        self.assertTrue(self.submissions[0].is_featured)
        self.assertTrue(self.submissions[0].points_awarded)
        self.assertEqual((self.students[0].points, self.students[0].miles, self.students[0].purchase_points), (12, 120, 12))
        repeat = self.client.post(self.url, payload, follow=True)
        self.assertContains(repeat, "لم يُحفظ قرار جديد")
        self.students[0].refresh_from_db()
        self.assertEqual(self.students[0].points, 12)
        self.assertEqual(PointsLedgerEntry.objects.count(), 1)

    def test_late_normal_and_featured_accept_keep_original_event_date(self):
        for user, featured, award in ((self.general, False, 10), (self.system, True, 12)):
            with self.subTest(role=user.role, featured=featured):
                # Separate synthetic row per case, not a reset of a real award.
                task = WeeklyTask.objects.create(title=f"قبول متأخر {user.pk}", description="اختبار", allowed_formats="text", due_date=self.expired.due_date)
                submission = TaskSubmission.objects.create(task=task, participant=self.students[1], text_content="نص")
                TaskSubmission.objects.filter(pk=submission.pk).update(submitted_at=self.original_date)
                self.client.force_login(user)
                response = self.client.post(self.url, {"action": "accept", "submission_id": submission.pk, **({"is_featured": "on"} if featured else {})})
                self.assertEqual(response.status_code, 302)
                submission.refresh_from_db()
                self.assertEqual(submission.status, "accepted")
                ledger = PointsLedgerEntry.objects.get(description__contains=task.title)
                self.assertEqual(ledger.points_delta, award)
                self.assertEqual(ledger.event_date, timezone.localdate(self.original_date))

    def test_revocation_after_open_preview_blocks_same_native_post(self):
        grant = self.enable()
        self.assertContains(self.page(), 'class="accept-form"')
        grant.enabled = False
        grant.save(update_fields=["enabled"])
        for action in ("accept", "reject"):
            with self.subTest(action=action):
                response = self.client.post(self.url, {"action": action, "submission_id": self.submissions[0].pk, "is_featured": "on"})
                self.assertEqual(response.status_code, 403)
                self.assertContains(response, "لم يُحفظ القرار", status_code=403)
                self.assertContains(response, "أُلغي تفويضك", status_code=403)
                self.assertNotContains(response, 'class="accept-form"', status_code=403)
                self.assert_unawarded(self.submissions[0])

    def test_foreign_preview_id_cannot_be_corrected_with_forged_post(self):
        self.enable()
        self.client.force_login(self.reviewer)
        response = self.client.post(self.url, {"action": "accept", "submission_id": self.submissions[-1].pk, "is_featured": "on"})
        self.assertEqual(response.status_code, 404)
        self.assert_unawarded(self.submissions[-1])

    def test_expired_session_redirect_and_csrf_failure_never_award(self):
        anonymous = self.client.post(self.url, {"action": "accept", "submission_id": self.submissions[0].pk})
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("accounts:login_supervisor"), anonymous.url)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.general)
        rejected = csrf_client.post(self.url, {"action": "accept", "submission_id": self.submissions[0].pk})
        self.assertEqual(rejected.status_code, 403)
        self.assert_unawarded(self.submissions[0])

    def test_native_form_with_real_csrf_token_succeeds(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.general)
        page = csrf_client.get(self.url)
        markup = PreviewMarkup(page.content.decode())
        token = next(attrs["value"] for tag, attrs in markup.elements if tag == "input" and attrs.get("name") == "csrfmiddlewaretoken")
        response = csrf_client.post(self.url, {"csrfmiddlewaretoken": token, "action": "accept", "submission_id": self.submissions[0].pk})
        self.assertEqual(response.status_code, 302)
        self.students[0].refresh_from_db()
        self.assertEqual(self.students[0].points, 10)

    def test_late_reject_and_return_deadline_stay_unchanged(self):
        self.client.force_login(self.general)
        forbidden_return = self.client.post(self.url, {"action": "return_for_revision", "submission_id": self.late_submission.pk, "revision_note": "أعدها"}, follow=True)
        self.assertContains(forbidden_return, "لا يمكن إعادة المهمة للتعديل")
        self.assert_unawarded(self.late_submission)
        rejected = self.client.post(self.url, {"action": "reject", "submission_id": self.late_submission.pk, "rejection_reason": "سبب واضح"}, follow=True)
        self.assertContains(rejected, "تم رفض التسليم")
        self.late_submission.refresh_from_db()
        self.assertEqual(self.late_submission.status, "rejected")
        self.assertEqual(self.late_submission.rejection_reason, "سبب واضح")
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_resubmitted_awarded_row_cannot_get_second_featured_grant(self):
        self.enable()
        self.client.force_login(self.reviewer)
        submission = self.submissions[4]
        self.assertTrue(submission.points_awarded)
        response = self.client.post(self.url, {"action": "accept", "submission_id": submission.pk, "is_featured": "on"})
        self.assertEqual(response.status_code, 302)
        self.students[4].refresh_from_db()
        self.assertEqual(self.students[4].points, 0)
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_historical_null_requires_existing_admin_choice_for_featured_accept(self):
        submission = self.submissions[0]
        TaskSubmission.objects.filter(pk=submission.pk).update(points_awarded=None)
        self.enable()
        response = self.page()
        row = self.submission_row(response, submission)
        self.assertNotIn('class="accept-form"', row)
        self.assertIn('value="reject"', row)
        denied = self.client.post(self.url, {"action": "accept", "submission_id": submission.pk, "is_featured": "on", "historical_award": "not_awarded"})
        self.assertEqual(denied.status_code, 403)
        self.assert_unawarded(submission)
        self.client.force_login(self.general)
        missing_choice = self.client.post(self.url, {"action": "accept", "submission_id": submission.pk, "is_featured": "on"}, follow=True)
        self.assertContains(missing_choice, "تحقق أولًا هل صُرفت نقاط")
        self.assert_unawarded(submission)
        row = self.submission_row(self.page(self.general), submission)
        self.assertIn('name="historical_award" required', row)
        response = self.client.post(self.url, {"action": "accept", "submission_id": submission.pk, "is_featured": "on", "historical_award": "not_awarded"})
        self.assertEqual(response.status_code, 302)
        self.students[0].refresh_from_db()
        self.assertEqual(self.students[0].points, 12)

    def test_historical_already_awarded_accepts_without_additional_points(self):
        submission = self.submissions[0]
        TaskSubmission.objects.filter(pk=submission.pk).update(points_awarded=None)
        self.client.force_login(self.system)
        self.client.post(self.url, {"action": "accept", "submission_id": submission.pk, "is_featured": "on", "historical_award": "already_awarded"})
        submission.refresh_from_db()
        self.assertEqual(submission.status, "accepted")
        self.assertTrue(submission.is_featured)
        self.assertTrue(submission.points_awarded)
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_assets_preserve_safe_text_native_post_and_mobile_name_wrapping_contract(self):
        javascript = (Path(settings.BASE_DIR) / "static/js/task-review-preview.js").read_text()
        stylesheet = (Path(settings.BASE_DIR) / "static/css/task-review-preview.css").read_text()
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("fetch(", javascript)
        self.assertIn("pre.textContent = text", javascript)
        self.assertIn("original.cloneNode(true)", javascript)
        self.assertNotIn("featured.name = 'is_featured'", javascript)
        self.assertNotIn("featured.type = 'submit'", javascript)
        self.assertNotIn("copy.querySelectorAll('.return-form').forEach(function (form) { form.remove(); })", javascript)
        self.assertIn("if (event.key === 'Enter') event.preventDefault();", javascript)
        template = (Path(settings.BASE_DIR) / "participants/templates/participants/weekly_task_review.html").read_text()
        self.assertRegex(template, r'<input[^>]*type="checkbox"[^>]*name="is_featured"[^>]*value="on"')
        self.assertIn('value="return_for_revision"', template)
        self.assertIn('value="cancel_grade"', template)
        self.assertNotIn('value="revise_grade"', template)
        response = self.page()
        self.assertContains(response, '/static/js/task-review-preview.js?v=20261107-cancel1')
        self.assertContains(response, '/static/css/task-review-preview.css?v=20261107-cancel1')
        self.assertIn("modal.showModal()", javascript)
        self.assertIn("modal.addEventListener('cancel'", javascript)
        self.assertIn("opener.focus()", javascript)
        self.assertIn("overflow-wrap: anywhere", stylesheet)
        self.assertIn("@media (max-width: 600px)", stylesheet)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", stylesheet)
        self.assertIn(".not-submitted-list li[data-participant][hidden]", stylesheet)
        self.assertNotIn(".not-submitted-list [hidden]", stylesheet)

    def test_review_page_is_private_and_not_cached(self):
        response = self.page()
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
