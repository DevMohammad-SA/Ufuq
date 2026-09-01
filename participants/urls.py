from django.urls import path

from . import views

app_name = "participants"

urlpatterns = [
    path("dashboard/", views.ParticipantDashboardView.as_view(), name="dashboard"),
    path("supervisor/dashboard/", views.SupervisorDashboardView.as_view(), name="supervisor_dashboard"),
    path("import/", views.ParticipantImportView.as_view(), name="import_participants"),
    path("general-supervisor/dashboard/", views.GeneralSupervisorDashboardView.as_view(), name="general_supervisor_dashboard"),
    path("data/", views.ParticipantsDataView.as_view(), name="participants_data"),
    path("tasks/review/", views.WeeklyTaskReviewView.as_view(), name="weekly_task_review"),
    path("tasks/submit/", views.TaskSubmissionView.as_view(), name="task_submission"),
]
