import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

_WAREHOUSE_LOCATION_LABEL = "Warehouse Location"
_REGULATORY_SUPPORT_LABEL = "Regulatory Support"


class CapabilityHolderMixin:
    """Read helpers shared by anything holding a ``capabilities`` M2M.

    Both :class:`Vendor` and :class:`VendorRegistration` carry the same link to
    the :class:`Capability` master, and every screen asks the same two questions
    of them: "which capabilities does this hold" and "does it hold this one".

    ``has_capability`` exists because the code it replaces was
    ``getattr(vendor, attr, False)`` — which, once the boolean columns went
    away, would have kept working and quietly answered False for every vendor.
    A wrong answer with no error is the worst outcome available here (it drives
    the AI evaluator's services_provided list and the bid comparison matrix), so
    the lookup is explicit and the codes come from the master.
    """

    @property
    def capability_codes(self):
        """Set of codes this record declares.

        Cached on the instance: the matrix and comparison screens ask per
        vendor per capability, which is one query each without it. Callers
        iterating many records should still ``prefetch_related("capabilities")``
        — the cache saves the repeat, not the first hit.
        """
        cached = self.__dict__.get("_capability_codes")
        if cached is None:
            cached = {c.code for c in self.capabilities.all()}
            self.__dict__["_capability_codes"] = cached
        return cached

    def has_capability(self, code):
        """Whether this record declares ``code``."""
        return code in self.capability_codes

    @property
    def capability_labels(self):
        """Labels of the declared capabilities, in master sort order."""
        return [c.label for c in self.capabilities.all()]

    @property
    def capability_count(self):
        """How many capabilities this record declares."""
        return len(self.capability_codes)


class Capability(models.Model):
    """One capability dimension a vendor can hold — an admin-managed master row.

    These nine started life as nine ``BooleanField``s on :class:`Vendor` and a
    hardcoded ``CAPABILITY_FIELDS`` list in ``constants.py``. That worked for
    exactly as long as the set never changed: adding a tenth dimension meant a
    model field, a migration, an edit to the constant, and hand-edits to seven
    templates that named the nine one by one. Which is not something the SCM team
    can do, so in practice the "master" could not be added to at all.

    Now the row is the master. ``code`` keeps the old field names as its values
    (``gdp_certification`` and the rest), so URLs, saved filters and the Excel
    exports that already circulate keep meaning the same thing after the switch.

    Deactivating rather than deleting is the intended retirement path:
    :class:`Vendor` links are the historical record of what a vendor declared,
    and deleting the master row would erase that instead of retiring it.
    """

    code = models.SlugField(
        max_length=60, unique=True,
        help_text="Stable identifier used in URLs, filters and exports. "
                  "Set once when the capability is created — changing it "
                  "invalidates saved filter links.",
    )
    label = models.CharField(max_length=120, help_text="Shown on every screen.")
    icon = models.CharField(
        max_length=60, blank=True, default="fa-circle-check",
        help_text="Font Awesome class, e.g. 'fa-certificate'.",
    )
    description = models.CharField(
        max_length=300, blank=True,
        help_text="Optional one-line note shown on the capability master.",
    )
    #: Sort position on every capability list, matrix column order included.
    #: Sparse by default (10, 20, 30…) so a new row can be slotted between two
    #: existing ones without renumbering the rest.
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive capabilities disappear from forms and filters but "
                  "stay on the vendors that already declared them.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "label"]
        verbose_name = "Capability"
        verbose_name_plural = "Capabilities"

    def __str__(self):
        return self.label

    @property
    def vendor_count(self):
        """Active vendors that declare this capability."""
        return self.vendors.filter(is_active=True).count()


class Vendor(CapabilityHolderMixin, models.Model):
    """Central repository record for an approved 3PL vendor.

    Maps to the 18-field Vendor Master data dictionary from the SOW:
    identity + contact fields plus nine Yes/No capability flags.
    """

    # ── Identity & location ────────────────────────────────────────────
    vendor_name = models.CharField(max_length=200, verbose_name="Vendor Name")
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    warehouse_location = models.CharField(max_length=300, blank=True, verbose_name=_WAREHOUSE_LOCATION_LABEL)

    # ── Contact ────────────────────────────────────────────────────────
    contact_person = models.CharField(max_length=150, blank=True, verbose_name="Contact Person")
    email = models.EmailField(blank=True)
    mobile = models.CharField(max_length=40, blank=True)
    website = models.URLField(blank=True)

    # ── Capabilities ───────────────────────────────────────────────────
    # Was nine BooleanFields; now links to the Capability master so the set can
    # be extended without a migration. See Capability's docstring.
    capabilities = models.ManyToManyField(
        "Capability", blank=True, related_name="vendors", verbose_name="Capabilities",
    )

    # ── Meta / audit ───────────────────────────────────────────────────
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendors_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vendor_name"]
        verbose_name = "Vendor"
        verbose_name_plural = "Vendors"
        indexes = [
            models.Index(fields=["country"]),
            models.Index(fields=["vendor_name"]),
        ]

    def save(self, *args, **kwargs):
        """Canonicalise the country on the way in.

        The add/edit form is a strict select off the Country master, so it could
        never produce a variant spelling — but spreadsheet imports, shell edits
        and the years of free-text entry before that select existed all could,
        and did. Normalising here means the column stops accumulating new
        duplicates whatever the write path, instead of every reader having to
        remember to fold them back together.

        Existing rows are untouched by this; ``normalise_vendor_countries``
        cleans those, deliberately as a command someone runs rather than a
        silent rewrite of live data.
        """
        from .utils import canonical_country

        self.country = canonical_country(self.country)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.vendor_name

    @property
    def current_commercial(self):
        """The rate card in force today, or the most recent one if none is.

        A vendor can hold several rate cards for different contract periods, so
        "the vendor's pricing" is ambiguous without a rule. The rule: prefer a
        card whose contract covers today; failing that, show the newest, so a
        vendor whose contract has lapsed still displays its last known rates
        rather than nothing at all.
        """
        cards = list(self.commercials.all())
        if not cards:
            return None
        today = timezone.localdate()
        live = [
            c for c in cards
            if (c.contract_start is None or c.contract_start <= today)
            and (c.contract_end is None or c.contract_end >= today)
        ]
        pool = live or cards
        return max(pool, key=lambda c: (c.contract_start or c.created_at.date(), c.pk))

    @property
    def current_scorecard(self):
        """The most recent Vendor Scorecard entry, or None if never scored."""
        return self.scorecards.order_by("-scored_at").first()

    @property
    def latest_kpi_record(self):
        """The most recent month's KPI reading, or None if never recorded."""
        return self.kpi_records.order_by("-period").first()


class CountrySearchEvent(models.Model):
    """One row per country search, used to drive the Top-3 most-searched widget."""

    country = models.CharField(max_length=100)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["country"])]

    def __str__(self):
        return f"{self.country} @ {self.created_at:%Y-%m-%d %H:%M}"


class VendorRequest(models.Model):
    """A structured RFI/request raised by a user and broadcast by email to all
    active vendors of a chosen country.

    Captures a full enquiry — intro, address/header, warehouse & operational
    requirements (as ranges), scope of work, product verticals, and a closing
    footer — plus any number of file attachments. A snapshot of recipients is
    stored so history stays accurate even if vendor emails later change.
    """

    STATUS_SENT = "sent"
    STATUS_NO_RECIPIENTS = "no_recipients"
    STATUS_CHOICES = [
        (STATUS_SENT, "Sent"),
        (STATUS_NO_RECIPIENTS, "No recipients"),
    ]

    subject = models.CharField(max_length=200)
    country = models.CharField(max_length=100)

    # Rich-text (HTML) body sections.
    intro_message = models.TextField(blank=True, verbose_name="Intro message")
    header_message = models.TextField(blank=True, verbose_name="Header / address")
    scope_of_work = models.TextField(blank=True, verbose_name="Scope of work")
    footer_message = models.TextField(blank=True, verbose_name="Footer message")

    # Plain detail fields.
    product_verticals = models.CharField(max_length=300, blank=True)

    # Range fields (stored as text so a free 'from–to' can be entered).
    warehouse_space_from = models.CharField(max_length=50, blank=True, verbose_name="Warehouse space from (sqm)")
    warehouse_space_to = models.CharField(max_length=50, blank=True, verbose_name="Warehouse space to (sqm)")
    storage_temp_from = models.CharField(max_length=50, blank=True, verbose_name="Storage temp from (°C)")
    storage_temp_to = models.CharField(max_length=50, blank=True, verbose_name="Storage temp to (°C)")
    monthly_shipment_from = models.CharField(max_length=50, blank=True)
    monthly_shipment_to = models.CharField(max_length=50, blank=True)
    inbound_from = models.CharField(max_length=50, blank=True)
    inbound_to = models.CharField(max_length=50, blank=True)
    outbound_from = models.CharField(max_length=50, blank=True)
    outbound_to = models.CharField(max_length=50, blank=True)

    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendor_requests",
    )
    recipients = models.TextField(blank=True)
    recipient_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SENT)
    created_at = models.DateTimeField(auto_now_add=True)

    # ── Bid window ────────────────────────────────────────────────
    bid_open_at = models.DateTimeField(null=True, blank=True, verbose_name="Bid opens at")
    bid_close_at = models.DateTimeField(null=True, blank=True, verbose_name="Bid closes at")

    # ── Reference number ─────────────────────────────────────────────
    #: RFI-<year>-<pk padded to 6>, e.g. "RFI-2026-000042". Generated once, in
    #: save() below, so it exists no matter how the row is created (the
    #: request form, the admin, a fixture, a test) rather than depending on one
    #: view remembering to set it. Searchable via request_list's `q` filter and
    #: Bid Analytics' filter box.
    reference_id = models.CharField(max_length=40, unique=True, blank=True, editable=False)

    # ── Nominating a winner ──────────────────────────────────────────
    #: Null means nominations are open. Deliberately independent of the bid
    #: SUBMISSION window (bid_open_at/bid_close_at) above: those govern when
    #: vendors may still send in a quote, not when staff may act on the quotes
    #: already in hand. A request can sit past its bid deadline for weeks while
    #: evaluation continues, and awarding is exactly the decision that happens
    #: in that stretch -- gating it on the submission window would block the
    #: normal case. Nominations stay open until a person on this side closes
    #: them, full stop.
    nomination_closed_at = models.DateTimeField(null=True, blank=True)
    nomination_closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Vendor Request"
        verbose_name_plural = "Vendor Requests"

    def __str__(self):
        return f"{self.subject} → {self.country} ({self.recipient_count} vendors)"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.reference_id:
            year = self.created_at.year if self.created_at else timezone.now().year
            self.reference_id = f"RFI-{year}-{self.pk:06d}"
            super().save(update_fields=["reference_id"])

    @property
    def nominations_open(self):
        """Whether a vendor can still be awarded on this request."""
        return self.nomination_closed_at is None

    @property
    def recipient_list(self):
        return [e for e in (self.recipients or "").split(",") if e]

    @staticmethod
    def _fmt_range(frm, to, unit=""):
        frm, to = (frm or "").strip(), (to or "").strip()
        if not frm and not to:
            return ""
        u = f" {unit}" if unit else ""
        if frm and to:
            return f"{frm} – {to}{u}"
        return f"{frm or to}{u}"

    @property
    def bid_window_status(self):
        """Returns 'open', 'not_started', or 'closed' based on bid_open_at/bid_close_at."""
        from django.utils import timezone
        now = timezone.now()
        if self.bid_close_at and now > self.bid_close_at:
            return "closed"
        if self.bid_open_at and now < self.bid_open_at:
            return "not_started"
        return "open"

    @property
    def is_bid_open(self):
        return self.bid_window_status == "open"

    @property
    def warehouse_space_range(self):
        return self._fmt_range(self.warehouse_space_from, self.warehouse_space_to, "sqm")

    @property
    def storage_temp_range(self):
        return self._fmt_range(self.storage_temp_from, self.storage_temp_to, "°C")

    @property
    def monthly_shipment_range(self):
        return self._fmt_range(self.monthly_shipment_from, self.monthly_shipment_to)

    @property
    def inbound_range(self):
        return self._fmt_range(self.inbound_from, self.inbound_to)

    @property
    def outbound_range(self):
        return self._fmt_range(self.outbound_from, self.outbound_to)


class VendorRegistration(CapabilityHolderMixin, models.Model):
    """Self-registration submitted by an external vendor via the public form.

    Sits in 'pending' until a VendorLink admin approves it, at which point
    a Vendor master record is created and the vendor is notified.
    """

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    # ── Company / contact details ──────────────────────────────────
    vendor_name = models.CharField(max_length=200, verbose_name="Company Name")
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    warehouse_location = models.CharField(max_length=300, blank=True, verbose_name=_WAREHOUSE_LOCATION_LABEL)
    contact_person = models.CharField(max_length=150, verbose_name="Contact Person")
    email = models.EmailField()
    mobile = models.CharField(max_length=40, blank=True)
    website = models.URLField(blank=True)

    # ── Self-declared capabilities ─────────────────────────────────
    # Same Capability master the Vendor record links to, so a capability added
    # by an admin shows up on the public registration form with no code change
    # — which is half the point of having the master.
    capabilities = models.ManyToManyField(
        "Capability", blank=True, related_name="registrations", verbose_name="Capabilities",
    )

    # ── Workflow ───────────────────────────────────────────────────
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="registrations_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    # Set on approval — links to the created Vendor master record.
    vendor = models.OneToOneField(
        Vendor, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="registration",
    )

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Vendor Registration"
        verbose_name_plural = "Vendor Registrations"

    def __str__(self):
        return f"{self.vendor_name} ({self.get_status_display()})"


def request_attachment_path(instance, filename):
    return f"vendorlink/requests/{filename}"


class VendorRequestAttachment(models.Model):
    """A file attached to a VendorRequest (pdf / word / excel / image)."""

    request = models.ForeignKey(VendorRequest, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=request_attachment_path)
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_name or self.file.name


class VendorBid(models.Model):
    """A vendor's price bid submitted in response to a VendorRequest."""

    STATUS_SUBMITTED = "submitted"
    STATUS_SHORTLISTED = "shortlisted"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_SHORTLISTED, "Shortlisted"),
        (STATUS_REJECTED, "Rejected"),
    ]

    CURRENCY_CHOICES = [
        ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP"), ("INR", "INR"),
        ("AED", "AED"), ("SGD", "SGD"), ("JPY", "JPY"), ("CNY", "CNY"),
        ("AUD", "AUD"), ("CAD", "CAD"), ("CHF", "CHF"), ("SAR", "SAR"),
    ]

    vendor_request = models.ForeignKey(
        VendorRequest, on_delete=models.CASCADE, related_name="bids"
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vendor_bids"
    )
    vendor_registration = models.ForeignKey(
        "VendorRegistration", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="bids"
    )
    currency = models.CharField(max_length=10, choices=CURRENCY_CHOICES, default="USD")
    storage_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    inbound_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    outbound_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    pick_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    packing_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    last_mile_charges = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    manpower_charges = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    additional_charges = models.TextField(blank=True)
    notes = models.TextField(blank=True, verbose_name="Message / Notes")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    bid_number = models.PositiveIntegerField(default=1, verbose_name="Bid #")
    is_selected = models.BooleanField(
        default=False,
        verbose_name="Selected as best bid",
        help_text="Staff-chosen bid to represent this vendor when comparing across the request.",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Vendor Bid"

    def __str__(self):
        name = self.submitted_by.get_full_name() or self.submitted_by.username
        return f"Bid by {name} on {self.vendor_request.subject}"


class VendorScore(models.Model):
    """Reputation and facility quality score for a vendor — used by the AI evaluator.
    Admins set these scores based on past performance, audits, and feedback.
    """
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    vendor = models.OneToOneField(
        Vendor, on_delete=models.CASCADE, related_name="score"
    )
    facility_rating = models.PositiveSmallIntegerField(
        choices=RATING_CHOICES, default=3,
        verbose_name="Facility Rating (1-5)",
        help_text="Warehouse quality, equipment, storage conditions.",
    )
    support_rating = models.PositiveSmallIntegerField(
        choices=RATING_CHOICES, default=3,
        verbose_name="Support Rating (1-5)",
        help_text="Responsiveness, communication, problem resolution.",
    )
    compliance_rating = models.PositiveSmallIntegerField(
        choices=RATING_CHOICES, default=3,
        verbose_name="Compliance Rating (1-5)",
        help_text="GDP/ISO adherence, documentation, audit readiness.",
    )
    delivery_reliability = models.PositiveSmallIntegerField(
        choices=RATING_CHOICES, default=3,
        verbose_name="Delivery Reliability (1-5)",
        help_text="On-time delivery and order accuracy.",
    )
    is_blacklisted = models.BooleanField(
        default=False,
        verbose_name="Blacklisted",
        help_text="Blacklisted vendors are excluded from AI evaluation.",
    )
    blacklist_reason = models.TextField(blank=True, verbose_name="Blacklist Reason")
    notes = models.TextField(
        blank=True,
        help_text="Additional context for the AI evaluator — past incidents, strengths, concerns.",
    )
    scored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendor_scores_given",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Score"
        verbose_name_plural = "Vendor Scores"

    def __str__(self):
        return f"{self.vendor.vendor_name} — Score"

    @property
    def overall_rating(self):
        return round(
            (self.facility_rating + self.support_rating +
             self.compliance_rating + self.delivery_reliability) / 4, 1
        )

    @property
    def is_eligible(self):
        """Vendor is eligible for AI evaluation if not blacklisted and average ≥ 2."""
        return not self.is_blacklisted and self.overall_rating >= 2.0


class VendorSearchEvent(models.Model):
    """One row per Commercial Database vendor search.

    Drives the "Top 3 most-searched vendors" widget the Phase 2 plan asks for.
    Deliberately a log of events rather than a counter on Vendor: the widget is
    meant to reflect what the team has been looking at *recently*, and a plain
    counter can only ever say "since the beginning of time".

    Mirrors :class:`CountrySearchEvent`, which does the same job for the Vendor
    Master country filter.
    """

    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE, related_name="commercial_searches",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["vendor", "created_at"])]
        verbose_name = "Commercial Search Event"
        verbose_name_plural = "Commercial Search Events"

    def __str__(self):
        return f"{self.vendor.vendor_name} @ {self.created_at:%Y-%m-%d %H:%M}"


class VendorCommercial(models.Model):
    """A vendor's commercial rate card — the Phase 2 Commercial Database record.

    Holds the 13 fields from the SOW's "Commercial Database" sheet: eight cost
    lines, free-text additional charges, contract duration, price validity and
    insurance, plus the currency they are all quoted in.

    One vendor may have several rate cards (the plan calls for "different
    contract periods"), so this is a plain ForeignKey rather than a OneToOne.
    :meth:`Vendor.current_commercial` picks the one that is live today.

    Not to be confused with :class:`VendorBid`, which is a vendor's quote against
    one specific :class:`VendorRequest`. A bid answers "what would you charge for
    this job"; a rate card is the standing agreement.
    """

    #: Same list as VendorBid so a bid can be compared against a rate card
    #: without a currency-mapping step in between.
    CURRENCY_CHOICES = VendorBid.CURRENCY_CHOICES

    #: A rate card inside this many days of its price validity or contract end
    #: is flagged in the UI. 60 days is a purchasing-cycle's notice — long
    #: enough to renegotiate, short enough that the list is not permanently amber.
    EXPIRY_WARNING_DAYS = 60

    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE, related_name="commercials",
        verbose_name="Vendor",
    )
    currency = models.CharField(max_length=10, choices=CURRENCY_CHOICES, default="USD")

    # ── The eight numeric cost lines ───────────────────────────────────
    storage_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Storage Cost")
    inbound_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Inbound Cost")
    outbound_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Outbound Cost")
    pick_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Pick Cost")
    packing_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Packing Cost")
    customs_clearance_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Customs Clearance Cost")
    last_mile_charges = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Last Mile Charges")
    manpower_charges = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Manpower Charges")

    # ── Terms ──────────────────────────────────────────────────────────
    additional_charges = models.TextField(
        blank=True, verbose_name="Additional Charges",
        help_text="Anything not covered by the cost lines above — fuel surcharges, "
                  "after-hours rates, minimum billing.",
    )
    contract_start = models.DateField(
        null=True, blank=True, verbose_name="Contract Start",
        help_text="First day the contract runs.",
    )
    contract_end = models.DateField(
        null=True, blank=True, verbose_name="Contract End",
        help_text="Last day the contract runs. Used for expiry warnings.",
    )
    price_validity = models.DateField(
        null=True, blank=True, verbose_name="Price Validity",
        help_text="Date these rates are guaranteed until.",
    )
    insurance = models.CharField(
        max_length=300, blank=True, verbose_name="Insurance",
        help_text="Cover type and limit — e.g. 'All-risk, EUR 2m in warehouse / EUR 500k in transit'.",
    )
    notes = models.TextField(blank=True, verbose_name="Internal Notes")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendor_commercials_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vendor__vendor_name", "-contract_start", "-created_at"]
        verbose_name = "Commercial Rate Card"
        verbose_name_plural = "Commercial Rate Cards"
        indexes = [
            models.Index(fields=["vendor", "-contract_start"]),
            models.Index(fields=["price_validity"]),
            models.Index(fields=["contract_end"]),
        ]

    def __str__(self):
        return f"{self.vendor.vendor_name} — rate card ({self.currency})"

    # ── Derived values the list and detail screens read ────────────────
    @property
    def cost_fields(self):
        """The eight cost lines as (label, value) pairs, in SOW order.

        Read straight off the model's own field definitions so a renamed or
        added cost line shows up on every screen without editing each template.
        """
        names = (
            "storage_cost", "inbound_cost", "outbound_cost", "pick_cost",
            "packing_cost", "customs_clearance_cost", "last_mile_charges",
            "manpower_charges",
        )
        return [
            (self._meta.get_field(n).verbose_name, getattr(self, n))
            for n in names
        ]

    @property
    def total_cost(self):
        """Sum of whatever cost lines are filled in.

        A rough comparison figure only: the lines are quoted in different units
        (per pallet, per shipment, per hour), so this is a sortable indicator,
        not a real bill. Blank lines are skipped rather than counted as zero, so
        a half-filled card is not made to look cheap.
        """
        values = [value for _, value in self.cost_fields if value is not None]
        return sum(values) if values else None

    @property
    def filled_cost_count(self):
        return sum(1 for _, value in self.cost_fields if value is not None)

    @property
    def contract_duration(self):
        """Human-readable contract span, from the two dates.

        Stored as start/end rather than a free-text "12 months" so expiry
        tracking is possible at all — you cannot compare a string to today.
        """
        if self.contract_start and self.contract_end:
            months = round((self.contract_end - self.contract_start).days / 30.44)
            if months:
                span = f"{months} month{'' if months == 1 else 's'}"
            else:
                span = "under a month"
            return f"{self.contract_start:%d %b %Y} → {self.contract_end:%d %b %Y} ({span})"
        if self.contract_end:
            return f"until {self.contract_end:%d %b %Y}"
        if self.contract_start:
            return f"from {self.contract_start:%d %b %Y}"
        return ""

    @property
    def expiry_date(self):
        """The earlier of price validity and contract end — whichever bites first."""
        dates = [d for d in (self.price_validity, self.contract_end) if d]
        return min(dates) if dates else None

    @property
    def days_to_expiry(self):
        expiry = self.expiry_date
        return (expiry - timezone.localdate()).days if expiry else None

    @property
    def is_expired(self):
        days = self.days_to_expiry
        return days is not None and days < 0

    @property
    def is_expiring_soon(self):
        """Within the warning window but not yet past it.

        Expired cards are excluded on purpose so the UI can style the two
        differently — "renew this soon" and "this is no longer valid" call for
        different colours and different urgency.
        """
        days = self.days_to_expiry
        return days is not None and 0 <= days <= self.EXPIRY_WARNING_DAYS

    @property
    def expiry_state(self):
        """One of ``expired`` / ``soon`` / ``ok`` / ``none`` for template styling."""
        if self.expiry_date is None:
            return "none"
        if self.is_expired:
            return "expired"
        return "soon" if self.is_expiring_soon else "ok"


class VendorEvaluation(models.Model):
    """AI evaluation result produced by the OpenAI bid evaluator for a vendor request."""

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    vendor_request = models.OneToOneField(
        VendorRequest, on_delete=models.CASCADE, related_name="evaluation"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="evaluations_triggered",
    )
    triggered_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    recommended_vendor_name = models.CharField(max_length=200, blank=True)
    reasoning = models.TextField(blank=True)
    ranked_vendors_json = models.TextField(blank=True)  # JSON list
    required_services_json = models.TextField(blank=True)  # JSON list of service labels
    decision_points_json = models.TextField(blank=True)  # JSON list of bullet points
    model_used = models.CharField(max_length=60, blank=True, verbose_name="Model used")
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-triggered_at"]
        verbose_name = "Vendor Evaluation"
        verbose_name_plural = "Vendor Evaluations"

    def __str__(self):
        return f"Evaluation for {self.vendor_request.subject} ({self.get_status_display()})"

    @staticmethod
    def _load_json_list(raw):
        import json as _j
        try:
            val = _j.loads(raw) if raw else []
            return val if isinstance(val, list) else []
        except Exception:
            return []

    @property
    def ranked_vendors(self):
        return self._load_json_list(self.ranked_vendors_json)

    @property
    def required_services(self):
        return self._load_json_list(self.required_services_json)

    @property
    def decision_points(self):
        return self._load_json_list(self.decision_points_json)

    #: How long a "processing" row is trusted before being treated as stuck.
    #: The evaluator normally finishes in single-digit seconds — this only
    #: fires if the background thread died mid-flight (e.g. the app server
    #: was restarted while an evaluation was in progress), which otherwise
    #: leaves the request permanently showing "Evaluating…" with no way for
    #: the page to recover on its own.
    STALE_AFTER = timedelta(minutes=3)

    @property
    def is_stale(self):
        return (
            self.status == self.STATUS_PROCESSING
            and timezone.now() - self.triggered_at > self.STALE_AFTER
        )

    def mark_stale_if_needed(self):
        """If stuck in "processing" past STALE_AFTER, fail it so the page and
        the retry button recover instead of polling forever. Returns True if
        it changed anything."""
        if not self.is_stale:
            return False
        self.status = self.STATUS_FAILED
        self.error_message = (
            "The evaluation did not finish — the server was likely restarted while it "
            "was running. Click AI Evaluate to try again."
        )
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "error_message", "completed_at"])
        return True


class CapabilityAssessment(models.Model):
    """Phase 3 — 3PL Capability Assessment.

    An external vendor's self-assessment against the SOW's 6-section, 34-field
    "3PL Capability Assessment Form". Filled by the vendor over a token-based
    link (no account needed), then reviewed and approved/rejected by a
    VendorLink admin, who may promote an approved assessment into a Vendor
    Master record.

    The 34 content fields below are intentionally free text everywhere the
    spreadsheet's own "Guidelines to Fill" reads as a description rather than a
    fixed value (e.g. "Describe your returns handling process") — this is a
    self-assessment, not a structured survey, and constraining it to choices
    would lose exactly the qualitative detail Global SCM asked for.
    """

    STATUS_INVITED = "invited"
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_INVITED, "Invited"),
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]
    #: Statuses in which the vendor's link still opens the fillable form.
    OPEN_STATUSES = {STATUS_INVITED, STATUS_DRAFT}

    # ── Invite / access ─────────────────────────────────────────────────
    vendor_email = models.EmailField(db_index=True)
    token = models.CharField(max_length=64, unique=True, editable=False)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assessments_invited",
    )

    # ── Status / review ───────────────────────────────────────────────────
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_INVITED, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=150, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    # ── Promotion ─────────────────────────────────────────────────────────
    vendor = models.ForeignKey(
        Vendor, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="capability_assessments",
    )

    # ── Section 1: Company & Network ───────────────────────────────────────
    company_name = models.CharField(max_length=200, blank=True, verbose_name="Company Name")
    warehouse_location_desc = models.TextField(blank=True, verbose_name=_WAREHOUSE_LOCATION_LABEL)
    medtech_experience = models.TextField(blank=True, verbose_name="MedTech Experience")
    client_base = models.TextField(blank=True, verbose_name="Client Base")
    last_mile_delivery_desc = models.TextField(blank=True, verbose_name="Last-Mile Delivery")
    cha_services = models.TextField(blank=True, verbose_name="CHA Services")
    wms_name = models.CharField(max_length=200, blank=True, verbose_name="WMS Name & Version")

    # ── Section 2: Operational Capability ──────────────────────────────────
    warehouse_size_sqm = models.CharField(max_length=100, blank=True, verbose_name="Warehouse Size (sq. m)")
    operating_hours = models.CharField(max_length=200, blank=True, verbose_name="Operating Hours")
    order_cutoff_time = models.CharField(max_length=100, blank=True, verbose_name="Order Cut-off Time")
    warehouse_cutoff_time = models.CharField(max_length=100, blank=True, verbose_name="Warehouse Dispatch Cut-off")
    emergency_delivery_cutoff = models.TextField(blank=True, verbose_name="Emergency Delivery Cut-off")
    returns_handling = models.TextField(blank=True, verbose_name="Returns Handling")
    temperature_controlled_storage = models.TextField(blank=True, verbose_name="Temperature-Controlled Storage")
    consignment_handling = models.TextField(blank=True, verbose_name="Consignment Handling")
    order_to_cash_service = models.TextField(blank=True, verbose_name="Order-to-Cash Service")
    batch_lot_tracking = models.TextField(blank=True, verbose_name="Batch / Lot Tracking")
    serial_number_tracking = models.TextField(blank=True, verbose_name="Serial Number Tracking")

    # ── Section 3: Compliance & Quality ────────────────────────────────────
    iso_9001_certified = models.TextField(blank=True, verbose_name="ISO 9001 Certification")
    iso_13485_experience = models.TextField(blank=True, verbose_name="ISO 13485 Experience")
    gdp_compliance = models.TextField(blank=True, verbose_name="GDP Compliance")
    regulatory_support_desc = models.TextField(blank=True, verbose_name=_REGULATORY_SUPPORT_LABEL)
    insurance_coverage = models.TextField(blank=True, verbose_name="Insurance Coverage")

    # ── Section 4: Technology & Integration ────────────────────────────────
    wms_platform = models.CharField(max_length=200, blank=True, verbose_name="WMS Platform")
    real_time_inventory_visibility = models.TextField(blank=True, verbose_name="Real-Time Inventory Visibility")
    dashboard_reporting = models.TextField(blank=True, verbose_name="Dashboard & Reporting")
    order_tracking_visibility = models.TextField(blank=True, verbose_name="Order Tracking Visibility")

    # ── Section 5: Strategic Fit ────────────────────────────────────────────
    why_choose_us = models.TextField(blank=True, verbose_name="Why Choose Us")
    cons = models.TextField(blank=True, verbose_name="Limitations / Cons")
    local_presence = models.TextField(blank=True, verbose_name="Local Presence")
    global_presence = models.TextField(blank=True, verbose_name="Global Presence")
    dedicated_account_manager = models.CharField(max_length=200, blank=True, verbose_name="Dedicated Account Manager")
    implementation_timeline = models.CharField(max_length=200, blank=True, verbose_name="Implementation Timeline")

    # ── Section 6: Additional Notes ─────────────────────────────────────────
    remarks = models.TextField(blank=True, verbose_name="Remarks")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "3PL Capability Assessment"
        verbose_name_plural = "3PL Capability Assessments"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["vendor_email"]),
        ]

    def __str__(self):
        return self.company_name or self.vendor_email

    @staticmethod
    def generate_token():
        return secrets.token_urlsafe(32)

    @property
    def is_token_expired(self):
        return bool(self.token_expires_at) and timezone.now() > self.token_expires_at

    @property
    def is_open_for_editing(self):
        """True while the vendor's link should still show the fillable form."""
        return self.status in self.OPEN_STATUSES and not self.is_token_expired

    @property
    def completion_percentage(self):
        from .constants import CAPABILITY_ASSESSMENT_FIELDS
        total = len(CAPABILITY_ASSESSMENT_FIELDS)
        filled = sum(1 for f in CAPABILITY_ASSESSMENT_FIELDS if str(getattr(self, f) or "").strip())
        return round(filled / total * 100) if total else 0

    @property
    def sections_for_display(self):
        """The 6 sections as (title, [(label, value), ...]) for the review screen."""
        from .constants import CAPABILITY_ASSESSMENT_SECTIONS
        return [
            (title, [(label, getattr(self, name)) for name, label, *_ in rows])
            for title, rows in CAPABILITY_ASSESSMENT_SECTIONS
        ]


class VendorKPIRecord(models.Model):
    """Phase 4 — one month's KPI reading for one vendor.

    The 8 metrics from the SOW's "KPI Dashboard" sheet. Entered by the Global
    SCM team, never by the vendor — the plan is explicit that this sheet "will
    be filled by global scm team while accessing all the Meril SCM vendors."
    Monthly is the storage grain; a quarterly view is a rollup computed over 3
    months' records rather than a second, differently-shaped row.
    """

    CURRENCY_CHOICES = VendorBid.CURRENCY_CHOICES

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="kpi_records")
    period = models.DateField(verbose_name="Period (month)")

    inventory_accuracy = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Inventory Accuracy (%)")
    on_time_delivery = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="On-Time Delivery (%)")
    order_fulfilment_accuracy = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Order Fulfilment Accuracy (%)")
    damage_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Damage Rate (%)")
    response_time_hours = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True, verbose_name="Response Time (hrs)")
    capa_closure_days = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True, verbose_name="CAPA Closure Time (days)")
    internal_complaints = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Internal Customer Complaints")
    cost_per_shipment = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Cost per Shipment")
    currency = models.CharField(max_length=10, choices=CURRENCY_CHOICES, default="USD")

    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="kpi_records_entered",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period"]
        verbose_name = "Vendor KPI Record"
        verbose_name_plural = "Vendor KPI Records"
        constraints = [
            models.UniqueConstraint(fields=["vendor", "period"], name="vlx_kpi_vendor_period_unique"),
        ]
        indexes = [
            models.Index(fields=["vendor", "period"]),
            models.Index(fields=["period"]),
        ]

    def __str__(self):
        return f"{self.vendor.vendor_name} — {self.period:%b %Y}"

    def save(self, *args, **kwargs):
        # Normalise to the first of the month so "any date in June" can never
        # silently create a second record for a month that already has one.
        if self.period:
            self.period = self.period.replace(day=1)
        super().save(*args, **kwargs)

    @property
    def metrics_for_display(self):
        from .constants import KPI_METRIC_FIELDS
        out = []
        for name, label, unit in KPI_METRIC_FIELDS:
            value = getattr(self, name)
            if name == "cost_per_shipment" and value is not None:
                unit = self.currency
            out.append((label, value, unit))
        return out

    @property
    def filled_metric_count(self):
        from .constants import KPI_METRIC_FIELDS
        return sum(1 for name, *_ in KPI_METRIC_FIELDS if getattr(self, name) is not None)


class VendorScorecard(models.Model):
    """Phase 4 — a single weighted scoring event for a vendor.

    Kept entirely separate from :class:`VendorScore` (which ``ai_evaluator.py``
    reads and must keep working unchanged) — this is the SOW's "Vendor
    Scorecard" sheet: Cost 20%, Serviceability 40%, WMS/Reports 20%, TAT 20%,
    each scored out of 10 with a mandatory comment per score.

    Immutable by design: a re-score creates a new row rather than editing an
    old one, so the scoring history for a vendor is exactly its list of rows —
    no separate audit log is needed to answer "what did we say last quarter".
    """

    SCORE_CHOICES = [(i, str(i)) for i in range(0, 11)]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="scorecards")

    cost_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name="Cost (20%)")
    cost_comment = models.TextField(verbose_name="Cost — comment")
    serviceability_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name="Serviceability (40%)")
    serviceability_comment = models.TextField(verbose_name="Serviceability — comment")
    wms_reports_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name="WMS / Reports (20%)")
    wms_reports_comment = models.TextField(verbose_name="WMS / Reports — comment")
    tat_score = models.PositiveSmallIntegerField(choices=SCORE_CHOICES, verbose_name="TAT (20%)")
    tat_comment = models.TextField(verbose_name="TAT — comment")

    notes = models.TextField(blank=True, verbose_name="Overall notes")
    scored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="scorecards_given",
    )
    scored_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scored_at"]
        verbose_name = "Vendor Scorecard"
        verbose_name_plural = "Vendor Scorecards"
        indexes = [
            models.Index(fields=["vendor", "scored_at"]),
        ]

    def __str__(self):
        return f"{self.vendor.vendor_name} — {self.final_score}/10 ({self.scored_at:%d %b %Y})"

    @staticmethod
    def weights():
        from .constants import SCORECARD_ATTRIBUTES
        return {name: weight / 100 for name, _label, weight in SCORECARD_ATTRIBUTES}

    @property
    def final_score(self):
        w = self.weights()
        total = (
            self.cost_score * w["cost"]
            + self.serviceability_score * w["serviceability"]
            + self.wms_reports_score * w["wms_reports"]
            + self.tat_score * w["tat"]
        )
        return round(total, 2)

    @property
    def attributes_for_display(self):
        return [
            ("Cost (20%)", self.cost_score, self.cost_comment),
            ("Serviceability (40%)", self.serviceability_score, self.serviceability_comment),
            ("WMS / Reports (20%)", self.wms_reports_score, self.wms_reports_comment),
            ("TAT (20%)", self.tat_score, self.tat_comment),
        ]


class GptConversation(models.Model):
    """One MerilGPT thread, so a user can return to what they asked before.

    Kept server-side rather than in the browser for two reasons: procurement
    reasoning is the kind of thing people are asked to justify later, and a
    thread that vanishes when someone clears their cache is not a record. It
    also means the same history follows a user between machines.

    Scoped to its owner — :func:`vendorlink.views.merilgpt_conversations` only
    ever lists ``request.user``'s own threads, since one buyer's line of
    questioning is not another's to read.

    Capped at :attr:`KEEP_RECENT` threads per user, with the remainder deleted
    by :meth:`prune_for` on every write. An uncapped table would grow forever,
    and each assistant turn can carry a whole report payload in
    ``GptMessage.report_json``.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gpt_conversations",
    )
    #: First question, trimmed — used as the label in the recent-chats list.
    title = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "MerilGPT Conversation"
        verbose_name_plural = "MerilGPT Conversations"
        indexes = [models.Index(fields=["user", "-updated_at"])]

    def __str__(self):
        return self.title or f"Conversation {self.pk}"

    #: How many threads a user keeps. Anything older is deleted, messages and
    #: all — this table grows without bound otherwise, and a report payload
    #: stored per assistant turn is not small.
    KEEP_RECENT = 15

    @staticmethod
    def title_from(question):
        text = " ".join((question or "").split())
        return text[:117] + "…" if len(text) > 120 else text

    @classmethod
    def prune_for(cls, user, keep=None):
        """Delete all but this user's ``keep`` most recently used threads.

        Returns the number deleted. Called after a turn is recorded — the only
        moment the count can grow — so the table is trimmed on write rather
        than by a job somebody has to remember to schedule.

        Ordered by ``updated_at``, not ``created_at``: returning to an old
        thread makes it recent again, and deleting the conversation a user is
        actively in would be the worst possible moment to reclaim space.

        The ids are resolved first and deleted by primary key. A delete against
        a sliced queryset is not allowed by Django, and re-evaluating the
        ordering inside the delete would race a concurrent turn in another tab.
        """
        keep = cls.KEEP_RECENT if keep is None else keep
        if not getattr(user, "is_authenticated", False):
            return 0
        doomed = list(
            cls.objects.filter(user=user)
            .order_by("-updated_at", "-pk")
            .values_list("pk", flat=True)[keep:]
        )
        if not doomed:
            return 0
        # GptMessage cascades, so the turns go with the thread.
        cls.objects.filter(pk__in=doomed).delete()
        return len(doomed)


class GptMessage(models.Model):
    """A single turn in a MerilGPT thread."""

    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [(ROLE_USER, "User"), (ROLE_ASSISTANT, "Assistant")]

    conversation = models.ForeignKey(
        GptConversation, on_delete=models.CASCADE, related_name="messages",
    )
    role = models.CharField(max_length=12, choices=ROLE_CHOICES)
    body = models.TextField(blank=True)
    #: The structured report payload, when the answer carried one. Stored as
    #: text rather than JSONField so the column behaves the same on SQLite (the
    #: test database) as on Postgres.
    report_json = models.TextField(blank=True)
    #: Names of any files the user attached to this turn — enough to explain
    #: an answer later without keeping the files themselves.
    attachments = models.CharField(max_length=500, blank=True)
    #: Which source answered this turn — portal / document / general / both.
    #:
    #: Stored, not recomputed, for two reasons. A reopened thread can show the
    #: same "answered from" chip it showed live, instead of losing it on
    #: reload. And the next turn's router can read it: a follow-up like "create
    #: a report with graphs included" names no subject at all, and without
    #: knowing what the last turn was about it drifts — on a real thread it
    #: answered about the attached quotation, then switched to the portal's bid
    #: data on the very next question.
    source = models.CharField(max_length=12, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "MerilGPT Message"
        verbose_name_plural = "MerilGPT Messages"

    def __str__(self):
        return f"{self.get_role_display()}: {self.body[:40]}"

    @property
    def report(self):
        import json as _j
        try:
            value = _j.loads(self.report_json) if self.report_json else None
        except ValueError:
            return None
        return value if isinstance(value, dict) else None


# ── Award: the outcome of a request, and the vendor's answer ────────────────
class BidAward(models.Model):
    """A request awarded to one vendor's bid.

    Deliberately NOT ``VendorBid.is_selected``. That flag marks which of a
    single vendor's revisions represents them when bids are compared, so
    several vendors on one request can each carry a selected bid — it can never
    mean "this vendor won". An award is the request's one outcome.

    The requester decides this unilaterally: nominating a vendor here is the
    decision, full stop, and the vendor is told as a courtesy rather than asked
    to confirm. There used to be a conversation — staff awarded it, the vendor
    accepted or declined over a tokened link in their mail, and only an
    acceptance counted as a win — but that made the outcome depend on the
    vendor's reply reaching them and on them acting on it, for a decision that
    was never really theirs to make. The requester now nominates and can
    withdraw and re-nominate freely; see ``vendorlink/awards.py``.

    ``vendor_email`` and ``vendor_name`` are snapshots rather than pure joins.
    The portal already identifies a vendor by the email on their login (see
    ``views.index``), and a VendorRegistration can be deleted or re-approved
    under a new row — a win total that silently dropped historical awards when
    that happened would be worse than no counter at all.
    """

    #: One award per request — the request has a single outcome. Re-awarding
    #: to a different vendor overwrites this row rather than adding a second.
    vendor_request = models.OneToOneField(
        VendorRequest, on_delete=models.CASCADE, related_name="award",
    )
    #: PROTECT, not CASCADE: deleting the winning bid out from under a live
    #: award would leave nothing behind an award that still names it.
    bid = models.ForeignKey(
        VendorBid, on_delete=models.PROTECT, related_name="awards",
    )
    vendor = models.ForeignKey(
        Vendor, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bid_awards",
    )
    vendor_registration = models.ForeignKey(
        "VendorRegistration", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bid_awards",
    )
    vendor_email = models.EmailField(verbose_name="Vendor email at award time")
    vendor_name = models.CharField(max_length=200, verbose_name="Vendor name at award time")

    #: The bid total as it stood when the award was made. Quoted back in the
    #: notice mail, so it must not move if the vendor later edits the bid.
    awarded_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    awarded_currency = models.CharField(max_length=10, blank=True)

    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bid_awards_made",
    )
    awarded_at = models.DateTimeField(auto_now_add=True)
    #: When the informational notice actually went out. Null means the send
    #: failed — the award screen says so rather than staying silent about a
    #: vendor who was never actually told.
    notified_at = models.DateTimeField(null=True, blank=True)
    notify_error = models.TextField(blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-awarded_at"]
        verbose_name = "Bid Award"
        indexes = [
            models.Index(fields=["vendor_email"], name="vl_award_email_idx"),
        ]

    def __str__(self):
        return f"{self.vendor_name} — {self.vendor_request.subject}"

    @classmethod
    def wins_for_email(cls, email):
        """How many requests are currently awarded to this email address.

        A live count, not a historical tally: withdrawing an award removes the
        row (see ``awards.award_withdraw``), so a vendor's number here always
        matches what they can see on their own portal right now.
        """
        if not email:
            return 0
        return cls.objects.filter(vendor_email__iexact=email.strip()).count()


class BidOutcomeNotice(models.Model):
    """One row per (request, vendor email) whose loop we have closed.

    Exists to answer one question before every send: have we already told this
    vendor how this request ended? Without it, a re-award, a second acceptance
    POST or a staff member re-running the notice would send the same regret
    letter again — and a vendor who is told twice that they lost reads it as
    either a system they cannot trust or a second, separate rejection.

    Keyed on the **request** and not on the BidAward. "We told this vendor they
    did not win this request" is a property of the request; the award row is
    overwritten on a re-award and deleted on a withdrawal, so keying there
    would lose the record exactly when it matters.

    ``KIND_DECLINED`` is a suppression, not a send. A vendor who declined their
    own award already knows the outcome, and posting them "we have selected
    another vendor on this occasion" a week later is tone deaf. Recording their
    decline here keeps the later sweep from writing to them at all.
    """

    KIND_REGRET = "regret"
    KIND_DECLINED = "declined"
    KIND_CHOICES = [
        (KIND_REGRET, "Told another vendor was selected"),
        (KIND_DECLINED, "Declined their own award — no letter owed"),
    ]

    vendor_request = models.ForeignKey(
        VendorRequest, on_delete=models.CASCADE, related_name="outcome_notices",
    )
    vendor_email = models.EmailField()
    vendor_name = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_REGRET)
    #: Null while a send is pending or has failed — the row is written before
    #: the mail leaves, so that a crash mid-sweep cannot double-send on retry.
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Bid Outcome Notice"
        constraints = [
            models.UniqueConstraint(
                fields=["vendor_request", "vendor_email"],
                name="vl_outcome_notice_once_per_vendor",
            ),
        ]

    def __str__(self):
        return f"{self.vendor_email} — {self.vendor_request.subject} ({self.kind})"


class FxRate(models.Model):
    """A stored base→INR exchange rate and when it was fetched.

    Stored rather than only cached because a converted figure has to be able to
    say what rate produced it. The same quote converted today and next month is
    a different number, and on a procurement record "₹34,20,000" with no
    attached rate or date is not a defensible figure.

    One row per base currency, overwritten on refresh — the history of rates is
    not what this is for; the currently-applied rate and its age is.
    """

    base = models.CharField(
        max_length=10, unique=True,
        verbose_name="Base currency",
        help_text="ISO code the rate converts FROM. The target is always INR.",
    )
    rate = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="Units of INR per 1 unit of the base currency.",
    )
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["base"]
        verbose_name = "FX rate"
        verbose_name_plural = "FX rates"

    def __str__(self):
        return f"1 {self.base} = {self.rate} INR"

    def is_stale(self, max_age_hours):
        """Older than the freshness window.

        A stale rate is still used in preference to showing nothing, but it is
        labelled — see vendorlink.fx.describe.
        """
        age = timezone.now() - self.fetched_at
        return age.total_seconds() > max_age_hours * 3600


class GptDocument(models.Model):
    """A document a user attached to a MerilGPT thread, kept for later turns.

    Before this, an attachment's extracted text went into one prompt and was
    thrown away — only the filename survived on the message. So the very next
    question in the same thread had no access to the document, and the model
    answered from the request payload alone while the panel still showed the
    file chip. That is the "it does not answer from the file" complaint: it
    could not, after the first turn.

    Kept per conversation rather than globally: a document someone attached to
    one line of questioning is not automatically relevant to another, and
    scoping retrieval to the thread is what stops an unrelated PDF leaking into
    an answer. Deleted with the conversation, which is already capped per user.
    """

    conversation = models.ForeignKey(
        GptConversation, on_delete=models.CASCADE, related_name="documents",
    )
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=12, default="text")
    size_bytes = models.PositiveIntegerField(default=0)
    #: The whole extracted text. Held alongside the chunks so a short document
    #: can be sent in full — retrieval on a two-page quotation only risks
    #: dropping half of it — and so re-chunking never needs the original file.
    text = models.TextField(blank=True)
    char_count = models.PositiveIntegerField(default=0)
    truncated = models.BooleanField(default=False)
    #: Whether embeddings were built. False means retrieval falls back to
    #: sending the text directly, which is the old behaviour and still correct
    #: — just less selective on a long document.
    indexed = models.BooleanField(default=False)
    index_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "MerilGPT Document"
        verbose_name_plural = "MerilGPT Documents"
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self):
        return f"{self.name} ({self.char_count} chars)"


class GptDocumentChunk(models.Model):
    """One passage of a document, with its embedding, for retrieval.

    The embedding is a JSON array of floats rather than a pgvector column:
    ``vector`` is not an available extension on this deployment (checked), and
    at the scale this runs at — a handful of documents per thread, a few
    hundred chunks — cosine similarity in NumPy over the thread's own chunks is
    a few milliseconds and needs no database extension. If pgvector is
    installed later, this column is what would migrate.
    """

    document = models.ForeignKey(
        GptDocument, on_delete=models.CASCADE, related_name="chunks",
    )
    #: Position in the document, so a retrieved passage can be cited as
    #: "page-ish section 3 of 11" and so neighbouring context can be pulled in.
    ordinal = models.PositiveIntegerField(default=0)
    text = models.TextField()
    embedding = models.TextField(blank=True)

    class Meta:
        ordering = ["document_id", "ordinal"]
        verbose_name = "MerilGPT Document Chunk"
        verbose_name_plural = "MerilGPT Document Chunks"
        indexes = [models.Index(fields=["document", "ordinal"])]

    def __str__(self):
        return f"{self.document_id}#{self.ordinal}"
