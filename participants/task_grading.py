"""Explicit grading corrections, separate from ordinary one-time review."""
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Role
from .models import Participant, TaskSubmission, TaskGradeRevision, PointsLedgerEntry


def can_revise_grades(user):
    return user.is_authenticated and user.is_active and user.role == Role.GENERAL_SUPERVISOR


class GradeRevisionForm(forms.Form):
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


def revise_submission_grade(request):
    if not can_revise_grades(request.user):
        raise PermissionDenied("تعديل التقييم متاح للمشرف العام النشط فقط.")
    form = GradeRevisionForm(request.POST)
    if not form.is_valid():
        return False, " ".join(str(error) for errors in form.errors.values() for error in errors)
    raw_id = request.POST.get("submission_id", "")
    if not raw_id.isdecimal():
        from django.http import Http404
        raise Http404("تسليم غير متاح")
    if "review_version" not in request.POST:
        return False, "حدّث الصفحة وافتح نموذج التعديل مجددًا."
    raw_version = request.POST.get("review_version", "")
    try:
        version = parse_datetime(raw_version) if raw_version else None
    except (ValueError, TypeError):
        version = None
    if raw_version and (version is None or timezone.is_naive(version)):
        return False, "نسخة التقييم غير صالحة. حدّث الصفحة."

    with transaction.atomic():
        # Block role/activity edits without blocking FK KEY SHARE checks from
        # concurrent attendance or ordinary reviews performed by this actor.
        actor = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        if not can_revise_grades(actor):
            raise PermissionDenied("لم تعد صلاحية تعديل التقييم متاحة لهذا الحساب.")
        submission = get_object_or_404(
            TaskSubmission.objects.select_for_update(), pk=raw_id,
        )
        if submission.status not in (TaskSubmission.Status.ACCEPTED, TaskSubmission.Status.REJECTED):
            return False, "هذا التسليم ليس تقييمًا نهائيًا. استخدم أدوات المراجعة الحالية."
        if submission.reviewed_at != version:
            return False, "عُدّل التقييم بعد فتح الصفحة؛ حدّثها قبل حفظ قرار آخر."
        old_award = submission.award_value
        if old_award is None:
            if submission.points_awarded is False:
                old_award = 0
            else:
                raw_award = form.cleaned_data["previous_award"]
                if raw_award not in ("0", "10", "12"):
                    return False, "هذا تسليم سابق: حدد قيمة النقاط التي صُرفت له أولًا دون تخمين."
                old_award = int(raw_award)
        if old_award not in (0, 10, 12):
            return False, "قيمة المنحة السابقة غير صالحة؛ يلزم التحقق من السجل."
        grade = form.cleaned_data["grade"]
        new_award = {"featured": 12, "accepted": 10, "rejected": 0}[grade]
        new_status = TaskSubmission.Status.REJECTED if grade == "rejected" else TaskSubmission.Status.ACCEPTED
        new_featured = grade == "featured"
        rejection_reason = (form.cleaned_data["rejection_reason"] or form.cleaned_data["reason"]) if grade == "rejected" else ""
        participant = Participant.objects.select_for_update().get(pk=submission.participant_id)
        if (
            submission.status == new_status and submission.is_featured == new_featured
            and old_award == new_award and submission.rejection_reason == rejection_reason
            and submission.award_value is not None
        ):
            return True, "التقييم محفوظ بالفعل؛ لم تُكرر أي نقاط."
        TaskGradeRevision.objects.create(
            submission=submission, old_status=submission.status, new_status=new_status,
            old_featured=submission.is_featured, new_featured=new_featured,
            old_award=old_award, new_award=new_award,
            reason=form.cleaned_data["reason"], revised_by=actor,
        )
        delta = new_award - old_award
        if delta:
            # Local import preserves the project's existing module structure.
            from .views import apply_points_delta
            apply_points_delta(
                participant, delta, source=PointsLedgerEntry.Source.WEEKLY_TASK,
                description=f'تعديل تقييم مهمة "{submission.task.title}": {old_award} ← {new_award}',
                granted_by=actor, event_date=timezone.localdate(submission.submitted_at),
            )
        submission.status = new_status
        submission.is_featured = new_featured
        submission.rejection_reason = rejection_reason
        submission.award_value = new_award
        submission.points_awarded = new_award > 0
        submission.reviewed_by = actor
        submission.reviewed_at = timezone.now()
        submission.save(update_fields=[
            "status", "is_featured", "rejection_reason", "award_value",
            "points_awarded", "reviewed_by", "reviewed_at",
        ])
    return True, "تم تعديل تقييم التسليم وحفظ فرق النقاط وسجل التغيير."


class GradeCancellationForm(forms.Form):
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


def cancel_submission_grade(request):
    """Cancel one reviewed decision and its confirmed award, without reopening upload."""
    if not can_revise_grades(request.user):
        raise PermissionDenied("إلغاء التقييم متاح للمشرف العام النشط فقط.")
    form = GradeCancellationForm(request.POST)
    if not form.is_valid():
        return False, " ".join(str(error) for errors in form.errors.values() for error in errors)
    raw_id = request.POST.get("submission_id", "")
    # Keep malformed/absent IDs as 404, including values exceeding BigAutoField
    # range: never pass a huge integer to SQLite or PostgreSQL's driver.
    if not raw_id.isdecimal() or len(raw_id) > 19 or int(raw_id) > 9223372036854775807:
        raise Http404("تسليم غير متاح")
    if "review_version" not in request.POST:
        return False, "حدّث الصفحة وافتح نموذج الإلغاء مجددًا."
    raw_version = request.POST.get("review_version", "")
    try:
        version = parse_datetime(raw_version) if raw_version else None
    except (ValueError, TypeError, OverflowError):
        version = None
    if raw_version and (version is None or timezone.is_naive(version)):
        return False, "نسخة التقييم غير صالحة. حدّث الصفحة."

    with transaction.atomic():
        # Match corrections' actor -> submission -> participant lock order.
        # NO KEY UPDATE allows concurrent audit FK KEY SHARE checks to finish.
        actor = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        if not can_revise_grades(actor):
            raise PermissionDenied("لم تعد صلاحية إلغاء التقييم متاحة لهذا الحساب.")
        submission = get_object_or_404(
            TaskSubmission.objects.select_for_update(), pk=raw_id,
        )
        if submission.status not in (
            TaskSubmission.Status.ACCEPTED, TaskSubmission.Status.REJECTED,
            TaskSubmission.Status.REVISION_REQUIRED,
        ):
            return False, "هذا التسليم قيد المراجعة بالفعل؛ لم يُلغَ تقييم أو تُخصم نقاط."
        if submission.reviewed_at != version:
            return False, "تغيّر التقييم بعد فتح الصفحة؛ حدّثها قبل إلغائه."
        old_award = submission.award_value
        if old_award is None:
            if submission.points_awarded is False:
                old_award = 0
            else:
                raw_award = form.cleaned_data["previous_award"]
                if raw_award not in ("0", "10", "12"):
                    return False, "هذا تسليم سابق: حدد قيمة النقاط التي صُرفت له أولًا دون تخمين."
                old_award = int(raw_award)
        if old_award not in (0, 10, 12):
            return False, "قيمة المنحة السابقة غير صالحة؛ يلزم التحقق من السجل."
        participant = Participant.objects.select_for_update().get(pk=submission.participant_id)
        reason_parts = ["إلغاء التقييم لإعادة المراجعة"]
        # The audit TextField has no length limit. Preserve the entire old
        # review context here before clearing active, participant-facing notes.
        # Never copy these notes into ledger descriptions or application logs.
        if submission.reviewed_by_id is not None:
            reason_parts.append(f"معرف المراجع السابق: {submission.reviewed_by_id}")
        if submission.reviewed_at is not None:
            reason_parts.append(f"وقت المراجعة السابقة: {submission.reviewed_at.isoformat()}")
        if submission.rejection_reason:
            reason_parts.append(f"سبب الرفض السابق: {submission.rejection_reason}")
        if submission.revision_note:
            reason_parts.append(f"ملاحظة التعديل السابقة: {submission.revision_note}")
        TaskGradeRevision.objects.create(
            submission=submission, old_status=submission.status,
            new_status=TaskSubmission.Status.PENDING,
            old_featured=submission.is_featured, new_featured=False,
            old_award=old_award, new_award=0,
            reason="\n".join(reason_parts), revised_by=actor,
        )
        if old_award:
            from .views import apply_points_delta
            apply_points_delta(
                participant, -old_award, source=PointsLedgerEntry.Source.WEEKLY_TASK,
                description=f'إلغاء تقييم مهمة "{submission.task.title}": {old_award} ← 0'[:255],
                granted_by=actor, event_date=timezone.localdate(submission.submitted_at),
            )
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
