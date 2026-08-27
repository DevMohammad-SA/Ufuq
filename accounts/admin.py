
from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import User


@admin.register(User)
class useradmin(ModelAdmin):
    list_display = ["username", "full_name" ,"role"]
    list_filter = [ "role"]
    search_fields = ["username", "full_name", "national_id"]