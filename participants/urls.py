from django.urls import path

from . import views

app_name = "participants"

urlpatterns = [
    path("dashboard/", views.ParticipantDashboardView.as_view(), name="dashboard"),
    path("supervisor/dashboard/", views.SupervisorDashboardView.as_view(), name="supervisor_dashboard"),
    path("quran-circle/", views.QuranCircleAttendanceView.as_view(), name="quran_circle_attendance"),
    path("import/", views.ParticipantImportView.as_view(), name="import_participants"),
    path("general-supervisor/dashboard/", views.GeneralSupervisorDashboardView.as_view(), name="general_supervisor_dashboard"),
    path("points-snapshots/", views.PointsSnapshotHistoryView.as_view(), name="points_snapshot_history"),
    path("extra-points/", views.ExtraPointsView.as_view(), name="extra_points"),
    path("points-ledger/", views.PointsLedgerView.as_view(), name="points_ledger"),
    path("data/", views.ParticipantsDataView.as_view(), name="participants_data"),
    path("data/export-pdf/", views.ParticipantsDataPDFExportView.as_view(), name="participants_data_pdf"),
    path("tasks/review/", views.WeeklyTaskReviewView.as_view(), name="weekly_task_review"),
    path("tasks/archive/", views.TasksArchiveView.as_view(), name="tasks_archive"),
    path("tasks/submit/", views.TaskSubmissionView.as_view(), name="task_submission"),
    path("store/", views.StoreView.as_view(), name="store"),
    path("store/management/", views.StoreManagementView.as_view(), name="store_management"),
]
