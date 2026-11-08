"""PWA privacy and integration contracts using synthetic SQLite test data only."""

from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.apps import apps
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse
from django.utils.functional import SimpleLazyObject

from accounts.middleware import ForcePasswordSetupMiddleware, PUBLIC_PWA_PATHS
from accounts.models import Role, User
from participants.models import Group, Participant


PUBLIC_ROUTES = {
    "pwa_manifest": "/manifest.webmanifest",
    "pwa_service_worker": "/service-worker.js",
    "pwa_offline": "/offline/",
}
OFFLINE_TEXT = (
    "أنت غير متصل بالإنترنت. عرض بياناتك وحفظ التغييرات يحتاجان إلى اتصال. "
    "لا تحفظ التغييرات دون اتصال."
)


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.metas = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        target = {"link": self.links, "meta": self.metas, "script": self.scripts}.get(tag)
        if target is not None:
            target.append(dict(attrs))


class PublicPWAResourceTests(SimpleTestCase):
    def assert_public(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertFalse(response.cookies)
        self.assertNotIn("cookie", response.get("Vary", "").lower())
        self.assertNotIn("Set-Cookie", response)

    def test_exact_named_root_routes_and_head(self):
        self.assertEqual(PUBLIC_PWA_PATHS, frozenset(PUBLIC_ROUTES.values()))
        for name, url in PUBLIC_ROUTES.items():
            with self.subTest(name=name):
                self.assertEqual(reverse(name), url)
                self.assertEqual(resolve(url).url_name, name)
                get_response = self.client.get(url)
                self.assert_public(get_response)
                head_response = self.client.head(url)
                self.assert_public(head_response)
                self.assertEqual(head_response.content, b"")
                for header in ("Content-Type", "Cache-Control", "X-Content-Type-Options"):
                    self.assertEqual(head_response[header], get_response[header])

    def test_manifest_schema_and_local_icons(self):
        response = self.client.get(reverse("pwa_manifest"))
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        self.assertEqual(response["Cache-Control"], "no-cache")
        manifest = response.json()
        self.assertEqual(set(manifest), {
            "name", "short_name", "description", "id", "start_url", "scope",
            "display", "background_color", "theme_color", "lang", "dir",
            "prefer_related_applications", "icons",
        })
        self.assertEqual(manifest["name"], "رحّال")
        self.assertEqual(manifest["short_name"], "رحّال")
        self.assertTrue(manifest["description"])
        self.assertLess(len(manifest["description"]), 100)
        for field in ("id", "start_url", "scope"):
            self.assertEqual(manifest[field], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["lang"], "ar")
        self.assertEqual(manifest["dir"], "rtl")
        self.assertIs(manifest["prefer_related_applications"], False)
        self.assertEqual(manifest["background_color"], "#F7F3EA")
        self.assertEqual(manifest["theme_color"], "#F7F3EA")
        self.assertEqual(manifest["icons"], [
            {"src": static("pwa/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("pwa/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("pwa/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ])
        for icon in manifest["icons"]:
            self.assertTrue(icon["src"].startswith("/static/pwa/"))
            self.assertNotIn("?", icon["src"])
            self.assertNotIn("//", icon["src"])

    def test_all_four_icons_exist_at_required_sizes_and_are_opaque(self):
        for name, size in (
            ("icon-192.png", 192), ("icon-512.png", 512),
            ("icon-maskable-512.png", 512), ("apple-touch-icon.png", 180),
        ):
            with self.subTest(name=name):
                asset = finders.find(f"pwa/{name}")
                self.assertIsNotNone(asset)
                with Image.open(asset) as image:
                    self.assertEqual(image.size, (size, size))
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.convert("RGBA").getchannel("A").getextrema(), (255, 255))

    def test_worker_mime_scope_no_cache_and_fixed_asset(self):
        response = self.client.get(reverse("pwa_service_worker"))
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(response.content, Path(finders.find("js/pwa-service-worker.js")).read_bytes())

    def test_offline_fixed_self_contained_and_no_forms_or_external_assets(self):
        response = self.client.get(reverse("pwa_offline"))
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(response["X-Rahhal-Offline"], "public-v1")
        self.assertEqual(response["Cache-Control"], "public, max-age=0, no-cache")
        self.assertEqual(response.content, Path(finders.find("pwa/offline.html")).read_bytes())
        self.assertContains(response, OFFLINE_TEXT)
        self.assertContains(response, '<html lang="ar" dir="rtl">')
        self.assertContains(response, 'onclick="location.reload()"')
        self.assertContains(response, '<a href="/">')
        self.assertContains(response, 'type="button"')
        self.assertContains(response, "system-ui")
        self.assertNotRegex(response.content.decode(), r"(?i)<form|<input|<iframe|<script|\bsrc=|https?://|@import|url\(|csrf|session|token|password")

    def test_views_ignore_query_strings_and_do_not_use_host_for_urls(self):
        for url in PUBLIC_ROUTES.values():
            with self.subTest(url=url):
                plain = self.client.get(url)
                queried = self.client.get(url, {"user": "private-name", "national_id": "1122334455", "next": "https://evil.invalid/"})
                self.assertEqual(queried.content, plain.content)
                self.assertNotContains(queried, "private-name")
                self.assertNotContains(queried, "1122334455")
        with self.settings(ALLOWED_HOSTS=["other.example"]):
            response = self.client.get(reverse("pwa_manifest"), HTTP_HOST="other.example")
            self.assertNotContains(response, "other.example")

    def test_anonymous_invalid_methods_are_405_when_test_csrf_is_disabled(self):
        for url in PUBLIC_ROUTES.values():
            for method in ("post", "put", "patch", "delete", "options", "trace"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response["Allow"], "GET, HEAD")
                    self.assertFalse(response.cookies)

    def test_real_csrf_is_unchanged_for_anonymous_posts(self):
        csrf_client = Client(enforce_csrf_checks=True)
        for url in PUBLIC_ROUTES.values():
            with self.subTest(url=url):
                self.assertEqual(csrf_client.post(url, {"action": "save"}).status_code, 403)

    def test_fixed_static_files_are_resolved_at_request_time(self):
        for url, asset in (("/service-worker.js", "js/pwa-service-worker.js"), ("/offline/", "pwa/offline.html")):
            with self.subTest(url=url), patch("participants.pwa_views.finders.find", return_value=None) as lookup:
                self.assertEqual(self.client.get(url).status_code, 404)
                lookup.assert_called_once_with(asset)

    def test_middleware_returns_before_lazy_user_or_reverse_is_touched(self):
        def fail_user_lookup():
            raise AssertionError("The public route must not evaluate request.user")

        for url in PUBLIC_ROUTES.values():
            for method in ("get", "head"):
                request = getattr(RequestFactory(), method)(url)
                request.user = SimpleLazyObject(fail_user_lookup)
                expected = HttpResponse("public")
                middleware = ForcePasswordSetupMiddleware(lambda request: expected)
                with self.subTest(url=url, method=method), patch("accounts.middleware.reverse", side_effect=AssertionError("No URL lookup needed")):
                    self.assertIs(middleware(request), expected)
                    self.assertIs(request.user._setupfunc, fail_user_lookup)


class AuthenticatedPWAResourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name="بيئة PWA مصطنعة")
        cls.forced = User.objects.create_user(
            national_id="8100112233", full_name="اسم خاص لا يظهر في PWA",
            role=Role.PARTICIPANT, password="pwa-test-password", must_set_password=True,
        )
        cls.student = Participant.objects.create(
            user=cls.forced, group=cls.group, academic_stage="grade_7",
            phone="0551239876", guardian_phone="0559998877",
            miles=430, points=43, purchase_points=39,
        )
        cls.ready = User.objects.create_user(
            national_id="8100112244", full_name="مشارك مصطنع جاهز",
            role=Role.PARTICIPANT, password="pwa-ready-password",
        )
        Participant.objects.create(user=cls.ready, group=cls.group, academic_stage="grade_7")
        cls.supervisors = [
            User.objects.create_user(username=f"pwa-{role}", role=role)
            for role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.SUPERADMIN)
        ]
        cls.group.supervisor.add(cls.supervisors[0])

    def snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_models(include_auto_created=True)
            if model._meta.app_label in ("accounts", "participants", "sessions")
        }

    def assert_public_without_identity(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies)
        self.assertNotIn("cookie", response.get("Vary", "").lower())
        self.assertNotIn("Set-Cookie", response)
        for secret in (self.forced.full_name, self.forced.national_id, self.student.phone, self.student.guardian_phone, "pwa-test-password"):
            self.assertNotIn(secret, response.content.decode())

    def test_all_roles_including_forced_password_public_get_head_have_zero_queries(self):
        anonymous = {url: self.client.get(url).content for url in PUBLIC_ROUTES.values()}
        for user in (self.forced, self.ready, *self.supervisors):
            self.client.force_login(user)
            before = self.snapshot()
            for url in PUBLIC_ROUTES.values():
                for method in ("get", "head"):
                    with self.subTest(role=user.role, forced=user.must_set_password, url=url, method=method):
                        with self.assertNumQueries(0):
                            response = getattr(self.client, method)(url)
                        self.assert_public_without_identity(response)
                        self.assertEqual(response.content, anonymous[url] if method == "get" else b"")
            self.assertEqual(self.snapshot(), before)

    def test_unsafe_public_requests_do_not_bypass_forced_password_gate(self):
        self.client.force_login(self.forced)
        before = self.snapshot()
        for url in PUBLIC_ROUTES.values():
            for method in ("post", "put", "patch", "delete", "options"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response["Location"], reverse("accounts:set_password"))
        self.assertEqual(self.snapshot(), before)

    def test_no_prefix_bypass_or_new_access_to_app_data(self):
        self.client.force_login(self.forced)
        before = self.snapshot()
        protected_paths = [
            "/offline/anything", "/offline", "/manifest.webmanifest/anything",
            "/service-worker.js/anything", "/offline//", "/",
            reverse("participants:dashboard"), reverse("participants:store"),
            reverse("participants:student_report"), reverse("participants:report_center"),
            reverse("participants:report_center_export"),
            reverse("participants:participants_data_pdf"),
        ]
        for url in protected_paths:
            for method in ("get", "head", "post"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response["Location"], reverse("accounts:set_password"))
        self.assertEqual(self.snapshot(), before)

    def assert_metadata(self, html):
        parser = MetadataParser()
        parser.feed(html)
        self.assertEqual([link["href"] for link in parser.links if link.get("rel") == "manifest"], [reverse("pwa_manifest")])
        self.assertEqual([meta["content"] for meta in parser.metas if meta.get("name") == "theme-color"], ["#F7F3EA"])
        self.assertEqual([meta["content"] for meta in parser.metas if meta.get("name") == "apple-mobile-web-app-title"], ["رحّال"])
        self.assertEqual([link["href"] for link in parser.links if link.get("rel") == "apple-touch-icon"], [static("pwa/apple-touch-icon.png")])
        self.assertEqual(len([script for script in parser.scripts if script.get("src", "").split("?")[0] == static("js/pwa-install.js")]), 1)

    def test_actual_home_and_both_login_pages_have_shared_metadata(self):
        for name in ("home", "accounts:login_participant", "accounts:login_supervisor"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assert_metadata(response.content.decode())

    def test_actual_participant_and_each_supervisor_dashboard_have_metadata(self):
        targets = [
            (self.ready, "participants:dashboard"),
            (self.supervisors[0], "participants:supervisor_dashboard"),
            (self.supervisors[1], "participants:general_supervisor_dashboard"),
            (self.supervisors[2], "participants:general_supervisor_dashboard"),
        ]
        for user, name in targets:
            self.client.force_login(user)
            with self.subTest(role=user.role):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assert_metadata(response.content.decode())

    def test_forced_password_page_remains_reachable_with_metadata(self):
        self.client.force_login(self.forced)
        response = self.client.get(reverse("accounts:set_password"))
        self.assertEqual(response.status_code, 200)
        self.assert_metadata(response.content.decode())

    def test_app_base_empty_content_still_includes_metadata_for_every_role(self):
        for user in (self.ready, *self.supervisors):
            request = RequestFactory().get("/participants/dashboard/")
            request.user = user
            with self.subTest(role=user.role), self.assertNumQueries(0):
                html = render_to_string("participants/app_base.html", {"navbar_items": []}, request=request)
            self.assert_metadata(html)
