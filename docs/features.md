# الميزات الوظيفية

> كل ميزة أدناه مطابقة لمسار حقيقي في `participants/urls.py` أو
> `accounts/urls.py` و View فعلي. الجدول الكامل للمسارات في نهاية الملف.

## صفحة الهبوط العامة

- **View:** `TemplateView` مباشرة في `config/urls.py:26` (`name="home"`).
- **القالب:** `templates/home.html`.
- عامة بلا تسجيل دخول: تعريف بالبرنامج + روابط دخول المشارك ودخول المشرفين.

## لوحة المشارك + مؤشر رحلة النخبة

- **View:** `ParticipantDashboardView` (`participants/views.py:253`) — المسار
  `participants:dashboard`.
- **القالب:** `participants/participant_dashboard.html`.
- تعرض عملات المشارك، وشريط موقعه ضمن مدى أميال البرنامج
  (`_build_range`, `participants/views.py:349`).
- **مؤشر رحلة النخبة** (`get_elite_status`, `participants/views.py:281`): رسالة
  نصية تُحسب لحظيًا (لا تُخزَّن) تخبر المشارك ببُعده عن حدّ أعلى 20 داخل بيئته.
  لا تكشف ترتيب أو نقاط أي مشارك آخر.
  > docstring الدالة يشير إلى ملف مواصفات `آلية_مؤشر_التبشير_برحلة_النخبة.md`
  > **غير موجود في المستودع** — راجع [`known-limitations.md`](known-limitations.md).

## تحضير اللقاء الأسبوعي (مشرف البيئة)

- **View:** `SupervisorDashboardView` (`participants/views.py:371`) — المسار
  `participants:supervisor_dashboard`.
- **القالب:** `participants/supervisor_dashboard.html`.
- كشف كامل لمشاركي بيئة المشرف، عمودا "حضور مبكر" و"حضور اللقاء"، منتقي تاريخ.
- الحفظ POST واحد يُنشئ/يحدّث `MeetingAttendance` لكل مشارك ويطبّق دلتا النقاط.
- الموديل: `MeetingAttendance`. النقاط: 8 / 10. راجع [`points-system.md`](points-system.md).

## الحلقة القرآنية — حضور وإنجاز (يدوي، مؤقت)

- **View:** `QuranCircleAttendanceView` (`participants/views.py:513`) — المسار
  `participants:quran_circle_attendance`.
- **القالب:** `participants/quran_circle_attendance.html`.
- متاح لمشرف البيئة (بيئته فقط) وللمشرف العام/النظام (أي بيئة عبر `?group=<id>`).
- عمودان مستقلان: "حضور الحلقة" (3 نقاط) و"إنجاز الحلقة" (2 نقطة).
- الموديل: `CircleAttendance` (حقلا `attended` و`achieved`).
- **واجهة يدوية مؤقتة** بديلة لاستيراد إكسل من معلّم الحلقة **لم يُبنَ بعد** —
  راجع [`known-limitations.md`](known-limitations.md) وdocstring الـ View.

## استيراد المشاركين من إكسل (مشرف عام)

- **View:** `ParticipantImportView` (`participants/views.py:693`) — المسار
  `participants:import_participants`.
- **القالب:** `participants/import_participants.html` + ملف قالب
  `static/templates/participants_import_template.xlsx`.
- الورقة المتوقعة اسمها `المشاركون`، البيانات من الصف 3، 6 أعمدة (الاسم،
  رقم الهوية، اسم البيئة، المرحلة الدراسية، جوال المشارك، جوال ولي الأمر).
- لكل صف صالح: يُنشئ `User` (دور `PARTICIPANT`، كلمة المرور = رقم الهوية،
  `must_set_password=True`) + `Participant`. البيئة تُطابَق بالاسم الدقيق؛ بيئة
  غير موجودة ترفض الصف (لا تُنشأ بيئات تلقائيًا).
- التحقق: رقم هوية من 10 أرقام، غير مكرر، مرحلة دراسية معروفة.

## لوحة المشرف العام

- **View:** `GeneralSupervisorDashboardView` (`participants/views.py:827`) —
  المسار `participants:general_supervisor_dashboard`.
- **القالب:** `participants/general_supervisor_dashboard.html`.
- **بطاقات KPI**: إجمالي المشاركين، مهام قيد المراجعة، طلبات متجر قيد التنفيذ.
- **4 رسوم بيانية** عبر **Chart.js 4** (يُحمَّل من CDN
  `cdn.jsdelivr.net/npm/chart.js@4` — القالب السطر 184): مشاركون لكل بيئة،
  حالة تسليم آخر مهمة، متوسط النقاط لكل بيئة، طلبات المتجر حسب الحالة.
  البيانات تُمرَّر كـ `json_script`.
- **بطاقة طلبات استرجاع كلمة المرور** (موافقة/إعادة تعيين) — راجع
  [`authentication.md`](authentication.md).
- **زر تصفير نقاط المشاركين** (`action=reset_points`) — يحفظ لقطة ثم يصفّر
  `points` فقط.
- **رابط "سجل عمليات التصفير السابقة"** → `PointsSnapshotHistoryView`.

## لقطة تاريخية للنقاط

- **الكتابة:** فرع `reset_points` في `GeneralSupervisorDashboardView.post()`
  (`participants/views.py:939-960`) — `bulk_create` لصف `PointsResetSnapshot`
  لكل مشارك قبل التصفير، داخل `transaction.atomic()`.
- **العرض:** `PointsSnapshotHistoryView` (`participants/views.py:986`) — المسار
  `participants:points_snapshot_history`، القالب
  `participants/points_snapshot_history.html`. يسرد "أحداث" التصفير (بتقريب
  `reset_at` إلى الثانية عبر `TruncSecond`)، واختيار حدث يعرض ترتيب المشاركين
  وقتها تنازليًا حسب النقاط.
- **قيد:** هذه اللقطة **لا تُستخدَم تلقائيًا** في أي حساب لاحق لرحلة النخبة —
  راجع [`known-limitations.md`](known-limitations.md).

## جدول بيانات المشاركين

- **View:** `ParticipantsDataView` (`participants/views.py:1045`) — المسار
  `participants:participants_data`.
- **القالب:** `participants/participants_data.html`.
- جدول للقراءة فقط، متاح للأدوار الإدارية الثلاثة. مشرف البيئة يرى بيئته فقط.
- بحث نصي + فلتر بيئات متعدد (checkboxes) + فرز أعمدة — **كله جافاسكربت في
  المتصفح** على بيانات محمّلة مسبقًا (لا طلبات خادم).
- عمودا الحضور التراكمي محسوبان بـ `annotate(Count(..., distinct=True))`.

## تصدير PDF لبيانات المشاركين

- **View:** `ParticipantsDataPDFExportView` (`participants/views.py:1115`) —
  المسار `participants:participants_data_pdf`.
- **القالب:** `participants/participants_data_pdf.html` (مستند مستقل).
- يستخدم **WeasyPrint** (استيراد كسول داخل `get()`، `participants/views.py:1175`).
- النطاق: مثل `ParticipantsDataView` (مشرف البيئة مقفل على بيئته)، مع تصفية
  إضافية بأسماء البيئات المُمرَّرة `?group=<name>` (مكرَّرة) — يبنيها جافاسكربت
  في `participants_data.html` من الـ checkboxes المحددة. **البحث والفرز لا
  ينعكسان**؛ الترتيب دائمًا أبجدي بالاسم.
- الترويسة الرسمية خلفية `@page` من `static/images/letterhead.png`.
  > **قيد تشغيلي:** ملف `letterhead.png` **غير موجود في المستودع حاليًا**،
  > ومكتبات WeasyPrint النظامية (Pango/Cairo) غير مثبتة في بيئة التطوير. راجع
  > [`known-limitations.md`](known-limitations.md).

## المهام الأسبوعية

- **إنشاء ومراجعة (مشرف عام):** `WeeklyTaskReviewView`
  (`participants/views.py:1193`) — المسار `participants:weekly_task_review`،
  القالب `participants/weekly_task_review.html`. إنشاء مهمة جديدة، قبول/رفض
  التسليمات (القبول = 10 نقاط عبر `apply_points_delta`).
- **أرشيف كل المهام (مشرف عام):** `TasksArchiveView`
  (`participants/views.py:1322`) — المسار `participants:tasks_archive`،
  القالب `participants/tasks_archive.html`. لكل مهمة: من سلّم ومن لم يسلّم،
  وإعادة فتح تسليم لمشارك (`action=reopen`). فيه معاينة ملف داخل نافذة منبثقة.
- **رفع التسليم (مشارك):** `TaskSubmissionView` (`participants/views.py:1246`) —
  المسار `participants:task_submission`، القالب
  `participants/task_submission_form.html`. تسليم واحد لكل مهمة (ما لم يُعَد
  فتحه). التحقق من الصيغة والحجم في `TaskSubmissionForm` (`participants/forms.py:41`):
  - الصيغ: `pdf`=.pdf، `image`=.jpg/.jpeg/.png، `audio`=.mp3/.wav/.m4a،
    `video`=.mp4/.mov/.webm.
  - الحدود: pdf 10MB، audio 15MB، video 50MB. الصور **لا تُرفض للحجم** —
    تُضغط في `TaskSubmission.save()` (`MAX_IMAGE_DIMENSION=1600`,
    `IMAGE_QUALITY=80`, JPEG — `participants/models.py:14-15`).

## المتجر

- **المتجر (مشارك):** `StoreView` (`participants/views.py:1397`) — المسار
  `participants:store`، القالب `participants/store.html`. شراء منتج بالنقاط
  الشرائية. التحقق من المخزون والرصيد **خادميًا** داخل `transaction.atomic()`
  مع `select_for_update` (حماية من السباق). ينشئ `StoreOrder` بـ
  `price_at_order` كلقطة سعر.
- **إدارة المتجر (مشرف عام):** `StoreManagementView` (`participants/views.py:1484`)
  — المسار `participants:store_management`، القالب
  `participants/store_management.html`.
  - إضافة/تعديل/حذف منتج (`StoreProductForm`). حذف منتج له طلبات → `ProtectedError`
    → رسالة عربية (لا صفحة 500).
  - إكمال طلب (`action=complete`) واسترجاع طلب (`action=refund`). الاسترجاع
    يعيد `purchase_points` والمخزون معًا، وحارس `status != REFUNDED` يمنع
    الاسترجاع المزدوج.
- **قاعدة صارمة في الكود:** المتجر يعدّل `purchase_points` **فقط** ولا يستدعي
  `apply_points_delta` إطلاقًا (تعليق `participants/views.py:1389-1394`).

## تغيير كلمة مرور المشرف

- **View:** `SupervisorPasswordChangeView` (`accounts/views.py:68`) — المسار
  `accounts:change_password`، القالب `accounts/change_password.html`.
- راجع [`authentication.md`](authentication.md).

## واجهة أدمن Django (Unfold)

`/admin/` — مُثمَّنة بـ `django-unfold`. الموديلات المسجّلة في
[`models.md`](models.md#الموديلات-المسجلة-في-واجهة-الأدمن-unfold).

---

## جدول المسارات الكامل

### `config/urls.py`
| المسار | الاسم |
|--------|------|
| `/admin/` | (أدمن Django) |
| `/` | `home` |
| `/accounts/…` | `include("accounts.urls")` |
| `/participants/…` | `include("participants.urls")` |

### `accounts/urls.py` (البادئة `/accounts/`)
| المسار | الاسم |
|--------|------|
| `login/participant/` | `accounts:login_participant` |
| `login/supervisor/` | `accounts:login_supervisor` |
| `logout/` | `accounts:logout` |
| `set-password/` | `accounts:set_password` |
| `forgot-password/` | `accounts:forgot_password` |
| `change-password/` | `accounts:change_password` |

### `participants/urls.py` (البادئة `/participants/`)
| المسار | الاسم |
|--------|------|
| `dashboard/` | `participants:dashboard` |
| `supervisor/dashboard/` | `participants:supervisor_dashboard` |
| `quran-circle/` | `participants:quran_circle_attendance` |
| `import/` | `participants:import_participants` |
| `general-supervisor/dashboard/` | `participants:general_supervisor_dashboard` |
| `points-snapshots/` | `participants:points_snapshot_history` |
| `data/` | `participants:participants_data` |
| `data/export-pdf/` | `participants:participants_data_pdf` |
| `tasks/review/` | `participants:weekly_task_review` |
| `tasks/archive/` | `participants:tasks_archive` |
| `tasks/submit/` | `participants:task_submission` |
| `store/` | `participants:store` |
| `store/management/` | `participants:store_management` |
