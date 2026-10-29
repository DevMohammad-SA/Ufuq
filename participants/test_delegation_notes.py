"""Regression coverage for delegated task reviews and private meeting notes.

Run this module only against ``config.test_settings``.  The tests deliberately
exercise the public routes and the access helpers; they never use the project's
production database configuration.
"""

import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from participants.access import (
    can_manage_tasks,
    can_review_task_submissions,
    can_view_meeting_notes,
    scoped_participants,
)
from participants.models import (
    Group,
    MeetingAttendance,
    MeetingNote,
    Participant,
    PointsLedgerEntry,
    TaskReviewPermission,
    TaskSubmission,
    WeeklyTask,
)


class DelegationNotesBase(TestCase):
    """Shared, deliberately multi-group fixture for both feature areas."""

    def setUp(self):
        self.review_url = reverse("participants:weekly_task_review")
        self.permissions_url = reverse("participants:task_review_permissions")
        self.dashboard_url = reverse("participants:supervisor_dashboard")
        self.meeting_date = datetime.date.today()

        self.general = self._supervisor(
            "delegation-general", Role.GENERAL_SUPERVISOR, "المشرف العام"
        )
        self.system = self._supervisor(
            "delegation-system", Role.SUPERADMIN, "مشرف النظام"
        )
        self.reviewer = self._supervisor(
            "delegation-reviewer", Role.GROUP_SUPERVISOR, "مشرف البيئتين"
        )
        self.gamma_supervisor = self._supervisor(
            "delegation-gamma", Role.GROUP_SUPERVISOR, "مشرف جيم"
        )

        self.alpha = Group.objects.create(name="بيئة التفويض ألف")
        self.beta = Group.objects.create(name="بيئة التفويض باء")
        self.gamma = Group.objects.create(name="بيئة التفويض جيم")
        # The delegated reviewer deliberately has more than one environment.
        self.alpha.supervisor.add(self.reviewer)
        self.beta.supervisor.add(self.reviewer)
        self.gamma.supervisor.add(self.gamma_supervisor)

        self.alpha_student = self._participant("ألف ضمن النطاق", self.alpha)
        self.beta_student = self._participant("باء ضمن النطاق", self.beta)
        self.gamma_student = self._participant("جيم خارج النطاق", self.gamma)

        future = self.meeting_date + datetime.timedelta(days=14)
        self.active_task = WeeklyTask.objects.create(
            title="مهمة نشطة للتفويض",
            description="مهمة اختبارية",
            due_date=future,
            allowed_formats="text",
            is_active=True,
            created_by=self.general,
        )
        self.inactive_task = WeeklyTask.objects.create(
            title="مهمة غير نشطة للتفويض",
            description="مهمة اختبارية",
            due_date=future,
            allowed_formats="text",
            is_active=False,
            created_by=self.general,
        )
        self.history_task = WeeklyTask.objects.create(
            title="مهمة تاريخية للتفويض",
            description="تظهر لمشرف البيئة للقراءة",
            due_date=self.meeting_date - datetime.timedelta(days=1),
            allowed_formats="text",
            is_active=True,
            created_by=self.general,
        )

        self.alpha_submission = self._submission(self.alpha_student, self.active_task)
        self.beta_submission = self._submission(self.beta_student, self.active_task)
        self.gamma_submission = self._submission(self.gamma_student, self.active_task)
        self.alpha_history_submission = self._submission(
            self.alpha_student, self.history_task
        )
        self.gamma_history_submission = self._submission(
            self.gamma_student, self.history_task
        )

    @staticmethod
    def _supervisor(username, role, full_name):
        return User.objects.create_user(
            username=username,
            password="pw12345678",
            role=role,
            full_name=full_name,
        )

    def _participant(self, full_name, group):
        number = Participant.objects.count() + 1
        user = User.objects.create_user(
            username=f"delegation-student-{number}",
            national_id=f"8{number:09d}",
            password="pw12345678",
            role=Role.PARTICIPANT,
            full_name=full_name,
        )
        return Participant.objects.create(
            user=user,
            group=group,
            academic_stage="grade_7",
            points=123,
            miles=1230,
            purchase_points=123,
        )

    @staticmethod
    def _submission(participant, task):
        return TaskSubmission.objects.create(
            task=task,
            participant=participant,
            text_content="تسليم اختباري",
        )

    def enable_reviewer(self):
        permission, _ = TaskReviewPermission.objects.update_or_create(
            supervisor=self.reviewer,
            defaults={"enabled": True, "granted_by": self.general},
        )
        return permission

    @staticmethod
    def balances(participant):
        participant.refresh_from_db()
        return participant.points, participant.miles, participant.purchase_points

    @staticmethod
    def roster_entry(response, participant):
        return next(
            entry
            for entry in response.context["roster"]
            if entry["participant"].id == participant.id
        )


class DelegatedTaskReviewTests(DelegationNotesBase):
    def test_access_helpers_honor_roles_active_grant_and_all_assigned_groups(self):
        self.assertTrue(can_manage_tasks(self.general))
        self.assertTrue(can_manage_tasks(self.system))
        self.assertFalse(can_manage_tasks(self.reviewer))

        self.assertTrue(can_review_task_submissions(self.general))
        self.assertTrue(can_review_task_submissions(self.system))
        self.assertFalse(can_review_task_submissions(self.reviewer))
        self.enable_reviewer()
        self.assertTrue(can_review_task_submissions(self.reviewer))

        self.assertTrue(can_view_meeting_notes(self.reviewer))
        self.assertTrue(can_view_meeting_notes(self.general))
        self.assertFalse(can_view_meeting_notes(self.system))

        scoped_ids = set(
            scoped_participants(self.reviewer).values_list("id", flat=True)
        )
        self.assertEqual(
            scoped_ids, {self.alpha_student.id, self.beta_student.id}
        )

    def test_permission_endpoint_is_general_only_and_validates_strictly(self):
        self.client.force_login(self.general)
        self.assertEqual(self.client.get(self.permissions_url).status_code, 200)

        response = self.client.post(
            self.permissions_url,
            {
                "action": "set_permission",
                "supervisor_id": self.reviewer.id,
                "enabled": "1",
            },
        )
        self.assertIn(response.status_code, (200, 302))
        permission = TaskReviewPermission.objects.get(supervisor=self.reviewer)
        self.assertTrue(permission.enabled)
        self.assertEqual(permission.granted_by, self.general)

        invalid = self.client.post(
            self.permissions_url,
            {
                "action": "set_permission",
                "supervisor_id": self.reviewer.id,
                "enabled": "true",
            },
        )
        self.assertEqual(invalid.status_code, 400)
        permission.refresh_from_db()
        self.assertTrue(permission.enabled)

        invalid_action = self.client.post(
            self.permissions_url,
            {
                "action": "toggle_permission",
                "supervisor_id": self.reviewer.id,
                "enabled": "0",
            },
        )
        self.assertEqual(invalid_action.status_code, 400)
        permission.refresh_from_db()
        self.assertTrue(permission.enabled)

        not_a_group_supervisor = self.client.post(
            self.permissions_url,
            {
                "action": "set_permission",
                "supervisor_id": self.alpha_student.user_id,
                "enabled": "0",
            },
        )
        self.assertIn(not_a_group_supervisor.status_code, (400, 404))
        self.assertFalse(
            TaskReviewPermission.objects.filter(
                supervisor_id=self.alpha_student.user_id
            ).exists()
        )

        for user in (self.reviewer, self.system):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.permissions_url).status_code, 403)
                denied = self.client.post(
                    self.permissions_url,
                    {
                        "action": "set_permission",
                        "supervisor_id": self.reviewer.id,
                        "enabled": "0",
                    },
                )
                self.assertEqual(denied.status_code, 403)

    def test_group_supervisor_reads_all_task_history_but_no_foreign_students(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # Both assigned environments are visible, including a past-due task.
        self.assertIn(self.alpha_student.user.full_name, html)
        self.assertIn(self.beta_student.user.full_name, html)
        self.assertIn(self.history_task.title, html)
        # A global task may be listed, but another environment's student data
        # must never be included in the review table.
        self.assertNotIn(self.gamma_student.user.full_name, html)

    def test_unapproved_group_reviewer_gets_no_management_or_review_controls(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(can_review_task_submissions(self.reviewer))
        html = response.content.decode()
        for forbidden_action in (
            "create_task",
            "toggle_active",
            'value="accept"',
            'value="reject"',
        ):
            with self.subTest(forbidden_action=forbidden_action):
                self.assertNotIn(forbidden_action, html)

    def test_delegated_reviewer_accepts_scoped_submissions_once_with_correct_credit(self):
        self.enable_reviewer()
        self.client.force_login(self.reviewer)

        cases = (
            (self.alpha_submission, self.alpha_student, False, 10),
            (self.beta_submission, self.beta_student, True, 12),
        )
        for submission, participant, featured, award in cases:
            with self.subTest(featured=featured):
                before = self.balances(participant)
                payload = {"action": "accept", "submission_id": submission.id}
                if featured:
                    payload["is_featured"] = "on"
                response = self.client.post(self.review_url, payload)
                self.assertIn(response.status_code, (200, 302))

                submission.refresh_from_db()
                self.assertEqual(submission.status, TaskSubmission.Status.ACCEPTED)
                self.assertEqual(submission.reviewed_by, self.reviewer)
                self.assertEqual(submission.is_featured, featured)
                expected = (before[0] + award, before[1] + award * 10, before[2] + award)
                self.assertEqual(self.balances(participant), expected)

                # A crafted retry cannot issue a second ledger credit.
                retry = self.client.post(self.review_url, payload)
                self.assertIn(retry.status_code, (200, 302))
                self.assertEqual(self.balances(participant), expected)
                self.assertEqual(
                    PointsLedgerEntry.objects.filter(
                        participant=participant,
                        source=PointsLedgerEntry.Source.WEEKLY_TASK,
                    ).count(),
                    1,
                )

    def test_delegated_reviewer_rejects_scoped_submission_without_credit(self):
        self.enable_reviewer()
        self.client.force_login(self.reviewer)
        before = self.balances(self.alpha_history_submission.participant)

        response = self.client.post(
            self.review_url,
            {
                "action": "reject",
                "submission_id": self.alpha_history_submission.id,
                "rejection_reason": "يرجى إعادة المحاولة",
            },
        )
        self.assertIn(response.status_code, (200, 302))

        self.alpha_history_submission.refresh_from_db()
        self.assertEqual(self.alpha_history_submission.status, TaskSubmission.Status.REJECTED)
        self.assertEqual(self.alpha_history_submission.rejection_reason, "يرجى إعادة المحاولة")
        self.assertEqual(self.alpha_history_submission.reviewed_by, self.reviewer)
        self.assertEqual(self.balances(self.alpha_student), before)
        self.assertFalse(
            PointsLedgerEntry.objects.filter(
                participant=self.alpha_student,
                source=PointsLedgerEntry.Source.WEEKLY_TASK,
            ).exists()
        )

    def test_delegated_post_scope_management_boundary_and_revocation(self):
        self.enable_reviewer()
        self.client.force_login(self.reviewer)

        foreign_before = self.balances(self.gamma_student)
        foreign_response = self.client.post(
            self.review_url,
            {"action": "accept", "submission_id": self.gamma_submission.id},
        )
        self.assertIn(foreign_response.status_code, (403, 404))
        self.gamma_submission.refresh_from_db()
        self.assertEqual(self.gamma_submission.status, TaskSubmission.Status.PENDING)
        self.assertEqual(self.balances(self.gamma_student), foreign_before)

        active_before = self.active_task.is_active
        forbidden_posts = (
            ("create_task", {}),
            ("toggle_active", {"task_id": self.active_task.id}),
            ("reopen", {"submission_id": self.alpha_submission.id}),
        )
        for action, data in forbidden_posts:
            with self.subTest(action=action):
                response = self.client.post(
                    self.review_url, {"action": action, **data}
                )
                self.assertEqual(response.status_code, 403)
        self.active_task.refresh_from_db()
        self.alpha_submission.refresh_from_db()
        self.assertEqual(self.active_task.is_active, active_before)
        self.assertFalse(self.alpha_submission.reopened_for_resubmission)

        permission = TaskReviewPermission.objects.get(supervisor=self.reviewer)
        permission.enabled = False
        permission.save(update_fields=["enabled"])
        own_before = self.balances(self.alpha_student)
        revoked = self.client.post(
            self.review_url,
            {"action": "accept", "submission_id": self.alpha_submission.id},
        )
        self.assertEqual(revoked.status_code, 403)
        self.alpha_submission.refresh_from_db()
        self.assertEqual(self.alpha_submission.status, TaskSubmission.Status.PENDING)
        self.assertEqual(self.balances(self.alpha_student), own_before)

    def test_general_and_system_keep_task_management_and_review_capabilities(self):
        future = self.meeting_date + datetime.timedelta(days=21)
        for number, user in enumerate((self.general, self.system), start=1):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                title = f"مهمة الإدارة {number}"
                create = self.client.post(
                    self.review_url,
                    {
                        "action": "create_task",
                        "title": title,
                        "description": "تأكيد بقاء صلاحيات الإدارة",
                        "due_date": future.isoformat(),
                        "allowed_formats": ["text"],
                        "is_active": "on",
                    },
                )
                self.assertIn(create.status_code, (200, 302))
                task = WeeklyTask.objects.get(title=title)
                self.assertTrue(task.is_active)

                toggle = self.client.post(
                    self.review_url,
                    {"action": "toggle_active", "task_id": task.id},
                )
                self.assertIn(toggle.status_code, (200, 302))
                task.refresh_from_db()
                self.assertFalse(task.is_active)

                participant = self._participant(
                    f"طالب الإدارة {number}", self.alpha
                )
                submission = self._submission(participant, task)
                before = self.balances(participant)
                action = "accept" if user == self.general else "reject"
                payload = {"action": action, "submission_id": submission.id}
                if action == "reject":
                    payload["rejection_reason"] = "قرار إداري"
                review = self.client.post(self.review_url, payload)
                self.assertIn(review.status_code, (200, 302))

                submission.refresh_from_db()
                self.assertEqual(submission.reviewed_by, user)
                if action == "accept":
                    self.assertEqual(submission.status, TaskSubmission.Status.ACCEPTED)
                    self.assertEqual(
                        self.balances(participant),
                        (before[0] + 10, before[1] + 100, before[2] + 10),
                    )
                else:
                    self.assertEqual(submission.status, TaskSubmission.Status.REJECTED)
                    self.assertEqual(submission.rejection_reason, "قرار إداري")
                    self.assertEqual(self.balances(participant), before)


class MeetingNotesTests(DelegationNotesBase):
    def test_group_dashboard_selection_uses_assigned_groups_and_rejects_outsider(self):
        self.client.force_login(self.reviewer)
        default_response = self.client.get(self.dashboard_url)
        self.assertEqual(default_response.status_code, 200)
        expected_default = self.reviewer.group_set.order_by("name").first()
        self.assertEqual(default_response.context["group"], expected_default)

        selected = self.client.get(self.dashboard_url, {"group": self.beta.id})
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.context["group"], self.beta)

        outsider = self.client.get(self.dashboard_url, {"group": self.gamma.id})
        self.assertEqual(outsider.status_code, 404)

    def test_notes_are_visible_only_to_group_and_general_within_selected_scope(self):
        alpha_note = MeetingNote.objects.create(
            participant=self.alpha_student,
            meeting_date=self.meeting_date,
            text="ملاحظة ألف الخاصة",
            created_by=self.general,
            updated_by=self.general,
        )
        gamma_note = MeetingNote.objects.create(
            participant=self.gamma_student,
            meeting_date=self.meeting_date,
            text="ملاحظة جيم الخاصة",
            created_by=self.general,
            updated_by=self.general,
        )

        self.client.force_login(self.reviewer)
        group_response = self.client.get(
            self.dashboard_url,
            {"group": self.alpha.id, "date": self.meeting_date.isoformat()},
        )
        self.assertEqual(group_response.status_code, 200)
        self.assertTrue(group_response.context["can_view_notes"])
        self.assertEqual(
            self.roster_entry(group_response, self.alpha_student)["note"], alpha_note.text
        )

        self.client.force_login(self.general)
        general_response = self.client.get(
            self.dashboard_url,
            {"group": self.gamma.id, "date": self.meeting_date.isoformat()},
        )
        self.assertEqual(general_response.status_code, 200)
        self.assertTrue(general_response.context["can_view_notes"])
        self.assertEqual(
            self.roster_entry(general_response, self.gamma_student)["note"], gamma_note.text
        )

        self.client.force_login(self.system)
        system_response = self.client.get(
            self.dashboard_url,
            {"group": self.alpha.id, "date": self.meeting_date.isoformat()},
        )
        self.assertEqual(system_response.status_code, 200)
        self.assertFalse(system_response.context["can_view_notes"])
        self.assertNotIn(
            "note", self.roster_entry(system_response, self.alpha_student)
        )
        self.assertNotIn(alpha_note.text, system_response.content.decode())

        self.client.force_login(self.alpha_student.user)
        participant_response = self.client.get(self.dashboard_url)
        self.assertNotEqual(participant_response.status_code, 200)

    def test_save_note_is_side_effect_free_updates_in_place_and_keeps_dates_distinct(self):
        attendance = MeetingAttendance.objects.create(
            participant=self.alpha_student,
            week_start_date=self.meeting_date,
            attended=True,
            is_early=True,
            recorded_by=self.reviewer,
        )
        before_balances = self.balances(self.alpha_student)
        self.client.force_login(self.reviewer)

        payload = {
            "action": "save_note",
            "participant_id": self.alpha_student.id,
            "note_text": "الملاحظة الأولى",
            "date": self.meeting_date.isoformat(),
            "group": self.alpha.id,
        }
        response = self.client.post(self.dashboard_url, payload)
        self.assertIn(response.status_code, (200, 302))
        note = MeetingNote.objects.get(
            participant=self.alpha_student, meeting_date=self.meeting_date
        )
        self.assertEqual(note.text, "الملاحظة الأولى")
        self.assertEqual(note.created_by, self.reviewer)
        self.assertEqual(note.updated_by, self.reviewer)

        updated = self.client.post(
            self.dashboard_url, {**payload, "note_text": "الملاحظة المحدّثة"}
        )
        self.assertIn(updated.status_code, (200, 302))
        note.refresh_from_db()
        self.assertEqual(note.text, "الملاحظة المحدّثة")
        self.assertEqual(
            MeetingNote.objects.filter(
                participant=self.alpha_student, meeting_date=self.meeting_date
            ).count(),
            1,
        )

        next_date = self.meeting_date + datetime.timedelta(days=7)
        later = self.client.post(
            self.dashboard_url,
            {**payload, "date": next_date.isoformat(), "note_text": "ملاحظة تاريخ آخر"},
        )
        self.assertIn(later.status_code, (200, 302))
        self.assertTrue(
            MeetingNote.objects.filter(
                participant=self.alpha_student,
                meeting_date=next_date,
                text="ملاحظة تاريخ آخر",
            ).exists()
        )

        attendance.refresh_from_db()
        self.assertTrue(attendance.attended)
        self.assertTrue(attendance.is_early)
        self.assertEqual(self.balances(self.alpha_student), before_balances)
        self.assertFalse(
            PointsLedgerEntry.objects.filter(
                participant=self.alpha_student,
                source=PointsLedgerEntry.Source.MEETING_ATTENDANCE,
            ).exists()
        )

    def test_bulk_notes_update_preserve_delete_and_ignore_foreign_fields(self):
        own_note = MeetingNote.objects.create(
            participant=self.alpha_student,
            meeting_date=self.meeting_date,
            text="قبل التحديث",
            created_by=self.general,
            updated_by=self.general,
        )
        foreign_note = MeetingNote.objects.create(
            participant=self.gamma_student,
            meeting_date=self.meeting_date,
            text="ملاحظة جيم لا تتغير",
            created_by=self.general,
            updated_by=self.general,
        )
        self.client.force_login(self.reviewer)
        base = {"date": self.meeting_date.isoformat(), "group": self.alpha.id}

        update = self.client.post(
            self.dashboard_url,
            {
                **base,
                f"note_{self.alpha_student.id}": "بعد التحديث",
                f"note_{self.gamma_student.id}": "محاولة اختراق",
            },
        )
        self.assertIn(update.status_code, (200, 302))
        own_note.refresh_from_db()
        foreign_note.refresh_from_db()
        self.assertEqual(own_note.text, "بعد التحديث")
        self.assertEqual(foreign_note.text, "ملاحظة جيم لا تتغير")

        preserve = self.client.post(self.dashboard_url, base)
        self.assertIn(preserve.status_code, (200, 302))
        own_note.refresh_from_db()
        self.assertEqual(own_note.text, "بعد التحديث")

        delete = self.client.post(
            self.dashboard_url,
            {**base, f"note_{self.alpha_student.id}": ""},
        )
        self.assertIn(delete.status_code, (200, 302))
        self.assertFalse(
            MeetingNote.objects.filter(
                participant=self.alpha_student, meeting_date=self.meeting_date
            ).exists()
        )
        foreign_note.refresh_from_db()
        self.assertEqual(foreign_note.text, "ملاحظة جيم لا تتغير")

    def test_note_validation_is_atomic_and_direct_cross_group_post_is_denied(self):
        original_note = MeetingNote.objects.create(
            participant=self.alpha_student,
            meeting_date=self.meeting_date,
            text="ملاحظة سليمة",
            created_by=self.general,
            updated_by=self.general,
        )
        attendance = MeetingAttendance.objects.create(
            participant=self.alpha_student,
            week_start_date=self.meeting_date,
            attended=True,
            is_early=True,
            recorded_by=self.reviewer,
        )
        before_balances = self.balances(self.alpha_student)
        too_long = "م" * 2001
        self.client.force_login(self.reviewer)

        cross_group = self.client.post(
            self.dashboard_url,
            {
                "action": "save_note",
                "participant_id": self.gamma_student.id,
                "note_text": "لا ينبغي الحفظ",
                "date": self.meeting_date.isoformat(),
                "group": self.gamma.id,
            },
        )
        self.assertIn(cross_group.status_code, (403, 404))
        self.assertFalse(
            MeetingNote.objects.filter(
                participant=self.gamma_student, meeting_date=self.meeting_date
            ).exists()
        )

        direct_invalid = self.client.post(
            self.dashboard_url,
            {
                "action": "save_note",
                "participant_id": self.alpha_student.id,
                "note_text": too_long,
                "date": self.meeting_date.isoformat(),
                "group": self.alpha.id,
            },
        )
        self.assertEqual(direct_invalid.status_code, 400)
        original_note.refresh_from_db()
        self.assertEqual(original_note.text, "ملاحظة سليمة")

        bulk_invalid = self.client.post(
            self.dashboard_url,
            {
                "date": self.meeting_date.isoformat(),
                "group": self.alpha.id,
                f"note_{self.alpha_student.id}": too_long,
            },
        )
        self.assertIn(bulk_invalid.status_code, (200, 400))
        original_note.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(original_note.text, "ملاحظة سليمة")
        self.assertTrue(attendance.attended)
        self.assertTrue(attendance.is_early)
        self.assertEqual(self.balances(self.alpha_student), before_balances)

    def test_system_note_posts_are_ignored_and_note_html_is_escaped(self):
        script = "<script>alert(1)</script>"
        self.client.force_login(self.system)
        ignored = self.client.post(
            self.dashboard_url,
            {
                "action": "save_note",
                "participant_id": self.alpha_student.id,
                "note_text": "لا تحفظ لمشرف النظام",
                "date": self.meeting_date.isoformat(),
                "group": self.alpha.id,
            },
        )
        self.assertEqual(ignored.status_code, 403)
        self.assertFalse(
            MeetingNote.objects.filter(
                participant=self.alpha_student, meeting_date=self.meeting_date
            ).exists()
        )

        self.client.force_login(self.reviewer)
        saved = self.client.post(
            self.dashboard_url,
            {
                "action": "save_note",
                "participant_id": self.alpha_student.id,
                "note_text": script,
                "date": self.meeting_date.isoformat(),
                "group": self.alpha.id,
            },
        )
        self.assertIn(saved.status_code, (200, 302))
        rendered = self.client.get(
            self.dashboard_url,
            {"group": self.alpha.id, "date": self.meeting_date.isoformat()},
        )
        self.assertEqual(rendered.status_code, 200)
        html = rendered.content.decode()
        self.assertNotIn(script, html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
