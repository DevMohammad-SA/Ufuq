# بنية التنقل (Sidebar + الدرج الجوال)

> مستخرج من `build_navbar` / `get_notification_counts` / `NAV_GROUP_LABELS`
> في `participants/views.py`، ومن القالبين
> `participants/templates/participants/app_base.html` و
> `participants/templates/participants/_nav_sections.html`، ومن
> `participants/tests.py` (`NavigationShellTests`) — بتاريخ مراجعة 2026-09-23.
> عند أي تعارض، **الكود هو المرجع**.

أُفرد هذا الملف لأن التنقل **أُعيد بناؤه كليًا في 1.2.0** (commit `c83a75b`)،
ولأن صياغته الحالية نتيجة **انحدارَين حقيقيَّين في الإنتاج** يجب ألا يتكررا.

## 1. الشكل الحالي

| المقاس | ما يظهر |
|--------|---------|
| سطح المكتب (> 720px) | **قائمة جانبية** (`<nav class="app-sidebar">`) فيها كل الروابط مقسّمة لأقسام، + شريط علوي رفيع (`app-topbar`) فيه زر الطي والشعار. |
| الجوال (≤ 720px) | **شريط سفلي ثابت** (`app-nav--bottom`) فيه عناصر `mobile_primary` فقط، + **درج منزلق** (`nav-drawer__panel`) يفتحه زر "القائمة" ويعرض القائمة كاملة. القائمة الجانبية والشريط العلوي مخفيان. |

- حالة الطي تُحفظ في `localStorage` تحت المفتاح **`rahhal.sidebar`** (القيمة
  `"collapsed"`)، وتُطبَّق بسكربت صغير **قبل رسم المحتوى** في أعلى `<body>` كي
  لا يومض الانتقال من "مفتوح" إلى "مطوي" عند كل تحميل صفحة. الافتراضي عند غياب
  القيمة: مفتوح.
- زر "القائمة" في الشريط السفلي **لا يظهر إلا إن وُجد عنصر ثانوي فعلًا**. دور
  المشارك كل عناصره أساسية، فلا زر ولا درج له — ويبقى "خروج" في شريطه السفلي.

## 2. مصدر القائمة الوحيد — `build_navbar(user, active_key)`

كل صفحة بعد الدخول ترث `app_base.html` وتستدعي `build_navbar` بمفتاح صفحتها
هي، فيُبرَز عنصرها. الدالة **بلا أي منطق أعمال** — تُنتج بيانات عرض فقط.

كل مُدخَل في القائمة يحمل الحقول التالية:

| الحقل | الغرض |
|-------|-------|
| `key` | معرّف العنصر (وبه تُربط شارة الإشعارات، لا بالتسمية). |
| `label` | التسمية العربية المعروضة. |
| `url` | ناتج `reverse()` لاسم المسار. |
| `icon` | أيقونة SVG مضمّنة، تُطبع عبر `|safe`. |
| `active` | `key == active_key`. |
| `badge_count` | عدد الشارة من `get_notification_counts`. |
| `group` | القسم الذي يقع فيه العنصر. |
| `group_label` | عنوان القسم من `NAV_GROUP_LABELS`. |
| `mobile_primary` | هل يظهر في الشريط السفلي للجوال؟ |

### الأقسام (`NAV_GROUP_LABELS`)

| `group` | العنوان المعروض |
|---------|------------------|
| `main` | **(فارغ عمدًا)** — عناصره تُرسم أعلى القائمة بلا عنوان فوقها. |
| `tasks` | المهام |
| `store` | المتجر |
| `students` | الطلاب |
| `points` | النقاط |
| `account` | الحساب |

### القائمة الفعلية لكل دور

**مشارك** (الثلاثة في `main`، وكلها `mobile_primary`):

| `key` | التسمية | المسار |
|-------|---------|--------|
| `home` | الرئيسية | `participants:dashboard` |
| `tasks` | المهام | `participants:task_submission` |
| `store` | المتجر | `participants:store` |

**مشرف بيئة:**

| `key` | التسمية | المسار | القسم | أساسي بالجوال؟ |
|-------|---------|--------|-------|-----------------|
| `attendance` | التحضير | `participants:supervisor_dashboard` | `main` | نعم |
| `quran` | الحلقة القرآنية | `participants:quran_circle_attendance` | `students` | نعم |
| `weekly_activity` | فعالية الأسبوع | `participants:weekly_activity_attendance` | `students` | نعم |
| `data` | بيانات المشاركين | `participants:participants_data` | `students` | لا |
| `points_ledger` | سجل النقاط | `participants:points_ledger` | `points` | لا |
| `change_password` | تغيير كلمة المرور | `accounts:change_password` | `account` | لا |

> مشرف البيئة ليس له لوحة منفصلة عن كشف التحضير، فـ"الرئيسية" و"التحضير"
> كانتا ستشيران لنفس الرابط — لذلك هو مُدخَل واحد اسمه "التحضير".

**مشرف عام / مشرف النظام:**

| `key` | التسمية | المسار | القسم | أساسي بالجوال؟ |
|-------|---------|--------|-------|-----------------|
| `home` | الرئيسية | `participants:general_supervisor_dashboard` | `main` | نعم |
| `tasks` | المهام | `participants:weekly_task_review` | `tasks` | نعم |
| `tasks_archive` | أرشيف المهام | `participants:tasks_archive` | `tasks` | لا |
| `store_management` | طلبات المتجر | `participants:store_management` | `store` | نعم |
| `import` | الاستيراد | `participants:import_participants` | `students` | لا |
| `add_participant` | إضافة طالب | `participants:add_participant` | `students` | لا |
| `quran` | الحلقة القرآنية | `participants:quran_circle_attendance` | `students` | لا |
| `weekly_activity` | فعالية الأسبوع | `participants:weekly_activity_attendance` | `students` | لا |
| `data` | بيانات المشاركين | `participants:participants_data` | `students` | لا |
| `extra_points` | نقاط إضافية | `participants:extra_points` | `points` | لا |
| `points_ledger` | سجل النقاط | `participants:points_ledger` | `points` | لا |
| `change_password` | تغيير كلمة المرور | `accounts:change_password` | `account` | لا |

## 3. القالب: قائمة روابط واحدة، تُدرَج مرتين

`_nav_sections.html` هو **المصدر الوحيد** لقائمة الروابط، ويُدرَج بـ
`{% include %}` مرتين: مرة في القائمة الجانبية ومرة في درج الجوال — فلا يمكن
أن تتباعد الواجهتان. يبني الأقسام بـ`{% regroup navbar_items by group_label %}`
ويرسم كل رابط مع أيقونته وشارته، ثم نموذج خروج POST في آخره.

### قاعدتان لا تُكسران

1. **لا تصنيف بمطابقة النص إطلاقًا.** القالب يصنّف العناصر من حقلَي
   `group`/`mobile_primary` فقط، ولا يقارن `item.label` بأي نص، ولا يتفرّع على
   `navbar_items|length`. مطابقة التسميات هي **سبب انحدارَين في الإنتاج**:
   قائمة "المهام" ظهرت فارغة لمشرف البيئة، و"فعالية الأسبوع" اختفت كليًا.
2. **عناصر القسم الواحد يجب أن تبقى متجاورة** في قوائم `build_navbar`، لأن
   `{% regroup %}` يدمج **المتتاليات المتجاورة فقط**؛ عنصر `students` يتيم بين
   عنصرَي `points` ينتج قسمين منفصلين بنفس العنوان.

### النقطة الحمراء (تنبيه بلا عدد)

تُصيَّر بالقالب وحده، بلا أي منطق في الـ View، باستخدام فرز القوالب:

- **زر الهامبورجر** (`#sidebarToggle`): نقطة إن وُجدت أي شارة على أي عنصر
  (`navbar_items|dictsortreversed:"badge_count"|first`)، وتظهر بالـCSS فقط حين
  تكون القائمة الجانبية مطوية — فحين تكون مفتوحة تكون الشارة نفسها مرئية.
- **زر "القائمة"** (`#drawerOpen`): نقطة إن وُجدت شارة على عنصر **ثانوي**
  تحديدًا (`dictsortreversed:"badge_count"` ثم `dictsort:"mobile_primary"`،
  والفرز مستقر فيكون الأول هو الثانوي صاحب أعلى شارة). شارة على عنصر أساسي
  مرئية أصلًا في الشريط السفلي فلا تُنقّط الزر.

### الخروج دائمًا نموذج POST

في القائمة الجانبية وفي الدرج/الشريط السفلي، الخروج زر داخل
`<form method="post">` يحمل `{% csrf_token %}` — **لا يوجد ولا يجوز** أن يوجد
`<a href="{% url 'accounts:logout' %}">` في أي مكان.

## 4. شارات الإشعارات — `get_notification_counts(user)`

تُحسب **حيًا في كل طلب** من حالة البيانات الحالية: لا حقل "تمت القراءة" ولا
موديل جديد ولا تخزين لأي شيء.

| الدور | المفتاح | ما يُعدّ |
|-------|---------|----------|
| مشرف عام / نظام | `tasks` | تسليمات حالتها `PENDING` عبر **كل المهام النشطة** (`get_active_tasks()`). |
| مشرف عام / نظام | `store_management` | طلبات متجر حالتها `PENDING`. |
| مشارك | `tasks` | عدد المهام النشطة التي **لم يسلّمها بعد** أو **أُعيد فتحها له** (الحالتان متنافيتان، فأقصى مساهمة لكل مهمة = 1). |
| مشارك | `store` | طلباته التي اكتملت أو استُرجعت خلال آخر **24 ساعة**. |

مشرف البيئة **بلا شارات** حاليًا (لا فرع له في الدالة).

## 5. كيف تضيف صفحة جديدة إلى التنقل

1. أضف المسار في `participants/urls.py` بـ`name=` واضح.
2. أضف ثابت أيقونة `ICON_…` في `participants/views.py` إن لم تناسبك أيقونة
   موجودة: **SVG مضمَّن، مسارات هندسية فقط، `stroke="currentColor"`،
   `width/height=20`** — لا إيموجي ولا أي اعتماد خارجي، كي تُرسم بشكل متطابق
   على كل نظام وتَرِث لون النص (بما في ذلك حالة `is-active`).
3. أضف المُدخَل في قائمة الدور المناسب داخل `build_navbar`، بالشكل
   `(key, label, url_name, icon, group, mobile_primary)`، **بجوار بقية عناصر
   قسمه** (شرط تجاور `{% regroup %}`).
4. في الـ View الجديد: `context["navbar_items"] = build_navbar(request.user, "<key>")`.
5. إن احتاجت الصفحة شارة، أضف `counts["<key>"] = …` في `get_notification_counts`
   داخل فرع الدور المناسب.
6. **لا تلمس القالب.** المُدخَل الجديد يظهر تلقائيًا في القائمة الجانبية
   والدرج. لو احتجت تعديل القالب لإظهار عنصر، فغالبًا أنت على وشك إعادة
   إدخال مطابقة التسميات — توقف.
7. إن أضافت الصفحة تجاوزًا لـ`.wrap` في CSS، **لا تقِسه بوحدات الشاشة**
   (`vw`/`vmin`/`vmax`): مع القائمة الجانبية المفتوحة يفيض عن منطقة المحتوى
   ويُحدث تمريرًا أفقيًا للصفحة كلها. قِسه نسبةً للمحتوى (`max-width:100%`).

## 6. ما يحرسه `NavigationShellTests`

في `participants/tests.py`، ويُشغَّل ضمن المجموعة الكاملة:

| الاختبار | ما يضمنه |
|----------|-----------|
| `test_every_navbar_item_renders_in_sidebar_and_on_mobile` | **حارس اليتامى**: كل مُدخَل تُرجعه `build_navbar` يُرسَم فعليًا — في القائمة الجانبية، وفي (الدرج ∪ الشريط السفلي) — بلا زيادة ولا نقصان. |
| `test_section_titles_match_groups` | عناوين الأقسام تأتي من `group_label` بالترتيب الصحيح، ولا يُرسَم عنوان فارغ. |
| `test_bottom_bar_is_primaries_only_and_menu_button_gating` | الشريط السفلي فيه عناصر `mobile_primary` فقط، وزر "القائمة"/الدرج يظهران **فقط** إن وُجد عنصر ثانوي. |
| `test_active_item_marked_in_sidebar` | الصفحة الحالية تُبرَز بـ`is-active` داخل القائمة الجانبية. |
| `test_secondary_badge_shows_dot_on_menu_button` | منطق النقطة الحمراء الثلاثي أعلاه (ثانوي → نقطة على "القائمة"، أساسي → لا، بلا شارات → لا نقاط إطلاقًا). |
| `test_logout_is_always_a_post_form` | الخروج نموذجان POST بـCSRF، ولا يوجد رابط `<a>` للخروج. |
| `test_no_viewport_relative_wrap_override` | لا قالب يرث `app_base.html` يقيس `.wrap` بوحدات الشاشة. |
