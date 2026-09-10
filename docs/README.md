# توثيق مشروع أُفق — الفهرس

توثيق تقني تفصيلي لمنصة **أُفق** (برنامج أُفق التنموي — جمعية صقيل لتنمية
الشباب). كُتب بالعربية لأن فريق الصيانة يتواصل بالعربية.

كل ملف هنا مبني على **فحص الكود الفعلي** في المستودع، لا على افتراضات. عند أي
تعارض، **الكود هو المرجع**.

## الملفات

| الملف | المحتوى |
|-------|---------|
| [`architecture.md`](architecture.md) | نظرة معمارية: التطبيقان (`accounts` / `participants`)، اتجاه الاعتماد، فلسفة الفصل بين الهوية (`User`) والبيانات البرنامجية (`Participant`)، طبقة العرض، تحويل الحضور لنقاط. |
| [`roles-and-permissions.md`](roles-and-permissions.md) | الأدوار الأربعة، كيف يدخل كل دور، ومصفوفة كاملة لصلاحيات كل View مستخرجة من `test_func()`. |
| [`points-system.md`](points-system.md) | العملات الثلاث (نقاط/أميال/نقاط شرائية)، معادلة `apply_points_delta`، والقيَم الرقمية الفعلية لكل مصدر نقاط (اللقاء، الحلقة، المهمة). |
| [`models.md`](models.md) | كل الموديلات حقلًا حقلًا، القيود (`UniqueConstraint`)، العلاقات (`related_name`)، وما هو مسجَّل في الأدمن. |
| [`authentication.md`](authentication.md) | `NationalIDOrUsernameBackend`، كلمة مرور المشارك (`must_set_password` + `ForcePasswordSetupMiddleware`)، واسترجاع كلمة المرور بموافقة المشرف العام. |
| [`features.md`](features.md) | كل ميزة وظيفية مع الـ View والقالب المسؤولين، وجدول المسارات الكامل. |
| [`known-limitations.md`](known-limitations.md) | **اقرأه أولًا.** القيود والأمور المؤجلة صراحةً: استيراد الحلقة الآلي غير مبني، لا صفحة "Live"، اللقطة التاريخية غير مربوطة برحلة النخبة، لا إعداد Docker/PostgreSQL في المستودع، كود ميت، وغيرها. |

## نقاط دخول سريعة

- **أريد فهم البنية العامة:** [`architecture.md`](architecture.md).
- **من يستطيع فعل ماذا:** [`roles-and-permissions.md`](roles-and-permissions.md).
- **كيف تُحسب النقاط:** [`points-system.md`](points-system.md).
- **ما الذي لا يجب أن أفترض وجوده:** [`known-limitations.md`](known-limitations.md).

## للتشغيل المحلي والرفع

راجع [`README.md`](../README.md) و[`README.ar.md`](../README.ar.md) في جذر
المشروع.
