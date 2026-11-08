from django.shortcuts import redirect
from django.urls import reverse

from .models import Role


PUBLIC_PWA_PATHS = frozenset({
    "/manifest.webmanifest", "/service-worker.js", "/offline/",
})


class ForcePasswordSetupMiddleware:
    """
    Redirects any authenticated participant who still needs to set a real
    password (must_set_password=True) to the mandatory set-password page,
    on every request, until they complete it — no page is reachable in
    between except the set-password page itself (its GET and POST target
    are the same URL) and logout (so a stuck participant can always sign
    out). GET/HEAD for the three exact public PWA resources are also
    permitted; they contain no account data and never evaluate request.user.

    Must be listed AFTER django.contrib.auth.middleware.AuthenticationMiddleware
    in settings.MIDDLEWARE, since it relies on request.user.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("GET", "HEAD") and request.path in PUBLIC_PWA_PATHS:
            return self.get_response(request)

        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "role", None) == Role.PARTICIPANT
            and getattr(user, "must_set_password", False)
        ):
            set_password_url = reverse("accounts:set_password")
            logout_url = reverse("accounts:logout")
            if request.path not in (set_password_url, logout_url):
                return redirect("accounts:set_password")

        return self.get_response(request)
