from django.shortcuts import render

# Create your views here.

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max, Min
from django.views.generic import TemplateView

from .models import Participant


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
