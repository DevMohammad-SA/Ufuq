# البنية المعمارية

> كل ما في هذا الملف مستخرج من فحص الكود الفعلي في المستودع بتاريخ التوثيق،
> وليس من افتراضات. عند أي تعارض بين هذا الملف والكود، **الكود هو المرجع**.

## نظرة عامة

مشروع **أُفق** تطبيق Django واحد (`config`) يضم تطبيقين اثنين فقط:

| التطبيق | المسؤولية | الاعتماد |
|---------|-----------|----------|
| `accounts` | الهوية والمصادقة فقط: موديل `User` المخصص، `Role`، backend الدخول، middleware إجبار كلمة المرور، طلبات استرجاع كلمة المرور. | لا يعتمد على `participants` إطلاقًا. |
| `participants` | كل البيانات البرنامجية: البيئات، المشاركون، الحضور، المهام، المتجر، لوحات التحكم، التقارير. | يعتمد على `accounts` (يستورد `Role` و`User` و`PasswordResetRequest`). |

**اتجاه الاعتماد دائمًا `participants → accounts`**، ولا يوجد استيراد عكسي على
مستوى الوحدة. الاستثناء الوحيد: `accounts/views.py`
(`SupervisorPasswordChangeView.get_context_data`) يستورد `build_navbar` من
`participants.views` **داخل الدالة** (استيراد كسول) لتفادي اعتماد دائري على
مستوى الوحدة — موثّق بتعليق في الكود (`accounts/views.py:84-92`).

المشروع أيضًا يحوي:
- `config/` — حزمة إعدادات Django (`settings.py`, `urls.py`, `wsgi.py`, `asgi.py`).
- `templates/home.html` — صفحة هبوط عامة (بدون تسجيل دخول).
- `static/` — الشعار وملف قالب الاستيراد `participants_import_template.xlsx`.

## فلسفة الفصل: `User` مقابل `Participant`

هذا القرار المعماري الأهم في المشروع:

- **`accounts.User`** (`accounts/models.py:49`) يرث من
  `AbstractBaseUser` + `PermissionsMixin`. يحمل **الهوية فقط**:
  `national_id`, `username`, `role`, `full_name`, أعلام الحساب
  (`is_active`, `is_staff`, `must_set_password`). `USERNAME_FIELD = "username"`.
- **`participants.Participant`** (`participants/models.py:85`) علاقة
  **`OneToOneField`** مع `User` (`on_delete=CASCADE`). يحمل **البيانات
  البرنامجية فقط**: البيئة (`group`)، العملات الثلاث (`miles`, `points`,
  `purchase_points`)، أرقام الجوال، المرحلة الدراسية.

الفائدة: تطبيق `accounts` يبقى نقيًا للمصادقة، ويمكن أن يوجد `User` من أي دور
دون أن يكون له `Participant` (المشرفون لا `Participant` لهم). المشاركون فقط
لديهم صف `Participant` مرتبط.

الوصول من مشارك مسجّل دخوله لبياناته البرنامجية يتم عبر
`request.user.participant` (انظر مثلًا `ParticipantDashboardView`,
`participants/views.py:260`).

## ربط مشرف البيئة ببيئته

لا يوجد حقل على `User` يشير للبيئة. بدلًا من ذلك **`Group.supervisor`**
(`participants/models.py:76`) حقل `ForeignKey` من `Group` إلى `User` بدون
`related_name`، لذا العلاقة العكسية من `User` هي `group_set` الافتراضية من
Django. كل الـ Views التي تحتاج بيئة مشرف البيئة تستخدم
`self.request.user.group_set.first()` (انظر
`SupervisorDashboardView.get_group`, `participants/views.py:394`).

`Group.supervisor` عليه `limit_choices_to={"role": Role.GROUP_SUPERVISOR}` —
لكن هذا قيد على واجهة الأدمن فقط، لا يُفرض على مستوى قاعدة البيانات.

## طبقة العرض

- كل صفحات ما بعد الدخول (باستثناء صفحات `accounts`) ترث من
  `participants/templates/participants/app_base.html`، الذي يرسم شريط تنقل
  (navbar) حسب الدور من قائمة `navbar_items` في السياق.
- `navbar_items` تُبنى بدالة `build_navbar(user, active_key)`
  (`participants/views.py:130`) — دالة عرض بحتة بلا منطق أعمال.
- صفحات المصادقة (`accounts/templates/accounts/`) ترث من
  `accounts/templates/base.html` (تصميم شاشة دخول منفصل)، ما عدا
  `change_password.html` التي ترث من `app_base.html` لأنها ضمن navbar
  المشرفين.
- قالب واحد مستقل تمامًا لا يرث شيئًا: `participants_data_pdf.html`
  (مستند HTML كامل يُحوّل إلى PDF عبر WeasyPrint).

## تحويل الحضور إلى نقاط

الموديلات (`CircleAttendance`, `MeetingAttendance`, `TaskSubmission`) **لا
تطبّق أي نقاط في `save()` ولا عبر signals**. تحويل الحضور إلى عملات يحدث
**صراحةً في الـ Views** عبر دالة واحدة `apply_points_delta`
(`participants/views.py:211`) وقت إرسال المشرف للنموذج. كل View يحسب الفرق
(`new_points - old_points`) ويطبّقه مرة واحدة فقط، فإعادة إرسال نفس الكشف
لا تضاعف النقاط (idempotent).

راجع [`points-system.md`](points-system.md) للتفاصيل الرقمية.

## قاعدة البيانات

الإعداد المُودَع في المستودع (`config/settings.py:83-88`) يستخدم **SQLite**
(`db.sqlite3`). **لا يوجد في المستودع أي إعداد PostgreSQL أو Docker أو Nginx
أو Gunicorn.** راجع [`known-limitations.md`](known-limitations.md).

## سجل الهجرات (Migrations)

- `accounts`: 3 هجرات (آخرها `0003_user_must_set_password_passwordresetrequest`).
- `participants`: 9 هجرات (آخرها `0009_pointsresetsnapshot`).
