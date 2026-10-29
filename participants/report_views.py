"""Supervisor-only, read-only detailed student report."""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.utils.cache import patch_cache_control
from django.views.generic import TemplateView

from .reporting import REPORT_ROLES, StudentReportFilterForm, build_student_report
from .views import build_navbar


class StudentReportView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/student_report.html"
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "head", "options"]

    def test_func(self):
        return self.request.user.role in REPORT_ROLES

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = StudentReportFilterForm(self.request.GET, user=self.request.user)
        valid = form.is_valid()
        rows = build_student_report(self.request.user, form.cleaned_data) if valid else []
        totals = {
            "students": len(rows),
            "attended": sum(row["circle_attended"] for row in rows),
            "achieved": sum(row["circle_achieved"] for row in rows),
            "submitted": sum(row["tasks_submitted"] for row in rows),
            "net_points": sum(row["net_points"] for row in rows),
        }
        params = self.request.GET.copy()
        params.pop("page", None)
        query = params.urlencode()
        context.update({
            "filter_form": form,
            "filters_valid": valid,
            "page_obj": Paginator(rows, 50).get_page(self.request.GET.get("page")),
            "top_students": [row for row in rows if row["net_points"] > 0][:15],
            "totals": totals,
            "pagination_query": query + "&" if query else "",
            "start_date": form.cleaned_data.get("start_date") if valid else None,
            "end_date": form.cleaned_data.get("end_date") if valid else None,
            "navbar_items": build_navbar(self.request.user, "student_report"),
            "is_scoped_to_group": self.request.user.role == "group_supervisor",
        })
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        patch_cache_control(response, private=True, no_store=True)
        return response
