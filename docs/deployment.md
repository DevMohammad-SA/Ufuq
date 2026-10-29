# النشر والتحديث (Docker + SSL + النسخ الاحتياطي)

> مستخرج من `Dockerfile`، `docker-compose.yml`، `docker/entrypoint.sh`،
> `docker/nginx/*.conf`، `docker/certbot-init.sh`، `.env.docker.example`،
> `config/settings.py` — بتاريخ مراجعة 2026-10-29. عند أي تعارض، **الكود هو
> المرجع**.
>
> هذا الملف هو **إجراءات التشغيل**. البنية المعمارية لهذه الحزمة موصوفة في
> [`architecture.md`](architecture.md#النشر-الإنتاجي-docker)، وهذا الملف لا
> يكررها بل يشرح **كيف تُشغَّل وتُحدَّث بأمان**.

## 1. المكوّنات

أربع خدمات في `docker-compose.yml`:

| الخدمة | الصورة | الدور |
|--------|--------|-------|
| `db` | `postgres:16-alpine` | قاعدة البيانات. بياناتها في حجم `postgres_data`. |
| `web` | تُبنى من `Dockerfile` | التطبيق عبر Gunicorn (3 عمّال، مهلة 120 ثانية). |
| `nginx` | `nginx:alpine` | بروكسي عكسي، وتقديم `staticfiles/` و`media/`، والمنفذان 80 و443. |
| `certbot` | `certbot/certbot` | حلقة تجديد: `certbot renew` كل 12 ساعة. |

الأحجام (volumes) الدائمة: `postgres_data`, `static_volume`, `media_volume`,
`certbot_certs`, `certbot_www`. **الملفات المرفوعة (`media/`) وقاعدة البيانات
تعيشان في أحجام Docker، لا داخل المستودع** — فحذف الحاويات لا يحذفها، لكن
`docker compose down -v` **يحذفها كلها**. لا تستخدم `-v` على خادم الإنتاج.

## 2. أول نشر

```bash
cp .env.docker.example .env.docker
#    املأ القيَم الحقيقية: SECRET_KEY، ALLOWED_HOSTS، CSRF_TRUSTED_ORIGINS،
#    ومعطيات قاعدة البيانات
docker compose up -d --build

#    أول مرة فقط، بعد أن يصبح الموقع متاحًا عبر HTTP العادي:
./docker/certbot-init.sh

#    ثم فعّل إعداد HTTPS الكامل:
cp docker/nginx/nginx-ssl.conf docker/nginx/nginx.conf
docker compose up -d --force-recreate nginx
```

`docker/entrypoint.sh` يتكفّل تلقائيًا عند كل إقلاع للحاوية `web` بـ:
انتظار جاهزية PostgreSQL → `migrate --noinput` → `collectstatic --noinput` →
تشغيل Gunicorn.

> **انتبه:** `migrate` يعمل **تلقائيًا** مع كل إقلاع. هذا يعني أن مجرد
> `docker compose up -d --build` بعد سحب كود جديد **سيطبّق الهجرات فورًا** بلا
> أي سؤال. لذلك تأتي النسخة الاحتياطية **قبل** الأمر، لا بعده (القسم 4).

### متغيرات البيئة الإلزامية

| المتغير | ملاحظة |
|---------|--------|
| `SECRET_KEY` | مطلوب دائمًا. |
| `DEBUG` | يجب أن يبقى `False` في الإنتاج. |
| `ALLOWED_HOSTS` | النطاق أو الـIP، مفصولة بفواصل. |
| `CSRF_TRUSTED_ORIGINS` | نطاق HTTPS — بدونه تفشل كل طلبات POST خلف البروكسي. |
| `USE_POSTGRES=True` | وإلا سيستخدم التطبيق SQLite داخل الحاوية، وتضيع البيانات مع الحاوية. |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST=db` / `DB_PORT=5432` | اتصال Django بالقاعدة. |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | تقرأها صورة `postgres` نفسها عند أول تشغيل، **ويجب أن تطابق الثلاثة أعلاه حرفيًا**. التكرار مفروض بتصميم تلك الصورة، وليس خطأً. |

`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` في
`config/settings.py` ضروري لأن TLS ينتهي عند Nginx لا عند Gunicorn — بدونه
يرفض تحقق CSRF أي POST عبر HTTPS.

## 3. الشهادات (SSL)

- `docker/nginx/nginx.conf` إعداد **HTTP فقط**، يخدم تحدي ACME عبر HTTP-01،
  ويُستخدم قبل وجود أي شهادة.
- `docker/certbot-init.sh` يُشغَّل **يدويًا مرة واحدة** لإصدار أول شهادة.
- `docker/nginx/nginx-ssl.conf` إعداد HTTPS الكامل، يُفعَّل بنسخه فوق
  `nginx.conf` ثم إعادة إنشاء الحاوية.
- التجديد تلقائي كل 12 ساعة، **لكن Nginx لا يُعاد تحميله بعد تجديد ناجح** —
  قرار مقبول للنسخة الأولى وموثّق كتعليق في `docker-compose.yml` نفسه. عمليًا:

```bash
# كل ~90 يومًا، بعد التجديد:
docker compose exec nginx nginx -s reload
```

راجع [`known-limitations.md`](known-limitations.md).

## 4. النسخ الاحتياطي قبل كل `migrate`

**هذه ليست توصية بل إجراء إلزامي**، لأن `entrypoint.sh` يشغّل `migrate`
تلقائيًا ولا يوجد في المشروع أي هجرة عكسية مكتوبة يدويًا.

```bash
# 1. نسخة قاعدة البيانات (داخل حاوية db)
docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" \
  > backup-$(date +%F-%H%M).sql

# 2. نسخة الملفات المرفوعة (تسليمات المهام وصور المنتجات)
docker compose run --rm --no-deps --entrypoint tar -v "$PWD:/backup" web \
  -czf /backup/media-$(date +%F-%H%M).tar.gz -C /app media
```

يستخدم أمر `media` صراحة `--no-deps --entrypoint tar`: لا يشغّل بذلك
`docker/entrypoint.sh` الخاص بتطبيق الويب، والذي قد ينتظر القاعدة ويشغّل
`migrate`. النسخة عملية أرشفة للمجلد المركّب `media/` فقط، لا إعادة تشغيل أو
ترقية للتطبيق.

ثم التحديث:

```bash
git pull
docker compose up -d --build     # يبني، ثم يهاجر تلقائيًا، ثم يقلع
docker compose logs -f web       # تابع سطر migrate في السجل
```

للاستعادة عند الفشل:

```bash
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" < backup-XXXX.sql
```

## 5. فحص توافق البيانات قبل الترقية

بعض الهجرات في هذا المشروع **تغيّر معنى قيَم مخزَّنة دون تحويل كامل لها**.
توجد `RunPython` في الهجرة `0019` فقط لغرض علامة منح نقاط المهمة؛ لا تحوّل
قيم صيغ المهام القديمة ولا تعيد احتساب الأرصدة. قبل الترقية من إصدار قديم،
نفّذ الفحوص التالية على قاعدة الإنتاج (للقراءة فقط) وعالج ما يظهر **يدويًا**:

### أ. صيغ المهام المركّبة القديمة (ترقية إلى 1.2.0)

الحقل `WeeklyTask.allowed_formats` كان يحمل قيمًا مركّبة جاهزة
(`image_text`, `pdf_text`) قبل إصدار 1.2.0. لا تحوّلها أي هجرة إلى الشكل
الجديد (`"image,text"` + `require_all_formats=True`)، وستُقرأ كصيغة مجهولة لا
يستطيع أي طالب تسليمها.

```bash
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT id, title, allowed_formats FROM participants_weeklytask
   WHERE allowed_formats LIKE '%_text' AND allowed_formats <> 'text';"
```

إن ظهر أي صف، صحّحه من واجهة الأدمن أو بتحديث مباشر:
`allowed_formats='image,text'` و`require_all_formats=true` (وبالمثل لـ`pdf`).

### ب. المهام صارت غير نشطة افتراضيًا (ترقية إلى 1.2.0)

هجرة `0016` أضافت `is_active` بقيمة افتراضية `False`، فكل مهمة كانت قائمة قبل
الترقية **ستصبح مخفية عن الطلاب** حتى يفعّلها المشرف. هذا متوقَّع، لكنه يفاجئ
إن لم يُنتظر: بعد الترقية، افتح صفحة المهام وفعّل المهام التي يُفترض أنها
مفتوحة.

### ج. مشرفو البيئات بعد تحوّل العلاقة إلى متعدد-لمتعدد

هجرة `0010` حوّلت `Group.supervisor` من `ForeignKey` إلى `ManyToManyField`.
**وهي تحذف العمود القديم ولا تنقل محتواه** إلى جدول الربط الجديد: يتبيّن ذلك من
`manage.py sqlmigrate participants 0010` — الجدول يُعاد بناؤه بـ
`INSERT INTO new__participants_group (id, name) SELECT id, name …` (العمودان
هذان فقط)، ثم يُنشأ `participants_group_supervisor` **فارغًا**. أي أن كل روابط
البيئة بمشرفها تُفقد عند تطبيق هذه الهجرة، ويجب إعادة إسنادها يدويًا من واجهة
الأدمن. تحقّق أن كل بيئة لا تزال مربوطة بمشرفها:

```bash
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT g.id, g.name, count(s.user_id) AS supervisors
   FROM participants_group g
   LEFT JOIN participants_group_supervisor s ON s.group_id = g.id
   GROUP BY g.id, g.name ORDER BY g.name;"
```

بيئة بـ`supervisors = 0` تعني عدم وجود مشرف مرتبط بها؛ يجب إعادة الإسناد يدويًا
كي تدخل في نطاق الكشوف والتقارير والبيانات الخاصة بمشرف البيئة.

### د. جداول الملاحظات والتفويض (0018)

الهجرة `0018_student_notes_and_review_permission` تنشئ جدولَي
`MeetingNote` و`TaskReviewPermission` فقط. لا تمنح مشرف بيئة صلاحية التصحيح
افتراضيًا: غياب صف التفويض أو `enabled=False` يعني اطلاعًا فقط. بعد الترقية،
يستطيع المشرف العام وحده تفعيل الصلاحية أو إلغاؤها من واجهة «صلاحيات تصحيح
المهام».

### هـ. علامة منح نقاط المهمة مرة واحدة (0019)

الهجرة `0019_task_points_once` تجعل `points_awarded` علامة ثلاثية الحالة:
`True` = صُرفت سابقًا، و`False` = لم تُصرف، و`NULL` = لا يمكن تأكيد التاريخ
بأمان. تنفذ الهجرة تحديثين جماعيين فقط:

- كل تسليم تاريخي حالته `accepted` يصبح `True`.
- كل تسليم تاريخي حالته `pending` أو `rejected` يصبح `NULL`.

التسليمات الجديدة وحدها تبدأ `False`. لا تقرأ الهجرة سجل النقاط، ولا تطابق
عنوانًا أو وصفًا، ولا تنشئ صفوف نقاط ولا تعيد حسابها ولا تغيّر أرصدة `points`
أو `miles` أو `purchase_points`. لذلك تمنع التخمين الذي قد يخطئ عند تكرار
العناوين أو عند وجود قبول أسبق من سجل التدقيق.

عند أول محاولة لقبول تسليم تاريخي `pending` حالته `NULL`، لا يستطيع مشرف البيئة
المفوّض اتخاذ القرار. على المشرف العام أو مشرف النظام تحديد قيمة
`historical_award` صراحة في نموذج التصحيح: `already_awarded` (يتحول إلى `True`
بلا منحة) أو `not_awarded` (تُمنح النقاط كالمعتاد ثم يتحول إلى `True`). من دون
قرار صريح لا تتغير الحالة ولا النقاط؛ الرفض يستطيع أن يترك العلامة `NULL`.
خذ نسخة احتياطية قبل الترقية ولا تعامل هذا الحقل كأرشيف يعيد بناء القرارات أو
المحاولات المحذوفة.

## 6. أوامر تشغيلية شائعة

```bash
docker compose ps                               # حالة الخدمات
docker compose logs -f web                      # سجل التطبيق
docker compose restart web                      # إعادة تشغيل التطبيق وحده
docker compose exec web uv run python manage.py createsuperuser
docker compose exec web uv run python manage.py check
docker compose exec nginx nginx -s reload       # بعد تجديد الشهادة
```

> **تصدير PDF:** مكتبات WeasyPrint النظامية (Pango/Cairo/GObject) **مثبَّتة
> داخل صورة الإنتاج** (`Dockerfile`)، فنقطة `participants:participants_data_pdf`
> تعمل في النشر. إن فشلت، فالسبب في الصورة لا في الكود — راجع
> [`known-limitations.md`](known-limitations.md).

## 7. ما ليس مؤتمتًا (اعرفه قبل أن تفترض)

- **لا نسخ احتياطي تلقائي.** لا cron ولا خدمة نسخ في `docker-compose.yml`. كل
  ما في القسم 4 يدوي بالكامل.
- **لا إعادة تحميل لـNginx بعد تجديد الشهادة** (القسم 3).
- **لا فحص صحة (healthcheck)** على أي خدمة؛ `web` يعتمد على `depends_on: db`
  فقط، والانتظار الفعلي لجاهزية القاعدة يتم في `entrypoint.sh` عبر `nc -z`.
- **لا هجرات عكسية مكتوبة**؛ التراجع = استعادة النسخة الاحتياطية.
- **الدليلان `دليل_رفع_الاستضافة.md` و`دليل_تحديث_الموقع.md`** المشار إليهما في
  توثيق سابق **غير موجودين في المستودع**. إن كانا لدى صاحب المشروع خارجيًا،
  فمكانهما هنا في `docs/`.

## 8. CI/CD الموجود في المستودع

الملف `.github/workflows/deploy-production.yml` هو مسار النشر المعرّف في
المستودع. يعمل عند `push` إلى فرع `master` وعند `workflow_dispatch`، ويهيئ
مفتاح SSH و`known_hosts` من GitHub Secrets ثم يستدعي على الخادم:

```text
/home/mohammad/scripts/deploy-ufuq.sh
```

هذا السكربت **ليس داخل المستودع**، ولذلك لا توثّق هذه الصفحة خطواته الداخلية
ولا تضمنها. أظهر سجل آخر نشر تمت مراجعته إنشاء نسخة لقاعدة البيانات و`media/`؛
هذه ملاحظة عن ذلك السجل لا ضمان لسلوك كل تشغيل لاحق. يظل إجراء النسخ اليدوي
في القسم 4 مطلوبًا قبل أي ترقية حساسة.
