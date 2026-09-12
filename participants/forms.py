from django import forms

from accounts.models import User
from .models import (
    AcademicStage,
    CircleAttendance,
    Group,
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

    # Hard upload ceilings per format. "image" is intentionally absent —
    # images are never rejected for size, they are downscaled/re-encoded in
    # TaskSubmission.save() (see compress_image_field).
    MAX_FILE_SIZES = {
        "pdf": 10 * 1024 * 1024,  # 10 MB
        "audio": 15 * 1024 * 1024,  # 15 MB
        "video": 50 * 1024 * 1024,  # 50 MB
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

            # Size ceiling — checked only after the extension is accepted.
            # Images have no entry here (compressed on save, never rejected).
            max_size = self.MAX_FILE_SIZES.get(self.task.allowed_formats)
            if max_size and file.size > max_size:
                max_mb = max_size // (1024 * 1024)
                raise forms.ValidationError(
                    f"حجم الملف يتجاوز الحد المسموح ({max_mb} ميجابايت) لهذا النوع."
                )
        return file


class ExtraPointsForm(forms.Form):
    participant = forms.ModelChoiceField(queryset=Participant.objects.none(), label="المشارك")
    points = forms.IntegerField(label="عدد النقاط", min_value=-1000, max_value=1000)
    reason = forms.CharField(label="السبب", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        # The allowed participant queryset is scoped by the view (a group
        # supervisor only sees their own group's participants) — this is a
        # security boundary, not just a UI convenience, so clean_participant
        # below re-checks it against a tampered submission.
        queryset = kwargs.pop("participant_queryset")
        super().__init__(*args, **kwargs)
        self.fields["participant"].queryset = queryset

    def clean_participant(self):
        participant = self.cleaned_data["participant"]
        if not self.fields["participant"].queryset.filter(id=participant.id).exists():
            raise forms.ValidationError("لا يمكنك منح نقاط لهذا المشارك")
        return participant


class StoreProductForm(forms.ModelForm):
    class Meta:
        model = StoreProduct
        fields = ["name", "description", "image", "price", "stock"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class SingleParticipantForm(forms.Form):
    full_name = forms.CharField(label="الاسم الكامل", max_length=100)
    national_id = forms.CharField(label="رقم الهوية / الإقامة", max_length=10)
    group = forms.ModelChoiceField(
        queryset=Group.objects.none(), label="البيئة", required=False
    )
    academic_stage = forms.ChoiceField(
        label="المرحلة الدراسية", choices=AcademicStage.choices
    )
    phone = forms.CharField(label="رقم جوال المشارك", max_length=20, required=False)
    guardian_phone = forms.CharField(
        label="رقم جوال ولي الأمر", max_length=20, required=False
    )

    def __init__(self, *args, **kwargs):
        # The allowed group queryset and an optional locked group are scoped
        # by the view (a group supervisor is limited to their own
        # environment) — this is a security boundary the view re-checks
        # itself in AddParticipantView.form_valid rather than trusting
        # `disabled` alone, since a direct POST can still include a
        # different group value.
        group_queryset = kwargs.pop("group_queryset")
        lock_group = kwargs.pop("lock_group", None)
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = group_queryset
        if lock_group:
            self.fields["group"].initial = lock_group
            self.fields["group"].disabled = True

    def clean_national_id(self):
        national_id = self.cleaned_data["national_id"]
        if len(national_id) != 10 or not national_id.isdigit():
            raise forms.ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام")
        if User.objects.filter(national_id=national_id).exists():
            raise forms.ValidationError("رقم الهوية مسجّل مسبقًا")
        return national_id
