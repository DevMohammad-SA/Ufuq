from django.urls import path

from . import views
from .views import ParticipantLoginView, RahhalLogoutView, SupervisorLoginView

app_name = "accounts"

urlpatterns = [
    path("login/participant/", ParticipantLoginView.as_view(), name="login_participant"),
    path("login/supervisor/", SupervisorLoginView.as_view(), name="login_supervisor"),
    path("logout/", RahhalLogoutView.as_view(), name="logout"),
    path("set-password/", views.SetPasswordView.as_view(), name="set_password"),
    path("forgot-password/", views.ForgotPasswordView.as_view(), name="forgot_password"),
    path("change-password/", views.SupervisorPasswordChangeView.as_view(), name="change_password"),
]
