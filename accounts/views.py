from django.contrib.auth.views import LoginView
from django.shortcuts import render

# Create your views here.

from .forms import ParticipantAuthenticationForm


class ParticipantLoginView(LoginView):
    template_name = "accounts/login_participant.html"
    form_class = ParticipantAuthenticationForm


class SupervisorLoginView(LoginView):
    template_name = "accounts/login_supervisor.html"
