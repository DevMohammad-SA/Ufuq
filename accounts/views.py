from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import render
from django.urls import reverse

# Create your views here.

from .forms import ParticipantAuthenticationForm
from .models import Role


class ParticipantLoginView(LoginView):
    template_name = "accounts/login_participant.html"
    form_class = ParticipantAuthenticationForm

    def get_success_url(self):
        return reverse("participants:dashboard")


class SupervisorLoginView(LoginView):
    template_name = "accounts/login_supervisor.html"

    def get_success_url(self):
        role = self.request.user.role
        if role == Role.GROUP_SUPERVISOR:
            return reverse("participants:supervisor_dashboard")
        return reverse("home")


class RahhalLogoutView(LogoutView):
    next_page = "home"
