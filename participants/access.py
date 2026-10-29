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


def can_review_task_submissions(user):
    if can_manage_tasks(user):
        return True
    return user.role == Role.GROUP_SUPERVISOR and TaskReviewPermission.objects.filter(
        supervisor=user, enabled=True,
    ).exists()


def scoped_submissions(user):
    return TaskSubmission.objects.filter(participant__in=scoped_participants(user))
