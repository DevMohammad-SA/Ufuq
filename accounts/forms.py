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
    Used only on the participant login page. Participants authenticate via
    national_id with no password check at all (see NationalIDOrUsernameBackend),
    so the password field here must not be required — otherwise Django's default
    AuthenticationForm would reject the submission for a missing password before
    authenticate() is ever called.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].required = False

    def clean(self):
        # Bypass AuthenticationForm's default validation, which always calls
        # authenticate() with both username and password and expects a
        # non-empty password. We call authenticate() manually here instead,
        # passing whatever password value exists (possibly empty) — the
        # NationalIDOrUsernameBackend ignores it entirely for participants.
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username is not None:
            self.user_cache = self.authenticate_via_backends(username, password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

    def authenticate_via_backends(self, username, password):
        from django.contrib.auth import authenticate
        return authenticate(self.request, username=username, password=password)