from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Group, Participant, StoreProduct, TaskSubmission, WeeklyTask
# Register your models here.

@admin.register(Group)
class GroupAdmin(ModelAdmin):
    list_display = ('name','supervisor')
    search_fields = ('name',)


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
