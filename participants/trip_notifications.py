"""Private in-app status events; no backfill, GET writes or document payloads."""
from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from django.db.transaction import TransactionManagementError
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from . import elite_trips as services
from .models import EliteTrip, EliteTripNotification


def emit_trip_notifications(trip):
    """Called only after saving the version, under the caller's trip lock/atomic.

    The frozen nomination roster, not live membership or ranking, owns events.
    A save error propagates to roll back status, nominations and audit together.
    """
    alias = trip._state.db or "default"
    if not connections[alias].in_atomic_block:
        raise TransactionManagementError("Trip notifications require the status transaction.")
    if trip.approved_at is None or trip.status not in services.PUBLISHED_STATUSES:
        return
    title = trip.snapshot.get("trip", {}).get("title", "")
    messages = {
        EliteTripNotification.Kind.NOMINATED: f"تهانينا! تم ترشيحك لرحلة «{title}». راجع رحلاتي للاطلاع على الدعوة.",
        EliteTripNotification.Kind.NOT_NOMINATED: f"غير مرشح لرحلة «{title}». واصل اجتهادك واحصد المزيد من الأميال للرحلات القادمة.",
        EliteTripNotification.Kind.CLOSED: f"أُغلقت رحلة «{title}». لا تُقبل ردود جديدة، ويبقى السجل السابق محفوظًا.",
        EliteTripNotification.Kind.CANCELLED: f"أُلغيت رحلة «{title}». لا تُقبل ردود جديدة، ويبقى السجل السابق محفوظًا.",
    }
    for nomination_id, selected in trip.nominations.filter(frozen_member=True).order_by("pk").values_list("pk", "is_selected"):
        kind = (EliteTripNotification.Kind.NOMINATED if selected else EliteTripNotification.Kind.NOT_NOMINATED
                ) if trip.status == EliteTrip.Status.APPROVED else trip.status
        EliteTripNotification.objects.using(alias).get_or_create(
            nomination_id=nomination_id, trip_version=trip.version,
            defaults={"kind": kind, "message": messages[kind]},
        )


def own_trip_notifications(user):
    """No related model is loaded; ownership uses the existing fresh-user scope."""
    return EliteTripNotification.objects.filter(nomination__in=services.own_nominations(user))


def trip_attention_count(user):
    """Distinct trips needing either an invitation response or an event read."""
    unread = EliteTripNotification.objects.filter(nomination_id=OuterRef("pk"), read_at__isnull=True)
    return services.own_nominations(user).annotate(_has_unread=Exists(unread)).filter(
        Q(is_selected=True, trip__status=EliteTrip.Status.APPROVED, consent__isnull=True) | Q(_has_unread=True),
    ).order_by().values("trip_id").distinct().count()


def notification_context(user, limit=20):
    notifications = own_trip_notifications(user)
    trips_url = reverse("participants:student_trips")
    rows = []
    ordered = notifications.annotate(_read_order=Case(
        When(read_at__isnull=True, then=Value(0)), default=Value(1), output_field=IntegerField(),
    )).order_by("_read_order", "-created_at", "-pk")
    for row in ordered.values(
        "id", "kind", "message", "created_at", "read_at", "nomination__trip_id",
    )[:limit]:
        rows.append({"id": row["id"], "kind": row["kind"], "message": row["message"],
                     "created_at": row["created_at"].isoformat(),
                     "read_at": row["read_at"].isoformat() if row["read_at"] else None,
                     "url": f'{trips_url}#trip-{row["nomination__trip_id"]}',
                     "read_url": reverse("participants:trip_notification_read", args=[row["id"]])})
    return {"trip_notifications": rows,
            "trip_notifications_unread": notifications.filter(read_at__isnull=True).count(),
            "trip_notifications_attention": trip_attention_count(user),
            "trip_notifications_url": reverse("participants:trip_notifications"),
            "trip_notifications_enabled": True}


def mark_notification_read(user, pk):
    """Serialize with account lifecycle; preserve the first read timestamp only."""
    services._require_actor(user, student=True)
    try:
        pk = services._id(pk)
    except ValidationError:
        raise Http404 from None
    with transaction.atomic():
        actor, _ = services._lock_users(user, student=True)
        notification = get_object_or_404(own_trip_notifications(actor).only("pk"), pk=pk)
        return bool(EliteTripNotification.objects.filter(pk=notification.pk, read_at__isnull=True).update(read_at=timezone.now()))
