"""Explicit, front-end account forms; never expose admin or balance fields."""

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db.models import Q

from accounts.models import Role, User
from .models import AcademicStage, Group

MANAGED_SUPERVISOR_ROLES = (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR)


def validate_identifier(value, user_id=None):
    """Login checks national_id before username: prevent cross-field collisions."""
    users = User.objects.filter(Q(username=value) | Q(national_id=value))
    if user_id is not None:
        users = users.exclude(pk=user_id)
    if users.exists():
        raise forms.ValidationError("هذا المعرّف مستخدم في حساب آخر؛ اختر اسم مستخدم أو رقم هوية غير مكرر")
    return value


class ParticipantEditForm(forms.Form):
    full_name = forms.CharField(label="الاسم الكامل", max_length=100)
    national_id = forms.CharField(
        label="رقم الهوية / الإقامة", max_length=10,
        help_text="تغيير رقم الهوية يغيّر معرّف الدخول فقط؛ كلمة المرور الحالية لا تتغيّر.",
    )
    group = forms.ModelChoiceField(queryset=Group.objects.none(), label="البيئة", required=False)
    academic_stage = forms.ChoiceField(label="المرحلة الدراسية", choices=AcademicStage.choices)
    phone = forms.CharField(label="رقم جوال المشارك", max_length=20, required=False)
    guardian_phone = forms.CharField(label="رقم جوال ولي الأمر", max_length=20, required=False)

    def __init__(self, *args, participant, **kwargs):
        self.participant = participant
        initial = {
            "full_name": participant.user.full_name,
            "national_id": participant.user.national_id,
            "group": participant.group_id,
            "academic_stage": participant.academic_stage,
            "phone": participant.phone,
            "guardian_phone": participant.guardian_phone,
        }
        kwargs.setdefault("initial", initial)
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = Group.objects.order_by("name")

    def clean_national_id(self):
        value = self.cleaned_data["national_id"]
        if len(value) != 10 or not value.isdigit():
            raise forms.ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام")
        return validate_identifier(value, self.participant.user_id)


class SupervisorManagementForm(forms.Form):
    full_name = forms.CharField(label="الاسم الكامل", max_length=100)
    username = forms.CharField(
        label="اسم المستخدم", max_length=30, validators=[UnicodeUsernameValidator()],
        help_text="معرّف الدخول؛ دون مسافات. لا يجوز أن يطابق معرّف حساب آخر.",
    )
    national_id = forms.CharField(label="رقم الهوية / الإقامة", max_length=10, required=False)
    role = forms.ChoiceField(
        label="الدور", choices=[(value, Role(value).label) for value in MANAGED_SUPERVISOR_ROLES],
    )
    environments = forms.ModelMultipleChoiceField(
        label="البيئات المسندة", queryset=Group.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="لمشرف البيئة فقط. يمكنك اختيار أكثر من بيئة؛ المشرف العام يرى البرنامج كله.",
    )
    is_active = forms.BooleanField(label="الحساب نشط", required=False, initial=True)
    password1 = forms.CharField(
        label="كلمة المرور", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        required=False, min_length=8,
        help_text="مطلوبة عند الإضافة. اترك الحقلين فارغين عند التعديل للإبقاء على كلمة المرور.",
    )
    password2 = forms.CharField(
        label="تأكيد كلمة المرور", required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, actor, supervisor=None, **kwargs):
        self.actor = actor
        self.supervisor = supervisor
        if supervisor is not None:
            kwargs.setdefault("initial", {
                "full_name": supervisor.full_name,
                "username": supervisor.username,
                "national_id": supervisor.national_id,
                "role": supervisor.role,
                "is_active": supervisor.is_active,
                "environments": list(supervisor.group_set.values_list("pk", flat=True)),
            })
        else:
            kwargs.setdefault("initial", {"role": Role.GROUP_SUPERVISOR, "is_active": True})
        super().__init__(*args, **kwargs)
        self.fields["environments"].queryset = Group.objects.order_by("name")
        if supervisor is None:
            self.fields["password1"].required = True
            self.fields["password2"].required = True
        if supervisor is not None and supervisor.pk == actor.pk:
            self.fields["current_password"] = forms.CharField(
                label="كلمة المرور الحالية", widget=forms.PasswordInput, required=False,
                help_text="مطلوبة فقط إذا كنت تغيّر كلمة مرور حسابك أنت.",
            )

    def clean_username(self):
        value = User.normalize_username(self.cleaned_data["username"])
        UnicodeUsernameValidator()(value)
        if len(value) > 30:
            raise forms.ValidationError("اسم المستخدم لا يتجاوز 30 حرفًا")
        return validate_identifier(value, self.supervisor.pk if self.supervisor else None)

    def clean_national_id(self):
        value = self.cleaned_data.get("national_id", "")
        if not value:
            return None
        if len(value) != 10 or not value.isdigit():
            raise forms.ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام")
        return validate_identifier(value, self.supervisor.pk if self.supervisor else None)

    def clean(self):
        data = super().clean()
        role = data.get("role")
        if role == Role.GENERAL_SUPERVISOR and data.get("environments"):
            self.add_error("environments", "المشرف العام لا يحتاج إسناد بيئات؛ ألغِ تحديد البيئات لهذا الدور")
        if self.supervisor is not None and self.supervisor.pk == self.actor.pk:
            if role is not None and role != self.actor.role:
                self.add_error("role", "لا يمكنك تغيير دور حسابك من صفحة إدارة المشرفين")
            if not data.get("is_active"):
                self.add_error("is_active", "لا يمكنك تعطيل حسابك أنت")
        password1, password2 = data.get("password1"), data.get("password2")
        if password1 or password2:
            if not password1 or password1 != password2:
                self.add_error("password2", "أدخل كلمتي مرور متطابقتين")
            if password1:
                candidate = User(
                    username=data.get("username"), national_id=data.get("national_id"),
                    full_name=data.get("full_name", ""), role=role or Role.GROUP_SUPERVISOR,
                )
                try:
                    validate_password(password1, candidate)
                except ValidationError as error:
                    self.add_error("password1", error)
                if self.supervisor is not None and self.supervisor.pk == self.actor.pk:
                    if not self.supervisor.check_password(data.get("current_password", "")):
                        self.add_error("current_password", "كلمة المرور الحالية غير صحيحة")
        return data
