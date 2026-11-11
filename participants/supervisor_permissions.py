"""Independent student/report grants without modifying supervisor accounts."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Role, User
from .management_views import AccountManagementView, locked_users, record_management
from .models import Participant, SupervisorCapability, SupervisorReportPermission
from .supervisor_permission_forms import SupervisorStudentReportsForm


STUDENT_REPORT_SUPERVISOR_ROLES = (
    Role.GENERAL_SUPERVISOR, Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR,
)


def student_report_supervisors():
    # is_staff is historical and is preserved, not a grant of platform powers.
    # Avoid nullable joins here: this queryset is also checked under User locks.
    return User.objects.filter(
        role__in=STUDENT_REPORT_SUPERVISOR_ROLES, is_superuser=False,
    ).exclude(pk__in=Participant.objects.values("user_id"))


def can_configure_student_reports(user):
    return bool(
        getattr(user, "is_authenticated", False) and getattr(user, "pk", None) is not None
        and User.objects.filter(pk=user.pk, role=Role.GENERAL_SUPERVISOR, is_active=True).exists()
    )


class SupervisorStudentReportsView(AccountManagementView):
    template_name = "participants/supervisor_student_reports_form.html"
    http_method_names = ["get", "head", "post", "options"]

    def test_func(self):
        return can_configure_student_reports(self.request.user)

    def target(self, pk):
        if pk == self.request.user.pk:
            raise PermissionDenied("لا يمكن استخدام هذا الإجراء على حسابك أنت")
        return get_object_or_404(student_report_supervisors(), pk=pk)

    def form(self, supervisor, data=None, *, locked=False):
        capabilities = SupervisorCapability.objects
        policies = SupervisorReportPermission.objects
        if locked:
            capabilities = capabilities.select_for_update()
            policies = policies.select_for_update()
        capability = capabilities.filter(supervisor_id=supervisor.pk).first()
        policy = policies.filter(supervisor_id=supervisor.pk).first()
        form = SupervisorStudentReportsForm(
            data, supervisor=supervisor, capability=capability, report_permission=policy,
        )
        return form, capability, policy

    def show(self, form, supervisor, *, status=200):
        return render(self.request, self.template_name, self.context(
            "supervisors", form=form, supervisor=supervisor,
            is_inactive=not supervisor.is_active,
            can_grant_permissions=supervisor.is_active,
            is_general=supervisor.role == Role.GENERAL_SUPERVISOR,
        ), status=status)

    def get(self, request, pk):
        supervisor = self.target(pk)
        form, _, _ = self.form(supervisor)
        return self.show(form, supervisor)

    def post(self, request, pk):
        target = self.target(pk)
        # Exact explicit confirmation precedes all locks and writes. Rebuild
        # the form under the locks below, since the target's role may change.
        if request.POST.getlist("confirm") != ["yes"]:
            form, _, _ = self.form(target, request.POST)
            form.is_valid()
            if "confirm" not in form.errors:
                form.add_error("confirm", "أكّد حفظ الصلاحيات أولًا")
            return self.show(form, target, status=400)

        with transaction.atomic():
            actor, supervisor = locked_users(request.user.pk, pk, no_key=True)
            if actor.role != Role.GENERAL_SUPERVISOR or not actor.is_active or actor.pk == supervisor.pk:
                raise PermissionDenied
            get_object_or_404(student_report_supervisors(), pk=supervisor.pk)
            form, capability, policy = self.form(supervisor, request.POST, locked=True)
            if not form.is_valid():
                return self.show(form, supervisor, status=400)

            changed = False
            if supervisor.role in (Role.GROUP_SUPERVISOR, Role.LIMITED_SUPERVISOR):
                manage_students = form.cleaned_data["manage_students"]
                if capability is None:
                    if manage_students:
                        SupervisorCapability.objects.create(
                            supervisor=supervisor, manage_students=True, granted_by=actor,
                        )
                        changed = True
                elif capability.manage_students != manage_students:
                    capability.manage_students = manage_students
                    capability.granted_by = actor
                    capability.save(update_fields=["manage_students", "granted_by", "updated_at"])
                    changed = True

            view_reports = form.cleaned_data["view_reports"]
            prepare_reports = form.cleaned_data["prepare_reports"]
            if policy is None:
                # An explicit False is a real denial, not the model's implicit
                # default or a request to restore legacy inheritance.
                SupervisorReportPermission.objects.create(
                    supervisor=supervisor, view_reports=view_reports,
                    prepare_reports=prepare_reports, granted_by=actor,
                )
                changed = True
            elif (policy.view_reports, policy.prepare_reports) != (view_reports, prepare_reports):
                policy.view_reports = view_reports
                policy.prepare_reports = prepare_reports
                policy.granted_by = actor
                policy.save(update_fields=["view_reports", "prepare_reports", "granted_by", "updated_at"])
                changed = True

            if changed:
                record_management(actor, supervisor, "صلاحيات الطلاب والتقارير")

        if changed:
            messages.success(request, "تم حفظ صلاحيات الطلاب والتقارير دون تغيير الدور أو بيانات الحساب")
        else:
            messages.info(request, "الصلاحيات مطابقة للمحفوظ؛ لم يتغير أي حساب")
        return redirect("participants:supervisor_list")
