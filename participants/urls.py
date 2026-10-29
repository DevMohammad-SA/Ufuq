from django.urls import path

from . import views
from .report_views import StudentReportView
from .permission_views import TaskReviewPermissionsView
from .student_views import StudentDetailView
from .management_views import (
    ParticipantEditView, SupervisorListView, SupervisorFormView, SupervisorDeleteView,
)

app_name = "participants"

urlpatterns = [
    path("dashboard/", views.ParticipantDashboardView.as_view(), name="dashboard"),
    path("supervisor/dashboard/", views.SupervisorDashboardView.as_view(), name="supervisor_dashboard"),
    path("quran-circle/", views.QuranCircleAttendanceView.as_view(), name="quran_circle_attendance"),
    path("weekly-activity/", views.WeeklyActivityAttendanceView.as_view(), name="weekly_activity_attendance"),
    path("import/", views.ParticipantImportView.as_view(), name="import_participants"),
    path("add-participant/", views.AddParticipantView.as_view(), name="add_participant"),
    path("general-supervisor/dashboard/", views.GeneralSupervisorDashboardView.as_view(), name="general_supervisor_dashboard"),
    path("points-snapshots/", views.PointsSnapshotHistoryView.as_view(), name="points_snapshot_history"),
    path("extra-points/", views.ExtraPointsView.as_view(), name="extra_points"),
    path("points-ledger/", views.PointsLedgerView.as_view(), name="points_ledger"),
    path("data/", views.ParticipantsDataView.as_view(), name="participants_data"),
    path("student-report/", StudentReportView.as_view(), name="student_report"),
    path("students/<int:pk>/", StudentDetailView.as_view(), name="student_detail"),
    path("students/<int:pk>/edit/", ParticipantEditView.as_view(), name="participant_edit"),
    path("supervisors/", SupervisorListView.as_view(), name="supervisor_list"),
    path("supervisors/add/", SupervisorFormView.as_view(), name="supervisor_add"),
    path("supervisors/<int:pk>/edit/", SupervisorFormView.as_view(), name="supervisor_edit"),
    path("supervisors/<int:pk>/delete/", SupervisorDeleteView.as_view(), name="supervisor_delete"),
    path("data/export-pdf/", views.ParticipantsDataPDFExportView.as_view(), name="participants_data_pdf"),
    path("tasks/review/", views.WeeklyTaskReviewView.as_view(), name="weekly_task_review"),
    path("tasks/review-permissions/", TaskReviewPermissionsView.as_view(), name="task_review_permissions"),
    path("tasks/archive/", views.TasksArchiveView.as_view(), name="tasks_archive"),
    path("tasks/submit/", views.TaskSubmissionView.as_view(), name="task_submission"),
    path("store/", views.StoreView.as_view(), name="store"),
    path("store/management/", views.StoreManagementView.as_view(), name="store_management"),
]
