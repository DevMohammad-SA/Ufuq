# الأدوار والصلاحيات

> مستخرج من `Role` في `accounts/models.py` ومن دوال `test_func()` في كل View
> فعليًا (تحقّق مباشر من الكود الحالي). المراجع بصيغة اسم الدالة/الكلاس بدل
> رقم السطر.

## الأدوار الأربعة

معرّفة في `accounts/models.py` كـ `Role(models.TextChoices)`:

| القيمة المخزنة | التسمية العربية | الوصف |
|----------------|-----------------|-------|
| `participant` | مشارك | الناشئ/الشاب المشارك في البرنامج. له صف `Participant` مرتبط، ويدخل برقم الهوية + كلمة مرور. |
| `group_supervisor` | مشرف بيئة | مسؤول عن بيئة واحدة أو أكثر (`Group`، منذ تحويل `Group.supervisor` إلى `ManyToManyField` — راجع [`models.md`](models.md)). يسجّل حضور اللقاء الأسبوعي وحضور/إنجاز الحلقة القرآنية وفعالية الأسبوع لمشاركي بيئته، ويستعرض سجل نقاط بيئته. |
| `general_supervisor` | مشرف عام | مسؤول على مستوى البرنامج كامله: المهام الأسبوعية، المتجر، الاستيراد، إضافة مشارك مفرد، النقاط الإضافية، تصفير النقاط، سجل النقاط الكامل، كل البيئات. |
| `superadmin` | مشرف النظام | نفس صلاحيات المشرف العام في كل الـ Views (كل `test_func` يعامل `GENERAL_SUPERVISOR` و`SUPERADMIN` معاملة واحدة)، بالإضافة إلى أن `createsuperuser` يعيّن هذا الدور تلقائيًا مع `is_staff=is_superuser=True` (`UserManager.create_superuser`). |

> **ملاحظة:** لا يوجد في الكود أي `test_func` يميّز `SUPERADMIN` عن
> `GENERAL_SUPERVISOR`. الفرق الوحيد عمليًا هو أعلام `is_staff`/`is_superuser`
> (الوصول لواجهة أدمن Django) التي يضبطها `createsuperuser` أو الأدمن يدويًا.

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
| `SupervisorDashboardView` | `supervisor_dashboard` | `role == GROUP_SUPERVISOR` |
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
| `WeeklyTaskReviewView` | `weekly_task_review` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |
| `TaskSubmissionView` | `task_submission` | `role == PARTICIPANT` — ولا يقبل POST إلا لمهمة ضمن `get_active_tasks()`، راجع [`weekly-tasks.md`](weekly-tasks.md) |
| `TasksArchiveView` | `tasks_archive` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |
| `StoreView` | `store` | `role == PARTICIPANT` |
| `StoreManagementView` | `store_management` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` |

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

- **مشرف البيئة**: كل Views الحضور والبيانات وسجل النقاط تقصره على بيئته/بيئاته
  عبر `request.user.group_set.first()` — منذ تحويل `Group.supervisor` إلى
  `ManyToManyField`، هذا يُرجع **بيئة واحدة فقط من عدة محتملة** إن أُسند
  المستخدم لأكثر من بيئة (أول نتيجة بلا ترتيب صريح مضمون). **أي قيمة `?group=`
  في الطلب تُتجاهل تمامًا** لمشرف البيئة (انظر
  `QuranCircleAttendanceView.get_selected_group`،
  `WeeklyActivityAttendanceView.get_selected_group`، و
  `ParticipantsDataPDFExportView._get_participants`).
- **المشرف العام / النظام**: يرى كل البيئات، ويختار البيئة بحرية عبر `?group=`
  حيثما توفّر، ويرى سجل النقاط والاستيراد ولوحة العام كاملة.
