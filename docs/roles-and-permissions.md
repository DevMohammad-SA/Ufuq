# الأدوار والصلاحيات

> مستخرج من `Role` في `accounts/models.py` ومن دوال `test_func()` في كل View
> فعليًا (تحقّق مباشر من الكود الحالي). المراجع بصيغة اسم الدالة/الكلاس بدل
> رقم السطر.

## الأدوار الأربعة

معرّفة في `accounts/models.py` كـ `Role(models.TextChoices)`:

| القيمة المخزنة | التسمية العربية | الوصف |
|----------------|-----------------|-------|
| `participant` | مشارك | الناشئ/الشاب المشارك في البرنامج. له صف `Participant` مرتبط، ويدخل برقم الهوية + كلمة مرور. |
| `group_supervisor` | مشرف بيئة | مسؤول عن بيئة واحدة أو أكثر (`Group`، منذ تحويل `Group.supervisor` إلى `ManyToManyField` — راجع [`models.md`](models.md)). يسجّل الحضور لبيئاته، ويرى بياناتها وتقاريرها وطلبات متجرها. يراجع تسليمات طلابها بعد تفويض صريح من المشرف العام فقط؛ الافتراضي اطلاع فقط. |
| `general_supervisor` | مشرف عام | مسؤول على مستوى البرنامج كامله: المهام الأسبوعية، المتجر، الاستيراد، إضافة مشارك مفرد، النقاط الإضافية، تصفير النقاط، سجل النقاط الكامل، كل البيئات، ومنح/سحب تفويض مشرف البيئة لمراجعة المهام. |
| `superadmin` | مشرف النظام | يدير البرنامج والمهام والمتجر ويصل إلى أدمن Django (`is_staff`/`is_superuser`). لا يدير تفويض مراجعة المهام ولا يرى ملاحظات اللقاء الخاصة؛ هاتان الصلاحيتان مقصورتان عمدًا على المشرف العام (والملاحظات يراها مشرف البيئة أيضًا). |

> **ملاحظة:** تشترك أدوار المشرفين في كثير من `test_func()`، لكنها ليست متطابقة
> في كل فعل: `TaskReviewPermissionsView` للمشرف العام فقط، وملاحظات اللقاء
> الخاصة تستبعد مشرف النظام، ومشرف البيئة لا يملك قبول/رفض تسليم إلا بتفويض
> مفعل يُعاد التحقق منه في كل POST.

## كيف يسجّل كل دور دخوله

| الدور | صفحة الدخول | الحقول | آلية المصادقة |
|-------|-------------|--------|----------------|
| مشارك | `accounts:login_participant` (`/accounts/login/participant/`) | رقم الهوية (في حقل `username`) + كلمة المرور | `ParticipantAuthenticationForm` → `NationalIDOrUsernameBackend` |
| مشرف بيئة / عام / نظام | `accounts:login_supervisor` (`/accounts/login/supervisor/`) | اسم المستخدم + كلمة المرور | `AuthenticationForm` القياسي → `NationalIDOrUsernameBackend` |

بعد الدخول (`accounts/views.py`، كلاسا `ParticipantLoginView`/`SupervisorLoginView`):
- المشارك → `participants:dashboard`.
- مشرف بيئة → `participants:supervisor_dashboard`.
- مشرف عام / نظام → `participants:general_supervisor_dashboard`.

راجع [`authentication.md`](authentication.md) لتفاصيل الـ backend وكلمة مرور
المشارك.

## مصفوفة الصلاحيات لكل View (من `test_func()` الفعلية)

كل الـ Views المحمية تستخدم `UserPassesTestMixin` مع `test_func()`. الجدول
التالي مستخرج حرفيًا من `participants/views.py` و`accounts/views.py` الحاليَّين:

### تطبيق `participants`

| View | المسار (`name`) | `test_func()` — من يُسمح له |
|------|------------------|------------------------------|
| `HomeView` | `home` (في `config/urls.py`) | **عام** — `TemplateView` بلا أي mixin؛ صفحة الهبوط لا تتطلب دخولًا |
| `ParticipantDashboardView` | `dashboard` | `LoginRequiredMixin` فقط (بدون `test_func`) — أي مستخدم مسجّل، لكن الصفحة تصل لـ `request.user.participant` فورًا فتتعطّل بـ `RelatedObjectDoesNotExist` لغير المشاركين |
| `SupervisorDashboardView` | `supervisor_dashboard` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)`؛ مشرف البيئة مقيد ببيئاته المسندة والبقية يختارون أي بيئة |
| `StudentReportView` **(غير موسوم بعد)** | `student_report` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` — تقرير قراءة فقط؛ مشرف البيئة يرى كل بيئاته المسندة |
| `StudentDetailView` **(غير موسوم بعد)** | `student_detail` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` — يجري التقييد قبل جلب الطالب بالمعرّف |
| `QuranCircleAttendanceView` | `quran_circle_attendance` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` |
| `WeeklyActivityAttendanceView` **(جديد)** | `weekly_activity_attendance` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` |
| `ParticipantImportView` | `import_participants` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |
| `AddParticipantView` **(جديد)** | `add_participant` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` — **لا** يشمل `GROUP_SUPERVISOR` (راجع الملاحظة أدناه) |
| `GeneralSupervisorDashboardView` | `general_supervisor_dashboard` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |
| `PointsSnapshotHistoryView` | `points_snapshot_history` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |
| `ExtraPointsView` **(جديد)** | `extra_points` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |
| `PointsLedgerView` **(جديد)** | `points_ledger` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` |
| `ParticipantsDataView` | `participants_data` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` |
| `ParticipantsDataPDFExportView` | `participants_data_pdf` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` |
| `WeeklyTaskReviewView` | `weekly_task_review` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)`؛ مشرف البيئة يرى تسليمات طلاب بيئاته فقط، وقبول/رفضها مشروط بتفويض فعّال |
| `TaskReviewPermissionsView` **(غير موسوم بعد)** | `task_review_permissions` | `role == GENERAL_SUPERVISOR` فقط — يمنح أو يسحب تفويض مشرف البيئة |
| `TaskSubmissionView` | `task_submission` | `role == PARTICIPANT` — ولا يقبل POST إلا لمهمة ضمن `get_active_tasks()`، راجع [`weekly-tasks.md`](weekly-tasks.md) |
| `TasksArchiveView` | `tasks_archive` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |
| `StoreView` | `store` | `role == PARTICIPANT` |
| `StoreManagementView` | `store_management` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)`؛ مشرف البيئة يطالع طلبات بيئاته فقط، وكل POST منه 403؛ الإدارة/الإكمال/الاسترجاع للمشرف العام أو النظام |

> **⚠️ ملاحظة على `AddParticipantView`:** الدالة الداخلية `get_locked_group()`
> فيها لا تزال تتحقق من `role == GROUP_SUPERVISOR` وتُرجع بيئته المقفلة في
> هذه الحالة — لكن `test_func()` يستبعد `GROUP_SUPERVISOR` بالكامل من الوصول
> لهذا الـ View أصلًا (تم سحب هذه الصلاحية عنه بحسب `CHANGELOG.md`، الإصدار
> 1.1.0). فرع `GROUP_SUPERVISOR` داخل `get_locked_group()` أصبح **كودًا لا
> يُنفَّذ عمليًا أبدًا** (dead branch) بعد ذلك التقييد — لا خطر أمني منه، لكنه
> بقايا لم تُنظَّف. راجع [`known-limitations.md`](known-limitations.md).

> **ملاحظة على `ExtraPointsView`:** رغم أن `test_func()` يقصرها على
> `GENERAL_SUPERVISOR`/`SUPERADMIN`، تحتوي طريقة `get_participant_queryset()`
> على فرع خاص بـ `GROUP_SUPERVISOR` يقصره على مشاركي بيئته فقط — وهو أيضًا فرع
> لا يُنفَّذ عمليًا حاليًا لنفس السبب (مشرف البيئة لا يصل لهذا الـ View أصلًا
> بعد سحب الصلاحية في 1.1.0). يبدو أن هذا كان التصميم الأصلي قبل السحب،
> وتُرك الفرع دون حذف.

### تطبيق `accounts`

| View | المسار (`name`) | القيد |
|------|------------------|-------|
| `ParticipantLoginView` | `login_participant` | عام (لا قيد) |
| `SupervisorLoginView` | `login_supervisor` | عام (لا قيد) |
| `RahhalLogoutView` | `logout` | `LogoutView` القياسي |
| `SetPasswordView` | `set_password` | `LoginRequiredMixin` فقط؛ `dispatch()` يعيد التوجيه للوحة إن كان `must_set_password=False` |
| `SupervisorPasswordChangeView` | `change_password` | `LoginRequiredMixin` فقط (لا `test_func`) — أي مستخدم مسجّل يمكنه فتحها |
| `ForgotPasswordView` | `forgot_password` | عام (لا قيد) |

> **ملاحظة على `SupervisorPasswordChangeView`:** لا يوجد `UserPassesTestMixin`،
> فأي مستخدم مسجّل دخوله (بما فيه مشارك) يستطيع الوصول لـ
> `/accounts/change-password/`. عمليًا الرابط يظهر في قسم "الحساب" بقائمة تنقل المشرفين فقط،
> ومشارك عليه `must_set_password=True` يُعاد توجيهه بواسطة
> `ForcePasswordSetupMiddleware` قبل الوصول.

## نطاق البيانات حسب الدور

- **مشرف البيئة**: تستخدم صفحات التقرير وملف الطالب ومراجعة المهام وإدارة
  المتجر `allowed_groups()`/`scoped_participants()`، فتشمل **كل بيئاته المسندة**
  ولا تقبل نطاقًا خارجها. وكذلك تستخدم واجهات كشف اللقاء وفعالية الأسبوع
  وسجل النقاط وبيانات المشاركين وتصدير PDF النطاق نفسه؛ يتجاهل PDF أسماء
  البيئات الممررة من مشرف البيئة بدل أن توسّع النطاق، ثم يعرض كل بيئاته
  المسندة.
- **المشرف العام / النظام**: يريان كل البيئات ويختاران البيئة بحرية حيثما
  توفّر. الاستثناءات المقصودة: إدارة تفويض التصحيح للمشرف العام فقط، وملاحظات
  اللقاء لمشرف البيئة/العام فقط، لا لمشرف النظام.

## تفويض مراجعة المهام

- لا ينشأ لمشرف البيئة تفويض افتراضي. يظل قادرًا على معاينة مهام وتسليمات
  طلاب بيئاته المسندة، بلا إنشاء/تفعيل/إعادة فتح.
- المشرف العام وحده يفعّل أو يلغي صف `TaskReviewPermission` لمشرف بيئة؛
  التفويض لا يوسّع نطاق الطلاب بل يسمح فقط بالقبول والرفض ضمن النطاق الموجود.
- يعيد `WeeklyTaskReviewView.post()` قفل وفحص `enabled=True` داخل معاملة عند
  كل قبول أو رفض. لذلك يسري الإلغاء على الطلب اللاحق حتى لو بقيت صفحة المراجع
  مفتوحة.
- لا يمكن لمشرف البيئة المفوّض قبول تسليم تاريخي `points_awarded=NULL`؛ يحتاج
  المشرف العام أو النظام إلى قرار صريح من حقل `historical_award` قبل القبول.
