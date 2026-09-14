# الميزات الوظيفية

> كل ميزة أدناه مطابقة لمسار حقيقي في `participants/urls.py` أو
> `accounts/urls.py` و View فعلي (تحقّق مباشر من الكود الحالي). المراجع
> بصيغة اسم الدالة/الكلاس بدل رقم السطر. الجدول الكامل للمسارات في نهاية
> الملف.

## صفحة الهبوط العامة

- **View:** `HomeView` (`config/urls.py` → `participants/views.py`، `name="home"`).
- **القالب:** `templates/home.html`.
- عامة بلا تسجيل دخول: تعريف بالبرنامج + روابط دخول المشارك ودخول المشرفين.
- عدد المشاركين المعروض (`participants_count`) **محسوب حيًا** من
  `Participant.objects.count()` في `HomeView.get_context_data` — **ليس** رقمًا
  ثابتًا. أما عدد "البيئات" (٣) فلا يزال **نصًا ثابتًا مكتوبًا يدويًا** في
  القالب، غير مرتبط بقاعدة البيانات — راجع
  [`known-limitations.md`](known-limitations.md).
- لا وجود لاسم "أُفق" أو ذكر تسويقي للحلقة القرآنية في نص هذه الصفحة — الهوية
  الظاهرة للمستخدم النهائي "رحّال" فقط (اسم النطاق الداخلي "أُفق"/الحلقة
  القرآنية لا يزالان موجودين في الكود والموديلات، لكن أُزيلا من النصوص
  المواجهة للمستخدم).

## لوحة المشارك + مؤشر رحلة النخبة + سجل النقاط

- **View:** `ParticipantDashboardView` (`participants/views.py`) — المسار
  `participants:dashboard`.
- **القالب:** `participants/participant_dashboard.html`.
- تعرض عملات المشارك: **الأميال والنقاط الشرائية فقط** (بطاقة "النقاط" أُزيلت
  من هذه اللوحة منذ 1.1.0، رغم أن الحقل `points` لا يزال محسوبًا ومستخدَمًا
  داخليًا — راجع [`points-system.md`](points-system.md))، وشريط موقعه ضمن مدى
  أميال البرنامج (طريقة `_build_range`).
- **مؤشر رحلة النخبة** (طريقة `get_elite_status`): رسالة نصية تُحسب لحظيًا (لا
  تُخزَّن) تخبر المشارك ببُعده عن حدّ أعلى 20 داخل بيئته، بلهجة شبابية عامية
  ورموز تعبيرية منذ تحديث 1.1.0. لا تكشف ترتيب أو نقاط أي مشارك آخر.
  > docstring الدالة يشير إلى ملف مواصفات `آلية_مؤشر_التبشير_برحلة_النخبة.md`
  > **غير موجود في المستودع** — راجع [`known-limitations.md`](known-limitations.md).
- **سجل النقاط الشخصي (جديد منذ 1.1.0):** أحدث 50 حركة من
  `participant.points_ledger_entries` تُعرض مدمجة في هذه الصفحة. راجع
  قسم "سجل النقاط" أدناه و[`points-system.md`](points-system.md).

## تحضير اللقاء الأسبوعي (مشرف البيئة)

- **View:** `SupervisorDashboardView` (`participants/views.py`) — المسار
  `participants:supervisor_dashboard`.
- **القالب:** `participants/supervisor_dashboard.html`.
- كشف كامل لمشاركي بيئة المشرف، عمودا "حضور مبكر" و"حضور اللقاء"، منتقي تاريخ.
- الحفظ POST واحد يُنشئ/يحدّث `MeetingAttendance` لكل مشارك ويطبّق دلتا النقاط
  عبر `apply_points_delta` (مصدر `MEETING_ATTENDANCE` في سجل النقاط).
- الموديل: `MeetingAttendance`. النقاط: 8 / 10. راجع [`points-system.md`](points-system.md).

## الحلقة القرآنية — حضور وإنجاز (يدوي، مؤقت)

- **View:** `QuranCircleAttendanceView` (`participants/views.py`) — المسار
  `participants:quran_circle_attendance`.
- **القالب:** `participants/quran_circle_attendance.html`.
- متاح لمشرف البيئة (بيئته فقط) وللمشرف العام/النظام (أي بيئة عبر `?group=<id>`).
- عمودان مستقلان: "حضور الحلقة" (3 نقاط) و"إنجاز الحلقة" (2 نقطة).
- الموديل: `CircleAttendance` (حقلا `attended` و`achieved`).
- **واجهة يدوية مؤقتة** بديلة لاستيراد إكسل من معلّم الحلقة **لم يُبنَ بعد** —
  راجع [`known-limitations.md`](known-limitations.md) وdocstring الـ View.

## فعالية الأسبوع (جديد منذ 1.1.0)

- **View:** `WeeklyActivityAttendanceView` (`participants/views.py`) — المسار
  `participants:weekly_activity_attendance`.
- **القالب:** `participants/weekly_activity_attendance.html`.
- نفس نمط الكشف + منتقي التاريخ المستخدم في الحلقة القرآنية، لكن بعلم حضور
  واحد فقط (بلا بُعد "إنجاز" منفصل) = **10 نقاط كاملة** عند التحضير.
- متاح لمشرف البيئة (بيئته فقط) وللمشرف العام/النظام (أي بيئة عبر `?group=<id>`).
- الموديل: `WeeklyActivityAttendance` — عملة نقاط مستقلة كليًا عن اللقاء
  والحلقة القرآنية، بمصدر سجل نقاط خاص بها (`WEEKLY_ACTIVITY`).

## استيراد المشاركين من إكسل (مشرف عام)

- **View:** `ParticipantImportView` (`participants/views.py`) — المسار
  `participants:import_participants`.
- **القالب:** `participants/import_participants.html` + ملف قالب
  `static/templates/participants_import_template.xlsx`.
- الورقة المتوقعة اسمها `المشاركون`، البيانات من الصف 3، 6 أعمدة (الاسم،
  رقم الهوية، اسم البيئة، المرحلة الدراسية، جوال المشارك، جوال ولي الأمر).
- لكل صف صالح: يُنشئ `User` (دور `PARTICIPANT`، كلمة المرور = رقم الهوية،
  `must_set_password=True`) + `Participant`. البيئة تُطابَق بالاسم الدقيق؛ بيئة
  غير موجودة ترفض الصف (لا تُنشأ بيئات تلقائيًا).
- التحقق: رقم هوية من 10 أرقام، غير مكرر، مرحلة دراسية معروفة.

## إضافة مشارك مفرد (جديد منذ 1.1.0)

- **View:** `AddParticipantView` (`participants/views.py`) — المسار
  `participants:add_participant`.
- **القالب:** `participants/add_participant.html`.
- بديل خفيف لاستيراد الإكسل لإضافة مشارك واحد عبر نموذج (`SingleParticipantForm`):
  الاسم الكامل، رقم الهوية، البيئة، المرحلة الدراسية، جوال المشارك، جوال ولي
  الأمر (الأخيران اختياريان).
- **مقصور على المشرف العام/النظام فقط** حاليًا (`test_func`) — راجع
  [`roles-and-permissions.md`](roles-and-permissions.md) للملاحظة حول فرع
  `GROUP_SUPERVISOR` الميت داخل `get_locked_group()`.
- نفس نمط إنشاء الحساب المستخدَم في الاستيراد: كلمة المرور الأولية = رقم
  الهوية، مع `must_set_password=True`.

## نقاط إضافية / خصم يدوي (جديد منذ 1.1.0)

- **View:** `ExtraPointsView` (`participants/views.py`) — المسار
  `participants:extra_points`.
- **القالب:** `participants/extra_points.html`.
- منح أو خصم نقاط يدوي (بين -1000 و+1000) لمشارك محدد، بسبب نصي إلزامي —
  يمر عبر `apply_points_delta` بمصدر `EXTRA`، فيُسجَّل في سجل النقاط تمامًا
  كأي حركة أخرى.
- **مقصور على المشرف العام/النظام فقط** (`test_func`) — تم سحب هذه الصلاحية
  من مشرف البيئة حسب `CHANGELOG.md` (الإصدار 1.1.0).

## سجل النقاط (جديد منذ 1.1.0)

- **View:** `PointsLedgerView` (`participants/views.py`) — المسار
  `participants:points_ledger`، عنصر "سجل النقاط" في شريط التنقل.
- **القالب:** `participants/points_ledger.html`.
- سجل تدقيق للقراءة فقط لكل حركة `PointsLedgerEntry` — مشرف البيئة يرى سجل
  بيئته فقط، والمشرف العام/النظام يرى كل السجل. محدود بأحدث 200 صف (بلا
  ترقيم صفحات حاليًا).
- يشمل أدوات بحث/فرز/تصفية متقدمة (بحث يمتد ليشمل اسم من منح النقاط أيضًا).
- **لا يشمل** حركات المتجر — راجع [`points-system.md`](points-system.md).

## لوحة المشرف العام

- **View:** `GeneralSupervisorDashboardView` (`participants/views.py`) —
  المسار `participants:general_supervisor_dashboard`.
- **القالب:** `participants/general_supervisor_dashboard.html`.
- **بطاقات KPI**: إجمالي المشاركين، مهام قيد المراجعة، طلبات متجر قيد التنفيذ.
- **4 رسوم بيانية** عبر **Chart.js 4** (يُحمَّل من CDN
  `cdn.jsdelivr.net/npm/chart.js@4`): مشاركون لكل بيئة، حالة تسليم آخر مهمة،
  متوسط النقاط لكل بيئة، طلبات المتجر حسب الحالة. البيانات تُمرَّر كـ
  `json_script`.
- **بطاقة طلبات استرجاع كلمة المرور** (موافقة/إعادة تعيين) — راجع
  [`authentication.md`](authentication.md).
- **زر تصفير نقاط المشاركين** (`action=reset_points`) — يحفظ لقطة ثم يصفّر
  `points` فقط.
- **رابط "سجل عمليات التصفير السابقة"** → `PointsSnapshotHistoryView`.

## لقطة تاريخية للنقاط

- **الكتابة:** فرع `reset_points` في طريقة
  `GeneralSupervisorDashboardView.post()` — `bulk_create` لصف
  `PointsResetSnapshot` لكل مشارك قبل التصفير، داخل `transaction.atomic()`.
- **العرض:** `PointsSnapshotHistoryView` (`participants/views.py`) — المسار
  `participants:points_snapshot_history`، القالب
  `participants/points_snapshot_history.html`. يسرد "أحداث" التصفير (بتقريب
  `reset_at` إلى الثانية عبر `TruncSecond`)، واختيار حدث يعرض ترتيب المشاركين
  وقتها تنازليًا حسب النقاط.
- **قيد:** هذه اللقطة **لا تُستخدَم تلقائيًا** في أي حساب لاحق لرحلة النخبة —
  راجع [`known-limitations.md`](known-limitations.md).

## جدول بيانات المشاركين

- **View:** `ParticipantsDataView` (`participants/views.py`) — المسار
  `participants:participants_data`.
- **القالب:** `participants/participants_data.html`.
- جدول للقراءة فقط، متاح للأدوار الإدارية الثلاثة. مشرف البيئة يرى بيئته فقط.
- بحث نصي + فلتر بيئات متعدد (checkboxes) + فرز أعمدة — **كله جافاسكربت في
  المتصفح** على بيانات محمّلة مسبقًا (لا طلبات خادم).
- عمودا الحضور التراكمي محسوبان بـ `annotate(Count(..., distinct=True))`.

## تصدير PDF لبيانات المشاركين

- **View:** `ParticipantsDataPDFExportView` (`participants/views.py`) —
  المسار `participants:participants_data_pdf`.
- **القالب:** `participants/participants_data_pdf.html` (مستند مستقل).
- يستخدم **WeasyPrint** (استيراد كسول داخل طريقة `get()`).
- النطاق: مثل `ParticipantsDataView` (مشرف البيئة مقفل على بيئته)، مع تصفية
  إضافية بأسماء البيئات المُمرَّرة `?group=<name>` (مكرَّرة) — يبنيها جافاسكربت
  في `participants_data.html` من الـ checkboxes المحددة. **البحث والفرز لا
  ينعكسان**؛ الترتيب دائمًا أبجدي بالاسم.
- الترويسة الرسمية خلفية `@page` من `static/images/letterhead.png`.
  > **⚠️ تحديث حالة القيد التشغيلي:** الملف `letterhead.png` **موجود الآن في
  > المستودع** (لم يكن موجودًا في مراجعة سابقة). مكتبات WeasyPrint النظامية
  > (Pango/Cairo/GObject) **مثبَّتة داخل صورة Docker الإنتاجية**
  > (`Dockerfile`)، فتصدير PDF يعمل في بيئة النشر؛ قد تبقى غير مثبَّتة على
  > جهاز تطوير محلي بعينه (خاصية بيئة، لا نقصًا في المستودع) — راجع
  > [`known-limitations.md`](known-limitations.md).

## المهام الأسبوعية

- **إنشاء ومراجعة (مشرف عام):** `WeeklyTaskReviewView`
  (`participants/views.py`) — المسار `participants:weekly_task_review`،
  القالب `participants/weekly_task_review.html`.
  - إنشاء مهمة جديدة: يختار المشرف **أكثر من صيغة مسموحة** الآن (خانات
    اختيار متعددة عبر `WeeklyTaskForm`، تشمل صيغة "نص مباشر" الجديدة) بدل
    صيغة واحدة سابقًا.
  - مراجعة التسليمات: قبول أو رفض.
    - **القبول**: خانة اختيار إضافية "مميزة ⭐" تمنح **12 نقطة** بدل **10**
      عند تفعيلها (`TaskSubmission.is_featured`) — **جديد منذ 1.1.0**.
    - **الرفض**: حقل "سبب الرفض" (`TaskSubmission.rejection_reason`) إلزامي
      الإدخال في الواجهة، يظهر لاحقًا للمشارك — **جديد منذ 1.1.0**.
- **أرشيف كل المهام (مشرف عام):** `TasksArchiveView`
  (`participants/views.py`) — المسار `participants:tasks_archive`،
  القالب `participants/tasks_archive.html`. لكل مهمة: من سلّم ومن لم يسلّم،
  وإعادة فتح تسليم لمشارك (`action=reopen`). فيه معاينة ملف داخل نافذة منبثقة.
- **رفع التسليم (مشارك):** `TaskSubmissionView` (`participants/views.py`) —
  المسار `participants:task_submission`، القالب
  `participants/task_submission_form.html`. تسليم واحد لكل مهمة (ما لم يُعَد
  فتحه).
  - **تعدد الصيغ + التسليم النصي (جديد منذ 1.1.0):** مهمة واحدة قد تقبل عدة
    صيغ معًا (مثل PDF أو صورة)؛ يكفي المشارك تقديم **واحدة منها فقط**. صيغة
    "نص مباشر" الجديدة لا تتطلب أي ملف — حقل `text_content` يُملأ بدلًا من
    ذلك. التحقق في `TaskSubmissionForm.clean()`
    (`participants/forms.py`) يرفض تقديم ملف ونص معًا، ويرفض عدم تقديم أي
    منهما، ويتحقق أن الصيغة المُقدَّمة (ملفًا كانت أو نصًا) ضمن صيغ المهمة
    المسموحة.
  - الصيغ المتاحة الآن: `pdf`=.pdf، `image`=.jpg/.jpeg/.png،
    `audio`=.mp3/.wav/.m4a، `video`=.mp4/.mov/.webm، **`text`=نص مباشر بلا
    ملف**.
  - الحدود: pdf 10MB، audio 15MB، video 50MB. الصور **لا تُرفض للحجم** —
    تُضغط في `TaskSubmission.save()` (`MAX_IMAGE_DIMENSION=1600`,
    `IMAGE_QUALITY=80`, JPEG).

## المتجر

- **المتجر (مشارك):** `StoreView` (`participants/views.py`) — المسار
  `participants:store`، القالب `participants/store.html`. شراء منتج بالنقاط
  الشرائية. التحقق من المخزون والرصيد **خادميًا** داخل `transaction.atomic()`
  مع `select_for_update` (حماية من السباق). ينشئ `StoreOrder` بـ
  `price_at_order` كلقطة سعر.
- **إدارة المتجر (مشرف عام):** `StoreManagementView` (`participants/views.py`)
  — المسار `participants:store_management`، القالب
  `participants/store_management.html`.
  - إضافة/تعديل/حذف منتج (`StoreProductForm`).
  - **حذف منتج (محدَّث)**: منتج له طلب **قيد التنفيذ أو مكتمل** يُرفض حذفه
    (`ProtectedError` → رسالة عربية، لا صفحة 500) — كما كان سابقًا. لكن منتج
    طلباته **كلها مسترجَعة فقط** أصبح **قابلًا للحذف الآن**: الطلبات
    المسترجَعة تُحذَف أولًا ثم المنتج، داخل نفس المعاملة — راجع
    [`models.md`](models.md).
  - إكمال طلب (`action=complete`) واسترجاع طلب (`action=refund`). الاسترجاع
    يعيد `purchase_points` والمخزون معًا، وحارس `status != REFUNDED` يمنع
    الاسترجاع المزدوج.
- **قاعدة صارمة في الكود:** المتجر يعدّل `purchase_points` **فقط** ولا يستدعي
  `apply_points_delta` إطلاقًا (تعليق أعلى كلاس `StoreView` في
  `participants/views.py`).

## شريط التنقل والإشعارات

- كل صفحات ما بعد الدخول ترث من `participants/templates/participants/app_base.html`
  وتعرض شريط تنقل (`navbar_items`) حسب الدور، تبنيه دالة `build_navbar`
  (`participants/views.py`).
- **عدّادات إشعارات (جديد منذ 1.1.0):** دالة `get_notification_counts(user)`
  تحسب عدد شارة (badge) لكل عنصر شريط تنقل، **حيًا في كل طلب** (بلا أي تتبّع
  "تمت القراءة" أو حقل موديل جديد):
  - **للمشرف العام/النظام:** عدد تسليمات المهمة الأخيرة قيد المراجعة، وعدد
    طلبات المتجر قيد التنفيذ.
  - **للمشارك:** شارة "المهام" إذا لم يُسلِّم المهمة الحالية بعد أو إن أُعيد
    فتحها له، وشارة "المتجر" لعدد طلباته التي تغيّرت حالتها (اكتمال أو
    استرجاع) خلال آخر 24 ساعة.

## تغيير كلمة مرور المشرف

- **View:** `SupervisorPasswordChangeView` (`accounts/views.py`) — المسار
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
| `weekly-activity/` **(جديد)** | `participants:weekly_activity_attendance` |
| `import/` | `participants:import_participants` |
| `add-participant/` **(جديد)** | `participants:add_participant` |
| `general-supervisor/dashboard/` | `participants:general_supervisor_dashboard` |
| `points-snapshots/` | `participants:points_snapshot_history` |
| `extra-points/` **(جديد)** | `participants:extra_points` |
| `points-ledger/` **(جديد)** | `participants:points_ledger` |
| `data/` | `participants:participants_data` |
| `data/export-pdf/` | `participants:participants_data_pdf` |
| `tasks/review/` | `participants:weekly_task_review` |
| `tasks/archive/` | `participants:tasks_archive` |
| `tasks/submit/` | `participants:task_submission` |
| `store/` | `participants:store` |
| `store/management/` | `participants:store_management` |
