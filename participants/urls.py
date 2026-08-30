from django.urls import path

from . import views

app_name = "participants"

urlpatterns = [
    path("dashboard/", views.ParticipantDashboardView.as_view(), name="dashboard"),
    path("supervisor/dashboard/", views.SupervisorDashboardView.as_view(), name="supervisor_dashboard"),
    path("import/", views.ParticipantImportView.as_view(), name="import_participants"),
]
