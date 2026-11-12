"""Participant-roster ordering shared by the table and its PDF export."""

from django.db.models import Count, Q


ROSTER_SORT_KEYS = frozenset({
    "full_name", "group", "academic_stage", "miles", "points",
    "purchase_points", "circle_days_attended", "meetings_attended",
})

# ECMAScript WhiteSpace and LineTerminator characters used by String.trim().
# Python's default strip() differs for BOM and a few control characters.
JS_TRIM_CHARACTERS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def annotate_roster_attendance(queryset):
    """Distinct counts prevent multiplying two reverse attendance joins."""
    return queryset.annotate(
        circle_days_attended=Count(
            "circle_attendances",
            filter=Q(circle_attendances__attended=True),
            distinct=True,
        ),
        meetings_attended=Count(
            "meeting_attendances",
            filter=Q(meeting_attendances__attended=True),
            distinct=True,
        ),
    )


def _text_key(value):
    # Arabic lowercasing uses the default Unicode rules. JS compares UTF-16
    # code units, not a database's locale collation. Match that comparison.
    return str(value or "—").strip(JS_TRIM_CHARACTERS).lower().encode("utf-16-be")


def _roster_value(participant, key):
    if key == "full_name":
        return _text_key(participant.user.full_name)
    if key == "group":
        return _text_key(participant.group.name if participant.group else None)
    if key == "academic_stage":
        return _text_key(participant.get_academic_stage_display())
    # key comes exclusively from ROSTER_SORT_KEYS, never an ORM lookup.
    return getattr(participant, key)


def order_roster_for_export(rows, params):
    """Sort scoped, baseline-ordered rows without reading any other records.

    Callers supply the same base order as the table: full name then primary
    key. Python's stable sort preserves that order for ties in both directions,
    matching the browser's explicit baseline tie-breaker. Invalid or absent
    sort choices retain the legacy miles-descending export.
    """
    key = params.get("sort_by")
    direction = params.get("sort_dir")
    if key not in ROSTER_SORT_KEYS or direction not in ("asc", "desc"):
        key, direction = "miles", "desc"
    return sorted(
        rows,
        key=lambda participant: _roster_value(participant, key),
        reverse=direction == "desc",
    )
