"""General-only report presentation and exports; no domain writes."""
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.generic import TemplateView, View

from .report_center import (
    ReportCenterFilterForm, build_filter_labels, build_reports,
    can_access_report_center,
)
from .views import build_navbar

logger = logging.getLogger(__name__)


class ReportCenterAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "head", "options"]

    def test_func(self):
        return can_access_report_center(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # Includes redirects, filter errors, OPTIONS and method-not-allowed.
        patch_cache_control(response, private=True, no_store=True)
        return response


class ReportCenterView(ReportCenterAccessMixin, TemplateView):
    template_name = "participants/report_center.html"
    page_size = 50

    def get_context_data(self, **kwargs):
        form = ReportCenterFilterForm(self.request.GET, user=self.request.user)
        valid = form.is_valid()
        reports = build_reports(self.request.user, form.cleaned_data) if valid else []
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
            "filter_labels": build_filter_labels(form.cleaned_data) if valid else [],
            "generated_at": timezone.localtime(timezone.now()),
            "pagination_query": query + "&" if query else "",
            "filters_valid": valid,
            "navbar_items": build_navbar(self.request.user, "report_center"),
        }


class ReportCenterExportView(ReportCenterAccessMixin, View):
    def get(self, request, *args, **kwargs):
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
