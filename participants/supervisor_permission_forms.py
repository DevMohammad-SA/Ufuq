"""Explicit checkbox-only student/report/grade policy; no identity or role fields."""

from django import forms

from accounts.models import Role
from .models import TaskReviewPermission


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
    edit_task_grades = forms.BooleanField(
        label="تعديل تقييم المهام والتميز", required=False,
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
        help_text="إذن مستقل لتعديل التقييم وإضافة أو إزالة التميز؛ يتطلب إذن التصحيح الحالي للبيئة والمحدود.",
    )
    task_grade_permissions_present = forms.ChoiceField(
        choices=[("yes", "نموذج صلاحية التقييم")], required=False,
        widget=forms.HiddenInput, initial="yes",
    )
    confirm = forms.BooleanField(
        label="أؤكد حفظ صلاحيات الطلاب والتقارير والتقييم لهذا المشرف", required=True,
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
    )

    def __init__(self, *args, supervisor, capability=None, report_permission=None,
                 task_grade_permission=None, **kwargs):
        self.supervisor = supervisor
        self.capability = capability
        inherited = supervisor.role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR)
        kwargs.setdefault("initial", {
            "manage_students": supervisor.role == Role.GENERAL_SUPERVISOR or bool(
                capability is not None and capability.manage_students
            ),
            "view_reports": report_permission.view_reports if report_permission is not None else inherited,
            "prepare_reports": report_permission.prepare_reports if report_permission is not None else inherited,
            "edit_task_grades": task_grade_permission.enabled if task_grade_permission is not None else (
                supervisor.role == Role.GENERAL_SUPERVISOR
            ),
            "task_grade_permissions_present": "yes",
        })
        super().__init__(*args, **kwargs)
        if supervisor.role == Role.GENERAL_SUPERVISOR:
            self.fields["manage_students"].disabled = True
            self.fields["manage_students"].initial = True
            self.initial["manage_students"] = True
            self.fields["manage_students"].help_text = "صلاحية موروثة من دور المشرف العام؛ لا تمنح أو تسحب من هذه الصفحة."

    def values(self, name):
        if hasattr(self.data, "getlist"):
            return self.data.getlist(name)
        if name in self.data:
            value = self.data[name]
            return value if isinstance(value, list) else [value]
        return []

    def clean(self):
        data = super().clean()
        # BooleanField accepts arbitrary truthy strings and QueryDict takes the
        # last value. Neither is an acceptable authorization contract.
        for name in ("manage_students", "view_reports", "prepare_reports", "edit_task_grades", "confirm"):
            values = self.values(name)
            if values not in ([], ["yes"]) or (name == "confirm" and values != ["yes"]):
                self.add_error(name, "قيمة غير صحيحة؛ استخدم مربع الاختيار مرة واحدة")
        marker = self.values("task_grade_permissions_present")
        if marker not in ([], ["yes"]) or ("task_grade_permissions_present" in self.data and marker != ["yes"]):
            self.add_error("task_grade_permissions_present", "علامة نموذج التقييم غير صحيحة؛ حدّث الصفحة")
        if "edit_task_grades" in self.data and marker != ["yes"]:
            self.add_error("task_grade_permissions_present", "حدّث الصفحة قبل حفظ صلاحية تعديل التقييم")
        if data.get("prepare_reports") and not data.get("view_reports"):
            self.add_error("prepare_reports", "إعداد وتصدير التقارير يتطلب إذن الاطلاع عليها")
        if not self.supervisor.is_active and (
            data.get("view_reports") or data.get("prepare_reports") or data.get("edit_task_grades")
            or (self.supervisor.role != Role.GENERAL_SUPERVISOR and data.get("manage_students"))
        ):
            self.add_error(None, "لا يمكن منح صلاحيات لحساب معطل؛ يمكن سحب الصلاحيات فقط")
        if data.get("edit_task_grades"):
            if self.supervisor.role == Role.GROUP_SUPERVISOR and not TaskReviewPermission.objects.filter(
                supervisor_id=self.supervisor.pk, enabled=True,
            ).exists():
                self.add_error("edit_task_grades", "امنح إذن تصحيح المهام لمشرف البيئة أولًا")
            if self.supervisor.role == Role.LIMITED_SUPERVISOR and not (
                self.capability is not None and self.capability.follow_tasks
            ):
                self.add_error("edit_task_grades", "امنح متابعة وتصحيح المهام للمشرف المحدود أولًا")
        return data
