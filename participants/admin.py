from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import (
    Group,
    Participant,
    PointsResetSnapshot,
    StoreProduct,
    TaskSubmission,
    WeeklyActivityAttendance,
    WeeklyTask,
)
# Register your models here.

@admin.register(Group)
class GroupAdmin(ModelAdmin):
    list_display = ('name','get_supervisors')
    search_fields = ('name',)
    # supervisor is now a ManyToManyField — filter_horizontal gives the
    # dual-list picker for assigning several supervisors to one environment.
    filter_horizontal = ('supervisor',)

    @admin.display(description="المشرفون")
    def get_supervisors(self, obj):
        return "، ".join(s.full_name for s in obj.supervisor.all()) or "—"


@admin.register(Participant)
class ParticipantAdmin(ModelAdmin):
    list_display = ('get_full_name','phone','group')
    search_fields = ('user__full_name','phone','group__name')
    list_filter=("group","academic_stage")

    @admin.display(description="الاسم الكامل")
    def get_full_name(self,obj):
        return obj.user.full_name


@admin.register(WeeklyTask)
class WeeklyTaskAdmin(ModelAdmin):
    list_display = ["title", "due_date", "created_at"]
    list_filter = ["due_date"]


@admin.register(TaskSubmission)
class TaskSubmissionAdmin(ModelAdmin):
    list_display = ["participant", "task", "status", "submitted_at"]
    list_filter = ["status", "task"]


@admin.register(StoreProduct)
class StoreProductAdmin(ModelAdmin):
    list_display = ["name", "price", "stock"]
    list_filter = ["stock"]
    search_fields = ["name"]


@admin.register(WeeklyActivityAttendance)
class WeeklyActivityAttendanceAdmin(ModelAdmin):
    list_display = ["participant", "date", "attended", "recorded_by"]
    list_filter = ["date", "attended"]
    search_fields = ["participant__user__full_name"]


@admin.register(PointsResetSnapshot)
class PointsResetSnapshotAdmin(ModelAdmin):
    list_display = ["participant", "points_before_reset", "reset_at", "reset_by"]
    list_filter = ["reset_at"]
    search_fields = ["participant__user__full_name"]
    readonly_fields = ["participant", "points_before_reset", "reset_at", "reset_by"]

    # Automatic historical data — never created or edited by hand, only viewed.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
