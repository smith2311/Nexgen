"""Awarding a request to a vendor.

Its own module for the same reason commercial.py and assessment.py are: this is
a fifth concern on top of an already 2,400-line views.py, and unlike
bidviews.py — which its own docstring pins as read-and-render only — awarding
writes a model and owns its own endpoints.

The flow, and why it has the shape it does:

1. **Staff award a bid** (``award_create``). This is a separate action from the
   comparison-revision chooser, which writes ``VendorBid.is_selected``.
   That flag is scoped per vendor — ``views.bid_select`` clears only the *same
   vendor's* other revisions, so three vendors on one request can each have a
   chosen comparison revision. Hanging an outward-facing mail off it would fire
   a notice at every vendor whose radio a buyer happened to click while
   comparing. An award is the request's single outcome and needs its own
   deliberate trigger.

2. **The vendor gets an informational mail.** No buttons, no answer expected —
   see the module docstring on ``models.BidAward`` for why this used to be a
   two-step Accept/Decline conversation and no longer is. The award is decided
   the moment staff make it; the vendor is told as a courtesy.

3. **The other bidders get their regret letter immediately**, in the same
   request that made the award — there is no longer a separate "the vendor
   said yes" event to wait for. ``notify_unsuccessful_bidders`` is safe to call
   on every award (first or re-award): each recipient is claimed exactly once
   via ``BidOutcomeNotice``'s uniqueness constraint, so a vendor bumped from a
   previous award and a bidder who only just submitted their quote both get
   exactly one letter, and nobody already told gets a second.

4. **Withdrawing a live award mails that vendor a short "this has been
   withdrawn" notice** and removes the row, freeing the request to be awarded
   again. Re-awarding to someone else already covers the displaced vendor
   through step 3 above, so this notice only fires from an explicit withdraw
   with nothing nominated in its place.

Both the offer/decision step and its authorisation are unilateral on the
requester's side now: :func:`vendorlink.rbac.can_manage_award` gates every
endpoint here, and it admits either a VendorLink Admin or the specific person
who raised the request in question.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives, get_connection
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from product_scanning.feedback_notifications import _send_async

from . import bidcompare
from . import mailer
from .models import BidAward, BidOutcomeNotice, VendorBid, VendorRequest
from .rbac import can_manage_award

logger = logging.getLogger(__name__)


# ── Resolving who to write to ───────────────────────────────────────────────
def vendor_contact(bid):
    """Name, email, registration and Vendor row behind a bid.

    A bid identifies its vendor through the registration it was submitted
    against, but that row can be missing — deleted, or a bid a buyer keyed in
    on a vendor's behalf. The submitting user is then the only contact there
    is, and their email is the one the portal will match on anyway, so it is
    the fallback rather than a reason to refuse the award.
    """
    registration = getattr(bid, "vendor_registration", None)
    vendor = getattr(registration, "vendor", None) if registration else None
    submitter = getattr(bid, "submitted_by", None)

    name = ""
    if registration and registration.vendor_name:
        name = registration.vendor_name
    elif vendor and vendor.vendor_name:
        name = vendor.vendor_name
    elif submitter:
        name = submitter.get_full_name() or submitter.username

    email = ""
    if registration and registration.email:
        email = registration.email
    elif vendor and vendor.email:
        email = vendor.email
    elif submitter and submitter.email:
        email = submitter.email

    return (name or "Vendor"), (email or "").strip(), registration, vendor


def bid_total(bid):
    """The bid's quoted total, by the same sum the comparison screens show."""
    column = bidcompare.column_for(bid, "")
    return column.get("total")


# ── Mail ────────────────────────────────────────────────────────────────────
def _absolute(request, name, *args):
    path = reverse(name, args=args)
    if request is not None:
        return request.build_absolute_uri(path)
    # Reachable from a management command or a shell, where there is no request
    # to derive the host from.
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{base}{path}"


def _sender_name(user):
    if not user:
        return "Meril Global SCM"
    return user.get_full_name() or user.username


def send_award_notice(request, award):
    """The "you have been selected" mail. Informational — nothing to answer.

    Recorded rather than fire-and-forget: ``notified_at`` is what lets the
    award screen tell "the vendor has been told" apart from "the mail never
    left", which otherwise look identical and only one of which staff can act
    on. Sent inline rather than through ``_send_async``: the outcome is stored
    on the award, and a thread that finished after the redirect would have to
    write the row from under the request that just saved it.
    """
    if not award.vendor_email:
        award.notify_error = "This vendor has no email address on file, so no notice could be sent."
        award.save(update_fields=["notify_error", "updated_at"])
        return False

    req = award.vendor_request
    context = {
        "award": award,
        "req": req,
        "vendor_name": award.vendor_name,
        "awarded_amount": award.awarded_amount,
        "awarded_currency": award.awarded_currency,
        "wins": BidAward.wins_for_email(award.vendor_email),
        "portal_url": _absolute(request, "vendorlink:index"),
        "raised_by_name": _sender_name(award.awarded_by),
        "raised_by_email": getattr(award.awarded_by, "email", ""),
    }
    html_body = render_to_string("vendorlink/email/bid_awarded.html", context)
    text_body = (
        f"Dear {award.vendor_name},\n\n"
        f"We are delighted to inform you that your bid for \"{req.subject}\" has been "
        "selected by the Meril Global SCM team. Thank you for a competitive proposal.\n\n"
        "Our team will reach out to you shortly with the next steps to formalise the "
        "engagement.\n\n"
        "Regards,\nMeril Life Sciences — Global SCM"
    )
    subject = f"You've been selected — {req.subject}"
    try:
        msg = EmailMultiAlternatives(
            subject, text_body, settings.DEFAULT_FROM_EMAIL, [award.vendor_email],
        )
        msg.attach_alternative(html_body, "text/html")
        # cid: in the footer needs the image on the message — see vendorlink.mailer.
        mailer.attach_logo(msg)
        msg.send(fail_silently=False)
    except Exception as exc:                                     # noqa: BLE001
        logger.exception("Award notice mail failed for award %s", award.pk)
        award.notify_error = str(exc)
        award.save(update_fields=["notify_error", "updated_at"])
        return False

    award.notified_at = timezone.now()
    award.notify_error = ""
    award.save(update_fields=["notified_at", "notify_error", "updated_at"])
    return True


def send_withdrawal_notice(request, vendor_name, vendor_email, req):
    """A short, neutral "this has been withdrawn" notice.

    Best-effort and asynchronous: the requester has already seen the
    withdrawal confirmed on screen, so a mail server hiccup must not turn that
    into an error page or make the withdrawal look like it failed. Fired only
    from an explicit withdraw with nothing nominated in its place — a
    re-award to someone else tells the displaced vendor through the regret
    letter instead (see the module docstring), so this and that never both
    fire for the same change.
    """
    if not vendor_email:
        return False
    html_body = render_to_string("vendorlink/email/award_withdrawn.html", {
        "req": req,
        "vendor_name": vendor_name,
    })
    text_body = (
        f"Dear {vendor_name},\n\n"
        f"This is to let you know that the nomination for \"{req.subject}\" has been "
        "withdrawn. No action is needed on your part.\n\n"
        "We may be in touch again should the requirement be re-opened.\n\n"
        "Regards,\nMeril Life Sciences — Global SCM"
    )
    msg = EmailMultiAlternatives(
        f"Update on your nomination — {req.subject}",
        text_body, settings.DEFAULT_FROM_EMAIL, [vendor_email],
    )
    msg.attach_alternative(html_body, "text/html")
    mailer.attach_logo(msg)
    _send_async(msg)
    return True


def other_bidder_contacts(award):
    """Every vendor on this request except the winner, deduped by email.

    Deduped because a vendor can submit several revisions of the same bid —
    three letters to one company for one request would read as an automated
    system nobody is minding. Keyed by lowercased email since that is the only
    identity the losing side reliably has: a bid may have no registration and
    no Vendor row, but it always has a submitter.
    """
    winner = (award.vendor_email or "").strip().lower()
    seen = {}
    bids = (
        VendorBid.objects
        .filter(vendor_request_id=award.vendor_request_id)
        .exclude(pk=award.bid_id)
        .select_related("vendor_registration", "vendor_registration__vendor", "submitted_by")
        .order_by("submitted_at")
    )
    for bid in bids:
        name, email, _registration, _vendor = vendor_contact(bid)
        key = email.strip().lower()
        if not key or key == winner or key in seen:
            continue
        seen[key] = (name, email)
    return list(seen.values())


def notify_unsuccessful_bidders(request, award):
    """Send the regret letter to everyone who bid and did not win.

    Returns the number of vendors written to. Every recipient is claimed in the
    database *before* any mail leaves, using BidOutcomeNotice's uniqueness
    constraint — so a retry, a double-submitted award or a later re-award
    cannot send a second letter to the same vendor on the same request. This
    is what lets it be called on every award unconditionally: a vendor bumped
    by a re-award is, from here, indistinguishable from any other bidder who
    did not win, and gets exactly the same one letter the first time they land
    in that set.

    All the messages go down a single SMTP connection rather than one
    connection each: this runs inline with the award being made, on a page
    someone is sat looking at, and six separate handshakes would be six times
    the latency.
    """
    contacts = other_bidder_contacts(award)
    if not contacts:
        return 0

    req = award.vendor_request
    claimed = []
    for name, email in contacts:
        notice, created = BidOutcomeNotice.objects.get_or_create(
            vendor_request=req, vendor_email=email,
            defaults={"vendor_name": name, "kind": BidOutcomeNotice.KIND_REGRET},
        )
        # An existing row means this vendor has already been told the outcome
        # -- nothing more is owed.
        if created:
            claimed.append((notice, name, email))

    if not claimed:
        return 0

    context_base = {
        "req": req,
        "raised_by_name": _sender_name(award.awarded_by),
        "raised_by_email": getattr(award.awarded_by, "email", ""),
    }
    subject = f'Meril VendorLink — thank you for your bid on "{req.subject}"'
    messages_out = []
    for _notice, name, email in claimed:
        html_body = render_to_string(
            "vendorlink/email/bid_not_selected.html",
            dict(context_base, vendor_name=name),
        )
        # Says nothing about another vendor being chosen, how many bids there
        # were, or where this one placed -- see the comment in
        # bid_not_selected.html. Keep the two bodies saying the same thing:
        # a mail client that shows the plain part must not be told more.
        text_body = (
            f"Dear {name},\n\n"
            f'Thank you for submitting a proposal for "{req.subject}".\n\n'
            "We regret to inform you that we are unable to proceed with your proposal "
            "on this occasion. This is in no way a reflection of the quality of your "
            "offering.\n\n"
            "We would be glad to have you bid on future opportunities.\n\n"
            "With appreciation,\nMeril Life Sciences — Global SCM"
        )
        message = EmailMultiAlternatives(
            subject, text_body, settings.DEFAULT_FROM_EMAIL, [email],
        )
        message.attach_alternative(html_body, "text/html")
        # cid: in the footer needs the image on the message — see vendorlink.mailer.
        mailer.attach_logo(message)
        messages_out.append(message)

    stamps = [notice for notice, _name, _email in claimed]
    try:
        connection = get_connection(fail_silently=False)
        connection.send_messages(messages_out)
    except Exception as exc:                                     # noqa: BLE001
        # The rows stay, with the failure on them. Not retried automatically:
        # a partial batch has no way to say which messages got out, and
        # re-running the whole thing risks a duplicate letter, which is the one
        # outcome this function exists to prevent.
        logger.exception("Regret letters failed for request %s", req.pk)
        BidOutcomeNotice.objects.filter(pk__in=[n.pk for n in stamps]).update(error=str(exc))
        return 0

    BidOutcomeNotice.objects.filter(pk__in=[n.pk for n in stamps]).update(
        sent_at=timezone.now(), error="",
    )
    return len(stamps)


def _back_to(request, fallback_url):
    """Where to send a buyer after an award or nomination action.

    Shared by bid-scoped actions (award_create, award_withdraw -- fallback is
    that bid's own detail page) and request-scoped ones (close/reopen
    nominations -- fallback is Bid Analytics, since there is no single bid to
    land on). Bouncing someone away from the screen they were working on loses
    their place mid-comparison, so the posted ``next`` wins whenever it is
    actually safe: validated against this host before it is trusted, since an
    unchecked redirect target taken from a form field is an open redirect.
    """
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect(fallback_url)


def _forbid(request, fallback_url):
    messages.error(request, "You do not have permission to manage the award on this request.")
    return _back_to(request, fallback_url)


@login_required
@require_POST
def close_nominations(request, pk):
    """Stop new vendors being awarded on this request, until reopened.

    Scoped to the request rather than a bid: this is a decision about the RFI
    as a whole ("we are done considering new nominations"), not about any one
    quote. Deliberately independent of the bid submission window -- see the
    module docstring in models.BidAward -- so closing this has no effect on
    whether vendors can still send in bids; it only stops staff from awarding
    one.

    Does not touch an existing award. A request can be awarded and then have
    nominations closed (locking that decision in against being changed), or
    have nominations closed with nothing awarded at all (the RFI is simply
    done, no vendor was chosen).
    """
    req = get_object_or_404(VendorRequest, pk=pk)
    if not can_manage_award(request.user, req):
        return _forbid(request, reverse("vendorlink:bid_analytics"))
    if req.nominations_open:
        req.nomination_closed_at = timezone.now()
        req.nomination_closed_by = request.user
        req.save(update_fields=["nomination_closed_at", "nomination_closed_by"])
        messages.success(request, f'Nominations closed for "{req.subject}". No new vendor can be awarded until reopened.')
    return _back_to(request, reverse("vendorlink:bid_analytics"))


@login_required
@require_POST
def reopen_nominations(request, pk):
    """Undo close_nominations — the symmetric correction, same as Withdraw is
    for an individual award."""
    req = get_object_or_404(VendorRequest, pk=pk)
    if not can_manage_award(request.user, req):
        return _forbid(request, reverse("vendorlink:bid_analytics"))
    if not req.nominations_open:
        req.nomination_closed_at = None
        req.nomination_closed_by = None
        req.save(update_fields=["nomination_closed_at", "nomination_closed_by"])
        messages.success(request, f'Nominations reopened for "{req.subject}".')
    return _back_to(request, reverse("vendorlink:bid_analytics"))


@login_required
@require_POST
def award_create(request, pk):
    """Award this bid's request to its vendor, and tell everyone where they stand.

    Re-awarding the same request to a different bid overwrites the award
    rather than adding a second, because a request has one outcome — there is
    no vendor-side answer left to wait for, so nothing blocks it. The bidder
    who held the award a moment ago is covered by the regret sweep below, the
    same as anyone else who did not win.
    """
    bid = get_object_or_404(
        VendorBid.objects.select_related(
            "vendor_request", "vendor_registration", "vendor_registration__vendor", "submitted_by",
        ),
        pk=pk,
    )
    req = bid.vendor_request
    if not can_manage_award(request.user, req):
        return _forbid(request, reverse("vendorlink:bid_detail", args=[bid.pk]))
    existing = BidAward.objects.filter(vendor_request=req).first()

    # Independent of the bid SUBMISSION window (bid_open_at / bid_close_at):
    # that window governs when vendors may still send in a quote, not when
    # staff may act on the quotes already in hand. Awarding is exactly the
    # decision that happens after the deadline while evaluation continues, so
    # it is gated only on nominations_open -- a separate, explicit close that
    # stays open until someone on this side closes it.
    if not req.nominations_open:
        closer = req.nomination_closed_by.get_full_name() if req.nomination_closed_by else None
        closer = closer or (req.nomination_closed_by.username if req.nomination_closed_by else "a staff member")
        messages.error(
            request,
            f'Nominations for "{req.subject}" were closed by {closer} on '
            f'{timezone.localtime(req.nomination_closed_at):%d %b %Y, %H:%M}. '
            "Reopen them before awarding a vendor.",
        )
        return _back_to(request, reverse("vendorlink:bid_detail", args=[bid.pk]))

    name, email, registration, vendor = vendor_contact(bid)
    total = bid_total(bid)

    fields = {
        "bid": bid,
        "vendor": vendor,
        "vendor_registration": registration,
        "vendor_email": email,
        "vendor_name": name,
        "awarded_amount": total or None,
        "awarded_currency": (bid.currency or "").upper(),
        "awarded_by": request.user,
        "notified_at": None,
        "notify_error": "",
    }

    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.save()
        award = existing
    else:
        award = BidAward.objects.create(vendor_request=req, **fields)

    if send_award_notice(request, award):
        messages.success(
            request,
            f"Awarded to {award.vendor_name}. A notice has gone to {award.vendor_email}.",
        )
    else:
        messages.warning(
            request,
            f"Awarded to {award.vendor_name}, but the notice mail could not be sent "
            f"({award.notify_error or 'no email address on file'}).",
        )

    # Always run, not only on a change of winner: a bidder who submitted after
    # the last award, or was missed by a prior failed send, still needs their
    # letter, and BidOutcomeNotice's uniqueness makes a repeat call a no-op for
    # everyone already told.
    try:
        notify_unsuccessful_bidders(request, award)
    except Exception:                                            # noqa: BLE001
        logger.exception("Regret sweep failed after award %s", award.pk)

    return _back_to(request, reverse("vendorlink:bid_detail", args=[bid.pk]))


@login_required
@require_POST
def award_withdraw(request, pk):
    """Drop an award so the request can be awarded to someone else.

    Sends the withdrawn vendor a short notice before the row is deleted --
    there is no other record of who held it once this returns. Kept a
    separate, explicit action rather than a side effect of awarding someone
    else: taking a live award away from a vendor with no one yet lined up to
    replace them is a decision worth its own deliberate click.
    """
    award = get_object_or_404(BidAward.objects.select_related("vendor_request"), pk=pk)
    if not can_manage_award(request.user, award.vendor_request):
        return _forbid(request, reverse("vendorlink:bid_detail", args=[award.bid_id]))
    bid_pk = award.bid_id
    name = award.vendor_name
    send_withdrawal_notice(request, award.vendor_name, award.vendor_email, award.vendor_request)
    award.delete()
    messages.success(request, f"Nomination for {name} withdrawn. The request can now be awarded again.")
    return _back_to(request, reverse("vendorlink:bid_detail", args=[bid_pk]))
