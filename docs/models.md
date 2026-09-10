# الموديلات

> مستخرج حقلًا حقلًا من `accounts/models.py` و`participants/models.py`.
> `verbose_name` العربي بين قوسين حيث يفيد.

## تطبيق `accounts`

### `User` — `accounts/models.py:49`

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

`clean()` يرفض المستخدم بلا `username` **و**بلا `national_id`.

### `Role` — `accounts/models.py:42` (`TextChoices`)

`participant` / `group_supervisor` / `general_supervisor` / `superadmin`.
راجع [`roles-and-permissions.md`](roles-and-permissions.md).

### `PasswordResetRequest` — `accounts/models.py:93`

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

### `AcademicStage` — `participants/models.py:58` (`TextChoices`)

`grade_5`=خامس ابتدائي، `grade_6`=سادس ابتدائي، `grade_7`=أول متوسط،
`grade_8`=ثاني متوسط، `grade_9`=ثالث متوسط.

### `Group` (بيئة) — `participants/models.py:65`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `name` | `CharField(max_length=50, unique=True)` | اسم البيئة. المطابقة في الاستيراد تتم بالاسم الدقيق. |
| `supervisor` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, limit_choices_to={"role": GROUP_SUPERVISOR})` | **بدون `related_name`** → العلاقة العكسية من `User` هي `group_set`. |

### `Participant` (مشارك) — `participants/models.py:85`

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

### `CircleAttendance` (حضور حلقة) — `participants/models.py:117`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="circle_attendances")` | — |
| `date` | `DateField` | تاريخ اليوم. |
| `attended` | `BooleanField(default=False)` | حضر؟ = 3 نقاط. |
| `achieved` | `BooleanField(default=False)` | أنجز؟ = 2 نقطة. مستقل عن `attended`. |
| `recorded_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, limit_choices_to={"role": GROUP_SUPERVISOR})` | من سجّله (قد يكون مشرفًا عامًا فعليًا — القيد للأدمن فقط). |

`Meta.constraints`: `UniqueConstraint(fields=["participant", "date"], name="unique_circle_attendance_per_day")` — **سجل واحد لكل مشارك في اليوم**.

### `MeetingAttendance` (حضور لقاء) — `participants/models.py:162`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="meeting_attendances")` | — |
| `week_start_date` | `DateField` | **اسم الحقل مضلِّل**: يُخزَّن فيه التاريخ المختار في الواجهة حرفيًا كـ "تاريخ اللقاء"، بلا أي حساب لبداية أسبوع (انظر docstring `SupervisorDashboardView`). |
| `attended` | `BooleanField(default=False)` | حضر؟ = 8 نقاط. |
| `is_early` | `BooleanField(default=False)` | حضور مبكر؟ = 2 نقطة إضافية. |
| `recorded_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, limit_choices_to={"role": GROUP_SUPERVISOR})` | — |

`Meta.constraints`: `UniqueConstraint(fields=["participant", "week_start_date"], name="unique_meeting_attendance_per_week")`.

### `WeeklyTask` (مهمة أسبوعية) — `participants/models.py:200`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `title` | `CharField(max_length=200)` | — |
| `description` | `TextField` | — |
| `due_date` | `DateField` | موعد التسليم. |
| `allowed_formats` | `CharField(max_length=10, choices=AllowedFormat.choices)` | صيغة واحدة مسموحة: `pdf` / `image` / `audio` / `video`. |
| `created_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True)` | — |
| `created_at` | `DateTimeField(auto_now_add=True)` | — |

`Meta.ordering = ["-created_at"]`. المهمة "الحالية" دائمًا الأحدث إنشاءً.
`is_past_due()` = `date.today() > due_date`.

### `TaskSubmission` (تسليم مهمة) — `participants/models.py:242`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `task` | `FK(WeeklyTask, on_delete=CASCADE, related_name="submissions")` | — |
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="task_submissions")` | — |
| `file` | `FileField(upload_to="task_submissions/%Y/%W/")` | يُضغط إن كان صورة ومهمةً صورية (`save()` override). |
| `status` | `CharField(choices=Status.choices, default=PENDING)` | `pending` / `accepted` / `rejected`. |
| `reopened_for_resubmission` | `BooleanField(default=False)` | يتيح تسليمًا ثانيًا لمشارك واحد. |
| `submitted_at` | `DateTimeField(auto_now_add=True)` | — |
| `reviewed_by` | `FK(AUTH_USER_MODEL, on_delete=SET_NULL, null=True, blank=True, related_name="+")` | — |
| `reviewed_at` | `DateTimeField(null=True, blank=True)` | — |

`Meta.constraints`: `UniqueConstraint(fields=["task", "participant"], name="unique_submission_per_task_per_participant")`.
`Meta.ordering = ["-submitted_at"]`.
`save()` يضغط الصورة عبر `compress_image_field` فقط لرفع جديد (`file._committed is False`) ولمهمة `allowed_formats == "image"`.

### `StoreProduct` (منتج) — `participants/models.py:326`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `name` | `CharField(max_length=150)` | — |
| `description` | `TextField(blank=True)` | — |
| `image` | `ImageField(upload_to="store_products/", blank=True, null=True)` | يُضغط لرفع جديد. |
| `price` | `PositiveIntegerField` | بالنقاط الشرائية. |
| `stock` | `PositiveIntegerField(default=0)` | الكمية. |

`Meta.ordering = ["name"]`. `is_available()` = `stock > 0`.

### `StoreOrder` (طلب) — `participants/models.py:365`

| الحقل | النوع | ملاحظات |
|-------|------|---------|
| `participant` | `FK(Participant, on_delete=CASCADE, related_name="store_orders")` | — |
| `product` | `FK(StoreProduct, on_delete=PROTECT, related_name="orders")` | **`PROTECT`**: لا يمكن حذف منتج له طلبات. |
| `price_at_order` | `PositiveIntegerField` | لقطة سعر المنتج وقت الطلب — تغيير السعر لاحقًا لا يؤثر على الطلبات القديمة. |
| `status` | `CharField(choices=Status.choices, default=PENDING)` | `pending` / `completed` / `refunded`. |
| `ordered_at` / `completed_at` / `refunded_at` | `DateTimeField` (الأخيران `null=True, blank=True`) | — |

`Meta.ordering = ["-ordered_at"]`.

### `PointsResetSnapshot` (لقطة نقاط قبل التصفير) — `participants/models.py:415`

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

مسجّلة: `User`, `Group`, `Participant`, `WeeklyTask`, `TaskSubmission`,
`StoreProduct`, `PointsResetSnapshot` (الأخير للقراءة فقط:
`has_add_permission` و`has_change_permission` يرجعان `False`).

**غير مسجّلة** في الأدمن: `CircleAttendance`, `MeetingAttendance`,
`StoreOrder`, `PasswordResetRequest` (تُدار عبر واجهات التطبيق فقط).
