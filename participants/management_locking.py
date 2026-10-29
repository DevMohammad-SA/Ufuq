"""Serialize conflicting login identifiers in front-end management writes."""

import hashlib

from django.db import connection

from accounts.models import User


def lock_login_identifiers(data):
    """Called inside atomic(), before form validation and before inserting users.

    The same identifier uses the same signed-bigint transaction lock regardless
    of whether it is entered as username or national_id. Sorted acquisition
    prevents lock-order cycles. SQLite serializes its write transactions itself;
    do not emulate these PostgreSQL locks with process-local thread locks.
    """
    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise RuntimeError("Identifier locking requires an active transaction")
    values = set()
    for name in ("username", "national_id"):
        value = User.normalize_username(data.get(name, "").strip())
        if value:
            values.add(value)
    keys = sorted({
        int.from_bytes(hashlib.sha256(("ufuq:login:" + value).encode()).digest()[:8], "big", signed=True)
        for value in values
    })
    with connection.cursor() as cursor:
        for key in keys:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
