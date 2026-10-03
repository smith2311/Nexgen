"""Presentation helpers for the Meril-V UI.

Colour is semantic here, so the mapping from a domain value to a visual family
lives in one place rather than being re-decided in each template. The three
axes stay independent, as the engine treats them:

* ``ValidationRow.status``        -> a 24px badge
* ``ValidationRow.stock_status``  -> a 20px chip
* ``ValidationRow.flags``         -> a wrapping row of chips

Nothing here reads the database or mutates state; it is formatting only.
"""

from __future__ import annotations

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from merilv import activitylog, rbac

register = template.Library()


# --------------------------------------------------------------------------- #
# Semantic families
# --------------------------------------------------------------------------- #

#: ValidationRow.status -> visual family. Values are the model's own constants.
STATUS_FAMILY = {
    "matched": "ok",
    # Something is off but the line still reconciles.
    "partial": "warn",
    "warning": "warn",
    "plant_mismatch": "warn",
    "expiry_mismatch": "warn",
    # The line does not reconcile.
    "variance": "danger",
    "variance_partial": "danger",
    "batch_mismatch": "danger",
    "dupe_scan": "danger",
    "over_scan": "danger",
    "error": "danger",
    # Not a failure of the stock, a gap in the data.
    "master_missing": "neutral",
    "incomplete": "neutral",
    "stock_exhausted": "neutral",
}

#: ValidationRow.stock_status -> visual family.
#: Damage outranks every expiry state; see ``stock_chip`` and rule 5 in
#: DATA_MODEL.md. Both are red, so the ordering matters only when a caller asks
#: for a single worst-case family.
STOCK_FAMILY = {
    "good": "ok",
    "damage": "danger",
    "expired": "danger",
    "short_expiry": "warn",
    "near_expiry": "warn",
    "no_expiry": "neutral",
}

#: Outbound + branch-transfer/ZVA05 ReasonCode -> visual family.
REASON_FAMILY = {
    "OK": "ok",
    # Rejected: the scan was wrong or the target is full.
    "WRONG_MATERIAL": "danger",
    "WRONG_BATCH": "danger",
    "DUPLICATE_SERIAL": "danger",
    "OVERSCAN": "danger",
    "TARGET_MISMATCH": "danger",
    # Nothing was focused, so there was no delivery to assign to. Its own
    # family, not "warn": warn is the duplicate colour, and reading DUPLICATE
    # over a scan that was never even looked at sends the operator to check the
    # carton instead of the delivery list. A workflow gate, not a verdict on
    # the item.
    "NO_FOCUS": "blocked",
    "EXPIRED_ITEM": "danger",
    "DELIVERY_FULL": "danger",
    "CUSTOMER_FULL": "danger",
    "BOX_FULL": "danger",
    # Branch transfer / ZVA05 equivalents.
    "MATERIAL_NOT_IN_PLAN": "danger",
    "LINE_FULFILLED": "danger",
    "NO_STOCK": "danger",
    "OUT_OF_FOCUS": "danger",
    # Branch transfer receiving. NOTHING_DISPATCHED is a warning rather than an
    # error: the scan itself is fine, but the paperwork disagrees with the pallet,
    # which is the sending plant's problem to answer for.
    "NOTHING_DISPATCHED": "warn",
    "OVER_RECEIVED": "danger",
    # Operator reversed their own accepted scan — not an error, but the code is
    # no longer assigned, so it must not read as "ok" in the status filter.
    # UNDONE is the reversal entry itself; REVERSED marks the original accept it
    # cancelled, which the append-only log cannot delete.
    "UNDONE": "warn",
    "REVERSED": "warn",
    # Data gaps rather than picking errors.
    "MISSING_MATERIAL": "neutral",
    "MISSING_BATCH": "neutral",
    "MASTER_MISSING": "neutral",
    "UNPARSEABLE": "neutral",
    "NO_DELIVERY_DATA": "neutral",
    "NO_PLAN_DATA": "neutral",
}

#: The engine's own cue names (services/outbound.AUDIO_CUE) collapsed onto the
#: four cue files the UI ships. Keeping this translation here means the engine
#: stays the single source of truth for which outcome deserves which sound.
CUE_FAMILY = {
    "match": "ok",
    "duplicate": "warn",
    "mismatch": "danger",
    "wrong_delivery": "danger",
    "error": "danger",
}


def _label(value) -> str:
    """``variance_partial`` -> ``Variance Partial``; reason codes stay as-is."""
    text = "" if value is None else str(value)
    if not text:
        return "—"
    if text.isupper():
        return text.replace("_", " ")
    return text.replace("_", " ").title()


# --------------------------------------------------------------------------- #
# Badges and chips
# --------------------------------------------------------------------------- #

@register.simple_tag
def status_badge(value):
    """24px pill for a ``ValidationRow.status``."""
    family = STATUS_FAMILY.get(str(value or ""), "neutral")
    return format_html(
        '<span class="mv-badge mv-badge--{}" title="{}">{}</span>',
        family, value or "", _label(value),
    )


@register.simple_tag
def stock_chip(value):
    """20px chip for a ``ValidationRow.stock_status``.

    Damage always reads as danger even when an expiry state is also present —
    a damaged carton must never be softened into an amber "near expiry".
    """
    family = STOCK_FAMILY.get(str(value or ""), "neutral")
    return format_html(
        '<span class="mv-chip mv-chip--{}" title="{}">{}</span>',
        family, value or "", _label(value),
    )


@register.simple_tag
def reason_badge(value):
    """Mono badge for an outbound/transfer ``ReasonCode``."""
    family = REASON_FAMILY.get(str(value or "").upper(), "neutral")
    return format_html(
        '<span class="mv-reason mv-reason--{}">{}</span>',
        family, _label(value),
    )


@register.simple_tag
def flag_chips(flags):
    """Every flag on a row, wrapping. Never truncated — pickers rely on all."""
    if not flags:
        return ""
    if isinstance(flags, str):
        flags = [f for f in (p.strip() for p in flags.split(",")) if f]
    out = [format_html('<span class="mv-flag">{}</span>', _label(f)) for f in flags]
    # Safe: every element of `out` is a format_html() SafeString.
    return format_html('<div class="mv-flags">{}</div>', mark_safe("".join(out)))  # nosec


#: ScanRecord.result (extractor live-scan/live-validate) -> visual family.
RESULT_FAMILY = {
    "found": "ok",
    "match": "ok",
    "duplicate": "warn",
    "not_found": "danger",
    "mismatch": "danger",
    "excess": "danger",
    "not_in_dump": "neutral",
}


@register.simple_tag
def scan_result_badge(value):
    family = RESULT_FAMILY.get(str(value or ""), "neutral")
    return format_html(
        '<span class="mv-badge mv-badge--{}">{}</span>', family, _label(value),
    )


@register.simple_tag
def scan_result_row_class(value):
    """CSS class for the whole row a scan result renders in — Row 3.9."""
    family = RESULT_FAMILY.get(str(value or ""), "")
    return f"mv-row--{family}" if family else ""


#: Bands that get no highlight — the unremarkable cases.
_EXPIRY_QUIET = frozenset({"", "good", "no_expiry"})

#: Why a date is flagged, for the cell's tooltip.
_EXPIRY_TITLES = {
    "expired": "Expired — this stock is past its date.",
    "near_expiry": "Near expiry — inside this entity's near-expiry window.",
    "short_expiry": "Short expiry — inside this entity's short-expiry window.",
    "damage": "Marked damaged.",
}


@register.simple_tag
def expiry_cell_class(state):
    """CSS class for an expiry cell — the third limb of Row 3.9.

    The band itself is computed server-side by ``classify_stock`` against the
    entity's own thresholds, because MHPL and MLSIPL invert "near" and "short"
    and a single hard-coded window would mislabel one of them.
    """
    state = str(state or "")
    if state in _EXPIRY_QUIET:
        return "mv-cell-expiry"
    return f"mv-cell-expiry mv-cell-expiry--{state}"


@register.simple_tag
def expiry_cell_title(state):
    """Tooltip explaining why an expiry date is flagged."""
    return _EXPIRY_TITLES.get(str(state or ""), "")


@register.simple_tag
def cue_for(reason_code):
    """Audio cue family for a reason code, for the ``data-cue`` attribute."""
    return REASON_FAMILY.get(str(reason_code or "").upper(), "neutral")


# --------------------------------------------------------------------------- #
# Tier predicates — used to gate whole sections, not to disable them
# --------------------------------------------------------------------------- #

@register.simple_tag
def can_access_merilv(user):
    return rbac.can_access_merilv(user)


@register.simple_tag
def can_operate_merilv(user):
    return rbac.can_operate_merilv(user)


@register.simple_tag
def is_merilv_admin(user):
    return rbac.is_merilv_admin(user)


@register.filter(name="can_operate_merilv")
def can_operate_merilv_filter(user):
    """Filter form, so templates can write ``{% if user|can_operate_merilv %}``."""
    return rbac.can_operate_merilv(user)


@register.filter(name="transit_label")
def transit_label(value):
    """``Road - M2`` — the word for the picker, the code for the SAP upload.

    One filter rather than the raw field, so every screen that shows a mode
    shows the same thing. The mapping is merilv/transit.py; the Excel columns
    take the code half of it.
    """
    from merilv import transit

    return transit.label_for(value)


@register.filter(name="is_merilv_admin")
def is_merilv_admin_filter(user):
    return rbac.is_merilv_admin(user)


# --------------------------------------------------------------------------- #
# Icons
# --------------------------------------------------------------------------- #

#: Lucide-style glyphs drawn locally: the design export ships no icon assets and
#: this platform must not fetch from a CDN. 24x24, 1.5 stroke, round joins —
#: matching the icon spec in DESIGN.md.
_ICON_PATHS = {
    "scan": '<rect x="3" y="6" width="18" height="12" rx="1"/><path d="M7 9v6M10 9v6M13 9v6M17 9v6"/>',
    "clipboard": '<rect x="6" y="4" width="12" height="16" rx="2"/><path d="M9 4h6M9 12l2 2 4-4"/>',
    "transfer": '<path d="M4 8h13l-3-3M20 16H7l3 3"/>',
    "wand": '<path d="M5 19 19 5M14 5h5v5"/><path d="M6 9h3M7.5 7.5v3"/>',
    "barcode": '<path d="M4 5v14M7 5v14M10 5v10M13 5v14M17 5v10M20 5v14"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    "truck": '<rect x="2" y="7" width="12" height="9" rx="1"/><path d="M14 10h4l3 3v3h-7z"/><circle cx="6.5" cy="18" r="1.6"/><circle cx="17.5" cy="18" r="1.6"/>',
    "history": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    "list": '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    "chart": '<path d="M4 20V4M4 20h16"/><rect x="8" y="12" width="3" height="5"/><rect x="14" y="8" width="3" height="9"/>',
    "activity": '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    "package": '<path d="M12 3 4 7v10l8 4 8-4V7z"/><path d="M4 7l8 4 8-4M12 11v10"/>',
    "users": '<circle cx="9" cy="9" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 7a3 3 0 0 1 0 6M17 20a6 6 0 0 0-2-4.3"/>',
    "box": '<rect x="4" y="6" width="16" height="13" rx="1"/><path d="M4 10h16M12 10v9"/>',
    "building": '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2"/>',
    "layers": '<path d="M12 3 3 8l9 5 9-5z"/><path d="M3 13l9 5 9-5"/>',
    "key": '<circle cx="8" cy="14" r="4"/><path d="M11 11 20 2M17 5l2 2M15 7l2 2"/>',
    "scroll": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/>',
    "chevron": '<path d="M9 6l6 6-6 6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.3-4.3"/>',
    "upload": '<path d="M12 16V4M8 8l4-4 4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "download": '<path d="M12 4v12M8 12l4 4 4-4"/><path d="M4 18v.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V18"/>',
    "cloud-upload": '<path d="M7 17a4 4 0 1 1 .6-7.96A5 5 0 0 1 17 9a3.5 3.5 0 0 1 .5 6.96"/><path d="M12 21v-8M9 16l3-3 3 3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "filter": '<path d="M4 5h16l-6 7v6l-4-2v-4z"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "check": '<path d="M5 12l4.5 4.5L19 7"/>',
    "check-circle": '<circle cx="12" cy="12" r="8"/><path d="M8.5 12l2.5 2.5L16 9.5"/>',
    "x-circle": '<circle cx="12" cy="12" r="8"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "alert": '<path d="M12 4 3 19h18z"/><path d="M12 10v4M12 16.5h.01"/>',
    "info": '<circle cx="12" cy="12" r="8"/><path d="M12 11v5M12 8.5h.01"/>',
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    "inbox": '<path d="M4 13h4l1 3h6l1-3h4"/><path d="M4 13 6 5h12l2 8v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/>',
    "search-x": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.3-4.3M9 9l4 4M13 9l-4 4"/>',
    "folder": '<path d="M4 7a1 1 0 0 1 1-1h4l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/>',
    "undo": '<path d="M9 10H4V5"/><path d="M4 10a8 8 0 1 1 3 6.2"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow-left": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    # Sort-direction toggle: a down/up arrow paired with the letters it
    # would order by, in the same hand-drawn stroke style as every other
    # glyph here rather than rendered text (which would carry its own font
    # weight and break at icon scale).
    "sort-az": '<path d="m3 16 4 4 4-4"/><path d="M7 20V4"/><path d="M20 8h-5"/><path d="M15 10V6.5a2.5 2.5 0 0 1 5 0V10"/><path d="M15 14h5l-5 6h5"/>',
    "sort-za": '<path d="m3 8 4-4 4 4"/><path d="M7 4v16"/><path d="M15 4h5l-5 6h5"/><path d="M15 20v-3.5a2.5 2.5 0 0 1 5 0V20"/><path d="M20 18h-5"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
    "volume": '<path d="M11 5 6.5 9H3v6h3.5L11 19z"/><path d="M15.5 9.5a3.5 3.5 0 0 1 0 5M18 7a7 7 0 0 1 0 10"/>',
    "volume-off": '<path d="M11 5 6.5 9H3v6h3.5L11 19z"/><path d="M16 10l4 4M20 10l-4 4"/>',
    "edit": '<path d="M4 20h4L20 8l-4-4L4 16z"/>',
    "trash": '<path d="M4 7h16M10 4h4M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/>',
    "sheet": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 13h6M9 17h6M9 13v4"/>',
    "sitemap": '<rect x="9" y="3" width="6" height="4" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 7v5M6 16v-2h12v2"/>',
    "more": '<circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/>',
    # View-mode switcher (cards / list / table / kanban)
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/>',
    "table": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M3 10h18M9 10v9M15 10v9"/>',
    "columns": '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="16" rx="1"/><rect x="17" y="4" width="4" height="16" rx="1"/>',
}


@register.simple_tag(takes_context=True)
def mv_back(context):
    """Resolve the current screen's parent for the header Back button.

    Returns ``{"url": ..., "label": ...}``. Deterministic — it depends only on
    which screen you are ON, never on how you got there, so Back behaves the
    same on a fresh tab, after a form POST redirect, or ten pages deep. See
    merilv/navigation.py for the hierarchy.
    """
    from django.urls import NoReverseMatch, reverse

    from merilv.navigation import LANDING, PARENTS

    fallback = {"url": reverse("merilv:index"), "label": "Meril-V"}

    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return fallback

    spec = PARENTS.get(match.url_name)
    if spec is None:
        return fallback

    parent, label, arg_key = spec

    if parent == LANDING:
        try:
            return {"url": reverse("landing_page"), "label": label}
        except NoReverseMatch:
            return fallback

    args = []
    if arg_key:
        # The parent route needs a pk (e.g. the discrepancy report belongs to
        # one session). If that object isn't in context, fall back rather than
        # render a broken link.
        obj = context.get(arg_key)
        pk = getattr(obj, "pk", None)
        if pk is None:
            return fallback
        args = [pk]

    try:
        return {"url": reverse(f"merilv:{parent}", args=args), "label": label}
    except NoReverseMatch:
        return fallback


@register.simple_tag(takes_context=True)
def mv_qs(context, **overrides):
    """The current query string with ``overrides`` applied.

    For links that change one facet and must keep the rest — switching a tab
    without losing the search, date range and sort the user set. Passing an
    empty value drops the key, which is how a filter chip clears itself.

    Also resets ``page``: keeping page 7 while changing a filter lands the user
    on an empty page and looks like the filter returned nothing.
    """
    request = context.get("request")
    params = request.GET.copy() if request is not None else {}
    for key, value in overrides.items():
        if value in ("", None):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop("page", None)
    return params.urlencode()


@register.simple_tag(takes_context=True)
def pagination_links(context, page_obj, param="page", window=2):
    """Render prev/1…N/next for a ``django.core.paginator.Page``.

    Every other active GET param (search, entity, status filters — and any
    OTHER paginator's own page number, when a page has more than one table)
    is preserved on every link, since this is a plain query-string param, not
    a path segment. ``param`` is which query key THIS paginator owns, so two
    tables on the same page (Master Data → Organisation) don't clobber each
    other's position when you page through one of them.
    """
    if page_obj is None or page_obj.paginator.num_pages <= 1:
        return ""

    request = context.get("request")
    base = request.GET.copy() if request is not None else {}

    def href(n):
        qd = base.copy()
        qd[param] = n
        return "?" + qd.urlencode()

    def cell(label, n=None, current=False, disabled=False):
        if disabled:
            return format_html('<span class="is-disabled">{}</span>', label)
        if current:
            return format_html('<span class="is-current">{}</span>', label)
        return format_html('<a href="{}">{}</a>', href(n), label)

    current = page_obj.number
    num_pages = page_obj.paginator.num_pages

    parts = [cell(icon("arrow-left", 14), current - 1, disabled=not page_obj.has_previous())]

    show = {1, num_pages} | {n for n in range(current - window, current + window + 1) if 1 <= n <= num_pages}
    prev_shown = None
    for n in sorted(show):
        if prev_shown is not None and n - prev_shown > 1:
            # Safe: a literal with no interpolation.
            parts.append(mark_safe('<span class="is-disabled">…</span>'))  # nosec
        parts.append(cell(str(n), n, current=(n == current)))
        prev_shown = n

    parts.append(cell(icon("arrow-right", 14), current + 1, disabled=not page_obj.has_next()))

    # Safe: `parts` holds only cell()/icon() SafeStrings.
    return format_html('<div class="mv-pagination">{}</div>', mark_safe("".join(parts)))  # nosec


@register.simple_tag
def icon(name, size=16):
    """Inline an icon. ``size`` is 16 inline, 20 for section/nav headers."""
    path = _ICON_PATHS.get(name, _ICON_PATHS["list"])
    return format_html(
        '<svg viewBox="0 0 24 24" width="{}" height="{}" fill="none" '
        'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true" focusable="false">{}</svg>',
        # Safe: `path` is a value from the _ICON_PATHS module constant.
        size, size, mark_safe(path),  # nosec
    )


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #

@register.filter
def box_capacity(max_qty):
    """``max_qty=0`` means unlimited — never render a bare "0"."""
    try:
        value = int(max_qty)
    except (TypeError, ValueError):
        return "Unlimited"
    return "Unlimited" if value <= 0 else f"{value:,}"


@register.simple_tag
def courier_options():
    """Active couriers for the Finish Delivery autocomplete.

    A template-only lookup so screens.py/views.py stay untouched — this is
    presentation data (a <datalist>), not something the view's control flow
    depends on.
    """
    from merilv.models import CourierMaster

    return CourierMaster.objects.filter(is_active=True).order_by("courier_name")


@register.filter
def box_by_code(boxes, code):
    """Find a ``BoxProfile`` in an already-fetched queryset/list by its code.

    A template-only lookup so the view does not need a dedicated "selected
    box" context var — ``boxes`` is already fetched for the box picker.
    """
    if not code:
        return None
    for box in boxes:
        if box.code == code:
            return box
    return None


#: The launcher's tool list and a few other spots still hand FontAwesome
#: classes to the view/template boundary (`icon: "fas fa-clipboard-check"`).
#: Rather than touch that view code, translate at render time.
_FA_TO_ICON = {
    "fas fa-clipboard-check": "clipboard",
    "fas fa-truck-fast": "truck",
    "fas fa-barcode": "barcode",
    "fas fa-right-left": "transfer",
    "fas fa-file-invoice": "file",
    "fas fa-wand-magic-sparkles": "wand",
    "fas fa-tower-broadcast": "monitor",
    "fas fa-clock-rotate-left": "history",
    "fas fa-chart-column": "chart",
    "fas fa-database": "layers",
    "fas fa-boxes-stacked": "package",
    "fas fa-address-book": "users",
    "fas fa-truck": "truck",
    "fas fa-cube": "box",
    "fas fa-sitemap": "layers",
    "fas fa-sliders": "building",
    "fas fa-user-shield": "key",
}


@register.simple_tag
def fa_icon(fa_class, size=20):
    return icon(_FA_TO_ICON.get(fa_class, "list"), size)


@register.filter
def pluck_max(rows, key):
    """Largest value of ``key`` across a list of dicts — for a bar chart's
    denominator, since Django templates have no built-in max()."""
    values = [row.get(key, 0) for row in rows] if rows else []
    return max(values) if values else 1


@register.filter
def display_name(user, fallback="—"):
    """A user's full name, or their username, or a fallback — safe when
    ``user`` itself is None (a nullable FK: ActivityLog.actor,
    FinishedDelivery.picker, ScanSession.created_by all use SET_NULL).

    The obvious template spelling —
    ``{{ x.get_full_name|default:x.username|default:"—" }}`` — crashes with an
    uncaught ``VariableDoesNotExist`` when ``x`` is None: the *argument* to a
    chained ``default`` filter is resolved strictly, unlike the main value,
    which fails silently. This does the same null-coalescing in Python instead,
    where None is just None.
    """
    if user is None:
        return fallback
    return user.get_full_name() or user.username or fallback


# --------------------------------------------------------------------------- #
# Activity log presentation
#
# The formatting itself lives in merilv/activitylog.py so the Excel download
# says exactly what this table says. These are thin template-facing wrappers.
# --------------------------------------------------------------------------- #
@register.filter
def activity_details(metadata):
    """``ActivityLog.metadata`` as readable label/value pairs.

    Returns a list of dicts so the template can space them properly instead of
    printing one pre-joined string. An empty or non-dict payload returns an
    empty list, which the template renders as a dash — most log rows genuinely
    have no extra detail, and "{}" is a worse way to say so.
    """
    return activitylog.detail_pairs(metadata)


@register.filter
def humanise_action(action):
    """``bt_plan_upload`` -> ``BT plan upload``. The slug stays the filter value."""
    return activitylog.humanise_action(action)


@register.filter
def pretty_json(value):
    """Indented JSON for the expand-a-row detail panel.

    ``{{ log.metadata }}`` there printed Python ``repr`` — single quotes,
    ``True``/``None`` — which is not JSON and not what the API or the database
    holds either.
    """
    import json

    if value in (None, "", {}, []):
        return "No additional detail recorded."
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


@register.filter
def get_item(mapping, key):
    """Dict lookup by a variable key — Django templates have no ``[key]`` syntax."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def pct(value):
    """One decimal place, or an em dash when there is nothing to show."""
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------- #
# Reports → User Activity
# --------------------------------------------------------------------------- #
@register.filter
def duration(seconds):
    """``9315`` -> ``2h 35m``, or an em dash for nothing.

    Shares ``useractivity.humanise_seconds`` with the Excel export, for the
    same reason ``activity_details`` shares its formatter: two spellings of
    "how long" that can disagree is a bug the reader cannot see.
    """
    from .. import useractivity

    return useractivity.humanise_seconds(seconds) or "—"


@register.filter
def initials(name):
    """``Smith Kansara`` -> ``SK``, for the avatar chip.

    Falls back to the first two characters of a single-word name so a username
    with no space still renders a two-letter chip rather than a lone letter
    floating in a circle.
    """
    parts = [p for p in str(name or "").replace(".", " ").replace("_", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@register.filter
def role_tier_class(role):
    """The ``mv-tier--*`` modifier for a role name.

    Only ``--admin`` and ``--picker`` exist as variants, so Manager borrows the
    admin treatment (it is a supervisor tier) and Viewer/no-role fall through
    to the neutral base class rather than inventing colours here.
    """
    if not role:
        return ""
    if "Admin" in role or "Superuser" in role or "Manager" in role:
        return "mv-tier--admin"
    if "Picker" in role:
        return "mv-tier--picker"
    return ""
