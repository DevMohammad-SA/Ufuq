"""Role-isolated trip UI. No report grant, admin flag, or GET performs a write."""
import logging
import re
from collections import Counter

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.debug import sensitive_post_parameters

from .elite_trip_forms import GuardianTripConsentForm, EliteTripForm, CONSENT_FIELDS
from . import elite_trips as services
from . import trip_notifications as notifications
from .models import EliteTrip, EliteTripNomination, GuardianTripConsent, Group

# Include missing/malformed paths and APPEND_SLASH redirects within these new
# feature prefixes, not only URL-resolved successful callbacks.
_PRIVATE_PATH = re.compile(r"^/participants/(?:elite-trips|my-trips|trip-invitations|trip-consents)(?:/.*)?$")
_LOGGER = logging.getLogger(__name__)


def private_response(response):
    response["Cache-Control"] = "private, no-store, no-cache, max-age=0, must-revalidate"
    response["CDN-Cache-Control"] = "no-store"
    response["Cloudflare-CDN-Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    # Native form POSTs need same-origin Origin/Referer for Django CSRF.
    # Never send private document URLs to any external origin.
    response["Referrer-Policy"] = "same-origin"
    response["X-Content-Type-Options"] = "nosniff"
    patch_vary_headers(response, ("Cookie",))
    return response


class EliteTripPrivateResponseMiddleware:
    """Integration must place this outside CSRF/forced-password middleware.

    Covers denials/redirects/errors produced BEFORE View.dispatch as well as the
    view responses. It changes caching only; global CSRF stays fully enforced.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return private_response(response) if _PRIVATE_PATH.fullmatch(request.path_info) else response


def _error_text(error):
    return " ".join(error.messages)


class PrivateTripView(View):
    audience = "manager"
    http_method_names = ["get", "post", "head", "options"]
    template_name = ""
    page_title = "رحلات النخبة"
    actions = ()

    @method_decorator(sensitive_post_parameters("guardian_name", "guardian_relation", "notes", "signature", "review_token"))
    def dispatch(self, request, *args, **kwargs):
        try:
            if not getattr(request.user, "is_authenticated", False):
                login = "accounts:login_participant" if self.audience == "student" else "accounts:login_supervisor"
                from django.contrib.auth.views import redirect_to_login
                from django.urls import reverse
                response = redirect_to_login(request.get_full_path(), reverse(login))
            else:
                actor = services._fresh_user(request.user)
                if actor is None:
                    raise PermissionDenied
                if self.audience == "manager":
                    services._require_actor(actor)
                elif self.audience == "student":
                    services._require_actor(actor, student=True)
                elif not (services.can_manage_elite_trips(actor) or services.can_use_student_trips(actor)):
                    raise PermissionDenied
                request.user = actor
                self.authorize_object(**kwargs)
                if request.method == "POST":
                    action = services._single(request.POST, "action")
                    if action not in self.actions:
                        return private_response(HttpResponse("الإجراء المطلوب غير معروف.", status=400))
                response = super().dispatch(request, *args, **kwargs)
        except PermissionDenied:
            response = HttpResponse("ليس لديك صلاحية الوصول إلى هذه الاستمارة.", status=403)
        except Http404:
            response = HttpResponse("الرحلة أو الاستمارة المطلوبة غير متاحة.", status=404)
        except ValidationError as error:
            response = HttpResponse(_error_text(error), status=400)
        except Exception as error:
            # Do not log payloads, notes, signatures, request.POST or exception
            # contents. Unexpected failures cannot look like a saved declaration.
            _LOGGER.error("EliteTrip operation failed (%s)", type(error).__name__)
            response = HttpResponse("تعذر إتمام الطلب الآن. لم يُؤكد الحفظ؛ أعد المحاولة لاحقًا.", status=500)
        return private_response(response)

    def authorize_object(self, **kwargs):
        """Optional object guard precedes action handling for student IDOR denials."""
        return None

    def context(self, frozen_trip=None, **kwargs):
        from .views import build_navbar
        active = "student_trips" if self.audience == "student" or services.can_use_student_trips(self.request.user) else "elite_trip_list"
        return {"navbar_items": build_navbar(self.request.user, active), "page_title": self.page_title,
                "error": "", **services.document_texts(frozen_trip), **kwargs}

    def render(self, context, status=200, template=None):
        return render(self.request, template or self.template_name, context, status=status)

    def trip(self, pk):
        try:
            pk = services._id(pk)
        except ValidationError:
            raise Http404 from None
        return get_object_or_404(EliteTrip, pk=pk)


class EliteTripListView(PrivateTripView):
    template_name = "participants/elite_trips/list.html"
    actions = ("create",)

    def get(self, request):
        trips = EliteTrip.objects.annotate(
            selected_count=Count("nominations", filter=Q(nominations__is_selected=True, nominations__frozen_member=True), distinct=True),
            response_count=Count("nominations__consent", distinct=True),
        )
        # Draft selected count is staged rather than frozen.
        for trip in trips:
            if trip.status == EliteTrip.Status.DRAFT:
                trip.selected_count = trip.nominations.filter(is_selected=True).count()
        return self.render(self.context(trips=trips))

    def post(self, request):
        trip = services.create_trip(request.user)
        return redirect("participants:elite_trip_edit", pk=trip.pk)


class EliteTripEditView(PrivateTripView):
    template_name = "participants/elite_trips/edit.html"
    actions = ("save",)

    def edit_context(self, trip, form=None, error=""):
        rows = services.staged_trip_rows(trip)
        if form is not None and form.is_bound:
            try:
                selected = {services._id(raw) for raw in self.request.POST.getlist("selected_ids")}
            except ValidationError:
                selected = set()
            for row in rows:
                row["is_selected"] = row["participant_id"] in selected
                row["reason"] = self.request.POST.get("reason_{}".format(row["participant_id"]), row["reason"])[:250]
        return self.context(trip, trip=trip, form=form if form is not None else EliteTripForm(instance=trip),
                            rows=rows, groups=Group.objects.order_by("name", "pk"),
                            selected_count=sum(row["is_selected"] and row["in_scope"] and row["eligible"] for row in rows),
                            roster_count=sum(row["in_scope"] and row["eligible"] for row in rows), version=trip.version, error=error)

    def get(self, request, pk):
        trip = self.trip(pk)
        if trip.status != EliteTrip.Status.DRAFT:
            return redirect("participants:elite_trip_review", pk=pk)
        return self.render(self.edit_context(trip))

    def post(self, request, pk):
        trip = self.trip(pk)
        form = EliteTripForm(request.POST, instance=trip)
        if not form.is_valid():
            return self.render(self.edit_context(self.trip(pk), form=form), status=200)
        try:
            trip = services.save_trip_draft(request.user, pk, request.POST)
        except ValidationError as error:
            return self.render(self.edit_context(self.trip(pk), form=form, error=_error_text(error)), status=400)
        return redirect("participants:elite_trip_edit", pk=trip.pk)


class EliteTripReviewView(PrivateTripView):
    template_name = "participants/elite_trips/review.html"
    actions = ("approve",)

    def review_context(self, trip, error=""):
        rows = [row for row in services.staged_trip_rows(trip) if row["in_scope"] and row["eligible"]]
        selected = [row for row in rows if row["is_selected"]]
        form = EliteTripForm(data={name: getattr(trip, name + "_id") if name == "scope_group" else getattr(trip, name)
                                  for name in services.TRIP_FIELDS}, instance=trip, require_complete=True)
        complete = form.is_valid()
        return self.context(trip, trip=trip, trip_data=services.trip_data(trip), rows=rows, selected_rows=selected,
                            selected_count=len(selected), roster_count=len(rows), version=trip.version,
                            can_approve=trip.status == EliteTrip.Status.DRAFT and bool(selected) and complete,
                            form=form, error=error)

    def get(self, request, pk):
        return self.render(self.review_context(self.trip(pk)))

    def post(self, request, pk):
        try:
            trip = services.approve_trip(request.user, pk, services._single(request.POST, "version"), services._single(request.POST, "confirm"))
        except ValidationError as error:
            return self.render(self.review_context(self.trip(pk), error=_error_text(error)), status=400)
        return redirect("participants:elite_trip_responses", pk=trip.pk)


class EliteTripResponsesView(PrivateTripView):
    template_name = "participants/elite_trips/responses.html"
    actions = ("close", "cancel")

    def response_context(self, trip, error=""):
        nominations = list(trip.nominations.filter(frozen_member=True, is_selected=True).order_by("group_name", "rank_snapshot", "participant_id"))
        # Do not join/load the private binary signature in the follow-up list.
        consents = {item.nomination_id: item for item in GuardianTripConsent.objects.filter(nomination__in=nominations).defer("signature")}
        counts = Counter(item.decision for item in consents.values())
        filters = {name: self.request.GET.get(name, "").strip() for name in ("group", "status", "q")}
        filters["q"] = filters["q"][:100]
        groups = list(trip.snapshot.get("groups", []))
        group_ids = {str(group["id"]) for group in groups}
        invalid = (any(len(self.request.GET.getlist(name)) > 1 for name in filters)
                   or (filters["group"] and filters["group"] not in group_ids)
                   or (filters["status"] and filters["status"] not in ("yes", "no", "pending")))
        rows = []
        if not invalid:
            for nomination in nominations:
                consent = consents.get(nomination.pk)
                decision = consent.decision if consent else "pending"
                code = "P{}".format(nomination.participant_id)
                if filters["group"] and str(nomination.group_snapshot_id) != filters["group"]:
                    continue
                if filters["status"] and decision != filters["status"]:
                    continue
                if filters["q"] and filters["q"].casefold() not in nomination.student_name.casefold() and filters["q"].casefold() not in code.casefold():
                    continue
                rows.append({"nomination_id": nomination.pk, "participant_id": nomination.participant_id,
                             "student_name": nomination.student_name, "student_code": code,
                             "group_id": nomination.group_snapshot_id, "group_name": nomination.group_name,
                             "points": nomination.points_snapshot, "rank": nomination.rank_snapshot,
                             "decision": decision, "decision_label": consent.get_decision_display() if consent else "بانتظار الرد",
                             "guardian_name": consent.guardian_name if consent else "",
                             "guardian_relation": consent.get_guardian_relation_display() if consent else "",
                             "notes": consent.notes if consent else "",
                             "notes_display": (consent.notes or "لا توجد ملاحظات") if consent else "بانتظار رد ولي الأمر",
                             "submitted_at": services.timezone.localtime(consent.submitted_at, services.RIYADH) if consent else None,
                             "consent_id": consent.pk if consent else None})
        return self.context(trip, trip=trip, trip_data=services.trip_data(trip), rows=rows, groups=groups, filters=filters,
                            kpis={"selected": len(nominations), "yes": counts["yes"], "no": counts["no"], "pending": len(nominations) - len(consents)},
                            filtered_count=len(rows), version=trip.version, can_close=trip.status == EliteTrip.Status.APPROVED,
                            can_cancel=trip.status != EliteTrip.Status.CANCELLED, error=error or ("فلتر غير صالح؛ لم تُعرض بيانات خارج النطاق." if invalid else ""))

    def get(self, request, pk):
        return self.render(self.response_context(self.trip(pk)))

    def post(self, request, pk):
        try:
            trip = services.change_trip_status(request.user, pk, {"close": "closed", "cancel": "cancelled"}[request.POST["action"]],
                                               services._single(request.POST, "version"), services._single(request.POST, "confirm"))
        except ValidationError as error:
            return self.render(self.response_context(self.trip(pk), error=_error_text(error)), status=400)
        return redirect("participants:elite_trip_responses", pk=trip.pk)


class StudentTripsView(PrivateTripView):
    audience = "student"
    template_name = "participants/elite_trips/student_trips.html"
    http_method_names = ["get", "head", "options"]
    page_title = "رحلاتي"

    def get(self, request):
        return self.render(self.context(trip_cards=services.student_trip_cards(request.user),
                                        **notifications.notification_context(request.user)))


def _notification_response(user):
    context = notifications.notification_context(user)
    return JsonResponse({"ok": True, "notifications": context["trip_notifications"],
                         "unread_count": context["trip_notifications_unread"],
                         "attention_count": context["trip_notifications_attention"]})


class TripNotificationsView(PrivateTripView):
    audience = "student"
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        return _notification_response(request.user)


class TripNotificationReadView(PrivateTripView):
    audience = "student"
    http_method_names = ["post", "options"]
    actions = ("read",)

    def authorize_object(self, **kwargs):
        try:
            pk = services._id(kwargs.get("pk"))
        except ValidationError:
            raise Http404 from None
        get_object_or_404(notifications.own_trip_notifications(self.request.user).only("pk"), pk=pk)

    def post(self, request, pk):
        notifications.mark_notification_read(request.user, pk)
        if "application/json" in request.headers.get("Accept", "").lower() and request.accepts("application/json"):
            return _notification_response(request.user)
        return redirect("participants:student_trips")


class TripInvitationView(PrivateTripView):
    audience = "student"
    template_name = "participants/elite_trips/invitation.html"
    actions = ("review", "submit")

    def authorize_object(self, **kwargs):
        self.nomination(kwargs.get("pk"))

    def nomination(self, pk):
        try:
            pk = services._id(pk)
        except ValidationError:
            raise Http404 from None
        return get_object_or_404(services.own_nominations(self.request.user, selected=True).select_related("trip"), pk=pk)

    def invitation_context(self, nomination, form=None, error=""):
        consent = GuardianTripConsent.objects.filter(nomination=nomination).defer("signature").first()
        form = form if form is not None else GuardianTripConsentForm()
        image = form.cleaned_data.get("signature", "") if form.is_bound and hasattr(form, "cleaned_data") else ""
        nomination, safe_trip, consent = services.student_safe_objects(nomination, consent)
        return self.context(nomination.trip, nomination=nomination, trip=nomination.trip,
                            trip_data=services.trip_data(nomination.trip), form=form, consent=consent,
                            can_submit=nomination.trip.status == EliteTrip.Status.APPROVED and consent is None,
                            student_name=nomination.student_name, group_name=nomination.group_name,
                            signature_data_url=image, error=error)

    def get(self, request, pk):
        nomination = self.nomination(pk)
        consent = GuardianTripConsent.objects.filter(nomination=nomination).only("pk").first()
        if consent:
            return redirect("participants:trip_consent_detail", pk=consent.pk)
        return self.render(self.invitation_context(nomination))

    def post(self, request, pk):
        nomination = self.nomination(pk)
        if nomination.trip.status != EliteTrip.Status.APPROVED:
            return self.render(self.invitation_context(nomination, error="الرحلة مغلقة أو ملغاة؛ العرض تاريخي فقط ولا يقبل ردًا جديدًا."), status=400)
        form = GuardianTripConsentForm(request.POST)
        if not form.is_valid():
            return self.render(self.invitation_context(nomination, form=form), status=200)
        if request.POST["action"] == "submit":
            try:
                consent, _ = services.submit_consent(request.user, pk, request.POST)
            except ValidationError as error:
                return self.render(self.invitation_context(self.nomination(pk), form=form, error=_error_text(error)), status=400)
            return redirect("participants:trip_consent_detail", pk=consent.pk)
        try:
            token = services.make_review_token(request.user, nomination, form)
        except ValidationError as error:
            return self.render(self.invitation_context(nomination, form=form, error=_error_text(error)), status=400)
        values = dict(form.cleaned_data)
        values["decision_label"] = dict(GuardianTripConsent.Decision.choices)[values["decision"]]
        values["guardian_relation_label"] = dict(GuardianTripConsent.Relation.choices)[values["guardian_relation"]]
        # The final payload consists ONLY of normalized reviewed values. The visible
        # review page is readonly and shows every value plus the normalized signature.
        normalized = {name: values[name] for name in CONSENT_FIELDS}
        confirmation_form = GuardianTripConsentForm(normalized)
        confirmation_form.is_valid()
        for field in confirmation_form.fields.values():
            field.widget = forms.HiddenInput()
        nomination, safe_trip, _ = services.student_safe_objects(nomination)
        return self.render(self.context(nomination.trip, review_page=True, nomination=nomination, trip=nomination.trip,
                                        trip_data=services.trip_data(nomination.trip), form=confirmation_form,
                                        review_values=values, signature_data_url=values["signature"], review_token=token,
                                        student_name=nomination.student_name, group_name=nomination.group_name),
                           template="participants/elite_trips/consent_review.html")


class TripConsentDetailView(PrivateTripView):
    audience = "either"
    template_name = "participants/elite_trips/consent_detail.html"
    http_method_names = ["get", "head", "options"]

    def consent(self, pk):
        # Apply current actor scope BEFORE loading the consent or private blob.
        try:
            pk = services._id(pk)
        except ValidationError:
            raise Http404 from None
        return get_object_or_404(services.scoped_consents(self.request.user).select_related("nomination__trip"), pk=pk)

    def get(self, request, pk):
        consent = self.consent(pk)
        context = services.consent_document_context(consent)
        if services.can_use_student_trips(request.user):
            nomination, trip, safe_consent = services.student_safe_objects(consent.nomination, consent)
            context.update(nomination=nomination, trip=trip, consent=safe_consent)
        return self.render(self.context(context["trip"], **context))


class TripConsentPDFView(TripConsentDetailView):
    def get(self, request, pk):
        consent = self.consent(pk)  # Authorization always precedes import/render.
        try:
            from .elite_trip_pdf import render_consent_pdf
            body = render_consent_pdf(services.consent_document_context(consent))
        except Exception as error:
            _LOGGER.error("EliteTrip PDF unavailable (%s)", type(error).__name__)
            return HttpResponse("تعذر إنشاء ملف PDF الآن. حاول لاحقًا؛ الاستمارة المحفوظة لم تتغير.", status=503)
        response = HttpResponse(body, content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="trip-consent-{}.pdf"'.format(consent.reference)
        return response
