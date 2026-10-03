"""Building and sending VendorLink's outgoing mail.

One place that renders an email template, attaches the Meril logo inline and
sends it, so every message a vendor receives looks like it came from the same
system. Before this, five templates each carried their own wrapper, several
mails were plain text with no branding at all, and none of them carried a logo.

Two things worth stating because they look wrong out of context:

**The logo is attached, not linked.** Gmail, Outlook and Apple Mail all block
remote images by default, so a hosted logo renders as a broken placeholder the
first time a vendor opens the mail — the one impression that matters. The image
rides along as a MIME part with a Content-ID and the template references
``cid:``.

**Every message carries a real plain-text alternative.** Some vendors read mail
as text, and a multipart message whose text part is a genuine rendering scores
better with spam filters than one whose text part is stripped HTML. Callers
pass a ``text`` template alongside the HTML one.

Sends are threaded by default. A live SMTP round trip measured just under a
second on this host and ``EMAIL_TIMEOUT`` is 20, so an unreachable mail server
would otherwise hold a form submission for twenty seconds.
"""

from __future__ import annotations

import logging
import threading
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

#: Content-ID the footer's <img src="cid:…"> points at.
LOGO_CID = "meril-logo"

#: Candidates in preference order. Both are JPEG, so neither has an alpha
#: channel and both arrive with their own background baked in — the footer
#: puts whichever is found into a white chip so that reads as deliberate. The
#: white-background file is preferred only because it matches that chip; a
#: transparent PNG would let the chip go entirely.
_LOGO_CANDIDATES = (
    "img/Meril Logo.jpg",
    "img/Meril-logo-bluebg.jpg",
)


def logo_path():
    """Absolute path to the footer logo, or None if none is present.

    Missing artwork must not stop a mail going out — the template falls back to
    a wordmark — so this returns None rather than raising.
    """
    roots = [Path(settings.BASE_DIR) / "static"]
    static_root = getattr(settings, "STATIC_ROOT", None)
    if static_root:
        roots.append(Path(static_root))

    for root in roots:
        for candidate in _LOGO_CANDIDATES:
            path = root / candidate
            if path.exists():
                return path
    logger.warning("VendorLink mail: no footer logo found under %s", roots)
    return None


def logo_cid():
    """The Content-ID to reference, or "" when no artwork is present.

    Resolvable before a message exists, which is what lets the templates carry
    the decision themselves via the {% vl_logo_cid %} tag instead of every
    sender plumbing it through its context.
    """
    return LOGO_CID if logo_path() else ""


def attach_logo(message):
    """Attach the footer logo to an already-built message. Returns whether it did."""
    return _attach_logo(message)


def _attach_logo(message):
    path = logo_path()
    if not path:
        return False
    try:
        with open(path, "rb") as handle:
            image = MIMEImage(handle.read(), _subtype="jpeg")
    except OSError as exc:
        logger.warning("VendorLink mail: could not read %s: %s", path, exc)
        return False

    image.add_header("Content-ID", f"<{LOGO_CID}>")
    # inline, not attachment: without this some clients list the logo as a
    # downloadable file alongside the message.
    image.add_header("Content-Disposition", "inline", filename=path.name)
    message.attach(image)
    return True


def build(subject, to, html_template, context=None, text_template=None, reply_to=None):
    """Render a VendorLink mail and return it ready to send.

    ``context`` gains ``brand_logo_cid`` only when the logo actually attached,
    so the template can fall back to a wordmark rather than rendering a broken
    image reference.
    """
    recipients = [address for address in (to if isinstance(to, (list, tuple)) else [to]) if address]
    if not recipients:
        return None

    context = dict(context or {})
    message = EmailMultiAlternatives(
        subject=subject,
        body="",                       # replaced below
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
        reply_to=[reply_to] if reply_to else None,
    )

    # Attached before rendering: the template branches on whether the logo is
    # really there.
    context["brand_logo_cid"] = LOGO_CID if _attach_logo(message) else ""

    message.body = (
        render_to_string(text_template, context) if text_template
        else _fallback_text(context, subject)
    )
    message.attach_alternative(render_to_string(html_template, context), "text/html")
    return message


def _fallback_text(context, subject):
    """A minimal text part for callers that have no text template yet.

    Deliberately terse rather than a stripped-HTML approximation: a mangled
    text rendering reads worse than a short honest one, and stripped markup is
    what spam filters treat as suspicious.
    """
    lines = [subject, ""]
    if context.get("intro"):
        lines += [str(context["intro"]), ""]
    lines += [
        "Open Meril VendorLink to view the details.",
        "",
        "— Meril Life Sciences Pvt. Ltd., Global Supply Chain Management",
    ]
    return "\n".join(lines)


def send(message, async_=True):
    """Send, off the request thread unless told otherwise.

    Threaded because a real SMTP round trip on this host measured ~916 ms and
    EMAIL_TIMEOUT is 20 — long enough for an unreachable server to make a form
    look broken. The DB connection is closed on the way out: a thread that
    borrows one and exits leaks it from the pool.
    """
    if message is None:
        return
    if not async_:
        _deliver(message)
        return
    threading.Thread(target=_deliver, args=(message,), daemon=True).start()


def _deliver(message):
    try:
        message.send(fail_silently=False)
    except Exception:
        # Logged, never raised: a failed notification must not roll back the
        # action that triggered it. The vendor row, the award, the registration
        # decision are all already committed by this point.
        logger.exception("VendorLink mail failed: %r to %r", message.subject, message.to)
    finally:
        from django.db import connection
        connection.close()


def send_template(subject, to, html_template, context=None, text_template=None,
                  reply_to=None, async_=True):
    """build() + send() — the call most senders want."""
    send(
        build(subject, to, html_template, context=context,
              text_template=text_template, reply_to=reply_to),
        async_=async_,
    )
