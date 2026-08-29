from django.urls import path

from .views import ParticipantLoginView, SupervisorLoginView

app_name = "accounts"

urlpatterns = [
    path("login/participant/", ParticipantLoginView.as_view(), name="login_participant"),
    path("login/supervisor/", SupervisorLoginView.as_view(), name="login_supervisor"),
]
