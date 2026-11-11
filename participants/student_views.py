"""Read-only, scoped detail page for a single participant."""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import formats, timezone
from django.views.generic import TemplateView

from .capabilities import can_view_reports, current_report_access
from .models import (
    CircleAttendance,
    MeetingAttendance,
    MeetingNote,
    PointsLedgerEntry,
    PointsResetSnapshot,
    StoreOrder,
    TaskSubmission,
    WeeklyActivityAttendance,
)
from .reporting import (
    PrivateReportResponseMixin, StudentReportFilterForm,
    can_view_private_report_notes, date_window, scoped_participants,
)
from .views import build_navbar


class StudentDetailFilterForm(StudentReportFilterForm):
    """The report's validated date window without its group/search controls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("group")
        self.fields.pop("q")


class StudentDetailView(PrivateReportResponseMixin, LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """A supervisor-only, private audit view scoped before object lookup."""

    template_name = "participants/student_detail.html"
    login_url = "accounts:login_supervisor"
    http_method_names = ["get", "head", "options"]
    page_size = 20

    def test_func(self):
        return can_view_reports(self.request.user)

    def get_participant(self):
        # Never retain an object authorized under an earlier role/assignment.
        return get_object_or_404(
            scoped_participants(self.request.user).select_related("user", "group"),
            pk=self.kwargs["pk"],
        )

    @staticmethod
    def _display(value, empty="—"):
        return empty if value in (None, "") else value

    @staticmethod
    def _date(value):
        return "—" if value is None else formats.date_format(value, "Y/m/d")

    @staticmethod
    def _datetime(value):
        if value is None:
            return "—"
        return formats.date_format(timezone.localtime(value), "Y/m/d H:i")

    @staticmethod
    def _yes_no(value):
        return "نعم" if value else "لا"

    def _page(self, queryset, parameter):
        return Paginator(queryset, self.page_size).get_page(self.request.GET.get(parameter))

    def _page_query(self, parameter):
        params = self.request.GET.copy()
        params.pop(parameter, None)
        query = params.urlencode()
        return query + "&" if query else ""

    def _table(self, *, anchor, title, columns, page, parameter, rows):
        return {
            "anchor": anchor,
            "title": title,
            "columns": columns,
            "page": page,
            "parameter": parameter,
            "query": self._page_query(parameter),
            "rows": rows,
        }

    def _build_tables(self, participant, start_date, end_date, include_notes):
        """Paginate every independently-filtered queryset, with no history cap."""
        access = current_report_access(self.request.user)
        if not access["can_view"] or not scoped_participants(
            self.request.user, _access=access,
        ).filter(pk=participant.pk).exists():
            raise PermissionDenied("الطالب غير متاح ضمن صلاحياتك.")
        include_notes = include_notes and can_view_private_report_notes(self.request.user, _access=access)
        circle = date_window(
            CircleAttendance.objects.filter(participant=participant).select_related("recorded_by"),
            "date", start_date, end_date,
        ).order_by("-date", "-pk")
        meeting = date_window(
            MeetingAttendance.objects.filter(participant=participant).select_related("recorded_by"),
            "week_start_date", start_date, end_date,
        ).order_by("-week_start_date", "-pk")
        activity = date_window(
            WeeklyActivityAttendance.objects.filter(participant=participant).select_related("recorded_by"),
            "date", start_date, end_date,
        ).order_by("-date", "-pk")
        tasks = date_window(
            TaskSubmission.objects.filter(participant=participant).select_related("task", "reviewed_by"),
            "submitted_at", start_date, end_date, timestamp=True,
        ).order_by("-submitted_at", "-pk")
        ledger = date_window(
            PointsLedgerEntry.objects.filter(participant=participant).select_related("granted_by"),
            "created_at", start_date, end_date, timestamp=True,
        ).order_by("-created_at", "-pk")
        orders = date_window(
            StoreOrder.objects.filter(participant=participant).select_related("product"),
            "ordered_at", start_date, end_date, timestamp=True,
        ).order_by("-ordered_at", "-pk")
        snapshots = date_window(
            PointsResetSnapshot.objects.filter(participant=participant).select_related("reset_by"),
            "reset_at", start_date, end_date, timestamp=True,
        ).order_by("-reset_at", "-pk")

        querysets = {
            "circle": circle,
            "meeting": meeting,
            "activity": activity,
            "task": tasks,
            "ledger": ledger,
            "order": orders,
            "snapshot": snapshots,
        }
        pages = {
            key: self._page(queryset, f"{key}_page")
            for key, queryset in querysets.items()
        }
        tables = [
            self._table(
                anchor="circle", title="حضور الحلقة القرآنية",
                columns=["التاريخ", "حضر", "أنجز", "سجّله"], page=pages["circle"],
                parameter="circle_page",
                rows=[[
                    self._date(item.date), self._yes_no(item.attended),
                    self._yes_no(item.achieved), self._display(getattr(item.recorded_by, "full_name", None)),
                ] for item in pages["circle"]],
            ),
            self._table(
                anchor="meeting", title="حضور اللقاءات",
                columns=["تاريخ اللقاء", "حضر", "حضور مبكر", "سجّله"], page=pages["meeting"],
                parameter="meeting_page",
                rows=[[
                    self._date(item.week_start_date), self._yes_no(item.attended),
                    self._yes_no(item.is_early), self._display(getattr(item.recorded_by, "full_name", None)),
                ] for item in pages["meeting"]],
            ),
            self._table(
                anchor="activity", title="فعاليات الأسبوع",
                columns=["التاريخ", "حضر", "سجّله"], page=pages["activity"],
                parameter="activity_page",
                rows=[[
                    self._date(item.date), self._yes_no(item.attended),
                    self._display(getattr(item.recorded_by, "full_name", None)),
                ] for item in pages["activity"]],
            ),
            self._table(
                anchor="tasks", title="تسليمات المهام",
                columns=[
                    "العنوان", "الوصف", "موعد المهمة", "تاريخ التسليم", "النص", "الملف",
                    "الحالة", "ملاحظة المراجع", "المراجع", "وقت المراجعة", "مميزة", "أُعيد فتحها",
                ], page=pages["task"], parameter="task_page",
                rows=[[
                    self._display(item.task.title), self._display(item.task.description),
                    self._date(item.task.due_date), self._datetime(item.submitted_at),
                    self._display(item.text_content),
                    {"value": item.file.name, "url": item.file.url} if item.file else "—",
                    item.get_status_display(), self._display(
                        item.revision_note
                        if item.status == TaskSubmission.Status.REVISION_REQUIRED
                        else item.rejection_reason
                    ),
                    self._display(getattr(item.reviewed_by, "full_name", None)),
                    self._datetime(item.reviewed_at), self._yes_no(item.is_featured),
                    self._yes_no(item.reopened_for_resubmission),
                ] for item in pages["task"]],
            ),
            self._table(
                anchor="ledger", title="سجل النقاط",
                columns=["الوصف", "المصدر", "التغيير", "تاريخ تسجيل النقاط", "تاريخ الحدث", "مُنح بواسطة"],
                page=pages["ledger"], parameter="ledger_page",
                rows=[[
                    self._display(item.description), item.get_source_display(), item.points_delta,
                    self._datetime(item.created_at),
                    self._date(item.event_date) if item.event_date else "غير مسجل",
                    self._display(getattr(item.granted_by, "full_name", None)),
                ] for item in pages["ledger"]],
            ),
            self._table(
                anchor="orders", title="طلبات المتجر",
                columns=["المنتج", "تاريخ الطلب", "السعر وقت الطلب", "الحالة", "اكتمل في", "استُرجع في"],
                page=pages["order"], parameter="order_page",
                rows=[[
                    item.product.name, self._datetime(item.ordered_at), item.price_at_order,
                    item.get_status_display(), self._datetime(item.completed_at), self._datetime(item.refunded_at),
                ] for item in pages["order"]],
            ),
            self._table(
                anchor="snapshots", title="سجل لقطات تصفير النقاط",
                columns=["النقاط قبل التصفير", "تاريخ التصفير", "نفّذه"],
                page=pages["snapshot"], parameter="snapshot_page",
                rows=[[
                    item.points_before_reset, self._datetime(item.reset_at),
                    self._display(getattr(item.reset_by, "full_name", None)),
                ] for item in pages["snapshot"]],
            ),
        ]

        if include_notes:
            # This queryset is deliberately created only for group/general supervisors.
            notes = date_window(
                MeetingNote.objects.filter(participant=participant).select_related("created_by", "updated_by"),
                "meeting_date", start_date, end_date,
            ).order_by("-meeting_date", "-updated_at", "-pk")
            note_page = self._page(notes, "note_page")
            pages["note"] = note_page
            tables.append(self._table(
                anchor="notes", title="ملاحظات اللقاء الخاصة",
                columns=["تاريخ اللقاء", "الملاحظة", "أُضيفت في", "أضافها", "آخر تحديث", "حدّثها"],
                page=note_page, parameter="note_page",
                rows=[[
                    self._date(item.meeting_date), self._display(item.text), self._datetime(item.created_at),
                    self._display(getattr(item.created_by, "full_name", None)), self._datetime(item.updated_at),
                    self._display(getattr(item.updated_by, "full_name", None)),
                ] for item in note_page],
            ))
        return querysets, pages, tables

    @staticmethod
    def _summary(circle, tasks, ledger, current_balance):
        counts = circle.aggregate(
            attended=Count("pk", filter=Q(attended=True)),
            achieved=Count("pk", filter=Q(achieved=True)),
        )
        task_counts = tasks.aggregate(
            submitted=Count("pk"),
            accepted=Count("pk", filter=Q(status=TaskSubmission.Status.ACCEPTED)),
            pending=Count("pk", filter=Q(status=TaskSubmission.Status.PENDING)),
            returned=Count("pk", filter=Q(status=TaskSubmission.Status.REVISION_REQUIRED)),
            rejected=Count("pk", filter=Q(status=TaskSubmission.Status.REJECTED)),
        )
        return {
            "attended": counts["attended"],
            "achieved": counts["achieved"],
            **task_counts,
            "ledger_total": ledger.aggregate(total=Sum("points_delta"))["total"] or 0,
            "current_balance": current_balance,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        access = current_report_access(self.request.user)
        if not access["can_view"]:
            context.update({
                "participant": None, "filter_form": None, "filters_valid": False,
                "tables": [], "summary": None, "navbar_items": [],
                "can_manage_accounts": False, "can_manage_students": False,
                "can_prepare_reports": False,
            })
            return context
        participant = self.get_participant()
        from .capabilities import can_manage_students
        # Report grants never imply permission to edit participant accounts.
        context["can_manage_students"] = can_manage_students(self.request.user)
        context["can_manage_accounts"] = (
            context["can_manage_students"]
            and access["role"] in ("general_supervisor", "superadmin")
        )
        context["can_prepare_reports"] = access["can_prepare"]
        form = StudentDetailFilterForm(self.request.GET, user=self.request.user)
        filters_valid = form.is_valid()
        include_notes = can_view_private_report_notes(self.request.user, _access=access)
        context.update({
            "participant": participant,
            "filter_form": form,
            "filters_valid": filters_valid,
            "start_date": form.cleaned_data.get("start_date") if filters_valid else None,
            "end_date": form.cleaned_data.get("end_date") if filters_valid else None,
            "navbar_items": build_navbar(self.request.user, "student_report"),
        })
        if not filters_valid:
            # Do not load any historical record when the requested window is invalid.
            context["tables"] = []
            context["summary"] = None
            return context

        querysets, pages, tables = self._build_tables(
            participant,
            form.cleaned_data.get("start_date"),
            form.cleaned_data.get("end_date"),
            include_notes,
        )
        context.update({
            "tables": tables,
            "summary": self._summary(
                querysets["circle"], querysets["task"], querysets["ledger"], participant.points,
            ),
        })
        context.update({f"{key}_page": page for key, page in pages.items()})
        return context
