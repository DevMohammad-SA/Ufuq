import datetime
import io

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from PIL import Image

from accounts.models import Role

# Uploaded images are downscaled to fit within this box (px) and re-encoded as
# JPEG at this quality on save. Images are never rejected for size — they are
# compressed instead (see compress_image_field and the save() overrides below).
MAX_IMAGE_DIMENSION = 1600
IMAGE_QUALITY = 80


def compress_image_field(image_field):
    """
    Resizes an uploaded image to fit within MAX_IMAGE_DIMENSION x
    MAX_IMAGE_DIMENSION (preserving aspect ratio, never upscaling smaller
    images), re-encodes it as JPEG at IMAGE_QUALITY, and returns a new
    ContentFile ready to replace the original field's content. Called
    explicitly from save() on models with an ImageField that needs this —
    never runs automatically via a signal, keeping the behavior visible and
    easy to trace from each model's own save() method.

    Returns None when there is nothing to do.
    """
    if not image_field:
        return None

    image_field.seek(0)
    img = Image.open(image_field)
    # JPEG has no alpha channel: flatten anything with transparency (RGBA, LA,
    # palette-with-transparency) onto a white background rather than letting
    # Pillow pick an arbitrary fill. Opaque images convert straight to RGB.
    if img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    ):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=IMAGE_QUALITY)
    buffer.seek(0)

    original_name = image_field.name.rsplit(".", 1)[0]
    return ContentFile(buffer.read(), name=f"{original_name}.jpg")


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
    supervisor = models.ManyToManyField(settings.AUTH_USER_MODEL,
                                        blank=True,
                                        limit_choices_to={"role":Role.GROUP_SUPERVISOR},
                                        verbose_name="المشرفون")

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
    achieved = models.BooleanField(default=False, verbose_name="إنجاز؟")
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
    is_featured = models.BooleanField(default=False, verbose_name="مميزة")
    rejection_reason = models.TextField(
        blank=True,
        verbose_name="سبب الرفض",
        help_text="يظهر للمشارك عند رفض تسليمه",
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

    def save(self, *args, **kwargs):
        # Compress ONLY a freshly uploaded image, and only for image tasks.
        # `_committed` is False exactly when `self.file` holds a new upload
        # that has not been written to storage yet; a file loaded back from
        # the database is already committed, so later saves (accept/reject,
        # reopen) never re-encode it and cause JPEG generation loss.
        # `self.task_id` avoids a needless task query when it is unset.
        if (
            self.task_id
            and self.file
            and not self.file._committed
            and self.task.allowed_formats == "image"
        ):
            compressed = compress_image_field(self.file)
            if compressed:
                self.file = compressed
        super().save(*args, **kwargs)


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

    def save(self, *args, **kwargs):
        # Compress the image only when it is a new upload. `_committed` is
        # False for a just-assigned upload and True for a value loaded from
        # the database, so a plain field edit (e.g. changing price/stock via
        # StoreProductForm without re-picking the image) re-saves the row
        # without ever re-encoding the stored image.
        if self.image and not self.image._committed:
            compressed = compress_image_field(self.image)
            if compressed:
                self.image = compressed
        super().save(*args, **kwargs)


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


class PointsLedgerEntry(models.Model):
    """
    Central audit log for every points grant/deduction that flows through
    apply_points_delta, regardless of source (attendance, tasks, Quran
    circle, or manual extra points). Store purchases/refunds are
    deliberately NOT logged here — they're a separate currency movement
    (purchase_points only) tracked via StoreOrder itself.
    """

    class Source(models.TextChoices):
        MEETING_ATTENDANCE = "meeting_attendance", "حضور اللقاء الأسبوعي"
        QURAN_CIRCLE = "quran_circle", "الحلقة القرآنية"
        WEEKLY_TASK = "weekly_task", "المهمة الأسبوعية"
        EXTRA = "extra", "نقاط إضافية"

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="points_ledger_entries",
        verbose_name="المشارك",
    )
    # Signed on purpose (not PositiveIntegerField) — a correction (e.g. an
    # attendance edit that lowers a previous grant) or a manual deduction
    # must be representable as a negative delta.
    points_delta = models.IntegerField(verbose_name="التغيير في النقاط")
    source = models.CharField(max_length=20, choices=Source.choices, verbose_name="المصدر")
    description = models.CharField(max_length=255, verbose_name="الوصف")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="التاريخ")
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name="مُنِح بواسطة",
    )

    class Meta:
        verbose_name = "سجل نقاط"
        verbose_name_plural = "سجل النقاط"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.participant.user.full_name} - {self.points_delta:+d} - {self.description}"


class PointsResetSnapshot(models.Model):
    """
    A single participant's points value captured at the exact moment of a
    program-wide points reset, BEFORE the reset zeroes it out. One row per
    participant per reset event (not one row per event) — this makes
    querying "who had the most points during period X" a plain filter +
    order_by, with no need to unpack a JSON blob.

    Every row created by one reset click shares the same reset_at timestamp
    down to the microsecond (all inserted in a single bulk_create call), so
    the history view groups rows into "reset events" by truncating reset_at
    to the second — precise enough in practice with no separate batch-id
    column.
    """

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="points_snapshots",
        verbose_name="المشارك",
    )
    points_before_reset = models.PositiveIntegerField(verbose_name="النقاط قبل التصفير")
    reset_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ التصفير")
    reset_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name="نفّذه",
    )

    class Meta:
        verbose_name = "لقطة نقاط قبل التصفير"
        verbose_name_plural = "لقطات نقاط قبل التصفير"
        ordering = ["-reset_at", "-points_before_reset"]

    def __str__(self):
        return f"{self.participant.user.full_name} - {self.points_before_reset} ({self.reset_at:%Y-%m-%d %H:%M})"