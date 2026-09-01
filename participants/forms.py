from django import forms

from .models import CircleAttendance, Participant, TaskSubmission, WeeklyTask


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
