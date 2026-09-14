# الموديلات

> مستخرج حقلًا حقلًا من `accounts/models.py` و`participants/models.py` (تحقّق مباشر
> من الكود الفعلي، لا من نسخة سابقة). المراجع أدناه بصيغة "اسم الدالة/الكلاس"
> بدل رقم السطر، لأن أرقام الأسطر تتغيّر مع كل تعديل بينما الأسماء لا تتغيّر إلا
> عمدًا. `verbose_name` العربي بين قوسين حيث يفيد.

## تطبيق `accounts`

### `User` — `accounts/models.py`, كلاس `User`

يرث `AbstractBaseUser` + `PermissionsMixin`. `USERNAME_FIELD = "username"`.
مديره `UserManager` المخصص (`create_user` / `create_superuser`).

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `national_id` | `CharField(max_length=10, unique=True, blank=True, null=True)` | الهوية الوطنية / الإقامة. مفتاح دخول المشارك. |
| `username` | `CharField(max_length=30, unique=True, blank=True, null=True)` | اسم المستخدم. مفتاح دخول المشرفين. |
| `role` | `CharField(max_length=30, choices=Role.choices)` | الدور. لا قيمة افتراضية. |
| `full_name` | `CharField(max_length=100, blank=True)` | الاسم الكامل. |
| `is_active` | `BooleanField(default=True)` | يُفحص في الـ backend. |
| `is_staff` | `BooleanField(default=False)` | الوصول لواجهة أدمن Django. |
| `must_set_password` | `BooleanField(default=False)` | إذا `True`: المشارك مُجبَر على صفحة تعيين كلمة مرور جديدة. راجع [`authentication.md`](authentication.md). |
| `date_joined` | `DateTimeField(auto_now_add=True)` | — |
| `+ PermissionsMixin` | `is_superuser`, `groups`, `user_permissions` | من Django. |

دالة `User.clean()` ترفض المستخدم بلا `username` **و**بلا `national_id`.

### `Role` — `accounts/models.py`, كلاس `Role` (`TextChoices`)

`participant` / `group_supervisor` / `general_supervisor` / `superadmin`.
راجع [`roles-and-permissions.md`](roles-and-permissions.md).

### `PasswordResetRequest` — `accounts/models.py`, كلاس `PasswordResetRequest`

طلب استرجاع كلمة مرور من مشارك، يعالجه المشرف العام.

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `user` | `FK(AUTH_USER_MODEL, on_delete=CASCADE, related_name="password_reset_requests")` | صاحب الطلب. |
| `requested_at` | `DateTimeField(auto_now_add=True)` | — |
| `resolved` | `BooleanField(default=False)` | — |
| `resolved_at` | `DateTimeField(null=True, blank=True)` | — |
| `resolved_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, blank=True, related_name="+")` | المشرف الذي وافق. |

`Meta.ordering = ["-requested_at"]`.

---

## تطبيق `participants`

### `AcademicStage` — `participants/models.py`, كلاس `AcademicStage` (`TextChoices`)

`grade_5`=خامس ابتدائي، `grade_6`=سادس ابتدائي، `grade_7`=أول متوسط،
`grade_8`=ثاني متوسط، `grade_9`=ثالث متوسط.

### `Group` (بيئة) — `participants/models.py`, كلاس `Group`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `name` | `CharField(max_length=50, unique=True)` | اسم البيئة. المطابقة في الاستيراد تتم بالاسم الدقيق. |
| `supervisor` | `ManyToManyField(AUTH_USER_MODEL, blank=True, limit_choices_to={"role": GROUP_SUPERVISOR})` | **⚠️ تغيّر مؤخرًا من `ForeignKey` إلى `ManyToManyField`** — بيئة واحدة يمكن أن يكون لها **أكثر من مشرف بيئة**. بدون `related_name` → العلاقة العكسية من `User` لا تزال `group_set` (نفس اسم العلاقة العكسية الافتراضي لكل من `ForeignKey` و`ManyToManyField` غير المسمّاة)، لذا كود مثل `request.user.group_set.first()` يستمر بالعمل دون تعديل، لكنه الآن يُرجع **بيئة واحدة عشوائية من عدة محتملة** إن كان للمستخدم أكثر من بيئة، وليس بالضرورة "بيئته الوحيدة". أدمن Django يعرض هذا الحقل بأداة `filter_horizontal` (قائمة مزدوجة لاختيار عدة مشرفين). |

### `Participant` (مشارك) — `participants/models.py`, كلاس `Participant`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `user` | `OneToOneField(AUTH_USER_MODEL, on_delete=CASCADE)` | **بدون `related_name`** → الوصول عبر `user.participant`. |
| `group` | `FK(Group, on_delete=SET_NULL, null=True, related_name="participants")` | البيئة. |
| `miles` | `PositiveIntegerField(default=0)` | الأميال. |
| `points` | `PositiveIntegerField(default=0)` | النقاط. |
| `purchase_points` | `PositiveIntegerField(default=0)` | النقاط الشرائية. |
| `phone` | `CharField(max_length=20, null=True, blank=True)` | جوال المشارك. |
| `guardian_phone` | `CharField(max_length=20, null=True, blank=True)` | جوال ولي الأمر. |
| `academic_stage` | `CharField(max_length=10, choices=AcademicStage.choices)` | لا قيمة افتراضية. |

`Meta.ordering = ["user__full_name"]`.

> **ملاحظة على واجهة المشارك:** منذ التحديث 1.1.0 لا تُعرض بطاقة "النقاط"
> (`points`) في لوحة المشارك — تُعرض فقط "الأميال" و"النقاط الشرائية". الحقل
> `points` **لا يزال موجودًا ويُحدَّث فعليًا** في قاعدة البيانات (يُستخدم في
> ترتيب مؤشر رحلة النخبة وفي تصفير النقاط)، لكنه اختيار عرض واجهة فقط، وليس
> تغييرًا في الموديل. راجع [`points-system.md`](points-system.md).

### `CircleAttendance` (حضور حلقة) — `participants/models.py`, كلاس `CircleAttendance`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="circle_attendances")` | — |
| `date` | `DateField` | تاريخ اليوم. |
| `attended` | `BooleanField(default=False)` | حضر؟ = 3 نقاط. |
| `achieved` | `BooleanField(default=False)` | أنجز؟ = 2 نقطة. مستقل عن `attended`. |
| `recorded_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, limit_choices_to={"role": GROUP_SUPERVISOR})` | من سجّله (قد يكون مشرفًا عامًا فعليًا — القيد للأدمن فقط، لا يُفرض على مستوى الـ View أو قاعدة البيانات). |

`Meta.constraints`: `UniqueConstraint(fields=["participant", "date"], name="unique_circle_attendance_per_day")` — **سجل واحد لكل مشارك في اليوم**.

### `MeetingAttendance` (حضور لقاء) — `participants/models.py`, كلاس `MeetingAttendance`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="meeting_attendances")` | — |
| `week_start_date` | `DateField` | **اسم الحقل مضلِّل**: يُخزَّن فيه التاريخ المختار في الواجهة حرفيًا كـ "تاريخ اللقاء"، بلا أي حساب لبداية أسبوع (انظر docstring كلاس `SupervisorDashboardView`). |
| `attended` | `BooleanField(default=False)` | حضر؟ = 8 نقاط. |
| `is_early` | `BooleanField(default=False)` | حضور مبكر؟ = 2 نقطة إضافية. |
| `recorded_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, limit_choices_to={"role": GROUP_SUPERVISOR})` | — |

`Meta.constraints`: `UniqueConstraint(fields=["participant", "week_start_date"], name="unique_meeting_attendance_per_week")`.

### `WeeklyActivityAttendance` (فعالية الأسبوع) — `participants/models.py`, كلاس `WeeklyActivityAttendance`

> **جديد** منذ الإصدار 1.1.0. لا علاقة له بـ `MeetingAttendance` أو
> `CircleAttendance` — عملة نقاط ثالثة مستقلة بنمط تحضير مطابق تمامًا (كشف +
> منتقي تاريخ)، لكن بعلم حضور واحد فقط بلا أبعاد إضافية، بلا اسم أو وصف
> للفعالية نفسها (خلافًا لـ `WeeklyTask`).

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="weekly_activity_attendances")` | — |
| `date` | `DateField` | تاريخ الفعالية. |
| `attended` | `BooleanField(default=False)` | حاضر؟ = 10 نقاط (كامل، بلا تدرّج). |
| `recorded_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True)` | **بدون `limit_choices_to`** (خلافًا لـ `CircleAttendance`/`MeetingAttendance`) — منطقي لأن هذه الفعالية يسجّلها أيضًا المشرف العام/النظام، لا مشرف البيئة فقط. |

`Meta.constraints`: `UniqueConstraint(fields=["participant", "date"], name="unique_weekly_activity_per_day")`.

### صيغ تسليم المهام — `SUBMISSION_FORMAT_EXTENSIONS` (`participants/models.py`)

قاموس على مستوى الوحدة يربط قيمة `AllowedFormat` الخاصة بالملف بامتدادات الملفات
المقبولة لها. يشترك فيه `TaskSubmissionForm.clean()` (التحقق من الامتداد) و
`TaskSubmission.get_submitted_format()` (تصنيف تسليم موجود للعرض) كي لا يتباعدا:

```python
SUBMISSION_FORMAT_EXTENSIONS = {
    "pdf": (".pdf",),
    "image": (".jpg", ".jpeg", ".png"),
    "audio": (".mp3", ".wav", ".m4a"),
    "video": (".mp4", ".mov", ".webm"),
}
```

لا يوجد فيه مدخل لـ `"text"` — التسليم النصي لا ملف له إطلاقًا.

### `WeeklyTask` (مهمة أسبوعية) — `participants/models.py`, كلاس `WeeklyTask`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `title` | `CharField(max_length=200)` | — |
| `description` | `TextField` | — |
| `due_date` | `DateField` | موعد التسليم. |
| `allowed_formats` | `CharField(max_length=50)` | **⚠️ تغيّر جذريًا**: لم يعد اختيار صيغة واحدة عبر `choices=`. الآن قائمة قيَم `AllowedFormat` مفصولة بفواصل (مثل `"pdf,image"`) — يكفي المشارك تقديم **واحدة منها**. عمدًا **ليس** `choices=` (قيمة مجمّعة مثل `"pdf,text"` تفشل مدقّق Django) وعمدًا **ليس** `ManyToManyField` (لا حاجة لجدول ربط منفصل لعدد صغير من القيم النصية الثابتة). مهمة قديمة بقيمة مفردة (مثل `"pdf"` فقط، من قبل دعم تعدد الصيغ) تبقى صالحة كما هي — تُعامَل كقائمة عنصر واحد عبر نفس منطق `split(",")`. الدوال `get_allowed_formats_list()` / `get_allowed_formats_display_list()` تُرجعان القائمة كقيَم/كتسميات عربية. |
| `created_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True)` | — |
| `created_at` | `DateTimeField(auto_now_add=True)` | — |

كلاس `WeeklyTask.AllowedFormat` (`TextChoices`) — القيَم الخمس المتاحة اليوم:
`pdf` (ملف PDF)، `image` (صورة)، `audio` (مقطع صوتي)، `video` (مقطع فيديو)،
**`text`** (نص مباشر — **جديد**، لا يتطلب أي ملف).

`Meta.ordering = ["-created_at"]`. المهمة "الحالية" دائمًا الأحدث إنشاءً.
`is_past_due()` = `date.today() > due_date`.

### `TaskSubmission` (تسليم مهمة) — `participants/models.py`, كلاس `TaskSubmission`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `task` | `FK(WeeklyTask, on_delete=CASCADE, related_name="submissions")` | — |
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="task_submissions")` | — |
| `file` | `FileField(upload_to="task_submissions/%Y/%W/", blank=True, null=True)` | اختياري الآن (كان إلزاميًا) — فارغ لتسليم نصي. يُضغط إن كان صورة عبر `save()`. |
| `text_content` | `TextField(blank=True)` | **جديد**. يُستخدم فقط عند اختيار صيغة "نص مباشر". |
| `status` | `CharField(choices=Status.choices, default=PENDING)` | `pending` / `accepted` / `rejected`. |
| `is_featured` | `BooleanField(default=False)` | **جديد**. تُفعَّل عند القبول فقط عبر خانة اختيار "مميزة" في `WeeklyTaskReviewView` — تمنح نقطتين إضافيتين (12 بدل 10). راجع [`points-system.md`](points-system.md). |
| `rejection_reason` | `TextField(blank=True)` | **جديد**. يُملأ عند الرفض، ويظهر للمشارك ليفهم سبب الرفض. |
| `reopened_for_resubmission` | `BooleanField(default=False)` | يتيح تسليمًا ثانيًا لمشارك واحد. |
| `submitted_at` | `DateTimeField(auto_now_add=True)` | — |
| `reviewed_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, blank=True, related_name="+")` | — |
| `reviewed_at` | `DateTimeField(null=True, blank=True)` | — |

`Meta.constraints`: `UniqueConstraint(fields=["task", "participant"], name="unique_submission_per_task_per_participant")`.
`Meta.ordering = ["-submitted_at"]`.

طريقة `save()`: تضغط الصورة فقط لرفع جديد (`file._committed is False`) **وعندما
يكون امتداد الملف نفسه امتداد صورة** (`SUBMISSION_FORMAT_EXTENSIONS["image"]`) —
وليس بناءً على صيغ المهمة المسموحة، لأن مهمة متعددة الصيغ (مثل `"image,pdf"`)
لا تخبرنا وحدها بنوع هذا الملف تحديدًا.

طريقة `get_submitted_format()` (**جديدة**): تُرجع `"text"` إن وُجد `text_content`،
وإلا تطابق امتداد `file` مع `SUBMISSION_FORMAT_EXTENSIONS`، أو `""` إن لم تُطابق
شيئًا. تُستخدم في القوالب لاختيار عنصر معاينة مناسب لكل تسليم الآن بعد أن باتت
المهمة تقبل أكثر من صيغة.

### `StoreProduct` (منتج) — `participants/models.py`, كلاس `StoreProduct`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `name` | `CharField(max_length=150)` | — |
| `description` | `TextField(blank=True)` | — |
| `image` | `ImageField(upload_to="store_products/", blank=True, null=True)` | يُضغط لرفع جديد. |
| `price` | `PositiveIntegerField` | بالنقاط الشرائية. |
| `stock` | `PositiveIntegerField(default=0)` | الكمية. |

`Meta.ordering = ["name"]`. `is_available()` = `stock > 0`.

### `StoreOrder` (طلب) — `participants/models.py`, كلاس `StoreOrder`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="store_orders")` | — |
| `product` | `FK(StoreProduct, on_delete=PROTECT, related_name="orders")` | **`PROTECT`**: لا يمكن حذف منتج له طلب **قيد التنفيذ أو مكتمل**. طلب مسترجَع لا يمنع الحذف بعد الآن — راجع الملاحظة أدناه. |
| `price_at_order` | `PositiveIntegerField` | لقطة سعر المنتج وقت الطلب — تغيير السعر لاحقًا لا يؤثر على الطلبات القديمة. |
| `status` | `CharField(choices=Status.choices, default=PENDING)` | `pending` / `completed` / `refunded`. |
| `ordered_at` / `completed_at` / `refunded_at` | `DateTimeField` (الأخيران `null=True, blank=True`) | — |

`Meta.ordering = ["-ordered_at"]`.

> **تحديث سلوك الحذف:** `on_delete=PROTECT` يمنع حذف منتج له **أي** طلب مرتبط،
> بصرف النظر عن حالته — هذا سلوك Django الثابت على مستوى قاعدة البيانات ولم
> يتغيّر. لكن `StoreManagementView.post()` (فرع `delete_product`) أصبح الآن
> **يحذف الطلبات المسترجَعة أولًا** ثم المنتج، داخل نفس المعاملة، بشرط ألا
> توجد أي طلبات قيد التنفيذ أو مكتملة — فمنتج طلباته كلها مسترجَعة أصبح قابلًا
> للحذف فعليًا رغم `PROTECT`. راجع [`features.md`](features.md#المتجر).

### `PointsLedgerEntry` (سجل نقاط) — `participants/models.py`, كلاس `PointsLedgerEntry`

> **جديد بالكامل** منذ الإصدار 1.1.0. سجل تدقيق مركزي لكل حركة نقاط تمرّ عبر
> `apply_points_delta`، أيًا كان مصدرها. مشتريات/استرجاعات المتجر **غير مسجّلة
> هنا عمدًا** — فهي حركة عملة منفصلة (`purchase_points` فقط) تُتابَع عبر
> `StoreOrder` نفسه، لا عبر هذا السجل.

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="points_ledger_entries")` | — |
| `points_delta` | `IntegerField` | **موقّع عمدًا** (وليس `PositiveIntegerField`) — تصحيح يخفّض منحة سابقة أو خصم يدوي يجب أن يكون قابلًا للتمثيل كدلتا سالبة. |
| `source` | `CharField(max_length=20, choices=Source.choices)` | مصدر الحركة — انظر أدناه. |
| `description` | `CharField(max_length=255)` | وصف نصي حر (مثل "حضور اللقاء + حضور مبكر" أو سبب النقاط الإضافية). |
| `created_at` | `DateTimeField(auto_now_add=True)` | — |
| `granted_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, related_name="+")` | من نفّذ الإجراء الذي ولّد الحركة. |

كلاس `PointsLedgerEntry.Source` (`TextChoices`): `meeting_attendance` (حضور اللقاء
الأسبوعي)، `quran_circle` (الحلقة القرآنية)، `weekly_task` (المهمة الأسبوعية)،
`weekly_activity` (فعالية الأسبوع)، `extra` (نقاط إضافية).

`Meta.ordering = ["-created_at"]`. راجع [`points-system.md`](points-system.md)
لتفاصيل متى يُكتب كل نوع سجل.

### `PointsResetSnapshot` (لقطة نقاط قبل التصفير) — `participants/models.py`, كلاس `PointsResetSnapshot`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="points_snapshots")` | — |
| `points_before_reset` | `PositiveIntegerField` | قيمة `points` لحظة التصفير. |
| `reset_at` | `DateTimeField(auto_now_add=True)` | كل صفوف نفس التصفير تشترك في نفس الطابع الزمني (نفس `bulk_create`). |
| `reset_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, related_name="+")` | من نفّذ التصفير. |

`Meta.ordering = ["-reset_at", "-points_before_reset"]`. صف واحد لكل مشارك لكل
عملية تصفير. عرض السجل يجمّع "الأحداث" بتقريب `reset_at` إلى الثانية
(`TruncSecond`) — لا يوجد عمود مُعرِّف دفعة منفصل.

---

## الموديلات المسجّلة في واجهة الأدمن (Unfold)

من `participants/admin.py` و`accounts/admin.py` الفعليَّين:

مسجّلة: `User`, `Group`, `Participant`, `WeeklyTask`, `TaskSubmission`,
`StoreProduct`, `WeeklyActivityAttendance` (**جديد التسجيل**), `PointsResetSnapshot`
(الأخير للقراءة فقط: `has_add_permission` و`has_change_permission` يرجعان
`False`).

**غير مسجّلة** في الأدمن: `CircleAttendance`, `MeetingAttendance`, `StoreOrder`,
`PasswordResetRequest`, `PointsLedgerEntry` (تُدار/تُعرض عبر واجهات التطبيق
المخصصة فقط — مثلًا `PointsLedgerEntry` تُعرض في `PointsLedgerView`، لا في
الأدمن).
