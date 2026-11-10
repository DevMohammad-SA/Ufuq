"""Task-filter regressions: run only with config.test_settings (local SQLite)."""
import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from .models import Group, Participant, TaskReviewPermission, TaskSubmission, WeeklyTask


class FilterMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class TaskFilterFixture:
    """Shared synthetic fixture without inheriting or duplicating test methods."""
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.general = User.objects.create_user(username="filter-general", role=Role.GENERAL_SUPERVISOR)
        cls.system = User.objects.create_user(username="filter-system", role=Role.SUPERADMIN)
        cls.reviewer = User.objects.create_user(username="filter-reviewer", role=Role.GROUP_SUPERVISOR)
        cls.no_groups = User.objects.create_user(username="filter-no-groups", role=Role.GROUP_SUPERVISOR)
        cls.alpha = Group.objects.create(name='بيئة ألف <اختبار>')
        cls.beta = Group.objects.create(name='بيئة باء "اختبار"')
        cls.foreign = Group.objects.create(name="بيئة خارج نطاق المشرف")
        cls.alpha.supervisor.add(cls.reviewer)
        cls.beta.supervisor.add(cls.reviewer)
        cls.students = {}
        states = ("pending", "accepted", "rejected", "returned", "missing", "inactive-submitted", "inactive-missing", "foreign-submitted", "foreign-missing")
        for i, state in enumerate(states):
            user = User.objects.create_user(
                username=f"filter-student-{i}", full_name=f"طالب {state} <script> & نص",
                role=Role.PARTICIPANT, is_active=not state.startswith("inactive"),
            )
            cls.students[state] = Participant.objects.create(
                user=user, group=cls.foreign if state.startswith("foreign") else (cls.alpha if i % 2 == 0 else cls.beta),
                academic_stage="grade_7", phone=f"private-phone-{i}", guardian_phone=f"private-guardian-{i}",
                points=31, miles=310, purchase_points=21,
            )
        cls.current = WeeklyTask.objects.create(
            title="مهمة كل الحالات", description="وصف مهمة حالي", allowed_formats="image,text",
            require_all_formats=True, is_active=True, due_date=timezone.localdate() + datetime.timedelta(days=5),
        )
        cls.expired_pending = cls.old_task("مهمة منتهية معلّقة", active=True)
        cls.old_rejected = cls.old_task("مهمة موقوفة مرفوضة")
        cls.old_accepted = cls.old_task("مهمة منتهية مقبولة", active=True)
        cls.foreign_only = cls.old_task("مهمة قديمة بتسليم خارج النطاق")
        cls.empty_old = cls.old_task("مهمة سابقة بلا تسليمات")
        cls.submissions = {}
        for state in ("pending", "accepted", "rejected", "returned", "inactive-submitted", "foreign-submitted"):
            cls.submissions[state] = TaskSubmission.objects.create(
                task=cls.current, participant=cls.students[state],
                status="rejected" if state == "foreign-submitted" else ("pending" if state == "inactive-submitted" else state),
                text_content=f'عمل {state} <img src=x onerror="unsafe()"> & نص',
                file=f"task_submissions/{state}.jpg", points_awarded=False,
                is_featured=state == "accepted", revision_note="ملاحظة التعديل" if state == "returned" else "",
                rejection_reason="سبب الرفض <script>" if state in ("rejected", "foreign-submitted") else "",
            )
        cls.late_pending = TaskSubmission.objects.create(task=cls.expired_pending, participant=cls.students["pending"], text_content="تسليم منتهٍ معلق")
        cls.late_rejected = TaskSubmission.objects.create(task=cls.old_rejected, participant=cls.students["rejected"], status="rejected", text_content="تسليم موقوف مرفوض")
        cls.late_accepted = TaskSubmission.objects.create(task=cls.old_accepted, participant=cls.students["accepted"], status="accepted", text_content="تسليم قديم مقبول", is_featured=True)
        cls.foreign_submission = TaskSubmission.objects.create(task=cls.foreign_only, participant=cls.students["foreign-submitted"], status="rejected", text_content="نص خارج نطاق البيئات")
        cls.url = reverse("participants:weekly_task_review")

    @classmethod
    def old_task(cls, title, active=False):
        return WeeklyTask.objects.create(
            title=title, description="وصف اختبار أرشيف", allowed_formats="text", is_active=active,
            due_date=timezone.localdate() - datetime.timedelta(days=5),
        )

    def page(self, user=None, **query):
        self.client.force_login(user or self.reviewer)
        return self.client.get(self.url, query)

    @staticmethod
    def entry(response, task):
        return next(row for row in response.context["tasks_with_submissions"] if row["task"].pk == task.pk)

    @staticmethod
    def task_ids(response):
        return {row["task"].pk for row in response.context["tasks_with_submissions"]}

    def enable_review(self):
        return TaskReviewPermission.objects.create(supervisor=self.reviewer, enabled=True, granted_by=self.general)


class TaskStatusFilterTests(TaskFilterFixture, TestCase):
    def test_default_legacy_task_selection_is_unchanged(self):
        expected = {self.current.pk, self.expired_pending.pk}
        for manager in (self.general, self.system):
            with self.subTest(role=manager.role):
                response = self.page(manager)
                self.assertEqual(self.task_ids(response), expected)
                self.assertTrue(response.context["show_not_submitted"])
                self.assertEqual(response.context["selected_status"], "all")
        group_response = self.page()
        self.assertEqual(self.task_ids(group_response), {self.current.pk, self.expired_pending.pk, self.old_rejected.pk, self.old_accepted.pk, self.foreign_only.pk, self.empty_old.pk})
        self.assertTrue(group_response.context["can_view_student_detail"])

    def test_all_status_filters_select_matching_scoped_tasks_even_expired_or_disabled(self):
        expectations = {
            "pending": {self.current.pk, self.expired_pending.pk},
            "rejected": {self.current.pk, self.old_rejected.pk},
            "accepted": {self.current.pk, self.old_accepted.pk},
            "returned": {self.current.pk},
        }
        for status, task_ids in expectations.items():
            for user in (self.reviewer, self.general, self.system):
                with self.subTest(status=status, role=user.role):
                    response = self.page(user, status=status)
                    allowed = task_ids | ({self.foreign_only.pk} if status == "rejected" and user != self.reviewer else set())
                    self.assertEqual(self.task_ids(response), allowed)
                    for entry in response.context["tasks_with_submissions"]:
                        self.assertTrue(entry["submissions"])
                        self.assertEqual({s.status for s in entry["submissions"]}, {status})
                    self.assertNotContains(response, "من لم يسلّموا")
                    self.assertNotContains(response, 'data-count="not-submitted"')

    def test_scope_still_covers_both_assigned_groups_and_excludes_foreign_rows(self):
        response = self.page(status="pending", group=self.foreign.pk, participant=self.students["foreign-submitted"].pk)
        current = self.entry(response, self.current)
        self.assertEqual({s.participant_id for s in current["submissions"]}, {self.students["pending"].pk, self.students["inactive-submitted"].pk})
        response = self.page(status="rejected")
        for entry in response.context["tasks_with_submissions"]:
            self.assertTrue(all(s.participant.group_id in (self.alpha.pk, self.beta.pk) for s in entry["submissions"]))
        self.assertNotContains(response, "foreign-submitted.jpg")
        self.assertNotContains(response, self.foreign.name)

    def test_not_submitted_context_uses_all_states_then_filters_visible_rows(self):
        response = self.page(status="pending", task=self.current.pk)
        entry = self.entry(response, self.current)
        self.assertEqual({p.pk for p in entry["not_submitted"]}, {self.students["missing"].pk})
        submitted = {self.students[state].pk for state in ("pending", "accepted", "rejected", "returned", "inactive-submitted")}
        self.assertTrue(submitted.isdisjoint({p.pk for p in entry["not_submitted"]}))
        self.assertNotContains(response, "من لم يسلّموا")
        all_response = self.page(task=self.current.pk)
        self.assertEqual({p.pk for p in self.entry(all_response, self.current)["not_submitted"]}, {self.students["missing"].pk})
        self.assertContains(all_response, "من لم يسلّموا")

    def test_numeric_task_intersects_status_scope_and_never_overrides_it(self):
        matched = self.page(status="rejected", task=self.old_rejected.pk)
        self.assertEqual(self.task_ids(matched), {self.old_rejected.pk})
        for task in (self.foreign_only, self.old_accepted, self.empty_old):
            with self.subTest(task=task.pk):
                response = self.page(status="rejected", task=task.pk)
                self.assertEqual(response.context["tasks_with_submissions"], [])
                self.assertContains(response, "لا توجد تسليمات مطابقة لحالة «المرفوضة»")
                self.assertNotContains(response, "لا توجد تسليمات بعد لهذه المهمة")
                self.assertNotContains(response, 'class="card task-review-card"')
                markup = FilterMarkup(response.content.decode())
                self.assertTrue(any(tag == "input" and attrs.get("name") == "task" and attrs.get("value") == str(task.pk) for tag, attrs in markup.elements))
                self.assertTrue(any(tag == "option" and attrs.get("value") == "rejected" and "selected" in attrs for tag, attrs in markup.elements))

    def test_all_status_explicit_task_preserves_access_to_old_task_without_pending(self):
        for user in (self.general, self.reviewer):
            with self.subTest(role=user.role):
                response = self.page(user, status="all", task=self.empty_old.pk)
                self.assertEqual(self.task_ids(response), {self.empty_old.pk})
                self.assertEqual(self.entry(response, self.empty_old)["submissions"], [])
                self.assertContains(response, "من لم يسلّموا")

    def test_unknown_status_normalizes_to_all_without_echoing_or_narrowing(self):
        for status in ("unknown", "<script>alert(1)</script>", "pending OR 1=1", ""):
            with self.subTest(status=status):
                response = self.page(self.general, status=status)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["selected_status"], "all")
                self.assertEqual(response.context["status_label"], "الكل")
                self.assertEqual(self.task_ids(response), {self.current.pk, self.expired_pending.pk})
                self.assertContains(response, "الحالة المعروضة: الكل")
                if status:
                    self.assertNotContains(response, status)

    def test_invalid_raw_task_keeps_404_regardless_of_status(self):
        for task in ("unsafe", "1 OR 1=1", "https://example.invalid", "-1", "1.5"):
            for status in ("all", "pending", "not-a-status"):
                with self.subTest(task=task, status=status):
                    self.assertEqual(self.page(task=task, status=status).status_code, 404)
        self.assertEqual(self.page(task="999999", status="rejected").context["tasks_with_submissions"], [])

    def test_no_assigned_groups_gets_filtered_empty_not_false_missing(self):
        response = self.page(self.no_groups, status="pending")
        self.assertEqual(response.context["tasks_with_submissions"], [])
        self.assertContains(response, "لا توجد تسليمات مطابقة")
        self.assertNotContains(response, "من لم يسلّموا")

    def test_get_form_precedes_creation_and_retains_task_and_status(self):
        response = self.page(self.general, status="accepted", task=self.old_accepted.pk)
        html = response.content.decode()
        self.assertLess(html.index('class="task-status-filter"'), html.index('value="create_task"'))
        markup = FilterMarkup(html)
        self.assertTrue(any(tag == "form" and attrs.get("method") == "get" and attrs.get("action") == self.url for tag, attrs in markup.elements))
        for value in ("accepted", str(self.old_accepted.pk)):
            self.assertTrue(any(tag == "option" and attrs.get("value") == value and "selected" in attrs for tag, attrs in markup.elements))
        for label in ("الكل", "تحت المعالجة", "المرفوضة", "المقبولة", "المعادة للتعديل"):
            self.assertContains(response, label)
        self.assertContains(response, "min-height:44px")
        self.assertContains(response, 'dir="rtl"')

    def test_management_links_retain_filters_without_external_return_parameters(self):
        response = self.page(self.general, status="rejected", task=self.old_rejected.pk, edit_task=self.current.pk, next="https://example.invalid")
        markup = FilterMarkup(response.content.decode())
        links = [attrs["href"] for tag, attrs in markup.elements if tag == "a" and attrs.get("href", "").startswith(self.url)]
        cancel_url = self.url + "?" + urlencode({"status": "rejected", "task": self.old_rejected.pk})
        self.assertIn(cancel_url, links)
        self.assertTrue(any(parse_qs(urlsplit(link).query) == {"status": ["rejected"], "task": [str(self.old_rejected.pk)], "edit_task": [str(self.current.pk)]} for link in links))
        self.assertTrue(any(parse_qs(urlsplit(link).query) == {"status": ["rejected"], "task": [str(self.current.pk)]} for link in links))
        self.assertTrue(all("example.invalid" not in link for link in links))

    def test_visible_group_filters_and_counts_use_only_visible_status_rows(self):
        response = self.page(status="pending", task=self.current.pk)
        entry = self.entry(response, self.current)
        self.assertEqual(entry["group_names"], sorted([self.alpha.name, self.beta.name]))
        self.assertContains(response, 'data-count="submitted">2</span>', html=False)
        for submission in entry["submissions"]:
            self.assertContains(response, f'data-submission="{submission.pk}"')
        self.assertNotContains(response, f'data-submission="{self.submissions["accepted"].pk}"')
        self.assertContains(response, "&lt;اختبار&gt;")
        self.assertContains(response, "&quot;اختبار&quot;")
        self.assertNotContains(response, '<img src=x onerror="unsafe()">')
        rejected = self.page(status="rejected", task=self.current.pk)
        self.assertEqual(self.entry(rejected, self.current)["group_names"], [self.alpha.name])

    def test_featured_rows_are_in_accepted_filter_without_alternate_status(self):
        response = self.page(status="accepted")
        self.assertEqual({s.pk for entry in response.context["tasks_with_submissions"] for s in entry["submissions"]}, {self.submissions["accepted"].pk, self.late_accepted.pk})
        self.assertTrue(all(s.is_featured for entry in response.context["tasks_with_submissions"] for s in entry["submissions"]))

    def test_accept_preserves_only_valid_get_filters_and_default_redirect(self):
        self.enable_review()
        self.client.force_login(self.reviewer)
        query = urlencode({"status": "pending", "task": self.current.pk, "next": "https://example.invalid", "group": self.foreign.pk})
        response = self.client.post(self.url + "?" + query, {"action": "accept", "submission_id": self.submissions["pending"].pk, "is_featured": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, self.url)
        self.assertEqual(parse_qs(urlsplit(response.url).query), {"status": ["pending"], "task": [str(self.current.pk)]})
        self.submissions["pending"].refresh_from_db()
        self.assertTrue(self.submissions["pending"].is_featured)
        default = self.client.post(self.url, {"action": "reject", "submission_id": self.submissions["inactive-submitted"].pk})
        self.assertEqual(default.url, self.url)

    def test_invalid_redirect_filters_and_external_return_urls_are_dropped(self):
        self.client.force_login(self.general)
        query = urlencode({"status": "https://example.invalid", "task": "1&next=https://example.invalid", "next": "//example.invalid"})
        response = self.client.post(self.url + "?" + query, {"action": "reject", "submission_id": self.late_pending.pk})
        self.assertEqual(response.url, self.url)

    def test_cancellation_keeps_existing_task_redirect_and_valid_status(self):
        self.client.force_login(self.general)
        submission = self.late_rejected
        response = self.client.post(self.url + "?" + urlencode({"status": "rejected", "task": self.current.pk}), {
            "action": "cancel_grade", "submission_id": submission.pk,
            "review_version": submission.grade_revision_token, "confirm": "yes",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query), {"status": ["rejected"], "task": [str(self.old_rejected.pk)]})
        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")
        default_submission = self.late_accepted
        default = self.client.post(self.url, {
            "action": "cancel_grade", "submission_id": default_submission.pk,
            "review_version": default_submission.grade_revision_token, "confirm": "yes",
        })
        self.assertEqual(default.url, f"{self.url}?task={self.old_accepted.pk}")
