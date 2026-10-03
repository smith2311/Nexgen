"""Commercial Database — VendorLink Phase 2.

Rate cards: what each 3PL vendor charges, and on what contract terms. The SOW's
"Commercial Database" sheet defines 13 fields per card; :class:`VendorCommercial`
holds them and this module is the screen layer over it.

Kept out of ``views.py`` deliberately — that file is already ~2,000 lines
covering Vendor Master, requests, registration and bid analytics, and a fourth
concern bolted on would make it the place nobody wants to open.

Access follows the platform's three tiers (see ``rbac.py``):
    viewer  — list, detail, both exports
    editor  — the above, plus add and edit
    admin   — the above, plus delete
That split is the spec's, not an invention: "Admin can add new rate cards, edit
existing ones, and delete records. Editors can add and edit but not delete."
"""

from __future__ import annotations

import io
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .forms import VendorCommercialForm
from .models import Vendor, VendorBid, VendorCommercial, VendorSearchEvent
from .rbac import can_access_vendorlink, can_edit_vendorlink, is_vendorlink_admin
from .utils import (
    canonical_countries, canonical_country, continent_choices,
    continent_country_map_json, continent_of, filter_countries_by_continent,
)

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")
edit_required = user_passes_test(can_edit_vendorlink, login_url="vendorlink:commercial_list")
admin_required = user_passes_test(is_vendorlink_admin, login_url="vendorlink:commercial_list")

#: Only searches from the last quarter feed the "most searched" widget, so it
#: tracks what the team is working on now rather than a lifetime tally.
SEARCH_WINDOW_DAYS = 90

#: Column order for the Excel export — matches the SOW sheet exactly so the
#: export can be pasted straight back over the source spreadsheet. ``Source``
#: leads because a merged sheet whose rows do not say where they came from
#: invites someone to read a point-in-time RFI quote as a negotiated rate.
EXPORT_HEADERS = (
    "Source", "Vendor Name", "Country", "Currency", "Storage Cost", "Inbound Cost",
    "Outbound Cost", "Pick Cost", "Packing Cost", "Additional Charges",
    "Customs Clearance Cost", "Last Mile Charges", "Manpower Charges",
    "Contract Duration", "Price Validity", "Insurance", "Status",
)

HEADER_FILL = "0076C8"


def _ctx(request, **extra):
    """Shared context — the templates gate controls on these two flags."""
    base = {
        "is_vm_admin": is_vendorlink_admin(request.user),
        "can_edit": can_edit_vendorlink(request.user),
    }
    # Every screen with a country picker or country filter needs this, and the
    # ones that forgot it silently fell back to the flat 252-country list — the
    # cascade JS treats a missing map as "no narrowing". Put on the shared
    # context so a new country screen gets it by default rather than by
    # remembering.
    base["continent_choices"] = continent_choices()
    base["continent_country_map"] = continent_country_map_json()
    base.update(extra)
    return base


def _top_searched_vendors(limit=3):
    """The Top-3 most-searched vendors widget from the Phase 2 plan.

    Counts only vendors that still exist and still have a rate card — a shortcut
    chip that lands on an empty result would be worse than no chip.
    """
    since = timezone.now() - timedelta(days=SEARCH_WINDOW_DAYS)
    rows = (
        VendorSearchEvent.objects.filter(created_at__gte=since)
        .values("vendor__vendor_name")
        .annotate(searches=Count("id"))
        .order_by("-searches", "vendor__vendor_name")[: limit * 3]
    )
    # Counted over both sources, not just rate cards. With the RFI half now on
    # the screen, a vendor can be well represented here and still have no card,
    # and suppressing its chip would hide a shortcut that does lead somewhere.
    holdings = {}
    for entry in all_entries():
        name = (entry.vendor_name or "").casefold()
        if name:
            holdings[name] = holdings.get(name, 0) + 1

    out = []
    for row in rows:
        name = row["vendor__vendor_name"]
        cards = holdings.get((name or "").casefold(), 0)
        if cards:
            out.append({"name": name, "searches": row["searches"], "cards": cards})
        if len(out) == limit:
            break
    return out


def _record_search(request, query: str) -> None:
    """Log a search against every vendor whose name matched.

    Logged per matched vendor rather than per raw string so the widget's chips
    are real vendor names that can be clicked straight back into a filter —
    a log of typos would produce chips that match nothing.
    """
    if not query:
        return
    matched = Vendor.objects.filter(vendor_name__icontains=query).values_list("pk", flat=True)[:5]
    user = request.user if request.user.is_authenticated else None
    VendorSearchEvent.objects.bulk_create(
        [VendorSearchEvent(vendor_id=pk, user=user) for pk in matched]
    )


# ── One row, two sources ────────────────────────────────────────────────────
SOURCE_CARD = "card"
SOURCE_BID = "bid"

#: The eight SOW cost lines, in sheet order. A bid does not quote customs
#: clearance — the RFI form has no field for it — so that column reads blank on
#: a bid row rather than being faked as zero.
_COST_LINES = (
    ("storage_cost", "Storage"),
    ("inbound_cost", "Inbound"),
    ("outbound_cost", "Outbound"),
    ("pick_cost", "Pick"),
    ("packing_cost", "Packing"),
    ("customs_clearance_cost", "Customs Clearance"),
    ("last_mile_charges", "Last Mile"),
    ("manpower_charges", "Manpower"),
)


class CommercialEntry:
    """One row of the Commercial Database, from either of its two sources.

    The screen has only ever read :class:`VendorCommercial` — rate cards typed
    in by hand. But the same cost lines also arrive on their own through the RFI
    flow: every :class:`VendorBid` submitted against a request quotes storage,
    inbound, outbound, pick, packing, last-mile and manpower in a stated
    currency. Reading one half meant the Commercial Database could sit
    completely empty while the actual quoted pricing lived one module away,
    invisible to anyone trying to compare vendors.

    The two are wrapped rather than merged in the ORM on purpose. They are
    different things with different lifecycles: a rate card is negotiated and
    carries contract dates and insurance terms, while a bid is a point-in-time
    quote against one RFI and has neither. A union queryset would force one to
    impersonate the other; this wrapper lets each row stay honest about where it
    came from and about what it simply does not have — a bid's expiry pill reads
    "Not set" because a bid genuinely has no validity date, not because someone
    forgot to fill one in.
    """

    def __init__(self, obj, source):
        self.obj = obj
        self.source = source
        self.pk = obj.pk
        #: Unique across both tables — ``pk`` alone collides between a card and
        #: a bid that happen to share an id, which matters the moment the
        #: template uses it as a DOM id or a dict key.
        self.key = f"{source}-{obj.pk}"

    # ── factories ──────────────────────────────────────────────────────
    @classmethod
    def from_card(cls, card):
        return cls(card, SOURCE_CARD)

    @classmethod
    def from_bid(cls, bid):
        return cls(bid, SOURCE_BID)

    # ── what kind of row is this ───────────────────────────────────────
    @property
    def is_card(self):
        return self.source == SOURCE_CARD

    @property
    def is_bid(self):
        return self.source == SOURCE_BID

    @property
    def source_label(self):
        return "Rate Card" if self.is_card else "RFI Bid"

    @property
    def rfi(self):
        return self.obj.vendor_request if self.is_bid else None

    @property
    def source_detail(self):
        """The second line under the source pill — which RFI a bid answers."""
        if self.is_card:
            return ""
        rfi = self.rfi
        subject = (rfi.subject or "").strip() or "Untitled"
        return f"RFI #{rfi.pk} · {subject}"

    # ── identity ───────────────────────────────────────────────────────
    @property
    def vendor_name(self):
        if self.is_card:
            return self.obj.vendor.vendor_name
        registration = self.obj.vendor_registration
        if registration and registration.vendor_name:
            return registration.vendor_name
        # A bid can be submitted by a portal user whose registration row was
        # since deleted. Naming the submitter beats rendering a blank cell.
        submitter = self.obj.submitted_by
        return (submitter.get_full_name() or submitter.username) if submitter else "Unknown vendor"

    @property
    def country(self):
        if self.is_card:
            return self.obj.vendor.country
        registration = self.obj.vendor_registration
        if registration and registration.country:
            return registration.country
        # Fall back to the RFI's own country — the request was broadcast to one
        # country's vendors, so it is the right answer often enough to beat "—".
        return self.rfi.country if self.rfi else ""

    @property
    def currency(self):
        return self.obj.currency

    # ── the cost lines ─────────────────────────────────────────────────
    def cost(self, name):
        return getattr(self.obj, name, None)

    @property
    def cost_fields(self):
        return [(label, self.cost(name)) for name, label in _COST_LINES]

    @property
    def total_cost(self):
        values = [value for _, value in self.cost_fields if value is not None]
        return sum(values) if values else None

    @property
    def filled_cost_count(self):
        return sum(1 for _, value in self.cost_fields if value is not None)

    @property
    def additional_charges(self):
        return self.obj.additional_charges or ""

    # ── contract & expiry — cards only ─────────────────────────────────
    @property
    def contract_start(self):
        return self.obj.contract_start if self.is_card else None

    @property
    def contract_end(self):
        return self.obj.contract_end if self.is_card else None

    @property
    def price_validity(self):
        return self.obj.price_validity if self.is_card else None

    @property
    def contract_duration(self):
        return self.obj.contract_duration if self.is_card else ""

    @property
    def insurance(self):
        return self.obj.insurance if self.is_card else ""

    @property
    def expiry_date(self):
        return self.obj.expiry_date if self.is_card else None

    @property
    def days_to_expiry(self):
        return self.obj.days_to_expiry if self.is_card else None

    @property
    def expiry_state(self):
        """A bid has no dates at all, so it lands in the same "none" bucket a
        dateless rate card does — which keeps the four status filters total."""
        return self.obj.expiry_state if self.is_card else "none"

    # ── bid-only detail ────────────────────────────────────────────────
    @property
    def submitted_at(self):
        return None if self.is_card else self.obj.submitted_at

    @property
    def bid_number(self):
        return None if self.is_card else self.obj.bid_number

    @property
    def is_selected(self):
        return bool(getattr(self.obj, "is_selected", False)) if self.is_bid else False

    @property
    def bid_status(self):
        return self.obj.status if self.is_bid else ""

    @property
    def bid_status_label(self):
        return self.obj.get_status_display() if self.is_bid else ""

    # ── where the row links ────────────────────────────────────────────
    @property
    def detail_url(self):
        if self.is_card:
            return reverse("vendorlink:commercial_detail", args=[self.pk])
        return reverse("vendorlink:request_detail", args=[self.rfi.pk])


def _card_entries():
    return [
        CommercialEntry.from_card(card)
        for card in VendorCommercial.objects.select_related("vendor", "created_by")
    ]


def _bid_entries():
    """Every bid, newest first, as Commercial Database rows.

    Rejected bids are kept rather than filtered out. They are still real prices
    a vendor quoted, which is exactly what a commercial database is for; hiding
    them would quietly narrow the price history the screen exists to show. The
    row carries its own status pill instead, so a rejected quote is visible as
    a rejected quote rather than being passed off as live pricing.
    """
    bids = VendorBid.objects.select_related(
        "vendor_request", "vendor_registration", "submitted_by"
    ).order_by("-submitted_at")
    return [CommercialEntry.from_bid(bid) for bid in bids]


def all_entries():
    """Both sources as one list — the single definition of "what this screen holds"."""
    return _card_entries() + _bid_entries()


# ── Shared filtering ────────────────────────────────────────────────────────
#: Status filter value -> the row's own ``expiry_state``. Going through
#: expiry_state rather than hand-written SQL is what keeps the filters, the
#: KPI counts and the per-row pill telling the same story.
_STATUS_TO_EXPIRY_STATE = {
    "expired": "expired",
    "expiring": "soon",
    "active": "ok",
    "unset": "none",
}

#: Source filter value -> the entry source it selects.
_SOURCE_FILTERS = {"cards": SOURCE_CARD, "bids": SOURCE_BID}


def _matches_query(entry, query):
    """Free-text match over the fields a person would actually type.

    For a bid the RFI subject is searchable too — "cold chain" is how someone
    remembers a request, not by its id.
    """
    haystack = [entry.vendor_name, entry.country, entry.insurance]
    if entry.is_bid:
        rfi = entry.rfi
        haystack += [rfi.subject if rfi else "", entry.source_detail]
    needle = query.casefold()
    return any(needle in (value or "").casefold() for value in haystack)


def filtered_cards(request, record_search=False):
    """The Commercial Database rows matching q / status / currency / source.

    Shared by the list and the Excel export. They used to filter separately —
    the export read only ``q`` — so filtering by status or currency and then
    hitting Export Excel silently handed back every card, contradicting the
    export's own "export what I am looking at" promise. One code path cannot
    drift like that.

    Everything is filtered in Python over :func:`all_entries`, because the rows
    now come from two tables that cannot be filtered as one queryset. That was
    already half-true: status has always been applied in Python against
    ``expiry_state``, since the SQL version tested each date independently while
    the property is driven by ``expiry_date`` = the *earlier* of price_validity
    and contract_end. A card whose price lapsed last year but whose contract
    ends in 30 days therefore counted as Expired on the KPI tile and was *also*
    returned by ?status=expiring — two screens disagreeing about one card. There
    is no pagination and the tables are small, so there is nothing to gain by
    pushing any of it back into SQL and a real correctness bug to lose.

    The name stays ``filtered_cards`` so existing callers keep working; the rows
    it returns are :class:`CommercialEntry` wrappers rather than model instances.
    """
    rows = all_entries()

    source = request.GET.get("source", "").strip()
    wanted_source = _SOURCE_FILTERS.get(source)
    if wanted_source:
        rows = [row for row in rows if row.source == wanted_source]

    query = request.GET.get("q", "").strip()
    if query:
        rows = [row for row in rows if _matches_query(row, query)]
        if record_search:
            _record_search(request, query)

    currency = request.GET.get("currency", "").strip()
    if currency:
        rows = [row for row in rows if row.currency == currency]

    # ── Continent, then country ────────────────────────────────────────────
    # Two levels rather than one flat country list. Rate cards and quotes span
    # every region Meril buys in, and "show me what Europe costs" was a
    # question this screen could not answer at all — there was no country
    # filter here, only a country column you could eyeball.
    #
    # Both go through canonical_country because the country behind a row comes
    # from Vendor.country (or the RFI's, for a bid), which is free text holding
    # several spellings of the same place. Comparing raw strings would drop
    # exactly the rows whose spelling differs from the one in the dropdown.
    continent = request.GET.get("continent", "").strip().upper()
    if continent:
        rows = [row for row in rows if continent_of(row.country) == continent]

    country = request.GET.get("country", "").strip()
    if country:
        wanted = canonical_country(country)
        rows = [row for row in rows if canonical_country(row.country) == wanted]

    status = request.GET.get("status", "").strip()
    wanted_state = _STATUS_TO_EXPIRY_STATE.get(status)
    if wanted_state:
        rows = [row for row in rows if row.expiry_state == wanted_state]

    return rows, {
        "query": query, "status": status, "currency": currency, "source": source,
        "continent": continent, "country": canonical_country(country),
    }


# ── List ────────────────────────────────────────────────────────────────────
@login_required
@access_required
def commercial_list(request):
    """Searchable commercial list — rate cards and RFI bids — with expiry flags.

    A fourth tab, ?source=analytics, swaps the pricing table out entirely for
    the Bid Analytics dashboard (RFI-by-RFI comparisons, AI Evaluate, the
    price-premium heatmap and Meril GPT) via the same context and templates the
    standalone Bid Analytics page uses — see views._bid_analytics_context and
    templates/vendorlink/_bid_analytics_*.html. The pricing-table query work
    below is skipped in that case; nothing on the analytics tab needs it.
    """
    if (request.GET.get("source") or "").strip() == "analytics":
        # Imported here, not at module level: views.py and commercial.py have
        # never imported from each other, and a function-local import keeps it
        # that way regardless of load order — both modules are already fully
        # loaded by the time any view actually runs.
        from .views import _bid_analytics_context

        return render(request, "vendorlink/commercial/list.html", _ctx(
            request,
            source="analytics",
            result_count=0,
            query="", status="", currency="",
            # No country/continent here: _bid_analytics_context below supplies
            # both, and passing them as well is a duplicate keyword.
            countries=[],
            currencies=[c for c, _ in VendorCommercial.CURRENCY_CHOICES],
            top_vendors=[],
            kpis={
                "total": len(all_entries()),
                "cards": VendorCommercial.objects.count(),
                "bids": VendorBid.objects.count(),
            },
            warning_days=VendorCommercial.EXPIRY_WARNING_DAYS,
            **_bid_analytics_context(request),
            # _bid_analytics_body.html's two "Clear filters" links reset the
            # country/month/year filters while staying on this tab, rather
            # than dropping back to "All" and losing the point of clicking in.
            ba_clear_url=reverse("vendorlink:commercial_list") + "?source=analytics",
        ))

    cards, applied = filtered_cards(request, record_search=True)

    # Vendor first, then negotiated rate cards ahead of point-in-time quotes,
    # then newest first. Sorting by vendor is what makes the merged table
    # readable: a vendor's card and its bids land together, which is the whole
    # reason for showing them on one screen.
    cards.sort(key=lambda row: (
        (row.vendor_name or "").casefold(),
        0 if row.is_card else 1,
        -(row.submitted_at.timestamp() if row.submitted_at else 0),
    ))

    # Deliberately counted over every row, not the filtered set: the strip is
    # navigation into the four states, so its numbers have to stay put when a
    # filter is applied. The header badge next to the title is what reports the
    # filtered count. Each tile links to the filter it names — previously the
    # first two linked to the unfiltered list, so they looked clickable and did
    # nothing.
    everything = all_entries()
    states = [row.expiry_state for row in everything]
    card_total = sum(1 for row in everything if row.is_card)
    kpis = {
        "total": len(everything),
        "cards": card_total,
        "bids": len(everything) - card_total,
        "vendors": len({(row.vendor_name or "").casefold() for row in everything if row.vendor_name}),
        "expiring": states.count("soon"),
        "expired": states.count("expired"),
    }

    return render(request, "vendorlink/commercial/list.html", _ctx(
        request,
        cards=cards,
        result_count=len(cards),
        query=applied["query"],
        status=applied["status"],
        currency=applied["currency"],
        source=applied["source"],
        continent=applied["continent"],
        country=applied["country"],
        # Only the countries actually present on a rate card or quote, narrowed
        # to the chosen continent. Offering the full 252-country master here
        # would fill the dropdown with options that return nothing.
        countries=filter_countries_by_continent(
            canonical_countries(row.country for row in everything),
            applied["continent"],
        ),
        currencies=[c for c, _ in VendorCommercial.CURRENCY_CHOICES],
        top_vendors=_top_searched_vendors(),
        kpis=kpis,
        warning_days=VendorCommercial.EXPIRY_WARNING_DAYS,
    ))


# ── Detail ──────────────────────────────────────────────────────────────────
@login_required
@access_required
def commercial_detail(request, pk):
    card = get_object_or_404(
        VendorCommercial.objects.select_related("vendor", "created_by"), pk=pk
    )
    # Sibling cards for the same vendor — the plan's "vendor detail view shows
    # all rate cards linked to a specific vendor".
    siblings = (
        VendorCommercial.objects.filter(vendor=card.vendor)
        .exclude(pk=card.pk)
        .order_by("-contract_start", "-created_at")
    )
    return render(request, "vendorlink/commercial/detail.html", _ctx(
        request, card=card, siblings=siblings,
    ))


# ── Create / edit / delete ──────────────────────────────────────────────────
@login_required
@edit_required
def commercial_create(request):
    initial = {}
    # Arriving from a vendor's page pre-selects them, so "add a rate card for
    # this vendor" does not mean hunting a 200-entry dropdown.
    vendor_id = request.GET.get("vendor")
    if vendor_id:
        initial["vendor"] = vendor_id

    form = VendorCommercialForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        card = form.save(commit=False)
        card.created_by = request.user
        card.save()
        messages.success(request, f"Rate card added for {card.vendor.vendor_name}.")
        return redirect("vendorlink:commercial_detail", pk=card.pk)

    return render(request, "vendorlink/commercial/form.html", _ctx(
        request, form=form, mode="create",
    ))


@login_required
@edit_required
def commercial_edit(request, pk):
    card = get_object_or_404(VendorCommercial, pk=pk)
    form = VendorCommercialForm(request.POST or None, instance=card)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Rate card updated for {card.vendor.vendor_name}.")
        return redirect("vendorlink:commercial_detail", pk=card.pk)

    return render(request, "vendorlink/commercial/form.html", _ctx(
        request, form=form, mode="edit", card=card,
    ))


@login_required
@admin_required
@require_POST
def commercial_delete(request, pk):
    """Delete one rate card. Admin only — the Editor tier is denied this."""
    card = get_object_or_404(VendorCommercial, pk=pk)
    name = card.vendor.vendor_name
    card.delete()
    messages.success(request, f"Rate card for {name} deleted.")
    return redirect("vendorlink:commercial_list")


# ── Exports ─────────────────────────────────────────────────────────────────
def _export_rows(cards):
    for card in cards:
        yield [
            card.source_label if card.is_card else f"{card.source_label} — {card.source_detail}",
            card.vendor_name,
            card.country,
            card.currency,
            card.cost("storage_cost"),
            card.cost("inbound_cost"),
            card.cost("outbound_cost"),
            card.cost("pick_cost"),
            card.cost("packing_cost"),
            card.additional_charges,
            card.cost("customs_clearance_cost"),
            card.cost("last_mile_charges"),
            card.cost("manpower_charges"),
            card.contract_duration,
            card.price_validity,
            card.insurance,
            # A bid has no validity window to be in or out of, so it reports the
            # state it is actually in — its own bid status — instead of being
            # slotted into an expiry bucket that does not apply to it.
            card.bid_status_label if card.is_bid else
            {"expired": "Expired", "soon": "Expiring soon",
             "ok": "Active", "none": "No dates set"}[card.expiry_state],
        ]


@login_required
@access_required
def commercial_export(request):
    """Excel export of the commercial data — available to every tier, per the plan.

    Honours every filter on the screen, via the same :func:`filtered_cards` the
    list uses, so "export what I am looking at" is actually true. It previously
    read only ``q`` and ignored status and currency entirely.
    """
    cards, _applied = filtered_cards(request)
    cards.sort(key=lambda card: ((card.vendor_name or "").lower(), 0 if card.is_card else 1))

    wb = Workbook()
    ws = wb.active
    ws.title = "Commercial Database"

    ws.append(list(EXPORT_HEADERS))
    header_font = Font(bold=True, color="FFFFFF", size=11)
    fill = PatternFill("solid", fgColor=HEADER_FILL)
    for col in range(1, len(EXPORT_HEADERS) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"

    for row in _export_rows(cards):
        ws.append(row)

    widths = [34, 30, 16, 10, 13, 13, 14, 11, 13, 30, 20, 16, 17, 34, 14, 34, 15]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="commercial_database.xlsx"'
    return response


@login_required
@access_required
def commercial_export_pdf(request, pk):
    """PDF of one vendor's rate card, for sharing outside the platform.

    Rendered through xhtml2pdf from a print-only template rather than drawn with
    reportlab primitives, so the layout stays editable by whoever edits the rest
    of the templates.
    """
    from xhtml2pdf import pisa

    card = get_object_or_404(
        VendorCommercial.objects.select_related("vendor", "created_by"), pk=pk
    )
    html = render(request, "vendorlink/commercial/rate_card_pdf.html", {
        "card": card,
        "generated_at": timezone.localtime(),
        "generated_by": request.user.get_full_name() or request.user.username,
    }).content.decode("utf-8")

    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if result.err:
        messages.error(request, "The rate card PDF could not be generated.")
        return redirect("vendorlink:commercial_detail", pk=card.pk)

    safe_name = "".join(
        ch if ch.isalnum() or ch in "-_" else "_" for ch in card.vendor.vendor_name
    )[:60]
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="rate_card_{safe_name}.pdf"'
    return response
