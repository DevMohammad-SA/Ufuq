"""Permission-checked, read-only detailed student report."""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.views.generic import TemplateView

from accounts.models import Role
from .capabilities import can_view_reports, current_report_access
from .reporting import PrivateReportResponseMixin, StudentReportFilterForm, build_student_report
from .views import build_navbar


class StudentReportView(PrivateReportResponseMixin, LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/student_report.html"
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "head", "options"]

    def test_func(self):
        return can_view_reports(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        access = current_report_access(self.request.user)
        if not access["can_view"]:
            context.update({
                "filter_form": None, "filters_valid": False,
                "page_obj": Paginator([], 50).get_page(1), "top_students": [],
                "totals": {key: 0 for key in ("students", "attended", "achieved", "submitted", "net_points")},
                "navbar_items": [], "can_prepare_reports": False,
            })
            return context
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
            "is_scoped_to_group": access["role"] == Role.GROUP_SUPERVISOR,
            "can_prepare_reports": access["can_prepare"],
        })
        return context
