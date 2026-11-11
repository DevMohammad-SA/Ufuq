"""Basic student account management, independent of report/points access."""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from accounts.models import Role, User
from . import capabilities
from .capabilities import can_manage_students
from .models import Group, Participant


ACCOUNT_MANAGER_ROLES = (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)


def student_management_groups(user):
    """Resolve current authorization and every currently assigned environment.

    Never use user.group_set: it may have been prefetched before a reassignment.
    An unauthorized caller gets an empty queryset, not global access.
    """
    role = capabilities.student_management_role(user)
    if role == Role.GROUP_SUPERVISOR:
        return Group.objects.filter(supervisor__pk=user.pk).order_by("name", "pk")
    if role in (*ACCOUNT_MANAGER_ROLES, Role.LIMITED_SUPERVISOR):
        return Group.objects.order_by("name", "pk")
    return Group.objects.none()


def student_management_participants(user):
    """Only ordinary student accounts, active or archived, within current scope."""
    students = Participant.objects.filter(
        user__role=Role.PARTICIPANT, user__is_staff=False, user__is_superuser=False,
    )
    role = capabilities.student_management_role(user)
    if role == Role.GROUP_SUPERVISOR:
        return students.filter(group__in=student_management_groups(user))
    if role in (*ACCOUNT_MANAGER_ROLES, Role.LIMITED_SUPERVISOR):
        return students
    return students.none()


def can_view_student_reports(user):
    """Use the independent policy, with legacy redirects during integration."""
    check = getattr(capabilities, "can_view_reports", None)
    if check is not None:
        return check(user)
    return User.objects.filter(
        pk=getattr(user, "pk", None), is_active=True,
        role__in=(Role.GROUP_SUPERVISOR, *ACCOUNT_MANAGER_ROLES),
    ).exists()


@method_decorator(never_cache, name="dispatch")
class StudentManagementListView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "participants/student_management_list.html"
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "head"]

    def test_func(self):
        role = capabilities.student_management_role(self.request.user)
        return role is not None and (
            role != Role.GROUP_SUPERVISOR
            or student_management_groups(self.request.user).exists()
        )

    def get(self, request):
        # Use the current actor for navigation and context too, not a stale role.
        actor = User.objects.filter(pk=request.user.pk, is_active=True).first()
        role = capabilities.student_management_role(actor)
        if actor is None or role is None:
            raise PermissionDenied
        groups = student_management_groups(actor)
        if role == Role.GROUP_SUPERVISOR and not groups.exists():
            raise PermissionDenied
        request.user = actor
        q = request.GET.get("q", "").strip()[:100]
        status = request.GET.get("status", "active")
        if status not in ("active", "inactive", "all"):
            status = "active"
        group_value = request.GET.get("group", "").strip()
        students = student_management_participants(actor).select_related("user", "group").only(
            "pk", "user_id", "group_id", "academic_stage", "phone", "guardian_phone",
            "user__id", "user__full_name", "user__national_id", "user__username", "user__is_active",
            "group__id", "group__name",
        ).order_by("user__full_name", "pk")
        if q:
            students = students.filter(
                Q(user__full_name__icontains=q) | Q(user__national_id__icontains=q)
                | Q(user__username__icontains=q) | Q(phone__icontains=q)
                | Q(guardian_phone__icontains=q),
            )
        if status != "all":
            students = students.filter(user__is_active=status == "active")
        if group_value:
            # An unknown/out-of-scope filter fails closed; never broadens scope.
            group = groups.filter(pk=int(group_value)).first() if group_value.isdecimal() and len(group_value) < 19 else None
            students = students.filter(group=group) if group is not None else students.none()
        page = Paginator(students, 30).get_page(request.GET.get("page"))
        params = request.GET.copy()
        params.pop("page", None)
        from .views import build_navbar
        report_check = getattr(capabilities, "can_view_reports", None)
        return render(request, self.template_name, {
            "students": page.object_list, "page_obj": page,
            "q": q, "status": status, "group": group_value,
            "selected_group": group_value, "group_value": group_value, "groups": groups,
            "filter_values": {"q": q, "status": status, "group": group_value},
            "querystring": params.urlencode(),
            "navbar_items": build_navbar(actor, "student_management"),
            "can_manage_accounts": role in ACCOUNT_MANAGER_ROLES,
            "can_manage_students": True,
            "can_restore_students": role in ACCOUNT_MANAGER_ROLES,
            "can_view_reports": bool(report_check(actor)) if report_check else False,
        })
