"""Explicit checkbox-only student/report policy; no identity or role fields."""

from django import forms

from accounts.models import Role


class SupervisorStudentReportsForm(forms.Form):
    manage_students = forms.BooleanField(
        label="إدارة الطلاب", required=False,
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
    )
    view_reports = forms.BooleanField(
        label="الاطلاع على التقارير", required=False,
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
    )
    prepare_reports = forms.BooleanField(
        label="إعداد وتصدير التقارير", required=False,
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
        help_text="إعداد وتصدير التقارير يتطلب إذن الاطلاع عليها.",
    )
    confirm = forms.BooleanField(
        label="أؤكد حفظ صلاحيات الطلاب والتقارير لهذا المشرف", required=True,
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
    )

    def __init__(self, *args, supervisor, capability=None, report_permission=None, **kwargs):
        self.supervisor = supervisor
        inherited = supervisor.role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR)
        kwargs.setdefault("initial", {
            "manage_students": supervisor.role == Role.GENERAL_SUPERVISOR or bool(
                capability is not None and capability.manage_students
            ),
            "view_reports": report_permission.view_reports if report_permission is not None else inherited,
            "prepare_reports": report_permission.prepare_reports if report_permission is not None else inherited,
        })
        super().__init__(*args, **kwargs)
        if supervisor.role == Role.GENERAL_SUPERVISOR:
            self.fields["manage_students"].disabled = True
            self.fields["manage_students"].initial = True
            self.initial["manage_students"] = True
            self.fields["manage_students"].help_text = "صلاحية موروثة من دور المشرف العام؛ لا تمنح أو تسحب من هذه الصفحة."

    def clean(self):
        data = super().clean()
        # BooleanField normally accepts arbitrary truthy strings and QueryDict
        # takes the last value. Neither is an acceptable authorization contract.
        for name in ("manage_students", "view_reports", "prepare_reports", "confirm"):
            if hasattr(self.data, "getlist"):
                values = self.data.getlist(name)
            elif name in self.data:
                value = self.data[name]
                values = value if isinstance(value, list) else [value]
            else:
                values = []
            if values not in ([], ["yes"]) or (name == "confirm" and values != ["yes"]):
                self.add_error(name, "قيمة غير صحيحة؛ استخدم مربع الاختيار مرة واحدة")
        if data.get("prepare_reports") and not data.get("view_reports"):
            self.add_error("prepare_reports", "إعداد وتصدير التقارير يتطلب إذن الاطلاع عليها")
        if not self.supervisor.is_active and (
            data.get("view_reports") or data.get("prepare_reports")
            or (self.supervisor.role != Role.GENERAL_SUPERVISOR and data.get("manage_students"))
        ):
            self.add_error(None, "لا يمكن منح صلاحيات لحساب معطل؛ يمكن سحب الصلاحيات فقط")
        return data
