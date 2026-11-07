"""Student task history regressions using synthetic, isolated test data.

Preview fixtures store filename strings only: no media is opened or uploaded.
POST regressions use the real submission endpoint with text submissions. Run
with config.test_settings (or its isolated PostgreSQL counterpart), not production.
"""

import datetime
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core.paginator import Page
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.html import escape

from accounts.models import Role, User
from participants.forms import TaskSubmissionForm
from participants.models import (
    Group,
    Participant,
    PointsLedgerEntry,
    PointsResetSnapshot,
    TaskGradeRevision,
    TaskSubmission,
    WeeklyTask,
)
from participants.views import TaskSubmissionView


class MarkupNode:
    """Small dependency-free HTML tree for structural, not copy-sensitive checks."""

    def __init__(self, tag, attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    def elements(self, tag=None):
        result = []
        for child in self.children:
            if isinstance(child, MarkupNode):
                if tag is None or child.tag == tag:
                    result.append(child)
                result.extend(child.elements(tag))
        return result

    def text(self):
        return "".join(
            child.text() if isinstance(child, MarkupNode) else child
            for child in self.children
        )


class StudentMarkup(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = MarkupNode("document")
        self.current = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = MarkupNode(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(MarkupNode(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


class StudentTaskHistoryTests(TestCase):
    HISTORY_TIME = datetime.datetime(2025, 9, 14, 9, 35, tzinfo=datetime.timezone.utc)
    PAST_DUE = datetime.date(2000, 1, 1)
    FUTURE_DUE = datetime.date(2099, 1, 1)

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name="بيئة تاريخ الطالب المصطنعة")
        cls.foreign_group = Group.objects.create(name="بيئة خارج تاريخ الطالب")
        cls.user = User.objects.create_user(
            username="history-owner", full_name="صاحب تسليمات الاختبار",
            role=Role.PARTICIPANT, must_set_password=False,
        )
        cls.student = Participant.objects.create(
            user=cls.user, group=cls.group, academic_stage="grade_7",
            points=37, miles=370, purchase_points=29,
        )
        cls.same_group_user = User.objects.create_user(
            username="history-same-group", full_name="اسم طالب آخر في نفس البيئة",
            role=Role.PARTICIPANT,
        )
        cls.same_group_student = Participant.objects.create(
            user=cls.same_group_user, group=cls.group, academic_stage="grade_7",
        )
        cls.foreign_user = User.objects.create_user(
            username="history-foreign", full_name="اسم طالب آخر في بيئة أخرى",
            role=Role.PARTICIPANT,
        )
        cls.foreign_student = Participant.objects.create(
            user=cls.foreign_user, group=cls.foreign_group, academic_stage="grade_7",
        )
        cls.staff = []
        for role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.SUPERADMIN):
            cls.staff.append(User.objects.create_user(
                username=f"history-{role}", role=role, is_active=True,
            ))
        cls.group.supervisor.add(cls.staff[0])
        cls.url = reverse("participants:task_submission")
        cls.login_url = reverse("accounts:login_participant")

    def setUp(self):
        self.client.force_login(self.user)

    def task(self, title="مهمة محفوظة مصطنعة", **fields):
        defaults = {
            "title": title, "description": f"وصف مستقل: {title}",
            "due_date": self.PAST_DUE, "is_active": False,
            "allowed_formats": "text",
        }
        defaults.update(fields)
        return WeeklyTask.objects.create(**defaults)

    def submission(self, task, participant=None, submitted_at=None, **fields):
        defaults = {"text_content": f"عمل محفوظ: {task.title}"}
        defaults.update(fields)
        submission = TaskSubmission.objects.create(
            task=task, participant=participant or self.student, **defaults,
        )
        # auto_now_add is deliberately overridden without sleeps or a clock race.
        TaskSubmission.objects.filter(pk=submission.pk).update(
            submitted_at=submitted_at or self.HISTORY_TIME,
        )
        submission.refresh_from_db()
        return submission

    def page(self, **query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["submission_history"], Page)
        return response

    @staticmethod
    def history_ids(response):
        return [row.pk for row in response.context["submission_history"]]

    @staticmethod
    def current_ids(response):
        return [row["task"].pk for row in response.context["tasks_data"]]

    def section(self, response, element_id):
        document = StudentMarkup(response.content.decode())
        matches = [
            node for node in document.root.elements()
            if node.attrs.get("id") == element_id
        ]
        self.assertEqual(len(matches), 1, f"Expected one #{element_id}")
        target = matches[0]
        # A fragment can identify either the region itself or its heading.
        # Follow aria-labelledby instead of requiring one particular layout.
        if target.tag in ("h1", "h2", "h3", "h4"):
            node = target.parent
            while node is not None:
                if element_id in node.attrs.get("aria-labelledby", "").split():
                    return node
                node = node.parent
        return target

    def preview(self, section, submission):
        candidates = section.elements("details")
        if submission.file:
            candidates = [
                node for node in candidates
                if any(
                    child.attrs.get("href") == submission.file.url
                    for child in node.elements("a")
                )
            ]
        else:
            candidates = [
                node for node in candidates
                if any(child.text() == submission.text_content for child in node.elements("pre"))
            ]
        self.assertEqual(len(candidates), 1, "One native preview per submission")
        preview = candidates[0]
        summaries = preview.elements("summary")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].text().strip(), "معاينة تسليمي")
        self.assertNotIn("open", preview.attrs, "Preview should be collapsed initially")
        return preview

    def card(self, section, task):
        headings = [
            node for node in section.elements()
            if node.tag in ("h2", "h3", "h4") and node.text().strip() == task.title
        ]
        self.assertEqual(len(headings), 1)
        node = headings[0].parent
        while node is not section and not node.elements("details"):
            node = node.parent
        self.assertIsNot(node, section, "Metadata and preview belong to one task card")
        return node

    def state(self):
        """Snapshot domain rows, including historical award and audit fields."""
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (
                Participant, WeeklyTask, TaskSubmission, PointsLedgerEntry,
                PointsResetSnapshot, TaskGradeRevision,
            )
        }

    def add_paginated_history(self):
        rows = []
        for index in range(11):
            # Creation order and submission order differ; two dates tie on purpose.
            offset = (index * 3) % 10
            rows.append(self.submission(
                self.task(f"تسليم مرقم {index}"),
                submitted_at=self.HISTORY_TIME + datetime.timedelta(days=offset),
            ))
        return sorted(rows, key=lambda row: (row.submitted_at, row.pk), reverse=True)

    def test_history_includes_own_expired_and_inactive_submissions_in_every_status(self):
        own = []
        for active in (True, False):
            for status in TaskSubmission.Status.values:
                task = self.task(
                    f"أرشيف {active} {status}", is_active=active,
                    due_date=self.PAST_DUE if active else self.FUTURE_DUE,
                )
                own.append(self.submission(
                    task, status=status,
                    rejection_reason="سبب رفض محفوظ" if status == "rejected" else "",
                    revision_note="ملاحظة تعديل محفوظة" if status == "returned" else "",
                ))
        response = self.page()
        self.assertEqual(set(self.history_ids(response)), {row.pk for row in own})
        self.assertEqual(response.context["submission_history"].paginator.count, 8)
        self.assertEqual(self.current_ids(response), [])
        history = self.section(response, "student-task-history")
        for row in own:
            card = self.card(history, row.task)
            self.assertIn(row.task.description, card.text())
            self.assertIn(row.get_status_display(), card.text())
            self.assertIn("2025", card.text(), "Render submitted_at, not the task deadline")
            timestamps = [
                datetime.datetime.fromisoformat(node.attrs["datetime"])
                for node in card.elements("time")
                if "T" in node.attrs.get("datetime", "")
            ]
            self.assertIn(row.submitted_at, timestamps)
            if row.rejection_reason:
                self.assertIn(row.rejection_reason, card.text())
            if row.revision_note:
                self.assertIn(row.revision_note, card.text())
            self.preview(card, row)

    def test_history_never_includes_unsubmitted_past_or_inactive_future_tasks(self):
        pending = self.submission(self.task("تسليم سابق ما زال قيد المراجعة", is_active=True))
        hidden = [
            self.task("منتهية دون تسليم", is_active=True),
            self.task("موقوفة مستقبلية دون تسليم", due_date=self.FUTURE_DUE),
            self.task("موقوفة منتهية دون تسليم"),
        ]
        response = self.page()
        self.assertEqual(self.history_ids(response), [pending.pk])
        for task in hidden:
            self.assertNotContains(response, task.title)

    def test_foreign_same_group_and_other_group_submissions_never_expand_context(self):
        own_task = self.task("مهمة مشتركة ذات عمل خاص لكل طالب")
        own = self.submission(own_task)
        forbidden = []
        for student in (self.same_group_student, self.foreign_student):
            shared = self.submission(
                own_task, participant=student,
                file=f"task_submissions/secret-shared-{student.pk}.pdf",
                text_content=f"نص سري مشترك للطالب {student.pk}",
            )
            exclusive_task = self.task(f"عنوان خاص بالطالب الآخر {student.pk}")
            exclusive = self.submission(
                exclusive_task, participant=student, reopened_for_resubmission=True,
                file=f"task_submissions/secret-exclusive-{student.pk}.mp4",
                text_content=f"نص سري مستقل للطالب {student.pk}",
            )
            forbidden.extend((student.user.full_name, shared.file.name, shared.text_content,
                              exclusive.task.title, exclusive.file.name, exclusive.text_content))
            response = self.page(
                participant=student.pk, participant_id=student.pk, user_id=student.user_id,
                student_id=student.pk, submission=exclusive.pk, submission_id=shared.pk,
                task=exclusive.task_id, group=student.group_id, status="all",
            )
            self.assertEqual(self.history_ids(response), [own.pk])
            self.assertEqual(self.current_ids(response), [])
            self.assertEqual(response.context["submission_history"].paginator.count, 1)
            for value in forbidden:
                self.assertNotContains(response, value)

    def test_active_tasks_keep_source_order_before_own_reopened_past_without_duplicates(self):
        later = self.task("نشطة بموعد لاحق", is_active=True, due_date=self.FUTURE_DUE)
        earlier = self.task("نشطة اليوم", is_active=True, due_date=datetime.date.today())
        recent = self.submission(
            later, status=TaskSubmission.Status.ACCEPTED, reopened_for_resubmission=True,
            submitted_at=self.HISTORY_TIME + datetime.timedelta(days=50),
        )
        past_task = self.task("سابقة أعيد فتحها لهذا الطالب")
        past = self.submission(past_task, reopened_for_resubmission=True)
        archived = self.submission(self.task("سابقة غير معاد فتحها"))
        with patch.object(WeeklyTask, "get_active_tasks", wraps=WeeklyTask.get_active_tasks) as source:
            response = self.page()
        source.assert_called()
        self.assertEqual(self.current_ids(response), [earlier.pk, later.pk, past_task.pk])
        self.assertEqual(self.history_ids(response), [archived.pk])
        entries = {row["task"].pk: row for row in response.context["tasks_data"]}
        self.assertIsNone(entries[earlier.pk]["existing_submission"])
        self.assertEqual(entries[later.pk]["existing_submission"].pk, recent.pk)
        self.assertEqual(entries[past_task.pk]["existing_submission"].pk, past.pk)
        for entry in entries.values():
            self.assertIsInstance(entry["form"], TaskSubmissionForm)
        html = response.content.decode()
        self.assertLess(html.index('id="student-current-tasks"'), html.index('id="student-task-history"'))
        current = self.section(response, "student-current-tasks")
        history = self.section(response, "student-task-history")
        for row in (recent, past):
            self.assertIn(row.task.title, current.text())
            self.assertNotIn(row.task.title, history.text())
            self.preview(current, row)
        self.assertLess(current.text().index(earlier.title), current.text().index(past_task.title))
        self.assertLess(current.text().index(later.title), current.text().index(past_task.title))

    def test_recently_submitted_active_tasks_remain_current_and_not_in_history(self):
        active = self.task("مهمة نشطة تم تسليمها الآن", is_active=True, due_date=self.FUTURE_DUE)
        saved = self.submission(active, status=TaskSubmission.Status.ACCEPTED)
        old = self.submission(self.task("مهمة مؤرشفة مستقلة"))
        response = self.page()
        self.assertEqual(self.current_ids(response), [active.pk])
        self.assertEqual(self.history_ids(response), [old.pk])
        entry = response.context["tasks_data"][0]
        self.assertEqual(entry["existing_submission"].pk, saved.pk)
        self.assertIsNone(entry["form"])
        self.preview(self.section(response, "student-current-tasks"), saved)

    def test_no_active_tasks_does_not_hide_history(self):
        past = self.submission(self.task())
        response = self.page()
        self.assertEqual(self.current_ids(response), [])
        self.assertEqual(self.history_ids(response), [past.pk])
        self.assertIn(past.task.title, self.section(response, "student-task-history").text())

    def test_own_reopened_inactive_future_task_is_current_without_hiding_other_history(self):
        reopened_task = self.task("موقوفة فتحت لهذا الطالب", due_date=self.FUTURE_DUE)
        reopened = self.submission(reopened_task, reopened_for_resubmission=True)
        archived = self.submission(self.task("عمل سابق يبقى في الأرشيف"))
        response = self.page()
        self.assertEqual(self.current_ids(response), [reopened_task.pk])
        self.assertEqual(self.history_ids(response), [archived.pk])
        entry = response.context["tasks_data"][0]
        self.assertEqual(entry["existing_submission"].pk, reopened.pk)
        self.assertIsInstance(entry["form"], TaskSubmissionForm)
        self.preview(self.section(response, "student-current-tasks"), reopened)

    def test_empty_history_has_an_explicit_empty_state_and_both_sections(self):
        response = self.page()
        self.assertEqual(self.history_ids(response), [])
        self.assertEqual(response.context["submission_history"].paginator.count, 0)
        self.section(response, "student-current-tasks")
        history = self.section(response, "student-task-history")
        self.assertEqual(history.elements("details"), [])
        self.assertTrue(any(
            node.text().strip() for node in history.elements("p")
        ), "Explain the empty history without relying on an exact Arabic caption")

    def test_pagination_uses_ten_rows_and_latest_submission_then_pk_without_sleep(self):
        expected = self.add_paginated_history()
        current_task = self.task("نشطة لا تحتسب في صفحات الأرشيف", is_active=True, due_date=self.FUTURE_DUE)
        self.submission(current_task)
        first = self.page()
        second = self.page(history_page=2)
        self.assertEqual(self.history_ids(first), [row.pk for row in expected[:10]])
        self.assertEqual(self.history_ids(second), [expected[10].pk])
        self.assertEqual(first.context["submission_history"].paginator.count, 11)
        self.assertEqual(first.context["submission_history"].paginator.num_pages, 2)
        self.assertEqual(first.context["submission_history"].paginator.per_page, 10)
        self.assertTrue(first.context["submission_history"].has_next())
        self.assertTrue(second.context["submission_history"].has_previous())
        self.assertEqual(self.current_ids(second), [current_task.pk])
        first_history = self.section(first, "student-task-history")
        self.assertEqual(len(first_history.elements("details")), 10)
        self.assertEqual(len(self.section(second, "student-task-history").elements("details")), 1)
        links = [parse_qs(urlsplit(node.attrs.get("href", "")).query) for node in first_history.elements("a")]
        self.assertTrue(any(query.get("history_page") == ["2"] for query in links))
        for row in expected[:10]:
            self.assertNotContains(second, row.task.title)
        self.assertNotContains(first, expected[10].task.title)

    def test_invalid_history_page_is_safe_and_uses_paginator_get_page_fallback(self):
        expected = self.add_paginated_history()
        for value, number, ids in (
            ("not-a-page", 1, [row.pk for row in expected[:10]]),
            ("0", 2, [expected[-1].pk]),
            ("-1", 2, [expected[-1].pk]),
            ("999999", 2, [expected[-1].pk]),
        ):
            with self.subTest(history_page=value):
                response = self.page(history_page=value)
                self.assertEqual(response.context["submission_history"].number, number)
                self.assertEqual(self.history_ids(response), ids)

    def test_history_page_task_relations_are_eager_loaded_and_only_one_page_is_materialized(self):
        self.add_paginated_history()
        request = RequestFactory().get(self.url, {"history_page": "1"})
        request.user = self.user
        view = TaskSubmissionView()
        view.setup(request)
        context = view.get_context_data()
        page = context["submission_history"]
        self.assertIsInstance(page, Page)
        self.assertEqual(page.paginator.count, 11)
        with self.assertNumQueries(1):
            rows = list(page.object_list)
        self.assertEqual(len(rows), 10)
        with self.assertNumQueries(0):
            for row in rows:
                self.assertTrue(row.task.title)
                self.assertTrue(row.task.description)

    def test_anonymous_get_and_post_redirect_to_participant_login(self):
        self.client.logout()
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(self.url)
                self.assertRedirects(
                    response, f"{self.login_url}?next={self.url}",
                    fetch_redirect_response=False,
                )

    def test_active_staff_roles_are_forbidden_for_both_get_and_post(self):
        for actor in self.staff:
            self.client.force_login(actor)
            for method in (self.client.get, self.client.post):
                with self.subTest(role=actor.role, method=method.__name__):
                    self.assertEqual(method(self.url).status_code, 403)

    def test_inactive_participant_forced_session_cannot_read_or_submit(self):
        self.submission(self.task("تسليم لا يراه الحساب المعطل"))
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.user.refresh_from_db()
        self.client.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")
        for method in (self.client.get, self.client.post):
            response = method(self.url)
            self.assertRedirects(
                response, f"{self.login_url}?next={self.url}",
                fetch_redirect_response=False,
            )
            self.assertNotIn("submission_history", response.context or {})
        # Also guard the role check if an authentication backend presents the user.
        request = RequestFactory().get(self.url)
        request.user = self.user
        view = TaskSubmissionView()
        view.setup(request)
        self.assertFalse(view.test_func())

    def test_page_is_private_and_no_store_even_for_history_pagination(self):
        self.add_paginated_history()
        for query in ({}, {"history_page": 2}):
            response = self.page(**query)
            directives = {part.strip().lower() for part in response["Cache-Control"].split(",")}
            self.assertIn("private", directives)
            self.assertIn("no-store", directives)
            self.assertNotIn("public", directives)

    def test_get_preview_and_pagination_never_write_domain_rows_or_awards(self):
        rows = self.add_paginated_history()
        awarded = rows[0]
        TaskSubmission.objects.filter(pk=awarded.pk).update(
            file="task_submissions/unchanged-awarded.PDF", status="accepted",
            is_featured=True, points_awarded=True, award_value=12,
            reviewed_by=self.staff[1], reviewed_at=self.HISTORY_TIME,
        )
        PointsLedgerEntry.objects.create(
            participant=self.student, points_delta=12,
            source=PointsLedgerEntry.Source.WEEKLY_TASK,
            description="سجل مصطنع قائم لا يتغير", granted_by=self.staff[1],
        )
        PointsResetSnapshot.objects.create(
            participant=self.student, points_before_reset=37, reset_by=self.staff[1],
        )
        TaskGradeRevision.objects.create(
            submission=awarded, old_status="accepted", new_status="accepted",
            old_featured=False, new_featured=True, old_award=10, new_award=12,
            reason="تدقيق مصطنع سابق", revised_by=self.staff[1],
        )
        before = self.state()
        with CaptureQueriesContext(connection) as queries:
            for query in ({}, {"history_page": 2}, {"submission_id": awarded.pk, "action": "reopen"}):
                self.page(**query)
        writes = [
            query["sql"] for query in queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            and "participants_" in query["sql"].lower()
        ]
        self.assertEqual(writes, [])
        self.assertEqual(self.state(), before)

    def test_history_contains_no_upload_forms_grading_actions_or_management_links(self):
        for status in TaskSubmission.Status.values:
            self.submission(self.task(f"اطلاع فقط {status}"), status=status)
        # A current upload form may exist, but must never be inside the archive.
        self.task("نموذج رفع في القسم الحالي فقط", is_active=True, due_date=self.FUTURE_DUE)
        history = self.section(self.page(), "student-task-history")
        for tag in ("form", "input", "textarea", "select"):
            self.assertEqual(history.elements(tag), [], f"No {tag} in read-only history")
        self.assertFalse(any(
            node.attrs.get("type") == "submit" for node in history.elements("button")
        ))
        self.assertFalse(any(
            node.attrs.get("name") in {"action", "is_featured", "grade", "submission_id"}
            for node in history.elements()
        ))
        forbidden_urls = (
            reverse("participants:weekly_task_review"),
            reverse("participants:tasks_archive"),
            reverse("participants:task_review_permissions"),
        )
        for node in history.elements("a"):
            self.assertNotIn(urlsplit(node.attrs.get("href", "")).path, forbidden_urls)

    def test_featured_accepted_history_has_a_distinct_featured_marker(self):
        saved = self.submission(
            self.task("عمل حصل على اثنتي عشرة نقطة"), status="accepted", is_featured=True,
            points_awarded=True, award_value=12,
        )
        history = self.section(self.page(), "student-task-history")
        card = self.card(history, saved.task)
        self.assertTrue(
            "مميز" in card.text() or "متمي" in card.text(),
            "Show a featured marker without prescribing its full caption",
        )
        self.preview(card, saved)

    def file_preview(self, filename, formats):
        saved = self.submission(
            self.task(f"معاينة {filename}", allowed_formats=formats),
            file=f"task_submissions/{filename}", text_content="",
        )
        preview = self.preview(self.section(self.page(), "student-task-history"), saved)
        for link in preview.elements("a"):
            if link.attrs.get("target") == "_blank":
                self.assertTrue({"noopener", "noreferrer"}.issubset(
                    set(link.attrs.get("rel", "").split()),
                ))
        return saved, preview

    def test_image_preview_uses_lazy_image_and_file_link(self):
        saved, preview = self.file_preview("student-image.JPG", "image")
        images = preview.elements("img")
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].attrs.get("src"), saved.file.url)
        self.assertEqual(images[0].attrs.get("loading"), "lazy")
        self.assertTrue(images[0].attrs.get("alt"))

    def test_pdf_preview_uses_accessibly_titled_iframe_and_fallback_link(self):
        saved, preview = self.file_preview("student-document.PDF", "pdf")
        frames = preview.elements("iframe")
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].attrs.get("src"), saved.file.url)
        self.assertTrue(frames[0].attrs.get("title"))

    def test_audio_preview_has_native_controls_and_no_preloading(self):
        saved, preview = self.file_preview("student-audio.M4A", "audio")
        players = preview.elements("audio")
        self.assertEqual(len(players), 1)
        self.assertIn("controls", players[0].attrs)
        self.assertEqual(players[0].attrs.get("preload"), "none")
        urls = [players[0].attrs.get("src")] + [node.attrs.get("src") for node in players[0].elements("source")]
        self.assertIn(saved.file.url, urls)

    def test_video_preview_has_native_controls_and_no_preloading(self):
        saved, preview = self.file_preview("student-video.WEBM", "video")
        players = preview.elements("video")
        self.assertEqual(len(players), 1)
        self.assertIn("controls", players[0].attrs)
        self.assertEqual(players[0].attrs.get("preload"), "none")
        urls = [players[0].attrs.get("src")] + [node.attrs.get("src") for node in players[0].elements("source")]
        self.assertIn(saved.file.url, urls)

    def test_text_preview_is_a_pre_preserving_lines_and_html_escaping(self):
        text = 'سطر أول\n  سطر ثان <img src=x onerror="alert(7)"> & "نص"'
        saved = self.submission(self.task("معاينة نص متعدد الأسطر"), text_content=text)
        response = self.page()
        preview = self.preview(self.section(response, "student-task-history"), saved)
        self.assertEqual([node.text() for node in preview.elements("pre")], [text])
        for tag in ("img", "iframe", "audio", "video", "script"):
            self.assertEqual(preview.elements(tag), [])
        self.assertContains(response, str(escape(text)))
        self.assertNotContains(response, '<img src=x onerror="alert(7)">')

    def test_unknown_extension_has_only_a_file_fallback_not_an_embed(self):
        saved, preview = self.file_preview("legacy-work.unknown", "pdf")
        self.assertEqual(saved.get_submitted_format(), "")
        self.assertTrue(any(node.attrs.get("href") == saved.file.url for node in preview.elements("a")))
        for tag in ("img", "iframe", "audio", "video", "object", "embed", "script"):
            self.assertEqual(preview.elements(tag), [])

    def test_and_file_plus_text_renders_both_in_same_native_preview(self):
        for filename, fmt, tag in (
            ("both.jpg", "image", "img"), ("both.pdf", "pdf", "iframe"),
            ("both.mp3", "audio", "audio"), ("both.mp4", "video", "video"),
            ("both.old", "pdf", None),
        ):
            with self.subTest(format=fmt, filename=filename):
                text = f'نص AND لـ {filename}\n<script>alert("and")</script>'
                saved = self.submission(
                    self.task(f"ملف ونص {filename}", allowed_formats=f"{fmt},text", require_all_formats=True),
                    file=f"task_submissions/{filename}", text_content=text,
                )
                response = self.page()
                preview = self.preview(self.section(response, "student-task-history"), saved)
                self.assertEqual([node.text() for node in preview.elements("pre")], [text])
                if tag:
                    self.assertEqual(len(preview.elements(tag)), 1)
                    self.assertEqual(saved.get_submitted_format(), fmt)
                else:
                    self.assertEqual(saved.get_submitted_format(), "")
                    for media_tag in ("img", "iframe", "audio", "video", "object", "embed"):
                        self.assertEqual(preview.elements(media_tag), [])
                self.assertEqual(preview.elements("script"), [])
                self.assertContains(response, str(escape(text)))

    def test_task_title_description_rejection_and_returned_notes_are_html_escaped(self):
        payloads = []
        for status, note_field in (("rejected", "rejection_reason"), ("returned", "revision_note")):
            title = f'<img src=x onerror="alert(1)"> عنوان {status}'
            description = f'<script>alert("description-{status}")</script> & وصف'
            note = f'<svg onload="alert(2)"></svg> ملاحظة {status}'
            text = f'<script>alert("text-{status}")</script>\nعمل محفوظ'
            self.submission(
                self.task(title, description=description), status=status,
                text_content=text, **{note_field: note},
            )
            payloads.extend((title, description, note, text))
        response = self.page()
        history = self.section(response, "student-task-history")
        self.assertEqual(history.elements("script"), [])
        self.assertFalse(any(
            attr.lower().startswith("on") for node in history.elements() for attr in node.attrs
        ))
        for payload in payloads:
            self.assertContains(response, str(escape(payload)))
            self.assertNotContains(response, payload)
            self.assertIn(payload, history.text())

    def test_full_long_description_remains_available_and_escaped_in_its_task_card(self):
        description = "وصف مطول مصطنع " * 30 + '<script>alert("description-end")</script>'
        saved = self.submission(self.task("مهمة بوصف يتجاوز الاختصار", description=description))
        response = self.page()
        card = self.card(self.section(response, "student-task-history"), saved.task)
        self.assertIn(description, card.text())
        self.assertContains(response, str(escape(description)))
        self.assertEqual(card.elements("script"), [])
        # Additional disclosure for the description must not be confused with
        # the one submission preview, or hide either saved content.
        self.preview(card, saved)

    def test_forged_post_cannot_resubmit_any_nonreopened_historical_status(self):
        for status in TaskSubmission.Status.values:
            saved = self.submission(
                self.task(f"تسليم منتهي ممنوع {status}", is_active=True),
                status=status, file=f"task_submissions/unchanged-{status}.pdf",
                points_awarded=True if status == "accepted" else False,
                award_value=10 if status == "accepted" else 0,
            )
            before = self.state()
            response = self.client.post(self.url, {
                "task_id": saved.task_id, "chosen_format": "text",
                "text_content": "محاولة مفبركة بعد الموعد",
                "participant_id": self.foreign_student.pk, "submission_id": saved.pk,
                "reopened_for_resubmission": "on", "action": "reopen",
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.state(), before)
        unseen = self.task("انتهت ولم يسلمها الطالب", is_active=True)
        before = self.state()
        self.assertEqual(self.client.post(self.url, {
            "task_id": unseen.pk, "chosen_format": "text", "text_content": "أول رفع متأخر",
        }).status_code, 302)
        self.assertEqual(self.state(), before)

    def test_inactive_future_task_stays_closed_and_foreign_reopen_grants_no_permission(self):
        inactive = self.task("مستقبلية موقوفة لا تقبل رفعا", due_date=self.FUTURE_DUE)
        expired = self.task("فتحت لطالب آخر فقط")
        foreign = self.submission(expired, participant=self.foreign_student, reopened_for_resubmission=True)
        before = self.state()
        for task in (inactive, expired):
            response = self.client.post(self.url, {
                "task_id": task.pk, "chosen_format": "text", "text_content": "رفع غير مصرح",
                "participant": self.foreign_student.pk, "participant_id": self.foreign_student.pk,
                "submission_id": foreign.pk, "reopened_for_resubmission": "True",
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.state(), before)

    def test_normal_current_submission_ignores_forged_owner_and_grading_fields(self):
        task = self.task("رفع طبيعي لمهمة نشطة", is_active=True, due_date=self.FUTURE_DUE)
        foreign = self.submission(task, participant=self.foreign_student, text_content="عمل الآخر لا يتغير")
        foreign_before = TaskSubmission.objects.filter(pk=foreign.pk).values().get()
        balances = (self.student.points, self.student.miles, self.student.purchase_points)
        response = self.client.post(self.url, {
            "task_id": task.pk, "chosen_format": "text", "text_content": "تسليم الطالب الفعلي",
            "participant": self.foreign_student.pk, "participant_id": self.foreign_student.pk,
            "student_id": self.foreign_student.pk, "submission_id": foreign.pk,
            "status": "accepted", "award_value": "12", "points_awarded": "True",
            "is_featured": "on", "action": "revise_grade", "grade": "featured",
        })
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        saved = TaskSubmission.objects.get(task=task, participant=self.student)
        self.assertEqual(saved.text_content, "تسليم الطالب الفعلي")
        self.assertEqual(saved.status, TaskSubmission.Status.PENDING)
        self.assertFalse(saved.points_awarded)
        self.assertIsNone(saved.award_value)
        self.assertFalse(saved.is_featured)
        self.assertIsNone(saved.reviewed_by)
        self.assertFalse(saved.file)
        self.assertEqual(TaskSubmission.objects.filter(pk=foreign.pk).values().get(), foreign_before)
        self.student.refresh_from_db()
        self.assertEqual((self.student.points, self.student.miles, self.student.purchase_points), balances)
        self.assertFalse(PointsLedgerEntry.objects.exists())
        self.assertFalse(TaskGradeRevision.objects.exists())
        page = self.page(participant_id=self.foreign_student.pk, submission_id=foreign.pk)
        self.assertEqual(page.context["tasks_data"][0]["existing_submission"].pk, saved.pk)
        self.assertIsNone(page.context["tasks_data"][0]["form"])
        self.assertEqual(self.history_ids(page), [])
        self.assertNotContains(page, foreign.text_content)

    def test_explicit_own_past_reopen_updates_same_row_and_preserves_original_award(self):
        saved = self.submission(
            self.task("إعادة فتح مصرح بها بعد الموعد"), status="accepted",
            reopened_for_resubmission=True, points_awarded=True, award_value=12,
            is_featured=True, reviewed_by=self.staff[1], reviewed_at=self.HISTORY_TIME,
        )
        before = self.state()
        page = self.page()
        self.assertEqual(self.current_ids(page), [saved.task_id])
        self.assertEqual(self.history_ids(page), [])
        response = self.client.post(self.url, {
            "task_id": saved.task_id, "chosen_format": "text", "text_content": "عمل أعيد رفعه بتصريح",
            "award_value": "0", "points_awarded": "False",
        })
        self.assertEqual(response.status_code, 302)
        saved.refresh_from_db()
        self.assertEqual(saved.text_content, "عمل أعيد رفعه بتصريح")
        self.assertEqual(saved.status, TaskSubmission.Status.PENDING)
        self.assertFalse(saved.reopened_for_resubmission)
        self.assertTrue(saved.points_awarded)
        self.assertEqual(saved.award_value, 12)
        self.assertEqual(saved.submitted_at, self.HISTORY_TIME)
        self.assertIsNone(saved.reviewed_by)
        self.assertIsNone(saved.reviewed_at)
        self.assertEqual(TaskSubmission.objects.filter(task=saved.task, participant=self.student).count(), 1)
        after = self.state()
        for model in (Participant, WeeklyTask, PointsLedgerEntry, PointsResetSnapshot, TaskGradeRevision):
            self.assertEqual(after[model.__name__], before[model.__name__])
        page = self.page()
        self.assertEqual(self.current_ids(page), [])
        self.assertEqual(self.history_ids(page), [saved.pk])
        repeated_before = self.state()
        self.client.post(self.url, {
            "task_id": saved.task_id, "chosen_format": "text", "text_content": "تصريح مستهلك لا يستخدم ثانية",
        })
        self.assertEqual(self.state(), repeated_before)

    def test_current_returned_submission_keeps_revision_and_award_when_resubmitted(self):
        task = self.task("معادة قبل الموعد الأصلي", is_active=True, due_date=self.FUTURE_DUE)
        saved = self.submission(
            task, status="returned", revision_note="أضف مثالًا قبل الموعد.",
            points_awarded=True, award_value=10,
        )
        page = self.page()
        self.assertEqual(self.current_ids(page), [task.pk])
        self.assertIsInstance(page.context["tasks_data"][0]["form"], TaskSubmissionForm)
        self.preview(self.section(page, "student-current-tasks"), saved)
        before = self.state()
        response = self.client.post(self.url, {
            "task_id": task.pk, "chosen_format": "text", "text_content": "محاولة مصححة قبل الموعد",
        })
        self.assertEqual(response.status_code, 302)
        saved.refresh_from_db()
        self.assertEqual(saved.status, "pending")
        self.assertEqual(saved.text_content, "محاولة مصححة قبل الموعد")
        self.assertEqual(saved.revision_note, "أضف مثالًا قبل الموعد.")
        self.assertTrue(saved.points_awarded)
        self.assertEqual(saved.award_value, 10)
        self.assertEqual(TaskSubmission.objects.filter(task=task, participant=self.student).count(), 1)
        after = self.state()
        for model in (Participant, WeeklyTask, PointsLedgerEntry, PointsResetSnapshot, TaskGradeRevision):
            self.assertEqual(after[model.__name__], before[model.__name__])
