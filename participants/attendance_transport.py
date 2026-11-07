"""Opt-in partial roster saves; legacy full-roster POSTs remain compatible."""
from django.http import JsonResponse
from django.utils import timezone
from django.utils.cache import patch_cache_control


def wants_roster_json(request):
    return "application/json" in request.headers.get("Accept", "")


def selected_roster_ids(post, allowed_ids):
    """Never expand the server-derived active environment participant scope."""
    if "partial_roster" not in post:
        return set(allowed_ids)
    if post.getlist("partial_roster") != ["1"]:
        raise ValueError("طلب حفظ غير صالح. حدّث الصفحة وحاول مجددًا.")
    selected = set()
    for participant_id in allowed_ids:
        key = f"prepared_{participant_id}"
        if key not in post:
            continue
        if post.getlist(key) != ["1"]:
            raise ValueError("تحديد حالة الطالب غير صالح. حدّث الصفحة وحاول مجددًا.")
        selected.add(participant_id)
    return selected


def roster_error(request, message, status=400):
    response = JsonResponse({"success": False, "message": message}, status=status)
    patch_cache_control(response, private=True, no_store=True)
    return response


def roster_success(request, selected_date, group, saved_ids, saved_note_ids=()):
    response = JsonResponse({
        "success": True,
        "saved_ids": sorted(saved_ids),
        "saved_note_ids": sorted(saved_note_ids),
        "saved_at": timezone.localtime().strftime("%H:%M"),
        "date": selected_date.isoformat(),
        "group_id": group.pk,
    })
    patch_cache_control(response, private=True, no_store=True)
    return response
