from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Group, Participant
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
