from django.shortcuts import render

# Create your views here.

import datetime

import openpyxl
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Min, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.generic import FormView, TemplateView

from accounts.models import Role, User
from .forms import (
    CircleAttendanceForm,
    ParticipantImportForm,
    TaskSubmissionForm,
    WeeklyTaskForm,
)
from .models import (
    CircleAttendance,
    Group,
    MeetingAttendance,
    Participant,
    TaskSubmission,
    WeeklyTask,
)

# Official weekly points table values for the Horizon program.
CIRCLE_DAY_POINTS = 3
MEETING_FULL_POINTS = 8
MEETING_EARLY_BONUS_POINTS = 2


def apply_points_delta(participant, points_delta):
    """
    Applies a points delta to a participant's triple-currency balances,
    following the program's fixed conversion rule: miles = points * 10,
    purchase_points = same as points. Accepts negative deltas (for
    corrections that reduce previously awarded points), but never allows
    any balance to drop below zero — clamped at 0 as a safety floor.
    """
    participant.points = max(0, participant.points + points_delta)
    participant.miles = max(0, participant.miles + points_delta * 10)
    participant.purchase_points = max(0, participant.purchase_points + points_delta)
    participant.save(update_fields=["points", "miles", "purchase_points"])


def circle_attendance_points(attended):
    return CIRCLE_DAY_POINTS if attended else 0


def meeting_attendance_points(attended, is_early):
    if not attended:
        return 0
    points = MEETING_FULL_POINTS
    if is_early:
        points += MEETING_EARLY_BONUS_POINTS
    return points


class ParticipantDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "participants/participant_dashboard.html"
    login_url = "accounts:login_participant"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        participant = self.request.user.participant
        context["participant"] = participant

        context["group_range"] = self._build_range(
            Participant.objects.filter(group=participant.group)
            if participant.group is not None
            else Participant.objects.none(),
            participant,
        )
        context["program_range"] = self._build_range(
            Participant.objects.all(),
            participant,
        )

        return context

    def _build_range(self, queryset, participant):
        aggregates = queryset.aggregate(min_miles=Min("miles"), max_miles=Max("miles"))
        min_miles = aggregates["min_miles"]
        max_miles = aggregates["max_miles"]

        if min_miles is None or max_miles is None:
            return None

        if max_miles == min_miles:
            position_percent = 50
        else:
            position_percent = round(
                (participant.miles - min_miles) / (max_miles - min_miles) * 100
            )

        return {
            "min_miles": min_miles,
            "max_miles": max_miles,
            "position_percent": position_percent,
        }


class SupervisorDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """
    Weekly-meeting attendance for a whole environment at once.

    One table lists every participant of the supervisor's group with two
    checkbox columns ("early arrival" / "attended"). A date picker at the top
    selects which meeting day is being prepared (defaults to today; any past
    or future date is allowed — no time restriction, per the project owner's
    explicit decision). Changing the date is a plain GET reload; saving is a
    single POST that upserts the whole table in one shot.

    Note on `MeetingAttendance.week_start_date`: the field name is unchanged,
    but this design has no concept of a "week". The date chosen in the UI is
    stored verbatim as `week_start_date` — it is treated purely as "the
    selected meeting date", with no first-day-of-week calculation.
    """

    template_name = "participants/supervisor_dashboard.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role == Role.GROUP_SUPERVISOR

    def get_group(self):
        # A GROUP_SUPERVISOR is linked to their Group via Group.supervisor.
        # Group.supervisor is an unnamed ForeignKey, so the reverse accessor
        # from User is Django's default "group_set".
        return self.request.user.group_set.first()

    def get_selected_date(self):
        # Defaults to today. The date travels as ?date=YYYY-MM-DD on a GET
        # reload and as a hidden POST field on save, so both are accepted
        # (POST wins when present). Any date parses — past or future — with
        # no restriction, per the explicit decision. A malformed value falls
        # back to today rather than erroring.
        raw = self.request.POST.get("date") or self.request.GET.get("date")
        if raw:
            try:
                return datetime.date.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.date.today()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.get_group()
        selected_date = self.get_selected_date()

        context["group"] = group
        context["selected_date"] = selected_date

        if group:
            participants = Participant.objects.filter(group=group).select_related(
                "user"
            )
            existing_records = {
                record.participant_id: record
                for record in MeetingAttendance.objects.filter(
                    participant__group=group,
                    week_start_date=selected_date,
                )
            }
            roster = []
            for participant in participants:
                record = existing_records.get(participant.id)
                roster.append({
                    "participant": participant,
                    "attended": record.attended if record else False,
                    "is_early": record.is_early if record else False,
                })
            context["roster"] = roster
        else:
            context["roster"] = []

        return context

    def post(self, request, *args, **kwargs):
        group = self.get_group()
        selected_date = self.get_selected_date()

        if not group:
            # Nothing to save for a supervisor with no environment linked —
            # just re-render the (empty) page.
            return self.get(request, *args, **kwargs)

        # The loop below iterates ONLY over this supervisor's own participant
        # ids and reads POST fields whose names are built from those ids, so
        # a crafted request naming a participant from another environment
        # simply has no effect — it is never looked at.
        participant_ids = set(
            Participant.objects.filter(group=group).values_list("id", flat=True)
        )

        for participant_id in participant_ids:
            attended = request.POST.get(f"attended_{participant_id}") == "on"
            is_early = request.POST.get(f"early_{participant_id}") == "on"
            participant = Participant.objects.get(id=participant_id)

            existing = MeetingAttendance.objects.filter(
                participant_id=participant_id,
                week_start_date=selected_date,
            ).first()

            old_points = (
                meeting_attendance_points(existing.attended, existing.is_early)
                if existing
                else 0
            )
            new_points = meeting_attendance_points(attended, is_early)

            if existing:
                existing.attended = attended
                existing.is_early = is_early
                existing.recorded_by = request.user
                existing.save()
            else:
                MeetingAttendance.objects.create(
                    participant_id=participant_id,
                    week_start_date=selected_date,
                    attended=attended,
                    is_early=is_early,
                    recorded_by=request.user,
                )

            delta = new_points - old_points
            if delta != 0:
                apply_points_delta(participant, delta)

        return redirect(
            f"{reverse('participants:supervisor_dashboard')}"
            f"?date={selected_date.isoformat()}"
        )


# ---------------------------------------------------------------------------
# Bulk participant import (Excel)
# ---------------------------------------------------------------------------

# Maps the Arabic display label used in the Excel template's "المرحلة الدراسية"
# column to AcademicStage's internal value. Verified against
# AcademicStage.choices in participants/models.py — matches exactly, no
# correction needed.
ACADEMIC_STAGE_LABEL_TO_VALUE = {
    "خامس ابتدائي": "grade_5",
    "سادس ابتدائي": "grade_6",
    "أول متوسط": "grade_7",
    "ثاني متوسط": "grade_8",
    "ثالث متوسط": "grade_9",
}

# The import template ships with a frozen sheet name and a fixed column order.
IMPORT_SHEET_NAME = "المشاركون"
# Row 1 = headers, row 2 = the italic example row (always skipped regardless of
# its content), so real data begins at row 3.
IMPORT_FIRST_DATA_ROW = 3


def _cell_text(value):
    """
    Normalise a raw openpyxl cell value to a stripped string.

    Excel commonly stores digit strings (national ids, phone numbers) as
    numbers, which openpyxl hands back as int/float — turn an integer-valued
    float like 1234567890.0 into "1234567890" rather than "1234567890.0".
    Returns "" for a blank cell.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


class ParticipantImportView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = "participants/import_participants.html"
    form_class = ParticipantImportForm

    def test_func(self):
        # Same access pattern as SupervisorDashboardView.test_func, but this
        # page is open to BOTH general supervisors and superadmins.
        return self.request.user.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)

    def form_valid(self, form):
        result = self._process_import(form.cleaned_data["excel_file"])
        context = self.get_context_data(form=form)
        context["import_result"] = result
        return self.render_to_response(context)

    def _process_import(self, excel_file):
        try:
            workbook = openpyxl.load_workbook(excel_file, data_only=True)
        except Exception:
            return {"error": "تعذّرت قراءة الملف. تأكد أنه ملف إكسل صالح بصيغة xlsx."}

        if IMPORT_SHEET_NAME not in workbook.sheetnames:
            return {"error": f'الملف لا يحتوي على ورقة باسم "{IMPORT_SHEET_NAME}".'}

        sheet = workbook[IMPORT_SHEET_NAME]

        created_count = 0
        rejected_rows = []

        for row_number in range(IMPORT_FIRST_DATA_ROW, sheet.max_row + 1):
            raw = [sheet.cell(row=row_number, column=c).value for c in range(1, 7)]

            # Skip fully empty rows (common trailing rows in a spreadsheet).
            if not any(v not in (None, "") for v in raw):
                continue

            full_name = _cell_text(raw[0])
            national_id = _cell_text(raw[1])
            group_name = _cell_text(raw[2])
            stage_label = _cell_text(raw[3])
            phone = _cell_text(raw[4])
            guardian_phone = _cell_text(raw[5])

            error = self._validate_row(full_name, national_id, stage_label)
            if error:
                rejected_rows.append({"row": row_number, "reason": error})
                continue

            group = None
            if group_name:
                # Exact match on Group.name (the unique name field). A missing
                # environment rejects the row — environments are never created
                # automatically.
                group = Group.objects.filter(name=group_name).first()
                if group is None:
                    rejected_rows.append({
                        "row": row_number,
                        "reason": f'البيئة "{group_name}" غير موجودة بالنظام',
                    })
                    continue

            academic_stage_value = ACADEMIC_STAGE_LABEL_TO_VALUE[stage_label]

            try:
                with transaction.atomic():
                    # Participants authenticate by national_id only (via
                    # NationalIDOrUsernameBackend) and never use a password, but
                    # AbstractBaseUser still needs one stored — mirror
                    # UserCreationForm.save()'s approach exactly.
                    user = User.objects.create_user(
                        national_id=national_id,
                        full_name=full_name,
                        role=Role.PARTICIPANT,
                        password=get_random_string(50),
                    )
                    Participant.objects.create(
                        user=user,
                        group=group,
                        academic_stage=academic_stage_value,
                        phone=phone,
                        guardian_phone=guardian_phone,
                    )
            except IntegrityError:
                # A duplicate national id that slipped past the exists() check
                # — a second occurrence of the same id later in this same file,
                # or a concurrent import of it.
                rejected_rows.append({
                    "row": row_number,
                    "reason": "رقم الهوية مسجّل مسبقًا في النظام",
                })
                continue

            created_count += 1

        return {"created_count": created_count, "rejected_rows": rejected_rows}

    def _validate_row(self, full_name, national_id, stage_label):
        if not full_name:
            return "الاسم الكامل مطلوب"

        if not national_id:
            return "رقم الهوية / الإقامة مطلوب"

        if len(national_id) != 10 or not national_id.isdigit():
            return "رقم الهوية / الإقامة يجب أن يتكون من 10 أرقام"

        if User.objects.filter(national_id=national_id).exists():
            return "رقم الهوية مسجّل مسبقًا في النظام"

        if not stage_label:
            return "المرحلة الدراسية مطلوبة"

        if stage_label not in ACADEMIC_STAGE_LABEL_TO_VALUE:
            return "المرحلة الدراسية غير معروفة"

        return None


# ---------------------------------------------------------------------------
# General supervisor dashboard
# ---------------------------------------------------------------------------


class GeneralSupervisorDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/general_supervisor_dashboard.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)

    def post(self, request, *args, **kwargs):
        # The only POST action on this page today: a full, program-wide
        # points reset. Confirmation happens client-side (a JS confirm()
        # dialog in the template) before the form ever submits — this is a
        # deliberate, wide-reaching action affecting every participant.
        if request.POST.get("action") == "reset_points":
            # Only `points` is reset — `miles` (permanent) and
            # `purchase_points` (never reset, spent in the store) must never
            # be touched by this action.
            Participant.objects.update(points=0)

        return redirect("participants:general_supervisor_dashboard")


# ---------------------------------------------------------------------------
# Participants data table (scoped by role)
# ---------------------------------------------------------------------------


class ParticipantsDataView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """
    Read-only, searchable/sortable table of participant data.

    Open to all three admin roles. A GROUP_SUPERVISOR only sees their own
    environment's participants; a GENERAL_SUPERVISOR and a SUPERADMIN see
    every participant across every environment, unfiltered.

    The two attendance-count columns are cumulative totals since the start of
    the program (no date window): each is the number of that participant's
    attendance records with attended=True. They are computed with annotated
    Count(..., distinct=True) — distinct is required because both counts are
    joined onto the same Participant row in one query, and without it each
    count would be multiplied by the number of rows in the other join.
    """

    template_name = "participants/participants_data.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role in (
            Role.GROUP_SUPERVISOR,
            Role.GENERAL_SUPERVISOR,
            Role.SUPERADMIN,
        )

    def get_queryset(self):
        # GROUP_SUPERVISOR is linked to their Group via Group.supervisor, an
        # unnamed ForeignKey, so the reverse accessor from User is Django's
        # default "group_set" — same pattern as SupervisorDashboardView.get_group().
        if self.request.user.role == Role.GROUP_SUPERVISOR:
            group = self.request.user.group_set.first()
            base_queryset = (
                Participant.objects.filter(group=group)
                if group
                else Participant.objects.none()
            )
        else:
            base_queryset = Participant.objects.all()

        # CircleAttendance.participant / MeetingAttendance.participant carry
        # explicit related_name values ("circle_attendances" /
        # "meeting_attendances"), so those are the reverse accessors used here.
        return base_queryset.select_related("user", "group").annotate(
            circle_days_attended=Count(
                "circle_attendances",
                filter=Q(circle_attendances__attended=True),
                distinct=True,
            ),
            meetings_attended=Count(
                "meeting_attendances",
                filter=Q(meeting_attendances__attended=True),
                distinct=True,
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["participants"] = self.get_queryset()
        context["is_scoped_to_group"] = self.request.user.role == Role.GROUP_SUPERVISOR
        return context


# ---------------------------------------------------------------------------
# Weekly task (upload + review)
# ---------------------------------------------------------------------------


class WeeklyTaskReviewView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/weekly_task_review.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_task = WeeklyTask.objects.order_by("-created_at").first()
        context["current_task"] = current_task
        context.setdefault("create_form", WeeklyTaskForm())
        if current_task:
            context["submissions"] = current_task.submissions.select_related(
                "participant__user"
            ).order_by("-submitted_at")
        else:
            context["submissions"] = TaskSubmission.objects.none()
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")

        if action == "create_task":
            form = WeeklyTaskForm(request.POST)
            if form.is_valid():
                task = form.save(commit=False)
                task.created_by = request.user
                task.save()
            else:
                context = self.get_context_data(create_form=form)
                return self.render_to_response(context)

        elif action in ("accept", "reject"):
            submission_id = request.POST.get("submission_id")
            submission = TaskSubmission.objects.filter(id=submission_id).first()
            if submission and submission.status == TaskSubmission.Status.PENDING:
                submission.status = (
                    TaskSubmission.Status.ACCEPTED
                    if action == "accept"
                    else TaskSubmission.Status.REJECTED
                )
                submission.reviewed_by = request.user
                submission.reviewed_at = timezone.now()
                submission.save()

                if action == "accept":
                    apply_points_delta(submission.participant, 10)

        return redirect("participants:weekly_task_review")


class TaskSubmissionView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = "participants/task_submission_form.html"
    form_class = TaskSubmissionForm

    def test_func(self):
        return self.request.user.role == Role.PARTICIPANT

    def get_current_task(self):
        return WeeklyTask.objects.order_by("-created_at").first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = self.get_current_task()
        context["task"] = task

        if task:
            context["existing_submission"] = TaskSubmission.objects.filter(
                task=task, participant=self.request.user.participant
            ).first()
        else:
            context["existing_submission"] = None

        return context

    def post(self, request, *args, **kwargs):
        task = self.get_current_task()
        participant = request.user.participant

        if not task:
            return redirect("participants:task_submission")

        # Never allow a submission after the due date has passed, and never
        # allow a second submission for a task the participant already
        # submitted for — both checks happen here in the view, not only in
        # the template, since a crafted request could bypass a disabled UI
        # button.
        if task.is_past_due():
            return redirect("participants:task_submission")

        if TaskSubmission.objects.filter(task=task, participant=participant).exists():
            return redirect("participants:task_submission")

        form = TaskSubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.task = task
            submission.participant = participant
            submission.save()

        return redirect("participants:task_submission")
