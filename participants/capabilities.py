"""Fresh database checks for independent supervisor capabilities and reports."""

from django.db import transaction
from django.db.models import Q

from accounts.models import Role, User
from .models import SupervisorCapability, SupervisorReportPermission


def _authenticated_active(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "pk", None) is not None
    )


def _authenticated_identity(user):
    # Activity and role must come from the same fresh database read, not user.
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "pk", None) is not None)


def can_manage_store(user):
    """General/system roles retain store access; limited flags are never cached.

    Recheck the persisted role and activity as well as the capability: a user
    object retained by a caller must not keep powers after demotion/revocation.
    """
    if not _authenticated_active(user):
        return False
    return User.objects.filter(pk=user.pk, is_active=True).filter(
        Q(role__in=(Role.GENERAL_SUPERVISOR, Role.SUPERADMIN))
        | Q(role=Role.LIMITED_SUPERVISOR, limited_capabilities__manage_store=True)
    ).exists()


def can_follow_tasks(user):
    """Allow limited task follow-up and pending acceptance/rejection only.

    This existing flag includes ordinary/featured acceptance and rejection,
    not returns, task management, grade corrections or historical-award
    decisions. Always recheck the persisted role, activity and grant.
    """
    if not _authenticated_active(user):
        return False
    return User.objects.filter(
        pk=user.pk, is_active=True, role=Role.LIMITED_SUPERVISOR,
        limited_capabilities__follow_tasks=True,
    ).exists()


def student_management_role(user):
    """Return the current authorized role; this does not broaden report access."""
    if not _authenticated_identity(user):
        return None
    return User.objects.filter(pk=user.pk, is_active=True).filter(
        Q(role__in=(Role.GENERAL_SUPERVISOR, Role.SUPERADMIN))
        | Q(role__in=(Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR),
            limited_capabilities__manage_students=True)
    ).values_list("role", flat=True).first()


def can_manage_students(user):
    return student_management_role(user) is not None


def current_report_access(user):
    """Resolve role, activity and explicit policy in ONE fresh database read.

    None means legacy inheritance, not an explicit denial. Preparation always
    requires viewing, even for inconsistent rows written outside the form.
    SUPERADMIN retains student reports/PDF but never gains report-center access.
    """
    access = {"role": None, "can_view": False, "can_prepare": False, "can_center": False}
    if not _authenticated_identity(user):
        return access
    row = User.objects.filter(pk=user.pk).values(
        "role", "is_active", "report_permissions__pk",
        "report_permissions__view_reports", "report_permissions__prepare_reports",
    ).first()
    if row is None:
        return access
    role = row["role"]
    access["role"] = role
    if not row["is_active"] or role not in (
        Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR,
        Role.GENERAL_SUPERVISOR, Role.SUPERADMIN,
    ):
        return access
    explicit = row["report_permissions__pk"] is not None
    if explicit:
        view = bool(row["report_permissions__view_reports"])
        prepare = view and bool(row["report_permissions__prepare_reports"])
    else:
        view = prepare = role != Role.LIMITED_SUPERVISOR
    access.update(
        can_view=view, can_prepare=prepare,
        can_center=view and (
            role == Role.GENERAL_SUPERVISOR
            or (explicit and role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR))
        ),
    )
    return access


def can_view_reports(user):
    return current_report_access(user)["can_view"]


def can_prepare_reports(user):
    return current_report_access(user)["can_prepare"]


def can_view_report_center(user):
    return current_report_access(user)["can_center"]


@transaction.atomic
def revoke_capabilities(user, actor):
    """Clear existing flags once; never create a row or mutate User attributes.

    Management callers already hold the supervisor User lock, serializing this
    operation with grants and in-flight authorized store writes. The capability
    row itself is locked here too. Disabled/missing rows are strict no-ops.
    """
    capability = SupervisorCapability.objects.select_for_update().filter(
        supervisor_id=user.pk,
    ).first()
    if capability is None or not (
        capability.manage_store or capability.follow_tasks or capability.manage_students
    ):
        return False
    capability.manage_store = False
    capability.follow_tasks = False
    capability.manage_students = False
    capability.granted_by = actor
    capability.save(update_fields=[
        "manage_store", "follow_tasks", "manage_students", "granted_by", "updated_at",
    ])
    return True


@transaction.atomic
def revoke_report_grants(user, actor):
    """Revoke only explicit positives; preserve missing policies and denied rows.

    Lifecycle callers must hold the User lock first, as permission configuration
    does, so a positive grant cannot race a role/activity change. A denial row is
    durable across those changes; no inherited False policy is created here.
    """
    policy = SupervisorReportPermission.objects.select_for_update().filter(
        supervisor_id=user.pk,
    ).first()
    if policy is None or not (policy.view_reports or policy.prepare_reports):
        return False
    policy.view_reports = False
    policy.prepare_reports = False
    policy.granted_by = actor
    policy.save(update_fields=["view_reports", "prepare_reports", "granted_by", "updated_at"])
    return True
