import datetime

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
        ordering = ["user__full_name"]

    def __str__(self):
        group_name = self.group.name if self.group else "بدون بيئة"
        return f"{self.user.full_name} - {group_name}"


class CircleAttendance(models.Model):
    """
    Daily Quran circle attendance record. One record per participant per day
    the circle meets (up to 5 records/week per participant, per the program's
    weekly points table: 15 points total = 3 points/day).

    This model itself has no overridden save() or signals — it only records
    what happened on a given day. Converting attendance into actual
    points/miles/purchase_points happens explicitly in participants/views.py
    (see apply_points_delta and circle_attendance_points), triggered when a
    supervisor submits the attendance form, not automatically whenever this
    model is saved through any other code path (e.g. the admin or a shell).
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


class WeeklyTask(models.Model):
    """
    A single week's assigned task for the whole program (not per-group).
    A NEW record is created each week by the General Supervisor — old
    records are never edited in place, they remain as historical archive.
    """

    class AllowedFormat(models.TextChoices):
        PDF = "pdf", "ملف PDF"
        IMAGE = "image", "صورة"
        AUDIO = "audio", "مقطع صوتي"
        VIDEO = "video", "مقطع فيديو"

    title = models.CharField(max_length=200, verbose_name="عنوان المهمة")
    description = models.TextField(verbose_name="وصف المهمة")
    due_date = models.DateField(verbose_name="موعد التسليم")
    # Which single format this specific task accepts.
    allowed_formats = models.CharField(
        max_length=10,
        choices=AllowedFormat.choices,
        verbose_name="الصيغة المسموحة",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="أنشأها",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")

    class Meta:
        verbose_name = "مهمة أسبوعية"
        verbose_name_plural = "المهام الأسبوعية"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.due_date})"

    def is_past_due(self):
        return datetime.date.today() > self.due_date


class TaskSubmission(models.Model):
    """
    A participant's file submission for a specific WeeklyTask.

    Status is either PENDING (awaiting review) or a final decision
    (ACCEPTED/REJECTED) made by the General Supervisor. There is no
    partial grading — acceptance awards the full 10 points, rejection
    awards 0. Points are NOT applied automatically by this model's save()
    — the review view applies them explicitly and exactly once per
    decision, mirroring the same pattern used for attendance points.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "قيد المراجعة"
        ACCEPTED = "accepted", "مقبولة"
        REJECTED = "rejected", "مرفوضة"

    task = models.ForeignKey(
        WeeklyTask,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="المهمة",
    )
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="task_submissions",
        verbose_name="المشارك",
    )
    file = models.FileField(upload_to="task_submissions/%Y/%W/", verbose_name="الملف")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="الحالة",
    )
    reopened_for_resubmission = models.BooleanField(
        default=False,
        verbose_name="أُتيح للتسليم مجددًا",
    )
    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الرفع")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="راجعها",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ المراجعة")

    class Meta:
        verbose_name = "تسليم مهمة"
        verbose_name_plural = "تسليمات المهام"
        constraints = [
            models.UniqueConstraint(
                fields=["task", "participant"],
                name="unique_submission_per_task_per_participant",
            )
        ]
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.participant.user.full_name} - {self.task.title} - {self.get_status_display()}"


class StoreProduct(models.Model):
    """
    A store item participants can purchase with purchase_points.
    Managed entirely through the Django admin (Unfold) given the tight
    timeline — no custom management UI beyond what's needed for orders.
    """

    name = models.CharField(max_length=150, verbose_name="اسم المنتج")
    description = models.TextField(blank=True, verbose_name="الوصف")
    image = models.ImageField(
        upload_to="store_products/", blank=True, null=True, verbose_name="الصورة"
    )
    price = models.PositiveIntegerField(verbose_name="السعر (نقاط شرائية)")
    stock = models.PositiveIntegerField(default=0, verbose_name="الكمية المتوفرة")

    class Meta:
        verbose_name = "منتج"
        verbose_name_plural = "المنتجات"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def is_available(self):
        return self.stock > 0


class StoreOrder(models.Model):
    """
    A single participant's order for exactly one unit of one product.
    price_at_order snapshots StoreProduct.price at order time, so later
    price changes never retroactively affect an existing order (same
    snapshot philosophy used elsewhere in the project, e.g. attendance
    points-at-grant time).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "قيد التنفيذ"
        COMPLETED = "completed", "مكتمل"
        REFUNDED = "refunded", "مسترجَع"

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="store_orders",
        verbose_name="المشارك",
    )
    product = models.ForeignKey(
        StoreProduct,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="المنتج",
    )
    price_at_order = models.PositiveIntegerField(verbose_name="السعر وقت الطلب")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="الحالة",
    )
    ordered_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الطلب")
    completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="تاريخ الاكتمال"
    )
    refunded_at = models.DateTimeField(
        null=True, blank=True, verbose_name="تاريخ الاسترجاع"
    )

    class Meta:
        verbose_name = "طلب"
        verbose_name_plural = "الطلبات"
        ordering = ["-ordered_at"]

    def __str__(self):
        return f"{self.participant.user.full_name} - {self.product.name} ({self.get_status_display()})"