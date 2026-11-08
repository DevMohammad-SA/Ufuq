"""Public, visitor-independent PWA resources; never render account data."""

from pathlib import Path

from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse, JsonResponse
from django.templatetags.static import static
from django.views.decorators.http import require_safe


def _static_response(asset_path, content_type):
    # Resolve the fixed asset at request time, using the existing staticfiles
    # configuration. No template context, session, host-derived URL or DB access.
    asset = finders.find(asset_path)
    if not asset:
        raise Http404("الملف غير موجود")
    response = HttpResponse(Path(asset).read_bytes(), content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_safe
def pwa_manifest(request):
    response = JsonResponse(
        {
            "name": "رحّال",
            "short_name": "رحّال",
            "description": "رحّال لمتابعة البرنامج والمهام والحضور.",
            "id": "/",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#F7F3EA",
            "theme_color": "#F7F3EA",
            "lang": "ar",
            "dir": "rtl",
            "prefer_related_applications": False,
            "icons": [
                {
                    "src": static("pwa/icon-192.png"),
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": static("pwa/icon-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": static("pwa/icon-maskable-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        },
        content_type="application/manifest+json",
        json_dumps_params={"ensure_ascii": False},
    )
    response["Cache-Control"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_safe
def pwa_service_worker(request):
    response = _static_response("js/pwa-service-worker.js", "application/javascript")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_safe
def pwa_offline(request):
    response = _static_response("pwa/offline.html", "text/html; charset=utf-8")
    response["Cache-Control"] = "public, max-age=0, no-cache"
    # Installation and fallback both require this marker so a login redirect or
    # unrelated response can never become the public offline cache entry.
    response["X-Rahhal-Offline"] = "public-v1"
    return response
