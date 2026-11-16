"""Bounded, server-side validation; signatures never touch public storage."""
import base64
import binascii
import io
import warnings
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from PIL import Image, ImageChops

from .models import EliteTrip, Group, GuardianTripConsent

SIGNATURE_PREFIX = "data:image/png;base64,"
MAX_SIGNATURE_BASE64 = 250 * 1024
MAX_SIGNATURE_BYTES = 180 * 1024
TRIP_FIELDS = ("title", "date", "time", "location", "fee", "meeting_point",
               "departure_time", "return_time", "instructions", "details", "scope_group")
CONSENT_FIELDS = ("decision", "guardian_name", "guardian_relation", "notes", "declaration_accepted", "signature")


def normalize_signature(value):
    """Accept one bounded static PNG, flatten on white and strip all metadata."""
    if not isinstance(value, str) or not value.startswith(SIGNATURE_PREFIX):
        raise ValidationError("التوقيع يجب أن يكون صورة PNG مرسومة داخل الاستمارة.")
    encoded = value[len(SIGNATURE_PREFIX):]
    if not encoded or len(encoded) > MAX_SIGNATURE_BASE64:
        raise ValidationError("حجم التوقيع يتجاوز الحد المسموح.")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError("بيانات التوقيع غير صالحة.") from None
    if not body or len(body) >= MAX_SIGNATURE_BYTES:
        raise ValidationError("حجم التوقيع يتجاوز الحد المسموح.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise ValueError
                width, height = image.size
                if not (100 <= width <= 1600 and 50 <= height <= 600):
                    raise ValueError
                image.verify()
            with Image.open(io.BytesIO(body)) as image:
                image.load()
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
        # Reject transparent/white canvases, isolated noise and effectively blank ink.
        delta = ImageChops.difference(flattened, Image.new("RGB", flattened.size, "white"))
        red, green, blue = delta.split()
        ink = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda x: 255 if x > 20 else 0)
        bbox = ink.getbbox()
        ink_count = ink.histogram()[255]
        if ink_count <= 20 or not bbox or bbox[2] - bbox[0] < 10 or bbox[3] - bbox[1] < 2:
            raise ValidationError("ارسم توقيعًا واضحًا؛ التوقيع الفارغ غير مقبول.")
        # A fresh image has no ICC/EXIF/text chunks from the source.
        clean = Image.new("RGB", flattened.size, "white")
        clean.paste(flattened)
        stream = io.BytesIO()
        clean.save(stream, format="PNG", optimize=True)
        normalized = stream.getvalue()
        if len(normalized) >= MAX_SIGNATURE_BYTES:
            raise ValidationError("حجم التوقيع بعد المعالجة يتجاوز الحد المسموح.")
        return normalized
    except ValidationError:
        raise
    except (Exception, Image.DecompressionBombError):
        raise ValidationError("صورة التوقيع غير صالحة أو أبعادها غير مسموحة.") from None


def signature_data_url(body):
    return SIGNATURE_PREFIX + base64.b64encode(bytes(body)).decode("ascii")


class EliteTripForm(forms.ModelForm):
    class Meta:
        model = EliteTrip
        fields = TRIP_FIELDS
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "departure_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "return_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "instructions": forms.Textarea(attrs={"rows": 4}),
            "details": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, require_complete=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_complete = require_complete
        self.fields["scope_group"].queryset = Group.objects.order_by("name", "pk")
        self.fields["scope_group"].empty_label = "كل البيئات"
        for name in TRIP_FIELDS:
            self.fields[name].required = require_complete and name not in ("instructions", "details", "scope_group")
            self.fields[name].error_messages["required"] = "هذا الحقل مطلوب لاعتماد الرحلة."
        self.fields["fee"].min_value = Decimal("0")
        self.fields["fee"].widget.attrs["min"] = "0"

    def clean(self):
        data = super().clean()
        times = [data.get(name) for name in ("departure_time", "time", "return_time")]
        if all(value is not None for value in times) and not times[0] <= times[1] < times[2]:
            raise ValidationError("للرحلة في يوم واحد: يجب أن يكون الانطلاق قبل بدء الرحلة أو معه، والعودة بعد بدء الرحلة.")
        if data.get("fee") is not None and data["fee"] < 0:
            self.add_error("fee", "الرسوم لا يمكن أن تكون سالبة؛ صفر يعني رحلة مجانية.")
        return data


class GuardianTripConsentForm(forms.Form):
    decision = forms.ChoiceField(choices=GuardianTripConsent.Decision.choices, widget=forms.RadioSelect,
                                label="قرار ولي الأمر", error_messages={"required": "اختر الموافقة أو عدم الموافقة دون افتراض مسبق."})
    guardian_name = forms.CharField(max_length=100, label="اسم ولي الأمر",
                                   error_messages={"required": "اكتب اسم ولي الأمر."})
    guardian_relation = forms.ChoiceField(choices=[("", "اختر الصفة")] + list(GuardianTripConsent.Relation.choices), label="صفة ولي الأمر")
    notes = forms.CharField(max_length=1500, required=False, widget=forms.Textarea(attrs={"rows": 5}), label="ملاحظات ولي الأمر")
    declaration_accepted = forms.BooleanField(required=True, label="إقرار ولي الأمر",
                                             error_messages={"required": "يجب قبول إقرار ولي الأمر قبل المراجعة."})
    signature = forms.CharField(max_length=MAX_SIGNATURE_BASE64 + len(SIGNATURE_PREFIX), widget=forms.HiddenInput,
                                label="التوقيع", error_messages={"required": "ارسم توقيع ولي الأمر قبل المراجعة."})

    def clean_signature(self):
        self.signature_bytes = normalize_signature(self.cleaned_data["signature"])
        return signature_data_url(self.signature_bytes)

    def clean(self):
        data = super().clean()
        # Duplicate scalar form fields are not an unambiguous reviewed decision.
        if hasattr(self.data, "getlist"):
            for name in CONSENT_FIELDS:
                if len(self.data.getlist(name)) > 1:
                    self.add_error(name, "أُرسل الحقل أكثر من مرة؛ أعد فتح الاستمارة.")
        return data
