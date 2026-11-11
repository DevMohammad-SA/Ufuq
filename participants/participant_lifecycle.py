"""Safe participant account archiving and status filtering.

Archiving changes only ``User.is_active``.  The Participant row and every
program record remain in place for supervisory history and possible restore.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

from accounts.models import Role
from .management_views import (
    AccountManagementView,
    can_manage_accounts,
    locked_users,
    record_management,
)
from .models import Participant
from .capabilities import student_management_role
from .student_management import (
    ACCOUNT_MANAGER_ROLES, can_manage_students, can_view_student_reports,
    student_management_participants,
)


PARTICIPANT_STATUS_VALUES = ("active", "inactive", "all")


def participant_status(request):
    """Return the safe roster status selected by an account manager.

    Environment supervisors always receive the active roster, even when they
    craft another value in the query string.  Invalid values also fall back to
    active so the default roster cannot accidentally expose archived accounts.
    """
    status = request.GET.get("status", "active")
    if status not in PARTICIPANT_STATUS_VALUES:
        return "active"
    return status if can_manage_accounts(request.user) else "active"


def filter_participant_status(queryset, request):
    """Apply the request's safe status selection to a Participant queryset."""
    status = participant_status(request)
    if status == "all":
        return queryset
    return queryset.filter(user__is_active=status == "active")


@method_decorator(never_cache, name="dispatch")
class ParticipantStatusView(AccountManagementView):
    """Shared confirm-only state transition for a participant account."""

    template_name = "participants/participant_status_confirm.html"
    desired_is_active = None
    action_label = ""
    success_message = ""
    unchanged_message = ""

    def test_func(self):
        # Restoration never inherits delegated student-management authority.
        if self.desired_is_active:
            return student_management_role(self.request.user) in ACCOUNT_MANAGER_ROLES
        return can_manage_students(self.request.user)

    def actor_check(self, user):
        if self.desired_is_active:
            return can_manage_accounts(user)
        return can_manage_students(user)

    def target(self, pk):
        return get_object_or_404(
            student_management_participants(self.request.user).select_related("user", "group"),
            pk=pk,
        )

    def confirmation_context(self, participant):
        destination = "participants:student_management"
        if student_management_role(self.request.user) in ACCOUNT_MANAGER_ROLES and can_view_student_reports(self.request.user):
            destination = "participants:participants_data"
        cancel_url = "{}?status={}".format(
            reverse(destination), "inactive" if self.desired_is_active else "active",
        )
        return self.context(
            "data",
            participant=participant,
            is_restore=self.desired_is_active,
            action_label=self.action_label,
            can_manage_accounts=student_management_role(self.request.user) in ACCOUNT_MANAGER_ROLES,
            can_manage_students=can_manage_students(self.request.user),
            can_restore_students=student_management_role(self.request.user) in ACCOUNT_MANAGER_ROLES,
            can_view_reports=can_view_student_reports(self.request.user),
            status_cancel_url=cancel_url,
            management_return_url=cancel_url,
        )

    def render_confirmation(self, participant, status=200):
        return render(
            self.request,
            self.template_name,
            self.confirmation_context(participant),
            status=status,
        )

    def get(self, request, pk):
        return self.render_confirmation(self.target(pk))

    def post(self, request, pk):
        # The target is read once for a helpful confirmation-error response.
        # Its identity and privileges are checked again under row locks below.
        participant = self.target(pk)
        if request.POST.getlist("confirm") != ["yes"]:
            messages.error(request, "يجب تأكيد الإجراء أولًا؛ لم تتغير حالة الحساب")
            return self.render_confirmation(participant, status=400)

        changed = False
        with transaction.atomic():
            # ``locked_users`` takes actor/target locks in primary-key order and
            # rechecks the actor's active management authority inside the lock.
            actor, user = locked_users(
                request.user.pk, participant.user_id, no_key=True,
                actor_check=self.actor_check,
            )
            if actor.pk == user.pk:
                raise PermissionDenied("لا يمكنك تغيير حالة حسابك من هذه الصفحة")
            if (
                user.role != Role.PARTICIPANT
                or user.is_staff
                or user.is_superuser
            ):
                raise PermissionDenied

            # Lock the program row only after the consistently ordered User
            # locks.  It must still be the same participant target at commit.
            participant = get_object_or_404(
                student_management_participants(actor).select_for_update(of=("self",)),
                pk=pk, user_id=user.pk,
            )
            request.user = actor
            if user.is_active != self.desired_is_active:
                user.is_active = self.desired_is_active
                user.save(update_fields=["is_active"])
                record_management(actor, user, self.action_label)
                changed = True

        if changed:
            messages.success(request, self.success_message)
        else:
            messages.info(request, self.unchanged_message)
        destination = "participants:student_management"
        if actor.role in ACCOUNT_MANAGER_ROLES and can_view_student_reports(actor):
            destination = "participants:participants_data"
        return redirect(
            "{}?status={}".format(
                reverse(destination),
                "active" if self.desired_is_active else "inactive",
            )
        )


class ParticipantDeleteView(ParticipantStatusView):
    """Archive a participant account without deleting any program history."""

    desired_is_active = False
    action_label = "حذف آمن للمشارك: تعطيل الحساب مع حفظ السجلات"
    success_message = "تم أرشفة حساب المشارك مع حفظ أرصدته وسجلاته، ويمكن إعادة تفعيله لاحقًا"
    unchanged_message = "حساب المشارك مؤرشف بالفعل؛ لم يتغير شيء"


class ParticipantRestoreView(ParticipantStatusView):
    """Reactivate an archived participant account without recalculation."""

    desired_is_active = True
    action_label = "إعادة تفعيل حساب المشارك المؤرشف"
    success_message = "تمت إعادة تفعيل حساب المشارك دون تغيير الأرصدة أو السجلات"
    unchanged_message = "حساب المشارك نشط بالفعل؛ لم يتغير شيء"
