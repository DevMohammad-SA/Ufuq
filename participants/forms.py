from django import forms

from .models import CircleAttendance, MeetingAttendance, Participant


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


class MeetingAttendanceForm(forms.ModelForm):
    participant = forms.ModelChoiceField(
        queryset=Participant.objects.all(),
        empty_label=None,
        label="المشارك",
    )

    class Meta:
        model = MeetingAttendance
        fields = ["participant", "week_start_date", "attended", "is_early"]
        widgets = {
            "week_start_date": forms.DateInput(attrs={"type": "date"}),
        }
