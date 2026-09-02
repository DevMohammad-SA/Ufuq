from django import forms

from .models import (
    CircleAttendance,
    Participant,
    StoreProduct,
    TaskSubmission,
    WeeklyTask,
)


class CircleAttendanceForm(forms.ModelForm):
    participant = forms.ModelChoiceField(
        queryset=Participant.objects.all(),
        empty_label=None,
        label="المشارك",
    )

    class Meta:
        model = CircleAttendance
        fields = ["participant", "date", "attended"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }


class ParticipantImportForm(forms.Form):
    excel_file = forms.FileField(label="ملف الإكسل")


class WeeklyTaskForm(forms.ModelForm):
    class Meta:
        model = WeeklyTask
        fields = ["title", "description", "due_date", "allowed_formats"]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class TaskSubmissionForm(forms.ModelForm):
    class Meta:
        model = TaskSubmission
        fields = ["file"]

    # Server-side gate: the uploaded file's extension must match the single
    # format the task accepts. The browser's file picker `accept` attribute
    # is trivially bypassed, so this check is the real enforcement.
    FORMAT_EXTENSIONS = {
        "pdf": [".pdf"],
        "image": [".jpg", ".jpeg", ".png"],
        "audio": [".mp3", ".wav", ".m4a"],
        "video": [".mp4", ".mov", ".webm"],
    }

    def __init__(self, *args, **kwargs):
        # The task this submission is for must be known to validate the
        # file's extension against its allowed_formats — passed explicitly
        # by the view rather than inferred from initial/instance data.
        self.task = kwargs.pop("task", None)
        super().__init__(*args, **kwargs)

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if file and self.task:
            allowed = self.FORMAT_EXTENSIONS.get(self.task.allowed_formats, [])
            filename = file.name.lower()
            if not any(filename.endswith(ext) for ext in allowed):
                allowed_display = self.task.get_allowed_formats_display()
                raise forms.ValidationError(
                    f"صيغة الملف غير مقبولة لهذه المهمة. الصيغة المطلوبة: {allowed_display}"
                )
        return file


class StoreProductForm(forms.ModelForm):
    class Meta:
        model = StoreProduct
        fields = ["name", "description", "image", "price", "stock"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }
