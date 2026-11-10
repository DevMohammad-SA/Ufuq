from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_must_set_password_passwordresetrequest"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("participant", "مشارك"),
                    ("group_supervisor", "مشرف بيئة"),
                    ("limited_supervisor", "مشرف بصلاحيات محددة"),
                    ("general_supervisor", "مشرف عام"),
                    ("superadmin", "مشرف النظام"),
                ],
                max_length=30,
                verbose_name="الدور",
            ),
        ),
    ]
