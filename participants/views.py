from django.shortcuts import render

# Create your views here.

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Max, Min
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from accounts.models import Role
from .forms import CircleAttendanceForm, MeetingAttendanceForm
from .models import CircleAttendance, MeetingAttendance, Participant

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
    template_name = "participants/supervisor_dashboard.html"
    login_url = "accounts:login_supervisor"

    def test_func(self):
        return self.request.user.role == Role.GROUP_SUPERVISOR

    def get_group(self):
        # A GROUP_SUPERVISOR is linked to their Group via Group.supervisor.
        # Group.supervisor is an unnamed ForeignKey, so the reverse accessor
        # from User is Django's default "group_set".
        return self.request.user.group_set.first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.get_group()

        context["group"] = group
        context["participants"] = (
            Participant.objects.filter(group=group).select_related("user")
            if group
            else Participant.objects.none()
        )
        # setdefault so that post() can inject an already-bound form (with its
        # errors) via **kwargs while the other, untouched form is still built
        # blank here — it was never submitted, so it must not appear "reset".
        context.setdefault("circle_form", CircleAttendanceForm())
        context.setdefault("meeting_form", MeetingAttendanceForm())

        return context

    def post(self, request, *args, **kwargs):
        group = self.get_group()
        participant_ids = set(
            Participant.objects.filter(group=group).values_list("id", flat=True)
        ) if group else set()

        form_type = request.POST.get("form_type")

        if form_type == "circle":
            return self._handle_circle(request, participant_ids)
        elif form_type == "meeting":
            return self._handle_meeting(request, participant_ids)

        return redirect("participants:supervisor_dashboard")

    def _handle_circle(self, request, participant_ids):
        # Look up whether a record already exists for this participant+date
        # BEFORE binding the new form data, so we can compute the points delta
        # (correction) instead of re-awarding from scratch.
        participant_id = request.POST.get("participant")
        date = request.POST.get("date")

        existing = None
        if participant_id and date:
            existing = CircleAttendance.objects.filter(
                participant_id=participant_id, date=date
            ).first()

        form = CircleAttendanceForm(request.POST, instance=existing)

        if not form.is_valid():
            context = self.get_context_data(circle_form=form)
            return self.render_to_response(context)

        participant = form.cleaned_data["participant"]
        if participant.id not in participant_ids:
            # Security check: a supervisor must not be able to record
            # attendance for a participant outside their own group, even
            # if they manually crafted the request.
            return redirect("participants:supervisor_dashboard")

        old_points = circle_attendance_points(existing.attended) if existing else 0
        new_points = circle_attendance_points(form.cleaned_data["attended"])

        attendance = form.save(commit=False)
        attendance.recorded_by = request.user
        attendance.save()

        delta = new_points - old_points
        if delta != 0:
            apply_points_delta(participant, delta)

        return redirect("participants:supervisor_dashboard")

    def _handle_meeting(self, request, participant_ids):
        participant_id = request.POST.get("participant")
        week_start_date = request.POST.get("week_start_date")

        existing = None
        if participant_id and week_start_date:
            existing = MeetingAttendance.objects.filter(
                participant_id=participant_id, week_start_date=week_start_date
            ).first()

        form = MeetingAttendanceForm(request.POST, instance=existing)

        if not form.is_valid():
            context = self.get_context_data(meeting_form=form)
            return self.render_to_response(context)

        participant = form.cleaned_data["participant"]
        if participant.id not in participant_ids:
            # Security check: a supervisor must not be able to record
            # attendance for a participant outside their own group, even
            # if they manually crafted the request.
            return redirect("participants:supervisor_dashboard")

        old_points = (
            meeting_attendance_points(existing.attended, existing.is_early)
            if existing
            else 0
        )
        new_points = meeting_attendance_points(
            form.cleaned_data["attended"], form.cleaned_data["is_early"]
        )

        attendance = form.save(commit=False)
        attendance.recorded_by = request.user
        attendance.save()

        delta = new_points - old_points
        if delta != 0:
            apply_points_delta(participant, delta)

        return redirect("participants:supervisor_dashboard")
