# المصادقة وكلمات المرور

> مستخرج من `accounts/backends.py`، `accounts/middleware.py`،
> `accounts/forms.py`، `accounts/views.py`، `config/settings.py` (تحقّق
> مباشر من الكود الفعلي). المراجع بصيغة اسم الدالة/الكلاس بدل رقم السطر.

## آلية الدخول المزدوجة — `NationalIDOrUsernameBackend`

في `accounts/backends.py`. مسجّل في `AUTHENTICATION_BACKENDS`
(`config/settings.py`) **قبل** `ModelBackend` الافتراضي (Django يجرّب كل
backend بالترتيب حتى ينجح واحد):

```python
AUTHENTICATION_BACKENDS = [
    "accounts.backends.NationalIDOrUsernameBackend",
    "django.contrib.auth.backends.ModelBackend",
]
```

منطق `authenticate(request, username, password)`:

1. يبحث عن مستخدم بـ `national_id == username`.
2. إن لم يجد، يبحث بـ `username == username`.
3. إن لم يجد أيًا منهما → `None`.
4. إن وجد: يُرجع المستخدم **فقط إذا** `user.check_password(password)` **و**
   `user.is_active`. غير ذلك → `None`.

> **تنبيه:** المشارك يمرّ **بفحص كلمة مرور حقيقي** مثل أي دور آخر. التصميم
> القديم (دخول المشارك برقم الهوية فقط بلا كلمة مرور) **أُلغي نهائيًا** منذ
> commit `9299bfb` ولا يزال كذلك في الكود الحالي. أي توثيق أو تعليق يقول
> "passwordless" **غير صحيح**.

## نظام كلمة مرور المشارك

### كلمة المرور الأولية = رقم الهوية

عند إنشاء أي مشارك، عبر أي من ثلاث طرق:

- **استيراد الإكسل** (طريقة `ParticipantImportView._process_import` في
  `participants/views.py`): `User.objects.create_user(..., password=national_id)`
  ثم `user.must_set_password = True`.
- **إضافة مشارك مفرد** (طريقة `AddParticipantView.form_valid`، **جديدة منذ
  1.1.0** — نموذج `SingleParticipantForm`): نفس النمط بالضبط —
  `password=national_id` ثم `must_set_password = True`.
- **واجهة الأدمن** (طريقة `UserCreationForm.save` في `accounts/forms.py`):
  إذا الدور `PARTICIPANT` وله `national_id` ولم تُدخَل كلمة مرور يدويًا →
  `user.set_password(user.national_id)` + `user.must_set_password = True`.

فكلمة مرور المشارك أول مرة **هي رقم هويته نفسه**، بصرف النظر عن طريقة الإنشاء
الثلاث.

### الإجبار على تعيين كلمة مرور جديدة — `must_set_password` + Middleware

- الحقل `User.must_set_password` (`accounts/models.py`, `BooleanField(default=False)`).
- **`ForcePasswordSetupMiddleware`** (`accounts/middleware.py`) — مُدرج في
  `MIDDLEWARE` **مباشرة بعد** `AuthenticationMiddleware`
  (`config/settings.py`). على **كل طلب**:
  - إذا المستخدم مسجّل دخوله **و** دوره `PARTICIPANT` **و**
    `must_set_password == True` **و** المسار ليس `accounts:set_password` ولا
    `accounts:logout` → إعادة توجيه إجباري إلى `accounts:set_password`.
  - المسارات المستثناة: صفحة تعيين كلمة المرور نفسها (نفس URL لـ GET وPOST)،
    وتسجيل الخروج (كي لا يعلق المستخدم بلا مخرج).
- **`SetPasswordView`** (`accounts/views.py`):
  - `dispatch()` يعيد التوجيه للوحة المشارك إذا `must_set_password == False`
    (لا داعي للصفحة).
  - `form_valid()`: `set_password(new_password1)` → `must_set_password = False`
    → `save(update_fields=["password", "must_set_password"])` →
    **`update_session_auth_hash()`** (وإلا خرج المستخدم من جلسته فور تغيير
    كلمة المرور).
  - النموذج `SetPasswordForm` (`accounts/forms.py`): حقلان
    `new_password1`/`new_password2` بـ **`min_length=8`** فقط + تحقق تطابق.

## استرجاع كلمة المرور (موافقة المشرف العام)

1. **الطلب** — `ForgotPasswordView` (`accounts/views.py`): زائر مجهول
   يُدخل رقم هوية في `/accounts/forgot-password/`. إن وُجد مستخدم
   `role=PARTICIPANT` بهذا الرقم → يُنشأ `PasswordResetRequest`.
   **رسالة النجاح واحدة دائمًا** ("تم إرسال طلبك...") سواء وُجد المستخدم أو لا
   — إجراء أمني يمنع كشف أرقام الهوية المسجّلة.
2. **العرض** — طريقة `GeneralSupervisorDashboardView.get_context_data`
   (`participants/views.py`): بطاقة "طلبات استرجاع كلمة المرور" تعرض
   `PasswordResetRequest.objects.filter(resolved=False)`.
3. **الموافقة** — طريقة `GeneralSupervisorDashboardView.post()` عند
   `action == "approve_password_reset"`:
   - `user.set_password(user.national_id)` — تُعاد كلمة المرور لرقم الهوية.
   - `user.must_set_password = True` — يُجبَر على تعيين كلمة جديدة عند دخوله
     التالي (نفس آلية أول دخول).
   - يُعلَّم الطلب `resolved=True` مع `resolved_at` و`resolved_by`.

## دخول المشرفين

`SupervisorLoginView` (`accounts/views.py`) يستخدم `AuthenticationForm`
القياسي (اسم مستخدم + كلمة مرور). لا يوجد `must_set_password` للمشرفين.

### إدارة حسابات المشرفين من واجهة رحّال

نموذج `SupervisorManagementForm` في `participants/management_forms.py` هو
مسار مستقل لإضافة مشرف بيئة/عام أو تعديل حسابه من الواجهة المخصصة للمشرف العام
ومشرف النظام:

- عند الإنشاء، كلمة المرور وتأكيدها إلزاميان، متطابقان، ويمران عبر
  `django.contrib.auth.password_validation.validate_password` مع سمات الحساب
  المرشح. عند تعديل حساب آخر هما اختياريان؛ تركهما فارغين لا يغير كلمة المرور.
- عند تعديل الحساب الذاتي وتقديم كلمة مرور جديدة، تُطلب كلمة المرور الحالية
  أيضًا. بعد الحفظ يستدعي الـ View `update_session_auth_hash()` كي تبقى جلسة
  المشرف الحالي صالحة.
- يتحقق النموذج من اسم الدخول والهوية عبر الحسابين معًا حتى لا يطابق أحدهما
  معرّفًا مستخدمًا في الحقل الآخر. رقم الهوية اختياري للمشرف لكنه، إن قُدّم، من
  10 أرقام وغير مكرر.
- هذه الضوابط القوية تخص نموذج الإدارة الجديد فقط. لا تُعدّل أو تعيد فحص كلمات
  المرور القديمة المخزنة، ولا تغيّر تحقق النماذج الأقدم الموضح في القسم التالي.

**تغيير كلمة المرور الاختياري** — `SupervisorPasswordChangeView`
(`accounts/views.py`, المسار `accounts:change_password`): صفحة يفتحها
المشرف من قسم "الحساب" في قائمة التنقل متى شاء. النموذج `SupervisorPasswordChangeForm`
(`accounts/forms.py`) يتطلب **كلمة المرور الحالية** (طريقة
`clean_current_password` تفحص `self.user.check_password`) + كلمة جديدة
`min_length=8` + تطابق. `form_valid` يستدعي `update_session_auth_hash` بعد
`set_password`.

## مدقّقات كلمة المرور (`AUTH_PASSWORD_VALIDATORS`)

`config/settings.py` يُفعّل المدقّقات الأربعة الافتراضية
(`UserAttributeSimilarityValidator`, `MinimumLengthValidator`,
`CommonPasswordValidator`, `NumericPasswordValidator`).

> **قيد لا يزال قائمًا:** النماذج المخصصة (`SetPasswordForm`،
> `SupervisorPasswordChangeForm`، `UserCreationForm`) **لا تستدعي**
> `django.contrib.auth.password_validation.validate_password`. تفرض فقط
> `min_length=8` على مستوى حقل النموذج. فعمليًا مدقّقات
> `AUTH_PASSWORD_VALIDATORS` (مثل منع كلمة المرور الرقمية بالكامل، أو
> الشائعة) تُطبَّق في المسارات القياسية مثل `createsuperuser` وبعض تدفقات
> أدمن Django، وفي `SupervisorManagementForm` الجديد باستدعاء صريح لـ
> `validate_password`؛ لا يعني ذلك أن النماذج المخصصة الأقدم تغيرت. راجع
> [`known-limitations.md`](known-limitations.md).

## أدوات جلسة إضافية

`ParticipantAuthenticationForm` (`accounts/forms.py`) نسخة معدّلة من
`AuthenticationForm` تُبقي `clean()` يدويًا لرفع رسالة خطأ عامة
(`get_invalid_login_error()`) لا تكشف أي الحقلين كان خاطئًا (رقم الهوية أم
كلمة المرور).

## تغطية الاختبارات

`accounts/tests.py` يحتوي حاليًا اختبارات فعلية (وليست ملفًا فارغًا) تغطي:
تدفق كلمة مرور المشارك الأول (`ParticipantPasswordFlowTests`)، ورفض تسجيل
دخول المشرف بكلمة مرور خاطئة (`SupervisorLoginUnaffectedTests`). الرقم 37 كان
خط أساس موثقًا للإصدار الموسوم 1.2.0 قبل التحديثات غير الموسومة؛ ليس نتيجة
للكود الحالي. راجع
[`docs/README.md`](README.md) والقسم المخصص للاختبارات في `README.md` بجذر
المشروع.

## إيقاف الجلسات ومنع تعارض معرّفات الإدارة

تتحقق `NationalIDOrUsernameBackend.get_user()` من `is_active` أيضًا، لا عند تسجيل دخول جديد فقط. لذلك يصبح الحساب المعطّل غير مصادق في طلبه التالي حتى لو كانت لديه جلسة قديمة من الخلفية المخصصة؛ وهذا ضروري للحذف الناعم في صفحة إدارة المشرفين.

تستخدم عمليات تعديل المشارك وإضافة/تعديل المشرف في `management_views.py` دالة `lock_login_identifiers()` من `management_locking.py` داخل المعاملة وقبل `form.is_valid()`. على PostgreSQL يُكتسب `pg_advisory_xact_lock` للقيم المطبعة بترتيب ثابت؛ القيمة نفسها لها القفل نفسه سواء كانت اسم مستخدم أو رقم هوية. يعاد فحص تعارض العمودين بعد القفل، فتمنع العمليات الإدارية الجديدة من حفظ معرّف متصادم تحت التزامن. تنتهي الأقفال تلقائيًا مع المعاملة، ولا تتطلب جدولًا أو هجرة. هذا عقد خاص بهذه العمليات؛ ليس قيدًا جديدًا على مستوى قاعدة البيانات لكل سكربت قديم أو تعديل مباشر خارجها.
