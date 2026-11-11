"""Operational boundaries, independent of read-only report permissions."""
from django.shortcuts import get_object_or_404
from accounts.models import Role, User
from .models import Group, Participant, TaskReviewPermission, TaskSubmission


OPERATIONAL_ROLES = (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)


def operational_role(user):
    """Read current identity/activity; never authorize a cached User or role."""
    if not getattr(user, "is_authenticated", False) or getattr(user, "pk", None) is None:
        return None
    # Like the task POST actor lock, a retained object must not silently acquire
    # a different role's powers. Ordinary requests load the current User afresh.
    return User.objects.filter(
        pk=user.pk, is_active=True, role=getattr(user, "role", None),
    ).values_list("role", flat=True).first()


def allowed_groups(user, *, _role=None):
    """Attendance/ledger scope only; report scopes stay in reporting.py.

    LIMITED grants are purpose-specific and never grant attendance or ledger.
    Query the M2M through Group, not a possibly prefetched user.group_set.
    """
    role = operational_role(user) if _role is None else _role
    if role == Role.GROUP_SUPERVISOR:
        return Group.objects.filter(supervisor__pk=user.pk).order_by("name")
    if role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN):
        return Group.objects.all().order_by("name")
    return Group.objects.none()


def scoped_participants(user, *, _role=None):
    """Operational student scope, including preserved inactive student history."""
    role = operational_role(user) if _role is None else _role
    students = Participant.objects.filter(user__role=Role.PARTICIPANT)
    if role == Role.GROUP_SUPERVISOR:
        return students.filter(group__in=allowed_groups(user, _role=role))
    if role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN):
        return students
    return students.none()


def can_access_operational_roster(user):
    return operational_role(user) in OPERATIONAL_ROLES


def attendance_group(request):
    role = operational_role(request.user)
    groups = allowed_groups(request.user, _role=role)
    raw = request.POST.get("group") or request.GET.get("group")
    if raw:
        if not raw.isdecimal():
            from django.http import Http404
            raise Http404("بيئة غير متاحة")
        return get_object_or_404(groups, pk=raw)
    return groups.first() if role == Role.GROUP_SUPERVISOR else None


def can_view_meeting_notes(user):
    # Intentionally excludes SUPERADMIN: these notes remain available only to
    # environment/general supervisors, never by a report or LIMITED grant.
    return operational_role(user) in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR)


def can_manage_tasks(user):
    return operational_role(user) in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)


def can_prepare_elite_trip(user):
    """Elite-trip preparation is available to active group/general supervisors only."""
    return operational_role(user) in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR)


def can_review_task_submissions(user):
    role = operational_role(user)
    # LIMITED uses the explicit fresh flag, never legacy group delegation.
    if role == Role.LIMITED_SUPERVISOR:
        from .capabilities import can_follow_tasks
        return can_follow_tasks(user)
    if role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN):
        return True
    return role == Role.GROUP_SUPERVISOR and TaskReviewPermission.objects.filter(
        supervisor_id=user.pk, enabled=True,
    ).exists()


def scoped_submissions(user):
    role = operational_role(user)
    if role in OPERATIONAL_ROLES:
        return TaskSubmission.objects.filter(participant__in=scoped_participants(user, _role=role))
    from .capabilities import can_follow_tasks
    if role == Role.LIMITED_SUPERVISOR and can_follow_tasks(user):
        return TaskSubmission.objects.filter(participant__user__role=Role.PARTICIPANT)
    return TaskSubmission.objects.none()


def task_participants(user):
    """Task-only roster; reporting/student profiles keep their separate scope."""
    role = operational_role(user)
    from .capabilities import can_follow_tasks
    if role == Role.LIMITED_SUPERVISOR and can_follow_tasks(user):
        return Participant.objects.filter(user__role=Role.PARTICIPANT)
    return scoped_participants(user, _role=role)


def can_view_task_submissions(user):
    role = operational_role(user)
    if role in OPERATIONAL_ROLES:
        return True
    from .capabilities import can_follow_tasks
    return role == Role.LIMITED_SUPERVISOR and can_follow_tasks(user)
