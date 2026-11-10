"""Fail-closed, fresh database checks for explicitly limited supervisors."""

from django.db import transaction
from django.db.models import Q

from accounts.models import Role, User
from .models import SupervisorCapability


def _authenticated_active(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "pk", None) is not None
    )


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
    """This is limited read-only delegation, not general review permission."""
    if not _authenticated_active(user):
        return False
    return User.objects.filter(
        pk=user.pk, is_active=True, role=Role.LIMITED_SUPERVISOR,
        limited_capabilities__follow_tasks=True,
    ).exists()


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
    if capability is None or not (capability.manage_store or capability.follow_tasks):
        return False
    capability.manage_store = False
    capability.follow_tasks = False
    capability.granted_by = actor
    capability.save(update_fields=["manage_store", "follow_tasks", "granted_by", "updated_at"])
    return True
