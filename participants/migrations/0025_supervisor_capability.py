import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_alter_user_role"),
        ("participants", "0024_task_grade_revision"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupervisorCapability",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("manage_store", models.BooleanField(default=False, verbose_name="إدارة المتجر")),
                ("follow_tasks", models.BooleanField(default=False, verbose_name="متابعة المهام للقراءة فقط")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")),
                ("supervisor", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="limited_capabilities", to=settings.AUTH_USER_MODEL, verbose_name="المشرف")),
                ("granted_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="حدّث الصلاحيات")),
            ],
            options={
                "verbose_name": "صلاحيات مشرف محددة",
                "verbose_name_plural": "صلاحيات المشرفين المحددة",
            },
        ),
    ]
