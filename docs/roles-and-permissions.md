# الأدوار والصلاحيات

> مستخرج من `Role` في `accounts/models.py:42` ومن دوال `test_func()` في كل
> View فعليًا.

## الأدوار الأربعة

معرّفة في `accounts/models.py:42-46` كـ `Role(models.TextChoices)`:

| القيمة المخزنة | التسمية العربية | الوصف |
|----------------|-----------------|-------|
| `participant` | مشارك | الناشئ/الشاب المشارك في البرنامج. له صف `Participant` مرتبط، ويدخل برقم الهوية + كلمة مرور. |
| `group_supervisor` | مشرف بيئة | مسؤول عن بيئة واحدة (`Group`). يسجّل حضور اللقاء الأسبوعي وحضور/إنجاز الحلقة القرآنية لمشاركي بيئته فقط. |
| `general_supervisor` | مشرف عام | مسؤول على مستوى البرنامج كامله: المهام الأسبوعية، المتجر، الاستيراد، تصفير النقاط، كل البيئات. |
| `superadmin` | مشرف النظام | نفس صلاحيات المشرف العام في كل الـ Views (كل `test_func` يعامل `GENERAL_SUPERVISOR` و`SUPERADMIN` معاملة واحدة)، بالإضافة إلى أن `createsuperuser` يعيّن هذا الدور تلقائيًا مع `is_staff=is_superuser=True` (`accounts/models.py:29-39`). |

> **ملاحظة:** لا يوجد في الكود أي `test_func` يميّز `SUPERADMIN` عن
> `GENERAL_SUPERVISOR`. الفرق الوحيد عمليًا هو أعلام `is_staff`/`is_superuser`
> (الوصول لواجهة أدمن Django) التي يضبطها `createsuperuser` أو الأدمن يدويًا.

## كيف يسجّل كل دور دخوله

| الدور | صفحة الدخول | الحقول | آلية المصادقة |
|-------|-------------|--------|----------------|
| مشارك | `accounts:login_participant` (`/accounts/login/participant/`) | رقم الهوية (في حقل `username`) + كلمة المرور | `ParticipantAuthenticationForm` → `NationalIDOrUsernameBackend` |
| مشرف بيئة / عام / نظام | `accounts:login_supervisor` (`/accounts/login/supervisor/`) | اسم المستخدم + كلمة المرور | `AuthenticationForm` القياسي → `NationalIDOrUsernameBackend` |

بعد الدخول (`accounts/views.py`):
- المشارك → `participants:dashboard`.
- مشرف بيئة → `participants:supervisor_dashboard`.
- مشرف عام / نظام → `participants:general_supervisor_dashboard`.

راجع [`authentication.md`](authentication.md) لتفاصيل الـ backend وكلمة مرور
المشارك.

## مصفوفة الصلاحيات لكل View (من `test_func()` الفعلية)

كل الـ Views المحمية تستخدم `UserPassesTestMixin` مع `test_func()`. الجدول
التالي مستخرج حرفيًا:

### تطبيق `participants` (`participants/views.py`)

| View | المسار (`name`) | `test_func()` — من يُسمح له | السطر |
|------|------------------|------------------------------|-------|
| `ParticipantDashboardView` | `dashboard` | `LoginRequiredMixin` فقط (بدون `test_func`) — أي مستخدم مسجّل، لكن الصفحة تصل لـ `request.user.participant` فيتعطل غير المشاركين | ~253 |
| `SupervisorDashboardView` | `supervisor_dashboard` | `role == GROUP_SUPERVISOR` | 391 |
| `QuranCircleAttendanceView` | `quran_circle_attendance` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` | 530 |
| `ParticipantImportView` | `import_participants` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` | 697 |
| `GeneralSupervisorDashboardView` | `general_supervisor_dashboard` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` | 833 |
| `PointsSnapshotHistoryView` | `points_snapshot_history` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` | 998 |
| `ParticipantsDataView` | `participants_data` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` | 1064 |
| `ParticipantsDataPDFExportView` | `participants_data_pdf` | `role in (GROUP_SUPERVISOR, GENERAL_SUPERVISOR, SUPERADMIN)` | 1133 |
| `WeeklyTaskReviewView` | `weekly_task_review` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` | 1197 |
| `TaskSubmissionView` | `task_submission` | `role == PARTICIPANT` | 1250 |
| `TasksArchiveView` | `tasks_archive` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` | 1338 |
| `StoreView` | `store` | `role == PARTICIPANT` | 1401 |
| `StoreManagementView` | `store_management` | `role in (GENERAL_SUPERVISOR, SUPERADMIN)` | 1488 |

### تطبيق `accounts` (`accounts/views.py`)

| View | المسار (`name`) | القيد |
|------|------------------|-------|
| `ParticipantLoginView` | `login_participant` | عام (لا قيد) |
| `SupervisorLoginView` | `login_supervisor` | عام (لا قيد) |
| `RahhalLogoutView` | `logout` | `LogoutView` القياسي |
| `SetPasswordView` | `set_password` | `LoginRequiredMixin` فقط؛ `dispatch()` يعيد التوجيه للوحة إن كان `must_set_password=False` |
| `SupervisorPasswordChangeView` | `change_password` | `LoginRequiredMixin` فقط (لا `test_func`) — أي مستخدم مسجّل يمكنه فتحها |
| `ForgotPasswordView` | `forgot_password` | عام (لا قيد) |

> **ملاحظة على `SupervisorPasswordChangeView`:** لا يوجد `UserPassesTestMixin`،
> فأي مستخدم مسجّل دخوله (بما فيه مشارك) يستطيع الوصول لـ `/accounts/change-password/`.
> عمليًا الرابط يظهر في navbar المشرفين فقط، ومشارك عليه `must_set_password=True`
> يُعاد توجيهه بواسطة `ForcePasswordSetupMiddleware` قبل الوصول.

## نطاق البيانات حسب الدور

- **مشرف البيئة**: كل Views الحضور والبيانات تقصره على بيئته عبر
  `request.user.group_set.first()`. **أي قيمة `?group=` في الطلب تُتجاهل
  تمامًا** لمشرف البيئة (انظر `QuranCircleAttendanceView.get_selected_group`,
  `participants/views.py:546` و`ParticipantsDataPDFExportView._get_participants`,
  `participants/views.py:1140`).
- **المشرف العام / النظام**: يرى كل البيئات، ويختار البيئة بحرية عبر `?group=`
  حيثما توفّر.
