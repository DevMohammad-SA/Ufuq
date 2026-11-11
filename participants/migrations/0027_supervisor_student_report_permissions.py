import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("participants", "0026_limited_task_grading_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="supervisorcapability",
            name="manage_students",
            field=models.BooleanField(default=False, verbose_name="إدارة الطلاب"),
        ),
        migrations.CreateModel(
            name="SupervisorReportPermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("view_reports", models.BooleanField(default=False, verbose_name="الاطلاع على التقارير")),
                ("prepare_reports", models.BooleanField(default=False, verbose_name="إعداد وتصدير التقارير")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")),
                ("supervisor", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="report_permissions", to=settings.AUTH_USER_MODEL, verbose_name="المشرف")),
                ("granted_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="حدّث أذونات التقارير")),
            ],
            options={
                "verbose_name": "إذن تقارير مشرف",
                "verbose_name_plural": "أذونات تقارير المشرفين",
            },
        ),
    ]
