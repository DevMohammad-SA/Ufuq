
from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import User
from .forms import UserCreationForm, UserChangeForm


@admin.register(User)
class UserAdmin(ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    list_display = ["username", "full_name" ,"role"]
    list_filter = [ "role"]
    search_fields = ["username", "full_name", "national_id"]
    fieldsets = (
        ("معلومات الدخول", {"fields": ("username", "national_id", "password", "new_password")}),
        ("المعلومات الشخصية", {"fields": ("full_name", "role")}),
        ("الصلاحيات", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        ("معلومات الدخول",{"fields": ("username", "password1","password2",)}),
        ("المعلومات الشخصية",{"fields": ("national_id", "full_name")}),
        ("الصلاحيات",{"fields":("role","is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, **kwargs)