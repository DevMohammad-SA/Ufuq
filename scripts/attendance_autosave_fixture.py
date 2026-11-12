"""Finite loopback fixture for test_individual_attendance_autosave_ui.js only.

All ORM operations run on the HTTPServer's single main thread, in config.test_settings
SQLite :memory:. Actual Django middleware, CSRF, templates, views and transactions are
used. No production connection, runserver, persistent database, or real accounts.
The parent Node harness terminates this subprocess in finally; a deadline also bounds it.
"""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Set (not setdefault): inherited deployment environment must never select a real DB.
os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
os.environ["SECRET_KEY"] = "synthetic-attendance-browser-fixture-not-for-deployment"
os.environ["USE_POSTGRES"] = "False"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["DEBUG"] = "False"
os.environ["CSRF_TRUSTED_ORIGINS"] = ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True)
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args()
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection
    from django.contrib.staticfiles import finders
    from django.test import Client
    from django.urls import reverse
    from accounts.models import User, Role
    from participants.models import (
        Group, Participant, MeetingAttendance, CircleAttendance,
        WeeklyActivityAttendance, MeetingNote, PointsLedgerEntry,
    )
    assert settings.SETTINGS_MODULE == "config.test_settings"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings.DATABASES["default"]["NAME"] == ":memory:"
    assert connection.vendor == "sqlite"
    call_command("migrate", interactive=False, verbosity=0)
    supervisor = User.objects.create_user(
        username="synthetic-autosave-supervisor", password="fixture-only",
        full_name="مشرف مصطنع لاختبار التحضير", role=Role.GROUP_SUPERVISOR,
    )
    group = Group.objects.create(name="بيئة مصطنعة لاختبار الحفظ")
    group.supervisor.add(supervisor)
    other_group = Group.objects.create(name="بيئة مصطنعة خارج النطاق")
    students = []
    for number in range(1, 5):
        user = User.objects.create_user(
            username=f"synthetic-autosave-student-{number}", password="fixture-only",
            national_id=f"900000000{number}", role=Role.PARTICIPANT,
            full_name=f"الطالب المصطنع {number} عبدالله عبدالرحمن محمد صاحب الاسم الكامل الطويل",
        )
        students.append(Participant.objects.create(
            user=user, group=group if number < 4 else other_group,
            academic_stage="grade_7", phone="0500000000",
        ))
    client = Client(enforce_csrf_checks=True)
    client.force_login(supervisor, backend="django.contrib.auth.backends.ModelBackend")
    routes = {kind: reverse("participants:" + kind) for kind in (
        "supervisor_dashboard", "quran_circle_attendance", "weekly_activity_attendance",
        "elite_trip_attendance",
    )}
    posts = []

    def state():
        assert connection.settings_dict["NAME"] == ":memory:"
        return {
            "database": ":memory:", "settings": settings.SETTINGS_MODULE,
            "group": group.pk, "ids": [s.pk for s in students[:3]], "outside_id": students[3].pk,
            "routes": routes,
            "balances": list(Participant.objects.filter(pk__in=[s.pk for s in students]).order_by("pk").values(
                "id", "points", "miles", "purchase_points")),
            "meeting": list(MeetingAttendance.objects.order_by("pk").values(
                "participant_id", "week_start_date", "attended", "is_early", "recorded_by_id")),
            "circle": list(CircleAttendance.objects.order_by("pk").values(
                "participant_id", "date", "attended", "achieved", "recorded_by_id")),
            "activity": list(WeeklyActivityAttendance.objects.order_by("pk").values(
                "participant_id", "date", "attended", "recorded_by_id")),
            "notes": list(MeetingNote.objects.order_by("pk").values("participant_id", "meeting_date", "text")),
            "ledger": list(PointsLedgerEntry.objects.order_by("pk").values(
                "participant_id", "points_delta", "source", "event_date")),
            "posts": posts,
        }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send(self, status, body, headers=()):
            self.send_response(status)
            for key, value in headers:
                if key.lower() not in ("content-length", "connection", "transfer-encoding"):
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def handle_request(self):
            parsed = urlsplit(self.path)
            if parsed.path.startswith("/__fixture__/"):
                if parsed.path != "/__fixture__/state" or parse_qs(parsed.query).get("token") != [args.token]:
                    self.send(404, b"Not found")
                    return
                self.send(200, json.dumps(state(), default=str).encode(), (
                    ("Content-Type", "application/json"), ("Cache-Control", "no-store"),
                ))
                return
            if parsed.path.startswith("/static/") and self.command in ("GET", "HEAD"):
                relative = parsed.path[len("/static/"):]
                if ".." in Path(relative).parts:
                    self.send(404, b"Not found")
                    return
                found = finders.find(relative)
                if not found:
                    self.send(404, b"Not found")
                    return
                self.send(200, Path(found).read_bytes(), (("Content-Type", mimetypes.guess_type(found)[0] or "application/octet-stream"),))
                return
            # Do not expose mutations beyond the four explicitly tested roster views.
            if parsed.path not in routes.values() or self.command not in ("GET", "HEAD", "POST"):
                self.send(404, b"Not found")
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size > 100000:
                self.send(413, b"Fixture body limit")
                return
            raw = self.rfile.read(size)
            extra = {"HTTP_" + key.upper().replace("-", "_"): value
                     for key, value in self.headers.items()
                     if key.lower() not in ("content-type", "content-length")}
            extra["HTTP_HOST"] = self.headers.get("Host")
            # The adapter authenticates only this synthetic user. A browser CSRF
            # cookie must not replace Client.force_login's private session cookie.
            # Keep the browser's actual CSRF cookie/token and normal middleware;
            # inject only the fixture session, never a CSRF bypass.
            session = client.cookies[settings.SESSION_COOKIE_NAME].value
            cookies = self.headers.get("Cookie", "")
            extra["HTTP_COOKIE"] = f"{cookies}; {settings.SESSION_COOKIE_NAME}={session}"
            response = client.generic(
                self.command, self.path, data=raw,
                content_type=self.headers.get("Content-Type", "application/octet-stream"), **extra,
            )
            if self.command == "POST":
                posts.append({"path": parsed.path, "status": response.status_code,
                              "data": dict(response.wsgi_request.POST.lists()),
                              "accept": self.headers.get("Accept", ""),
                              "content_type": self.headers.get("Content-Type", "")})
            headers = list(response.items())
            headers.extend(("Set-Cookie", cookie.output(header="").strip()) for cookie in response.cookies.values())
            self.send(response.status_code, response.content, headers)

        def do_GET(self):
            self.handle_request()

        def do_HEAD(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

    # HTTPServer (not ThreadingHTTPServer): same connection/thread as migrate + fixture.
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 0.25
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}",
                          "token": args.token, "database": ":memory:", "settings": settings.SETTINGS_MODULE}), flush=True)
        deadline = time.monotonic() + min(max(args.max_seconds, 1), 900)
        while time.monotonic() < deadline:
            server.handle_request()
    connection.close()


if __name__ == "__main__":
    main()
