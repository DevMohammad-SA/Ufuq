from django.conf import settings
from django.db import models
from accounts.models import Role


# Create your models here.
class AcademicStage(models.TextChoices):
    GRADE_5 = "grade_5", "خامس ابتدائي"
    GRADE_6 = "grade_6", "سادس ابتدائي"
    GRADE_7 = "grade_7", "أول متوسط"
    GRADE_8 = "grade_8", "ثاني متوسط"
    GRADE_9 = "grade_9", "ثالث متوسط"

class Group(models.Model):
    """
    Represents one of the 3 "بيئات" (environments) in the Horizon program.
    Each environment has ~35 participants and one Group Supervisor.
    """

    class Meta:
        verbose_name = "بيئة"
        verbose_name_plural = "البيئات"

    name = models.CharField(max_length=50,unique=True,verbose_name="اسم البيئة")
    supervisor = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.SET_NULL,
                                   null=True,
                                   limit_choices_to={"role":Role.GROUP_SUPERVISOR},
                                   verbose_name="المشرف")

    def __str__(self):
        return f"{self.name}"

class Participant(models.Model):
    """
    Program-specific data for a user whose role is PARTICIPANT.
    Kept separate from User (accounts app) so that accounts stays focused
    purely on identity/authentication, while this model owns program data:
    group membership, the triple-currency rewards, and contact info.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    group = models.ForeignKey(Group,on_delete=models.SET_NULL,null=True,related_name="participants",verbose_name="البيئة")
    miles = models.PositiveIntegerField(default=0,verbose_name="الأميال")
    points = models.PositiveIntegerField(default=0,verbose_name="النقاط")
    purchase_points = models.PositiveIntegerField(default=0,verbose_name="النقاط الشرائية")
    phone = models.CharField(max_length=20,null=True,blank=True,verbose_name="رقم الجوال")
    guardian_phone = models.CharField(max_length=20,null=True,blank=True,verbose_name="رقم جوال ولي الأمر")
    academic_stage = models.CharField(
        max_length=10,
        choices=AcademicStage.choices,
        verbose_name="المرحلة الدراسية"

    )

    class Meta:
        verbose_name = "مشارك"
        verbose_name_plural = "المشاركون"

    def __str__(self):
        group_name = self.group.name if self.group else "بدون بيئة"
        return f"{self.user.full_name} - {group_name}"


class CircleAttendance(models.Model):
    """
    Daily Quran circle attendance record. One record per participant per day
    the circle meets (up to 5 records/week per participant, per the program's
    weekly points table: 15 points total = 3 points/day).

    Converting attended records into actual points/miles is handled by
    separate logic OUTSIDE this model (not automatically on save()) — this
    model's only job is to record what happened on a given day.
    """

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="circle_attendances",
        verbose_name="المشارك",
    )
    date = models.DateField(verbose_name="التاريخ")
    attended = models.BooleanField(default=False, verbose_name="حضر؟")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        limit_choices_to={"role": Role.GROUP_SUPERVISOR},
        verbose_name="سجّله",
    )

    class Meta:
        verbose_name = "حضور حلقة"
        verbose_name_plural = "حضور الحلقات"
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "date"],
                name="unique_circle_attendance_per_day",
            )
        ]

    def __str__(self):
        return f"{self.participant.user.full_name} - {self.date}"


class MeetingAttendance(models.Model):
    """
    Weekly gathering ("اللقاء الأسبوعي") attendance record. One record per
    participant per week. Full attendance = 8 points, early arrival = extra
    2 points (per the program's weekly points table).
    """

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="meeting_attendances",
        verbose_name="المشارك",
    )
    week_start_date = models.DateField(verbose_name="بداية الأسبوع")
    attended = models.BooleanField(default=False, verbose_name="حضر؟")
    is_early = models.BooleanField(default=False, verbose_name="حضور مبكر؟")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        limit_choices_to={"role": Role.GROUP_SUPERVISOR},
        verbose_name="سجّله",
    )

    class Meta:
        verbose_name = "حضور لقاء"
        verbose_name_plural = "حضور اللقاءات"
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "week_start_date"],
                name="unique_meeting_attendance_per_week",
            )
        ]

    def __str__(self):
        return f"{self.participant.user.full_name} - {self.week_start_date}"