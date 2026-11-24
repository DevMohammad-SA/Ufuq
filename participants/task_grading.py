"""Explicit grading corrections, separate from ordinary one-time review."""
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Participant, TaskSubmission, TaskGradeRevision, PointsLedgerEntry


MAX_SUBMISSION_ID = 9223372036854775807
MAX_REVIEW_VERSION_LENGTH = 64


def can_revise_grades(user):
    # Keep the public compatibility helper, but use the same fresh permission
    # policy for direct corrections, legacy cancellations and quick featuring.
    from .capabilities import can_edit_task_grades
    return can_edit_task_grades(user)


def _scalar_values(data, name):
    if hasattr(data, "getlist"):
        return data.getlist(name)
    if name not in data:
        return []
    value = data[name]
    return value if isinstance(value, (list, tuple)) else [value]


class _ScalarForm(forms.Form):
    """Never let QueryDict's last-value behavior choose a grading decision."""

    def clean(self):
        cleaned = super().clean()
        for name in self.fields:
            values = _scalar_values(self.data, name)
            if len(values) > 1 or (values and not isinstance(values[0], str)):
                self.add_error(name, "أرسل قيمة واحدة فقط لكل حقل.")
        return cleaned


class GradeRevisionForm(_ScalarForm):
    grade = forms.ChoiceField(
        choices=[("featured", "متميز"), ("accepted", "قبول"), ("rejected", "رفض")],
        label="التقييم الجديد", error_messages={"required": "اختر التقييم الجديد."},
    )
    reason = forms.CharField(
        max_length=2000, label="سبب التعديل",
        error_messages={"required": "اكتب سبب تعديل التقييم.", "max_length": "سبب التعديل لا يتجاوز 2000 حرف."},
    )
    rejection_reason = forms.CharField(max_length=2000, required=False)
    previous_award = forms.ChoiceField(
        choices=[("0", "لم تصرف نقاط"), ("10", "صرفت 10 نقاط"), ("12", "صرفت 12 نقطة")],
        required=False,
    )


class GradeCancellationForm(_ScalarForm):
    previous_award = forms.ChoiceField(
        choices=[("0", "لم تصرف نقاط"), ("10", "صرفت 10 نقاط"), ("12", "صرفت 12 نقطة")],
        required=False, label="المنحة السابقة المؤكدة",
        error_messages={"invalid_choice": "حدد قيمة المنحة السابقة: 0 أو 10 أو 12 فقط."},
    )
    confirm = forms.ChoiceField(
        choices=[("yes", "تأكيد إلغاء التقييم")], widget=forms.HiddenInput,
        error_messages={
            "required": "يجب تأكيد إلغاء التقييم أولًا.",
            "invalid_choice": "يجب تأكيد إلغاء التقييم أولًا.",
        },
    )


class FeaturedAdjustmentForm(_ScalarForm):
    is_featured = forms.ChoiceField(
        choices=[("yes", "إضافة التميز"), ("no", "إزالة التميز")],
        error_messages={
            "required": "اختر إضافة التميز أو إزالته.",
            "invalid_choice": "قيمة التميز يجب أن تكون yes أو no فقط.",
        },
    )
    previous_award = forms.ChoiceField(
        choices=[("10", "صرفت 10 نقاط"), ("12", "صرفت 12 نقطة")],
        required=False, label="المنحة السابقة المؤكدة",
        error_messages={"invalid_choice": "حدد قيمة المنحة السابقة: 10 أو 12 فقط."},
    )


def _form_error(form):
    return " ".join(str(error) for errors in form.errors.values() for error in errors)


def _request_metadata(data):
    """Bound scalars before parsing or passing integers to database drivers."""
    ids = _scalar_values(data, "submission_id")
    if len(ids) != 1:
        raise Http404("تسليم غير متاح")
    raw_id = ids[0]
    if (
        not isinstance(raw_id, str) or not raw_id or len(raw_id) > 19
        or not raw_id.isascii() or not raw_id.isdecimal()
    ):
        raise Http404("تسليم غير متاح")
    submission_id = int(raw_id)
    if not 0 < submission_id <= MAX_SUBMISSION_ID:
        raise Http404("تسليم غير متاح")
    versions = _scalar_values(data, "review_version")
    if len(versions) != 1:
        return submission_id, None, "حدّث الصفحة وافتح النموذج مجددًا؛ أرسل نسخة تقييم واحدة."
    actions = _scalar_values(data, "action")
    if len(actions) > 1:
        return submission_id, None, "أرسل إجراءً واحدًا فقط."
    raw_version = versions[0]
    if not isinstance(raw_version, str) or len(raw_version) > MAX_REVIEW_VERSION_LENGTH:
        return submission_id, None, "نسخة التقييم غير صالحة. حدّث الصفحة."
    try:
        version = parse_datetime(raw_version) if raw_version else None
        if raw_version and (version is None or timezone.is_naive(version)):
            return submission_id, None, "نسخة التقييم غير صالحة. حدّث الصفحة."
    except (ValueError, TypeError, OverflowError):
        return submission_id, None, "نسخة التقييم غير صالحة. حدّث الصفحة."
    # An explicitly present empty scalar is the legacy reviewed_at=None token.
    return submission_id, version, None


def _locked_subject(request, submission_id):
    """Called inside atomic: User(NO KEY) -> scoped submission(self) -> student."""
    from .access import scoped_submissions
    original_role = getattr(request.user, "role", None)
    actor = get_user_model().objects.select_for_update(no_key=True).filter(
        pk=request.user.pk,
    ).first()
    if actor is None or actor.role != original_role or not can_revise_grades(actor):
        raise PermissionDenied("لم تعد صلاحية تعديل التقييم متاحة لهذا الحساب.")
    submission = get_object_or_404(
        scoped_submissions(actor).select_for_update(of=("self",)), pk=submission_id,
    )
    participant = Participant.objects.select_for_update().get(pk=submission.participant_id)
    # Student management changes group while owning this Participant lock. An
    # initial scoped lookup may have preceded its commit: query scope anew now,
    # without cached relations, before any audit, currency or submission write.
    if not scoped_submissions(actor).filter(pk=submission.pk).exists():
        raise Http404("تسليم غير متاح")
    return actor, submission, participant


def _confirmed_old_award(submission, previous_award):
    old_award = submission.award_value
    if old_award is None:
        if submission.points_awarded is False:
            old_award = 0
        elif previous_award in ("0", "10", "12"):
            old_award = int(previous_award)
        else:
            return None, "هذا تسليم سابق: حدد قيمة النقاط التي صُرفت له أولًا دون تخمين."
    if old_award not in (0, 10, 12):
        return None, "قيمة المنحة السابقة غير صالحة؛ يلزم التحقق من السجل."
    return old_award, None


def _previous_context(submission, reason):
    # The audit TextField is unbounded: preserve old context in full, never in
    # ledger descriptions or logs, before replacing participant-facing notes.
    parts = [reason]
    if submission.reviewed_by_id is not None:
        parts.append(f"معرف المراجع السابق: {submission.reviewed_by_id}")
    if submission.reviewed_at is not None:
        parts.append(f"وقت المراجعة السابقة: {submission.reviewed_at.isoformat()}")
    if submission.rejection_reason:
        parts.append(f"سبب الرفض السابق: {submission.rejection_reason}")
    if submission.revision_note:
        parts.append(f"ملاحظة التعديل السابقة: {submission.revision_note}")
    return "\n".join(parts)


def _apply_award_delta(participant, submission, actor, old_award, new_award, label):
    delta = new_award - old_award
    if delta:
        from .views import apply_points_delta
        apply_points_delta(
            participant, delta, source=PointsLedgerEntry.Source.WEEKLY_TASK,
            description=f'{label} مهمة "{submission.task.title}": {old_award} ← {new_award}'[:255],
            granted_by=actor, event_date=timezone.localdate(submission.submitted_at),
        )


def revise_submission_grade(request):
    """Save a final decision directly, without cancellation or a PENDING step."""
    if not can_revise_grades(request.user):
        raise PermissionDenied("لا تملك صلاحية تعديل تقييم المهام.")
    form = GradeRevisionForm(request.POST)
    if not form.is_valid():
        return False, _form_error(form)
    submission_id, version, error = _request_metadata(request.POST)
    if error:
        return False, error

    with transaction.atomic():
        actor, submission, participant = _locked_subject(request, submission_id)
        if submission.status not in (
            TaskSubmission.Status.ACCEPTED, TaskSubmission.Status.REJECTED,
            TaskSubmission.Status.REVISION_REQUIRED,
        ):
            return False, "هذا التسليم قيد المراجعة؛ استخدم أدوات المراجعة الحالية."
        if submission.reviewed_at != version:
            return False, "عُدّل التقييم بعد فتح الصفحة؛ حدّثها قبل حفظ قرار آخر."
        old_award, error = _confirmed_old_award(submission, form.cleaned_data["previous_award"])
        if error:
            return False, error
        grade = form.cleaned_data["grade"]
        new_award = {"featured": 12, "accepted": 10, "rejected": 0}[grade]
        new_status = TaskSubmission.Status.REJECTED if grade == "rejected" else TaskSubmission.Status.ACCEPTED
        new_featured = grade == "featured"
        rejection_reason = (form.cleaned_data["rejection_reason"] or form.cleaned_data["reason"]) if grade == "rejected" else ""
        if (
            submission.status == new_status and submission.is_featured == new_featured
            and submission.award_value == new_award and old_award == new_award
            and submission.points_awarded == (new_award > 0)
            and submission.rejection_reason == rejection_reason and not submission.revision_note
        ):
            return True, "التقييم محفوظ بالفعل؛ لم تُكرر أي نقاط."
        TaskGradeRevision.objects.create(
            submission=submission, old_status=submission.status, new_status=new_status,
            old_featured=submission.is_featured, new_featured=new_featured,
            old_award=old_award, new_award=new_award,
            reason=_previous_context(submission, form.cleaned_data["reason"]), revised_by=actor,
        )
        _apply_award_delta(participant, submission, actor, old_award, new_award, "تعديل تقييم")
        submission.status = new_status
        submission.is_featured = new_featured
        submission.rejection_reason = rejection_reason
        submission.revision_note = ""
        # An explicit archive-admin reopening grant is separate from RETURNED.
        # Preserve it; final status alone ends the ordinary returned upload path.
        submission.award_value = new_award
        submission.points_awarded = new_award > 0
        submission.reviewed_by = actor
        submission.reviewed_at = timezone.now()
        submission.save(update_fields=[
            "status", "is_featured", "rejection_reason", "revision_note", "award_value",
            "points_awarded", "reviewed_by", "reviewed_at",
        ])
    return True, "تم تعديل تقييم التسليم وحفظ فرق النقاط وسجل التغيير."


def cancel_submission_grade(request):
    """Legacy cancellation, with the same permission/scope as direct correction."""
    if not can_revise_grades(request.user):
        raise PermissionDenied("لا تملك صلاحية إلغاء تقييم المهام.")
    form = GradeCancellationForm(request.POST)
    if not form.is_valid():
        return False, _form_error(form)
    submission_id, version, error = _request_metadata(request.POST)
    if error:
        return False, error

    with transaction.atomic():
        actor, submission, participant = _locked_subject(request, submission_id)
        if submission.status not in (
            TaskSubmission.Status.ACCEPTED, TaskSubmission.Status.REJECTED,
            TaskSubmission.Status.REVISION_REQUIRED,
        ):
            return False, "هذا التسليم قيد المراجعة بالفعل؛ لم يُلغَ تقييم أو تُخصم نقاط."
        if submission.reviewed_at != version:
            return False, "تغيّر التقييم بعد فتح الصفحة؛ حدّثها قبل إلغائه."
        old_award, error = _confirmed_old_award(submission, form.cleaned_data["previous_award"])
        if error:
            return False, error
        TaskGradeRevision.objects.create(
            submission=submission, old_status=submission.status,
            new_status=TaskSubmission.Status.PENDING,
            old_featured=submission.is_featured, new_featured=False,
            old_award=old_award, new_award=0,
            reason=_previous_context(submission, "إلغاء التقييم لإعادة المراجعة"), revised_by=actor,
        )
        _apply_award_delta(participant, submission, actor, old_award, 0, "إلغاء تقييم")
        submission.status = TaskSubmission.Status.PENDING
        submission.is_featured = False
        submission.award_value = 0
        submission.points_awarded = False
        submission.reviewed_by = None
        submission.reviewed_at = None
        submission.rejection_reason = ""
        submission.revision_note = ""
        submission.reopened_for_resubmission = False
        submission.save(update_fields=[
            "status", "is_featured", "award_value", "points_awarded",
            "reviewed_by", "reviewed_at", "rejection_reason", "revision_note",
            "reopened_for_resubmission",
        ])
    return True, "أُلغي التقييم ومنحته؛ التسليم قيد المراجعة مجددًا."


def set_submission_featured(request):
    """Absolute yes/no on ACCEPTED work: only the confirmed +/-2 difference."""
    if not can_revise_grades(request.user):
        raise PermissionDenied("لا تملك صلاحية تعديل تميز المهام.")
    form = FeaturedAdjustmentForm(request.POST)
    if not form.is_valid():
        return False, _form_error(form)
    submission_id, version, error = _request_metadata(request.POST)
    if error:
        return False, error

    with transaction.atomic():
        actor, submission, participant = _locked_subject(request, submission_id)
        if submission.status != TaskSubmission.Status.ACCEPTED:
            return False, "تعديل التميز متاح للتسليم المقبول فقط؛ استخدم تعديل التقييم للحالات الأخرى."
        if submission.reviewed_at != version:
            return False, "عُدّل التقييم بعد فتح الصفحة؛ حدّثها قبل تغيير التميز."
        old_award, error = _confirmed_old_award(submission, form.cleaned_data["previous_award"])
        if error:
            return False, error
        if old_award not in (10, 12):
            return False, "تعديل التميز يتطلب منحة قبول مؤكدة 10 أو 12؛ استخدم تعديل التقييم أولًا."
        new_featured = form.cleaned_data["is_featured"] == "yes"
        new_award = 12 if new_featured else 10
        if (
            submission.is_featured == new_featured and submission.award_value == new_award
            and submission.points_awarded is True
        ):
            return True, "التميز محفوظ بالفعل؛ لم تُكرر أي نقاط."
        reason = "إضافة التميز إلى تسليم مقبول" if new_featured else "إزالة التميز من تسليم مقبول"
        TaskGradeRevision.objects.create(
            submission=submission, old_status=submission.status, new_status=submission.status,
            old_featured=submission.is_featured, new_featured=new_featured,
            old_award=old_award, new_award=new_award,
            reason=_previous_context(submission, reason), revised_by=actor,
        )
        _apply_award_delta(participant, submission, actor, old_award, new_award, "تعديل تميز")
        submission.is_featured = new_featured
        submission.award_value = new_award
        submission.points_awarded = True
        submission.reviewed_by = actor
        submission.reviewed_at = timezone.now()
        # Keep status, work, dates, rejection/return notes and explicit reopening.
        submission.save(update_fields=[
            "is_featured", "award_value", "points_awarded", "reviewed_by", "reviewed_at",
        ])
    return True, "تم تحديث تميز التسليم وحفظ فرق النقاط وسجل التغيير."
