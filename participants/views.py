from django.shortcuts import render

# Create your views here.

import datetime

import openpyxl
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Min, ProtectedError, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.generic import FormView, TemplateView

from accounts.models import Role, User
from .forms import (
    CircleAttendanceForm,
    ParticipantImportForm,
    StoreProductForm,
    TaskSubmissionForm,
    WeeklyTaskForm,
)
from .models import (
    CircleAttendance,
    Group,
    MeetingAttendance,
    Participant,
    StoreOrder,
    StoreProduct,
    TaskSubmission,
    WeeklyTask,
)

# Official weekly points table values for the Horizon program.
CIRCLE_DAY_POINTS = 3
MEETING_FULL_POINTS = 8
MEETING_EARLY_BONUS_POINTS = 2


# ---------------------------------------------------------------------------
# Shared in-app navbar (app_base.html)
# ---------------------------------------------------------------------------
#
# Every post-login page extends participants/app_base.html, which renders a
# role-aware navbar (horizontal on desktop, fixed bottom bar on mobile) from a
# `navbar_items` context list. This helper builds that list; each view calls it
# with the key of the entry that represents the page currently being shown so
# that entry is highlighted. It carries NO business logic — it only produces
# display data for the template.
#
# Navbar icons are hand-written inline SVG (outline style, stroke=currentColor)
# rather than Unicode emoji, so every OS/browser renders them identically and
# they inherit the surrounding text colour (including the `is-active` state)
# with no extra CSS. The template prints these strings through the `|safe`
# filter. Keep them dependency-free — plain geometric paths only.
ICON_HOME = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>'
)
ICON_TASKS = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<rect x="5" y="4" width="14" height="17" rx="2"/>'
    '<path d="M9 3h6v3H9z"/><path d="M8.5 11h7"/><path d="M8.5 15h7"/></svg>'
)
ICON_STORE = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<path d="M6 8h12l1.2 12H4.8z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/></svg>'
)
ICON_DATA = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 20c0-3.3 2.5-5.5 5.5-5.5s5.5 2.2 5.5 5.5"/>'
    '<path d="M16 5.2a3 3 0 0 1 0 5.6"/><path d="M17.5 14.7c2 .9 3.5 2.8 3.5 5.3"/></svg>'
)
ICON_IMPORT = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<path d="M12 15V4"/><path d="M7 9l5-5 5 5"/><path d="M5 19h14"/></svg>'
)
ICON_ATTENDANCE = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M4 9h16"/>'
    '<path d="M8 3v4"/><path d="M16 3v4"/><path d="M9 14.5l2 2 4-4.5"/></svg>'
)
ICON_LOGOUT = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<path d="M14 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8"/>'
    '<path d="M18 8l4 4-4 4"/><path d="M22 12H10"/></svg>'
)
ICON_ARCHIVE = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<rect x="3" y="4" width="18" height="4" rx="1"/>'
    '<path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/>'
    '<path d="M10 12h4"/></svg>'
)
ICON_CART = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" width="20" height="20">'
    '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/>'
    '<path d="M3 4h2l2.4 12.2a1 1 0 0 0 1 .8h9.2a1 1 0 0 0 1-.8L21 8H6"/></svg>'
)


def build_navbar(user, active_key):
    if user.role == Role.PARTICIPANT:
        entries = [
            ("home", "الرئيسية", "participants:dashboard", ICON_HOME),
            ("tasks", "المهام", "participants:task_submission", ICON_TASKS),
            ("store", "المتجر", "participants:store", ICON_STORE),
        ]
    elif user.role == Role.GROUP_SUPERVISOR:
        # A group supervisor has no dashboard separate from the attendance
        # roster, so "الرئيسية" and "التحضير" would be the exact same link.
        # Rather than list one URL twice, this is a single "التحضير" entry.
        entries = [
            (
                "attendance",
                "التحضير",
                "participants:supervisor_dashboard",
                ICON_ATTENDANCE,
            ),
            ("data", "بيانات المشاركين", "participants:participants_data", ICON_DATA),
        ]
    else:  # GENERAL_SUPERVISOR / SUPERADMIN
        entries = [
            (
                "home",
                "الرئيسية",
                "participants:general_supervisor_dashboard",
                ICON_HOME,
            ),
            ("tasks", "المهام", "participants:weekly_task_review", ICON_TASKS),
            (
                "tasks_archive",
                "أرشيف المهام",
                "participants:tasks_archive",
                ICON_ARCHIVE,
            ),
            (
                "store_management",
                "طلبات المتجر",
                "participants:store_management",
                ICON_CART,
            ),
            ("import", "الاستيراد", "participants:import_participants", ICON_IMPORT),
            ("data", "بيانات المشاركين", "participants:participants_data", ICON_DATA),
        ]

    return [
        {
            "key": key,
            "label": label,
            "url": reverse(url_name),
            "icon": icon,
            "active": key == active_key,
        }
        for key, label, url_name, icon in entries
    ]


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

        # Only the program-wide miles comparison is shown on the dashboard;
        # the per-environment miles tab was dropped, so `group_range` is no
        # longer built (it would be an unused extra aggregate query).
        context["program_range"] = self._build_range(
            Participant.objects.all(),
            participant,
        )

        # Elite-trip ("رحلة النخبة") indicator: a per-request, never-stored
        # message telling the participant only their distance to / safety
        # margin from the 20-seat nomination cutoff inside their own
        # environment. No other participant's rank, points, or name is
        # exposed — see get_elite_status().
        context["elite_status"] = self.get_elite_status(participant)

        context["navbar_items"] = build_navbar(self.request.user, "home")
        return context

    def get_elite_status(self, participant):
        """
        Compute the elite-trip nomination status for `participant` within
        their environment, following آلية_مؤشر_التبشير_برحلة_النخبة.md.

        Returns None when there is no environment or the participant is not
        found in the ranking; otherwise a dict with exactly:
        {"eliteStatus": "qualified"|"not_qualified", "difference": int|None,
         "message": str}. Nothing about other participants is returned.

        Everything is recomputed here on every page load — no value is
        persisted.
        """
        if participant.group is None:
            return None

        # Same ordering the rest of the app would use for a ranking:
        # highest points = rank 1. A secondary "-id" would be arbitrary;
        # "id" ascending is a stable, deterministic tie-breaker so two
        # participants on identical points always resolve the same way
        # across repeated requests (the spec does not define tie handling).
        group_ranking = list(
            Participant.objects.filter(group=participant.group)
            .order_by("-points", "id")
            .values_list("id", "points")
        )

        participant_index = next(
            (i for i, (pid, _) in enumerate(group_ranking) if pid == participant.id),
            None,
        )
        if participant_index is None:
            return None

        rank = participant_index + 1  # 1-indexed, matching the spec's "المركز 20/21"

        def points_at_rank(target_rank):
            if target_rank < 1 or target_rank > len(group_ranking):
                return None
            return group_ranking[target_rank - 1][1]

        if rank > 20:
            rank_20_points = points_at_rank(20)
            if rank_20_points is None:
                return None
            difference = rank_20_points - participant.points
            return {
                "eliteStatus": "not_qualified",
                "difference": difference,
                "message": f"بقي لك {difference} نقطة لتصل إلى رحلة النخبة",
            }
        else:
            rank_21_points = points_at_rank(21)
            if rank_21_points is None:
                # Fewer than 21 participants in the group — everyone within
                # the top 20 is safely qualified with no one to compare against.
                return {
                    "eliteStatus": "qualified",
                    "difference": None,
                    "message": "أنت ضمن المرشحين لرحلة النخبة",
                }
            difference = participant.points - rank_21_points
            return {
                "eliteStatus": "qualified",
                "difference": difference,
                "message": f"بينك وبين عدم الترشيح {difference} نقطة",
            }

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
                roster.append(
                    {
                        "participant": participant,
                        "attended": record.attended if record else False,
                        "is_early": record.is_early if record else False,
                    }
                )
            context["roster"] = roster
        else:
            context["roster"] = []

        context["navbar_items"] = build_navbar(self.request.user, "attendance")
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["navbar_items"] = build_navbar(self.request.user, "import")
        return context

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
                    rejected_rows.append(
                        {
                            "row": row_number,
                            "reason": f'البيئة "{group_name}" غير موجودة بالنظام',
                        }
                    )
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
                rejected_rows.append(
                    {
                        "row": row_number,
                        "reason": "رقم الهوية مسجّل مسبقًا في النظام",
                    }
                )
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


class GeneralSupervisorDashboardView(
    LoginRequiredMixin, UserPassesTestMixin, TemplateView
):
    template_name = "participants/general_supervisor_dashboard.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["navbar_items"] = build_navbar(self.request.user, "home")
        return context

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
        # Unique environment names present in the rows above, for the
        # client-side environment filter dropdown. Built from the already
        # evaluated queryset — get_queryset() itself is untouched.
        context["group_names"] = sorted(
            {p.group.name for p in context["participants"] if p.group}
        )
        context["navbar_items"] = build_navbar(self.request.user, "data")
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
        context["navbar_items"] = build_navbar(self.request.user, "tasks")
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

        context["navbar_items"] = build_navbar(self.request.user, "tasks")
        return context

    def post(self, request, *args, **kwargs):
        task = self.get_current_task()
        participant = request.user.participant

        if not task:
            return redirect("participants:task_submission")

        existing = TaskSubmission.objects.filter(
            task=task, participant=participant
        ).first()

        # One submission per participant per task. A second attempt is only
        # allowed when a supervisor has explicitly reopened this participant's
        # submission (reopened_for_resubmission=True) — every check is here in
        # the view, not only in the template, since a crafted request could
        # bypass a disabled UI button.
        if existing and not existing.reopened_for_resubmission:
            return redirect("participants:task_submission")

        # With no prior submission the normal deadline gate applies. An
        # explicit reopen overrides the deadline for THIS participant only
        # (a reopened submission always has `existing` set).
        if existing is None and task.is_past_due():
            return redirect("participants:task_submission")

        # instance=existing makes a successful save UPDATE the same row
        # (respecting the task+participant UniqueConstraint) instead of
        # trying to insert a second one. On a fresh submission existing is
        # None and this behaves exactly like the old create path.
        form = TaskSubmissionForm(
            request.POST, request.FILES, task=task, instance=existing
        )
        if form.is_valid():
            submission = form.save(commit=False)
            submission.task = task
            submission.participant = participant
            # A new upload always goes back to the review queue from scratch,
            # even if the previous decision was ACCEPTED, and consumes the
            # one-time reopen grant.
            submission.status = TaskSubmission.Status.PENDING
            submission.reviewed_by = None
            submission.reviewed_at = None
            submission.reopened_for_resubmission = False
            submission.save()
            return redirect("participants:task_submission")

        # Invalid (e.g. wrong file format) — re-render so the participant
        # actually sees the Arabic error message instead of a silent bounce.
        return self.render_to_response(self.get_context_data(form=form))


class TasksArchiveView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """
    Full archive of every WeeklyTask (not just the latest), for the general
    supervisor / superadmin. Picking a task via ?task=<id> lists, for that
    task, who submitted (with their current status) and who did not — the
    weekly task is program-wide, so "not submitted" spans every Participant.

    The one POST action is `reopen`: it flips a chosen submission's
    `reopened_for_resubmission` flag on, letting that one participant upload
    again (even past the deadline). The old file/status/dates stay untouched
    until the participant actually re-uploads.
    """

    template_name = "participants/tasks_archive.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tasks"] = WeeklyTask.objects.all()

        selected_task_id = self.request.GET.get("task")
        selected_task = None
        submitted = []
        not_submitted = []

        if selected_task_id:
            selected_task = WeeklyTask.objects.filter(id=selected_task_id).first()

        if selected_task:
            submissions_by_participant = {
                s.participant_id: s
                for s in TaskSubmission.objects.filter(
                    task=selected_task
                ).select_related("participant__user")
            }
            all_participants = Participant.objects.select_related("user").all()

            for p in all_participants:
                if p.id in submissions_by_participant:
                    submitted.append(submissions_by_participant[p.id])
                else:
                    not_submitted.append(p)

        context["selected_task"] = selected_task
        context["submitted"] = submitted
        context["not_submitted"] = not_submitted
        context["navbar_items"] = build_navbar(self.request.user, "tasks_archive")
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "reopen":
            submission_id = request.POST.get("submission_id")
            submission = TaskSubmission.objects.filter(id=submission_id).first()
            if submission:
                submission.reopened_for_resubmission = True
                submission.save(update_fields=["reopened_for_resubmission"])

        task_id = request.POST.get("task_id", "")
        return redirect(f"{reverse('participants:tasks_archive')}?task={task_id}")


# ---------------------------------------------------------------------------
# Store (products, orders, refunds)
# ---------------------------------------------------------------------------
#
# The store touches only `purchase_points`. It never calls apply_points_delta
# (which moves points/miles/purchase_points together on a fixed ratio) — every
# balance change here is a direct, isolated write to `purchase_points` alone,
# with `points` and `miles` untouched. Stock and balance are always enforced
# server-side regardless of any disabled-button hint in the UI.


class StoreView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/store.html"
    login_url = "accounts:login_participant"

    def test_func(self):
        return self.request.user.role == Role.PARTICIPANT

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        participant = self.request.user.participant

        context["participant"] = participant
        context["products"] = StoreProduct.objects.filter(stock__gt=0)
        context["my_orders"] = StoreOrder.objects.filter(
            participant=participant
        ).select_related("product")
        context["navbar_items"] = build_navbar(self.request.user, "store")
        return context

    def post(self, request, *args, **kwargs):
        participant = request.user.participant
        product_id = request.POST.get("product_id")
        product = StoreProduct.objects.filter(id=product_id).first()

        if product is None:
            messages.error(request, "المنتج المطلوب غير موجود.")
            return redirect("participants:store")

        # Server-side enforcement — never trust the disabled-button UI hint.
        if product.stock <= 0:
            messages.error(
                request, f'نفد مخزون "{product.name}"، لا يمكن إتمام الطلب.'
            )
            return redirect("participants:store")

        if participant.purchase_points < product.price:
            messages.error(
                request,
                f'رصيدك من النقاط الشرائية لا يكفي لطلب "{product.name}".',
            )
            return redirect("participants:store")

        with transaction.atomic():
            # Re-check stock inside the transaction to guard against a race
            # between two participants ordering the last unit at nearly the
            # same time.
            product = StoreProduct.objects.select_for_update().get(id=product.id)
            if product.stock <= 0:
                messages.error(
                    request,
                    f'نفد مخزون "{product.name}" للتو، لا يمكن إتمام الطلب.',
                )
                return redirect("participants:store")

            # Re-read the participant under lock and re-check the balance so a
            # second concurrent order cannot overspend purchase_points.
            participant = Participant.objects.select_for_update().get(
                id=participant.id
            )
            if participant.purchase_points < product.price:
                messages.error(
                    request,
                    f'رصيدك من النقاط الشرائية لا يكفي لطلب "{product.name}".',
                )
                return redirect("participants:store")

            product.stock -= 1
            product.save(update_fields=["stock"])

            # purchase_points ONLY — points and miles are unrelated currencies
            # and are never touched by the store.
            participant.purchase_points -= product.price
            participant.save(update_fields=["purchase_points"])

            StoreOrder.objects.create(
                participant=participant,
                product=product,
                price_at_order=product.price,
            )

        messages.success(
            request,
            f'تم طلب "{product.name}" بنجاح! يمكنك متابعة حالته من تبويب "طلباتي".',
        )
        return redirect("participants:store")


class StoreManagementView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "participants/store_management.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pending_orders"] = StoreOrder.objects.filter(
            status=StoreOrder.Status.PENDING
        ).select_related("participant__user", "product")
        context["all_orders"] = StoreOrder.objects.all().select_related(
            "participant__user", "product"
        )

        # Product management section (added on top of the orders view). The
        # setdefault calls mirror SupervisorDashboardView: post() can hand an
        # errored form / an in-progress edit target / a delete error message
        # back through **kwargs and it must not be clobbered by a fresh one.
        context["products"] = StoreProduct.objects.all()
        context.setdefault("product_form", StoreProductForm())
        context.setdefault("editing_product", None)
        context.setdefault("delete_error", None)

        # GET ?edit=<id> puts the section into edit mode: the form is
        # pre-filled with that product's data. Only honoured when post()
        # hasn't already supplied its own editing_product via **kwargs.
        edit_id = self.request.GET.get("edit")
        if edit_id and "editing_product" not in kwargs:
            editing_product = StoreProduct.objects.filter(id=edit_id).first()
            if editing_product:
                context["editing_product"] = editing_product
                context["product_form"] = StoreProductForm(instance=editing_product)

        context["navbar_items"] = build_navbar(self.request.user, "store_management")
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")

        # --- Product management actions (add / edit / delete) ---------------
        # These are wholly independent of the complete/refund order handling
        # below; an early return keeps the two paths from ever overlapping.
        if action == "add_product":
            form = StoreProductForm(request.POST, request.FILES)
            if form.is_valid():
                form.save()
                return redirect("participants:store_management")
            context = self.get_context_data(product_form=form)
            return self.render_to_response(context)

        if action == "edit_product":
            product = StoreProduct.objects.filter(
                id=request.POST.get("product_id")
            ).first()
            if product is None:
                return redirect("participants:store_management")
            form = StoreProductForm(request.POST, request.FILES, instance=product)
            if form.is_valid():
                form.save()
                return redirect("participants:store_management")
            context = self.get_context_data(
                product_form=form, editing_product=product
            )
            return self.render_to_response(context)

        if action == "delete_product":
            product = StoreProduct.objects.filter(
                id=request.POST.get("product_id")
            ).first()
            if product is not None:
                try:
                    product.delete()
                except ProtectedError:
                    # StoreOrder.product is on_delete=PROTECT — a product with
                    # existing orders cannot be deleted. Surface a clear Arabic
                    # message instead of a 500 error page.
                    context = self.get_context_data(
                        delete_error=(
                            "لا يمكن حذف هذا المنتج لوجود طلبات مرتبطة به. "
                            "يمكنك تصفير المخزون بدلًا من ذلك."
                        )
                    )
                    return self.render_to_response(context)
            return redirect("participants:store_management")

        # --- Existing order actions (unchanged) ----------------------------
        order_id = request.POST.get("order_id")
        order = StoreOrder.objects.filter(id=order_id).first()

        if order is None:
            return redirect("participants:store_management")

        if action == "complete" and order.status == StoreOrder.Status.PENDING:
            order.status = StoreOrder.Status.COMPLETED
            order.completed_at = timezone.now()
            order.save(update_fields=["status", "completed_at"])

        elif action == "refund" and order.status != StoreOrder.Status.REFUNDED:
            # Refund is allowed from BOTH pending and completed (a participant
            # may have already received the item before an error surfaced),
            # but the status != REFUNDED guard blocks a double refund — a
            # second click never re-credits points or re-adds stock.
            with transaction.atomic():
                order = StoreOrder.objects.select_for_update().get(id=order.id)
                if order.status == StoreOrder.Status.REFUNDED:
                    return redirect("participants:store_management")

                order.status = StoreOrder.Status.REFUNDED
                order.refunded_at = timezone.now()
                order.save(update_fields=["status", "refunded_at"])

                # Refund restores BOTH the participant's purchase_points and
                # the product's stock — purchase_points only, never points or
                # miles (unrelated currencies).
                participant = Participant.objects.select_for_update().get(
                    id=order.participant_id
                )
                participant.purchase_points += order.price_at_order
                participant.save(update_fields=["purchase_points"])

                product = StoreProduct.objects.select_for_update().get(
                    id=order.product_id
                )
                product.stock += 1
                product.save(update_fields=["stock"])

        return redirect("participants:store_management")
