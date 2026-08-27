from django import forms
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import User


class UserCreationForm(forms.ModelForm):
    """
    Used on the "Add user" page in the admin.
    Shows two password fields (entry + confirmation) instead of exposing
    the raw `password` model field directly.
    """

    password1 = forms.CharField(widget=forms.PasswordInput,label="كلمة المرور")
    password2 = forms.CharField(widget=forms.PasswordInput,label="تأكيد كلمة المرور")

    class Meta:
        model = User
        fields = ["username","full_name","role","national_id",]

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("كلمتا المرور غير متطابقتين")
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
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

    class Meta:
        model = User
        fields = ["username","password","full_name","role","national_id","is_active","is_staff"]