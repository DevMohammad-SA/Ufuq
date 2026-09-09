from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView

# Create your views here.

from .forms import (
    ForgotPasswordRequestForm,
    ParticipantAuthenticationForm,
    SetPasswordForm,
)
from .models import PasswordResetRequest, Role, User


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
        if role in (Role.GENERAL_SUPERVISOR, Role.SUPERADMIN):
            return reverse("participants:general_supervisor_dashboard")
        return reverse("home")


class RahhalLogoutView(LogoutView):
    next_page = "home"


class SetPasswordView(LoginRequiredMixin, FormView):
    template_name = "accounts/set_password.html"
    form_class = SetPasswordForm
    login_url = "accounts:login_participant"

    def dispatch(self, request, *args, **kwargs):
        # If the user doesn't actually need to set a password, there's
        # nothing for this page to do — send them to their normal landing
        # page instead of showing a pointless form.
        if request.user.is_authenticated and not request.user.must_set_password:
            return redirect("participants:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = self.request.user
        user.set_password(form.cleaned_data["new_password1"])
        user.must_set_password = False
        user.save(update_fields=["password", "must_set_password"])
        # Re-authenticate the session with the new password hash so the
        # user isn't logged out by Django's session auth hash check.
        update_session_auth_hash(self.request, user)
        return redirect("participants:dashboard")


class ForgotPasswordView(FormView):
    template_name = "accounts/forgot_password.html"
    form_class = ForgotPasswordRequestForm
    success_url = reverse_lazy("accounts:login_participant")

    def form_valid(self, form):
        national_id = form.cleaned_data["national_id"]
        user = User.objects.filter(
            national_id=national_id, role=Role.PARTICIPANT
        ).first()
        if user:
            PasswordResetRequest.objects.create(user=user)
        # Always show the same success message whether or not a matching
        # user was found — this avoids revealing which national_ids are
        # registered in the system to an anonymous visitor.
        messages.success(
            self.request,
            "تم إرسال طلبك. سيتواصل معك المشرف بعد المراجعة.",
        )
        return super().form_valid(form)
