"""Finite loopback WSGI fixture for test_elite_trip_ui.js; real Django + CSRF.

All database work stays in one HTTPServer thread with SQLite :memory:. The only
fixture-specific endpoints authenticate synthetic actors or inspect actual ORM
state; trip/create/review/approve/consent requests use unmodified application
WSGI middleware and views. No deployment settings, network DB or public media.
"""
import argparse
import json
import logging
import mimetypes
import os
from pathlib import Path
from http.cookies import Morsel
import sys
import time
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def transport_cookie(source):
    """Serialize a Django test-client Morsel without absent=None attributes.

    force_login fills the reserved attributes from settings, including None for
    Domain/Expires/Max-Age. Those mean absent, not the literal string "None";
    copying all items into a wire header makes Chromium reject the session.
    Preserve the encoded value and every real attribute (including flags).
    """
    cookie = Morsel()
    cookie.set(source.key, source.value, source.coded_value)
    for attribute, value in source.items():
        if value is not None and value != "":
            cookie[attribute] = value
    return cookie.OutputString()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True)
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args()
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
    os.environ["SECRET_KEY"] = "synthetic-elite-consent-browser-only"
    os.environ["USE_POSTGRES"] = "False"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["DEBUG"] = "False"
    os.environ["CSRF_TRUSTED_ORIGINS"] = ""
    import django
    django.setup()
    from django.conf import settings
    from django.contrib.staticfiles import finders
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.db import connection
    from django.test import Client
    from django.urls import reverse
    from accounts.models import Role, User
    from participants.models import (
        EliteTrip, EliteTripNomination, GuardianTripConsent, EliteTripAudit,
        Group, Participant, PointsLedgerEntry, EliteTripAttendance,
    )
    csrf_logger = logging.getLogger("django.security.csrf")
    csrf_logger.addHandler(logging.StreamHandler(sys.stderr))
    csrf_logger.setLevel(logging.WARNING)
    assert settings.SETTINGS_MODULE == "config.test_settings"
    assert connection.settings_dict["ENGINE"] == "django.db.backends.sqlite3"
    assert connection.settings_dict["NAME"] == ":memory:"
    call_command("migrate", interactive=False, verbosity=0)
    users = {}
    def user(key, role):
        result = User.objects.create_user(username=f"synthetic-elite-ui-{key}", role=role,
            full_name=f"اسم تركيبي طويل لاختبار الواجهة {key}", password="SyntheticOnly123!",
            must_set_password=False)
        users[key] = result
        return result
    general = user("general", Role.GENERAL_SUPERVISOR)
    user("system", Role.SUPERADMIN)
    user("limited", Role.LIMITED_SUPERVISOR)
    group_a = Group.objects.create(name="بيئة تركيبية أ")
    group_b = Group.objects.create(name="بيئة تركيبية ب")
    participants = []
    for group, prefix in ((group_a, "a"), (group_b, "b")):
        for number in range(16):
            student = user(f"{prefix}-{number}", Role.PARTICIPANT)
            participants.append(Participant.objects.create(user=student, group=group, academic_stage="grade_7",
                points=100 if number < 14 else 1, miles=17, purchase_points=8))
    baseline = list(Participant.objects.order_by("pk").values("id", "points", "miles", "purchase_points"))
    routes = {name: reverse("participants:" + name) for name in (
        "elite_trip_list", "student_trips", "elite_trip_attendance", "dashboard")}
    posts = []
    external_network_attempts = []
    def guard_network(event, arguments):
        # Real PDF renderer, no fake success or resource content. Reject and
        # record any attempted network fetch; Unix/local transports stay local.
        if event == "socket.connect":
            address = arguments[1]
            if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
                external_network_attempts.append(str(address))
                raise RuntimeError("External network is forbidden in QA fixture")
        elif event == "socket.getaddrinfo":
            hostname = arguments[0]
            if hostname not in (None, "localhost", "127.0.0.1", "::1"):
                external_network_attempts.append(str(hostname))
                raise RuntimeError("External DNS is forbidden in QA fixture")
    sys.addaudithook(guard_network)
    def state():
        assert connection.settings_dict["NAME"] == ":memory:"
        return {"database": ":memory:", "settings": settings.SETTINGS_MODULE,
            "middleware": settings.MIDDLEWARE,
            "external_network_attempts": external_network_attempts,
            "routes": routes, "groups": [group_a.pk, group_b.pk],
            "participants": [{"id": p.pk, "actor": f"{prefix}-{i}", "group": p.group_id}
                             for prefix, rows in (("a", participants[:16]), ("b", participants[16:]))
                             for i, p in enumerate(rows)],
            "trips": list(EliteTrip.objects.order_by("pk").values()),
            "nominations": list(EliteTripNomination.objects.order_by("pk").values()),
            "consents": list(GuardianTripConsent.objects.defer("signature").order_by("pk").values(
                "id", "nomination_id", "decision", "guardian_name", "guardian_relation", "notes",
                "reference", "verification_method", "submitted_at", "signature_sha256")),
            "audits": list(EliteTripAudit.objects.order_by("pk").values("event", "trip_id")),
            "balances": list(Participant.objects.order_by("pk").values("id", "points", "miles", "purchase_points")),
            "baseline": baseline, "ledger": PointsLedgerEntry.objects.count(),
            "attendance": EliteTripAttendance.objects.count(), "posts": posts}
    app = get_wsgi_application()
    csrf_logger.disabled = False
    csrf_logger.setLevel(logging.WARNING)
    from django.middleware.csrf import CsrfViewMiddleware
    original_reject = CsrfViewMiddleware._reject
    def observed_reject(middleware, request, reason):
        print("QA CSRF rejection: " + str(reason), file=sys.stderr, flush=True)
        return original_reject(middleware, request, reason)
    CsrfViewMiddleware._reject = observed_reject
    def respond(start_response, code, body, headers=()):
        status = {200: "OK", 400: "Bad Request", 404: "Not Found", 413: "Payload Too Large"}[code]
        start_response(f"{code} {status}", [("Content-Length", str(len(body))),
            ("Cache-Control", "private, no-store"), ("CDN-Cache-Control", "no-store"), *headers])
        return [body]
    def wrapper(environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        if path.startswith("/__fixture__/"):
            query = parse_qs(environ.get("QUERY_STRING", ""))
            if query.get("token") != [args.token]:
                return respond(start_response, 404, b"Not found")
            if path == "/__fixture__/state" and method == "GET":
                return respond(start_response, 200, json.dumps(state(), default=str).encode(),
                               [("Content-Type", "application/json")])
            if path == "/__fixture__/login" and method == "POST":
                actor = query.get("actor", [""])[0]
                if actor not in users:
                    return respond(start_response, 404, b"Not found")
                client = Client(enforce_csrf_checks=True)
                client.force_login(users[actor])
                cookie = client.cookies[settings.SESSION_COOKIE_NAME]
                cookie["httponly"] = True
                cookie["samesite"] = "Lax"
                return respond(start_response, 200, b'{"synthetic":true}',
                    [("Content-Type", "application/json"), ("Set-Cookie", transport_cookie(cookie))])
            return respond(start_response, 404, b"Not found")
        if path.startswith("/static/") and method == "GET":
            relative = path[len("/static/"):]
            if ".." in Path(relative).parts:
                return respond(start_response, 404, b"Not found")
            found = finders.find(relative)
            if not found:
                return respond(start_response, 404, b"Not found")
            return respond(start_response, 200, Path(found).read_bytes(),
                           [("Content-Type", mimetypes.guess_type(found)[0] or "application/octet-stream")])
        # Expose only the trip surface and read-only dashboard; no unrelated
        # account/store/points/reset/import/attendance mutations are reachable.
        allowed = path.startswith(("/participants/elite-trips/", "/participants/trip-invitations/",
                                  "/participants/trip-consents/", "/participants/my-trips/"))
        allowed = allowed or method == "GET" and path in (routes["dashboard"], routes["elite_trip_attendance"])
        if not allowed or method not in ("GET", "HEAD", "POST"):
            return respond(start_response, 404, b"Not found")
        if int(environ.get("CONTENT_LENGTH") or 0) > 400000:
            return respond(start_response, 413, b"Fixture request limit")
        def captured(status, headers, exc_info=None):
            if method == "POST":
                posts.append({"path": path, "status": int(status.split()[0])})
            return start_response(status, headers, exc_info)
        return app(environ, captured)
    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *unused):
            pass
    try:
        with make_server("127.0.0.1", 0, wrapper, handler_class=QuietHandler) as server:
            server.timeout = 0.25
            print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}",
                              "database": ":memory:", "settings": settings.SETTINGS_MODULE}), flush=True)
            deadline = time.monotonic() + min(max(args.max_seconds, 1), 900)
            while time.monotonic() < deadline:
                server.handle_request()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
