# أُفق — رحّال

اقرأ هذا الملف بالإنجليزية: [README.md](README.md)

منصة إدارة **برنامج أُفق التنموي** (برنامج قيمي مهاري لمدة 14 أسبوعًا) التابع
لـ**جمعية صقيل لتنمية الشباب**. يسجّل المشرفون الحضور اليومي والأسبوعي
ويراجعون مهمة أسبوعية، ويكسب المشاركون عملة مكافآت ثلاثية ويتقدّمون نحو
الترشح لـ"رحلة النخبة". المنتج الموجَّه للمستخدم النهائي يحمل هوية **رحّال**
— اسم النطاق الداخلي "أُفق" وميزات داخلية مثل الحلقة القرآنية باقيان في أسماء
الكود والموديلات، لكن أُزيلا من النصوص المواجهة للمشارك.

كامل الواجهة ولغة المجال **عربية** (`ar-sa`، `Asia/Riyadh`، اتجاه من اليمين
لليسار). النسخة الإنجليزية في [`README.md`](README.md). التوثيق التقني
التفصيلي في مجلد [`docs/`](docs/)، وسجل الإصدارات في
[`CHANGELOG.md`](CHANGELOG.md)..

## نظرة عامة

- كل مشارك ينتمي إلى **بيئة** (`Group`)، ويمكن إسناد **مشرف بيئة واحد أو
  أكثر** لها (`Group.supervisor` أصبح حقل علاقة متعدد-لمتعدد).
- مع تسجيل المشرفين للحضور ومراجعة المهام، يكسب المشارك عملة ثلاثية، تُحوَّل
  كلها معًا عبر دالة واحدة `apply_points_delta` (`participants/views.py`):

  | العملة | الحقل | القاعدة |
  |--------|------|---------|
  | النقاط | `points` | عملة الترتيب. **قابلة للتصفير** على مستوى البرنامج (تُحفظ لقطة أولًا). لم تعد تُعرض كبطاقة مستقلة في لوحة المشارك، لكنها لا تزال محسوبة ومستخدَمة في ترتيب مؤشر رحلة النخبة. |
  | الأميال | `miles` | `دلتا النقاط × 10`، تُطبَّق معًا. **لا تُصفَّر أبدًا**. |
  | النقاط الشرائية | `purchase_points` | تُصرف في المتجر. **لا يمسّها إجراء التصفير**؛ المتجر هو الميزة الوحيدة التي تعدّلها مباشرة، متجاوزًا `apply_points_delta`. |

  كل رصيد محدود بحد أدنى صفر.

- **مصادر النقاط** (قيَم مأخوذة حرفيًا من `participants/views.py`):

  | المصدر | النقاط | ملاحظة |
  |--------|--------|--------|
  | اللقاء الأسبوعي | 8 (+2 مكافأة الحضور المبكر) | `MeetingAttendance` |
  | الحلقة القرآنية | 3 حضور + 2 إنجاز (مستقلان) | `CircleAttendance` |
  | فعالية الأسبوع | 10 (كاملة، علم حضور واحد) | `WeeklyActivityAttendance` — جديدة |
  | قبول المهمة الأسبوعية | 10، أو 12 إن كانت "مميزة" (⭐) | `TaskSubmission.is_featured` — جديد |
  | نقاط إضافية / خصم يدوي | من -1000 إلى +1000، بسبب إلزامي | `ExtraPointsView`، للمشرف العام/النظام فقط — جديد |

  كل واحدة من هذه (باستثناء المتجر) تكتب أيضًا صفًا في سجل تدقيق
  `PointsLedgerEntry`، يُعرض لكل بيئة أو على مستوى البرنامج في **سجل النقاط**
  الجديد (`participants:points_ledger`). التفاصيل الكاملة:
  [`docs/points-system.md`](docs/points-system.md).

## الأدوار الأربعة

معرّفة في `accounts/models.py` (`Role`):

| الدور | القيمة | ملخص |
|------|--------|------|
| مشارك | `participant` | يدخل بـ **رقم الهوية + كلمة المرور**. لوحة شخصية (فيها سجل نقاط مدمج ومؤشر رحلة النخبة)، رفع المهمة الأسبوعية، المتجر. |
| مشرف بيئة | `group_supervisor` | يسجّل حضور اللقاء والحلقة القرآنية وفعالية الأسبوع **لبيئته/بيئاته فقط**، ويستعرض بيانات بيئته وسجل نقاطها. |
| مشرف عام | `general_supervisor` | على مستوى البرنامج: استيراد إكسل، استمارة إضافة مشارك مفرد، المهام الأسبوعية، إدارة المتجر، منح نقاط إضافية يدويًا، تصفير النقاط، سجل النقاط الكامل، كل البيئات. |
| مشرف النظام | `superadmin` | نفس صلاحيات المشرف العام في كل الـ Views، إضافةً إلى أدمن Django (`is_staff`/`is_superuser`). |

المصفوفة الكاملة للصلاحيات:
[`docs/roles-and-permissions.md`](docs/roles-and-permissions.md).

## التقنيات المستخدمة

من `pyproject.toml` (‏`requires-python = ">=3.12"`):

- [Django](https://www.djangoproject.com/) `>=6.0,<6.1`
- [django-unfold](https://unfoldadmin.com/) `>=0.104.1` — واجهة أدمن مُثمَّنة
- [django-environ](https://django-environ.readthedocs.io/) `>=0.14` — إعدادات من `.env`
- [openpyxl](https://openpyxl.readthedocs.io/) `>=3.1.5` — استيراد المشاركين من إكسل
- [Pillow](https://python-pillow.org/) `>=12.3` — ضغط الصور المرفوعة
- [WeasyPrint](https://weasyprint.org/) `>=70.0` — تصدير PDF لجدول المشاركين
- [gunicorn](https://gunicorn.org/) `>=26.2.0` — خادم WSGI إنتاجي (نشر Docker)
- [psycopg2-binary](https://www.psycopg.org/) `>=2.9.12` — مشغّل PostgreSQL (نشر Docker)
- [uv](https://docs.astral.sh/uv/) — إدارة الاعتماديات والبيئة
- **Chart.js 4** — يُحمَّل من CDN في لوحة المشرف العام فقط
- **قاعدة البيانات:** **SQLite** (`db.sqlite3`) للتطوير المحلي (الافتراضي،
  بلا تغيير)؛ **PostgreSQL 16** في إعداد Docker الإنتاجي
  (`USE_POSTGRES=True`). راجع [الرفع والتحديث](#الرفع-والتحديث).

## التشغيل المحلي للتطوير

```bash
# 1. تثبيت الاعتماديات في بيئة معزولة مُدارة
uv sync

# 2. إعداد متغيرات البيئة
cp .env.example .env
#    عدّل .env — SECRET_KEY مطلوب؛ DEBUG افتراضيًا False

# 3. تطبيق الهجرات
uv run python manage.py migrate

# 4. إنشاء حساب إداري (يُضبط الدور superadmin تلقائيًا)
uv run python manage.py createsuperuser

# 5. تشغيل خادم التطوير
uv run python manage.py runserver
```

- التطبيق: <http://127.0.0.1:8000/>
- الأدمن: <http://127.0.0.1:8000/admin/>

### متغيرات البيئة

التطوير المحلي يحتاج المتغيرَين الأولين فقط؛ البقية تخص إعداد Docker
الإنتاجي (`.env.docker`، راجع [الرفع والتحديث](#الرفع-والتحديث)).

| المتغير | مطلوب؟ | الافتراضي | الوصف |
|--------|--------|-----------|-------|
| `SECRET_KEY` | نعم | — | مفتاح Django السري |
| `DEBUG` | لا | `False` | وضع التصحيح |
| `ALLOWED_HOSTS` | لا (إنتاج: نعم) | `[]` | قائمة نطاقات مفصولة بفواصل |
| `CSRF_TRUSTED_ORIGINS` | لا (إنتاج: نعم) | `[]` | نطاقات HTTPS موثوقة مفصولة بفواصل |
| `USE_POSTGRES` | لا | `False` | `True` يبدّل `DATABASES` إلى PostgreSQL |
| `DB_ENGINE` | لا (إنتاج: مع `USE_POSTGRES`) | `django.db.backends.postgresql` | محرّك قاعدة البيانات |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` | مع `USE_POSTGRES` | — | معطيات اتصال PostgreSQL |
| `DB_PORT` | لا | `5432` | منفذ PostgreSQL |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | مع خدمة `db` في Docker | — | يقرأها تصوير `postgres` الرسمي نفسه عند أول تشغيل؛ يجب أن تطابق `DB_NAME`/`DB_USER`/`DB_PASSWORD` تمامًا (نفس القيَم بأسماء متغيرات مختلفة — تكرار يفرضه تصميم تلك الصورة نفسها). |

الملفات `.env`، `.env.docker`، `db.sqlite3`، و`media/` مستثناة من Git.

### أوامر شائعة

```bash
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py check
uv run python manage.py test                 # كل الاختبارات
uv run python manage.py test participants     # تطبيق واحد
```

يحتوي `accounts/tests.py` و`participants/tests.py` اختبارات فعلية (تدفق كلمة
مرور المشارك الأول، دلتا نقاط الحلقة القرآنية، سجل لقطات تصفير النقاط، فروع
إدارة المتجر، نطاق تصدير PDF حسب الدور) — لم يعودا ملفَّين فارغَين.

> **ملاحظة WeasyPrint:** نقطة تصدير PDF
> (`/participants/data/export-pdf/`) تحتاج مكتبات النظام التي يعتمد عليها
> WeasyPrint (‏Pango، Cairo، GObject). هذه المكتبات **مثبَّتة داخل صورة
> Docker الإنتاجية** (`Dockerfile`)، فتصدير PDF يعمل هناك؛ جهاز تطوير محلي
> بلا هذه المكتبات ستتعطل فيه هذه النقطة فقط (الاستيراد كسول داخل الـ View
> فبقية الموقع لا تتأثر). ملف `static/images/letterhead.png` (الترويسة
> الرسمية المستخدمة كخلفية صفحات الـ PDF) **موجود في المستودع**. راجع
> [`docs/known-limitations.md`](docs/known-limitations.md).

## الرفع والتحديث

يحوي المستودع الآن إعداد نشر إنتاجي كامل عبر Docker:

| الملف | الدور |
|-------|-------|
| `Dockerfile` | يعتمد `python:3.12-slim`، يثبّت مكتبات النظام التي تحتاجها WeasyPrint وpsycopg2، ويثبّت الاعتماديات عبر `uv sync --frozen --no-dev`. |
| `docker-compose.yml` | أربع خدمات: `db` (PostgreSQL 16)، `web` (عبر Gunicorn)، `nginx` (بروكسي عكسي + تقديم static/media)، `certbot` (تجديد شهادات Let's Encrypt كل 12 ساعة). |
| `docker/entrypoint.sh` | ينتظر جاهزية PostgreSQL، يُشغّل `migrate` ثم `collectstatic`، ثم يُشغّل Gunicorn (3 عمّال، مهلة 120 ثانية). |
| `docker/nginx/nginx.conf` | إعداد HTTP فقط (Bootstrap) — يُستخدم قبل صدور أول شهادة TLS (يخدم تحدي ACME عبر HTTP-01). |
| `docker/nginx/nginx-ssl.conf` | إعداد HTTPS الكامل — يُفعَّل يدويًا بعد صدور أول شهادة. |
| `docker/certbot-init.sh` | سكربت تشغيل يدوي لمرة واحدة لإصدار أول شهادة Let's Encrypt. |
| `.env.docker.example` | قالب متغيرات بيئة الإنتاج — انسخه إلى `.env.docker` واملأ القيَم الحقيقية قبل `docker compose up`. |

```bash
cp .env.docker.example .env.docker
#    عدّل .env.docker بقيَم حقيقية: SECRET_KEY، ALLOWED_HOSTS، معطيات قاعدة البيانات...
docker compose up -d --build
#    أول مرة فقط، بعد أن تصبح المكدّسة متاحة عبر HTTP العادي:
./docker/certbot-init.sh
#    ثم فعّل HTTPS:
cp docker/nginx/nginx-ssl.conf docker/nginx/nginx.conf
docker compose up -d --force-recreate nginx
```

ملاحظات:
- ينتهي TLS عند Nginx؛ Gunicorn يرى HTTP عادي فقط عبر الشبكة الداخلية
  لـ Docker. `SECURE_PROXY_SSL_HEADER` في `config/settings.py` يثق بترويسة
  `X-Forwarded-Proto` القادمة من Nginx كي لا يرفض تحقق CSRF في Django طلبات
  POST عبر HTTPS.
- تجديد الشهادة تلقائي، لكن **Nginx لا يُعاد تحميله تلقائيًا بعد التجديد** —
  راجع [`docs/known-limitations.md`](docs/known-limitations.md).
- الدليلان المشار إليهما سابقًا بـ `دليل_رفع_الاستضافة.md` و
  `دليل_تحديث_الموقع.md` لا يزالان **غير مُتتبَّعين في هذا المستودع**؛ إن كانا
  موجودين لدى صاحب المشروع خارجيًا، يُستحسن إضافتهما إلى `docs/` ليصبحا
  مرجعًا فعليًا.

التفصيل التقني الكامل: [`docs/architecture.md`](docs/architecture.md#النشر-الإنتاجي-docker).

## بنية المشروع

```
config/         حزمة مشروع Django (الإعدادات، المسارات، wsgi/asgi)
accounts/       الهوية والمصادقة — User مخصص، Role، backend الدخول، middleware
participants/   البيانات البرنامجية — Group، Participant، الحضور (ثلاثة أنواع)، المهام، المتجر، سجل النقاط، اللوحات
templates/      قوالب مشتركة (صفحة الهبوط العامة)
static/         الشعار، ترويسة PDF، خط Tajawal، وملف قالب استيراد الإكسل
docker/         النشر الإنتاجي: سكربت التشغيل، إعدادات Nginx، سكربت تهيئة الشهادة
docs/           التوثيق التقني التفصيلي (بالعربية)
```

اتجاه الاعتماد دائمًا `participants → accounts` ولا عكس.

## التوثيق

ابدأ من [`docs/README.md`](docs/README.md). الأهم لمن يتسلّم الصيانة:
**[`docs/known-limitations.md`](docs/known-limitations.md)** — يحوي الآن أيضًا
قسمًا مخصصًا يوثّق صراحة كل ما تغيّر منذ مراجعات سابقة لهذا التوثيق، بدل حذف
تلك الملاحظات بصمت. راجع [`CHANGELOG.md`](CHANGELOG.md) لسجل الإصدارات.

## الترخيص / الجهة المالكة

طُوّر لصالح **جمعية صقيل لتنمية الشباب** لأجل برنامج أُفق التنموي. لا يوجد ملف
ترخيص مفتوح المصدر في المستودع؛ كل الحقوق محفوظة للجمعية ما لم يُذكر خلاف ذلك.
