from django import forms
from django.contrib.auth.forms import ReadOnlyPasswordHashField

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