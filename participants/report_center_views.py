"""Independent report viewing/preparation permissions; no domain writes."""
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.generic import TemplateView, View

from .capabilities import current_report_access
from .report_center import (
    ReportCenterFilterForm, build_filter_labels, build_reports,
    can_access_report_center,
)
from .reporting import PrivateReportResponseMixin
from .views import build_navbar
from .report_group_charts import build_group_comparison

logger = logging.getLogger(__name__)


class ReportCenterAccessMixin(PrivateReportResponseMixin, LoginRequiredMixin, UserPassesTestMixin):
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "head", "options"]

    def test_func(self):
        return can_access_report_center(self.request.user)


class ReportCenterView(ReportCenterAccessMixin, TemplateView):
    template_name = "participants/report_center.html"
    page_size = 50

    def get_context_data(self, **kwargs):
        access = current_report_access(self.request.user)
        if not (access["can_view"] and access["can_center"]):
            return {
                "form": None, "reports": [], "group_comparison": None,
                "filter_labels": [], "filters_valid": False,
                "navbar_items": [], "can_prepare_reports": False,
            }
        form = ReportCenterFilterForm(self.request.GET, user=self.request.user)
        valid = form.is_valid()
        reports = build_reports(self.request.user, form.cleaned_data) if valid else []
        comparison_report = next((report for report in reports if report["key"] == "groups"), None)
        # Do not chart a page of table rows: every group shares the same full
        # filtered comparison before any presentation pagination takes place.
        group_comparison = build_group_comparison(comparison_report) if comparison_report else None
        for report in reports:
            for section in report["sections"]:
                page = Paginator(section["rows"], self.page_size).get_page(self.request.GET.get("page"))
                section["page_obj"] = page
                section["rows"] = list(page.object_list)
        params = self.request.GET.copy()
        params.pop("page", None)
        query = params.urlencode()
        return {
            "form": form,
            "reports": reports,
            "group_comparison": group_comparison,
            "filter_labels": build_filter_labels(form.cleaned_data) if valid else [],
            "generated_at": timezone.localtime(timezone.now()),
            "pagination_query": query + "&" if query else "",
            "filters_valid": valid,
            "navbar_items": build_navbar(self.request.user, "report_center"),
            "can_prepare_reports": access["can_prepare"],
        }


class ReportCenterExportView(ReportCenterAccessMixin, View):
    def test_func(self):
        access = current_report_access(self.request.user)
        return bool(access["can_view"] and access["can_prepare"] and access["can_center"])

    def get(self, request, *args, **kwargs):
        # Also protect direct calls and recheck before validation/data/imports.
        access = current_report_access(request.user)
        if not (access["can_view"] and access["can_prepare"] and access["can_center"]):
            raise PermissionDenied("لا تملك صلاحية إعداد وتصدير التقارير.")
        formats = request.GET.getlist("format")
        if len(formats) != 1 or formats[0] not in ("pdf", "xlsx"):
            return HttpResponseBadRequest("صيغة التصدير غير صالحة؛ اختر PDF أو Excel.")
        form = ReportCenterFilterForm(request.GET, user=request.user)
        if not form.is_valid():
            return HttpResponseBadRequest("فلاتر التقرير غير صالحة. صحح الإعدادات قبل التصدير.")
        reports = build_reports(request.user, form.cleaned_data)
        labels = build_filter_labels(form.cleaned_data)
        generated_at = timezone.localtime(timezone.now())
        # Neither importing views nor rendering reports loads PDF dependencies.
        # Import only the chosen exporter, and only inside this GET endpoint.
        try:
            if formats[0] == "xlsx":
                from .report_center_exports import export_reports_xlsx
                content = export_reports_xlsx(reports, labels, generated_at)
                content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                from .report_center_exports import export_reports_pdf
                content = export_reports_pdf(reports, labels, generated_at)
                content_type = "application/pdf"
        except Exception as exc:
            # Do not log report payloads, names, notes, or exception text which
            # a renderer might construct from private data. No partial download.
            logger.error("Report export failed: format=%s error_type=%s", formats[0], type(exc).__name__)
            return HttpResponse(
                "تعذر إنشاء ملف التقرير كاملًا. جرّب الصيغة الأخرى أو فترة أقصر، "
                "وإذا استمرت المشكلة تواصل مع مسؤول المنصة. لم تُغيّر أي بيانات.",
                status=503, content_type="text/plain; charset=utf-8",
            )
        response = HttpResponse(content, content_type=content_type)
        filename = f"rahhal-reports-{generated_at:%Y%m%d-%H%M%S}.{formats[0]}"
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        patch_cache_control(response, private=True, no_store=True)
        return response
