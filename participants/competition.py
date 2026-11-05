"""Read-only, privacy-safe competition data for the requesting participant."""

from django.db.models import Count, Q

from .models import Participant


ELITE_LIMIT = 10
# Decorative positions, not ranks or records of any other participants.
GENERIC_RIDER_POSITIONS = (10, 27, 44, 61, 78, 94)


def build_elite_competition(participant):
    """Return the student's current rank and cutoff distance, or None.

    The ranking scope/order matches ParticipantDashboardView.get_elite_status:
    the same environment, active accounts, points descending then id ascending.
    Only current points are read; miles and reset snapshots are never consulted.

    Context contract (all JSON-safe, no participant records or identifiers):
      rank, total, limit: integers (total includes the requesting student).
      qualified: bool; difference: int or None.
      difference_kind: "gap", "margin", or None when total is at most ELITE_LIMIT.
      position_percent: float, rank / total * 100 from the RTL start.
      cutoff_percent: float, min(ELITE_LIMIT, total) / total * 100.
      marker_positions: at most six decorative integer percentages, not people.

    Three bounded queries at most: own current points, aggregate rank/count,
    and one cutoff value. No ranking list is loaded or placed in the context.
    A zero difference does not override the stable id tie-breaker and is not a
    promise that earning any particular number of points guarantees nomination.
    The existing elite_status context remains the view's responsibility.
    """
    if participant.group_id is None or participant.pk is None:
        return None

    competitors = Participant.objects.filter(
        group_id=participant.group_id, user__is_active=True,
    )
    current_points = competitors.filter(pk=participant.pk).values_list(
        "points", flat=True,
    ).first()
    if current_points is None:
        return None

    counts = competitors.aggregate(
        total=Count("pk"),
        ahead=Count(
            "pk",
            filter=Q(points__gt=current_points) | (
                Q(points=current_points) & Q(pk__lt=participant.pk)
            ),
        ),
    )
    total = counts["total"]
    rank = counts["ahead"] + 1
    qualified = rank <= ELITE_LIMIT
    difference = None
    difference_kind = None
    if total > ELITE_LIMIT:
        target_rank = ELITE_LIMIT + 1 if qualified else ELITE_LIMIT
        cutoff_points = list(
            competitors.order_by("-points", "id")
            .values_list("points", flat=True)[target_rank - 1:target_rank]
        )
        if cutoff_points:
            difference = (
                current_points - cutoff_points[0]
                if qualified else cutoff_points[0] - current_points
            )
            difference_kind = "margin" if qualified else "gap"

    return {
        "rank": rank,
        "total": total,
        "limit": ELITE_LIMIT,
        "qualified": qualified,
        "difference": difference,
        "difference_kind": difference_kind,
        "position_percent": round(rank / total * 100, 6),
        "cutoff_percent": round(min(ELITE_LIMIT, total) / total * 100, 6),
        "marker_positions": list(GENERIC_RIDER_POSITIONS) if total > 1 else [],
    }
