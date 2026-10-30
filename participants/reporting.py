"""Read-only student reporting, with separate aggregates to avoid join fan-out."""
import datetime

from django import forms
from django.core.validators import MinValueValidator
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import Role
from .models import (
    CircleAttendance, Group, MeetingAttendance, Participant,
    PointsLedgerEntry, TaskSubmission, WeeklyActivityAttendance,
)

REPORT_ROLES = (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)
POINT_COLUMNS = {
    PointsLedgerEntry.Source.QURAN_CIRCLE: "points_circle",
    PointsLedgerEntry.Source.WEEKLY_TASK: "points_tasks",
    PointsLedgerEntry.Source.MEETING_ATTENDANCE: "points_meetings",
    PointsLedgerEntry.Source.WEEKLY_ACTIVITY: "points_activities",
    PointsLedgerEntry.Source.EXTRA: "points_extra",
}
COUNT_COLUMNS = (
    "circle_attended", "circle_achieved", "tasks_submitted", "tasks_accepted",
    "tasks_pending", "tasks_returned", "tasks_rejected", "meetings_attended",
    "activities_attended",
)


def allowed_groups(user):
    if user.role == Role.GROUP_SUPERVISOR:
        return user.group_set.all().order_by("name")
    if user.role in REPORT_ROLES:
        return Group.objects.all().order_by("name")
    return Group.objects.none()


def scoped_participants(user):
    students = Participant.objects.filter(user__role=Role.PARTICIPANT)
    if user.role == Role.GROUP_SUPERVISOR:
        return students.filter(group__in=allowed_groups(user))
    return students if user.role in REPORT_ROLES else students.none()


class StudentReportFilterForm(forms.Form):
    start_date = forms.DateField(
        required=False, label="من تاريخ", input_formats=["%Y-%m-%d"],
        validators=[MinValueValidator(datetime.date(1000, 1, 1))],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "min": "1000-01-01"}),
        error_messages={"invalid": "أدخل تاريخ بداية صالحًا.", "min_value": "يجب ألا يكون التاريخ قبل 1000/01/01."},
    )
    end_date = forms.DateField(
        required=False, label="إلى تاريخ", input_formats=["%Y-%m-%d"],
        validators=[MinValueValidator(datetime.date(1000, 1, 1))],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "min": "1000-01-01"}),
        error_messages={"invalid": "أدخل تاريخ نهاية صالحًا.", "min_value": "يجب ألا يكون التاريخ قبل 1000/01/01."},
    )
    group = forms.ModelChoiceField(
        queryset=Group.objects.none(), required=False, label="البيئة",
        empty_label="جميع البيئات المتاحة",
        error_messages={"invalid_choice": "البيئة المختارة غير متاحة ضمن صلاحياتك."},
    )
    q = forms.CharField(
        required=False, max_length=100, label="البحث عن طالب",
        widget=forms.TextInput(attrs={"placeholder": "اسم الطالب أو رقم الهوية", "autocomplete": "off"}),
        error_messages={"max_length": "نص البحث أطول من الحد المسموح."},
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = allowed_groups(user)

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("end_date")
        if start and end and start > end:
            raise forms.ValidationError("يجب أن يكون تاريخ البداية قبل تاريخ النهاية أو مساويًا له.")
        return data


def date_window(queryset, field, start, end, *, timestamp=False):
    """Inclusive calendar-day bounds in the site's timezone; no SQL TZ tables."""
    if timestamp:
        zone = timezone.get_current_timezone()
        if start:
            lower = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min), zone)
            queryset = queryset.filter(**{field + "__gte": lower})
        if end:
            upper = timezone.make_aware(datetime.datetime.combine(end, datetime.time.max), zone)
            queryset = queryset.filter(**{field + "__lte": upper})
    else:
        if start:
            queryset = queryset.filter(**{field + "__gte": start})
        if end:
            queryset = queryset.filter(**{field + "__lte": end})
    return queryset


def build_student_report(user, filters):
    """Six bulk queries independent of student count, all tied to the same scope.

    Activity counts use activity dates. Points use actual signed ledger deltas
    at grant/correction time, not a recomputed score or a historical balance.
    TaskSubmission only retains its first submitted_at, not resubmission history.
    """
    students = scoped_participants(user)
    if filters.get("group"):
        students = students.filter(group=filters["group"])
    if filters.get("q"):
        query = filters["q"]
        students = students.filter(Q(user__full_name__icontains=query) | Q(user__national_id__icontains=query))
    student_ids = students.order_by().values("pk")
    rows = []
    for student in students.select_related("user", "group").order_by("user__full_name", "pk"):
        row = {"participant": student, "net_points": 0}
        row.update({key: 0 for key in COUNT_COLUMNS})
        row.update({key: 0 for key in POINT_COLUMNS.values()})
        rows.append(row)
    if not rows:
        return rows
    by_id = {row["participant"].pk: row for row in rows}
    start, end = filters.get("start_date"), filters.get("end_date")

    circle = date_window(CircleAttendance.objects.filter(participant_id__in=student_ids), "date", start, end)
    for values in circle.order_by().values("participant_id").annotate(
        circle_attended=Count("pk", filter=Q(attended=True)),
        circle_achieved=Count("pk", filter=Q(achieved=True)),
    ):
        row = by_id.get(values.pop("participant_id"))
        if row is not None:
            row.update(values)

    submissions = date_window(
        TaskSubmission.objects.filter(participant_id__in=student_ids),
        "submitted_at", start, end, timestamp=True,
    )
    for values in submissions.order_by().values("participant_id").annotate(
        tasks_submitted=Count("pk"),
        tasks_accepted=Count("pk", filter=Q(status=TaskSubmission.Status.ACCEPTED)),
        tasks_pending=Count("pk", filter=Q(status=TaskSubmission.Status.PENDING)),
        tasks_returned=Count("pk", filter=Q(status=TaskSubmission.Status.REVISION_REQUIRED)),
        tasks_rejected=Count("pk", filter=Q(status=TaskSubmission.Status.REJECTED)),
    ):
        row = by_id.get(values.pop("participant_id"))
        if row is not None:
            row.update(values)

    meeting = date_window(MeetingAttendance.objects.filter(participant_id__in=student_ids), "week_start_date", start, end)
    for values in meeting.filter(attended=True).order_by().values("participant_id").annotate(count=Count("pk")):
        row = by_id.get(values["participant_id"])
        if row is not None:
            row["meetings_attended"] = values["count"]

    activity = date_window(WeeklyActivityAttendance.objects.filter(participant_id__in=student_ids), "date", start, end)
    for values in activity.filter(attended=True).order_by().values("participant_id").annotate(count=Count("pk")):
        row = by_id.get(values["participant_id"])
        if row is not None:
            row["activities_attended"] = values["count"]

    ledger = date_window(
        PointsLedgerEntry.objects.filter(participant_id__in=student_ids),
        "created_at", start, end, timestamp=True,
    )
    for values in ledger.order_by().values("participant_id", "source").annotate(total=Sum("points_delta")):
        row = by_id.get(values["participant_id"])
        if row is not None:
            total = values["total"] or 0
            row["net_points"] += total
            column = POINT_COLUMNS.get(values["source"])
            if column:
                row[column] = total

    rows.sort(key=lambda row: (-row["net_points"], row["participant"].user.full_name, row["participant"].pk))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows
