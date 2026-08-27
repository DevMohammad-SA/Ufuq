from enum import unique

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models

# Create your models here.


class UserManager(BaseUserManager):
    """
    Custom manager for the User model — required for any model inheriting
    from AbstractBaseUser, since it doesn't come with create_user/create_superuser
    like the default User does.
    """

    def create_user(self, password=None, **extra_fields):
        if not extra_fields.get("username") and not extra_fields.get("national_id"):
            raise ValueError("User must have either a username or a national_id")

        user = self.model(**extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Role.SUPERADMIN)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(username=username, password=password, **extra_fields)


class Role(models.TextChoices):
    PARTICIPANT = "participant", "مشارك"
    GROUP_SUPERVISOR = "group_supervisor", "مشرف بيئة"
    GENERAL_SUPERVISOR = "general_supervisor", "مشرف عام"
    SUPERADMIN = "superadmin", "مشرف النظام"


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model for all four roles.

    USERNAME_FIELD is technically fixed to "username" (Django requires exactly
    one field here). The actual login check for a Participant (via national_id)
    is handled by the custom NationalIDOrUsernameBackend in backends.py,
    not by this field directly.
    """

    class Meta:
        verbose_name = "المستخدم"
        verbose_name_plural = "المستخدمين"

    national_id = models.CharField(
        max_length=10, unique=True, blank=True,null=True, verbose_name="الهوية الوطنية / الإقامة"
    )
    username = models.CharField(
        max_length=30, blank=True, unique=True, null=True, verbose_name="اسم المستخدم"
    )
    role = models.CharField(max_length=30, choices=Role.choices, verbose_name="الدور")
    full_name = models.CharField(
        max_length=100, blank=True, verbose_name="الاسم الكامل"
    )
    is_active = models.BooleanField(default=True, verbose_name="الحساب نشط؟")
    is_staff = models.BooleanField(default=False, verbose_name="حساب مشرف؟")
    date_joined = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الانضمام")
    objects = UserManager()

    USERNAME_FIELD = "username"

    def __str__(self):
        return f"{self.full_name} - {self.get_role_display()}"

    def clean(self):
        super().clean()
        if not self.username and not self.national_id:
            raise ValidationError("يجب توفر اسم مستخدم أو رقم هوية على الأقل")
