# البنية المعمارية

> كل ما في هذا الملف مستخرج من فحص الكود الفعلي في المستودع بتاريخ آخر
> مراجعة (2026-09-14)، وليس من افتراضات. عند أي تعارض بين هذا الملف والكود،
> **الكود هو المرجع**. المراجع أدناه بصيغة اسم الدالة/الكلاس بدل رقم السطر،
> لأن أرقام الأسطر تتغيّر مع كل تعديل.

## نظرة عامة

مشروع **أُفق** تطبيق Django واحد (`config`) يضم تطبيقين اثنين فقط:

| التطبيق | المسؤولية | الاعتماد |
|---------|-----------|----------|
| `accounts` | الهوية والمصادقة فقط: موديل `User` المخصص، `Role`، backend الدخول، middleware إجبار كلمة المرور، طلبات استرجاع كلمة المرور. | لا يعتمد على `participants` على مستوى الوحدة (باستثناء استيراد كسول واحد داخل دالة، انظر أدناه). |
| `participants` | كل البيانات البرنامجية: البيئات، المشاركون، الحضور بأنواعه الثلاثة، المهام، المتجر، سجل النقاط، لوحات التحكم، التقارير. | يعتمد على `accounts` (يستورد `Role` و`User` و`PasswordResetRequest`). |

**اتجاه الاعتماد دائمًا `participants → accounts`**، ولا يوجد استيراد عكسي على
مستوى الوحدة. الاستثناء الوحيد: `accounts/views.py`
(طريقة `SupervisorPasswordChangeView.get_context_data`) يستورد `build_navbar`
من `participants.views` **داخل الدالة** (استيراد كسول) لتفادي اعتماد دائري
على مستوى الوحدة — موثّق بتعليق في الكود.

المشروع أيضًا يحوي:
- `config/` — حزمة إعدادات Django (`settings.py`, `urls.py`, `wsgi.py`, `asgi.py`).
- `templates/home.html` — صفحة هبوط عامة (بدون تسجيل دخول).
- `static/` — الشعار، الترويسة الرسمية (`letterhead.png`)، خط Tajawal، وملف
  قالب الاستيراد `participants_import_template.xlsx`.
- `docker/` + `Dockerfile` + `docker-compose.yml` — إعداد نشر إنتاجي كامل
  (Postgres + Gunicorn + Nginx + Certbot). راجع
  [قسم النشر الإنتاجي](#النشر-الإنتاجي-docker) أدناه — هذا **جديد** منذ آخر
  مراجعة لهذا الملف؛ سابقًا لم يكن يوجد أي إعداد نشر في المستودع.

## فلسفة الفصل: `User` مقابل `Participant`

هذا القرار المعماري الأهم في المشروع:

- **`accounts.User`** (`accounts/models.py`) يرث من `AbstractBaseUser` +
  `PermissionsMixin`. يحمل **الهوية فقط**: `national_id`, `username`, `role`,
  `full_name`, أعلام الحساب (`is_active`, `is_staff`, `must_set_password`).
  `USERNAME_FIELD = "username"`.
- **`participants.Participant`** (`participants/models.py`) علاقة
  **`OneToOneField`** مع `User` (`on_delete=CASCADE`). يحمل **البيانات
  البرنامجية فقط**: البيئة (`group`)، العملات الثلاث (`miles`, `points`,
  `purchase_points`)، أرقام الجوال، المرحلة الدراسية.

الفائدة: تطبيق `accounts` يبقى نقيًا للمصادقة، ويمكن أن يوجد `User` من أي دور
دون أن يكون له `Participant` (المشرفون لا `Participant` لهم). المشاركون فقط
لديهم صف `Participant` مرتبط.

الوصول من مشارك مسجّل دخوله لبياناته البرنامجية يتم عبر
`request.user.participant` (انظر مثلًا طريقة
`ParticipantDashboardView.get_context_data`).

## ربط مشرف البيئة ببيئته/بيئاته

> **⚠️ تغيّر معماري مهم**: هذا القسم كان يصف `Group.supervisor` كـ
> `ForeignKey` بمشرف واحد لكل بيئة. الحقل **تحوّل إلى `ManyToManyField`** —
> راجع [`models.md`](models.md) للتفاصيل الكاملة.

لا يوجد حقل على `User` يشير للبيئة. بدلًا من ذلك **`Group.supervisor`**
(`participants/models.py`) حقل `ManyToManyField` من `Group` إلى `User` بدون
`related_name`، فيمكن لبيئة واحدة أن يكون لها أكثر من مشرف، ويمكن نظريًا
لمشرف أن يظهر في أكثر من بيئة. العلاقة العكسية من `User` لا تزال الاسم
الافتراضي `group_set` (نفس الاسم الذي كانت تنتجه `ForeignKey` غير المسمّاة).
كل الـ Views التي تحتاج بيئة مشرف البيئة تستخدم
`self.request.user.group_set.first()` (انظر مثلًا طريقة
`SupervisorDashboardView.get_group`) — وهذا يُرجع الآن **أول بيئة فقط** من
عدة بيئات محتملة، لا بالضرورة البيئة الوحيدة.

`Group.supervisor` عليه `limit_choices_to={"role": Role.GROUP_SUPERVISOR}` —
لكن هذا قيد على واجهة الأدمن فقط، لا يُفرض على مستوى قاعدة البيانات. أدمن
Django (`GroupAdmin` في `participants/admin.py`) يستخدم `filter_horizontal`
لعرض هذا الحقل كقائمة اختيار مزدوجة تلائم علاقة متعددة-إلى-متعددة.

## طبقة العرض

- كل صفحات ما بعد الدخول (باستثناء صفحات `accounts`) ترث من
  `participants/templates/participants/app_base.html`، الذي يرسم شريط تنقل
  (navbar) حسب الدور من قائمة `navbar_items` في السياق، مع **عدّادات إشعارات**
  (badge) لكل عنصر — راجع [`features.md`](features.md#شريط-التنقل-والإشعارات).
- `navbar_items` تُبنى بدالة `build_navbar(user, active_key)`
  (`participants/views.py`) — دالة عرض بحتة بلا منطق أعمال، تستدعي داخليًا
  `get_notification_counts(user)` لجلب أعداد الإشعارات لكل مفتاح عنصر.
- صفحات المصادقة (`accounts/templates/accounts/`) ترث من
  `accounts/templates/base.html` (تصميم شاشة دخول منفصل)، ما عدا
  `change_password.html` التي ترث من `app_base.html` لأنها ضمن navbar
  المشرفين.
- قالب واحد مستقل تمامًا لا يرث شيئًا: `participants_data_pdf.html`
  (مستند HTML كامل يُحوّل إلى PDF عبر WeasyPrint).

## تحويل الحضور إلى نقاط

الموديلات (`CircleAttendance`, `MeetingAttendance`, `WeeklyActivityAttendance`,
`TaskSubmission`) **لا تطبّق أي نقاط في `save()` ولا عبر signals**. تحويل
الحضور إلى عملات يحدث **صراحةً في الـ Views** عبر دالة واحدة
`apply_points_delta` (`participants/views.py`) وقت إرسال المشرف للنموذج. كل
View يحسب الفرق (`new_points - old_points`) ويطبّقه مرة واحدة فقط، فإعادة
إرسال نفس الكشف لا تضاعف النقاط (idempotent). منذ 1.1.0، كل استدعاء لـ
`apply_points_delta` يكتب أيضًا صفًا في `PointsLedgerEntry` (سجل تدقيق مركزي) —
راجع [`points-system.md`](points-system.md) للتفاصيل الرقمية والتقنية الكاملة.

## قاعدة البيانات

> **⚠️ تغيّر مهم منذ آخر مراجعة**: هذا القسم كان يذكر أن المستودع لا يحتوي أي
> إعداد PostgreSQL. هذا لم يعد صحيحًا — راجع أدناه.

`config/settings.py` يدعم قاعدتي بيانات حسب متغيّر بيئة واحد:

- **التطوير المحلي** (لا `USE_POSTGRES` في `.env`، أو `USE_POSTGRES=False`):
  **SQLite** (`db.sqlite3`) — السلوك الافتراضي الأصلي، بلا تغيير.
- **الإنتاج عبر Docker** (`USE_POSTGRES=True` في `.env.docker`): **PostgreSQL**،
  بمعطيات الاتصال (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`)
  تُقرأ من نفس ملف البيئة عبر django-environ.

راجع [النشر الإنتاجي](#النشر-الإنتاجي-docker) أدناه.

## النشر الإنتاجي (Docker)

> **جديد بالكامل** — لم يكن موجودًا في مراجعة سابقة لهذا الملف.

المستودع يحوي الآن حزمة نشر إنتاجية كاملة:

| الملف | الدور |
|-------|-------|
| `Dockerfile` | صورة تعتمد `python:3.12-slim`، تثبّت مكتبات النظام التي يحتاجها WeasyPrint (Pango/Cairo/GObject) ومكتبات بناء psycopg2، وتُدير الاعتماديات عبر `uv sync --frozen --no-dev`. |
| `docker-compose.yml` | يُشغّل 4 خدمات: `db` (Postgres 16)، `web` (التطبيق عبر Gunicorn)، `nginx` (بروكسي عكسي + تقديم static/media)، `certbot` (تجديد شهادات Let's Encrypt كل 12 ساعة). |
| `docker/entrypoint.sh` | ينتظر جاهزية PostgreSQL، يُشغّل `migrate` ثم `collectstatic`، ثم يُشغّل Gunicorn (3 عمّال، مهلة 120 ثانية). |
| `docker/nginx/nginx.conf` | إعداد HTTP فقط (Bootstrap) — يُستخدم أول مرة قبل صدور أي شهادة، لتقديم تحدي ACME عبر HTTP-01. |
| `docker/nginx/nginx-ssl.conf` | إعداد HTTPS الكامل — يُفعَّل يدويًا بعد صدور أول شهادة (استبدال `nginx.conf` به). |
| `docker/certbot-init.sh` | سكربت تشغيل يدوي لمرة واحدة لإصدار أول شهادة Let's Encrypt (النطاق `rahhal.saqeel.org.sa`)، قبل التحوّل لإعداد HTTPS. |
| `.env.docker.example` | قالب متغيرات بيئة الإنتاج — `DEBUG=False`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `USE_POSTGRES=True` ومعطيات Postgres. |

نقاط تقنية مهمة:
- `config/settings.py` يقرأ `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`
  لأن TLS يُنهى عند Nginx لا عند Gunicorn — بدونه يفشل تحقق CSRF على أي POST
  عبر HTTPS خلف البروكسي.
  `ALLOWED_HOSTS` و`CSRF_TRUSTED_ORIGINS` أصبحا قابلين للتهيئة عبر البيئة
  (كانا فارغين ثابتًا `[]`).
- `STATIC_ROOT` أُضيف (`BASE_DIR / "staticfiles"`) ليعمل `collectstatic` داخل
  الحاوية — لا يؤثر على التطوير المحلي إطلاقًا لأن لا شيء يستدعي
  `collectstatic` محليًا.
- التبعيات `gunicorn` و`psycopg2-binary` أُضيفتا إلى `pyproject.toml` خصيصًا
  لهذا المسار الإنتاجي.

راجع [`known-limitations.md`](known-limitations.md) لما تبقّى غير مؤتمت في
هذا الإعداد (تجديد الشهادة لا يُعيد تحميل Nginx تلقائيًا).

## سجل الهجرات (Migrations)

- `accounts`: 3 هجرات (آخرها `0003_user_must_set_password_passwordresetrequest`).
- `participants`: 15 هجرة (آخرها `0015_tasksubmission_text_content_and_more`) —
  ارتفع العدد من 9 في مراجعة سابقة نتيجة الميزات المضافة في الإصدار 1.1.0
  (سجل النقاط، فعالية الأسبوع، سبب الرفض، التسليم المميز، تعدد صيغ المهام
  والتسليم النصي).
