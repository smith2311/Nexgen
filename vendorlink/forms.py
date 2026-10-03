import re

from django import forms
from django.db import models
from django.utils.text import slugify

from .constants import (
    CAPABILITY_ASSESSMENT_FIELDS, CAPABILITY_ASSESSMENT_SECTIONS,
    CAPABILITY_ICON_CHOICES, DEFAULT_CAPABILITY_ICON,
    KPI_METRIC_FIELDS, KPI_PERCENT_FIELDS,
)
from .models import Capability, CapabilityAssessment, Vendor, VendorBid, VendorCommercial, VendorKPIRecord, VendorRegistration, VendorRequest, VendorScorecard
from .utils import (
    continent_choices, continent_codes, continent_of, countries_in_continent,
    geonames_country_choices, geonames_valid_names,
)

_PHONE_RE = re.compile(r'^[\d\+\-\(\)\s\.]{7,25}$')
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,10}$')


def _country_choices(blank_label="Select a country…"):
    return geonames_country_choices(blank_label)


_CLS_INPUT_SELECT = "vm-input vm-select"
_VENDOR_SELECT_LABEL = "Select a vendor…"
_SCORE_COMMENT_PLACEHOLDER = "Why this score?"

_DISPOSABLE_DOMAINS = frozenset({
    'mailinator.com', 'guerrillamail.com', 'guerrillamail.info', 'guerrillamail.biz',
    'guerrillamail.de', 'guerrillamail.net', 'guerrillamail.org', 'tempmail.com',
    'yopmail.com', 'maildrop.cc', 'sharklasers.com', 'spam4.me', 'trashmail.com',
    'trashmail.me', 'trashmail.net', 'trashmail.at', 'trashmail.io',
    'dispostable.com', 'fakeinbox.com', 'mailnull.com', 'spamgourmet.com',
    'tempr.email', 'discard.email', 'throwam.com', 'mailexpire.com',
    'mintemail.com', 'getairmail.com', 'temp-mail.org', 'tempinbox.com',
    'throwaway.email', 'rhyta.com', 'jourrapide.com', 'armyspy.com',
    'dayrep.com', 'einrot.com', 'fleckens.hu', 'gustr.com',
    'superrito.com', 'teleworm.us', 'spamgourmet.net', 'spamgourmet.org',
})

_DOMAIN_TYPOS = {
    'gmial.com': 'gmail.com', 'gmai.com': 'gmail.com', 'gmil.com': 'gmail.com',
    'gmail.co': 'gmail.com', 'gmaill.com': 'gmail.com', 'gmaul.com': 'gmail.com',
    'yahooo.com': 'yahoo.com', 'yaho.com': 'yahoo.com', 'yhaoo.com': 'yahoo.com',
    'yahoo.co': 'yahoo.com', 'yaoo.com': 'yahoo.com',
    'hotmial.com': 'hotmail.com', 'hotmai.com': 'hotmail.com',
    'homail.com': 'hotmail.com', 'hotmal.com': 'hotmail.com',
    'outlok.com': 'outlook.com', 'outloook.com': 'outlook.com',
    'iclud.com': 'icloud.com', 'icould.com': 'icloud.com',
}


def validate_vendor_email(email):
    """Return (ok: bool, error: str|None, suggestion: str|None).

    Used by both the OTP send view and the form's clean_email.
    """
    if not email:
        return False, 'Email address is required.', None
    if not _EMAIL_RE.match(email):
        return False, 'Enter a valid email address (e.g. name@company.com).', None
    if '..' in email:
        return False, 'Email address cannot contain consecutive dots.', None
    if email.startswith('.') or email.endswith('.'):
        return False, 'Email address cannot start or end with a dot.', None
    local, domain = email.rsplit('@', 1)
    if not local or len(local) > 64:
        return False, 'The part before @ must be 1–64 characters.', None
    tld = domain.rsplit('.', 1)[-1]
    if len(tld) < 2 or len(tld) > 10:
        return False, 'Email domain has an invalid extension.', None
    if domain in _DISPOSABLE_DOMAINS:
        return False, 'Disposable or temporary email addresses are not accepted.', None
    if domain in _DOMAIN_TYPOS:
        suggestion = f'{local}@{_DOMAIN_TYPOS[domain]}'
        return False, f'Did you mean {suggestion}?', suggestion
    return True, None, None


class ContinentCountryMixin:
    """Adds a Continent select that narrows the Country select next to it.

    The continent is not stored: it is derived from the country through the same
    geonames master the country list comes from (``utils.continent_of``), so
    there is no second copy of that mapping to fall out of step. The field
    exists purely so the person filling the form picks from ~50 countries
    instead of 252 — which is why it is ``required=False``: a form submitted
    with only a country is complete and correct, and hard-requiring the
    continent would break every existing bookmark and API-ish POST.

    ``clean`` cross-checks the pair rather than trusting it. The browser resets
    the country whenever the continent changes, so a mismatch means either JS
    was off or the POST was hand-made — and silently accepting "Europe /
    India" would put a country on the record that the filter for its own
    continent then never returns.
    """

    #: Subclasses override if their country field is named differently.
    continent_field = "continent"
    country_field = "country"

    def _add_continent_field(self, blank_label="Select a continent…"):
        self.fields[self.continent_field] = forms.ChoiceField(
            choices=continent_choices(blank_label),
            required=False,
            label="Continent",
            widget=forms.Select(attrs={
                "class": _CLS_INPUT_SELECT,
                # Read by vl-continent-country.js, which repopulates the named
                # country select from the embedded continent→country map.
                "data-vl-continent": self.country_field,
            }),
        )
        country = self.fields.get(self.country_field)
        if country is not None:
            country.widget.attrs["data-vl-country"] = "1"
        # Pre-select the continent of the country already on the record, so
        # editing an existing vendor opens with the pair consistent rather than
        # with an empty continent above a filled country.
        existing = getattr(self.instance, self.country_field, "") if self.instance else ""
        if existing and not self.initial.get(self.continent_field):
            self.initial[self.continent_field] = continent_of(existing)

    def clean_continent(self):
        code = (self.cleaned_data.get(self.continent_field) or "").strip().upper()
        if code and code not in continent_codes():
            raise forms.ValidationError("Select a continent from the list.")
        return code

    def clean(self):
        cleaned = super().clean()
        code = (cleaned.get(self.continent_field) or "").strip().upper()
        country = (cleaned.get(self.country_field) or "").strip()
        if code and country and country not in countries_in_continent(code):
            self.add_error(
                self.country_field,
                f"{country} is not in the selected continent. "
                "Pick the continent first, then a country inside it.",
            )
        return cleaned


class CapabilityFieldMixin:
    """Turns the ``capabilities`` M2M into a checkbox group off the master.

    The nine capabilities used to be nine ``BooleanField``s and so nine
    hardcoded checkboxes in each template. They are master rows now, which is
    what makes "add a capability later" an admin action rather than a release.

    ``_save_m2m`` is overridden because the field's queryset is the *active*
    capabilities: ModelForm assigns an M2M wholesale, so a vendor holding a
    capability that was since retired would lose it on the next unrelated edit
    to their phone number. The retired links are captured up front and
    re-attached after the normal assignment — the form never offered them, so a
    save through it must not be read as the vendor withdrawing them.
    """

    def _init_capabilities(self):
        """Point the field at the live master.

        Order matters and used to be wrong. This assigned ``queryset`` and THEN
        replaced ``field.widget`` with a fresh ``CheckboxSelectMultiple()``.
        ``ModelChoiceField``'s queryset setter pushes the options onto whatever
        widget is attached at that moment, so the choices landed on the widget
        that was about to be thrown away and the replacement had none — a field
        whose queryset held all nine capabilities, rendering zero checkboxes.
        The vendor form fell through to its "No capabilities are defined yet"
        empty state on a database with nine of them.

        The widget is declared in each form's ``Meta.widgets`` now, so there is
        nothing to swap and the options cannot be assigned to the wrong object.
        """
        field = self.fields.get("capabilities")
        if field is None:
            return
        field.required = False
        field.label = "Capabilities"
        # Assigned last, so the options reach the widget that will render.
        field.queryset = Capability.objects.filter(is_active=True)
        self._retired_held = (
            [c for c in self.instance.capabilities.all() if not c.is_active]
            if self.instance and self.instance.pk else []
        )

    def _save_m2m(self):
        super()._save_m2m()
        retired = getattr(self, "_retired_held", None)
        if retired:
            self.instance.capabilities.add(*retired)
        self.instance.__dict__.pop("_capability_codes", None)

    @property
    def capability_choices(self):
        """[{capability, checked}, …] for templates that render icon cards.

        The public registration form shows each capability as a card with the
        master's own Font Awesome icon on it. A form choice is a
        ``(value, label)`` pair and cannot carry the icon, and a Django template
        cannot look a side dict up by a loop variable to find it — so the pairing
        is assembled here, where both halves are in scope.

        Checked state comes from the bound value, so a form redisplayed after a
        validation error keeps the boxes the person actually ticked.
        """
        field = self.fields.get("capabilities")
        if field is None:
            return []
        raw = self["capabilities"].value() or []
        selected = {str(v) for v in raw}
        return [
            {"capability": capability, "checked": str(capability.pk) in selected}
            for capability in field.queryset
        ]


class VendorForm(ContinentCountryMixin, CapabilityFieldMixin, forms.ModelForm):
    # Strict select sourced from the Country master — no free text, no typing in new countries.
    country = forms.ChoiceField(
        choices=[],  # populated dynamically in __init__
        widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Full master list, always. The continent select narrows it in the
        # browser; validating against the whole master keeps a POST that never
        # ran the JS (or carries a country from another continent the user then
        # corrected) from being rejected for the wrong reason.
        self.fields["country"].choices = _country_choices()
        self._add_continent_field()
        self._init_capabilities()
        if not self.instance.pk:
            self.fields["contact_person"].required = True
            self.fields["email"].required = True

    class Meta:
        model = Vendor
        fields = [
            "vendor_name", "country", "state", "city", "warehouse_location",
            "contact_person", "email", "mobile", "website",
            "capabilities",
            "is_active",
        ]
        widgets = {
            "vendor_name": forms.TextInput(attrs={"class": "vm-input", "placeholder": "e.g. Blue Dart Express", "maxlength": "200"}),
            "state": forms.TextInput(attrs={"class": "vm-input", "placeholder": "e.g. Maharashtra", "maxlength": "120"}),
            "city": forms.TextInput(attrs={"class": "vm-input", "placeholder": "Select or type a city", "maxlength": "120"}),
            "warehouse_location": forms.TextInput(attrs={"class": "vm-input", "placeholder": "Full warehouse address", "maxlength": "300"}),
            "contact_person": forms.TextInput(attrs={"class": "vm-input", "placeholder": "e.g. Priya Sharma", "maxlength": "150"}),
            "email": forms.EmailInput(attrs={"class": "vm-input", "placeholder": "name@vendor.com"}),
            "mobile": forms.TextInput(attrs={"class": "vm-input", "placeholder": "+91 ..."}),
            "website": forms.URLInput(attrs={"class": "vm-input", "placeholder": "https://vendor.com"}),
            # Declared here, not swapped in __init__ — see _init_capabilities.
            "capabilities": forms.CheckboxSelectMultiple(),
        }

    # ── vendor_name ───────────────────────────────────────────────────────────
    def clean_vendor_name(self):
        name = (self.cleaned_data.get("vendor_name") or "").strip()
        if not name:
            raise forms.ValidationError("Vendor name is required.")
        if len(name) > 200:
            raise forms.ValidationError("Vendor name must be 200 characters or fewer.")
        qs = Vendor.objects.filter(vendor_name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A vendor with this name already exists.")
        return name

    # ── country ───────────────────────────────────────────────────────────────
    def clean_country(self):
        country = (self.cleaned_data.get("country") or "").strip()
        if not country:
            raise forms.ValidationError("Please select a country.")
        if country not in geonames_valid_names():
            raise forms.ValidationError("Select a valid country from the list.")
        return country

    # ── email ─────────────────────────────────────────────────────────────────
    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email and not self.instance.pk:
            raise forms.ValidationError("Vendor email address is required when adding a vendor.")
        if email:
            valid, error, _suggestion = validate_vendor_email(email)
            if not valid:
                raise forms.ValidationError(error)
        return email.lower()

    # ── mobile ────────────────────────────────────────────────────────────────
    def clean_mobile(self):
        mobile = (self.cleaned_data.get("mobile") or "").strip()
        if mobile:
            if not _PHONE_RE.match(mobile):
                raise forms.ValidationError(
                    "Enter a valid phone number — digits, +, -, (, ) and spaces only (7–25 characters)."
                )
        return mobile

    # ── website ───────────────────────────────────────────────────────────────
    def clean_website(self):
        website = (self.cleaned_data.get("website") or "").strip()
        if website:
            if not website.startswith(("http://", "https://")):
                website = "https://" + website
            host = website.replace("https://", "").replace("http://", "").split("/")[0].split("?")[0]
            if "." not in host or len(host) < 4:
                raise forms.ValidationError("Enter a valid website URL (e.g. https://vendor.com).")
        return website

    # ── warehouse_location ───────────────────────────────────────────────────
    def clean_warehouse_location(self):
        loc = (self.cleaned_data.get("warehouse_location") or "").strip()
        if len(loc) > 300:
            raise forms.ValidationError("Warehouse location must be 300 characters or fewer.")
        return loc

    def clean_city(self):
        city = (self.cleaned_data.get("city") or "").strip()
        if len(city) > 120:
            raise forms.ValidationError("City must be 120 characters or fewer.")
        return city

    def clean_state(self):
        state = (self.cleaned_data.get("state") or "").strip()
        if len(state) > 120:
            raise forms.ValidationError("State must be 120 characters or fewer.")
        return state

    # ── contact_person ────────────────────────────────────────────────────────
    def clean_contact_person(self):
        name = (self.cleaned_data.get("contact_person") or "").strip()
        if not name and not self.instance.pk:
            raise forms.ValidationError("Contact person is required when adding a vendor.")
        if len(name) > 150:
            raise forms.ValidationError("Contact name must be 150 characters or fewer.")
        return name


class VendorRequestForm(ContinentCountryMixin, forms.ModelForm):
    country = forms.ChoiceField(
        choices=[],  # populated dynamically in __init__
        widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}),
    )
    bid_open_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"class": "vm-input", "type": "datetime-local"}),
        label="Bid opens at",
    )
    bid_close_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"class": "vm-input", "type": "datetime-local"}),
        label="Bid closes at",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country"].choices = _country_choices()
        self._add_continent_field()
        # Pre-populate datetime-local with current instance value in correct format
        for fname in ("bid_open_at", "bid_close_at"):
            if self.instance and getattr(self.instance, fname, None):
                from django.utils import timezone
                val = getattr(self.instance, fname)
                # Convert to local naive for datetime-local input
                local = timezone.localtime(val)
                self.initial[fname] = local.strftime("%Y-%m-%dT%H:%M")

    class Meta:
        model = VendorRequest
        fields = [
            "subject", "country",
            "header_message",
            "warehouse_space_from", "warehouse_space_to",
            "scope_of_work", "product_verticals",
            "storage_temp_from", "storage_temp_to",
            "monthly_shipment_from", "monthly_shipment_to",
            "inbound_from", "inbound_to",
            "outbound_from", "outbound_to",
            "footer_message",
            "bid_open_at", "bid_close_at",
        ]
        widgets = {
            "subject": forms.TextInput(attrs={
                "class": "vm-input",
                "placeholder": "e.g. 3PL Warehousing & Distribution — Expression of Interest",
                "maxlength": "200",
            }),
            "header_message": forms.Textarea(attrs={"class": "vm-input vm-rich", "rows": 4,
                                                    "data-placeholder": "Address / opening lines…"}),
            "scope_of_work": forms.Textarea(attrs={"class": "vm-input vm-rich", "rows": 6,
                                                   "data-placeholder": "List scope points…"}),
            "footer_message": forms.Textarea(attrs={"class": "vm-input vm-rich", "rows": 4,
                                                    "data-placeholder": "Closing remarks / sign-off…"}),
            "product_verticals": forms.TextInput(attrs={"class": "vm-input",
                                                        "placeholder": "e.g. Cardiovascular, Orthopedics, Diagnostics",
                                                        "maxlength": "300"}),
            "warehouse_space_from": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "From"}),
            "warehouse_space_to": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "To"}),
            "storage_temp_from": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "From"}),
            "storage_temp_to": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "To"}),
            "monthly_shipment_from": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "From"}),
            "monthly_shipment_to": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "To"}),
            "inbound_from": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "From"}),
            "inbound_to": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "To"}),
            "outbound_from": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "From"}),
            "outbound_to": forms.TextInput(attrs={"class": "vm-input vm-range-input", "placeholder": "To"}),
        }

    # ── subject ───────────────────────────────────────────────────────────────
    def clean_subject(self):
        subject = (self.cleaned_data.get("subject") or "").strip()
        if not subject:
            raise forms.ValidationError("Subject is required.")
        if len(subject) > 200:
            raise forms.ValidationError("Subject must be 200 characters or fewer.")
        return subject

    # ── country ───────────────────────────────────────────────────────────────
    def clean_country(self):
        country = (self.cleaned_data.get("country") or "").strip()
        if not country:
            raise forms.ValidationError("Please select a country to send this request to.")
        if country not in geonames_valid_names():
            raise forms.ValidationError("Select a valid country from the list.")
        return country

    # ── product_verticals ─────────────────────────────────────────────────────
    def clean_product_verticals(self):
        v = (self.cleaned_data.get("product_verticals") or "").strip()
        if len(v) > 300:
            raise forms.ValidationError("Product verticals must be 300 characters or fewer.")
        return v

    # ── cross-field range validation ──────────────────────────────────────────
    @staticmethod
    def _parse_range_val(raw, label, allow_negative=False):
        """Return (float_or_None, error_msg_or_None)."""
        val = (raw or "").strip()
        if not val:
            return None, None
        try:
            n = float(val)
        except ValueError:
            return None, f"{label} must be a numeric value."
        if not allow_negative and n < 0:
            return None, f"{label} cannot be negative."
        return n, None

    def clean(self):
        cleaned = super().clean()

        RANGE_SPECS = [
            ("warehouse_space_from", "warehouse_space_to", "Warehouse space", False),
            ("storage_temp_from", "storage_temp_to", "Storage temperature", True),
            ("monthly_shipment_from", "monthly_shipment_to", "Monthly shipment", False),
            ("inbound_from", "inbound_to", "Inbound volume", False),
            ("outbound_from", "outbound_to", "Outbound volume", False),
        ]

        for from_f, to_f, label, allow_neg in RANGE_SPECS:
            frm_raw = cleaned.get(from_f)
            to_raw = cleaned.get(to_f)
            frm_num, frm_err = self._parse_range_val(frm_raw, f"{label} (From)", allow_neg)
            to_num, to_err = self._parse_range_val(to_raw, f"{label} (To)", allow_neg)
            if frm_err:
                self.add_error(from_f, frm_err)
            if to_err:
                self.add_error(to_f, to_err)
            if frm_num is not None and to_num is not None and frm_num > to_num:
                self.add_error(to_f, f"{label}: 'To' value ({to_raw}) must be ≥ 'From' value ({frm_raw}).")

        bid_open = cleaned.get("bid_open_at")
        bid_close = cleaned.get("bid_close_at")
        if bid_open and bid_close and bid_close <= bid_open:
            self.add_error("bid_close_at", "Bid closing time must be after the opening time.")

        return cleaned


class VendorRegistrationForm(ContinentCountryMixin, CapabilityFieldMixin, forms.ModelForm):
    country = forms.ChoiceField(
        choices=[],  # populated dynamically in __init__
        widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country"].choices = _country_choices("Select your country…")
        self._add_continent_field("Select your continent…")
        # Same master the internal vendor form reads, so a capability an admin
        # adds appears on the public registration form with no code change.
        self._init_capabilities()

    class Meta:
        model = VendorRegistration
        fields = [
            "vendor_name", "country", "state", "city", "warehouse_location",
            "contact_person", "email", "mobile", "website",
            "capabilities",
        ]
        widgets = {
            "vendor_name": forms.TextInput(attrs={"class": "vm-input", "placeholder": "e.g. ABC Logistics Pvt. Ltd.", "maxlength": "200"}),
            "state": forms.TextInput(attrs={"class": "vm-input", "placeholder": "e.g. Maharashtra", "maxlength": "120"}),
            "city": forms.TextInput(attrs={"class": "vm-input", "placeholder": "Select or type a city", "maxlength": "120"}),
            "warehouse_location": forms.TextInput(attrs={"class": "vm-input", "placeholder": "Full warehouse address", "maxlength": "300"}),
            "contact_person": forms.TextInput(attrs={"class": "vm-input", "placeholder": "Your full name", "maxlength": "150"}),
            "email": forms.EmailInput(attrs={"class": "vm-input", "placeholder": "you@company.com"}),
            "mobile": forms.TextInput(attrs={"class": "vm-input", "placeholder": "+971 ..."}),
            "website": forms.URLInput(attrs={"class": "vm-input", "placeholder": "https://yourcompany.com"}),
            "capabilities": forms.CheckboxSelectMultiple(),
        }

    def clean_vendor_name(self):
        name = (self.cleaned_data.get("vendor_name") or "").strip()
        if not name:
            raise forms.ValidationError("Company name is required.")
        if len(name) > 200:
            raise forms.ValidationError("Company name must be 200 characters or fewer.")
        return name

    def clean_country(self):
        country = (self.cleaned_data.get("country") or "").strip()
        if not country:
            raise forms.ValidationError("Please select your country.")
        if country not in geonames_valid_names():
            raise forms.ValidationError("Select a valid country from the list.")
        return country

    def clean_contact_person(self):
        name = (self.cleaned_data.get("contact_person") or "").strip()
        if not name:
            raise forms.ValidationError("Contact person name is required.")
        return name

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        ok, error, _ = validate_vendor_email(email)
        if not ok:
            raise forms.ValidationError(error)
        if VendorRegistration.objects.filter(email=email, status=VendorRegistration.STATUS_PENDING).exists():
            raise forms.ValidationError("A registration with this email is already pending review.")
        if VendorRegistration.objects.filter(email=email, status=VendorRegistration.STATUS_APPROVED).exists():
            raise forms.ValidationError("This email is already registered as an approved vendor.")
        if Vendor.objects.filter(email__iexact=email, is_active=True).exists():
            raise forms.ValidationError("This email already exists in our active vendor directory.")
        return email

    def clean_mobile(self):
        mobile = (self.cleaned_data.get("mobile") or "").strip()
        if mobile and not _PHONE_RE.match(mobile):
            raise forms.ValidationError("Enter a valid phone number (digits, +, -, spaces; 7–25 characters).")
        return mobile

    def clean_website(self):
        website = (self.cleaned_data.get("website") or "").strip()
        if website:
            if not website.startswith(("http://", "https://")):
                website = "https://" + website
            host = website.replace("https://", "").replace("http://", "").split("/")[0]
            if "." not in host or len(host) < 4:
                raise forms.ValidationError("Enter a valid website URL (e.g. https://yourcompany.com).")
        return website

    def clean_city(self):
        city = (self.cleaned_data.get("city") or "").strip()
        if len(city) > 120:
            raise forms.ValidationError("City must be 120 characters or fewer.")
        return city

    def clean_state(self):
        state = (self.cleaned_data.get("state") or "").strip()
        if len(state) > 120:
            raise forms.ValidationError("State must be 120 characters or fewer.")
        return state


class VendorBidForm(forms.ModelForm):
    CURRENCY_CHOICES = [
        ("USD", "USD — US Dollar"), ("EUR", "EUR — Euro"), ("GBP", "GBP — Pound"),
        ("INR", "INR — Indian Rupee"), ("AED", "AED — UAE Dirham"), ("SGD", "SGD — Singapore Dollar"),
        ("JPY", "JPY — Japanese Yen"), ("CNY", "CNY — Chinese Yuan"), ("AUD", "AUD — Australian Dollar"),
        ("CAD", "CAD — Canadian Dollar"), ("CHF", "CHF — Swiss Franc"), ("SAR", "SAR — Saudi Riyal"),
    ]
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        widget=forms.Select(attrs={"class": "vm-input vm-select"}),
        initial="USD",
    )

    class Meta:
        model = VendorBid
        fields = [
            "currency", "storage_cost", "inbound_cost", "outbound_cost",
            "pick_cost", "packing_cost", "last_mile_charges", "manpower_charges",
            "additional_charges", "notes",
        ]
        widgets = {
            "storage_cost":     forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01"}),
            "inbound_cost":     forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01"}),
            "outbound_cost":    forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01"}),
            "pick_cost":        forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01"}),
            "packing_cost":     forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01"}),
            "last_mile_charges":  forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01"}),
            "manpower_charges": forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01"}),
            "additional_charges": forms.TextInput(attrs={"class": "vm-input", "placeholder": "e.g. Fuel surcharge 5%, Handling fee…"}),
            "notes": forms.Textarea(attrs={"class": "vm-input", "rows": 4, "placeholder": "Any message or additional terms for the Meril SCM team…"}),
        }


class VendorCommercialForm(forms.ModelForm):
    """The Phase 2 rate card form — 13 SOW fields plus the currency they're in.

    Every cost line is optional on purpose. Vendors quote what applies to them
    (a pure warehousing provider has no last-mile rate), and forcing a 0 into an
    unused line would make that vendor look free rather than not-applicable —
    which matters because ``total_cost`` skips blanks but would happily add zeros.
    """

    CURRENCY_CHOICES = VendorBidForm.CURRENCY_CHOICES

    #: The eight numeric lines, so the widget dict below is built once rather
    #: than typed out eight times with a typo waiting to happen.
    COST_FIELDS = (
        "storage_cost", "inbound_cost", "outbound_cost", "pick_cost",
        "packing_cost", "customs_clearance_cost", "last_mile_charges",
        "manpower_charges",
    )

    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.none(),          # set in __init__, see below
        widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}),
        empty_label=_VENDOR_SELECT_LABEL,
        help_text="Rate cards attach to a vendor already in the Vendor Master.",
    )
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}),
        initial="USD",
    )

    class Meta:
        model = VendorCommercial
        fields = [
            "vendor", "currency",
            "storage_cost", "inbound_cost", "outbound_cost", "pick_cost",
            "packing_cost", "customs_clearance_cost", "last_mile_charges",
            "manpower_charges", "additional_charges",
            "contract_start", "contract_end", "price_validity",
            "insurance", "notes",
        ]
        widgets = {
            "additional_charges": forms.Textarea(attrs={
                "class": "vm-input", "rows": 3,
                "placeholder": "Fuel surcharge 5%, after-hours handling, minimum monthly billing…",
            }),
            "contract_start": forms.DateInput(attrs={"class": "vm-input", "type": "date"}),
            "contract_end": forms.DateInput(attrs={"class": "vm-input", "type": "date"}),
            "price_validity": forms.DateInput(attrs={"class": "vm-input", "type": "date"}),
            "insurance": forms.TextInput(attrs={
                "class": "vm-input",
                "placeholder": "All-risk, EUR 2m in warehouse / EUR 500k in transit",
            }),
            "notes": forms.Textarea(attrs={
                "class": "vm-input", "rows": 3,
                "placeholder": "Internal notes — negotiation history, who owns the relationship…",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only active vendors can be given a new rate card, but an existing card
        # must keep showing its own vendor even if that vendor was later
        # deactivated — otherwise editing the card silently reassigns it.
        vendors = Vendor.objects.filter(is_active=True)
        if self.instance.pk and self.instance.vendor_id:
            vendors = Vendor.objects.filter(
                models.Q(is_active=True) | models.Q(pk=self.instance.vendor_id)
            )
        self.fields["vendor"].queryset = vendors.order_by("vendor_name")

        for name in self.COST_FIELDS:
            self.fields[name].widget = forms.NumberInput(attrs={
                "class": "vm-input", "placeholder": "0.00", "step": "0.01", "min": "0",
            })
            self.fields[name].required = False

    def _clean_non_negative(self, name):
        value = self.cleaned_data.get(name)
        if value is not None and value < 0:
            raise forms.ValidationError("Cost cannot be negative.")
        return value

    def clean(self):
        cleaned = super().clean()

        # Negative money is always a typo, and a stray minus sign would quietly
        # drag total_cost down instead of failing.
        for name in self.COST_FIELDS:
            if name in cleaned and cleaned.get(name) is not None and cleaned[name] < 0:
                self.add_error(name, "Cost cannot be negative.")

        start, end = cleaned.get("contract_start"), cleaned.get("contract_end")
        if start and end and end < start:
            self.add_error("contract_end", "Contract end cannot be before the start date.")

        # A card with no costs and no terms is an empty row that would still show
        # up in the list and the export, so it is rejected rather than stored.
        has_cost = any(cleaned.get(n) is not None for n in self.COST_FIELDS)
        has_terms = any(cleaned.get(n) for n in ("additional_charges", "insurance",
                                                "contract_start", "contract_end", "price_validity"))
        if not has_cost and not has_terms:
            raise forms.ValidationError(
                "Enter at least one cost or contract term — an empty rate card cannot be saved."
            )
        return cleaned


class CapabilityAssessmentInviteForm(forms.Form):
    """Phase 3 — generates a unique, token-based link for one external vendor."""

    vendor_email = forms.EmailField(
        label="Vendor Email",
        widget=forms.EmailInput(attrs={"class": "vm-input", "placeholder": "contact@vendor.com"}),
    )
    expires_days = forms.IntegerField(
        label="Link expires in (days)", initial=14, min_value=1, max_value=90,
        widget=forms.NumberInput(attrs={"class": "vm-input"}),
    )

    def clean_vendor_email(self):
        email = (self.cleaned_data.get("vendor_email") or "").strip().lower()
        ok, error, suggestion = validate_vendor_email(email)
        if not ok:
            raise forms.ValidationError(f"{error} {'Did you mean ' + suggestion + '?' if suggestion else ''}".strip())

        # An invite already in flight for this address would fork the review
        # queue into two rows for the same vendor — reuse it instead.
        existing = CapabilityAssessment.objects.filter(vendor_email=email).exclude(
            status__in=[CapabilityAssessment.STATUS_REJECTED]
        ).order_by("-created_at").first()
        if existing and existing.status != CapabilityAssessment.STATUS_APPROVED:
            raise forms.ValidationError(
                f"An assessment for {email} is already {existing.get_status_display().lower()}. "
                "Review it instead of sending a new invite."
            )
        return email


def _capability_assessment_widgets():
    """Short fields render as one-line inputs, long fields as textareas.

    Built from CAPABILITY_ASSESSMENT_SECTIONS so the widget kind can never drift
    from the spec used to build the external form's sections. The HTML
    ``maxlength`` mirrors each field's real model ``max_length`` rather than a
    flat guess, so the browser never lets a vendor type past a limit the
    server will then reject.
    """
    model_fields = {f.name: f for f in CapabilityAssessment._meta.get_fields()}
    widgets = {}
    for _, rows in CAPABILITY_ASSESSMENT_SECTIONS:
        for name, _label, _hint, _required, kind in rows:
            if kind == "short":
                max_len = getattr(model_fields.get(name), "max_length", None) or 300
                widgets[name] = forms.TextInput(attrs={"class": "vl-ext-input", "maxlength": str(max_len)})
            else:
                widgets[name] = forms.Textarea(attrs={"class": "vl-ext-textarea", "rows": 3})
    return widgets


class VendorKPIRecordForm(forms.ModelForm):
    """One month's KPI reading for one vendor — the SOW's 8-metric sheet.

    Every metric is optional, same reasoning as the rate card: a vendor a
    Global SCM analyst hasn't finished reviewing this month shouldn't force a
    0 into a metric nobody actually measured yet.
    """

    CURRENCY_CHOICES = VendorBid.CURRENCY_CHOICES

    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.none(),
        widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}),
        empty_label=_VENDOR_SELECT_LABEL,
    )
    period = forms.DateField(
        label="Period",
        widget=forms.DateInput(attrs={"class": "vm-input", "type": "date"}),
        help_text="Any date within the month — it's normalised to the 1st automatically.",
    )
    currency = forms.ChoiceField(choices=CURRENCY_CHOICES, widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}), initial="USD")

    class Meta:
        model = VendorKPIRecord
        fields = [
            "vendor", "period",
            "inventory_accuracy", "on_time_delivery", "order_fulfilment_accuracy", "damage_rate",
            "response_time_hours", "capa_closure_days", "internal_complaints",
            "cost_per_shipment", "currency", "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"class": "vm-input", "rows": 3, "placeholder": "Context for this month's numbers…"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        vendors = Vendor.objects.filter(is_active=True)
        if self.instance.pk and self.instance.vendor_id:
            vendors = Vendor.objects.filter(models.Q(is_active=True) | models.Q(pk=self.instance.vendor_id))
        self.fields["vendor"].queryset = vendors.order_by("vendor_name")

        for name, _label, _unit in KPI_METRIC_FIELDS:
            if name == "internal_complaints":
                self.fields[name].widget = forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0", "min": "0", "step": "1"})
            elif name != "cost_per_shipment":
                self.fields[name].widget = forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01", "min": "0"})
            self.fields[name].required = False
        self.fields["cost_per_shipment"].widget = forms.NumberInput(attrs={"class": "vm-input", "placeholder": "0.00", "step": "0.01", "min": "0"})
        self.fields["cost_per_shipment"].required = False

    def clean(self):
        cleaned = super().clean()

        for name in KPI_PERCENT_FIELDS:
            value = cleaned.get(name)
            if value is not None and not (0 <= value <= 100):
                self.add_error(name, "Enter a percentage between 0 and 100.")

        for name in ("response_time_hours", "capa_closure_days", "internal_complaints", "cost_per_shipment"):
            value = cleaned.get(name)
            if value is not None and value < 0:
                self.add_error(name, "This value cannot be negative.")

        if not any(cleaned.get(name) is not None for name, *_ in KPI_METRIC_FIELDS):
            raise forms.ValidationError(
                "Enter at least one metric — an empty KPI record cannot be saved."
            )
        return cleaned


class VendorScorecardForm(forms.ModelForm):
    """A single weighted scoring event — the SOW's Vendor Scorecard sheet.

    All four scores and their comments are required together: the plan is
    explicit that a comment is mandatory for every score, and a Top-10 ranking
    built from a partially-scored vendor would be misleading rather than just
    incomplete.
    """

    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.none(),
        widget=forms.Select(attrs={"class": _CLS_INPUT_SELECT}),
        empty_label=_VENDOR_SELECT_LABEL,
    )

    class Meta:
        model = VendorScorecard
        fields = [
            "vendor",
            "cost_score", "cost_comment",
            "serviceability_score", "serviceability_comment",
            "wms_reports_score", "wms_reports_comment",
            "tat_score", "tat_comment",
            "notes",
        ]
        widgets = {
            "cost_score": forms.Select(attrs={"class": "vm-select"}),
            "serviceability_score": forms.Select(attrs={"class": "vm-select"}),
            "wms_reports_score": forms.Select(attrs={"class": "vm-select"}),
            "tat_score": forms.Select(attrs={"class": "vm-select"}),
            "cost_comment": forms.Textarea(attrs={"class": "vm-input", "rows": 2, "placeholder": _SCORE_COMMENT_PLACEHOLDER}),
            "serviceability_comment": forms.Textarea(attrs={"class": "vm-input", "rows": 2, "placeholder": _SCORE_COMMENT_PLACEHOLDER}),
            "wms_reports_comment": forms.Textarea(attrs={"class": "vm-input", "rows": 2, "placeholder": _SCORE_COMMENT_PLACEHOLDER}),
            "tat_comment": forms.Textarea(attrs={"class": "vm-input", "rows": 2, "placeholder": _SCORE_COMMENT_PLACEHOLDER}),
            "notes": forms.Textarea(attrs={"class": "vm-input", "rows": 2, "placeholder": "Optional — overall context for this scoring round"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Vendor.objects.filter(is_active=True).order_by("vendor_name")


class CapabilityAssessmentExternalForm(forms.ModelForm):
    """The 34-field self-assessment a vendor fills over their token link.

    Every field is optional at the form level (mirrors the model's ``blank``
    fields) so "Save Progress" works on a half-filled form; the two mandatory
    fields are enforced by the view only when the vendor clicks Submit, since
    that is the one action meant to require them.
    """

    class Meta:
        model = CapabilityAssessment
        fields = CAPABILITY_ASSESSMENT_FIELDS
        widgets = _capability_assessment_widgets()


class CapabilityForm(forms.ModelForm):
    """Add / edit one row of the capability master.

    Two fields on screen: the name and the icon. That is the whole form, because
    the people who maintain this master are the SCM team, not developers.

    ``code`` and ``sort_order`` are still required by the model, and ``code`` is
    still what filter URLs, Excel column matching and the AI evaluator payload
    key on — they are derived here rather than asked for. A field labelled
    "Code" gets a label typed into it with spaces in it, and a field labelled
    "Position" gets left at its default every time.

    The icon is a hidden input driven by the palette in
    ``_capability_icon_picker.html``: the value has to be one of
    ``CAPABILITY_ICON_CHOICES``, so nobody needs to know that a snowflake is
    spelled ``fa-snowflake``, and a class Font Awesome cannot render — which
    shows as a blank square — cannot be saved.
    """

    class Meta:
        model = Capability
        fields = ["label", "icon"]
        widgets = {
            "label": forms.TextInput(attrs={
                "class": "vm-input", "placeholder": "e.g. Cold Chain Validation",
                "maxlength": "120", "autocomplete": "off",
            }),
            "icon": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["label"].label = "Capability name"
        self.fields["icon"].required = False
        if not self.initial.get("icon") and not getattr(self.instance, "icon", ""):
            self.initial["icon"] = DEFAULT_CAPABILITY_ICON

    # ── label ────────────────────────────────────────────────────────────────
    def clean_label(self):
        label = " ".join((self.cleaned_data.get("label") or "").split())
        if not label:
            raise forms.ValidationError("Enter a name for this capability.")
        qs = Capability.objects.filter(label__iexact=label)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A capability with this name already exists.")
        return label

    # ── icon ─────────────────────────────────────────────────────────────────
    def clean_icon(self):
        """Normalise, then insist on an icon the palette actually offers.

        Normalising first because a value can still arrive as "fas fa-box" or
        bare "box" — from a row created before the picker, a fixture, or a
        hand-made POST. Templates write ``class="fas {{ icon }}"``, so
        "fas fa-box" renders as ``fas fas fa-box`` and "box" renders as nothing;
        both read as a broken screen rather than a bad input.

        Anything still outside the palette falls back to the default instead of
        raising: the field is optional, the picker always supplies a value, and
        failing someone's save over a decorative choice is the wrong trade.
        """
        icon = (self.cleaned_data.get("icon") or "").strip().lower()
        if not icon:
            return DEFAULT_CAPABILITY_ICON
        for prefix in ("fa-solid ", "fa-regular ", "fas ", "far ", "fab "):
            icon = icon.replace(prefix, "")
        icon = icon.strip()
        if icon and not icon.startswith("fa-"):
            icon = f"fa-{icon}"
        return icon if icon in CAPABILITY_ICON_CHOICES else DEFAULT_CAPABILITY_ICON

    # ── derived ──────────────────────────────────────────────────────────────
    @staticmethod
    def _code_from_label(label):
        """A stable snake_case identifier for a human label.

        Underscores rather than slugify's hyphens because the nine original
        codes are the old model field names (``gdp_certification``) and every
        code should look like one kind of thing. Collisions get a numeric tail
        rather than failing the save — two capabilities may legitimately reduce
        to the same slug.
        """
        base = slugify(label).replace("-", "_").strip("_") or "capability"
        code, suffix = base[:60], 2
        while Capability.objects.filter(code=code).exists():
            tail = f"_{suffix}"
            code = f"{base[: 60 - len(tail)]}{tail}"
            suffix += 1
        return code

    def save(self, commit=True):
        capability = super().save(commit=False)
        if not capability.pk:
            # Set once, at creation. Renaming the label later must NOT move the
            # code: saved filter links and circulating Excel columns carry it.
            capability.code = self._code_from_label(self.cleaned_data["label"])
            last = (
                Capability.objects.order_by("-sort_order")
                .values_list("sort_order", flat=True).first()
            )
            capability.sort_order = (last or 0) + 10
        if commit:
            capability.save()
        return capability
