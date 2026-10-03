import logging
import os

from django import template

logger = logging.getLogger(__name__)
register = template.Library()


@register.filter
def split(value, delimiter=","):
    return [s.strip() for s in value.split(delimiter)]


@register.filter
def getattr_val(obj, name):
    return getattr(obj, name, None)


@register.filter
def friendly_name(field_name):
    return field_name.replace("_", " ").title()


_EXT_ICONS = {
    ".pdf":  "fa-file-pdf",
    ".doc":  "fa-file-word",  ".docx": "fa-file-word",
    ".xls":  "fa-file-excel", ".xlsx": "fa-file-excel",
    ".ppt":  "fa-file-powerpoint", ".pptx": "fa-file-powerpoint",
    ".zip":  "fa-file-zipper", ".rar": "fa-file-zipper", ".7z": "fa-file-zipper",
    ".jpg":  "fa-file-image", ".jpeg": "fa-file-image",
    ".png":  "fa-file-image", ".gif":  "fa-file-image", ".webp": "fa-file-image",
    ".txt":  "fa-file-lines", ".csv": "fa-file-csv",
}

_PREVIEWABLE = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".xls", ".xlsx"}


@register.filter
def file_icon(filename):
    ext = os.path.splitext((filename or "").lower())[1]
    return _EXT_ICONS.get(ext, "fa-file")


@register.filter
def is_previewable(filename):
    ext = os.path.splitext((filename or "").lower())[1]
    return ext in _PREVIEWABLE


@register.filter
def sparkline(values, height=28):
    """Render a list of numbers as an inline SVG trace.

    Inline rather than a chart library because a KPI tile draws one of these
    per metric — eight Chart.js instances for eight 28px traces costs far more
    than the traces are worth, in both bytes and paint time.

    The y-axis is scaled to the series' own min/max, so the shape shows the
    movement in *this* metric rather than being flattened by whatever unit it
    happens to use. A flat series is drawn as a mid-height line rather than
    dividing by a zero range.
    """
    from django.utils.html import format_html
    from django.utils.safestring import mark_safe

    points = [v for v in (values or []) if v is not None]
    if len(points) < 2:
        return ""

    low, high = min(points), max(points)
    span = (high - low) or 1
    step = 100 / (len(points) - 1)

    coords = []
    for index, value in enumerate(points):
        x = index * step
        # SVG y grows downward, so invert: the highest value sits at the top.
        y = 100 - ((value - low) / span * 100) if high != low else 50
        coords.append(f"{x:.1f},{y:.1f}")

    path = "M" + " L".join(coords)
    return format_html(
        '<svg class="vl-spark" viewBox="0 0 100 100" preserveAspectRatio="none" '
        'aria-hidden="true" focusable="false"><path d="{}"/></svg>',
        # Safe: `path` is built from formatted floats only.
        mark_safe(path),  # nosec
    )


# ── Rich-text sanitisation ──────────────────────────────────────────────────
# The four operator-authored copy fields on a VendorRequest (intro_message,
# header_message, scope_of_work, footer_message) are rendered as HTML so that
# lists and emphasis in the request body survive. They used to be rendered with
# a bare `|safe`, which is stored XSS: anyone able to raise a request could
# persist a <script> that then fired for every vendor and every admin who
# opened that request. `|safe_richtext` replaces those four call sites.
#
# Allowlist, not denylist — a denylist of "dangerous" tags loses to the next
# parser quirk, an allowlist does not.
_RICHTEXT_TAGS = {
    "p", "br", "strong", "em", "ul", "ol", "li", "a", "code", "pre", "h3", "h4",
}
_RICHTEXT_ATTRIBUTES = {"a": {"href", "title"}}

# `javascript:` and `data:` are the two schemes that turn a link into script
# execution, so only these three are permitted through.
_RICHTEXT_URL_SCHEMES = {"http", "https", "mailto"}

# Whether the sanitised output already carries its own block structure. If it
# does not, the source was plain text typed into a textarea and its newlines
# are the only paragraph information there is — dropping them would run the
# whole request body together into one wall of text.
_BLOCK_MARKERS = ("<p", "<br", "<ul", "<ol", "<pre", "<h3", "<h4")


@register.filter
def safe_richtext(value):
    """Sanitise operator-authored HTML against a fixed allowlist.

    Returns markup that is safe to render unescaped. Tags outside the allowlist
    are unwrapped (their text survives, the tag does not) except for script and
    style, whose contents are dropped outright; event handlers and any
    `javascript:`/`data:` URL are stripped; every surviving link gains
    ``rel="noopener noreferrer"``.

    Plain text is handled too: if the cleaned result has no block-level markup
    of its own, newlines are promoted to paragraphs, because for a field typed
    into a plain textarea the line breaks *are* the structure.
    """
    from django.utils.html import escape, linebreaks
    from django.utils.safestring import mark_safe

    text = "" if value is None else str(value)
    if not text.strip():
        return ""

    try:
        import nh3
    except ImportError:
        # Fail closed, and fail *soft*. This filter is the only thing standing
        # between operator-authored HTML and the browser, so without it the
        # content cannot be rendered as markup — but a missing dependency must
        # not take out every request-detail page either, which is exactly what
        # happened when a container was recreated without nh3 installed and the
        # bare `import nh3` turned into a 500 on /vendorlink/requests/<id>/.
        #
        # Escaping shows the copy as literal text: visibly wrong, obviously a
        # deployment fault, and safe. Logged at error level because the machine
        # is missing a declared requirement.
        logger.error(
            "nh3 is not installed — request copy is being escaped rather than "
            "rendered. Install it (it is in requirements.txt) and rebuild the "
            "image; the running container has drifted from the manifest."
        )
        # Safe: escape() is applied before linebreaks().
        return mark_safe(linebreaks(escape(text), autoescape=False))  # nosec

    cleaned = nh3.clean(
        text,
        tags=_RICHTEXT_TAGS,
        attributes=_RICHTEXT_ATTRIBUTES,
        url_schemes=_RICHTEXT_URL_SCHEMES,
    )

    if not any(marker in cleaned.lower() for marker in _BLOCK_MARKERS):
        # autoescape=False because `cleaned` is already sanitised — escaping it
        # again would show operators their own tags as literal text.
        cleaned = linebreaks(cleaned, autoescape=False)

    # Safe: `cleaned` came through bleach.clean() with the allowlists above.
    return mark_safe(cleaned)  # nosec


@register.simple_tag
def vl_logo_cid():
    """Content-ID of the footer logo for VendorLink emails, or "".

    A tag rather than a context variable so an email template is self-contained:
    six different senders build these messages, and every one of them would
    otherwise have to remember to put brand_logo_cid in its context — which is
    exactly the kind of thing that gets forgotten and renders a dead image in a
    vendor's inbox. Senders only have to attach the image.
    """
    from vendorlink import mailer

    return mailer.logo_cid()
