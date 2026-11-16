"""Memory-only consent PDF for an already-authorized frozen document.

The caller authorizes BEFORE obtaining the signature/context. No ORM, network,
public storage, default URL fetcher, user paths, or eager WeasyPrint imports.
"""
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.template import Context, Engine

from .elite_trip_forms import normalize_signature, signature_data_url
from .report_center_exports import _pdf_assets


def restricted_pdf_fetcher(allowed, roots, signature_url):
    """Only exact normalized PNG in memory and the four bundled static assets."""
    png = normalize_signature(signature_url)
    if signature_data_url(png) != signature_url:
        raise ValueError("التوقيع ليس صورة PNG مطبعة.")

    def fetch(url, *args, **kwargs):
        from weasyprint.urls import URLFetcherResponse
        if url == signature_url:
            return URLFetcherResponse(url, png, headers={"Content-Type": "image/png"})
        parsed = urlsplit(url)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("لا يُسمح بموارد خارجية في ملف الاستمارة.")
        path = Path(unquote(parsed.path))
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("مورد PDF محلي غير متاح.") from None
        if path not in allowed or resolved not in allowed or not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError("مورد PDF خارج قائمة الأصول المحلية.")
        return URLFetcherResponse(resolved.as_uri(), resolved.read_bytes(), headers={
            "Content-Type": mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"})

    fetch._fail_on_errors = True
    return fetch


def render_consent_pdf(context):
    """Arabic frozen document; every renderer/library error propagates as failure."""
    if not isinstance(context, dict):
        raise ValueError("سياق الاستمارة غير صالح.")
    consent = context["consent"]
    normalized = normalize_signature(context["signature_data_url"])
    if hashlib.sha256(normalized).hexdigest() != consent.signature_sha256 or bytes(consent.signature) != normalized:
        raise ValueError("بصمة التوقيع غير مطابقة.")
    if context["trip"].snapshot_sha256 != consent.trip_sha256:
        raise ValueError("بصمة الرحلة غير مطابقة.")
    # Deliberately deferred: missing native libraries must affect this route only.
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration

    assets, allowed, roots = _pdf_assets()
    engine = Engine(dirs=[str(Path(__file__).parent / "templates")], autoescape=True)
    template = engine.get_template("participants/elite_trips/consent_pdf.html")
    # Strip SafeString provenance from all plain-text inputs. Templates still use
    # autoescape; guardian notes and names never become HTML/CSS/URL fragments.
    clean_context = dict(context)
    clean_context["assets"] = assets
    html = template.render(Context(clean_context, autoescape=True, use_l10n=False, use_tz=False))
    result = HTML(string=html, url_fetcher=restricted_pdf_fetcher(allowed, roots, context["signature_data_url"])).write_pdf(
        font_config=FontConfiguration())
    if not isinstance(result, bytes) or not result.startswith(b"%PDF-"):
        raise ValueError("تعذر إنشاء ملف PDF كامل.")
    return result
