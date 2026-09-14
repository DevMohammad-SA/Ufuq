from django import forms

from accounts.models import User
from .models import (
    AcademicStage,
    CircleAttendance,
    Group,
    Participant,
    StoreProduct,
    SUBMISSION_FORMAT_EXTENSIONS,
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
    allowed_formats = forms.MultipleChoiceField(
        choices=WeeklyTask.AllowedFormat.choices,
        widget=forms.CheckboxSelectMultiple,
        label="الصيغ المسموحة",
    )

    class Meta:
        model = WeeklyTask
        fields = ["title", "description", "due_date", "allowed_formats"]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-check the boxes matching an existing task's stored
        # comma-separated value when editing (WeeklyTaskForm is currently
        # only ever used to create a new task, but this keeps the form
        # correct if that changes).
        if self.instance and self.instance.pk:
            self.fields["allowed_formats"].initial = (
                self.instance.get_allowed_formats_list()
            )

    def clean_allowed_formats(self):
        return ",".join(self.cleaned_data["allowed_formats"])


class TaskSubmissionForm(forms.ModelForm):
    text_content = forms.CharField(
        label="المحتوى النصي",
        widget=forms.Textarea(attrs={"rows": 6}),
        required=False,
    )

    class Meta:
        model = TaskSubmission
        fields = ["file", "text_content"]

    # Server-side gate: an uploaded file's extension must match one of the
    # task's allowed formats. The browser's file picker `accept` attribute
    # is trivially bypassed, so this check is the real enforcement.
    # Shared with TaskSubmission.get_submitted_format() (see models.py) so
    # the two never drift apart.
    FORMAT_EXTENSIONS = SUBMISSION_FORMAT_EXTENSIONS

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
        # submission (file extension or text) against its allowed_formats —
        # passed explicitly by the view rather than inferred from
        # initial/instance data.
        self.task = kwargs.pop("task", None)
        super().__init__(*args, **kwargs)
        self.fields["file"].required = False

    def clean(self):
        cleaned_data = super().clean()
        text_content = cleaned_data.get("text_content", "").strip()
        cleaned_data["text_content"] = text_content

        # A file actually uploaded in *this* request. Deliberately NOT
        # cleaned_data.get("file") — Django's FileField.clean() silently
        # falls back to the instance's EXISTING file when no new upload is
        # present (so a normal edit that doesn't touch the file field
        # doesn't null it out). Without this, a resubmission that switches
        # from a file to a text answer would carry the old file forward and
        # look like "both provided" below.
        file = self.files.get("file")

        if not file and not text_content:
            raise forms.ValidationError("يجب تقديم ملف أو نص، حسب الصيغة المطلوبة لهذه المهمة")

        if file and text_content:
            raise forms.ValidationError("قدّم ملفًا أو نصًا فقط، وليس كليهما معًا")

        if self.task:
            allowed = self.task.get_allowed_formats_list()

            if text_content and "text" not in allowed:
                raise forms.ValidationError("هذه المهمة لا تقبل التسليم النصي")

            if file:
                filename = file.name.lower()
                matched_format = None
                for fmt in allowed:
                    extensions = self.FORMAT_EXTENSIONS.get(fmt, [])
                    if any(filename.endswith(ext) for ext in extensions):
                        matched_format = fmt
                        break

                if not matched_format:
                    allowed_display = "، أو ".join(
                        self.task.get_allowed_formats_display_list()
                    )
                    raise forms.ValidationError(
                        f"صيغة الملف غير مقبولة لهذه المهمة. الصيغ المطلوبة: {allowed_display}"
                    )

                # Size ceiling — checked only after the extension is
                # accepted, against the specific format the file matched
                # (not just whichever format happens to come first in the
                # task's allowed list). Images have no entry here
                # (compressed on save, never rejected).
                max_size = self.MAX_FILE_SIZES.get(matched_format)
                if max_size and file.size > max_size:
                    max_mb = max_size // (1024 * 1024)
                    raise forms.ValidationError(
                        f"حجم الملف يتجاوز الحد المسموح ({max_mb} ميجابايت) لهذا النوع."
                    )

        # A text-only submission must not keep a stale file from an earlier
        # attempt on the same row (a reopened resubmission reuses the same
        # TaskSubmission instance). False is Django's FileField sentinel for
        # "clear this field" — distinct from None, which means "no change"
        # and would otherwise leave the old file in place on save().
        if text_content:
            cleaned_data["file"] = False

        return cleaned_data


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
