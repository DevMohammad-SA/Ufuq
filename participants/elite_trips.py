"""Private trip declarations, with no attendance/point mutations or GET writes.

Lock order: ALL actor/candidate User rows globally ascending (NO KEY UPDATE),
then trip, Participants ascending, Groups ascending, nominations ascending.
User locks precede program rows just like account-management/lifecycle writes.
No nullable joined table is ever locked. Users/eligibility are re-read after waits.
"""
import hashlib
import json
from copy import copy
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from accounts.models import Role, User
from .elite_trip_forms import (EliteTripForm, GuardianTripConsentForm, TRIP_FIELDS,
                              CONSENT_FIELDS, signature_data_url, normalize_signature)
from .models import EliteTrip, EliteTripNomination, EliteTripAudit, GuardianTripConsent, Group, Participant

VERIFICATION_DISCLOSURE = "هذه الاستمارة أُرسلت عبر حساب الطالب؛ لم يُتحقق من هوية ولي الأمر برمز أو شهادة رقمية."
DECLARATION_TEXT = "أقر بأنني ولي أمر الطالب أو المخوّل برعايته، وأنني اطلعت على تفاصيل الرحلة وأرسلت هذا القرار بنفسي عبر حساب الطالب، وأفهم أن هويتي لم تُوثّق برمز أو شهادة رقمية."
PRIVACY_TEXT = "تُحفظ بيانات هذه الاستمارة والتوقيع بصورة خاصة لمتابعة الرحلة وأرشفة القرار، ولا يطلع عليها إلا المشرف العام وصاحب حساب الطالب. لا تُعد هذه الاستمارة إثباتًا موثّقًا لهوية ولي الأمر."
RIYADH = ZoneInfo("Asia/Riyadh")
REVIEW_SALT = "participants.elite-trip.guardian-review.v1"
REVIEW_MAX_AGE = 15 * 60
PUBLISHED_STATUSES = (EliteTrip.Status.APPROVED, EliteTrip.Status.CLOSED, EliteTrip.Status.CANCELLED)


def _fresh_user(user):
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return None
    return User.objects.filter(pk=user.pk, is_active=True, role=getattr(user, "role", None)).only(
        "pk", "role", "is_active", "is_staff", "is_superuser", "full_name",
    ).first()


def can_manage_elite_trips(user):
    actor = _fresh_user(user)
    return actor is not None and actor.role == Role.GENERAL_SUPERVISOR


def can_use_student_trips(user):
    actor = _fresh_user(user)
    return actor is not None and actor.role == Role.PARTICIPANT and not actor.is_staff and not actor.is_superuser


def _require_actor(user, *, student=False):
    check = can_use_student_trips if student else can_manage_elite_trips
    if not check(user):
        raise PermissionDenied("هذه الصفحة متاحة لصاحب حساب الطالب فقط." if student else "هذه الصفحة متاحة للمشرف العام النشط فقط.")


def _lock_users(user, *, roster=False, student=False):
    ids = {user.pk}
    if roster:
        ids.update(Participant.objects.values_list("user_id", flat=True))
    users = {item.pk: item for item in User.objects.filter(pk__in=ids).order_by("pk").select_for_update(no_key=True).only(
        "pk", "role", "is_active", "is_staff", "is_superuser", "full_name",
    )}
    actor = users.get(user.pk)
    if actor is None or actor.role != getattr(user, "role", None):
        raise PermissionDenied("تغيّرت صلاحية الحساب؛ أعد فتح الصفحة.")
    _require_actor(actor, student=student)
    return actor, users


def _id(raw):
    text = str(raw)
    if not text.isascii() or not text.isdecimal() or len(text) > 19 or not 0 < int(text) <= 9223372036854775807:
        raise ValidationError("مرجع الطالب أو نسخة الصفحة غير صالح.")
    return int(text)


def _single(data, name, required=True):
    values = data.getlist(name) if hasattr(data, "getlist") else ([data[name]] if name in data else [])
    if len(values) != 1:
        if required or values:
            raise ValidationError("أعد فتح الصفحة؛ الحقل المطلوب مفقود أو مكرر.")
        return ""
    return values[0]


def _version(trip, raw):
    if trip.version != _id(raw):
        raise ValidationError("تغيّرت الرحلة بعد فتح الصفحة؛ حدّثها قبل حفظ قرار جديد.")


def _confirm(raw):
    if raw != "yes":
        raise ValidationError("يجب تأكيد الإجراء صراحة؛ لم تُحفظ أي تغييرات.")


def _draft(trip):
    if trip.status != EliteTrip.Status.DRAFT or trip.approved_at is not None:
        raise ValidationError("لا يمكن تعديل الرحلة بعد اعتمادها؛ السجل محفوظ للعرض فقط.")


def _ordinary(user):
    return user.role == Role.PARTICIPANT and user.is_active and not user.is_staff and not user.is_superuser


def _locked_roster(users):
    participants = list(Participant.objects.order_by("pk").select_for_update().only("pk", "user_id", "group_id", "points"))
    if any(item.user_id not in users for item in participants):
        # A newly inserted account is not covered by the ordered User locks.
        raise ValidationError("تغيّر كشف الطلاب أثناء الحفظ؛ حدّث الصفحة وأعد المحاولة.")
    groups = {item.pk: item for item in Group.objects.order_by("pk").select_for_update()}
    eligible = [item for item in participants if item.group_id in groups and _ordinary(users[item.user_id])]
    eligible.sort(key=lambda item: (groups[item.group_id].name, item.group_id, -item.points, item.pk))
    ranks = {}
    counts = {}
    for item in eligible:
        counts[item.group_id] = counts.get(item.group_id, 0) + 1
        ranks[item.pk] = counts[item.group_id]
    return eligible, groups, ranks


def _audit(trip, actor, event, **data):
    # Only IDs/counts/version: never notes, names, signatures or auth metadata.
    EliteTripAudit.objects.create(trip=trip, actor=actor, event=event, data=data)


def _nomination_rows(trip, eligible, users, groups, ranks):
    existing = {item.participant_id: item for item in trip.nominations.order_by("pk").select_for_update()}
    new = []
    changed = []
    for participant in eligible:
        item = existing.get(participant.pk)
        if item is None:
            item = EliteTripNomination(trip=trip, participant=participant)
            new.append(item)
            existing[participant.pk] = item
        else:
            changed.append(item)
        item.student_name = users[participant.user_id].full_name
        item.group_name = groups[participant.group_id].name
        item.group_snapshot_id = participant.group_id
        item.points_snapshot = participant.points
        item.rank_snapshot = ranks[participant.pk]
    return existing, new, changed


def _persist_roster(new, changed):
    if new:
        EliteTripNomination.objects.bulk_create(new)
    if changed:
        EliteTripNomination.objects.bulk_update(changed, ["is_selected", "reason", "student_name", "group_name",
                                                         "group_snapshot_id", "points_snapshot", "rank_snapshot",
                                                         "frozen_member", "notified_at"])


def create_trip(actor):
    _require_actor(actor)
    with transaction.atomic():
        actor, users = _lock_users(actor, roster=True)
        eligible, groups, ranks = _locked_roster(users)
        trip = EliteTrip.objects.create(title="رحلة النخبة — مسودة جديدة", created_by=actor)
        rows = []
        for participant in eligible:
            rows.append(EliteTripNomination(
                trip=trip, participant=participant, is_selected=ranks[participant.pk] <= 13,
                student_name=users[participant.user_id].full_name, group_name=groups[participant.group_id].name,
                group_snapshot_id=participant.group_id, points_snapshot=participant.points, rank_snapshot=ranks[participant.pk],
            ))
        EliteTripNomination.objects.bulk_create(rows)
        _audit(trip, actor, "created", version=trip.version, roster_count=len(rows), selected_count=sum(row.is_selected for row in rows))
        return trip


def save_trip_draft(actor, trip_id, data):
    _require_actor(actor)
    with transaction.atomic():
        actor, users = _lock_users(actor, roster=True)
        trip = get_object_or_404(EliteTrip.objects.select_for_update(), pk=trip_id)
        _draft(trip)
        _version(trip, _single(data, "version"))
        for name in TRIP_FIELDS:
            _single(data, name, required=False)
        form = EliteTripForm(data, instance=trip)
        if not form.is_valid():
            raise ValidationError(form.errors.as_data())
        eligible, groups, ranks = _locked_roster(users)
        allowed = {item.pk for item in eligible}
        raw = data.getlist("selected_ids") if hasattr(data, "getlist") else data.get("selected_ids", [])
        if not isinstance(raw, (list, tuple)):
            raise ValidationError("قائمة المرشحين غير صالحة.")
        selected = [_id(value) for value in raw]
        if not selected or len(selected) != len(set(selected)) or not set(selected).issubset(allowed):
            raise ValidationError("اختر طالبًا مؤهلًا واحدًا على الأقل؛ المراجع الدخيلة أو المكررة أو غير النشطة غير مقبولة.")
        staged_ids = set(trip.nominations.values_list("participant_id", flat=True))
        reasons = {}
        for key in data:
            if key.startswith("reason_"):
                participant_id = _id(key[7:])
                if participant_id not in allowed | staged_ids:
                    raise ValidationError("سبب الاختيار يخص طالبًا خارج الكشف المؤهل.")
                reason = str(_single(data, key)).strip()
                if len(reason) > 250:
                    raise ValidationError("سبب الاختيار لا يتجاوز 250 حرفًا.")
                reasons[participant_id] = reason
        if trip.scope_group_id is not None and trip.scope_group_id not in groups:
            raise ValidationError("البيئة المحددة غير متاحة.")
        existing, new, changed = _nomination_rows(trip, eligible, users, groups, ranks)
        # All staged selections across environments are preserved by the submitted
        # global roster. Changing scope never discards another environment's choice.
        for participant_id, item in existing.items():
            item.is_selected = participant_id in selected
            item.reason = reasons.get(participant_id, item.reason)
            if participant_id not in allowed:
                changed.append(item)
        _persist_roster(new, changed)
        trip.version += 1
        trip.save(update_fields=[*TRIP_FIELDS, "version", "updated_at"])
        _audit(trip, actor, "draft_saved", version=trip.version, selected_count=len(selected))
        return trip


def trip_data(trip):
    if trip.approved_at is not None:
        return dict(trip.snapshot.get("trip", {}))
    result = {}
    for name in TRIP_FIELDS:
        value = getattr(trip, name + "_id") if name == "scope_group" else getattr(trip, name)
        result[name] = value.isoformat() if hasattr(value, "isoformat") else (str(value) if value is not None else "")
    return result


def document_texts(trip=None):
    texts = (trip.snapshot.get("form", {}) if trip and trip.approved_at else {})
    return {"verification_disclosure": texts.get("verification_disclosure", VERIFICATION_DISCLOSURE),
            "declaration_text": texts.get("declaration_text", DECLARATION_TEXT),
            "privacy_text": texts.get("privacy_text", PRIVACY_TEXT)}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def approve_trip(actor, trip_id, version, confirm):
    _require_actor(actor)
    _confirm(confirm)
    with transaction.atomic():
        actor, users = _lock_users(actor, roster=True)
        trip = get_object_or_404(EliteTrip.objects.select_for_update(), pk=trip_id)
        _draft(trip)
        _version(trip, version)
        complete = EliteTripForm(initial=None, data={name: getattr(trip, name + "_id") if name == "scope_group" else getattr(trip, name)
                                                   for name in TRIP_FIELDS}, require_complete=True, instance=trip)
        if not complete.is_valid():
            raise ValidationError(complete.errors.as_data())
        eligible, groups, ranks = _locked_roster(users)
        if trip.scope_group_id is not None and trip.scope_group_id not in groups:
            raise ValidationError("البيئة المحددة غير متاحة.")
        scoped = [item for item in eligible if trip.scope_group_id is None or item.group_id == trip.scope_group_id]
        eligible_ids = {item.pk for item in eligible}
        existing = list(trip.nominations.order_by("pk").select_for_update())
        # A previously selected account disabled/promoted since save cannot be
        # silently approved. Choices outside the current scope are kept untouched.
        current = {item.pk: item for item in eligible}
        for item in existing:
            participant = current.get(item.participant_id)
            relevant = (trip.scope_group_id is None or item.group_snapshot_id == trip.scope_group_id
                        or (participant is not None and participant.group_id == trip.scope_group_id))
            if not item.is_selected or not relevant:
                continue
            if item.participant_id not in eligible_ids:
                raise ValidationError("تغيّرت أهلية أحد المرشحين؛ راجع المسودة واحفظها قبل الاعتماد.")
            if (item.group_snapshot_id != participant.group_id
                    or item.student_name != users[participant.user_id].full_name
                    or item.group_name != groups[participant.group_id].name):
                raise ValidationError("تغيّرت بيئة أو بيانات أحد المرشحين بعد آخر حفظ؛ احفظ المسودة وراجع القائمة الحديثة قبل الاعتماد.")
        roster, new, changed = _nomination_rows(trip, eligible, users, groups, ranks)
        selected = [roster[item.pk] for item in scoped if roster[item.pk].is_selected]
        if not scoped or not selected:
            raise ValidationError("لا يمكن اعتماد رحلة بلا مرشحين مؤهلين داخل النطاق المحدد.")
        now = timezone.now()
        frozen_rows = []
        for participant in scoped:
            item = roster[participant.pk]
            item.frozen_member = True
            item.notified_at = now if item.is_selected else None
            frozen_rows.append({"participant_id": item.participant_id, "student_name": item.student_name,
                                "student_code": "P{}".format(item.participant_id), "group_id": item.group_snapshot_id,
                                "group_name": item.group_name, "points": item.points_snapshot, "rank": item.rank_snapshot,
                                "is_selected": item.is_selected, "reason": item.reason})
        content = trip_data(trip)
        snapshot = {"schema": 1, "trip": content,
                    "groups": [{"id": pk, "name": groups[pk].name} for pk in sorted({item.group_id for item in scoped})],
                    "rows": frozen_rows, "form": {**document_texts(), "verification_method": "account_declaration",
                                                   "decision_choices": list(GuardianTripConsent.Decision.choices),
                                                   "relation_choices": list(GuardianTripConsent.Relation.choices)}}
        _persist_roster(new, changed)
        trip.snapshot = snapshot
        trip.snapshot_sha256 = digest(snapshot)
        trip.status = EliteTrip.Status.APPROVED
        trip.approved_by = actor
        trip.approved_at = now
        trip.version += 1
        trip.save(update_fields=["snapshot", "snapshot_sha256", "status", "approved_by", "approved_at", "version", "updated_at"])
        _audit(trip, actor, "approved", version=trip.version, selected_count=len(selected), roster_count=len(scoped))
        return trip


def change_trip_status(actor, trip_id, status, version, confirm):
    _require_actor(actor)
    _confirm(confirm)
    if status not in (EliteTrip.Status.CLOSED, EliteTrip.Status.CANCELLED):
        raise ValidationError("الإجراء المطلوب غير معروف.")
    with transaction.atomic():
        actor, _ = _lock_users(actor)
        trip = get_object_or_404(EliteTrip.objects.select_for_update(), pk=trip_id)
        _version(trip, version)
        if trip.status == status:
            return trip
        if status == EliteTrip.Status.CLOSED and trip.status != EliteTrip.Status.APPROVED:
            raise ValidationError("يمكن إغلاق الرحلات المعتمدة المفتوحة فقط.")
        if trip.status == EliteTrip.Status.CANCELLED:
            raise ValidationError("الرحلة ملغاة بالفعل؛ لا يمكن إعادة فتحها.")
        trip.status = status
        trip.version += 1
        trip.save(update_fields=["status", "version", "updated_at"])
        _audit(trip, actor, status, version=trip.version)
        return trip


def staged_trip_rows(trip):
    """Read-only live draft roster; never initialize rows on GET."""
    if trip.approved_at:
        return [{**item, "nomination_id": trip.nominations.filter(participant_id=item["participant_id"]).values_list("pk", flat=True).first(),
                 "in_scope": True, "eligible": True} for item in trip.snapshot.get("rows", [])]
    participants = list(Participant.objects.filter(group__isnull=False, user__role=Role.PARTICIPANT,
                       user__is_active=True, user__is_staff=False, user__is_superuser=False).select_related("user", "group").only(
                           "pk", "group_id", "points", "user_id", "user__id", "user__full_name", "group__id", "group__name",
                       ).order_by("group__name", "group_id", "-points", "pk"))
    nominations = {item.participant_id: item for item in trip.nominations.all()}
    rows = []
    ranks = {}
    for participant in participants:
        ranks[participant.group_id] = ranks.get(participant.group_id, 0) + 1
        item = nominations.pop(participant.pk, None)
        rows.append({"participant_id": participant.pk, "nomination_id": item.pk if item else None,
                     "student_name": participant.user.full_name, "student_code": "P{}".format(participant.pk),
                     "group_id": participant.group_id, "group_name": participant.group.name, "points": participant.points,
                     "rank": ranks[participant.group_id], "is_selected": item.is_selected if item else False,
                     "reason": item.reason if item else "", "in_scope": trip.scope_group_id is None or participant.group_id == trip.scope_group_id,
                     "eligible": True})
    for item in nominations.values():
        rows.append({"participant_id": item.participant_id, "nomination_id": item.pk, "student_name": item.student_name,
                     "student_code": "P{}".format(item.participant_id), "group_id": item.group_snapshot_id,
                     "group_name": item.group_name, "points": item.points_snapshot, "rank": item.rank_snapshot,
                     "is_selected": item.is_selected, "reason": item.reason, "eligible": False,
                     "in_scope": trip.scope_group_id is None or item.group_snapshot_id == trip.scope_group_id})
    rows.sort(key=lambda item: (item["group_name"], item["group_id"] or 0, -item["points"], item["participant_id"]))
    return rows


def own_nominations(user, *, selected=False):
    if not can_use_student_trips(user):
        return EliteTripNomination.objects.none()
    query = EliteTripNomination.objects.filter(participant__user_id=user.pk, frozen_member=True,
                trip__status__in=PUBLISHED_STATUSES, trip__approved_at__isnull=False)
    return query.filter(is_selected=True) if selected else query


def student_safe_objects(nomination, consent=None):
    """Even server template contexts must not contain another student's roster.

    Shallow unsaved copies retain normal display methods/PKs but their JSON is
    narrowed to the owner's frozen row. Red cards receive no detail/form content.
    They are display-only and must never be passed to a mutation/PDF service.
    """
    safe_nomination = copy(nomination)
    safe_trip = copy(nomination.trip)
    source = nomination.trip.snapshot
    if nomination.is_selected:
        safe_trip.snapshot = {"schema": source.get("schema", 1), "trip": dict(source.get("trip", {})),
                              "form": dict(source.get("form", {})),
                              "groups": [dict(item) for item in source.get("groups", []) if item["id"] == nomination.group_snapshot_id],
                              "rows": [dict(item) for item in source.get("rows", []) if item["participant_id"] == nomination.participant_id]}
    else:
        safe_trip.snapshot = {"trip": {name: source.get("trip", {}).get(name, "") for name in ("title", "date")}}
        for name in ("location", "meeting_point", "instructions", "details"):
            setattr(safe_trip, name, "")
        for name in ("time", "fee", "departure_time", "return_time"):
            setattr(safe_trip, name, None)
    safe_nomination.trip = safe_trip
    safe_consent = copy(consent) if consent is not None else None
    if safe_consent is not None:
        safe_consent.nomination = safe_nomination
    return safe_nomination, safe_trip, safe_consent


def student_trip_cards(user):
    cards = []
    for nomination in own_nominations(user).select_related("trip").order_by("-trip__approved_at", "-trip_id"):
        consent = GuardianTripConsent.objects.filter(nomination=nomination).defer("signature").first() if nomination.is_selected else None
        can_reply = nomination.is_selected and nomination.trip.status == EliteTrip.Status.APPROVED and consent is None
        nomination, safe_trip, consent = student_safe_objects(nomination, consent)
        cards.append({"nomination": nomination, "trip": nomination.trip, "consent": consent, "can_reply": can_reply,
                      "nomination_id": nomination.pk, "trip_id": nomination.trip_id,
                      "title": nomination.trip.snapshot.get("trip", {}).get("title", ""),
                      "date": nomination.trip.snapshot.get("trip", {}).get("date", ""),
                      "status": nomination.trip.status, "status_label": nomination.trip.get_status_display(),
                      "is_selected": nomination.is_selected, "has_response": consent is not None,
                      "consent_id": consent.pk if consent else None,
                      "invitation_url": reverse("participants:trip_invitation", args=[nomination.pk]) if nomination.is_selected else "",
                      "consent_url": reverse("participants:trip_consent_detail", args=[consent.pk]) if consent else "",
                      "message": "تهانينا! تم ترشيحك لرحلة النخبة" if nomination.is_selected else "غير مرشح لهذه الرحلة",
                      "motivation": "" if nomination.is_selected else "الفرصة أمامك! اجتهد واحصد المزيد من الأميال لتكون من المرشحين للرحلات القادمة."})
    return cards


def pending_trip_invitation_count(user):
    return own_nominations(user, selected=True).filter(trip__status=EliteTrip.Status.APPROVED, consent__isnull=True).count()


def scoped_consents(user):
    if can_manage_elite_trips(user):
        return GuardianTripConsent.objects.filter(nomination__frozen_member=True, nomination__is_selected=True,
                                                  nomination__trip__approved_at__isnull=False)
    if can_use_student_trips(user):
        return GuardianTripConsent.objects.filter(nomination__in=own_nominations(user, selected=True))
    return GuardianTripConsent.objects.none()


def consent_payload(form):
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    return {name: form.cleaned_data[name] for name in CONSENT_FIELDS if name != "signature"} | {
        "signature_sha256": hashlib.sha256(form.signature_bytes).hexdigest(), "verification_method": "account_declaration"}


def _review_binding(user, nomination, form):
    return {"user": user.pk, "nomination": nomination.pk, "trip": nomination.trip.snapshot_sha256,
            "payload": digest(consent_payload(form))}


def make_review_token(user, nomination, form):
    _require_actor(user, student=True)
    current = get_object_or_404(own_nominations(user, selected=True).select_related("trip"), pk=nomination.pk)
    if current.trip.status != EliteTrip.Status.APPROVED or GuardianTripConsent.objects.filter(nomination=current).exists():
        raise ValidationError("لم تعد الدعوة مفتوحة لاستلام قرار جديد.")
    return signing.dumps(_review_binding(user, current, form), salt=REVIEW_SALT, compress=True)


def submit_consent(user, nomination_id, data):
    _require_actor(user, student=True)
    _confirm(_single(data, "confirm"))
    token = _single(data, "review_token")
    form = GuardianTripConsentForm(data)
    payload = consent_payload(form)
    try:
        binding = signing.loads(token, salt=REVIEW_SALT, max_age=REVIEW_MAX_AGE)
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError("انتهت صلاحية المراجعة أو تغيّرت بياناتها؛ راجع الاستمارة مجددًا قبل التأكيد.") from None
    with transaction.atomic():
        actor, _ = _lock_users(user, student=True)
        initial = get_object_or_404(own_nominations(actor, selected=True).only("pk", "trip_id", "participant_id"), pk=nomination_id)
        trip = get_object_or_404(EliteTrip.objects.select_for_update(), pk=initial.trip_id)
        participant = get_object_or_404(Participant.objects.select_for_update(), pk=initial.participant_id, user_id=actor.pk)
        nomination = get_object_or_404(EliteTripNomination.objects.select_for_update(), pk=initial.pk,
                                      participant=participant, trip=trip, frozen_member=True, is_selected=True)
        nomination.trip = trip
        # A token may expire while this request waits for another transaction.
        # Re-check its server timestamp only after all relevant locks are held.
        try:
            binding = signing.loads(token, salt=REVIEW_SALT, max_age=REVIEW_MAX_AGE)
        except (signing.BadSignature, ValueError, TypeError):
            raise ValidationError("انتهت صلاحية المراجعة؛ راجع الاستمارة مجددًا قبل التأكيد.") from None
        expected = _review_binding(actor, nomination, form)
        if not isinstance(binding, dict) or not constant_time_compare(digest(binding), digest(expected)):
            raise ValidationError("تغيّرت بيانات القرار بعد المراجعة؛ أعد مراجعتها قبل الحفظ.")
        content_hash = digest({"binding": expected, "payload": payload})
        if trip.status != EliteTrip.Status.APPROVED or trip.approved_at is None:
            raise ValidationError("أُغلقت الرحلة أو أُلغيت؛ لا يمكن إرسال رد جديد.")
        existing = GuardianTripConsent.objects.filter(nomination=nomination).defer("signature").first()
        if existing:
            if constant_time_compare(existing.content_sha256, content_hash):
                return existing, False
            raise ValidationError("يوجد رد محفوظ لهذه الدعوة؛ لا يمكن استبداله بقرار أو بيانات مختلفة.")
        consent = GuardianTripConsent.objects.create(
            nomination=nomination, decision=payload["decision"], guardian_name=payload["guardian_name"],
            guardian_relation=payload["guardian_relation"], notes=payload["notes"], declaration_accepted=True,
            signature=form.signature_bytes, signature_sha256=payload["signature_sha256"],
            trip_sha256=trip.snapshot_sha256, content_sha256=content_hash,
            verification_method="account_declaration", submitted_by=actor,
        )
        _audit(trip, actor, "consent_submitted", nomination_id=nomination.pk, consent_id=consent.pk, version=trip.version)
        return consent, True


def consent_document_context(consent):
    nomination = consent.nomination
    trip = nomination.trip
    raw_signature = bytes(consent.signature)
    image_url = signature_data_url(raw_signature)
    normalized = normalize_signature(image_url)
    if (normalized != raw_signature or hashlib.sha256(normalized).hexdigest() != consent.signature_sha256
            or trip.snapshot_sha256 != consent.trip_sha256 or digest(trip.snapshot) != consent.trip_sha256):
        raise ValidationError("تعذر التحقق من سلامة وثيقة الاستمارة؛ لم يتغير السجل المحفوظ.")
    return {"consent": consent, "nomination": nomination, "trip": trip, "trip_data": trip_data(trip),
            "student_name": nomination.student_name, "group_name": nomination.group_name,
            "signature_data_url": image_url,
            "submitted_at": timezone.localtime(consent.submitted_at, RIYADH),
            "decision_label": consent.get_decision_display(), "guardian_relation_label": consent.get_guardian_relation_display(),
            **document_texts(trip)}
