import datetime
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db.models.query import QuerySet
from django.http import Http404
from django.test import TestCase, RequestFactory
from django.utils import timezone

from accounts.models import User, Role
from .models import (
    Group, Participant, WeeklyTask, TaskSubmission, TaskGradeRevision, PointsLedgerEntry,
    SupervisorTaskGradePermission, TaskReviewPermission, SupervisorCapability,
)
from .task_grading import (
    FeaturedAdjustmentForm, cancel_submission_grade, revise_submission_grade,
    set_submission_featured,
)
from .views import apply_points_delta


class TaskFeaturedAdjustmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.general = User.objects.create_user(username='featured_general', role=Role.GENERAL_SUPERVISOR)
        cls.environment = User.objects.create_user(username='featured_group', role=Role.GROUP_SUPERVISOR)
        cls.system = User.objects.create_user(username='featured_system', role=Role.SUPERADMIN)
        cls.limited = User.objects.create_user(username='featured_limited', role=Role.LIMITED_SUPERVISOR)
        cls.student = User.objects.create_user(username='featured_student', role=Role.PARTICIPANT)
        cls.group = Group.objects.create(name='بيئة التميز المصطنعة')
        cls.group.supervisor.add(cls.environment)
        cls.participant = Participant.objects.create(
            user=cls.student, group=cls.group, points=100, miles=1000, purchase_points=100,
        )
        cls.task = WeeklyTask.objects.create(
            title='مهمة التميز المصطنعة', description='وصف محفوظ', allowed_formats='pdf,text',
            require_all_formats=True, is_active=False,
            due_date=timezone.localdate() - datetime.timedelta(days=5), created_by=cls.general,
        )
        cls.submission = TaskSubmission.objects.create(
            task=cls.task, participant=cls.participant, file='task_submissions/featured-synthetic.pdf',
            text_content='عمل ثابت للطالب', status=TaskSubmission.Status.ACCEPTED,
            is_featured=False, award_value=10, points_awarded=True,
            reviewed_by=cls.system, reviewed_at=timezone.now(),
            revision_note='ملاحظة سياقية محفوظة', rejection_reason='سبب سابق محفوظ',
            reopened_for_resubmission=True,
        )
        TaskSubmission.objects.filter(pk=cls.submission.pk).update(
            submitted_at=timezone.now() - datetime.timedelta(days=12),
        )
        cls.submission.refresh_from_db()

    def setUp(self):
        self.factory = RequestFactory()

    def data(self, action='set_featured', **extra):
        self.submission.refresh_from_db()
        fields = {
            'action': action, 'submission_id': str(self.submission.pk),
            'review_version': self.submission.grade_revision_token,
        }
        fields.update({
            'set_featured': {'is_featured': 'yes'},
            'revise_grade': {'grade': 'featured', 'reason': 'تصحيح مصطنع موثق'},
            'cancel_grade': {'confirm': 'yes'},
        }[action])
        fields.update(extra)
        return fields

    def call(self, action='set_featured', actor=None, data=None, **extra):
        request = self.factory.post('/participants/tasks/review/', self.data(action, **extra) if data is None else data)
        request.user = self.general if actor is None else actor
        service = {
            'set_featured': set_submission_featured,
            'revise_grade': revise_submission_grade,
            'cancel_grade': cancel_submission_grade,
        }[action]
        return service(request)

    def set_review(self, **fields):
        TaskSubmission.objects.filter(pk=self.submission.pk).update(**fields)
        self.submission.refresh_from_db()

    def assert_balances(self, expected):
        self.participant.refresh_from_db()
        self.assertEqual(
            (self.participant.points, self.participant.miles, self.participant.purchase_points), expected,
        )

    def original_submission(self):
        return TaskSubmission.objects.filter(pk=self.submission.pk).values().get()

    def assert_untouched(self, original):
        self.assertEqual(self.original_submission(), original)
        self.assert_balances((100, 1000, 100))
        self.assertFalse(TaskGradeRevision.objects.exists())
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_round_trip_applies_only_two_in_all_currencies_and_original_event_date(self):
        old_reviewed_at = self.submission.reviewed_at
        uploaded_at = self.submission.submitted_at
        self.assertTrue(self.call()[0])
        self.assert_balances((102, 1020, 102))
        self.submission.refresh_from_db()
        self.assertEqual((self.submission.status, self.submission.award_value, self.submission.is_featured), ('accepted', 12, True))
        self.assertTrue(self.submission.points_awarded)
        self.assertEqual(self.submission.reviewed_by_id, self.general.pk)
        self.assertNotEqual(self.submission.reviewed_at, old_reviewed_at)
        self.assertTrue(self.call(is_featured='no')[0])
        self.assert_balances((100, 1000, 100))
        self.submission.refresh_from_db()
        self.assertEqual((self.submission.award_value, self.submission.is_featured), (10, False))
        entries = list(PointsLedgerEntry.objects.order_by('pk'))
        self.assertEqual([entry.points_delta for entry in entries], [2, -2])
        for entry in entries:
            self.assertEqual(entry.event_date, timezone.localdate(uploaded_at))
            self.assertEqual(entry.source, PointsLedgerEntry.Source.WEEKLY_TASK)
            self.assertEqual(entry.granted_by_id, self.general.pk)
        revisions = list(TaskGradeRevision.objects.order_by('pk'))
        self.assertEqual([(audit.old_award, audit.new_award) for audit in revisions], [(10, 12), (12, 10)])
        self.assertEqual([(audit.old_featured, audit.new_featured) for audit in revisions], [(False, True), (True, False)])
        for audit in revisions:
            self.assertEqual((audit.old_status, audit.new_status), ('accepted', 'accepted'))
        self.assertIn('إضافة التميز', revisions[0].reason)
        self.assertIn('إزالة التميز', revisions[1].reason)
        self.assertIn(old_reviewed_at.isoformat(), revisions[0].reason)
        self.assertIn(str(self.system.pk), revisions[0].reason)

    def test_quick_adjustment_preserves_entire_work_task_notes_and_reopen_context(self):
        original = self.original_submission()
        original_task = WeeklyTask.objects.filter(pk=self.task.pk).values().get()
        self.assertTrue(self.call()[0])
        current = self.original_submission()
        changed = {'is_featured', 'award_value', 'points_awarded', 'reviewed_at', 'reviewed_by_id'}
        for field, value in original.items():
            if field not in changed:
                self.assertEqual(current[field], value, field)
        self.assertEqual(WeeklyTask.objects.filter(pk=self.task.pk).values().get(), original_task)
        self.assertIn(original['revision_note'], TaskGradeRevision.objects.get().reason)
        self.assertIn(original['rejection_reason'], TaskGradeRevision.objects.get().reason)

    def test_spent_balances_still_use_independent_zero_floors_without_historical_restore(self):
        self.set_review(award_value=12, is_featured=True)
        Participant.objects.filter(pk=self.participant.pk).update(points=1, miles=5, purchase_points=0)
        self.assertTrue(self.call(is_featured='no')[0])
        self.assert_balances((0, 0, 0))
        self.assertTrue(self.call()[0])
        self.assert_balances((2, 20, 2))
        self.assertEqual(list(PointsLedgerEntry.objects.order_by('pk').values_list('points_delta', flat=True)), [-2, 2])

    def test_rejected_pending_and_returned_are_blocked(self):
        for status in ('rejected', 'pending', 'returned'):
            with self.subTest(status=status):
                self.set_review(status=status)
                original = self.original_submission()
                self.assertFalse(self.call()[0])
                self.assert_untouched(original)

    def test_zero_or_invalid_award_never_mints_acceptance_base(self):
        for award in (0, 1, 11, 99):
            with self.subTest(award=award):
                self.set_review(award_value=award)
                original = self.original_submission()
                self.assertFalse(self.call(previous_award='10')[0])
                self.assert_untouched(original)

    def test_unknown_true_or_null_needs_explicit_confirmation_not_featured_flag_guess(self):
        for flag in (True, None):
            for featured in (True, False):
                with self.subTest(flag=flag, featured=featured):
                    self.set_review(award_value=None, points_awarded=flag, is_featured=featured)
                    original = self.original_submission()
                    self.assertFalse(self.call()[0])
                    self.assert_untouched(original)

    def test_unknown_award_explicit_ten_upgrades_by_two(self):
        self.set_review(award_value=None, points_awarded=None)
        self.assertTrue(self.call(previous_award='10')[0])
        self.assert_balances((102, 1020, 102))
        self.assertEqual((TaskGradeRevision.objects.get().old_award, TaskGradeRevision.objects.get().new_award), (10, 12))

    def test_unknown_award_explicit_twelve_downgrades_by_two(self):
        self.set_review(award_value=None, points_awarded=True, is_featured=True)
        self.assertTrue(self.call(is_featured='no', previous_award='12')[0])
        self.assert_balances((98, 980, 98))
        self.assertEqual(PointsLedgerEntry.objects.get().points_delta, -2)

    def test_unknown_matched_choice_confirms_metadata_and_audits_once_with_zero_delta(self):
        for flag in (True, None):
            for featured, award in ((False, '10'), (True, '12')):
                with self.subTest(flag=flag, featured=featured):
                    self.set_review(award_value=None, points_awarded=flag, is_featured=featured)
                    entries = TaskGradeRevision.objects.count()
                    data = self.data(is_featured='yes' if featured else 'no', previous_award=award)
                    self.assertTrue(self.call(data=data)[0])
                    self.assertFalse(self.call(data=data)[0])
                    self.assertTrue(self.call(is_featured='yes' if featured else 'no')[0])
                    self.submission.refresh_from_db()
                    self.assertEqual(self.submission.award_value, int(award))
                    self.assertTrue(self.submission.points_awarded)
                    self.assertEqual(TaskGradeRevision.objects.count(), entries + 1)
                    audit = TaskGradeRevision.objects.latest('pk')
                    self.assertEqual((audit.old_award, audit.new_award), (int(award), int(award)))
        self.assert_balances((100, 1000, 100))
        self.assertFalse(PointsLedgerEntry.objects.exists())

    def test_unknown_unpaid_false_means_zero_even_with_explicit_ten(self):
        self.set_review(award_value=None, points_awarded=False)
        original = self.original_submission()
        self.assertFalse(self.call(previous_award='10')[0])
        self.assert_untouched(original)

    def test_known_award_is_authoritative_not_optional_choice(self):
        self.assertTrue(self.call(previous_award='12')[0])
        self.assert_balances((102, 1020, 102))
        self.assertEqual(TaskGradeRevision.objects.get().old_award, 10)

    def test_invalid_previous_award_is_rejected_even_if_known(self):
        for value in ('0', '-10', '10.0', '11', '999', ' 10 ', 'true'):
            with self.subTest(value=value):
                original = self.original_submission()
                self.assertFalse(self.call(previous_award=value)[0])
                self.assert_untouched(original)

    def test_absolute_featured_choice_is_required_and_strict(self):
        for value in ('', 'on', 'off', 'true', 'false', '1', '0', 'YES', ' no ', 'featured'):
            with self.subTest(value=value):
                original = self.original_submission()
                self.assertFalse(self.call(is_featured=value)[0])
                self.assert_untouched(original)
        data = self.data()
        del data['is_featured']
        self.assertFalse(self.call(data=data)[0])
        self.assertFalse(FeaturedAdjustmentForm({'is_featured': ['no', 'yes']}).is_valid())

    def test_known_matched_request_changes_no_timestamp_audit_or_points(self):
        original = self.original_submission()
        self.assertTrue(self.call(is_featured='no')[0])
        self.assert_untouched(original)

    def test_double_post_and_fresh_repeat_pay_and_audit_once(self):
        data = self.data()
        self.assertTrue(self.call(data=data)[0])
        self.assertFalse(self.call(data=data)[0])
        original = self.original_submission()
        self.assertTrue(self.call()[0])
        self.assertEqual(self.original_submission(), original)
        self.assert_balances((102, 1020, 102))
        self.assertEqual(TaskGradeRevision.objects.count(), 1)
        self.assertEqual(list(PointsLedgerEntry.objects.values_list('points_delta', flat=True)), [2])

    def test_quick_change_invalidates_old_direct_and_cancel_forms(self):
        old_direct = self.data('revise_grade', grade='rejected')
        old_cancel = self.data('cancel_grade')
        self.assertTrue(self.call()[0])
        self.assertFalse(self.call('revise_grade', data=old_direct)[0])
        self.assertFalse(self.call('cancel_grade', data=old_cancel)[0])
        self.assert_balances((102, 1020, 102))
        self.assertEqual(TaskGradeRevision.objects.count(), 1)

    def test_failure_after_actual_points_write_rolls_back_everything(self):
        original = self.original_submission()

        def write_then_fail(*args, **kwargs):
            apply_points_delta(*args, **kwargs)
            raise RuntimeError('synthetic rollback after actual currencies')

        with patch('participants.views.apply_points_delta', side_effect=write_then_fail):
            with self.assertRaises(RuntimeError):
                self.call()
        self.assert_untouched(original)

    def test_submission_save_failure_rolls_back_actual_delta_and_audit(self):
        original = self.original_submission()
        with patch('participants.models.TaskSubmission.save', side_effect=RuntimeError('synthetic save failure')):
            with self.assertRaises(RuntimeError):
                self.call()
        self.assert_untouched(original)

    def test_ledger_descriptions_are_bounded_and_never_include_private_notes(self):
        # PostgreSQL enforces the task title's own limit before grading. Use
        # the longest valid title rather than an impossible production row.
        prefix = 'عنوان '
        maximum = WeeklyTask._meta.get_field('title').max_length
        title = prefix + 'م' * (maximum - len(prefix))
        WeeklyTask.objects.filter(pk=self.task.pk).update(title=title)
        self.assertTrue(self.call()[0])
        entry = PointsLedgerEntry.objects.get()
        self.assertLessEqual(len(entry.description), 255)
        self.assertNotIn(self.submission.revision_note, entry.description)
        self.assertNotIn(self.submission.rejection_reason, entry.description)

    def test_all_services_reject_malformed_bigauto_ids_without_driver_overflow(self):
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            for value in ('', 'abc', '0', '-1', '1.0', '9' * 5000, '9223372036854775808', '9223372036854775807', '١'):
                with self.subTest(action=action, value=value[:30]):
                    with self.assertRaises(Http404):
                        self.call(action, submission_id=value)
        self.assert_untouched(original)

    def test_all_services_require_single_present_aware_bounded_review_version(self):
        original = self.original_submission()
        invalid_versions = [
            '', 'not-time', '2026-99-99T99:99:99+03:00', '999999999999-01-01T00:00:00+03:00',
            '9' * 5000, self.submission.reviewed_at.replace(tzinfo=None).isoformat(),
            (self.submission.reviewed_at + datetime.timedelta(microseconds=1)).isoformat(),
        ]
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            for version in invalid_versions:
                with self.subTest(action=action, version=version[:40]):
                    self.assertFalse(self.call(action, review_version=version)[0])
            data = self.data(action)
            del data['review_version']
            self.assertFalse(self.call(action, data=data)[0])
            self.assertFalse(self.call(action, review_version=[self.submission.grade_revision_token] * 2)[0])
        self.assert_untouched(original)

    def test_version_parser_overflow_type_and_value_errors_are_safe(self):
        original = self.original_submission()
        for error in (OverflowError, TypeError, ValueError):
            for action in ('set_featured', 'revise_grade', 'cancel_grade'):
                with self.subTest(error=error, action=action):
                    with patch('participants.task_grading.parse_datetime', side_effect=error):
                        self.assertFalse(self.call(action)[0])
        self.assert_untouched(original)

    def test_legacy_empty_version_once_is_valid_but_missing_or_duplicate_is_not(self):
        self.set_review(reviewed_at=None, reviewed_by=None)
        original = self.original_submission()
        data = self.data()
        del data['review_version']
        self.assertFalse(self.call(data=data)[0])
        self.assertFalse(self.call(review_version=['', ''])[0])
        self.assert_untouched(original)
        self.assertTrue(self.call(review_version='')[0])
        self.assert_balances((102, 1020, 102))

    def test_duplicate_scalar_fields_never_select_last_value(self):
        original = self.original_submission()
        cases = [
            ('set_featured', 'is_featured', ['no', 'yes']),
            ('set_featured', 'previous_award', ['10', '12']),
            ('revise_grade', 'grade', ['accepted', 'featured']),
            ('revise_grade', 'reason', ['سبب أول', 'سبب آخر']),
            ('revise_grade', 'rejection_reason', ['', 'سبب دخيل']),
            ('revise_grade', 'previous_award', ['0', '10']),
            ('cancel_grade', 'confirm', ['no', 'yes']),
            ('cancel_grade', 'previous_award', ['0', '10']),
        ]
        for action, field, values in cases:
            with self.subTest(action=action, field=field):
                self.assertFalse(self.call(action, **{field: values})[0])
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.assertRaises(Http404):
                self.call(action, submission_id=[str(self.submission.pk)] * 2)
            data = self.data(action)
            data['action'] = [action, action]
            self.assertFalse(self.call(action, data=data)[0])
        self.assert_untouched(original)

    def test_all_services_deny_ungranted_roles_inactive_and_anonymous(self):
        inactive = User.objects.create_user(username='featured_inactive', role=Role.GENERAL_SUPERVISOR, is_active=False)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            for actor in (self.environment, self.system, self.limited, self.student, inactive, AnonymousUser()):
                with self.subTest(action=action, role=getattr(actor, 'role', 'anonymous')):
                    with self.assertRaises(PermissionDenied):
                        self.call(action, actor=actor)
        self.assert_untouched(original)

    def test_retained_user_cannot_acquire_new_role_powers(self):
        User.objects.filter(pk=self.system.pk).update(role=Role.GENERAL_SUPERVISOR)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(PermissionDenied):
                    self.call(action, actor=self.system)
        self.assert_untouched(original)

    def test_role_change_after_initial_check_is_rejected_under_actor_lock(self):
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            User.objects.filter(pk=self.general.pk).update(role=Role.GENERAL_SUPERVISOR)

            def initial_check_then_change_role(actor):
                User.objects.filter(pk=actor.pk).update(role=Role.LIMITED_SUPERVISOR)
                return True

            with self.subTest(action=action):
                with patch('participants.task_grading.can_revise_grades', side_effect=initial_check_then_change_role):
                    with self.assertRaises(PermissionDenied):
                        self.call(action)
        self.assert_untouched(original)

    def test_all_services_exclude_preserved_participant_profiles_with_nonstudent_role(self):
        User.objects.filter(pk=self.student.pk).update(role=Role.GENERAL_SUPERVISOR)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(Http404):
                    self.call(action)
        self.assert_untouched(original)

    def test_scope_is_rechecked_after_participant_lock_before_any_write(self):
        original = self.original_submission()
        original_get = QuerySet.get
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            User.objects.filter(pk=self.student.pk).update(role=Role.PARTICIPANT)

            def lock_then_change_role(queryset, *args, **kwargs):
                result = original_get(queryset, *args, **kwargs)
                if queryset.model is Participant:
                    User.objects.filter(pk=self.student.pk).update(role=Role.GENERAL_SUPERVISOR)
                return result

            with self.subTest(action=action):
                with patch.object(QuerySet, 'get', autospec=True, side_effect=lock_then_change_role):
                    with self.assertRaises(Http404):
                        self.call(action)
        self.assert_untouched(original)

    def grant_group_edit(self):
        TaskReviewPermission.objects.create(supervisor=self.environment, enabled=True, granted_by=self.general)
        SupervisorTaskGradePermission.objects.create(supervisor=self.environment, enabled=True, granted_by=self.general)

    def test_explicit_general_deny_overrides_default_for_all_three_services(self):
        SupervisorTaskGradePermission.objects.create(supervisor=self.general, enabled=False, granted_by=self.general)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(PermissionDenied):
                    self.call(action)
        self.assert_untouched(original)

    def test_group_explicit_edit_without_initial_review_permission_is_denied(self):
        SupervisorTaskGradePermission.objects.create(supervisor=self.environment, enabled=True, granted_by=self.general)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(PermissionDenied):
                    self.call(action, actor=self.environment)
        self.assert_untouched(original)

    def test_granted_group_can_edit_own_submission_but_all_services_404_outside_group(self):
        self.grant_group_edit()
        outside_group = Group.objects.create(name='بيئة خارج تفويض التميز')
        outside_user = User.objects.create_user(username='featured_outside_student', role=Role.PARTICIPANT)
        outside_participant = Participant.objects.create(user=outside_user, group=outside_group, points=80, miles=800, purchase_points=80)
        outside_submission = TaskSubmission.objects.create(
            task=self.task, participant=outside_participant, status='accepted',
            award_value=10, points_awarded=True, reviewed_at=timezone.now(),
        )
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(Http404):
                    self.call(action, actor=self.environment, submission_id=str(outside_submission.pk), review_version=outside_submission.grade_revision_token)
        self.assert_untouched(original)
        outside_participant.refresh_from_db()
        self.assertEqual((outside_participant.points, outside_participant.miles, outside_participant.purchase_points), (80, 800, 80))
        self.assertTrue(self.call(actor=self.environment)[0])
        self.assert_balances((102, 1020, 102))
        self.assertEqual(TaskGradeRevision.objects.get().revised_by_id, self.environment.pk)

    def test_group_scope_is_rechecked_after_participant_moves_while_acquiring_lock(self):
        self.grant_group_edit()
        outside_group = Group.objects.create(name='بيئة النقل المتداخل المصطنع')
        original = self.original_submission()
        original_get = QuerySet.get
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            Participant.objects.filter(pk=self.participant.pk).update(group=self.group)

            def lock_then_move_group(queryset, *args, **kwargs):
                result = original_get(queryset, *args, **kwargs)
                if queryset.model is Participant:
                    Participant.objects.filter(pk=self.participant.pk).update(group=outside_group)
                return result

            with self.subTest(action=action):
                with patch.object(QuerySet, 'get', autospec=True, side_effect=lock_then_move_group):
                    with self.assertRaises(Http404):
                        self.call(action, actor=self.environment)
        self.assert_untouched(original)

    def test_limited_edit_requires_follow_flag_and_allows_all_student_history_only(self):
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.limited, enabled=True, granted_by=self.general)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(PermissionDenied):
                    self.call(action, actor=self.limited)
        self.assert_untouched(original)
        SupervisorCapability.objects.create(supervisor=self.limited, follow_tasks=True, granted_by=self.general)
        self.assertTrue(self.call(actor=self.limited)[0])
        self.assert_balances((102, 1020, 102))
        policy.enabled = False
        policy.save(update_fields=['enabled'])
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(PermissionDenied):
                    self.call(action, actor=self.limited)
        self.assertEqual(TaskGradeRevision.objects.count(), 1)

    def test_prefetched_permission_does_not_survive_revocation(self):
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.general, enabled=True, granted_by=self.general)
        cached_actor = User.objects.select_related('task_grade_permission').get(pk=self.general.pk)
        self.assertTrue(cached_actor.task_grade_permission.enabled)
        SupervisorTaskGradePermission.objects.filter(pk=policy.pk).update(enabled=False)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            with self.subTest(action=action):
                with self.assertRaises(PermissionDenied):
                    self.call(action, actor=cached_actor)
        self.assert_untouched(original)

    def test_permission_revoked_after_initial_check_is_rechecked_under_user_lock(self):
        from .task_grading import can_revise_grades
        policy = SupervisorTaskGradePermission.objects.create(supervisor=self.general, enabled=True, granted_by=self.general)
        original = self.original_submission()
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            SupervisorTaskGradePermission.objects.filter(pk=policy.pk).update(enabled=True)
            calls = 0

            def check_then_revoke(actor):
                nonlocal calls
                calls += 1
                permitted = can_revise_grades(actor)
                if calls == 1:
                    SupervisorTaskGradePermission.objects.filter(pk=policy.pk).update(enabled=False)
                return permitted

            with self.subTest(action=action):
                with patch('participants.task_grading.can_revise_grades', side_effect=check_then_revoke):
                    with self.assertRaises(PermissionDenied):
                        self.call(action)
                self.assertEqual(calls, 2)
        self.assert_untouched(original)

    def test_services_use_user_no_key_then_submission_self_then_participant_locks(self):
        original_select_for_update = QuerySet.select_for_update
        for action in ('set_featured', 'revise_grade', 'cancel_grade'):
            self.set_review(status='accepted', award_value=10, points_awarded=True, is_featured=False, reviewed_at=timezone.now())
            locks = []

            def record_locks(queryset, *args, **kwargs):
                locks.append((queryset.model, kwargs))
                return original_select_for_update(queryset, *args, **kwargs)

            with self.subTest(action=action):
                with patch.object(QuerySet, 'select_for_update', autospec=True, side_effect=record_locks):
                    self.assertTrue(self.call(action)[0])
                self.assertEqual(locks, [(User, {'no_key': True}), (TaskSubmission, {'of': ('self',)}), (Participant, {})])
