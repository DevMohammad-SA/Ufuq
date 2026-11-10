"""Shared boundaries for administrative student data and delegated review."""
from django.shortcuts import get_object_or_404
from accounts.models import Role
from .models import TaskReviewPermission, TaskSubmission
from .reporting import REPORT_ROLES, allowed_groups, scoped_participants


def attendance_group(request):
    groups = allowed_groups(request.user)
    raw = request.POST.get("group") or request.GET.get("group")
    if raw:
        if not raw.isdecimal():
            from django.http import Http404
            raise Http404("بيئة غير متاحة")
        return get_object_or_404(groups, pk=raw)
    return groups.first() if request.user.role == Role.GROUP_SUPERVISOR else None


def can_view_meeting_notes(user):
    # Intentionally excludes SUPERADMIN: the owner's request says these
    # notes are visible to environment supervisors and general supervisor ONLY.
    return user.role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR)


def can_manage_tasks(user):
    return user.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)


def can_prepare_elite_trip(user):
    """Elite-trip preparation is available to active group/general supervisors only."""
    return user.is_active and user.role in (
        Role.GROUP_SUPERVISOR,
        Role.GENERAL_SUPERVISOR,
    )


def can_review_task_submissions(user):
    # A review delegation must never turn limited follow-up into grading.
    if user.role == Role.LIMITED_SUPERVISOR:
        return False
    if can_manage_tasks(user):
        return True
    return user.role == Role.GROUP_SUPERVISOR and TaskReviewPermission.objects.filter(
        supervisor=user, enabled=True,
    ).exists()


def scoped_submissions(user):
    if user.role in REPORT_ROLES:
        return TaskSubmission.objects.filter(participant__in=scoped_participants(user))
    from .capabilities import can_follow_tasks
    if user.role == Role.LIMITED_SUPERVISOR and can_follow_tasks(user):
        return TaskSubmission.objects.filter(participant__user__role=Role.PARTICIPANT)
    return TaskSubmission.objects.none()


def task_participants(user):
    """Task-only roster; reporting/student profiles keep their original scope."""
    from .capabilities import can_follow_tasks
    from .models import Participant
    if user.role == Role.LIMITED_SUPERVISOR and can_follow_tasks(user):
        return Participant.objects.filter(user__role=Role.PARTICIPANT)
    return scoped_participants(user)


def can_view_task_submissions(user):
    from .capabilities import can_follow_tasks
    return bool(user.is_active and user.role in REPORT_ROLES) or can_follow_tasks(user)
