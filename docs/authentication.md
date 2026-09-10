# المصادقة وكلمات المرور

> مستخرج من `accounts/backends.py`، `accounts/middleware.py`،
> `accounts/forms.py`، `accounts/views.py`، `config/settings.py`.

## آلية الدخول المزدوجة — `NationalIDOrUsernameBackend`

`accounts/backends.py:6`. مسجّل في
`AUTHENTICATION_BACKENDS` (`config/settings.py:112-115`) **قبل**
`ModelBackend` الافتراضي (Django يجرّب كل backend بالترتيب حتى ينجح واحد):

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

> **تنبيه:** المشارك الآن يمرّ **بفحص كلمة مرور حقيقي** مثل أي دور آخر.
> التصميم القديم (دخول المشارك برقم الهوية فقط بلا كلمة مرور) **أُلغي نهائيًا**
> (commit `9299bfb`). أي توثيق أو تعليق قديم يقول "passwordless" **غير صحيح
> حاليًا**.

## نظام كلمة مرور المشارك

### كلمة المرور الأولية = رقم الهوية

عند إنشاء أي مشارك:
- **عبر استيراد الإكسل** (`ParticipantImportView`,
  `participants/views.py:769-776`): `User.objects.create_user(..., password=national_id)`
  ثم `user.must_set_password = True`.
- **عبر واجهة الأدمن** (`UserCreationForm.save`, `accounts/forms.py:41-46`):
  إذا الدور `PARTICIPANT` وله `national_id` ولم تُدخَل كلمة مرور يدويًا →
  `user.set_password(user.national_id)` + `user.must_set_password = True`.

فكلمة مرور المشارك أول مرة **هي رقم هويته نفسه**.

### الإجبار على تعيين كلمة مرور جديدة — `must_set_password` + Middleware

- الحقل `User.must_set_password` (`accounts/models.py:75`, `BooleanField(default=False)`).
- **`ForcePasswordSetupMiddleware`** (`accounts/middleware.py:7`) — مُدرج في
  `MIDDLEWARE` **مباشرة بعد** `AuthenticationMiddleware`
  (`config/settings.py:54-56`). على **كل طلب**:
  - إذا المستخدم مسجّل دخوله **و** دوره `PARTICIPANT` **و**
    `must_set_password == True` **و** المسار ليس `accounts:set_password` ولا
    `accounts:logout` → إعادة توجيه إجباري إلى `accounts:set_password`.
  - المسارات المستثناة: صفحة تعيين كلمة المرور نفسها (نفس URL لـ GET وPOST)،
    وتسجيل الخروج (كي لا يعلق المستخدم بلا مخرج).
- **`SetPasswordView`** (`accounts/views.py:44`):
  - `dispatch()` يعيد التوجيه للوحة المشارك إذا `must_set_password == False`
    (لا داعي للصفحة).
  - `form_valid()`: `set_password(new_password1)` → `must_set_password = False`
    → `save(update_fields=["password", "must_set_password"])` →
    **`update_session_auth_hash()`** (وإلا خرج المستخدم من جلسته فور تغيير
    كلمة المرور).
  - النموذج `SetPasswordForm` (`accounts/forms.py:117`): حقلان
    `new_password1`/`new_password2` بـ **`min_length=8`** فقط + تحقق تطابق.

## استرجاع كلمة المرور (موافقة المشرف العام)

1. **الطلب** — `ForgotPasswordView` (`accounts/views.py:104`): زائر مجهول
   يُدخل رقم هوية في `/accounts/forgot-password/`. إن وُجد مستخدم
   `role=PARTICIPANT` بهذا الرقم → يُنشأ `PasswordResetRequest`.
   **رسالة النجاح واحدة دائمًا** ("تم إرسال طلبك...") سواء وُجد المستخدم أو لا
   — إجراء أمني يمنع كشف أرقام الهوية المسجّلة.
2. **العرض** — `GeneralSupervisorDashboardView.get_context_data`
   (`participants/views.py:926-928`): بطاقة "طلبات استرجاع كلمة المرور" تعرض
   `PasswordResetRequest.objects.filter(resolved=False)`.
3. **الموافقة** — `GeneralSupervisorDashboardView.post()` عند
   `action == "approve_password_reset"` (`participants/views.py:962-981`):
   - `user.set_password(user.national_id)` — تُعاد كلمة المرور لرقم الهوية.
   - `user.must_set_password = True` — يُجبَر على تعيين كلمة جديدة عند دخوله
     التالي (نفس آلية أول دخول).
   - يُعلَّم الطلب `resolved=True` مع `resolved_at` و`resolved_by`.

## دخول المشرفين

`SupervisorLoginView` (`accounts/views.py:28`) يستخدم `AuthenticationForm`
القياسي (اسم مستخدم + كلمة مرور). لا يوجد `must_set_password` للمشرفين.

**تغيير كلمة المرور الاختياري** — `SupervisorPasswordChangeView`
(`accounts/views.py:68`, المسار `accounts:change_password`): صفحة يفتحها
المشرف من navbar متى شاء. النموذج `SupervisorPasswordChangeForm`
(`accounts/forms.py:138`) يتطلب **كلمة المرور الحالية** (`clean_current_password`
يفحص `self.user.check_password`) + كلمة جديدة `min_length=8` + تطابق.
`form_valid` يستدعي `update_session_auth_hash` بعد `set_password`.

## مدقّقات كلمة المرور (`AUTH_PASSWORD_VALIDATORS`)

`config/settings.py:97-110` تُفعّل المدقّقات الأربعة الافتراضية
(`UserAttributeSimilarityValidator`, `MinimumLengthValidator`,
`CommonPasswordValidator`, `NumericPasswordValidator`).

> **قيد مهم:** النماذج المخصصة (`SetPasswordForm`،
> `SupervisorPasswordChangeForm`، `UserCreationForm`) **لا تستدعي**
> `django.contrib.auth.password_validation.validate_password`. تفرض فقط
> `min_length=8` على مستوى حقل النموذج. فعمليًا مدقّقات `AUTH_PASSWORD_VALIDATORS`
> (مثل منع كلمة المرور الرقمية بالكامل، أو الشائعة) **تُطبَّق فقط** في المسارات
> التي تستخدم نماذج Django القياسية مباشرة، مثل `createsuperuser` وبعض تدفقات
> أدمن Django. راجع [`known-limitations.md`](known-limitations.md).

## أدوات جلسة إضافية

`ParticipantAuthenticationForm` (`accounts/forms.py:87`) نسخة معدّلة من
`AuthenticationForm` تُبقي `clean()` يدويًا لرفع رسالة خطأ عامة
(`get_invalid_login_error()`) لا تكشف أي الحقلين كان خاطئًا (رقم الهوية أم
كلمة المرور).
