"""General-supervisor-only delegation, revocable without changing task scope."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.utils.cache import patch_cache_control
from django.views.generic import TemplateView

from accounts.models import Role, User
from .models import TaskReviewPermission
from .views import build_navbar


class TaskReviewPermissionsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/task_review_permissions.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role == Role.GENERAL_SUPERVISOR

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supervisors = User.objects.filter(role=Role.GROUP_SUPERVISOR).prefetch_related("group_set").order_by("full_name", "pk")
        permissions = {permission.supervisor_id: permission for permission in TaskReviewPermission.objects.select_related("granted_by")}
        context["supervisors"] = [{
            "user": supervisor,
            "groups": supervisor.group_set.all(),
            "permission": permissions.get(supervisor.pk),
        } for supervisor in supervisors]
        context["navbar_items"] = build_navbar(self.request.user, "task_review_permissions")
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") != "set_permission" or request.POST.get("enabled") not in ("0", "1"):
            return HttpResponseBadRequest("طلب تغيير صلاحية غير صالح.")
        raw_id = request.POST.get("supervisor_id", "")
        if not raw_id.isdecimal():
            raise Http404("مشرف غير متاح")
        with transaction.atomic():
            supervisor = get_object_or_404(User.objects.select_for_update(), pk=raw_id, role=Role.GROUP_SUPERVISOR)
            TaskReviewPermission.objects.update_or_create(
                supervisor=supervisor,
                defaults={"enabled": request.POST["enabled"] == "1", "granted_by": request.user},
            )
        messages.success(request, "تم تحديث صلاحية التصحيح. يظل الاطلاع محصورًا بطلاب بيئات المشرف.")
        return redirect("participants:task_review_permissions")

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        patch_cache_control(response, private=True, no_store=True)
        return response
