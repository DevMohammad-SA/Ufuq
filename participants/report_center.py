"""Read-only report centre with fresh permissions and persisted-role scope.

All inputs passed to build_reports must be the valid filter form's cleaned_data.
Each activity is aggregated independently: no multi-relation joins, guessed
attendance calendar, historical enrolment, point awards or data backfill.
Reports contain plain scalars, never marked-safe HTML or spreadsheet formulas.
"""
import datetime
import re
from collections import defaultdict
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import PermissionDenied
from django.core.validators import MinValueValidator
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from .capabilities import can_view_report_center, current_report_access
from .models import (
    AcademicStage, CircleAttendance, EliteTripAttendance, Group, MeetingAttendance, MeetingNote,
    Participant, PointsLedgerEntry, TaskSubmission, WeeklyActivityAttendance,
    WeeklyTask,
)
from .reporting import (
    allowed_groups, can_view_private_report_notes, date_window, scoped_participants,
)

RIYADH = ZoneInfo("Asia/Riyadh")
REPORT_CHOICES = (
    ("summary", "الملخص العام"),
    ("students", "تقرير الطلاب"),
    ("attendance", "الحضور والإنجاز"),
    ("tasks", "المهام الأسبوعية"),
    ("groups", "مقارنة البيئات"),
)
PERIOD_CHOICES = (
    ("week", "هذا الأسبوع"), ("month", "هذا الشهر"),
    ("custom", "من تاريخ إلى تاريخ"), ("all", "كامل المدة"),
)
ACCOUNT_STATUS_CHOICES = (
    ("active", "النشطون"), ("inactive", "المعطلون"), ("all", "الجميع"),
)
DETAIL_CHOICES = (("summary", "ملخص"), ("detailed", "تفصيلي"))
POINTS_BASIS_CHOICES = (("recorded", "تاريخ تسجيل النقاط"), ("event", "تاريخ الحدث"))
DEFAULTS = {
    "period": "month", "account_status": "active", "detail": "summary",
    "points_basis": "recorded",
}
CHOICE_ERRORS = {
    "required": "اختر قيمة لهذا الحقل.", "invalid_choice": "القيمة المختارة غير صالحة.",
}
STATUS_KEYS = ("submitted", "accepted", "featured", "pending", "returned", "rejected")
STATUS_LABELS = (
    "التسليمات المحفوظة", "المقبولة (تشمل المميزة)", "المميزة من المقبولة",
    "قيد المراجعة", "المعادة للتعديل", "المرفوضة",
)
ATTENDANCE_SOURCES = (
    ("circle", "الحلقة القرآنية", CircleAttendance, "date", "achieved", "إنجاز الحلقة"),
    ("meeting", "اللقاء", MeetingAttendance, "week_start_date", "is_early", "الحضور المبكر"),
    ("activity", "فعالية الأسبوع", WeeklyActivityAttendance, "date", None, "إنجاز الفعالية"),
)
MEMBERSHIP_NOTE = (
    "النطاق والبيئة والمرحلة وحالة الحساب بحسب البيانات الحالية؛ لا يوجد سجل انتقال "
    "أو عضوية تاريخية، ويشمل «بدون بيئة» الطلاب غير المرتبطين ببيئة حاليًا."
)
ATTENDANCE_NOTE = (
    "نسبة الحضور = الحاضر ÷ (الحاضر + الغياب الصريح المسجل) فقط؛ الصف المفقود "
    "ليس غيابًا ولا يدخل المقام. لا يوجد جدول تقويم شامل للحضور المتوقع."
)
MISSING_NOTE = (
    "«غير مسجل» يعني صفًا مفقودًا لطالب من النطاق على تاريخ تحضير ملاحظ لدى "
    "طلاب البيئة الحالية، حتى لو كان صاحب التحضير خارج فلتر الطالب أو المرحلة "
    "أو حالة الحساب. ليس حضورًا متوقعًا ولا تكليفًا تاريخيًا؛ ولا نستنتج ذلك "
    "لطلاب «بدون بيئة» لعدم وجود بيئة تربطهم."
)
SUBMISSION_NOTE = (
    "عدادات الطلاب والتسليمات في الملخص والمقارنة تُرشح بتاريخ submitted_at الأول "
    "المحفوظ ضمن أيام الفترة بتوقيت الرياض، وتعرض الحالة الحالية؛ لا تاريخ لكل "
    "محاولات إعادة الرفع المستبدلة. المميزة جزء من المقبولة وليست حالة إضافية."
)
TASK_NOTE = (
    "تقرير المهام يختار المهام بموعد due_date داخل الفترة، ويحصي جميع تسليماتها "
    "المحفوظة لطلاب النطاق الحالي، حتى لو كان تاريخ التسليم خارج الفترة. "
    "من لا يملك تسليمًا من هذا النطاق ليس إثباتًا لتكليف أو أهلية تاريخية."
)
BALANCE_NOTE = (
    "المكتسب مجموع الحركات الموجبة، والخصم القيمة المطلقة للحركات السالبة، "
    "والصافي مجموع الدلتا الموقعة في الدفتر؛ الرصيد الحالي منفصل وليس رصيد الفترة "
    "وقد يختلف بسبب التصفير وحد الصفر وحركات أقدم. لا تشمل النقاط حركة المتجر."
)


def can_access_report_center(user):
    """Compatibility entry point; permission policy is owned by capabilities."""
    return can_view_report_center(user)


class ISODateField(forms.DateField):
    """Require a canonical ISO calendar day, not permissive strptime input."""

    def to_python(self, value):
        if value not in self.empty_values and not isinstance(value, datetime.date):
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
        return super().to_python(value)


class NameOnlyParticipantField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.user.full_name


class ReportSelectionField(forms.MultipleChoiceField):
    def clean(self, value):
        # Validate every value BEFORE deduplicating; preserve submitted order.
        return list(dict.fromkeys(super().clean(value)))


class ExplicitCheckboxInput(forms.CheckboxInput):
    def __init__(self, attrs=None):
        super().__init__(attrs, check_test=lambda value: value in (True, "1", "true", "True", "on", "yes"))

    def value_from_datadict(self, data, files, name):
        # CheckboxInput otherwise coerces any unknown nonempty string to True
        # before the field can reject it.
        return data.get(name, False)


class ExplicitBooleanField(forms.BooleanField):
    widget = ExplicitCheckboxInput

    def to_python(self, value):
        if value in (True, "1", "true", "True", "on", "yes"):
            return True
        if value in (False, None, "", "0", "false", "False", "off", "no"):
            return False
        raise forms.ValidationError("قيمة خيار البيانات الخاصة غير صالحة.", code="invalid")


class ReportCenterFilterForm(forms.Form):
    period = forms.ChoiceField(label="الفترة", choices=PERIOD_CHOICES, error_messages=CHOICE_ERRORS)
    start_date = ISODateField(
        required=False, label="من تاريخ", input_formats=["%Y-%m-%d"],
        validators=[MinValueValidator(datetime.date(1000, 1, 1))],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "min": "1000-01-01"}),
        error_messages={"invalid": "أدخل تاريخ بداية صالحًا بصيغة YYYY-MM-DD.",
                        "min_value": "يجب ألا يكون التاريخ قبل 1000/01/01."},
    )
    end_date = ISODateField(
        required=False, label="إلى تاريخ", input_formats=["%Y-%m-%d"],
        validators=[MinValueValidator(datetime.date(1000, 1, 1))],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "min": "1000-01-01"}),
        error_messages={"invalid": "أدخل تاريخ نهاية صالحًا بصيغة YYYY-MM-DD.",
                        "min_value": "يجب ألا يكون التاريخ قبل 1000/01/01."},
    )
    group = forms.ModelChoiceField(
        queryset=Group.objects.none(), required=False, label="البيئة", empty_label="جميع البيئات",
        error_messages={"invalid_choice": "البيئة المختارة غير متاحة ضمن صلاحياتك."},
    )
    academic_stage = forms.ChoiceField(
        required=False, label="المرحلة الدراسية", choices=[("", "جميع المراحل"), *AcademicStage.choices],
        error_messages=CHOICE_ERRORS,
    )
    participant = NameOnlyParticipantField(
        queryset=Participant.objects.none(), required=False, label="الطالب", empty_label="جميع الطلاب",
        error_messages={"invalid_choice": "الطالب المختار غير متاح ضمن صلاحياتك."},
    )
    account_status = forms.ChoiceField(
        label="حالة الحساب", choices=ACCOUNT_STATUS_CHOICES, error_messages=CHOICE_ERRORS,
    )
    detail = forms.ChoiceField(label="مستوى التفاصيل", choices=DETAIL_CHOICES, error_messages=CHOICE_ERRORS)
    points_basis = forms.ChoiceField(
        label="أساس تاريخ النقاط", choices=POINTS_BASIS_CHOICES, error_messages=CHOICE_ERRORS,
    )
    task = forms.ModelChoiceField(
        queryset=WeeklyTask.objects.none(), required=False, label="تفصيل مهمة", empty_label="جميع المهام",
        error_messages={"invalid_choice": "المهمة المختارة غير موجودة أو غير متاحة."},
    )
    include_private = ExplicitBooleanField(required=False, label="تضمين الهوية والجوال والملاحظات الخاصة")
    reports = ReportSelectionField(
        label="التقارير", choices=REPORT_CHOICES, widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "اختر تقريرًا واحدًا على الأقل.",
                        "invalid_choice": "أحد التقارير المختارة غير صالح.",
                        "invalid_list": "اختر التقارير من القائمة المتاحة."},
    )

    def __init__(self, data=None, *, user, **kwargs):
        # Always bound, including initial GET. A page/format-only GET is also an
        # initial filter request; explicitly empty reports in a real filter GET
        # is an error rather than silently producing a different report.
        supplied = data.copy() if data is not None else {}
        empty_filters = not any(name in supplied for name in self.base_fields)
        for name, value in DEFAULTS.items():
            if name not in supplied:
                supplied[name] = value
        if empty_filters:
            if hasattr(supplied, "setlist"):
                supplied.setlist("reports", ["summary"])
            else:
                supplied["reports"] = ["summary"]
        super().__init__(data=supplied, **kwargs)
        self.initial.update({**DEFAULTS, "reports": ["summary"], "include_private": False})
        access = current_report_access(user)
        if access["can_view"]:
            # All inactive participant accounts remain selectable; status
            # compatibility is a clean-time error, not a hidden option filter.
            self.fields["group"].queryset = allowed_groups(user, _access=access)
            self.fields["participant"].queryset = scoped_participants(user, _access=access).select_related("user").order_by("user__full_name", "pk")
            self.fields["task"].queryset = WeeklyTask.objects.order_by("-due_date", "pk")

    def clean(self):
        data = super().clean()
        period = data.get("period")
        today = timezone.localdate(timezone.now(), RIYADH)
        if period == "custom":
            for name in ("start_date", "end_date"):
                if not data.get(name) and name not in self.errors:
                    self.add_error(name, "هذا التاريخ مطلوب للفترة المخصصة.")
            if data.get("start_date") and data.get("end_date") and data["start_date"] > data["end_date"]:
                self.add_error("end_date", "يجب أن يكون تاريخ النهاية بعد البداية أو مساويًا لها.")
        elif period in ("week", "month", "all"):
            data["end_date"] = today if period != "all" else None
            data["start_date"] = (
                today - datetime.timedelta(days=(today.weekday() + 1) % 7)
                if period == "week" else today.replace(day=1) if period == "month" else None
            )
        participant = data.get("participant")
        if participant:
            if data.get("group") and participant.group_id != data["group"].pk:
                self.add_error("participant", "الطالب لا ينتمي إلى البيئة المختارة حاليًا.")
            if data.get("academic_stage") and participant.academic_stage != data["academic_stage"]:
                self.add_error("participant", "مرحلة الطالب لا تطابق المرحلة المختارة.")
            status = data.get("account_status")
            if status in ("active", "inactive") and participant.user.is_active != (status == "active"):
                self.add_error("participant", "حالة حساب الطالب لا تطابق الحالة المختارة.")
        task = data.get("task")
        if task and period and "start_date" not in self.errors and "end_date" not in self.errors:
            start, end = data.get("start_date"), data.get("end_date")
            if (start and task.due_date < start) or (end and task.due_date > end):
                self.add_error("task", "موعد المهمة المختارة يجب أن يقع داخل الفترة المحددة.")
        return data


def build_filter_labels(filters):
    """Only readable plain strings, from validated filters, for UI and exports."""
    labels = [
        ("الفترة", dict(PERIOD_CHOICES).get(filters.get("period"), "")),
        ("من تاريخ", filters["start_date"].isoformat() if filters.get("start_date") else "بلا حد"),
        ("إلى تاريخ", filters["end_date"].isoformat() if filters.get("end_date") else "بلا حد"),
        ("البيئة", filters["group"].name if filters.get("group") else "جميع البيئات (تشمل بدون بيئة)"),
        ("المرحلة الدراسية", dict(AcademicStage.choices).get(filters.get("academic_stage"), "جميع المراحل")),
        ("الطالب", filters["participant"].user.full_name if filters.get("participant") else "جميع الطلاب"),
        ("حالة الحساب", dict(ACCOUNT_STATUS_CHOICES).get(filters.get("account_status", "active"), "")),
        ("مستوى التفاصيل", dict(DETAIL_CHOICES).get(filters.get("detail", "summary"), "")),
        ("أساس تاريخ النقاط", dict(POINTS_BASIS_CHOICES).get(filters.get("points_basis", "recorded"), "")),
        ("المهمة", filters["task"].title if filters.get("task") else "جميع المهام"),
        ("البيانات الخاصة", "مضمّنة بطلب صريح" if filters.get("include_private") else "مستبعدة"),
        ("التقارير", "، ".join(dict(REPORT_CHOICES)[key] for key in filters.get("reports", ["summary"]))),
    ]
    return [{"label": str(label), "value": str(value)} for label, value in labels]


def _window(queryset, field, filters, *, timestamp=False):
    # date_window uses the current timezone; make Riyadh explicit even if a
    # caller activated another timezone for its session or test.
    with timezone.override(RIYADH):
        return date_window(queryset, field, filters.get("start_date"), filters.get("end_date"), timestamp=timestamp)


def _cohort(user, filters, *, _access=None):
    students = scoped_participants(user, _access=_access)
    if filters.get("group"):
        students = students.filter(group=filters["group"])
    if filters.get("academic_stage"):
        students = students.filter(academic_stage=filters["academic_stage"])
    if filters.get("participant"):
        students = students.filter(pk=filters["participant"].pk)
    status = filters.get("account_status", "active")
    if status in ("active", "inactive"):
        students = students.filter(user__is_active=(status == "active"))
    return students


def _status_aggregates():
    return {
        "submitted": Count("pk"),
        "accepted": Count("pk", filter=Q(status=TaskSubmission.Status.ACCEPTED)),
        "featured": Count("pk", filter=Q(status=TaskSubmission.Status.ACCEPTED, is_featured=True)),
        "pending": Count("pk", filter=Q(status=TaskSubmission.Status.PENDING)),
        "returned": Count("pk", filter=Q(status=TaskSubmission.Status.REVISION_REQUIRED)),
        "rejected": Count("pk", filter=Q(status=TaskSubmission.Status.REJECTED)),
    }


def _percentage(numerator, denominator):
    return round(numerator * 100 / denominator, 2) if denominator else "—"


def _row(cells, student=None):
    result = {"cells": cells}
    if student is not None:
        result["detail_url"] = reverse("participants:student_detail", kwargs={"pk": student.pk})
    return result


def _student_row(student, cells):
    return _row([student.user.full_name, *cells], student)


def _section(title, columns, rows):
    return {"title": title, "columns": columns, "rows": rows}


def _metrics(items):
    return [{"label": label, "value": value} for label, value in items]


def _group_name(student):
    return student.group.name if student.group_id else "بدون بيئة"


def _stamp(value):
    return timezone.localtime(value, RIYADH).isoformat(timespec="seconds") if value else "غير مسجل"


def _status_label(item):
    if item.status == TaskSubmission.Status.ACCEPTED and item.is_featured:
        return "مقبولة مميزة"
    return item.get_status_display()


def _load_data(user, filters, *, _access=None):
    cohort = _cohort(user, filters, _access=_access)
    students = list(cohort.select_related("user", "group").order_by("user__full_name", "pk"))
    ids = cohort.order_by().values("pk")
    data = {
        "students": students, "by_id": {p.pk: p for p in students},
        "attendance": {}, "attendance_queries": {}, "status": {},
        "points": defaultdict(lambda: {"earned": 0, "deducted": 0, "net": 0}),
        "source_points": defaultdict(lambda: {"earned": 0, "deducted": 0, "net": 0}),
        "undated": {},
    }
    for key, label, model, field, extra, extra_label in ATTENDANCE_SOURCES:
        queryset = _window(model.objects.filter(participant_id__in=ids), field, filters)
        data["attendance_queries"][key] = queryset
        aggregates = {
            "recorded": Count("pk"), "present": Count("pk", filter=Q(attended=True)),
            "absent": Count("pk", filter=Q(attended=False)),
        }
        if extra:
            aggregates["extra"] = Count("pk", filter=Q(**{extra: True}))
        else:
            aggregates["extra"] = Count("pk", filter=Q(attended=True))
        data["attendance"][key] = {
            row.pop("participant_id"): row
            for row in queryset.order_by().values("participant_id").annotate(**aggregates)
        }
    submissions = TaskSubmission.objects.filter(participant_id__in=ids)
    if filters.get("task"):
        submissions = submissions.filter(task=filters["task"])
    submissions = _window(submissions, "submitted_at", filters, timestamp=True)
    data["submission_query"] = submissions
    data["status"] = {
        row.pop("participant_id"): row
        for row in submissions.order_by().values("participant_id").annotate(**_status_aggregates())
    }
    ledger = PointsLedgerEntry.objects.filter(participant_id__in=ids)
    data["undated"] = {
        row["participant_id"]: row["count"] for row in ledger.filter(event_date__isnull=True)
        .order_by().values("participant_id").annotate(count=Count("pk"))
    }
    basis = filters.get("points_basis", "recorded")
    if basis == "event":
        ledger = ledger.filter(event_date__isnull=False)
    ledger = _window(ledger, "event_date" if basis == "event" else "created_at", filters, timestamp=(basis != "event"))
    data["ledger_query"] = ledger
    for row in ledger.order_by().values("participant_id", "source").annotate(
        earned=Sum("points_delta", filter=Q(points_delta__gt=0)),
        negative=Sum("points_delta", filter=Q(points_delta__lt=0)), net=Sum("points_delta"),
    ):
        amounts = {"earned": row["earned"] or 0, "deducted": -(row["negative"] or 0), "net": row["net"] or 0}
        for field, value in amounts.items():
            data["points"][row["participant_id"]][field] += value
            data["source_points"][row["source"]][field] += value
    return data


def _attendance_counts(data, key, student_ids=None):
    rows = data["attendance"][key]
    selected = rows.values() if student_ids is None else (rows.get(pk, {}) for pk in student_ids)
    total = {field: 0 for field in ("recorded", "present", "absent", "extra")}
    for row in selected:
        for field in total:
            total[field] += row.get(field, 0)
    return total


def _status_counts(data, student_ids=None):
    rows = data["status"]
    selected = rows.values() if student_ids is None else (rows.get(pk, {}) for pk in student_ids)
    total = {field: 0 for field in STATUS_KEYS}
    for row in selected:
        for field in total:
            total[field] += row.get(field, 0)
    return total


def _point_counts(data, student_ids=None):
    rows = data["points"]
    selected = rows.values() if student_ids is None else (rows.get(pk, {}) for pk in student_ids)
    total = {field: 0 for field in ("earned", "deducted", "net")}
    for row in selected:
        for field in total:
            total[field] += row.get(field, 0)
    return total


def _common_notes(data, filters):
    notes = [MEMBERSHIP_NOTE, ATTENDANCE_NOTE, SUBMISSION_NOTE, BALANCE_NOTE]
    undated = sum(data["undated"].values())
    if filters.get("points_basis", "recorded") == "event":
        notes.append(
            f"استُبعد {undated} سجل نقاط بلا تاريخ حدث من كامل دفتر طلاب النطاق الحالي؛ "
            "لا يمكن نسبتها للفترة، ولا يُخمّن تاريخها من وقت التسجيل أو الوصف."
        )
    else:
        notes.append(
            f"يوجد {undated} سجل نقاط بلا تاريخ حدث في كامل دفتر طلاب النطاق الحالي؛ "
            "أساس التسجيل يستخدم created_at ولا يشترط تاريخ الحدث ولا يملأه."
        )
    if filters.get("task"):
        notes.append("فلتر المهمة يقيّد عدّ المهام ذات الموعد والتسليمات؛ لا يغيّر نطاق الحضور أو النقاط أو النخبة.")
    if not data["students"]:
        notes.append("لا يوجد طلاب ضمن الفلاتر المختارة؛ تبقى المهام ذات الموعد المطابق ظاهرة بلا تسليمات من هذا النطاق.")
    if not filters.get("include_private"):
        notes.append("الهوية والجوال وملاحظات اللقاء والمراجعة ووصف حركات النقاط الخاصة مستبعدة افتراضيًا.")
    return notes


def _student_summary_sections(data):
    students = data["students"]
    rows = []
    for p in students:
        points = data["points"].get(p.pk, {})
        rows.append(_student_row(p, [
            _group_name(p), p.get_academic_stage_display(), "نشط" if p.user.is_active else "معطل",
            data["status"].get(p.pk, {}).get("submitted", 0),
            points.get("earned", 0), points.get("deducted", 0), points.get("net", 0), p.points,
        ]))
    sections = [_section("ملخص الطلاب", [
        "الطالب", "البيئة الحالية", "المرحلة", "حالة الحساب الحالية", "التسليمات في الفترة",
        "المكتسب", "الخصم", "صافي الفترة", "رصيد النقاط الحالي (ليس رصيد الفترة)",
    ], rows)]
    sections.append(_section("حالات التسليم الحالية حسب تاريخ التسليم الأول", ["الطالب", *STATUS_LABELS], [
        _student_row(p, [data["status"].get(p.pk, {}).get(field, 0) for field in STATUS_KEYS])
        for p in students
    ]))
    for key, label, model, field, extra, extra_label in ATTENDANCE_SOURCES:
        rows = []
        for p in students:
            counts = data["attendance"][key].get(p.pk, {})
            present, absent = counts.get("present", 0), counts.get("absent", 0)
            rows.append(_student_row(p, [_group_name(p), present, absent, present + absent,
                                           _percentage(present, present + absent), counts.get("extra", 0)]))
        sections.append(_section(label + " — ملخص الطلاب", [
            "الطالب", "البيئة الحالية", "الحاضر", "الغياب الصريح", "مقام النسبة (سجلات التحضير)",
            "نسبة الحضور %", extra_label,
        ], rows))
    return sections


def _attendance_detail_sections(data, user, filters, *, _access=None):
    """Bulk saved rows and missing rows on observed CURRENT group dates only."""
    students_by_group = defaultdict(list)
    for p in data["students"]:
        if p.group_id is not None:
            students_by_group[p.group_id].append(p)
    sections = []
    group_ids = list(students_by_group)
    for key, label, model, field, extra, extra_label in ATTENDANCE_SOURCES:
        saved = list(data["attendance_queries"][key].order_by(field, "participant_id").values(
            "participant_id", field, "attended", *([extra] if extra else []),
        ))
        rows = []
        seen = set()
        for item in saved:
            p = data["by_id"][item["participant_id"]]
            seen.add((p.pk, item[field]))
            rows.append(_student_row(p, [
                _group_name(p), item[field], "حاضر" if item["attended"] else "غياب صريح",
                "نعم" if (item[extra] if extra else item["attended"]) else "لا",
            ]))
        sections.append(_section(label + " — السجلات المحفوظة", [
            "الطالب", "البيئة الحالية", "التاريخ", "الحالة المسجلة", extra_label,
        ], rows))
        missing = []
        if group_ids:
            # Deliberately independent of participant/stage/status filters:
            # evidence of an observed date can come from a cohort-excluded peer.
            observed = _window(model.objects.filter(
                participant__in=scoped_participants(user, _access=_access), participant__group_id__in=group_ids,
            ), field, filters).order_by(field, "participant__group_id").values_list(
                "participant__group_id", field,
            ).distinct()
            for group_id, day in observed:
                for p in students_by_group[group_id]:
                    if (p.pk, day) not in seen:
                        missing.append(_student_row(p, [_group_name(p), day, "غير مسجل — ليس غيابًا"] ))
        sections.append(_section(label + " — صفوف غير مسجلة على تواريخ ملاحظة فقط", [
            "الطالب", "البيئة الحالية", "تاريخ تحضير ملاحظ", "الحالة",
        ], missing))
    return sections


def _submission_section(queryset, data, title, include_private):
    columns = ["الطالب", "البيئة الحالية", "المهمة", "موعد المهمة", "تاريخ التسليم الأول (الرياض)", "الحالة الحالية"]
    if include_private:
        columns.extend(["سبب الرفض", "ملاحظة التعديل"])
    rows = []
    fields = ["pk", "participant_id", "task_id", "submitted_at", "status", "is_featured"]
    if include_private:
        fields.extend(["rejection_reason", "revision_note"])
    for item in queryset.select_related("task").only(*fields, "task__title", "task__due_date").order_by("submitted_at", "pk"):
        p = data["by_id"][item.participant_id]
        cells = [_group_name(p), item.task.title, item.task.due_date, _stamp(item.submitted_at), _status_label(item)]
        if include_private:
            cells.extend([item.rejection_reason, item.revision_note])
        rows.append(_student_row(p, cells))
    return _section(title, columns, rows)


def _ledger_section(data, include_private):
    columns = ["الطالب", "البيئة الحالية", "وقت التسجيل (الرياض)", "تاريخ الحدث", "المصدر", "دلتا النقاط الموقعة"]
    if include_private:
        columns.append("الوصف الخاص")
    fields = ["participant_id", "created_at", "event_date", "source", "points_delta"]
    if include_private:
        fields.append("description")
    rows = []
    for item in data["ledger_query"].order_by("created_at", "pk").values(*fields):
        p = data["by_id"][item["participant_id"]]
        cells = [_group_name(p), _stamp(item["created_at"]), item["event_date"] or "غير مسجل",
                 dict(PointsLedgerEntry.Source.choices).get(item["source"], item["source"]), item["points_delta"]]
        if include_private:
            cells.append(item["description"])
        rows.append(_student_row(p, cells))
    return _section("حركات الدفتر المطابقة لأساس التاريخ المختار", columns, rows)


def _private_sections(data, filters, *, include_meeting_notes):
    # Never called or queried when the explicit private-data checkbox is off.
    sections = [_section("بيانات الهوية والتواصل الخاصة — تضمين صريح", [
        "الطالب", "الهوية", "جوال الطالب", "جوال ولي الأمر",
    ], [_student_row(p, [p.user.national_id or "—", p.phone or "—", p.guardian_phone or "—"])
        for p in data["students"]])]
    if not include_meeting_notes:
        # LIMITED/SUPERADMIN may see contact data on explicit request, but must
        # never query MeetingNote.text, even with include_private enabled.
        return sections
    notes = _window(MeetingNote.objects.filter(participant_id__in=list(data["by_id"])), "meeting_date", filters)
    sections.append(_section("ملاحظات اللقاء الخاصة — تضمين صريح", ["الطالب", "تاريخ اللقاء", "الملاحظة"], [
        _student_row(data["by_id"][item["participant_id"]], [item["meeting_date"], item["text"]])
        for item in notes.order_by("meeting_date", "participant_id").values("participant_id", "meeting_date", "text")
    ]))
    return sections


def _reference_summary(data, filters):
    """Evidence-backed programme totals, not a reconstructed activity calendar.

    One attended environment/day is one observed meeting. Unknown environments
    remain in cumulative attendance but cannot define a made-up meeting cohort.
    All three additional aggregates are summary-only and independent sources.
    """
    groups = {}
    ungrouped = 0
    group_names = {p.group_id: p.group.name for p in data["students"] if p.group_id is not None}
    observed = data["attendance_queries"]["meeting"].filter(attended=True).order_by().values(
        "participant__group_id", "week_start_date",
    ).annotate(present=Count("pk"))
    for item in observed:
        group_id = item["participant__group_id"]
        if group_id is None:
            ungrouped += item["present"]
            continue
        counts = groups.setdefault(group_id, {"meetings": 0, "present": 0})
        counts["meetings"] += 1
        counts["present"] += item["present"]
    meetings = sum(item["meetings"] for item in groups.values())
    grouped_present = sum(item["present"] for item in groups.values())
    tasks = _window(WeeklyTask.objects.all(), "due_date", filters)
    if filters.get("task"):
        tasks = tasks.filter(pk=filters["task"].pk)
    trip = _window(EliteTripAttendance.objects.filter(
        participant_id__in=list(data["by_id"]), status=EliteTripAttendance.Status.PRESENT,
    ), "date", filters).aggregate(days=Count("date", distinct=True), present=Count("pk"))
    size = len(data["students"])
    earned_miles = _point_counts(data)["earned"] * 10
    metrics = [
        ("البيئات ذات حضور لقاء مسجل", len(groups)),
        ("اللقاءات ذات حضور مسجل", meetings),
        ("إجمالي حضور اللقاء التراكمي", grouped_present + ungrouped),
        ("متوسط حضور لقاءات البيئات", round(grouped_present / meetings, 2) if meetings else "—"),
        ("حضور لقاء غير منسوب لبيئة", ungrouped),
        ("المهام ذات موعد في الفترة", tasks.count()),
        ("إجمالي تسليمات المهام في الفترة", _status_counts(data)["submitted"]),
        ("إجمالي الأميال المكتسبة في الفترة", earned_miles),
        ("متوسط الأميال المكتسبة لكل طالب بالنطاق", round(earned_miles / size, 2) if size else "—"),
        ("أيام رحلة النخبة ذات حضور مسجل", trip["days"]),
        ("إجمالي حضور رحلة النخبة التراكمي", trip["present"]),
    ]
    sections = [_section("حصيلة اللقاءات حسب البيئة الحالية", [
        "البيئة الحالية", "اللقاءات ذات حضور", "الحضور التراكمي", "متوسط الحضور للقاء",
    ], [_row([group_names[group_id], counts["meetings"], counts["present"],
              round(counts["present"] / counts["meetings"], 2)])
        for group_id, counts in sorted(groups.items(), key=lambda item: (group_names[item[0]], item[0]))]),
        _section("مؤشرات تحتاج بيانات غير محفوظة", ["المؤشر", "حالة البيانات"], [
            _row(["المنافسات الرياضية", "غير متاح — لا يوجد تصنيف محفوظ للفعاليات"]),
            _row(["المنافسات الثقافية والحركية", "غير متاح — لا يوجد تصنيف محفوظ للفعاليات"]),
            _row(["عدد الرحلات المنفذة فعليًا", "غير متاح — التحضير يحفظ تاريخًا وحالة، لا سجل تنفيذ رحلة مستقلة"]),
        ])]
    notes = [
        "حصيلة اللقاءات من الحضور الصريح ضمن الفلاتر: البيئة الحالية + التاريخ = لقاء "
        "ذو حضور مسجل. لقاء البيئتين في اليوم نفسه يُعد مرتين، وحضور الطالب عدة "
        "لقاءات يُعد في الإجمالي عدة مرات؛ الغياب أو الحضور المبكر وحده لا يثبت لقاءً.",
        f"متوسط حضور لقاءات البيئات = {grouped_present} حضورًا ÷ {meetings} لقاءً "
        f"ضمن الفلاتر. يوجد {ungrouped} حضور بلا بيئة خارج هذا المتوسط، لكنه داخل الإجمالي.",
        f"الأميال المكتسبة = حركات النقاط الموجبة ×10 وفق أساس التاريخ المختار؛ "
        f"متوسطها = {earned_miles} ميلًا ÷ {size} طالبًا بالنطاق، بما فيهم من لا منحة له. "
        "هذه أميال الفترة المكتسبة لا الرصيد الحالي، ولا تُطرح الخصومات من هذا المؤشر.",
        "عدد المهام بحسب موعدها، لا عدد تكليفات أو إثبات نشرها تاريخيًا؛ "
        "تسليمات الفترة بتاريخ الرفع الأول، ولذلك قد تختلف عن تسليمات تقرير المهام بحسب الموعد.",
        "أيام رحلة النخبة هي تواريخ مختلفة لها حالة «حاضر» ضمن الفلاتر فقط، "
        "حتى لو حضرت عدة بيئات في اليوم نفسه؛ ليست عدد رحلات منفذة مؤكدًا. "
        "لا تُعد حالات المعتذر والغائب وغير المرشح أيام رحلة ذات حضور.",
        "أعداد الرياضة والثقافة والحركة والرحلات المنفذة غير محفوظة كمصادر مستقلة؛ "
        "لا نخمّنها من عنوان المهمة أو ننسخ أرقام التقرير المصور.",
    ]
    return metrics, sections, notes


def _summary_report(data, filters):
    status, points = _status_counts(data), _point_counts(data)
    participating = set()
    for source in data["attendance"].values():
        participating.update(pk for pk, counts in source.items() if counts.get("present", 0) or counts.get("extra", 0))
    participating.update(pk for pk, counts in data["status"].items() if counts.get("submitted", 0))
    participating.update(pk for pk, amounts in data["points"].items() if amounts.get("earned", 0))
    metrics = [("الطلاب في النطاق الحالي", len(data["students"])),
               ("طلاب لديهم مشاركة مسجلة في الفترة", len(participating)),
               ("الحسابات النشطة في النطاق", sum(p.user.is_active for p in data["students"])),
               ("الحسابات المعطلة في النطاق", sum(not p.user.is_active for p in data["students"]))]
    metrics += list(zip(STATUS_LABELS, [status[key] for key in STATUS_KEYS]))
    metrics += [("النقاط المكتسبة في الفترة", points["earned"]), ("النقاط المخصومة في الفترة", points["deducted"]),
                ("صافي نقاط الفترة", points["net"]), ("مجموع رصيد النقاط الحالي (ليس رصيد الفترة)", sum(p.points for p in data["students"]))]
    activity_rows = []
    for key, label, model, field, extra, extra_label in ATTENDANCE_SOURCES:
        counts = _attendance_counts(data, key)
        metrics.extend([(label + " — الحاضر", counts["present"]), (extra_label, counts["extra"])])
        activity_rows.append(_row([label, counts["present"], counts["absent"], counts["recorded"],
                                  _percentage(counts["present"], counts["recorded"]), counts["extra"]]))
    sections = [_section("الحضور والإنجاز — مصادر مستقلة", [
        "المصدر", "الحاضر", "الغياب الصريح", "مقام النسبة (الحاضر + الغياب المسجل)", "نسبة الحضور %", "الإنجاز / المبكر المستقل",
    ], activity_rows), _section("حالات التسليم الحالية في الفترة", ["الحالة", "العدد"], [
        _row([label, status[key]]) for key, label in zip(STATUS_KEYS, STATUS_LABELS)
    ]), _section("النقاط حسب المصدر من الدفتر", ["المصدر", "المكتسب", "الخصم", "الصافي"], [
        _row([label, *[data["source_points"].get(source, {}).get(key, 0) for key in ("earned", "deducted", "net")]])
        for source, label in PointsLedgerEntry.Source.choices
    ])]
    if filters.get("detail") == "detailed":
        sections.extend(_student_summary_sections(data))
    reference_metrics, reference_sections, reference_notes = _reference_summary(data, filters)
    return _metrics(reference_metrics + metrics), reference_sections + sections, reference_notes + [
        "المشاركة المسجلة تحسب الطالب مرة واحدة إذا حضر أو أنجز أو سُجل له حضور مبكر، "
        "أو لديه تسليم ضمن الفترة، أو حركة نقاط موجبة وفق أساس التاريخ المختار. "
        "الغياب أو الخصم وحدهما لا يعدان مشاركة."
    ]


def _students_report(data, filters):
    sections = _student_summary_sections(data)
    if filters.get("detail") == "detailed":
        sections.extend([
            _submission_section(data["submission_query"], data, "تفاصيل التسليمات بحسب تاريخ الرفع الأول", filters.get("include_private", False)),
            _ledger_section(data, filters.get("include_private", False)),
        ])
    return _metrics([("الطلاب في النطاق الحالي", len(data["students"])),
                     ("صافي نقاط الفترة", _point_counts(data)["net"])]), sections, []


def _attendance_report(data, user, filters, *, _access=None):
    sections = []
    metrics = []
    for key, label, model, field, extra, extra_label in ATTENDANCE_SOURCES:
        counts = _attendance_counts(data, key)
        metrics.extend([(label + " — حاضر", counts["present"]), (label + " — غياب صريح", counts["absent"]),
                        (label + " — مقام النسبة", counts["recorded"]),
                        (label + " — نسبة الحضور %", _percentage(counts["present"], counts["recorded"])),
                        (extra_label, counts["extra"])])
    sections = _student_summary_sections(data)[2:]
    notes = ["إنجاز الحلقة والحضور المبكر مستقلان عن الحضور؛ إنجاز الفعالية هو علم attended المحفوظ."]
    if filters.get("detail") == "detailed":
        sections.extend(_attendance_detail_sections(data, user, filters, _access=_access))
        notes.append(MISSING_NOTE)
        missing = sum(len(s["rows"]) for s in sections if "صفوف غير مسجلة" in s["title"])
        notes.append(f"عدد الصفوف غير المسجلة على تواريخ التحضير الملاحظة فقط: {missing}؛ لا يدخل في الغياب أو مقام أي نسبة.")
    return _metrics(metrics), sections, notes


def _tasks_report(data, user, filters):
    tasks = _window(WeeklyTask.objects.all(), "due_date", filters)
    if filters.get("task"):
        tasks = tasks.filter(pk=filters["task"].pk)
    tasks = list(tasks.order_by("due_date", "pk"))
    task_ids = [task.pk for task in tasks]
    saved = TaskSubmission.objects.filter(participant_id__in=list(data["by_id"]), task_id__in=task_ids)
    counts = {row.pop("task_id"): row for row in saved.order_by().values("task_id").annotate(**_status_aggregates())}
    size = len(data["students"])
    rows = []
    totals = {key: 0 for key in STATUS_KEYS}
    for task in tasks:
        current = counts.get(task.pk, {})
        for key in totals:
            totals[key] += current.get(key, 0)
        submitted = current.get("submitted", 0)
        rows.append(_row([task.title, task.due_date, "مفعلة" if task.is_active else "موقوفة", size,
                          *[current.get(key, 0) for key in STATUS_KEYS], size - submitted,
                          _percentage(submitted, size)]))
    sections = [_section("المهام حسب موعد التسليم — كل التسليمات المحفوظة للنطاق", [
        "المهمة", "موعد التسليم", "التفعيل الحالي", "طلاب النطاق الحالي (مقام نسبة التسليم)", *STATUS_LABELS,
        "من لا يملكون تسليمًا من النطاق الحالي", "نسبة التسليم للنطاق الحالي %",
    ], rows)]
    if filters.get("detail") == "detailed" or filters.get("task"):
        sections.append(_submission_section(saved, data, "المسلّمون — كل التسليمات المحفوظة للمهام المختارة", filters.get("include_private", False)))
    if filters.get("task"):
        submitted_ids = set(saved.order_by().values_list("participant_id", flat=True))
        sections.append(_section("طلاب النطاق الحالي الذين لا يملكون تسليمًا للمهمة المختارة", [
            "الطالب", "البيئة الحالية", "المرحلة الحالية", "حالة الحساب الحالية",
        ], [_student_row(p, [_group_name(p), p.get_academic_stage_display(), "نشط" if p.user.is_active else "معطل"])
            for p in data["students"] if p.pk not in submitted_ids]))
    notes = [TASK_NOTE]
    if not tasks:
        notes.append("لا توجد مهام موعدها داخل الفترة المختارة.")
    notes.append(f"مقام نسبة التسليم لكل مهمة: {size} طالبًا من النطاق الحالي، وليس قائمة تكليف تاريخية.")
    return _metrics([("المهام ذات الموعد المطابق", len(tasks)), ("طلاب النطاق الحالي", size),
                     *zip(STATUS_LABELS, [totals[key] for key in STATUS_KEYS])]), sections, notes


def _groups_report(data, user, filters, *, _access=None):
    groups = allowed_groups(user, _access=_access)
    if filters.get("group"):
        groups = groups.filter(pk=filters["group"].pk)
    buckets = {group.pk: (group.name, []) for group in groups}
    for p in data["students"]:
        if p.group_id not in buckets:
            buckets[p.group_id] = (_group_name(p), [])
        buckets[p.group_id][1].append(p)
    rows, task_rows = [], []
    attendance_rows = {key: [] for key, *_ in ATTENDANCE_SOURCES}
    for name, students in buckets.values():
        ids = [p.pk for p in students]
        size = len(students)
        points, status = _point_counts(data, ids), _status_counts(data, ids)
        rows.append(_row([name, size, sum(p.user.is_active for p in students), sum(not p.user.is_active for p in students),
                          points["earned"], points["deducted"], points["net"], size,
                          round(points["net"] / size, 2) if size else "—", sum(p.points for p in students)]))
        task_rows.append(_row([name, size, *[status[key] for key in STATUS_KEYS],
                               sum(data["status"].get(p.pk, {}).get("submitted", 0) > 0 for p in students),
                               _percentage(sum(data["status"].get(p.pk, {}).get("submitted", 0) > 0 for p in students), size)]))
        for key, label, model, field, extra, extra_label in ATTENDANCE_SOURCES:
            counts = _attendance_counts(data, key, ids)
            attendance_rows[key].append(_row([name, size, counts["present"], counts["absent"], counts["recorded"],
                                              _percentage(counts["present"], counts["recorded"]), counts["extra"]]))
    sections = [_section("حجم البيئة والنقاط — حسب العضوية الحالية", [
        "البيئة الحالية", "طلاب النطاق", "النشطون", "المعطلون", "المكتسب", "الخصم", "صافي الفترة",
        "مقام متوسط الصافي (طلاب النطاق)", "متوسط الصافي لكل طالب", "مجموع الرصيد الحالي (ليس رصيد الفترة)",
    ], rows), _section("تسليمات الفترة — مقام الطلاب ظاهر", [
        "البيئة الحالية", "طلاب النطاق (مقام نسبة الطلاب المسلّمين)", *STATUS_LABELS,
        "طلاب لديهم تسليم واحد على الأقل في الفترة", "نسبة الطلاب ذوي التسليم %",
    ], task_rows)]
    for key, label, model, field, extra, extra_label in ATTENDANCE_SOURCES:
        sections.append(_section(label + " — مقارنة البيئات", [
            "البيئة الحالية", "طلاب النطاق", "الحاضر", "الغياب الصريح", "مقام الحضور (السجلات المحفوظة)",
            "نسبة الحضور %", extra_label,
        ], attendance_rows[key]))
    if filters.get("detail") == "detailed":
        sections.extend(_student_summary_sections(data))
    notes = ["البيئات ذات النطاق الفارغ تبقى ظاهرة بأعداد صفرية ومقام صفر ونسبة غير متاحة؛ ليست نسبة صفرية مفترضة.",
             "«بدون بيئة» تجميع ذو معنى للطلاب بلا ارتباط حالي، لا بيئة فعلية ولا عضوية تاريخية."]
    return _metrics([("البيئات والتجميعات المعروضة", len(buckets)), ("طلاب النطاق الحالي", len(data["students"]))]), sections, notes


def build_reports(user, filters):
    """Build COMPLETE reports behind one fresh access and persisted-role scope."""
    access = current_report_access(user)
    if not (access["can_view"] and access["can_center"]):
        raise PermissionDenied("لا تملك صلاحية الاطلاع على مركز التقارير.")
    selected = list(dict.fromkeys(filters.get("reports", ["summary"])))
    if not selected or any(key not in dict(REPORT_CHOICES) for key in selected):
        raise forms.ValidationError("اختر تقارير صالحة من القائمة المتاحة.")
    data = _load_data(user, filters, _access=access)
    common_notes = _common_notes(data, filters)
    private = _private_sections(
        data, filters, include_meeting_notes=can_view_private_report_notes(user, _access=access),
    ) if filters.get("include_private") else []
    reports = []
    descriptions = {
        "summary": "نظرة عامة على النشاط الفعلي وحالات التسليم والنقاط ضمن الفلاتر.",
        "students": "أعداد مستقلة لكل طالب مع النقاط المسجلة ورصيد حالي منفصل.",
        "attendance": "الحضور والغياب الصريح والإنجاز حسب تاريخ النشاط، دون تقويم مفترض.",
        "tasks": "المهام بحسب موعدها، وكل تسليماتها المحفوظة ضمن النطاق الحالي.",
        "groups": "مقارنة بأعداد ومقامات واضحة حسب بيئة الطلاب الحالية.",
    }
    for key in selected:
        if key == "summary":
            metrics, sections, notes = _summary_report(data, filters)
        elif key == "students":
            metrics, sections, notes = _students_report(data, filters)
        elif key == "attendance":
            metrics, sections, notes = _attendance_report(data, user, filters, _access=access)
        elif key == "tasks":
            metrics, sections, notes = _tasks_report(data, user, filters)
        else:
            metrics, sections, notes = _groups_report(data, user, filters, _access=access)
        # Copy containers because display pagination must not mutate another
        # report's shared private sections; exports always call this afresh.
        sections = sections + [{"title": s["title"], "columns": s["columns"][:], "rows": s["rows"][:]} for s in private]
        reports.append({
            "key": key, "title": dict(REPORT_CHOICES)[key], "description": descriptions[key],
            "metrics": metrics, "notes": common_notes + notes, "sections": sections,
        })
    return reports
