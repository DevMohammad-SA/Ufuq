from django import forms
from django.contrib.auth.forms import AuthenticationForm, ReadOnlyPasswordHashField

from .models import User, Role


class UserCreationForm(forms.ModelForm):
    """
    Used on the "Add user" page in the admin.
    Shows two password fields (entry + confirmation) instead of exposing
    the raw `password` model field directly.
    """

    password1 = forms.CharField(widget=forms.PasswordInput, label="كلمة المرور", required=False)
    password2 = forms.CharField(widget=forms.PasswordInput, label="تأكيد كلمة المرور", required=False)

    class Meta:
        model = User
        fields = ["username","full_name","role","national_id",]

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        role = cleaned_data.get("role")

        if role != Role.PARTICIPANT and not password1:
            raise forms.ValidationError("كلمة المرور مطلوبة لهذا الدور")

        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("كلمتا المرور غير متطابقتين")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password1 = self.cleaned_data.get("password1")

        if password1:
            user.set_password(password1)
        elif user.role == Role.PARTICIPANT and user.national_id:
            # Participants now get a real, known initial password (their own
            # national_id) instead of an unusable random string — they must
            # change it on first login via must_set_password.
            user.set_password(user.national_id)
            user.must_set_password = True
        else:
            from django.utils.crypto import get_random_string
            user.set_password(get_random_string(50))

        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    """
    Used on the "Change user" page in the admin.
    Displays the password as a read-only hash instead of an editable field.
    """

    password = ReadOnlyPasswordHashField(
        label="كلمة المرور",
        help_text="كلمات المرور لا تُخزن كنص صريح، لذلك لا يمكن عرضها هنا مباشرة.",
    )
    new_password = forms.CharField(
        label="كلمة مرور جديدة",
        widget=forms.PasswordInput,
        required=False,
        help_text="اتركه فارغاً إذا كنت لا تريد تغيير كلمة المرور.",
    )

    class Meta:
        model = User
        fields = ["username","password","full_name","role","national_id","is_active","is_staff"]

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password = self.cleaned_data.get("new_password")
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
        return user


class ParticipantAuthenticationForm(AuthenticationForm):
    """
    Used only on the participant login page. Participants authenticate by
    national_id (as the "username" field) plus a real password, resolved
    by NationalIDOrUsernameBackend exactly like every other role. The
    field is labelled "رقم الهوية / الإقامة" in the template.

    This subclass keeps a manual clean() (rather than AuthenticationForm's
    default) only so the generic "الرقم غير مسجّل أو كلمة المرور غير صحيحة"
    error is raised through get_invalid_login_error() without leaking which
    of the two was wrong.
    """

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username is not None and password:
            self.user_cache = self.authenticate_via_backends(username, password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

    def authenticate_via_backends(self, username, password):
        from django.contrib.auth import authenticate
        return authenticate(self.request, username=username, password=password)


class SetPasswordForm(forms.Form):
    new_password1 = forms.CharField(
        label="كلمة المرور الجديدة", widget=forms.PasswordInput, min_length=8
    )
    new_password2 = forms.CharField(
        label="تأكيد كلمة المرور", widget=forms.PasswordInput, min_length=8
    )

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("new_password1")
        p2 = cleaned_data.get("new_password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("كلمتا المرور غير متطابقتين")
        return cleaned_data


class ForgotPasswordRequestForm(forms.Form):
    national_id = forms.CharField(label="رقم الهوية / الإقامة", max_length=10)


class SupervisorPasswordChangeForm(forms.Form):
    """
    Optional, self-service password change for supervisor roles — opened by
    the supervisor from their own navbar whenever they want. Unlike the
    participant-only SetPasswordForm (mandatory after first login, no old
    password), this requires the current password before accepting a new one.
    """

    current_password = forms.CharField(
        label="كلمة المرور الحالية", widget=forms.PasswordInput
    )
    new_password1 = forms.CharField(
        label="كلمة المرور الجديدة", widget=forms.PasswordInput, min_length=8
    )
    new_password2 = forms.CharField(
        label="تأكيد كلمة المرور الجديدة", widget=forms.PasswordInput, min_length=8
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current_password = self.cleaned_data.get("current_password")
        if not self.user.check_password(current_password):
            raise forms.ValidationError("كلمة المرور الحالية غير صحيحة")
        return current_password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("new_password1")
        p2 = cleaned_data.get("new_password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("كلمتا المرور الجديدتان غير متطابقتين")
        return cleaned_data