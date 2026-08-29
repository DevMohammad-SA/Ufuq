from django.urls import path

from . import views

app_name = "participants"

urlpatterns = [
    path("dashboard/", views.ParticipantDashboardView.as_view(), name="dashboard"),
]
