# أُفق — رحّال

منصة إدارة **برنامج أُفق التنموي** (برنامج قيمي مهاري لمدة 14 أسبوعًا) التابع
لـ**جمعية صقيل لتنمية الشباب**. يسجّل المشرفون الحضور اليومي والأسبوعي،
ويكسب المشاركون عملة مكافآت ثلاثية ويتقدّمون نحو الترشح لـ"رحلة النخبة".
المنتج الموجَّه للمستخدم النهائي يحمل هوية **رحّال**.

كامل الواجهة ولغة المجال **عربية** (`ar-sa`، `Asia/Riyadh`، اتجاه من اليمين
لليسار). النسخة الإنجليزية في [`README.md`](README.md). التوثيق التقني
التفصيلي في مجلد [`docs/`](docs/).

> ⚠️ النسخة السابقة من التوثيق كانت تصف دخول المشارك بأنه "بلا كلمة مرور".
> **هذا التصميم أُلغي نهائيًا** — المشارك الآن يدخل بكلمة مرور حقيقية. راجع
> [`docs/authentication.md`](docs/authentication.md).

## نظرة عامة

- كل مشارك ينتمي إلى **بيئة** (`Group`) يقودها مشرف بيئة واحد.
- مع تسجيل المشرفين للحضور، يكسب المشارك عملة ثلاثية:

  | العملة | الحقل | القاعدة |
  |--------|------|---------|
  | النقاط | `points` | عملة الترتيب. **قابلة للتصفير** على مستوى البرنامج (تُحفظ لقطة أولًا). |
  | الأميال | `miles` | `دلتا النقاط × 10`، تُطبَّق معًا. **لا تُصفَّر أبدًا**. |
  | النقاط الشرائية | `purchase_points` | تُصرف في المتجر. **لا يمسّها إجراء التصفير**. |

  كل رصيد محدود بحد أدنى صفر. التحويل في دالة واحدة `apply_points_delta`
  (`participants/views.py`).

- **مصادر النقاط** (قيَم مأخوذة حرفيًا من `participants/views.py`): اللقاء
  الأسبوعي = **8** (+**2** مكافأة الحضور المبكر)؛ الحلقة القرآنية = **3**
  حضور + **2** إنجاز (مستقلان)؛ قبول المهمة الأسبوعية = **10**. التفاصيل:
  [`docs/points-system.md`](docs/points-system.md).

## الأدوار الأربعة

معرّفة في `accounts/models.py` (`Role`):

| الدور | القيمة | ملخص |
|------|--------|------|
| مشارك | `participant` | يدخل بـ **رقم الهوية + كلمة المرور**. لوحة شخصية، رفع المهمة الأسبوعية، المتجر. |
| مشرف بيئة | `group_supervisor` | يسجّل حضور اللقاء والحلقة القرآنية **لبيئته فقط**، ويستعرض بيانات مشاركيه. |
| مشرف عام | `general_supervisor` | على مستوى البرنامج: استيراد إكسل، المهام الأسبوعية، إدارة المتجر، تصفير النقاط، كل البيئات. |
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
- [uv](https://docs.astral.sh/uv/) — إدارة الاعتماديات والبيئة
- **Chart.js 4** — يُحمَّل من CDN في لوحة المشرف العام فقط
- **قاعدة البيانات:** SQLite (`db.sqlite3`) — القاعدة الوحيدة المُعدّة في
  المستودع. راجع [الرفع](#الرفع-والتحديث).

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

| المتغير | مطلوب؟ | الافتراضي | الوصف |
|--------|--------|-----------|-------|
| `SECRET_KEY` | نعم | — | مفتاح Django السري |
| `DEBUG` | لا | `False` | وضع التصحيح |

الملفات `.env` و`db.sqlite3` و`media/` مستثناة من Git.

### أوامر شائعة

```bash
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py check
uv run python manage.py test                 # كل الاختبارات
uv run python manage.py test participants     # تطبيق واحد
```

> **ملاحظة WeasyPrint:** نقطة تصدير PDF
> (`/participants/data/export-pdf/`) تحتاج مكتبات النظام التي يعتمد عليها
> WeasyPrint (‏Pango، Cairo، GObject)، وهي **لا تُثبَّت** عبر `uv sync`. كما
> تتوقّع وجود `static/images/letterhead.png` وهو غير موجود حاليًا في المستودع.
> الاستيراد كسول فبقية الموقع لا تتأثر. راجع
> [`docs/known-limitations.md`](docs/known-limitations.md).

## الرفع والتحديث

المستودع **لا يحتوي** إعداد إنتاج: لا `Dockerfile`، لا `docker-compose.yml`،
لا إعداد Nginx/Gunicorn، لا `STATIC_ROOT`، لا `ALLOWED_HOSTS`، ولا إعداد
PostgreSQL — `config/settings.py` يأتي بـ SQLite فقط.

إجراءات الاستضافة والتحديث يديرها صاحب المشروع **خارج هذا المستودع** (الدليلان
المشار إليهما بـ `دليل_رفع_الاستضافة.md` و`دليل_تحديث_الموقع.md` غير مُتتبَّعين
هنا). عند إضافتهما، يُربطان من هذا القسم. راجع
[`docs/known-limitations.md`](docs/known-limitations.md) البند 7 لقائمة كل ما
ينقص للإنتاج.

## بنية المشروع

```
config/         حزمة مشروع Django (الإعدادات، المسارات، wsgi/asgi)
accounts/       الهوية والمصادقة — User مخصص، Role، backend الدخول، middleware
participants/   البيانات البرنامجية — Group، Participant، الحضور، المهام، المتجر، اللوحات
templates/      قوالب مشتركة (صفحة الهبوط العامة)
static/         الشعار (logo.png) وملف قالب استيراد الإكسل
docs/           التوثيق التقني التفصيلي (بالعربية)
```

اتجاه الاعتماد دائمًا `participants → accounts` ولا عكس.

## التوثيق

ابدأ من [`docs/README.md`](docs/README.md). الأهم لمن يتسلّم الصيانة:
**[`docs/known-limitations.md`](docs/known-limitations.md)**.

## الترخيص / الجهة المالكة

طُوّر لصالح **جمعية صقيل لتنمية الشباب** لأجل برنامج أُفق التنموي. لا يوجد ملف
ترخيص مفتوح المصدر في المستودع؛ كل الحقوق محفوظة للجمعية ما لم يُذكر خلاف ذلك.
