"""General-supervisor management without granting Django-admin privileges."""

import json

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from accounts.models import Role, User
from .capabilities import can_follow_tasks, can_manage_store, revoke_capabilities
from .management_forms import (
    MANAGED_SUPERVISOR_ROLES, ParticipantEditForm, SupervisorCapabilitiesForm,
    SupervisorManagementForm,
)
from .management_locking import lock_login_identifiers
from .models import Participant, SupervisorCapability, TaskReviewPermission
from .views import build_navbar


def can_manage_accounts(user):
    return bool(user.is_authenticated and user.is_active and user.role in (
        Role.GENERAL_SUPERVISOR, Role.SUPERADMIN,
    ))


def managed_supervisors():
    # Use a subquery rather than a nullable join: safe for PostgreSQL row locks.
    return User.objects.filter(
        role__in=MANAGED_SUPERVISOR_ROLES, is_staff=False, is_superuser=False,
    ).exclude(pk__in=Participant.objects.values("user_id"))


def visible_supervisors():
    # Admin flags must protect writes, not hide an existing platform supervisor.
    return User.objects.filter(role__in=MANAGED_SUPERVISOR_ROLES).annotate(
        has_participant_profile=Exists(Participant.objects.filter(user_id=OuterRef("pk"))),
    )


def general_grant_supervisors():
    # Role-only promotion preserves existing staff access and all credentials.
    # Full admin and participant-backed accounts remain ineligible for changes.
    return User.objects.filter(
        role__in=MANAGED_SUPERVISOR_ROLES, is_superuser=False,
    ).exclude(pk__in=Participant.objects.values("user_id"))


def capability_supervisors():
    # Deliberately separate from strict edit/delete scope: preserve staff flags.
    return User.objects.filter(
        role__in=(Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR),
        is_superuser=False,
    ).exclude(pk__in=Participant.objects.values("user_id")).filter(
        Q(is_active=True) | Q(role=Role.LIMITED_SUPERVISOR),
    )


def can_configure_capabilities(user):
    return bool(
        user.is_authenticated and user.is_active
        and user.role == Role.GENERAL_SUPERVISOR
        and User.objects.filter(pk=user.pk, role=Role.GENERAL_SUPERVISOR, is_active=True).exists()
    )


def locked_users(actor_id, target_id=None, *, no_key=False):
    # Consistent order prevents managers editing each other from deadlocking.
    # Promotions do not change keys: allow concurrent historical actor FKs.
    ids = {actor_id}
    if target_id is not None:
        ids.add(target_id)
    rows = {
        user.pk: user for user in User.objects.select_for_update(no_key=no_key)
        .filter(pk__in=ids).order_by("pk")
    }
    actor = rows.get(actor_id)
    if actor is None or not can_manage_accounts(actor):
        raise PermissionDenied
    target = rows.get(target_id)
    if target_id is not None and target is None:
        get_object_or_404(User, pk=target_id)
    return actor, target


def require_managed_supervisor(user):
    if not managed_supervisors().filter(pk=user.pk).exists():
        get_object_or_404(managed_supervisors(), pk=user.pk)


def record_management(actor, obj, label, flag=CHANGE):
    # Only field/event labels are logged, never identifiers or passwords.
    LogEntry.objects.create(
        user_id=actor.pk, content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk), object_repr=str(obj)[:200], action_flag=flag,
        change_message=json.dumps([{"changed": {"fields": [label]}}], ensure_ascii=False),
    )


def revoke_review(user, actor):
    permission = TaskReviewPermission.objects.select_for_update().filter(supervisor=user).first()
    if permission is not None and permission.enabled:
        permission.enabled = False
        permission.granted_by = actor
        permission.save(update_fields=["enabled", "granted_by", "updated_at"])


@method_decorator(never_cache, name="dispatch")
class AccountManagementView(LoginRequiredMixin, UserPassesTestMixin, View):
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "post", "head", "options"]

    def test_func(self):
        return can_manage_accounts(self.request.user)

    def context(self, key, **kwargs):
        return {"navbar_items": build_navbar(self.request.user, key), "can_manage_accounts": True, **kwargs}


class ParticipantEditView(AccountManagementView):
    template_name = "participants/participant_edit.html"

    def participant(self, pk):
        return get_object_or_404(
            Participant.objects.select_related("user", "group").filter(
                user__role=Role.PARTICIPANT, user__is_staff=False, user__is_superuser=False,
            ), pk=pk,
        )

    def show(self, form, participant):
        return render(self.request, self.template_name, self.context(
            "data", form=form, participant=participant,
        ))

    def get(self, request, pk):
        participant = self.participant(pk)
        return self.show(ParticipantEditForm(participant=participant), participant)

    def post(self, request, pk):
        participant = self.participant(pk)
        form = None
        try:
            with transaction.atomic():
                actor, user = locked_users(request.user.pk, participant.user_id)
                if user.role != Role.PARTICIPANT or user.is_staff or user.is_superuser:
                    raise PermissionDenied
                participant = get_object_or_404(Participant.objects.select_for_update(), pk=pk, user_id=user.pk)
                participant.user = user
                form = ParticipantEditForm(request.POST, participant=participant)
                lock_login_identifiers(request.POST)
                if form.is_valid():
                    user.full_name = form.cleaned_data["full_name"]
                    user.national_id = form.cleaned_data["national_id"]
                    user.save(update_fields=["full_name", "national_id"])
                    for field in ("group", "academic_stage", "phone", "guardian_phone"):
                        setattr(participant, field, form.cleaned_data[field])
                    participant.save(update_fields=["group", "academic_stage", "phone", "guardian_phone"])
                    record_management(actor, participant, "بيانات المشارك الأساسية")
                    messages.success(request, "تم تحديث بيانات المشارك دون تغيير أرصدته أو سجلاته")
                    return redirect("participants:student_detail", pk=participant.pk)
        except IntegrityError:
            form.add_error(None, "تعذر الحفظ بسبب معرّف مكرر أو تغيير متزامن؛ راجع البيانات وحاول مجددًا")
        return self.show(form, participant)


class SupervisorListView(AccountManagementView):
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        q = request.GET.get("q", "").strip()[:100]
        status = request.GET.get("status", "active")
        if status not in ("active", "inactive", "all"):
            status = "active"
        role = request.GET.get("role", "")
        if role not in MANAGED_SUPERVISOR_ROLES:
            role = ""
        supervisors = visible_supervisors().select_related("limited_capabilities").prefetch_related("group_set").order_by("full_name", "pk")
        if q:
            supervisors = supervisors.filter(
                Q(full_name__icontains=q) | Q(username__icontains=q) | Q(national_id__icontains=q),
            )
        if status != "all":
            supervisors = supervisors.filter(is_active=status == "active")
        if role:
            supervisors = supervisors.filter(role=role)
        page = Paginator(supervisors, 30).get_page(request.GET.get("page"))
        rows = list(page.object_list)
        actor_can_configure = can_configure_capabilities(request.user)
        for supervisor in rows:
            supervisor.can_edit_supervisor = not (
                supervisor.is_staff or supervisor.is_superuser or supervisor.has_participant_profile
            )
            supervisor.can_grant_general = bool(
                supervisor.is_active and supervisor.role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR)
                and not supervisor.is_superuser and not supervisor.has_participant_profile
                and supervisor.pk != request.user.pk
            )
            supervisor.can_configure_capabilities = bool(
                actor_can_configure
                and supervisor.role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR)
                and (supervisor.is_active or supervisor.role == Role.LIMITED_SUPERVISOR)
                and not supervisor.is_superuser and not supervisor.has_participant_profile
                and supervisor.pk != request.user.pk
            )
            capability = getattr(supervisor, "limited_capabilities", None)
            supervisor.capabilities_labels = []
            if supervisor.role == Role.LIMITED_SUPERVISOR and capability is not None:
                if capability.manage_store:
                    supervisor.capabilities_labels.append("إدارة المتجر")
                if capability.follow_tasks:
                    supervisor.capabilities_labels.append("متابعة المهام للقراءة فقط")
            if supervisor.is_superuser:
                supervisor.protection_note = "حساب إداري محمي؛ يُدار من مشرف النظام."
            elif supervisor.has_participant_profile:
                supervisor.protection_note = "مرتبط بملف طالب؛ يلزم مراجعة مشرف النظام."
            elif supervisor.is_staff:
                supervisor.protection_note = "بيانات الحساب الإدارية محمية؛ منح الدور لا يغيّرها."
            else:
                supervisor.protection_note = ""
        page.object_list = rows
        params = request.GET.copy()
        params.pop("page", None)
        return render(request, "participants/supervisor_list.html", self.context(
            "supervisors", supervisors=rows, page_obj=page,
            querystring=params.urlencode(), q=q, status=status, role=role,
            role_choices=[(value, Role(value).label) for value in MANAGED_SUPERVISOR_ROLES],
        ))


class SupervisorFormView(AccountManagementView):
    template_name = "participants/supervisor_form.html"

    def target(self, pk=None):
        return get_object_or_404(managed_supervisors(), pk=pk) if pk is not None else None

    def show(self, form, supervisor):
        return render(self.request, self.template_name, self.context(
            "supervisors", form=form, supervisor=supervisor, is_create=supervisor is None,
        ))

    def get(self, request, pk=None):
        supervisor = self.target(pk)
        return self.show(SupervisorManagementForm(actor=request.user, supervisor=supervisor), supervisor)

    def post(self, request, pk=None):
        supervisor = self.target(pk)
        original_supervisor = supervisor
        form = None
        try:
            with transaction.atomic():
                actor, locked = locked_users(request.user.pk, pk)
                if locked is not None:
                    require_managed_supervisor(locked)
                supervisor = locked
                form = SupervisorManagementForm(request.POST, actor=actor, supervisor=supervisor)
                lock_login_identifiers(request.POST)
                if form.is_valid():
                    is_new = supervisor is None
                    supervisor = supervisor or User(is_staff=False, is_superuser=False)
                    previous_role = supervisor.role
                    previous_active = supervisor.is_active
                    previous_groups = set(supervisor.group_set.values_list("pk", flat=True)) if not is_new else set()
                    for field in ("full_name", "username", "national_id", "role", "is_active"):
                        setattr(supervisor, field, form.cleaned_data[field])
                    password = form.cleaned_data.get("password1")
                    if password:
                        supervisor.set_password(password)
                    if is_new:
                        supervisor.must_set_password = False
                        supervisor.save()
                    else:
                        fields = ["full_name", "username", "national_id", "role", "is_active"]
                        if password:
                            fields.append("password")
                        supervisor.save(update_fields=fields)
                    selected = form.cleaned_data["environments"]
                    current_groups = {group.pk for group in selected}
                    if supervisor.role == Role.GROUP_SUPERVISOR:
                        supervisor.group_set.set(selected)
                    else:
                        supervisor.group_set.clear()
                        current_groups = set()
                    if not supervisor.is_active or previous_role != supervisor.role or previous_groups != current_groups:
                        revoke_review(supervisor, actor)
                    if not is_new and (
                        not supervisor.is_active or previous_role != supervisor.role
                        or previous_active != supervisor.is_active
                    ):
                        revoke_capabilities(supervisor, actor)
                    record_management(actor, supervisor, "إضافة مشرف" if is_new else "بيانات المشرف", ADDITION if is_new else CHANGE)
                    if supervisor.pk == request.user.pk and password:
                        update_session_auth_hash(request, supervisor)
                    messages.success(request, "تمت إضافة المشرف" if is_new else "تم تحديث بيانات المشرف")
                    if is_new and actor.role == Role.GENERAL_SUPERVISOR and supervisor.role == Role.LIMITED_SUPERVISOR:
                        return redirect("participants:supervisor_capabilities", pk=supervisor.pk)
                    return redirect("participants:supervisor_list")
        except IntegrityError:
            form.add_error(None, "تعذر الحفظ؛ اسم المستخدم أو الهوية مسجلان مسبقًا أو تغيّرت البيانات بالتزامن")
        return self.show(form, original_supervisor)


class SupervisorGrantGeneralView(AccountManagementView):
    template_name = "participants/supervisor_grant_general_confirm.html"

    def target(self, pk, *, allow_general=False):
        supervisors = general_grant_supervisors().filter(is_active=True)
        if not allow_general:
            supervisors = supervisors.filter(role__in=(Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR))
        return get_object_or_404(supervisors, pk=pk)

    def show(self, supervisor, *, status=200):
        return render(self.request, self.template_name, self.context(
            "supervisors", supervisor=supervisor,
        ), status=status)

    def get(self, request, pk):
        return self.show(self.target(pk))

    def post(self, request, pk):
        target = self.target(pk, allow_general=True)
        if target.pk == request.user.pk:
            raise PermissionDenied("لا يمكن استخدام هذا الإجراء على حسابك أنت")
        if request.POST.getlist("confirm") != ["yes"]:
            messages.error(request, "أكّد منح الصلاحية أولًا؛ لم يتغير أي حساب")
            return self.show(target, status=400)

        with transaction.atomic():
            actor, supervisor = locked_users(request.user.pk, pk, no_key=True)
            get_object_or_404(general_grant_supervisors(), pk=supervisor.pk)
            if actor.pk == supervisor.pk or not supervisor.is_active:
                raise PermissionDenied
            already_general = supervisor.role == Role.GENERAL_SUPERVISOR
            if not already_general:
                supervisor.role = Role.GENERAL_SUPERVISOR
                supervisor.save(update_fields=["role"])
                supervisor.group_set.clear()
                revoke_review(supervisor, actor)
                revoke_capabilities(supervisor, actor)
                record_management(actor, supervisor, "منح صلاحية المشرف العام")

        if already_general:
            messages.info(request, "هذا المشرف يملك صلاحية المشرف العام بالفعل؛ لم يتغير أي حساب")
        else:
            messages.success(request, "تم منح صلاحية المشرف العام؛ صلاحيات حسابك لم تتغير")
        return redirect("participants:supervisor_list")


class SupervisorDeleteView(AccountManagementView):
    def target(self, pk):
        supervisor = get_object_or_404(managed_supervisors(), pk=pk)
        if supervisor.pk == self.request.user.pk:
            raise PermissionDenied("لا يمكنك حذف حسابك أنت")
        return supervisor

    def get(self, request, pk):
        return render(request, "participants/supervisor_delete_confirm.html", self.context(
            "supervisors", supervisor=self.target(pk),
        ))

    def post(self, request, pk):
        target = self.target(pk)
        if request.POST.get("confirm") != "yes":
            messages.error(request, "يجب تأكيد حذف المشرف أولًا؛ لم يتغير الحساب")
            return render(request, "participants/supervisor_delete_confirm.html", self.context(
                "supervisors", supervisor=target,
            ), status=400)
        with transaction.atomic():
            actor, supervisor = locked_users(request.user.pk, pk)
            require_managed_supervisor(supervisor)
            if actor.pk == supervisor.pk:
                raise PermissionDenied
            supervisor.is_active = False
            supervisor.save(update_fields=["is_active"])
            revoke_review(supervisor, actor)
            revoke_capabilities(supervisor, actor)
            record_management(actor, supervisor, "حذف آمن للمشرف: تعطيل الحساب مع حفظ السجلات")
        messages.success(request, "تم حذف المشرف من الحسابات النشطة؛ حُفظت سجلاته ويمكن إعادة تفعيله من الحسابات المعطلة")
        return redirect("participants:supervisor_list")


class SupervisorCapabilitiesView(AccountManagementView):
    template_name = "participants/supervisor_capabilities_form.html"

    def test_func(self):
        return can_configure_capabilities(self.request.user)

    def target(self, pk):
        if pk == self.request.user.pk:
            raise PermissionDenied("لا يمكن استخدام هذا الإجراء على حسابك أنت")
        return get_object_or_404(capability_supervisors(), pk=pk)

    def show(self, form, supervisor, *, status=200):
        return render(self.request, self.template_name, self.context(
            "supervisors", form=form, supervisor=supervisor,
            can_grant_capabilities=supervisor.is_active,
            is_inactive=not supervisor.is_active,
        ), status=status)

    def get(self, request, pk):
        supervisor = self.target(pk)
        capability = SupervisorCapability.objects.filter(supervisor=supervisor).first()
        initial = {"manage_store": False, "follow_tasks": False}
        if supervisor.role == Role.LIMITED_SUPERVISOR and capability is not None:
            initial = {"manage_store": capability.manage_store, "follow_tasks": capability.follow_tasks}
        return self.show(SupervisorCapabilitiesForm(initial=initial), supervisor)

    def post(self, request, pk):
        target = self.target(pk)
        form = SupervisorCapabilitiesForm(request.POST)
        valid = form.is_valid()
        if request.POST.getlist("confirm") != ["yes"]:
            form.add_error("confirm", "أكّد حفظ الصلاحيات أولًا")
            valid = False
        if not valid:
            return self.show(form, target, status=400)

        with transaction.atomic():
            actor, supervisor = locked_users(request.user.pk, pk, no_key=True)
            if actor.role != Role.GENERAL_SUPERVISOR or not actor.is_active:
                raise PermissionDenied
            get_object_or_404(capability_supervisors(), pk=supervisor.pk)
            if actor.pk == supervisor.pk:
                raise PermissionDenied
            manage_store = form.cleaned_data["manage_store"]
            follow_tasks = form.cleaned_data["follow_tasks"]
            if not supervisor.is_active and (manage_store or follow_tasks):
                form.add_error(None, "لا يمكن منح صلاحيات لحساب معطل؛ يمكن سحب الصلاحيات فقط")
                return self.show(form, supervisor, status=400)

            role_changed = supervisor.role == Role.GROUP_SUPERVISOR
            if role_changed:
                supervisor.role = Role.LIMITED_SUPERVISOR
                supervisor.save(update_fields=["role"])
                supervisor.group_set.clear()
                revoke_review(supervisor, actor)

            capability = SupervisorCapability.objects.select_for_update().filter(
                supervisor_id=supervisor.pk,
            ).first()
            flags_changed = False
            if capability is None:
                if manage_store or follow_tasks:
                    SupervisorCapability.objects.create(
                        supervisor=supervisor, manage_store=manage_store,
                        follow_tasks=follow_tasks, granted_by=actor,
                    )
                    flags_changed = True
            elif (capability.manage_store, capability.follow_tasks) != (manage_store, follow_tasks):
                capability.manage_store = manage_store
                capability.follow_tasks = follow_tasks
                capability.granted_by = actor
                capability.save(update_fields=["manage_store", "follow_tasks", "granted_by", "updated_at"])
                flags_changed = True

            changed = role_changed or flags_changed
            if changed:
                record_management(actor, supervisor, "صلاحيات المشرف المحددة")

        if changed:
            messages.success(request, "تم حفظ صلاحيات المشرف المحددة دون تغيير بيانات الدخول أو الصلاحيات الإدارية")
        else:
            messages.info(request, "الصلاحيات مطابقة للمحفوظ؛ لم يتغير أي حساب")
        return redirect("participants:supervisor_list")


@method_decorator(never_cache, name="dispatch")
class LimitedSupervisorDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/limited_supervisor_dashboard.html"
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "head", "options"]

    def test_func(self):
        user = self.request.user
        return bool(
            user.is_authenticated and user.is_active and user.role == Role.LIMITED_SUPERVISOR
            and User.objects.filter(pk=user.pk, is_active=True, role=Role.LIMITED_SUPERVISOR).exists()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["navbar_items"] = build_navbar(self.request.user, "home")
        context["can_manage_store"] = can_manage_store(self.request.user)
        context["can_follow_tasks"] = can_follow_tasks(self.request.user)
        return context
