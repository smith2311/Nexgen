import io
import json
import re
import hashlib
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.db import transaction
# RestrictedError sits beside ProtectedError: Django raises it for
# on_delete=RESTRICT, it shares no base with ProtectedError beyond
# Exception, and catching only one leaves the other an unhandled 500.
from django.db.models import (
    Count, F, Prefetch, ProtectedError, Q, RestrictedError, Sum,
)
from django.core.exceptions import ValidationError
from django.contrib import messages
from main.utils import invalidate_user_sessions

from . import routing, userprogress

_EMAIL_RE    = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_ACRONYM_SKIP = {'and', '&', 'the', 'of', 'for', 'a', 'an', 'in', 'at', 'to'}

_BLOCKED_EMAIL_DOMAINS = frozenset({
    'gmail.com', 'gmail.co.in', 'googlemail.com',
    'yahoo.com', 'yahoo.in', 'yahoo.co.in', 'yahoo.co.uk',
    'outlook.com', 'outlook.in',
    'hotmail.com', 'hotmail.in', 'hotmail.co.uk',
    'rediffmail.com',
    'live.com', 'live.in', 'live.co.uk',
    'icloud.com', 'me.com', 'mac.com',
    'aol.com', 'ymail.com', 'msn.com', 'protonmail.com', 'mail.com',
})
_BLOCKED_DOMAIN_MSG = 'Personal email domains (Gmail, Yahoo, Outlook, Hotmail, etc.) are not allowed. Please use a company email address.'


def _parse_emails(raw):
    """Return a list of valid email addresses from a comma/semicolon/newline separated string."""
    parts = re.split(r'[,;\s]+', (raw or '').strip())
    return [e.strip() for e in parts if e.strip() and _EMAIL_RE.match(e.strip())]


def _is_blocked_domain(email):
    """Return True if the email uses a consumer/personal domain."""
    if '@' not in email:
        return False
    return email.lower().rsplit('@', 1)[-1] in _BLOCKED_EMAIL_DOMAINS


def _first_blocked_email(raw):
    """Return the first blocked-domain address in a comma-separated field, or None."""
    for e in _parse_emails(raw):
        if _is_blocked_domain(e):
            return e
    return None


from .models import (
    Company, Country, Department, DispositionBatch, DispositionItem,
    RequestedByOption, ReworkLocation, ReworkType, Vertical,
)


def _make_acronym(text):
    words = re.sub(r'[^a-zA-Z\s]', '', text).split()
    return ''.join(w[0].upper() for w in words if w.lower() not in _ACRONYM_SKIP and w) or 'MD'


def _generate_reference_number(batch):
    year = timezone.now().year
    acronym = _make_acronym(batch.vertical.name)
    return f"{acronym}-{year}{batch.pk:07d}"


def _parse_qty_int(raw, default=0):
    try:
        value = Decimal(str(raw).strip() if raw is not None else default)
    except (InvalidOperation, ValueError):
        value = Decimal(default)
    value = value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return max(int(value), 0)


def _validate_qty_int(raw):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if value != value.to_integral_value() or value < 0:
        return None
    return int(value)


# ── Permission helpers ──────────────────────────────────────────

def _is_md_admin(user):
    """Full administrative access across Material Rework."""
    if not user or not user.is_authenticated:
        return False
    return (
        user.is_superuser
        or user.groups.filter(name__in=['Material Rework Admin', ROOT_USER_GROUP]).exists()
    )


def _is_production(user):
    """Can VIEW the production section (admin + production group).

    Every ``_is_<stage>`` below reads ``_is_md_admin`` rather than naming
    'Material Rework Admin' in a group list of its own. The list spelling was
    narrower than the edit check beside it in a way nobody would choose: it
    admitted that one group and not the other two things _is_md_admin admits,
    so a SUPERUSER and the module's own ``Root User`` were refused the
    production, warehouse, OM and PPIC sections outright — while
    ``_can_production_edit`` and friends were happy to let both ACT there.
    Being allowed to approve a batch you are not allowed to open is not a
    permission model, and it is why running this module without a superuser
    account still did not work: Root User exists precisely to be that
    administrator and could not reach four of the seven screens.
    """
    return _is_md_admin(user) or user.groups.filter(name='MR Production').exists()


def _can_production_edit(user):
    """Can submit production reviews."""
    return _is_md_admin(user) or user.groups.filter(name='MR Production').exists()


def _is_warehouse(user):
    """Can VIEW the warehouse section (admin + warehouse group).

    See _is_production for why this reads _is_md_admin rather than naming the
    admin group itself.
    """
    return _is_md_admin(user) or user.groups.filter(name='MR Warehouse').exists()


def _can_warehouse_edit(user):
    """Can mark warehouse release/receipt actions."""
    return _is_md_admin(user) or user.groups.filter(name='MR Warehouse').exists()


def _can_hod_action(user):
    """Can approve/reject HOD batches."""
    return (
        _is_md_admin(user)
        or bool(_get_hod_departments(user))
        or user.groups.filter(name='MR HOD').exists()
    )


def _is_om(user):
    """Can VIEW the Order Management section (admin + OM group).

    See _is_production for why this reads _is_md_admin rather than naming the
    admin group itself.
    """
    return _is_md_admin(user) or user.groups.filter(name='MR Order Management').exists()


def _can_om_edit(user):
    """Can approve/reject at the OM gate."""
    return _is_md_admin(user) or user.groups.filter(name='MR Order Management').exists()


#: Which group may act at each gate, so one lookup serves any stage rather
#: than a parallel if/elif per stage that has to be kept in step with
#: routing.STAGE_META.
_STAGE_EDIT_CHECKS = {
    routing.STAGE_OM: _can_om_edit,
}


def _is_ppic(user):
    """Can VIEW the PPIC section (admin + PPIC group).

    See _is_production for why this reads _is_md_admin rather than naming the
    admin group itself.
    """
    return _is_md_admin(user) or user.groups.filter(name='MR PPIC').exists()


def _can_ppic_edit(user):
    """Can approve/reject in PPIC."""
    return _is_md_admin(user) or user.groups.filter(name='MR PPIC').exists()


_STAGE_EDIT_CHECKS[routing.STAGE_PPIC] = _can_ppic_edit


def _is_merillife(user):
    allowed = {'merillife.com', 'meril.com', 'meril.in', 'merillife.in'}
    return user.is_authenticated and (
        user.is_superuser or
        (user.email and user.email.split('@')[-1] in allowed)
    )


# ── Routing Rules admin ──────────────────────────────────────────────────
# Where the flows are decided. One screen listing every rule, with an inline
# editor per rule and a form to add one.
#
# Restricted to Material Rework Admin: a routing rule decides which teams see
# a batch at all, so it is a stronger permission than the other masters on
# that screen.

@login_required
def routing_rules(request):
    from .models import RoutingRule

    if not _is_md_admin(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        errors = []

        if action == 'save':
            rule_id = request.POST.get('rule_id', '').strip()
            key_kind = request.POST.get('key_kind', '').strip()
            key_id = request.POST.get('key_id', '').strip()
            # An ordered list: the stage picker submits one hidden input per
            # position, so the order the admin arranged is the order received.
            stages = [s for s in request.POST.getlist('stages') if s.strip()]
            is_active = request.POST.get('is_active') == 'on'
            notes = request.POST.get('notes', '').strip()

            rule = None
            if rule_id:
                rule = RoutingRule.objects.filter(pk=rule_id).first()
                if rule is None:
                    errors.append('That routing rule no longer exists.')

            if not errors and rule is None:
                if key_kind not in ('requested_by', 'rework_type'):
                    errors.append('Choose whether the rule keys on Requested By or Type of Rework.')
                elif not key_id:
                    errors.append('Choose which option the rule applies to.')

            if not errors:
                try:
                    stages = routing.validate_chain(stages)
                except routing.RoutingError as exc:
                    errors.append(str(exc))

            if not errors:
                if rule is None:
                    rule = RoutingRule(
                        requested_by_id=key_id if key_kind == 'requested_by' else None,
                        rework_type_id=key_id if key_kind == 'rework_type' else None,
                    )
                    # One rule per key: a second rule for the same option would
                    # make which one wins depend on row order.
                    clash = RoutingRule.objects.filter(
                        requested_by_id=rule.requested_by_id,
                        rework_type_id=rule.rework_type_id,
                    ).exists()
                    if clash:
                        errors.append(
                            'A rule for that option already exists — edit it instead of adding a second one.'
                        )
                if not errors:
                    rule.stages = stages
                    rule.is_active = is_active
                    rule.notes = notes[:300]
                    try:
                        rule.full_clean()
                        rule.save()
                        messages.success(
                            request,
                            f'Flow saved: {rule.key_label} → {rule.chain_label}',
                        )
                        return redirect('material_rework:routing_rules')
                    except ValidationError as exc:
                        errors.extend(
                            str(m) for msgs in exc.message_dict.values() for m in msgs
                        )

        elif action == 'delete':
            rule = RoutingRule.objects.filter(pk=request.POST.get('rule_id', '').strip()).first()
            if rule is None:
                errors.append('That routing rule no longer exists.')
            else:
                label = rule.key_label
                rule.delete()
                # Batches already created keep their own frozen chain, so
                # deleting a rule never disturbs work in flight — it only
                # changes where future uploads go.
                messages.success(
                    request,
                    f'Flow for {label} deleted. Batches already in progress keep the flow '
                    f'they started on; new uploads will fall back to the default flow '
                    f'({routing.describe(routing.DEFAULT_CHAIN)}).',
                )
                return redirect('material_rework:routing_rules')

        for message in errors:
            messages.error(request, message)
        return redirect('material_rework:routing_rules')

    rules = list(
        RoutingRule.objects.select_related('requested_by', 'rework_type')
        .order_by('rework_type__name', 'requested_by__name')
    )
    for rule in rules:
        # Precomputed so the template does not call a property per stage per
        # row, and so the chain renders in one place.
        rule.stage_labels = [routing.label(s) for s in (rule.stages or [])]

    # Options that have no rule yet — worth surfacing, because an unruled
    # requester silently takes the default flow rather than erroring.
    requested_by_opts = list(RequestedByOption.objects.filter(is_active=True))
    rework_type_opts = list(ReworkType.objects.filter(is_active=True))
    ruled_rb = {r.requested_by_id for r in rules if r.requested_by_id}
    ruled_rt = {r.rework_type_id for r in rules if r.rework_type_id}

    return render(request, 'material_rework/routing_rules.html', {
        'rules': rules,
        'type_rules': [r for r in rules if r.rework_type_id],
        'requester_rules': [r for r in rules if r.requested_by_id],
        'requested_by_opts': requested_by_opts,
        'rework_type_opts': rework_type_opts,
        'unruled_requested_by': [o for o in requested_by_opts if o.pk not in ruled_rb],
        'unruled_rework_type': [o for o in rework_type_opts if o.pk not in ruled_rt],
        'stage_choices': routing.STAGE_CHOICES,
        'gate_stages': routing.GATE_STAGES,
        'terminal_stages': routing.TERMINAL_STAGES,
        'stage_labels': routing.STAGE_LABELS,
        'default_chain_label': routing.describe(routing.DEFAULT_CHAIN),
    })


# ── Root User role ──────────────────────────────────────────────
# "Root User" is a cross-module super-admin group. Members have the same
# rights as a Django superuser inside Material Rework and Warehouse
# Inspection, but without global Django admin access.
ROOT_USER_GROUP = 'Root User'


def _is_root_user(user):
    """Return True if the user is in the Root User group."""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=ROOT_USER_GROUP).exists()


def _get_root_user():
    """Return the single active Root User, or None. Only one is allowed at a time."""
    from django.contrib.auth.models import User as _User
    return _User.objects.filter(groups__name=ROOT_USER_GROUP, is_active=True).first()


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _ajax_messages(request):
    from django.contrib.messages import get_messages as _gm
    return [{'tags': str(m.tags), 'text': str(m)} for m in _gm(request)]


def _approval_link_claimed_by_current_session(request, batch):
    if not request.session.session_key:
        request.session.create()

    session_key = request.session.session_key
    if batch.approval_token_session_key:
        return batch.approval_token_session_key == session_key

    batch.approval_token_session_key = session_key
    batch.approval_token_claimed_at = timezone.now()
    batch.save(update_fields=['approval_token_session_key', 'approval_token_claimed_at'])
    return True


# ── Email helpers ───────────────────────────────────────────────

def _send_hod_email(request, batch):
    recipients = _parse_emails(batch.department.hod_email)
    if not recipients:
        return
    review_url = request.build_absolute_uri(
        f'/material-rework/hod-review/{batch.approval_token}/'
    )
    items = batch.items.all()
    ctx = {'batch': batch, 'items': items, 'review_url': review_url}

    html_body  = render_to_string('material_rework/email/hod_notification.html', ctx)
    plain_body = (
        f"Dear HOD,\n\n"
        f"A new material rework request requires your approval.\n\n"
        f"Reference  : {batch.reference_number or batch.pk}\n"
        f"Vertical   : {batch.vertical.name}\n"
        f"Department : {batch.department.name}\n"
        f"Uploaded by: {batch.uploaded_by_name} ({batch.uploaded_by_email})\n"
        f"Date       : {batch.uploaded_at.strftime('%d %b %Y %H:%M')}\n\n"
        f"Items ({len(items)}):\n" +
        '\n'.join(
            f"  {i+1}. [{it.serial_number or '—'}] {it.material} | Batch: {it.batch_no} | Qty: {it.qty}"
            f" | Box: {it.box_no} | Country: {it.country or '—'}"
            f" | Mfg: {it.mfg_date or '—'} | Exp: {it.exp_date or '—'}"
            f" | From: {it.from_storage_location or '—'} | To: {it.to_storage_location or '—'}"
            f" | Uploaded At: {it.item_date or '—'} | Remark: {it.remark or '—'}"
            for i, it in enumerate(items)) +
        f"\n\nReview & approve at:\n{review_url}\n\n"
        f"CONFIDENTIAL ACTION LINK: Do not forward this email to anyone. If this email/link is forwarded and accessed by another person, the forwarder will be responsible and liable for any action taken through the forwarded link.\n\n"
        f"Regards,\nNexGen Automation"
    )

    email = EmailMultiAlternatives(
        subject=batch.subject or f'[MD Approval] {batch.reference_number or batch.pk} — {batch.department.name}',
        body=plain_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@merillife.com'),
        to=recipients,
    )
    email.attach_alternative(html_body, 'text/html')
    try:
        email.send()
    except Exception:
        pass


def _send_ppic_email(request, batch):
    recipients = _parse_emails(batch.department.ppic_email)
    if not recipients:
        return
    items = batch.items.all()
    plain_body = (
        f"Dear PPIC Team,\n\n"
        f"A new material rework request (Order Management) requires PPIC review.\n\n"
        f"Reference  : {batch.reference_number or batch.pk}\n"
        f"Vertical   : {batch.vertical.name}\n"
        f"Department : {batch.department.name}\n"
        f"Uploaded by: {batch.uploaded_by_name} ({batch.uploaded_by_email})\n"
        f"Items      : {items.count()}\n\n"
        f"Please log in to review this batch.\n\n"
        f"Regards,\nNexGen Automation"
    )
    email = EmailMultiAlternatives(
        subject=batch.subject or f'[MR PPIC] {batch.reference_number or batch.pk} — {batch.department.name}',
        body=plain_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@merillife.com'),
        to=recipients,
    )
    try:
        email.send()
    except Exception:
        pass


def _send_om_email(request, batch):
    """Tell Order Management a batch is waiting at their gate.

    The body is assembled as a list of lines rather than one long f-string so
    the flow itself can be printed: an OM reviewer seeing "Order Management →
    PPIC → Production" knows a second gate follows theirs, which is the
    question the old single-flow mails never had to answer.
    """
    recipients = _parse_emails(batch.department.om_email)
    if not recipients:
        return
    lines = [
        "Dear Order Management Team,",
        "",
        "A new material rework request requires your review.",
        "",
        "Reference     : %s" % (batch.reference_number or batch.pk),
        "Vertical      : %s" % batch.vertical.name,
        "Department    : %s" % batch.department.name,
        "Requested by  : %s" % (batch.requested_by.name if batch.requested_by else "-"),
        "Type of rework: %s" % (batch.rework_type.name if batch.rework_type else "-"),
        "Approval flow : %s" % routing.describe(batch.routing_chain),
        "Uploaded by   : %s (%s)" % (batch.uploaded_by_name, batch.uploaded_by_email),
        "Items         : %s" % batch.items.count(),
        "",
        "Please log in to review this batch.",
        "",
        "Regards,",
        "NexGen Automation",
    ]
    email = EmailMultiAlternatives(
        subject=batch.subject or "[MR OM] %s - %s" % (
            batch.reference_number or batch.pk, batch.department.name,
        ),
        body="\n".join(lines),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@merillife.com'),
        to=recipients,
    )
    try:
        email.send()
    except Exception:
        # Matches every other sender in this module: a batch that is correctly
        # routed must not fail to exist because a notification could not go
        # out. The batch is already committed and visible in the OM inbox.
        pass


def _send_production_pending_email(request, batch):
    """Tell production a batch has reached their review.

    Production previously found work only by opening its own list, because a
    batch could arrive there from just one predecessor. With the chain
    configurable it can now arrive from OM, from PPIC or straight from upload,
    so the arrival is announced the way every other stage's is.

    Distinct from _send_production_review_email, which reports a review that
    has already happened.
    """
    recipients = _parse_emails(batch.department.production_review_email)
    if not recipients:
        return
    lines = [
        "Dear Production Team,",
        "",
        "A material rework batch is ready for your review.",
        "",
        "Reference     : %s" % (batch.reference_number or batch.pk),
        "Vertical      : %s" % batch.vertical.name,
        "Department    : %s" % batch.department.name,
        "Requested by  : %s" % (batch.requested_by.name if batch.requested_by else "-"),
        "Type of rework: %s" % (batch.rework_type.name if batch.rework_type else "-"),
        "Priority      : %s" % batch.get_priority_display(),
        "Approval flow : %s" % routing.describe(batch.routing_chain),
        "Items         : %s" % batch.items.count(),
        "",
        "Please log in to record accepted quantities and book the rework slot.",
        "",
        "Regards,",
        "NexGen Automation",
    ]
    email = EmailMultiAlternatives(
        subject=batch.subject or "[MR Production] %s - %s" % (
            batch.reference_number or batch.pk, batch.department.name,
        ),
        body="\n".join(lines),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@merillife.com'),
        to=recipients,
    )
    try:
        email.send()
    except Exception:
        pass


def _notify_stage(request, batch, stage):
    """Send the one notification that ``stage`` expects.

    A single place that maps stage → sender, so a new stage cannot be routed
    to and then sit silently in an inbox nobody was told about. The warehouse
    terminal is the exception that takes an item list, because its mail names
    the lines to release.
    """
    if stage == routing.STAGE_OM:
        _send_om_email(request, batch)
    elif stage == routing.STAGE_PPIC:
        _send_ppic_email(request, batch)
    elif stage == routing.STAGE_PRODUCTION:
        _send_production_pending_email(request, batch)
    elif stage == routing.STAGE_WAREHOUSE:
        pending = list(batch.items.filter(status='pending_rework_handover'))
        if pending:
            _send_warehouse_email(request, batch, pending)


def _enter_stage(request, batch, stage, notify=True):
    """Put ``batch`` at ``stage`` and move its lines to match.

    The batch status, the line statuses and ``current_stage`` are written
    together — three representations of one fact, and a batch whose lines say
    "pending PPIC" while the batch says "pending OM" appears in two inboxes at
    once and gets reviewed twice.

    The Warehouse terminal is special: there is no review there, so the lines
    enter the rework round trip directly with the full quantity accepted. That
    is only ever reached by a flow whose requester is Production, which is to
    say the team that would otherwise have done the accepting has already
    decided by raising the request.
    """
    batch.current_stage = stage
    batch.status = routing.batch_status_for(stage)

    if stage == routing.STAGE_WAREHOUSE:
        # accepted_qty = qty because nobody downstream will set it: the
        # Production Review screen is skipped entirely on this flow.
        #
        # One UPDATE rather than a save() per line. Every line gets the same
        # three values unconditionally, and accepted_qty is a copy of a column
        # on the same row, so F('qty') does it in the database — a 400-line
        # batch was 401 queries here and is now one. The `else` branch below
        # already worked this way; this branch was the odd one out.
        batch.items.update(
            accepted_qty=F('qty'),
            pending_qty=0,
            status='pending_rework_handover',
        )
    else:
        batch.items.exclude(
            status__in=('rejected', 'received', 'received_discrepancy'),
        ).update(status=routing.item_status_for(stage))

    batch.save()
    if notify:
        _notify_stage(request, batch, stage)
    return stage


def _advance_batch(request, batch, from_stage):
    """Move a batch to the stage after ``from_stage`` on its own chain.

    Returns the stage entered, or None when ``from_stage`` was genuinely the
    last stage on the chain — the caller then owns whatever "finished" means.

    Reads ``batch.routing_chain``, never the RoutingRule table: the chain was
    frozen at upload precisely so that an admin reordering a flow cannot
    re-route a batch mid-journey.

    A batch whose chain does not contain ``from_stage`` at all falls through to
    Production review rather than going nowhere. That covers batches created
    before the routing engine existed, which carry no chain: leaving them where
    they are would strand the lines at an approved gate, in a status the next
    screen does not query, with nothing raised to say so. Production review is
    the safe destination — it is where such a batch went under the old rules,
    and it is a review, so nothing is skipped by landing there.
    """
    chain = list(batch.routing_chain or [])
    if from_stage not in chain:
        return _enter_stage(request, batch, routing.STAGE_PRODUCTION)

    nxt = routing.next_stage(chain, from_stage)
    if nxt is None:
        return None
    return _enter_stage(request, batch, nxt)


def _send_warehouse_email(request, batch, pending_items):
    recipients = _parse_emails(batch.department.warehouse_email)
    if not recipients:
        return
    warehouse_url = request.build_absolute_uri('/material-rework/warehouse/')
    ctx = {'batch': batch, 'pending_items': pending_items, 'warehouse_url': warehouse_url}

    html_body  = render_to_string('material_rework/email/warehouse_notification.html', ctx)
    plain_body = (
        f"Dear Warehouse Team,\n\n"
        f"Production has reviewed {batch.reference_number or batch.pk} and items are pending receipt.\n\n"
        f"Vertical   : {batch.vertical.name}\n"
        f"Department : {batch.department.name}\n\n"
        f"Pending Items:\n" +
        '\n'.join(
            f"  - [{it.serial_number or '—'}] {it.material} | Batch: {it.batch_no} | Pending Qty: {it.pending_qty}"
            f" | Box: {it.box_no} | Country: {it.country or '—'}"
            f" | Mfg: {it.mfg_date or '—'} | Exp: {it.exp_date or '—'}"
            f" | From: {it.from_storage_location or '—'} | To: {it.to_storage_location or '—'}"
            f" | Uploaded At: {it.item_date or '—'}"
            for it in pending_items) +
        f"\n\nLog in to receive these items:\n{warehouse_url}\n\n"
        f"CONFIDENTIAL ACTION LINK: Do not forward this email to anyone. If this email/link is forwarded and accessed by another person, the forwarder will be responsible and liable for any action taken through the forwarded link.\n\n"
        f"Regards,\nNexGen Automation"
    )

    email = EmailMultiAlternatives(
        subject=batch.subject or f'[MD Warehouse] Pending items — {batch.reference_number or batch.pk}',
        body=plain_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@merillife.com'),
        to=recipients,
    )
    email.attach_alternative(html_body, 'text/html')
    try:
        email.send()
    except Exception:
        pass


def _send_production_review_email(request, batch):
    """
    Triggered when production review is completed.
    Notifies: department's production_review_email + uploader + HOD emails.
    """
    recipients = set()
    for e in _parse_emails(batch.department.production_review_email):
        recipients.add(e)
    if batch.uploaded_by_email:
        recipients.add(batch.uploaded_by_email)
    for e in _parse_emails(batch.department.hod_email):
        recipients.add(e)
    recipients = list(recipients)
    if not recipients:
        return

    items = batch.items.all()
    ctx   = {'batch': batch, 'items': items}
    html_body  = render_to_string('material_rework/email/production_review_notification.html', ctx)
    plain_body = (
        f"Production review completed for {batch.reference_number or batch.pk}.\n\n"
        f"Vertical   : {batch.vertical.name}\n"
        f"Department : {batch.department.name}\n"
        f"Reviewed by: {request.user.get_full_name() or request.user.username}\n\n"
        f"Items:\n" +
        '\n'.join(
            f"  - [{it.serial_number or '—'}] {it.material} | Batch: {it.batch_no}"
            f" | Accepted: {it.accepted_qty} | Pending WH: {it.pending_qty}"
            f" | Country: {it.country or '—'}"
            f" | Mfg: {it.mfg_date or '—'} | Exp: {it.exp_date or '—'}"
            f" | From: {it.from_storage_location or '—'} | To: {it.to_storage_location or '—'}"
            f" | Uploaded At: {it.item_date or '—'} | Remarks: {it.production_remarks or '—'}"
            for it in items
        ) +
        "\n\nCONFIDENTIAL EMAIL: Do not forward this email to anyone. If this email is forwarded and accessed by another person, the forwarder will be responsible and liable for any misuse of the information.\n\n"
        "Regards,\nNexGen Automation"
    )

    email = EmailMultiAlternatives(
        subject=batch.subject or f'[MD Production Review] {batch.reference_number or batch.pk} — Completed',
        body=plain_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@merillife.com'),
        to=recipients,
    )
    email.attach_alternative(html_body, 'text/html')
    try:
        email.send()
    except Exception:
        pass


# ── Template validation error ────────────────────────────────────

class TemplateValidationError(ValueError):
    """Raised when the uploaded Excel file does not match the expected template."""
    pass


#: Batch statuses that mean "cleared for production".
#:
#: Two of them because there are two approval routes and only one production
#: queue: an Order Management batch is cleared by PPIC (``ppic_approved``) and
#: goes straight there, everything else is cleared by HOD (``hod_approved``).
#: Kept as one constant so the production list, the review screen and the
#: dashboard counter cannot disagree about which batches are waiting.
#: Batch statuses the Production Review screen will open.
#: ``pending_production`` is what the routing engine now sets. The other two
#: are the pre-routing statuses, which said who had just approved rather than
#: what was pending; kept so any batch created before the engine landed is
#: still reviewable rather than stranded.
PRODUCTION_READY_STATUSES = ('pending_production', 'hod_approved', 'ppic_approved')

#: Priority first, then oldest-first within a priority — so a High batch never
#: sits behind a Low one, and two High batches are still worked FIFO.
PRIORITY_ORDERING = ('priority', 'uploaded_at')

#: ``action`` -> (model, human label, POST field holding the name).
#: Country, Type of Rework and Location are all "a unique name plus an active
#: flag", so they share one CRUD handler in ``masters_view`` rather than three
#: copies of the same twenty lines.
_SIMPLE_MASTER_ACTIONS = {
    'add_country':          (Country, 'Country', 'country_name'),
    'edit_country':         (Country, 'Country', 'country_name'),
    'delete_country':       (Country, 'Country', 'country_name'),
    'add_rework_type':      (ReworkType, 'Type of Rework', 'rework_type_name'),
    'edit_rework_type':     (ReworkType, 'Type of Rework', 'rework_type_name'),
    'delete_rework_type':   (ReworkType, 'Type of Rework', 'rework_type_name'),
    'add_location':         (ReworkLocation, 'Location', 'location_name'),
    'edit_location':        (ReworkLocation, 'Location', 'location_name'),
    'delete_location':      (ReworkLocation, 'Location', 'location_name'),
}


# ── Excel parser ────────────────────────────────────────────────

#: Column order of the official upload template. The download endpoint, the
#: parser's fallback positions and the "wrong template" error message all read
#: from this one list, so adding a column here cannot leave the three of them
#: disagreeing about what the template looks like.
UPLOAD_TEMPLATE_COLUMNS = [
    'Serial Number', 'Country', 'From Country', 'To Country', 'Code', 'Batch',
    'Mfg Date', 'Exp Date', 'From Storage Location', 'To Storage Location',
    'Qty', 'Box No', 'Remarks', 'Uploaded At',
]


def _country_lookup():
    """``{normalised name: canonical master spelling}`` for active countries.

    Empty dict when the master has no rows, which callers treat as "country
    validation is not configured here" rather than "every country is wrong" —
    otherwise switching this on would block every upload on day one.
    """
    return {
        name.strip().casefold(): name
        for name in Country.objects.filter(is_active=True).values_list('name', flat=True)
        if name and name.strip()
    }


def _resolve_country(raw, lookup):
    """``(canonical_name, is_unknown)`` for one country cell.

    Case and surrounding whitespace are corrected silently against the master
    ("  india " -> "India"), because that is a typing artefact rather than a
    data error worth stopping an upload for. A name the master has never heard
    of is reported instead of guessed at — there is no safe way to tell
    "Germny" from a country that simply is not in the master yet.
    """
    value = (raw or '').strip()
    if not value or not lookup:
        return value, False
    canonical = lookup.get(value.casefold())
    if canonical is None:
        return value, True
    return canonical, False


def _parse_excel(file_obj):
    """Parse the disposition Excel file.

    Returns ``(items, row_errors)``. ``row_errors`` lists per-row problems
    (currently country spellings not present in the Country master) as
    operator-facing strings naming the row number; the caller decides whether
    to block the upload. Template-shape problems still raise
    ``TemplateValidationError``, because those cannot be reported per row —
    the whole file is the wrong shape.
    """
    wb = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    ws = wb.active
    rows_iter   = ws.iter_rows(values_only=True)
    header_row  = next(rows_iter, None) or []
    raw_headers = [str(c or '').strip() for c in header_row]
    headers     = [h.lower() for h in raw_headers]

    # Map each field to all accepted header aliases (exact, case-insensitive).
    FIELD_ALIASES = [
        ('serial_number',          ('serial number', 'serial_number', 'serial no', 'serial no.', 'sr no', 'sr. no', 'serial')),
        ('country',                ('country',)),
        ('from_country',           ('from country', 'from_country', 'origin country', 'source country')),
        ('to_country',             ('to country', 'to_country', 'destination country', 'dest country')),
        ('material',               ('code', 'material code', 'sap code', 'item code', 'product code')),
        ('batch_no',               ('batch', 'batch no', 'batch no.', 'batch number', 'batch_no')),
        ('mfg_date',               ('mfg date', 'mfg_date', 'mfg. date', 'manufacturing date', 'manufacture date')),
        ('exp_date',               ('exp date', 'exp_date', 'exp. date', 'expiry date', 'expiry', 'expiration date')),
        ('from_storage_location',  ('from storage location', 'from storage', 'from_storage_location', 'from location', 'source location')),
        ('to_storage_location',    ('to storage location', 'to storage', 'to_storage_location', 'to location', 'destination location')),
        ('qty',                    ('qty', 'quantity', 'qty.', 'quantity (unit)')),
        ('box_no',                 ('box no', 'box no.', 'box_no', 'box number', 'box')),
        ('remark',                 ('remarks', 'remark', 'remark/remarks', 'comments')),
        ('item_date',              ('uploaded at', 'uploaded_at', 'upload date', 'upload_date', 'date uploaded')),
    ]

    # Header -> item field. Declared before the header checks because both the
    # "is this required column present" loop and the positional fallback below
    # read it, and they must agree about what each template column means.
    _FIELD_BY_HEADER = {
        'Serial Number': 'serial_number', 'Country': 'country',
        'From Country': 'from_country', 'To Country': 'to_country',
        'Code': 'material', 'Batch': 'batch_no',
        'Mfg Date': 'mfg_date', 'Exp Date': 'exp_date',
        'From Storage Location': 'from_storage_location',
        'To Storage Location': 'to_storage_location',
        'Qty': 'qty', 'Box No': 'box_no', 'Remarks': 'remark',
        'Uploaded At': 'item_date',
    }
    ALIASES_BY_FIELD = dict(FIELD_ALIASES)

    #: Columns the file must actually contain. The rest may be left out and
    #: come back blank, but a missing column here is always operator error:
    #: 'Code'/'Qty' because a row means nothing without them, and the two
    #: country columns because the routing they describe cannot be inferred.
    REQUIRED_COLUMNS = ('Code', 'Qty', 'From Country', 'To Country')

    EXPECTED_COLUMNS = list(UPLOAD_TEMPLATE_COLUMNS)
    EXPECTED_COUNT = len(EXPECTED_COLUMNS)

    # Flat set of every known alias across all fields
    all_known_aliases = {alias for _, aliases in FIELD_ALIASES for alias in aliases}

    # ── Header validation ─────────────────────────────────────────
    non_empty_headers = [(i + 1, raw, h) for i, (raw, h) in enumerate(zip(raw_headers, headers)) if raw]

    # 1. Check for unrecognised columns
    unrecognised = [
        f'column {pos} ("{raw}")'
        for pos, raw, h in non_empty_headers
        if h not in all_known_aliases
    ]
    if unrecognised:
        raise TemplateValidationError(
            f"The file contains column(s) that are not part of the official template: "
            f"{', '.join(unrecognised)}. "
            f"Please download the official upload template and do not add, rename, or remove any columns. "
            f"The template must have exactly these {EXPECTED_COUNT} columns in order: "
            f"{', '.join(EXPECTED_COLUMNS)}."
        )

    # 2. Check for too many columns
    if len(non_empty_headers) > EXPECTED_COUNT:
        extra = len(non_empty_headers) - EXPECTED_COUNT
        raise TemplateValidationError(
            f"Your file has {len(non_empty_headers)} columns, but only {EXPECTED_COUNT} are allowed. "
            f"Please remove the {extra} extra column(s) and use the official upload template. "
            f"The template must have exactly these {EXPECTED_COUNT} columns: "
            f"{', '.join(EXPECTED_COLUMNS)}."
        )

    # 3. Check that every required column is present. Reported together so an
    #    old file missing both country columns takes one round trip, not two.
    missing = [
        f"'{header}' (column {EXPECTED_COLUMNS.index(header) + 1})"
        for header in REQUIRED_COLUMNS
        if not any(h in ALIASES_BY_FIELD[_FIELD_BY_HEADER[header]] for h in headers)
    ]
    if missing:
        plural = 's' if len(missing) > 1 else ''
        raise TemplateValidationError(
            f"The required column{plural} {', '.join(missing)} "
            f"{'are' if len(missing) > 1 else 'is'} missing from your file. "
            "Please download the official upload template and fill it in correctly."
        )

    # ── Build column-index map using exact header matching only ───
    col = {}
    for field, aliases in FIELD_ALIASES:
        for i, h in enumerate(headers):
            if h in aliases:
                col[field] = i
                break

    # Fallback positions match the expected column order (0-based), derived
    # from UPLOAD_TEMPLATE_COLUMNS so the two cannot drift apart.
    #
    # Only trusted when the file is the full expected width. On a narrower file
    # the positions no longer line up, and reading by position would quietly
    # pull the wrong column — a 12-column pre-country file would have served
    # "Code" as From Country and "Batch" as To Country, then blamed the
    # operator for a country spelling it never typed.
    FALLBACK = {}
    if len(non_empty_headers) == EXPECTED_COUNT:
        FALLBACK = {
            _FIELD_BY_HEADER[h]: i
            for i, h in enumerate(EXPECTED_COLUMNS)
            if h in _FIELD_BY_HEADER
        }

    def _cell(row, key, default=''):
        idx = col.get(key, FALLBACK.get(key))
        if idx is None or idx >= len(row):
            return default
        return str(row[idx] or '').strip()

    country_lookup = _country_lookup()
    items      = []
    row_errors = []
    # Row 1 is the header, so data starts at spreadsheet row 2 — the number
    # reported back is the row the operator sees in Excel, not a 0-based index.
    for excel_row, row in enumerate(rows_iter, start=2):
        if not any(row):
            continue
        material = _cell(row, 'material')      # Code column — required
        if not material:
            continue
        qty = _parse_qty_int(_cell(row, 'qty') or 0)

        resolved = {}
        for field, label in (
            ('country', 'Country'),
            ('from_country', 'From Country'),
            ('to_country', 'To Country'),
        ):
            value, unknown = _resolve_country(_cell(row, field), country_lookup)
            resolved[field] = value
            if unknown:
                row_errors.append(
                    f'Row {excel_row}: {label} "{value}" is not in the Country master — '
                    f'check the spelling, or ask an administrator to add it.'
                )

        items.append({
            'serial_number':         _cell(row, 'serial_number'),
            'country':               resolved['country'],
            'from_country':          resolved['from_country'],
            'to_country':            resolved['to_country'],
            'material':              material,
            'batch_no':              _cell(row, 'batch_no'),
            'mfg_date':              _cell(row, 'mfg_date'),
            'exp_date':              _cell(row, 'exp_date'),
            'from_storage_location': _cell(row, 'from_storage_location'),
            'to_storage_location':   _cell(row, 'to_storage_location'),
            'qty':                   qty,
            'box_no':                _cell(row, 'box_no'),
            'remark':                _cell(row, 'remark'),
            'item_date':             _cell(row, 'item_date'),
        })
    return items, row_errors


# ── Views ───────────────────────────────────────────────────────


@login_required
def download_upload_template(request):
    """Return a plain Excel template with header row only — no data, no colours."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    # Straight from UPLOAD_TEMPLATE_COLUMNS so the file people download is by
    # construction the file the parser expects.
    headers = list(UPLOAD_TEMPLATE_COLUMNS)
    _WIDTHS = {
        'Serial Number': 22, 'Country': 12, 'From Country': 14, 'To Country': 14,
        'Code': 14, 'Batch': 10, 'Mfg Date': 10, 'Exp Date': 10,
        'From Storage Location': 24, 'To Storage Location': 24,
        'Qty': 6, 'Box No': 8, 'Remarks': 30, 'Uploaded At': 12,
    }
    col_widths = [_WIDTHS.get(h, 14) for h in headers]

    wb = Workbook()
    ws = wb.active
    ws.title = 'Upload Template'

    # Plain bold header row — no fill, no colours
    bold = Font(bold=True)
    for col_idx, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.font = bold
        ws.column_dimensions[get_column_letter(col_idx)].width = col_widths[col_idx - 1]

    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="material_rework_upload_template.xlsx"'
    return response


@login_required
def dashboard(request):
    is_admin      = _is_md_admin(request.user)
    is_requester  = request.user.groups.filter(name='Material Rework User').exists()
    is_production = _is_production(request.user)
    is_warehouse  = _is_warehouse(request.user)
    is_ppic_user  = _is_ppic(request.user)
    is_om_user    = _is_om(request.user)
    can_manage    = _is_md_admin(request.user)

    is_hod_group  = request.user.groups.filter(name='MR HOD').exists()
    hod_depts     = _get_hod_departments(request.user) if not is_admin else None
    is_hod_user    = is_admin or bool(hod_depts) or is_hod_group

    if is_hod_user and (is_admin or is_hod_group):
        pending_hod_count = DispositionBatch.objects.filter(status='pending_hod').count()
    elif is_hod_user and hod_depts:
        pending_hod_count = DispositionBatch.objects.filter(status='pending_hod', department__in=hod_depts).count()
    else:
        pending_hod_count = 0

    return render(request, 'material_rework/dashboard.html', {
        'is_admin':               is_admin,
        'is_requester':           is_requester,
        'is_production':          is_production,
        'is_warehouse':           is_warehouse,
        'is_ppic':                is_ppic_user,
        'can_manage':             can_manage,
        'is_om':                  is_om_user,
        'is_hod':                 is_hod_user,
        'pending_hod_count':      pending_hod_count,
        'pending_om_count': (
            DispositionBatch.objects.filter(status='pending_om').count()
            if is_om_user else 0
        ),
        'pending_ppic_count': (
            DispositionBatch.objects.filter(status='pending_ppic').count()
            if is_ppic_user else 0
        ),
        'pending_production_count': (
            DispositionBatch.objects.filter(status__in=PRODUCTION_READY_STATUSES).count()
            if is_production else 0
        ),
        # Both legs warehouse owns, plus legacy rows — this is their to-do
        # count, so a line sitting at production is deliberately excluded.
        'pending_warehouse_count': (
            DispositionItem.objects.filter(status__in=[
                'pending_rework_handover', 'pending_return_receipt', 'pending_warehouse',
            ]).count()
            if is_warehouse else 0
        ),
    })


@login_required
def upload_view(request):
    has_md_role = request.user.groups.filter(name__in=MD_ROLE_NAMES).exists()
    if not (_is_merillife(request.user) or has_md_role):
        return render(request, 'material_rework/access_denied.html', status=403)

    verticals          = Vertical.objects.filter(is_active=True)
    requested_by_opts  = RequestedByOption.objects.filter(is_active=True)
    rework_type_opts   = ReworkType.objects.filter(is_active=True)
    location_opts      = ReworkLocation.objects.filter(is_active=True)

    if request.method == 'POST':
        vertical_id     = request.POST.get('vertical')
        dept_id         = request.POST.get('department')
        requested_by_id = request.POST.get('requested_by', '').strip()
        rework_type_id  = request.POST.get('rework_type', '').strip()
        location_id     = request.POST.get('location', '').strip()
        priority_raw    = request.POST.get('priority', '').strip()
        subject         = request.POST.get('subject', '').strip()
        # Only used by a flow that ends at the Warehouse terminal, which skips
        # the Production Review screen where the slot is normally booked.
        slot_from_raw   = request.POST.get('slot_from', '').strip()
        slot_to_raw     = request.POST.get('slot_to', '').strip()
        file            = request.FILES.get('excel_file')

        valid_priorities = {str(v) for v, _label in DispositionBatch.PRIORITY_CHOICES}

        errors = []
        if not vertical_id:
            errors.append('Please select a vertical.')
        if not dept_id:
            errors.append('Please select a department.')
        if not requested_by_id:
            errors.append('Please select Requested By.')
        if not rework_type_id:
            errors.append('Please select Type of Rework.')
        if not location_id:
            errors.append('Please select Location.')
        if not priority_raw:
            errors.append('Please select a Priority.')
        elif priority_raw not in valid_priorities:
            errors.append('Please select a valid Priority.')
        if not subject:
            errors.append('Please enter an email subject.')
        elif len(subject.split()) > 100:
            errors.append('Email subject cannot exceed 100 words.')
        if not file:
            errors.append('Please upload an Excel file.')
        elif not (file.name.endswith('.xlsx') or file.name.endswith('.xls')):
            errors.append('Only .xlsx / .xls files are accepted.')

        _ctx = {
            'verticals': verticals, 'requested_by_opts': requested_by_opts,
            'rework_type_opts': rework_type_opts, 'location_opts': location_opts,
            'priority_choices': DispositionBatch.PRIORITY_CHOICES,
            'sel_vertical': vertical_id, 'sel_dept': dept_id,
            'sel_requested_by': requested_by_id, 'sel_subject': subject,
            'sel_rework_type': rework_type_id, 'sel_location': location_id,
            'sel_priority': priority_raw,
            'sel_slot_from': slot_from_raw, 'sel_slot_to': slot_to_raw,
            # Lets the form re-show the slot fields on a failed submit without
            # the browser having to re-derive the flow from the dropdowns.
            'slot_required': bool(slot_from_raw or slot_to_raw),
            'routing_map': _routing_map_for_form(),
        }

        if errors:
            return render(request, 'material_rework/upload.html', {**_ctx, 'errors': errors})

        try:
            items_data, country_errors = _parse_excel(file)
        except TemplateValidationError as e:
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': [str(e)],
            })
        except Exception as e:
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': [f'Could not read the Excel file. Make sure it is a valid .xlsx file and is not open in another program. Detail: {e}'],
            })

        if not items_data:
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': ['No valid rows found in the Excel file.'],
            })

        # A country the master does not know blocks the upload rather than
        # being saved as-is: the whole point of the master is that downstream
        # reports can group on a country name without three spellings of it.
        if country_errors:
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': country_errors, 'error_title': 'Country validation failed',
            })

        try:
            vertical     = Vertical.objects.get(pk=vertical_id, is_active=True)
            dept         = Department.objects.get(pk=dept_id, vertical=vertical, is_active=True)
            requested_by = RequestedByOption.objects.get(pk=requested_by_id, is_active=True)
            rework_type  = ReworkType.objects.get(pk=rework_type_id, is_active=True)
            location     = ReworkLocation.objects.get(pk=location_id, is_active=True)
        except (Vertical.DoesNotExist, Department.DoesNotExist):
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': ['Invalid vertical or department selected.'],
            })
        except RequestedByOption.DoesNotExist:
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': ['Invalid "Requested By" selection.'],
            })
        except ReworkType.DoesNotExist:
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': ['Invalid "Type of Rework" selection.'],
            })
        except ReworkLocation.DoesNotExist:
            return render(request, 'material_rework/upload.html', {
                **_ctx, 'errors': ['Invalid "Location" selection.'],
            })

        # ── Routing ──────────────────────────────────────────────────────
        # The chain comes from the RoutingRule master, resolved once here and
        # then frozen onto the batch. A rework type overrides the requester's
        # flow outright, which is how "Damage goes straight to production"
        # works regardless of who raised it.
        chain, origin, rule = routing.resolve(requested_by, rework_type)
        terminal = routing.terminal_of(chain)
        first_stage = routing.first_stage(chain)

        # A flow ending at Warehouse has no Production Review, and the slot is
        # normally booked there — so on that flow alone the uploader commits to
        # it. Validated after the flow is known rather than from the dropdown,
        # because the rework type can override the requester and change the
        # answer.
        slot_from = slot_to = None
        if terminal == routing.STAGE_WAREHOUSE:
            slot_errors = []
            if not slot_from_raw or not slot_to_raw:
                slot_errors.append(
                    'This request goes straight to Warehouse with no production '
                    'review, so Slot From and Slot To are both required.'
                )
            else:
                slot_from = parse_date(slot_from_raw)
                slot_to   = parse_date(slot_to_raw)
                if slot_from is None or slot_to is None:
                    slot_errors.append('Slot dates must be valid dates (YYYY-MM-DD).')
                elif slot_to < slot_from:
                    slot_errors.append('Slot To cannot be earlier than Slot From.')
            if slot_errors:
                return render(request, 'material_rework/upload.html', {
                    **_ctx, 'errors': slot_errors, 'slot_required': True,
                })

        with transaction.atomic():
            batch = DispositionBatch.objects.create(
                vertical=vertical,
                department=dept,
                requested_by=requested_by,
                rework_type=rework_type,
                location=location,
                priority=int(priority_raw),
                subject=subject,
                # Written by _enter_stage below, which sets the batch status,
                # the line statuses and current_stage together. Seeded with the
                # first stage's status so the row is never briefly stored in a
                # state no screen recognises.
                status=routing.batch_status_for(first_stage),
                current_stage=first_stage,
                routing_chain=chain,
                routing_origin=origin,
                routing_rule_note=(rule.key_label if rule else 'No rule — default flow'),
                slot_from=slot_from,
                slot_to=slot_to,
                uploaded_by_name=request.user.get_full_name() or request.user.username,
                uploaded_by_email=request.user.email,
            )
            batch.reference_number = _generate_reference_number(batch)
            batch.save(update_fields=['reference_number'])
            DispositionItem.objects.bulk_create([
                DispositionItem(batch=batch, **d) for d in items_data
            ])
            # Notification is deferred until after the transaction commits:
            # mailing a team about a batch that then rolls back sends them to
            # an inbox with nothing in it.
            _enter_stage(request, batch, first_stage, notify=False)

        request.session.pop('md_upload_draft', None)
        _notify_stage(request, batch, first_stage)
        messages.success(
            request,
            f'Batch {batch.reference_number} uploaded with {len(items_data)} item(s). '
            f'Flow: {routing.describe(chain)}. '
            f'{routing.label(first_stage)} has been notified.'
        )
        return redirect('material_rework:my_uploads')

    # Restore draft from session if present
    draft = request.session.get('md_upload_draft', {})
    return render(request, 'material_rework/upload.html', {
        'verticals':         verticals,
        'requested_by_opts': requested_by_opts,
        'rework_type_opts':  rework_type_opts,
        'location_opts':     location_opts,
        'priority_choices':  DispositionBatch.PRIORITY_CHOICES,
        'sel_vertical':      draft.get('vertical', ''),
        'sel_dept':          draft.get('department', ''),
        'sel_requested_by':  draft.get('requested_by', ''),
        'sel_rework_type':   draft.get('rework_type', ''),
        'sel_location':      draft.get('location', ''),
        'sel_priority':      draft.get('priority', str(DispositionBatch.PRIORITY_MEDIUM)),
        'sel_subject':       draft.get('subject', ''),
        'sel_slot_from':     draft.get('slot_from', ''),
        'sel_slot_to':       draft.get('slot_to', ''),
        'has_draft':         bool(draft),
        # Drives the form's live "Flow: ..." preview and decides when the slot
        # fields appear, so the uploader sees where the batch will go before
        # submitting rather than discovering it from the success message.
        'routing_map':       _routing_map_for_form(),
    })


@login_required
@require_POST
def save_upload_session(request):
    """AJAX endpoint to persist upload form selections into the session."""
    request.session['md_upload_draft'] = {
        'vertical':    request.POST.get('vertical', ''),
        'department':  request.POST.get('department', ''),
        'requested_by': request.POST.get('requested_by', ''),
        'rework_type': request.POST.get('rework_type', ''),
        'location':    request.POST.get('location', ''),
        'priority':    request.POST.get('priority', ''),
    }
    return JsonResponse({'ok': True})


@login_required
def replace_batch(request, batch_id):
    """Allow the uploader to replace a rejected batch's file and re-submit."""
    batch = get_object_or_404(DispositionBatch, pk=batch_id)

    if not (_is_md_admin(request.user) or batch.uploaded_by_email == request.user.email):
        return render(request, 'material_rework/access_denied.html', status=403)

    if batch.status != 'hod_rejected':
        messages.error(request, 'Only HOD-rejected batches can be replaced.')
        return redirect('material_rework:my_uploads')

    if request.method == 'POST':
        file = request.FILES.get('excel_file')
        errors = []
        if not file:
            errors.append('Please upload an Excel file.')
        elif not (file.name.endswith('.xlsx') or file.name.endswith('.xls')):
            errors.append('Only .xlsx / .xls files are accepted.')

        if errors:
            return render(request, 'material_rework/replace_batch.html', {
                'batch': batch, 'errors': errors,
            })

        try:
            items_data, country_errors = _parse_excel(file)
        except Exception as e:
            return render(request, 'material_rework/replace_batch.html', {
                'batch': batch, 'errors': [f'Could not read Excel file: {e}'],
            })

        if not items_data:
            return render(request, 'material_rework/replace_batch.html', {
                'batch': batch, 'errors': ['No valid rows found in the Excel file.'],
            })

        # Same country gate as the first upload — a replacement file must not be
        # a way around the Country master.
        if country_errors:
            return render(request, 'material_rework/replace_batch.html', {
                'batch': batch, 'errors': country_errors,
                'error_title': 'Country validation failed',
            })

        with transaction.atomic():
            batch.items.all().delete()
            batch.status = 'pending_hod'
            batch.hod_reviewed_at = None
            batch.hod_comments = ''
            batch.save(update_fields=['status', 'hod_reviewed_at', 'hod_comments'])
            DispositionItem.objects.bulk_create([
                DispositionItem(batch=batch, **d) for d in items_data
            ])

        _send_hod_email(request, batch)
        messages.success(
            request,
            f'Batch {batch.reference_number} has been updated with {len(items_data)} item(s) and re-submitted for HOD approval.'
        )
        return redirect('material_rework:my_uploads')

    return render(request, 'material_rework/replace_batch.html', {'batch': batch})


@login_required
def departments_ajax(request, vertical_id):
    depts = list(
        Department.objects.filter(vertical_id=vertical_id, is_active=True).values('id', 'name')
    )
    return JsonResponse({'departments': depts})


@login_required
def my_uploads(request):
    is_admin = _is_md_admin(request.user)
    items_qs = DispositionItem.objects.only(
        'id', 'batch_id', 'material', 'serial_number', 'batch_no', 'qty',
        'box_no', 'remark', 'country', 'mfg_date', 'exp_date',
        'from_storage_location', 'to_storage_location', 'item_date',
        'status', 'accepted_qty', 'pending_qty',
    )
    batches = DispositionBatch.objects.filter(
        uploaded_by_email=request.user.email
    ).select_related('company', 'vertical', 'department', 'requested_by'
    ).prefetch_related(Prefetch('items', queryset=items_qs)
    ).annotate(item_count=Count('items', distinct=True)
    ).order_by('-uploaded_at')
    return render(request, 'material_rework/my_uploads.html', {
        'batches':    batches,
        'user_email': request.user.email,
        'is_admin':   is_admin,
        'upload_url': reverse('material_rework:upload'),
    })


@login_required
def my_uploads_status(request):
    """Polling endpoint — returns current status of all batches for the logged-in user."""
    batches = DispositionBatch.objects.filter(
        uploaded_by_email=request.user.email
    ).values('id', 'status', 'hod_reviewed_at', 'hod_comments', 'reference_number')
    result = {}
    for b in batches:
        result[str(b['id'])] = {
            'status':         b['status'],
            'hod_reviewed_at': b['hod_reviewed_at'].strftime('%d %b %Y %H:%M') if b['hod_reviewed_at'] else '—',
            'hod_comments':   b['hod_comments'] or '',
            'ref':            b['reference_number'] or str(b['id']),
        }
    return JsonResponse({'batches': result})


def hod_review(request, token):
    with transaction.atomic():
        batch = get_object_or_404(
            DispositionBatch.objects.select_for_update(),
            approval_token=token,
        )
        link_allowed = _approval_link_claimed_by_current_session(request, batch)

    items = list(batch.items.all())

    if not link_allowed:
        return render(request, 'material_rework/hod_review.html', {
            'batch': batch, 'items': items, 'link_blocked': True,
        }, status=403)

    if batch.status != 'pending_hod':
        return render(request, 'material_rework/hod_review.html', {
            'batch': batch, 'items': items, 'already_reviewed': True,
        })

    if request.method == 'POST':
        action   = request.POST.get('action')
        comments = request.POST.get('comments', '').strip()
        if action in ('approve', 'reject'):
            with transaction.atomic():
                batch.hod_reviewed_at = timezone.now()
                batch.hod_comments    = comments
                if action == 'approve':
                    batch.status = 'hod_approved'
                    batch.items.all().update(status='pending_production')
                else:
                    batch.status = 'hod_rejected'
                    batch.items.all().update(status='hod_rejected')
                batch.save()
            return render(request, 'material_rework/hod_review.html', {
                'batch': batch, 'items': items, 'done': True, 'action': action,
            })

    return render(request, 'material_rework/hod_review.html', {
        'batch': batch, 'items': items,
    })


@login_required
def production_list(request):
    if not _is_production(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)
    can_edit = _can_production_edit(request.user)
    items_qs = DispositionItem.objects.only(
        'id', 'batch_id', 'material', 'serial_number', 'batch_no', 'qty',
        'box_no', 'remark', 'country', 'from_country', 'to_country',
        'mfg_date', 'exp_date',
        'from_storage_location', 'to_storage_location', 'item_date',
    )
    # Highest priority first — this is the queue production actually works
    # from, so the ordering is the feature, not cosmetics.
    pending_batches  = DispositionBatch.objects.filter(
        status__in=PRODUCTION_READY_STATUSES
    ).select_related('company', 'vertical', 'department', 'requested_by', 'rework_type', 'location'
    ).prefetch_related(Prefetch('items', queryset=items_qs)
    ).annotate(item_count=Count('items', distinct=True)
    ).order_by(*PRIORITY_ORDERING)
    reviewed_batches = DispositionBatch.objects.filter(
        status__in=['production_done', 'completed']
    ).select_related('company', 'vertical', 'department', 'requested_by', 'rework_type', 'location'
    ).prefetch_related(Prefetch('items', queryset=items_qs)
    ).annotate(item_count=Count('items', distinct=True)
    ).order_by(F('production_reviewed_at').desc(nulls_last=True), '-uploaded_at')
    # Leg 3 of the round trip: lines warehouse has released and production
    # still has on the floor. Listed per line, not per batch, because a batch's
    # lines come back at different times.
    in_rework_items = (
        DispositionItem.objects.filter(status='in_rework')
        .select_related('batch', 'batch__vertical', 'batch__department',
                        'batch__rework_type', 'batch__location')
        .order_by('batch__priority', 'batch__uploaded_at', 'pk')
    )

    return render(request, 'material_rework/production_list.html', {
        'batches':          pending_batches,
        'reviewed_batches': reviewed_batches,
        'in_rework_items':  in_rework_items,
        'can_edit':         can_edit,
    })


@login_required
def production_review_batch(request, batch_id):
    if not _is_production(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)

    can_edit = _can_production_edit(request.user)
    batch = get_object_or_404(DispositionBatch, pk=batch_id, status__in=PRODUCTION_READY_STATUSES)
    items = list(batch.items.filter(status='pending_production'))

    if request.method == 'POST':
        if not can_edit:
            messages.error(request, 'You have view-only access and cannot submit production reviews.')
            return redirect('material_rework:production_review_batch', batch_id=batch_id)

        slot_from_raw = request.POST.get('slot_from', '').strip()
        slot_to_raw   = request.POST.get('slot_to', '').strip()

        reviewed_items = []
        invalid_qty = False
        missing_remarks = []
        for item in items:
            raw = request.POST.get(f'accepted_{item.pk}', '0')
            accepted_qty = _validate_qty_int(raw)
            if accepted_qty is None or accepted_qty > item.qty:
                invalid_qty = True
                break
            remainder = item.qty - accepted_qty
            # Special rule: if accepted = total (remainder = 0), send all to warehouse
            wh_qty = item.qty if (remainder == 0 and accepted_qty > 0) else remainder
            remarks = request.POST.get(f'remarks_{item.pk}', '').strip()
            if not remarks:
                missing_remarks.append(item.material or f'item #{item.pk}')
            reviewed_items.append((item, accepted_qty, wh_qty, remarks))

        errors = []
        if invalid_qty:
            errors.append('Invalid quantity entered. Accepted quantity cannot be more than the total assigned quantity.')
        # A remark per line is mandatory: production deciding how much of a
        # line it accepts *is* the approve/reject decision for that line.
        if missing_remarks:
            shown = ', '.join(missing_remarks[:5])
            more  = f' and {len(missing_remarks) - 5} more' if len(missing_remarks) > 5 else ''
            errors.append(f'A remark is required for every line. Missing for: {shown}{more}.')

        slot_from = slot_to = None
        if not slot_from_raw or not slot_to_raw:
            errors.append('Slot From and Slot To dates are both required.')
        else:
            slot_from = parse_date(slot_from_raw)
            slot_to   = parse_date(slot_to_raw)
            if slot_from is None or slot_to is None:
                errors.append('Slot dates must be valid dates (YYYY-MM-DD).')
            elif slot_to < slot_from:
                errors.append('Slot To cannot be earlier than Slot From.')

        if errors:
            return render(request, 'material_rework/production_review_batch.html', {
                'batch': batch,
                'items': items,
                'can_edit': can_edit,
                'errors': errors,
                'sel_slot_from': slot_from_raw,
                'sel_slot_to': slot_to_raw,
            })

        with transaction.atomic():
            for item, accepted_qty, pending_qty, remarks in reviewed_items:
                item.accepted_qty           = accepted_qty
                item.pending_qty            = pending_qty
                item.production_remarks     = remarks
                item.production_reviewed_at = timezone.now()

                # Every line now enters the round trip rather than going
                # straight back to warehouse: warehouse releases the accepted
                # quantity to production, production works it and returns it,
                # warehouse counts it again. A line production accepts nothing
                # of has no journey to make, so it closes here.
                if accepted_qty == 0:
                    item.status = 'rejected'
                else:
                    item.status = 'pending_rework_handover'
                item.save()

            batch.status = 'production_done'
            # Past the end of the chain: the round trip that follows is item
            # -level, so leaving a stage set here would keep the batch showing
            # in Production's inbox after it had been reviewed.
            batch.current_stage = ''
            batch.slot_from = slot_from
            batch.slot_to   = slot_to
            batch.production_reviewed_by_name  = request.user.get_full_name() or request.user.username
            batch.production_reviewed_by_email = request.user.email
            batch.production_reviewed_at       = timezone.now()
            batch.save()

        pending_items = list(batch.items.filter(status='pending_rework_handover'))
        if pending_items:
            _send_warehouse_email(request, batch, pending_items)
        _send_production_review_email(request, batch)
        messages.success(
            request,
            f'{batch.reference_number} reviewed. Warehouse notified to release '
            f'{len(pending_items)} line(s) to production for rework.'
        )
        return redirect('material_rework:production_list')

    return render(request, 'material_rework/production_review_batch.html', {
        'batch': batch, 'items': items, 'can_edit': can_edit,
    })


# ── The rework round trip ────────────────────────────────────────────────
#: Statuses a line passes through between production's decision and the final
#: warehouse count. One list so the warehouse screen, the production return
#: screen and the batch-completion check cannot disagree about what "still in
#: flight" means.
REWORK_IN_FLIGHT_STATUSES = (
    'pending_rework_handover', 'in_rework', 'pending_return_receipt',
)
#: Statuses that mean the line's journey is over, however it ended.
REWORK_CLOSED_STATUSES = ('received', 'received_discrepancy', 'rejected')


def _refresh_batch_completion(batch):
    """Close a batch once no line is still in flight, and reopen it if one is.

    Called after every leg of the round trip. Reopening matters: an admin
    correcting a line back to an earlier stage must not leave the batch
    claiming to be complete.
    """
    in_flight = batch.items.filter(
        status__in=REWORK_IN_FLIGHT_STATUSES + ('pending_warehouse',)
    ).exists()

    # Which status "open" means for this batch. A flow ending at the Warehouse
    # terminal skips the Production Review screen entirely, so it never passes
    # through ``production_done`` — matching only that status left such a
    # batch stuck reading "Pending Warehouse Release" after its round trip had
    # finished, with every line received.
    open_status = (
        'pending_warehouse_release'
        if routing.terminal_of(batch.routing_chain) == routing.STAGE_WAREHOUSE
        else 'production_done'
    )

    if in_flight and batch.status == 'completed':
        batch.status = open_status
        batch.save(update_fields=['status'])
    elif not in_flight and batch.status in ('production_done', 'pending_warehouse_release'):
        batch.status = 'completed'
        # Past the end of the chain, so no stage is current any more.
        batch.current_stage = ''
        batch.save(update_fields=['status', 'current_stage'])


@login_required
@require_POST
def rework_release(request, item_id):
    """Leg 2 — warehouse confirms it has released the line to production.

    Warehouse, not production, records this: the units physically leave the
    warehouse here, so the count that matters is the one the person handing
    them over actually put on the trolley. It may be lower than production
    asked for; it can never be higher, because you cannot release stock you
    were not holding for this line.
    """
    if not _is_warehouse(request.user):
        return JsonResponse({'ok': False, 'error': 'Warehouse access required.'}, status=403)
    if not _can_warehouse_edit(request.user):
        return JsonResponse({'ok': False, 'error': 'You have view-only access.'}, status=403)

    with transaction.atomic():
        try:
            item = DispositionItem.objects.select_for_update().get(
                pk=item_id, status='pending_rework_handover',
            )
        except DispositionItem.DoesNotExist:
            return JsonResponse(
                {'ok': False, 'error': 'Line not found, or it is not awaiting handover.'}, status=404,
            )

        released = _validate_qty_int(request.POST.get('released_qty', ''))
        remarks  = request.POST.get('remarks', '').strip()
        if released is None or released <= 0:
            return JsonResponse({'ok': False, 'error': 'Enter the quantity released to production.'}, status=400)
        if released > item.accepted_qty:
            return JsonResponse(
                {'ok': False,
                 'error': f'Cannot release more than production accepted ({item.accepted_qty}).'},
                status=400,
            )
        if not remarks:
            return JsonResponse({'ok': False, 'error': 'A remark is required to confirm the handover.'}, status=400)

        actor_name = request.user.get_full_name() or request.user.username
        actor_email = request.user.email or ''
        item.rework_released_qty     = released
        item.rework_released_at      = timezone.now()
        item.rework_released_by_name  = actor_name
        item.rework_released_by_email = actor_email
        item.rework_released_remarks = remarks
        item.status                  = 'in_rework'
        item.save(update_fields=[
            'rework_released_qty', 'rework_released_at',
            'rework_released_by_name', 'rework_released_by_email',
            'rework_released_remarks', 'status',
        ])
        _refresh_batch_completion(item.batch)

    return JsonResponse({
        'ok': True, 'item_id': item.pk, 'status': item.status,
        'released_qty': int(item.rework_released_qty),
    })


@login_required
@require_POST
def rework_return(request, item_id):
    """Leg 3 — production reports what it reworked and sends it back.

    Returning fewer than were released is allowed and is the normal way
    breakage gets recorded, but it must be explained: the shortfall is stored
    as ``rework_loss_qty`` with a required reason rather than quietly vanishing
    from the totals.
    """
    if not _is_production(request.user):
        return JsonResponse({'ok': False, 'error': 'Production access required.'}, status=403)
    if not _can_production_edit(request.user):
        return JsonResponse({'ok': False, 'error': 'You have view-only access.'}, status=403)

    with transaction.atomic():
        try:
            item = DispositionItem.objects.select_for_update().get(pk=item_id, status='in_rework')
        except DispositionItem.DoesNotExist:
            return JsonResponse(
                {'ok': False, 'error': 'Line not found, or it is not currently in rework.'}, status=404,
            )

        reworked = _validate_qty_int(request.POST.get('reworked_qty', ''))
        reason   = request.POST.get('loss_reason', '').strip()
        if reworked is None:
            return JsonResponse({'ok': False, 'error': 'Enter the quantity reworked.'}, status=400)
        if reworked > item.rework_released_qty:
            return JsonResponse(
                {'ok': False,
                 'error': f'Cannot return more than was released ({item.rework_released_qty}).'},
                status=400,
            )

        loss = int(item.rework_released_qty) - int(reworked)
        if loss and not reason:
            return JsonResponse(
                {'ok': False,
                 'error': f'{loss} unit(s) short of what was released — a reason is required.'},
                status=400,
            )

        actor_name = request.user.get_full_name() or request.user.username
        actor_email = request.user.email or ''
        item.reworked_qty       = reworked
        item.rework_loss_qty    = loss
        item.rework_loss_reason = reason
        item.rework_returned_at = timezone.now()
        item.rework_returned_by_name  = actor_name
        item.rework_returned_by_email = actor_email
        item.status             = 'pending_return_receipt'
        item.save(update_fields=[
            'reworked_qty', 'rework_loss_qty', 'rework_loss_reason',
            'rework_returned_at', 'status',
            'rework_returned_by_name', 'rework_returned_by_email',
        ])
        _refresh_batch_completion(item.batch)

    return JsonResponse({
        'ok': True, 'item_id': item.pk, 'status': item.status,
        'reworked_qty': int(item.reworked_qty), 'rework_loss_qty': int(item.rework_loss_qty),
    })


@login_required
@require_POST
def rework_receive(request, item_id):
    """Leg 4 — warehouse counts what actually came back.

    A count that differs from what production said it sent is recorded as a
    discrepancy rather than rejected. Forcing the two to match would only push
    the disagreement off-system, and a transit loss that nobody can see is
    worse than one flagged on the report.
    """
    if not _is_warehouse(request.user):
        return JsonResponse({'ok': False, 'error': 'Warehouse access required.'}, status=403)
    if not _can_warehouse_edit(request.user):
        return JsonResponse({'ok': False, 'error': 'You have view-only access.'}, status=403)

    with transaction.atomic():
        try:
            item = DispositionItem.objects.select_for_update().get(
                pk=item_id, status='pending_return_receipt',
            )
        except DispositionItem.DoesNotExist:
            return JsonResponse(
                {'ok': False, 'error': 'Line not found, or it is not awaiting receipt.'}, status=404,
            )

        received = _validate_qty_int(request.POST.get('received_qty', ''))
        remarks  = request.POST.get('remarks', '').strip()
        if received is None:
            return JsonResponse({'ok': False, 'error': 'Enter the quantity received.'}, status=400)
        if received > item.reworked_qty:
            return JsonResponse(
                {'ok': False,
                 'error': f'Cannot receive more than production returned ({item.reworked_qty}).'},
                status=400,
            )

        mismatch = received != int(item.reworked_qty)
        if mismatch and not remarks:
            return JsonResponse(
                {'ok': False,
                 'error': (f'Production sent {item.reworked_qty} but {received} were counted — '
                           f'a remark is required to record the difference.')},
                status=400,
            )

        actor_name = request.user.get_full_name() or request.user.username
        actor_email = request.user.email or ''
        item.qty_received            = received
        item.pending_qty             = 0
        item.warehouse_received_at   = timezone.now()
        item.warehouse_received_by_name  = actor_name
        item.warehouse_received_by_email = actor_email
        item.warehouse_remarks       = remarks
        item.has_receipt_discrepancy = mismatch
        item.status                  = 'received_discrepancy' if mismatch else 'received'
        item.save(update_fields=[
            'qty_received', 'pending_qty', 'warehouse_received_at',
            'warehouse_received_by_name', 'warehouse_received_by_email',
            'warehouse_remarks', 'has_receipt_discrepancy', 'status',
        ])
        _refresh_batch_completion(item.batch)

    return JsonResponse({
        'ok': True, 'item_id': item.pk, 'status': item.status,
        'qty_received': int(item.qty_received), 'discrepancy': mismatch,
        'variance': item.receipt_variance,
    })


def _routing_map_for_form():
    """The rule table as JSON the upload form can resolve without a round trip.

    The form shows the flow a batch will take the moment the Requested By and
    Type of Rework dropdowns are set, and the slot fields appear only for a
    flow ending at Warehouse. Doing that server-side would mean a fetch on
    every dropdown change; the whole table is a handful of rows, so it is
    simply handed over once.

    The same precedence as routing.resolve() is applied client-side, and the
    server re-resolves and re-validates on POST regardless — this is a
    convenience for the person filling the form, never the decision.
    """
    from .models import RoutingRule

    rules = RoutingRule.objects.filter(is_active=True).select_related(
        'requested_by', 'rework_type',
    )
    by_requested_by, by_rework_type = {}, {}
    for rule in rules:
        try:
            chain = routing.validate_chain(rule.stages)
        except routing.RoutingError:
            continue
        entry = {
            'stages': chain,
            'label': routing.describe(chain),
            'terminal': routing.terminal_of(chain),
        }
        if rule.rework_type_id:
            by_rework_type[str(rule.rework_type_id)] = entry
        elif rule.requested_by_id:
            by_requested_by[str(rule.requested_by_id)] = entry

    default_chain = list(routing.DEFAULT_CHAIN)
    return json.dumps({
        'requested_by': by_requested_by,
        'rework_type': by_rework_type,
        'default': {
            'stages': default_chain,
            'label': routing.describe(default_chain),
            'terminal': routing.terminal_of(default_chain),
        },
        'warehouse_terminal': routing.STAGE_WAREHOUSE,
    })


# ── Order Management gate ────────────────────────────────────────────────
# A new stage, added because the Warehouse flow reviews at OM before PPIC.
# Mirrors the PPIC screens deliberately: same view/edit split, same mandatory
# remark, same reject-closes-the-batch behaviour. Approving hands the batch to
# whatever the batch's own chain says comes next, which is PPIC on the seeded
# Warehouse flow but is not hardcoded to it.

@login_required
def om_list(request):

    if not _is_om(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)

    # Highest priority first, same ordering as the other gates so a batch does
    # not appear urgent on one screen and ordinary on the next.
    pending_batches = DispositionBatch.objects.filter(
        status='pending_om'
    ).select_related('vertical', 'department', 'requested_by', 'rework_type', 'location'
    ).annotate(item_count=Count('items', distinct=True)
    ).order_by(*PRIORITY_ORDERING)

    reviewed_batches = DispositionBatch.objects.filter(
        om_reviewed_at__isnull=False
    ).exclude(status='pending_om'
    ).select_related('vertical', 'department', 'requested_by', 'rework_type', 'location'
    ).annotate(item_count=Count('items', distinct=True)
    ).order_by('-om_reviewed_at')

    # An OM reviewer has to know whether PPIC follows them or whether their
    # approval sends the batch straight to production — the PPIC screen never
    # had to answer that, because only one flow ever led into it. Attached
    # here rather than computed in the template so the chain is described in
    # exactly one place.
    pending_batches = list(pending_batches)
    for batch in pending_batches:
        batch.flow_label = routing.describe(batch.routing_chain)

    return render(request, 'material_rework/om_list.html', {
        'batches':          pending_batches,
        'reviewed_batches': reviewed_batches,
        'can_edit':         _can_om_edit(request.user),
    })


@login_required
def om_review_batch(request, batch_id):
    if not _is_om(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)
    can_edit = _can_om_edit(request.user)
    batch = get_object_or_404(DispositionBatch, pk=batch_id, status='pending_om')
    items = list(batch.items.all())

    if request.method == 'POST' and can_edit:
        action = request.POST.get('action', '').strip()
        comments = request.POST.get('comments', '').strip()

        # A remark is mandatory on both approve and reject, as at the PPIC
        # gate: the decision is the record, and "approved" with no stated
        # reason cannot be reviewed later.
        if action in ('approve', 'reject') and not comments:
            verb = 'approving' if action == 'approve' else 'rejecting'
            return render(request, 'material_rework/om_review_batch.html', {
                'batch': batch, 'items': items, 'can_edit': can_edit,
                'errors': [f'A remark is required before {verb} this batch.'],
                'sel_action': action,
                'flow_label': routing.describe(batch.routing_chain),
            })

        if action in ('approve', 'reject'):
            with transaction.atomic():
                batch.om_reviewed_by_name = request.user.get_full_name() or request.user.username
                batch.om_reviewed_by_email = request.user.email
                batch.om_reviewed_at = timezone.now()
                batch.om_comments = comments

                if action == 'reject':
                    batch.status = 'om_rejected'
                    batch.current_stage = ''
                    batch.items.all().update(status='om_rejected')
                    batch.save()
                    messages.success(
                        request, f'Batch {batch.reference_number} rejected by Order Management.',
                    )
                    return redirect('material_rework:om_list')

                # Approved: hand it to whatever this batch's own chain says
                # follows OM. Read from the batch, not from RoutingRule, so a
                # rule edited since upload cannot re-route it.
                batch.save()
                nxt = _advance_batch(request, batch, routing.STAGE_OM)

            if nxt is None:
                # OM was the last stage on the chain. Only reachable from a
                # rule that ends at a gate, which validate_chain refuses — so
                # this means the chain was edited in the database. Reported
                # rather than silently leaving the batch in limbo.
                batch.status = 'om_approved'
                batch.save(update_fields=['status'])
                messages.warning(
                    request,
                    f'Batch {batch.reference_number} approved, but its flow '
                    f'({routing.describe(batch.routing_chain)}) has no stage after '
                    f'Order Management. Ask an admin to check the routing rule.',
                )
            else:
                messages.success(
                    request,
                    f'Batch {batch.reference_number} approved by Order Management and '
                    f'sent to {routing.label(nxt)}.',
                )
        return redirect('material_rework:om_list')

    # The approve button names the stage that actually follows, read from this
    # batch's own chain. The PPIC screen could hardcode "Send to Production"
    # because one flow led into it; with the chain configurable, promising a
    # destination the batch is not going to is a lie on the button.
    nxt = routing.next_stage(batch.routing_chain, routing.STAGE_OM)
    return render(request, 'material_rework/om_review_batch.html', {
        'batch': batch, 'items': items, 'can_edit': can_edit,
        'flow_label': routing.describe(batch.routing_chain),
        'next_stage_label': routing.label(nxt) if nxt else 'the next stage',
    })


@login_required
def ppic_list(request):
    if not _is_ppic(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)
    can_edit = _can_ppic_edit(request.user)

    # Pending PPIC approval — highest priority first
    pending_batches = DispositionBatch.objects.filter(
        status='pending_ppic'
    ).select_related('vertical', 'department', 'requested_by', 'rework_type', 'location'
    ).annotate(item_count=Count('items', distinct=True)
    ).order_by(*PRIORITY_ORDERING)

    # Batches PPIC already reviewed (approved → straight to Production, or rejected)
    reviewed_batches = DispositionBatch.objects.filter(
        ppic_reviewed_at__isnull=False
    ).exclude(status='pending_ppic'
    ).select_related('vertical', 'department', 'requested_by', 'rework_type', 'location'
    ).annotate(item_count=Count('items', distinct=True)
    ).order_by('-ppic_reviewed_at')

    return render(request, 'material_rework/ppic_list.html', {
        'batches':          pending_batches,
        'reviewed_batches': reviewed_batches,
        'can_edit':         can_edit,
    })


@login_required
def ppic_review_batch(request, batch_id):
    if not _is_ppic(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)
    can_edit = _can_ppic_edit(request.user)
    batch = get_object_or_404(DispositionBatch, pk=batch_id, status='pending_ppic')
    items = list(batch.items.all())

    if request.method == 'POST' and can_edit:
        action   = request.POST.get('action', '').strip()
        comments = request.POST.get('comments', '').strip()

        # A remark is mandatory on both approve and reject — the decision is
        # the record, and "approved" with no stated reason cannot be reviewed.
        if action in ('approve', 'reject') and not comments:
            verb = 'approving' if action == 'approve' else 'rejecting'
            return render(request, 'material_rework/ppic_review_batch.html', {
                'batch': batch, 'items': items, 'can_edit': can_edit,
                'errors': [f'A remark is required before {verb} this batch.'],
                'sel_action': action,
            })

        if action in ('approve', 'reject'):
            with transaction.atomic():
                batch.ppic_reviewed_by_name  = request.user.get_full_name() or request.user.username
                batch.ppic_reviewed_by_email = request.user.email
                batch.ppic_reviewed_at       = timezone.now()
                batch.ppic_comments          = comments
                if action == 'approve':
                    # Whatever this batch's own chain says follows PPIC, which
                    # is Production on both seeded flows but is no longer
                    # hardcoded to it. Read from the batch rather than from
                    # RoutingRule so a rule edited since upload cannot
                    # re-route work already in flight.
                    batch.save()
                    nxt = _advance_batch(request, batch, routing.STAGE_PPIC)
                    if nxt is None:
                        batch.status = 'ppic_approved'
                        batch.save(update_fields=['status'])
                        messages.warning(
                            request,
                            f'Batch {batch.reference_number} approved, but its flow '
                            f'({routing.describe(batch.routing_chain)}) has no stage after '
                            f'PPIC. Ask an admin to check the routing rule.',
                        )
                    else:
                        messages.success(
                            request,
                            f'Batch {batch.reference_number} approved by PPIC and sent to '
                            f'{routing.label(nxt)}.'
                        )
                else:
                    batch.status = 'ppic_rejected'
                    batch.current_stage = ''
                    batch.items.all().update(status='ppic_rejected')
                    batch.save()
                    messages.success(request, f'Batch {batch.reference_number} rejected by PPIC.')
        return redirect('material_rework:ppic_list')

    return render(request, 'material_rework/ppic_review_batch.html', {
        'batch': batch, 'items': items, 'can_edit': can_edit,
    })


@login_required
def print_labels(request, batch_id):
    if not (_is_production(request.user) or _is_md_admin(request.user)):
        return render(request, 'material_rework/access_denied.html', status=403)
    batch = get_object_or_404(
        DispositionBatch, pk=batch_id,
        status__in=['production_done', 'completed'],
    )
    items = list(batch.items.order_by('box_no', 'id'))

    # Group by box_no (preserve insertion order), compute total qty per box
    from collections import OrderedDict
    _boxes = OrderedDict()
    for item in items:
        key = item.box_no.strip() if item.box_no else 'N/A'
        _boxes.setdefault(key, []).append(item)

    total_boxes = len(_boxes)
    boxes = [
        (box_no, box_items, sum(i.qty for i in box_items))
        for box_no, box_items in _boxes.items()
    ]

    return render(request, 'material_rework/print_labels.html', {
        'batch': batch, 'boxes': boxes, 'total_boxes': total_boxes,
    })


# Items the warehouse page shows: pending receipts plus already-received/accepted history.
#: What the warehouse screen lists. Includes every leg of the round trip, not
#: just the ones warehouse acts on, so the team can see a line sitting at
#: production instead of it vanishing from their view between the two legs
#: they own (release, then receive).
_WAREHOUSE_ITEM_FILTER = {
    'status__in': [
        'pending_rework_handover',   # warehouse's move: release to production
        'in_rework',                 # at production — visible, not actionable
        'pending_return_receipt',    # warehouse's move: count what came back
        'received_discrepancy',
        'pending_warehouse',         # legacy rows from before the round trip
        'received', 'accepted',
    ],
    # Batch-level gate as well as item-level, so a line cannot show here
    # while its batch is still awaiting a review.
    #
    # ``pending_warehouse_release`` is the routing engine's Warehouse
    # terminal: a rework production raised itself, which skips every review
    # and therefore never reaches ``production_done``. Without it the material
    # sat in pending_rework_handover on a batch no warehouse query matched —
    # invisible rather than broken, since nothing raises and the line simply
    # never appears in anyone's list.
    'batch__status__in': [
        'pending_warehouse_release', 'production_done', 'completed',
    ],
}


def _warehouse_signature():
    """Cheap fingerprint of everything the warehouse page renders.

    Changes whenever an item enters the warehouse queue, is received,
    has its quantity edited, or its batch is deleted — so the client can
    detect updates without re-rendering the whole page on every poll.
    """
    rows = (
        DispositionItem.objects
        .filter(**_WAREHOUSE_ITEM_FILTER)
        .order_by('pk')
        # The round-trip quantities are part of the fingerprint too, or the
        # page would not repaint when production returns a line.
        .values_list('pk', 'status', 'qty_received', 'pending_qty',
                     'rework_released_qty', 'reworked_qty')
    )
    raw = ';'.join(':'.join(str(v) for v in row) for row in rows)
    return hashlib.md5(raw.encode('utf-8'), usedforsecurity=False).hexdigest()


@login_required
def warehouse_status(request):
    """Polling endpoint — returns a signature of the warehouse queue so the
    page can auto-sync when items are inserted or updated by others."""
    if not _is_warehouse(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    return JsonResponse({'signature': _warehouse_signature()})


@login_required
def warehouse_view(request):
    if not _is_warehouse(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)

    can_edit = _can_warehouse_edit(request.user)

    if request.method == 'POST':
        if not can_edit:
            if _is_ajax(request):
                return JsonResponse({'ok': False, 'error': 'View-only access.'}, status=403)
            messages.error(request, 'You have view-only access and cannot update warehouse receipts.')
            return redirect('material_rework:warehouse')

        action = request.POST.get('action', 'receive')

        if action == 'edit_received':
            item_id = request.POST.get('item_id', '').strip()
            raw_qty = request.POST.get('new_qty_received', '0').strip()
            with transaction.atomic():
                try:
                    item = DispositionItem.objects.select_for_update().get(pk=item_id)
                except DispositionItem.DoesNotExist:
                    if _is_ajax(request):
                        return JsonResponse({'ok': False, 'error': 'Item not found.'}, status=404)
                    messages.error(request, 'Item not found.')
                    return redirect('material_rework:warehouse')
                try:
                    new_qty = _parse_qty_int(raw_qty)
                except InvalidOperation:
                    new_qty = 0
                wh_total = item.qty_received + item.pending_qty  # invariant: total originally dispatched to WH
                new_qty = min(new_qty, wh_total)
                item.qty_received = new_qty
                item.pending_qty  = wh_total - new_qty
                if item.pending_qty <= 0:
                    item.pending_qty = 0
                    item.status      = 'received'
                    if not item.warehouse_received_at:
                        item.warehouse_received_at = timezone.now()
                else:
                    item.status                = 'pending_warehouse'
                    item.warehouse_received_at = None
                item.warehouse_remarks = request.POST.get('warehouse_remarks', '').strip()
                item.inward_number     = request.POST.get('inward_number', '').strip() or item.inward_number
                item.outward_number    = request.POST.get('outward_number', '').strip() or item.outward_number
                item.save()
                batch       = item.batch
                has_pending = batch.items.filter(status='pending_warehouse').exists()
                if has_pending and batch.status == 'completed':
                    batch.status = 'production_done'
                    batch.save()
                elif not has_pending and batch.status == 'production_done':
                    batch.status = 'completed'
                    batch.save()
            messages.success(request, f'Received quantity updated for {item.material}.')
            if _is_ajax(request):
                return JsonResponse({
                    'ok':               True,
                    'action':           'edit_received',
                    'item_id':          item.pk,
                    'batch_id':         item.batch_id,
                    'qty_received':     int(item.qty_received),
                    'pending_qty':      int(item.pending_qty),
                    'accepted_qty':     int(item.qty_received + item.pending_qty),  # wh_total
                    'status':           item.status,
                    'warehouse_remarks': item.warehouse_remarks,
                })

        else:
            item_ids = request.POST.getlist('item_ids')
            if item_ids:
                now            = timezone.now()
                received_count = 0
                with transaction.atomic():
                    for raw_id in item_ids:
                        try:
                            item = DispositionItem.objects.select_for_update().get(
                                pk=raw_id, status='pending_warehouse'
                            )
                        except DispositionItem.DoesNotExist:
                            continue
                        raw_recv = request.POST.get(f'recv_{raw_id}', '')
                        try:
                            recv_qty = _parse_qty_int(raw_recv)
                        except InvalidOperation:
                            recv_qty = item.pending_qty
                        recv_qty = min(recv_qty, item.pending_qty)
                        if recv_qty <= 0:
                            continue
                        item.qty_received += recv_qty
                        item.pending_qty  -= recv_qty
                        if item.pending_qty <= 0:
                            item.pending_qty          = 0
                            item.status               = 'received'
                            item.warehouse_received_at = now
                        item.warehouse_remarks = request.POST.get(f'wh_remark_{raw_id}', '').strip()
                        item.inward_number     = request.POST.get(f'inward_{raw_id}', '').strip()
                        item.outward_number    = request.POST.get(f'outward_{raw_id}', '').strip()
                        item.save()
                        received_count += 1

                    affected_batch_ids = list(
                        DispositionItem.objects.filter(pk__in=item_ids)
                        .values_list('batch_id', flat=True).distinct()
                    )
                    # Two set-based queries instead of an exists() and a
                    # save() per affected batch. A warehouse receipt covering
                    # lines from ten batches ran twenty queries here.
                    still_pending = set(
                        DispositionBatch.objects
                        .filter(pk__in=affected_batch_ids,
                                items__status='pending_warehouse')
                        .values_list('pk', flat=True)
                    )
                    DispositionBatch.objects.filter(
                        pk__in=affected_batch_ids,
                    ).exclude(pk__in=still_pending).update(status='completed')
                messages.success(request, f'{received_count} item(s) updated.')
            if _is_ajax(request):
                return JsonResponse({'ok': True, 'action': 'receive'})

        return redirect('material_rework:warehouse')

    qs = DispositionItem.objects.filter(
        **_WAREHOUSE_ITEM_FILTER
    ).select_related(
        'batch', 'batch__vertical', 'batch__department', 'batch__requested_by'
    ).order_by('-batch__uploaded_at')

    all_items = list(qs)
    for _item in all_items:
        # wh_qty = total originally sent to warehouse; stays constant as received+pending always sum to it
        _item.wh_qty = _item.qty_received + _item.pending_qty

    pending_items  = [i for i in all_items if i.status == 'pending_warehouse']
    received_items = [i for i in all_items if i.status in ('received', 'accepted', 'received_discrepancy')]

    # The two legs warehouse owns in the round trip, plus the one it can only
    # watch. Kept as flat lists rather than batch-grouped: each line moves on
    # its own, so grouping would imply a batch-level action that does not exist.
    release_items   = [i for i in all_items if i.status == 'pending_rework_handover']
    receipt_items   = [i for i in all_items if i.status == 'pending_return_receipt']
    at_production   = [i for i in all_items if i.status == 'in_rework']

    # Group pending items by batch (preserve order)
    pending_groups_map   = {}
    pending_groups_order = []
    for item in pending_items:
        bid = item.batch_id
        if bid not in pending_groups_map:
            pending_groups_map[bid] = {'batch': item.batch, 'items': []}
            pending_groups_order.append(bid)
        pending_groups_map[bid]['items'].append(item)
    pending_groups = [pending_groups_map[bid] for bid in pending_groups_order]

    # Group received items by batch (preserve order)
    received_groups_map   = {}
    received_groups_order = []
    for item in received_items:
        bid = item.batch_id
        if bid not in received_groups_map:
            received_groups_map[bid] = {'batch': item.batch, 'items': []}
            received_groups_order.append(bid)
        received_groups_map[bid]['items'].append(item)
    received_groups = [received_groups_map[bid] for bid in received_groups_order]

    # Build JS-side batch → items map for the detail panel
    batch_items_map = {}
    for item in all_items:
        bid = str(item.batch_id)
        if bid not in batch_items_map:
            if item.batch.requested_by:
                req_by = item.batch.requested_by.name
            elif item.batch.source:
                req_by = item.batch.get_source_display()
            else:
                req_by = '—'
            batch_items_map[bid] = {
                'ref':         item.batch.reference_number or str(item.batch.id),
                'vertical':    item.batch.vertical.name,
                'department':  item.batch.department.name,
                'requestedBy': req_by,
                'uploadedBy':  item.batch.uploaded_by_name or '',
                'batchStatus': item.batch.status,
                'subject':     item.batch.subject or '',
                'items':       [],
            }
        batch_items_map[bid]['items'].append({
            'id':                   item.pk,
            'material':             item.material,
            'batchNo':              item.batch_no or '',
            'boxNo':                item.box_no or '',
            'qty':                  int(item.qty),
            'acceptedQty':          item.wh_qty,
            'qtyReceived':          int(item.qty_received),
            'pendingQty':           int(item.pending_qty),
            'status':               item.status,
            'remark':               item.remark or '',
            'prodRemarks':          item.production_remarks or '',
            'whAt':                 item.warehouse_received_at.strftime('%d %b %Y %H:%M') if item.warehouse_received_at else '—',
            'whRemarks':            item.warehouse_remarks or '',
            'inwardNumber':         item.inward_number or '',
            'outwardNumber':        item.outward_number or '',
            'serialNumber':         item.serial_number or '',
            'country':              item.country or '',
            'mfgDate':              item.mfg_date or '',
            'expDate':              item.exp_date or '',
            'fromStorageLocation':  item.from_storage_location or '',
            'toStorageLocation':    item.to_storage_location or '',
            'uploadedAt':           item.item_date or '',
        })

    return render(request, 'material_rework/warehouse.html', {
        'items':            pending_items,
        'release_items':    release_items,
        'receipt_items':    receipt_items,
        'at_production':    at_production,
        'pending_groups':   pending_groups,
        'received_items':   received_items,
        'received_groups':  received_groups,
        'batch_items_json': json.dumps(batch_items_map),
        'can_edit':         can_edit,
        'wh_signature':     _warehouse_signature(),
    })


@login_required
@require_POST
def delete_batch(request, batch_id):
    batch = get_object_or_404(DispositionBatch, pk=batch_id)
    if not (_is_md_admin(request.user) or batch.uploaded_by_email == request.user.email):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'No permission to delete this batch.'}, status=403)
        messages.error(request, 'You do not have permission to delete this batch.')
        return redirect(request.POST.get('next', '/material-rework/my-uploads/'))
    ref = batch.reference_number or f'#{batch.pk}'
    # Delete items first in a single bulk DELETE before removing the batch
    # (faster than relying on ON DELETE CASCADE for large item sets)
    DispositionItem.objects.filter(batch=batch).delete()
    batch.delete()
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'batch_id': batch_id, 'ref': ref})
    messages.success(request, f'Batch {ref} deleted successfully.')
    return redirect(request.POST.get('next', '/material-rework/my-uploads/'))


def _send_hod_decision_email(request, batch, action):
    """Notify the uploader when HOD approves or rejects via the in-platform inbox."""
    if not batch.uploaded_by_email:
        return
    approved = action == 'approve'
    verb     = 'Approved' if approved else 'Rejected'
    subject  = batch.subject or f'[MD] Batch {batch.reference_number or batch.pk} {verb} by HOD'
    hod_name = request.user.get_full_name() or request.user.username
    reviewed_at = batch.hod_reviewed_at.strftime('%d %b %Y, %H:%M') if batch.hod_reviewed_at else ''
    ctx = {
        'batch':       batch,
        'items':       batch.items.all(),
        'approved':    approved,
        'hod_name':    hod_name,
        'reviewed_at': reviewed_at,
    }
    html_body  = render_to_string('material_rework/email/hod_decision_notification.html', ctx)
    plain_body = (
        f"Dear {batch.uploaded_by_name or 'Team'},\n\n"
        f"Your material rework batch has been {verb.lower()} by {hod_name}.\n\n"
        f"Reference  : {batch.reference_number or batch.pk}\n"
        f"Vertical   : {batch.vertical.name}\n"
        f"Department : {batch.department.name}\n"
        + (f"Comments   : {batch.hod_comments}\n" if batch.hod_comments else '') +
        "\nCONFIDENTIAL EMAIL: Do not forward this email to anyone. If this email is forwarded and accessed by another person, the forwarder will be responsible and liable for any misuse of the information.\n\n"
        "Regards,\nNexGen Automation"
    )
    try:
        send_mail(
            subject=subject,
            message=plain_body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@merillife.com'),
            recipient_list=[batch.uploaded_by_email],
            html_message=html_body,
            fail_silently=True,
        )
    except Exception:
        pass


def _get_hod_departments(user):
    """Return departments where the given user is listed as an HOD email."""
    if not user.is_authenticated or not user.email:
        return []
    email = user.email.lower()
    # icontains narrows in the database; the exact check stays in Python
    # because hod_email is a comma-separated list and a substring match would
    # also admit bob@x.com from bobby@x.com.
    candidates = Department.objects.filter(
        is_active=True, hod_email__icontains=email,
    )
    return [
        dept for dept in candidates
        if email in [e.lower() for e in _parse_emails(dept.hod_email)]
    ]


@login_required
def hod_inbox(request):
    is_admin     = _is_md_admin(request.user)
    is_hod_group = request.user.groups.filter(name='MR HOD').exists()
    can_approve  = _can_hod_action(request.user)
    # Email-matched departments (for scoping when not admin/group-HOD)
    hod_depts    = _get_hod_departments(request.user) if not (is_admin or is_hod_group) else []

    # Access: admin OR MD HOD group OR email-matched HOD
    if not is_admin and not can_approve:
        return render(request, 'material_rework/access_denied.html', status=403)

    if request.method == 'POST':
        is_ajax = _is_ajax(request)

        if not can_approve:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': 'View-only access. Only HODs can approve or reject.'}, status=403)
            messages.error(request, 'You have view-only access.')
            return redirect('material_rework:hod_inbox')

        batch_id = request.POST.get('batch_id', '').strip()
        action   = request.POST.get('action', '').strip()
        comments = request.POST.get('comments', '').strip()

        if not batch_id:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': 'Batch ID missing.'})
            return redirect('material_rework:hod_inbox')

        if action not in ('approve', 'reject'):
            if is_ajax:
                return JsonResponse({'ok': False, 'error': 'Invalid action.'})
            return redirect('material_rework:hod_inbox')

        # Mandatory on both approve and reject, same rule as PPIC — the
        # decision is the permanent record and needs a stated reason.
        if not comments:
            verb = 'approving' if action == 'approve' else 'rejecting'
            msg = f'A remark is required before {verb} this batch.'
            if is_ajax:
                return JsonResponse({'ok': False, 'error': msg}, status=400)
            messages.error(request, msg)
            return redirect('material_rework:hod_inbox')

        try:
            qs = DispositionBatch.objects.filter(pk=batch_id, status='pending_hod')
            if not is_admin and not is_hod_group:
                qs = qs.filter(department__in=hod_depts)
            batch = qs.get()
        except DispositionBatch.DoesNotExist:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': 'Batch not found or already reviewed.'})
            messages.error(request, 'Batch not found or already reviewed.')
            return redirect('material_rework:hod_inbox')

        try:
            with transaction.atomic():
                batch.hod_reviewed_at = timezone.now()
                batch.hod_comments    = comments
                if action == 'approve':
                    batch.status = 'hod_approved'
                    batch.items.all().update(status='pending_production')
                else:
                    batch.status = 'hod_rejected'
                    batch.items.all().update(status='hod_rejected')
                batch.save()
        except Exception as e:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': f'Could not save: {e}'})
            messages.error(request, f'Could not process decision: {e}')
            return redirect('material_rework:hod_inbox')

        _send_hod_decision_email(request, batch, action)

        if is_ajax:
            return JsonResponse({'ok': True, 'batch_id': int(batch_id), 'action': action})
        verb = 'approved' if action == 'approve' else 'rejected'
        messages.success(request, f'Batch {batch.reference_number or batch.pk} {verb}.')
        return redirect('material_rework:hod_inbox')

    items_qs = DispositionItem.objects.only(
        'id', 'batch_id', 'material', 'serial_number', 'batch_no', 'qty',
        'box_no', 'remark', 'country', 'from_country', 'to_country',
        'mfg_date', 'exp_date',
        'from_storage_location', 'to_storage_location', 'item_date',
    )
    base_qs = DispositionBatch.objects.filter(status='pending_hod'
        ).select_related('vertical', 'department', 'requested_by', 'rework_type', 'location'
        ).prefetch_related(Prefetch('items', queryset=items_qs)
        ).annotate(item_count=Count('items', distinct=True)
        ).order_by(*PRIORITY_ORDERING)

    # Admin / MR HOD group → all pending batches; email-based HOD → their departments only
    if is_admin or is_hod_group:
        pending_batches = base_qs
    else:
        pending_batches = base_qs.filter(department__in=hod_depts)

    return render(request, 'material_rework/hod_inbox.html', {
        'batches':     pending_batches,
        'hod_depts':   hod_depts,
        'is_admin':    is_admin,
        'can_approve': can_approve,
    })


@login_required
def report_view(request):
    if not (_is_md_admin(request.user) or _is_warehouse(request.user) or _is_production(request.user)):
        return render(request, 'material_rework/access_denied.html', status=403)

    from .models import Vertical, Department

    # ── Filters ──────────────────────────────────────────────
    f_status   = request.GET.get('status', '').strip()
    f_vertical = request.GET.get('vertical', '').strip()
    f_dept     = request.GET.get('department', '').strip()
    f_from     = request.GET.get('date_from', '').strip()
    f_to       = request.GET.get('date_to', '').strip()

    batches_qs = DispositionBatch.objects.prefetch_related('items').select_related(
        'vertical', 'department', 'requested_by'
    )
    if f_status:
        batches_qs = batches_qs.filter(status=f_status)
    if f_vertical:
        batches_qs = batches_qs.filter(vertical_id=f_vertical)
    if f_dept:
        batches_qs = batches_qs.filter(department_id=f_dept)
    if f_from:
        try:
            from datetime import datetime
            batches_qs = batches_qs.filter(uploaded_at__date__gte=datetime.strptime(f_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if f_to:
        try:
            from datetime import datetime
            batches_qs = batches_qs.filter(uploaded_at__date__lte=datetime.strptime(f_to, '%Y-%m-%d').date())
        except ValueError:
            pass
    batches = batches_qs.annotate(
        total_qty_sum=Sum('items__qty'),
        # item counts
        accepted_count=Count('items', filter=Q(items__status='accepted'), distinct=True),
        pending_wh_count=Count('items', filter=Q(items__status='pending_warehouse'), distinct=True),
        received_count=Count('items', filter=Q(items__status='received'), distinct=True),
        rejected_count=Count('items', filter=Q(items__status='rejected'), distinct=True),
        # quantity totals
        prod_accepted_qty=Sum('items__accepted_qty'),
        wh_received_qty=Sum('items__qty_received'),
        wh_pending_qty=Sum('items__pending_qty'),
    ).order_by('-uploaded_at')

    # ── Analytics (always over full dataset) ─────────────────
    batch_counts = {s: 0 for s in [
        'pending_ppic', 'ppic_approved', 'ppic_rejected',
        'pending_hod', 'hod_approved', 'hod_rejected', 'production_done', 'completed',
    ]}
    for row in DispositionBatch.objects.values('status').annotate(c=Count('id')):
        if row['status'] in batch_counts:
            batch_counts[row['status']] = row['c']

    item_counts = {s: 0 for s in [
        'pending_ppic', 'ppic_rejected',
        'pending_hod', 'pending_production', 'pending_warehouse',
        'accepted', 'partially_accepted', 'rejected', 'received', 'hod_rejected',
    ]}
    for row in DispositionItem.objects.values('status').annotate(c=Count('id')):
        if row['status'] in item_counts:
            item_counts[row['status']] = row['c']

    qty = DispositionItem.objects.aggregate(
        total_qty=Sum('qty'),
        total_accepted=Sum('accepted_qty'),
        total_received=Sum('qty_received'),
    )

    top_depts     = list(DispositionBatch.objects.values('department__name').annotate(c=Count('id')).order_by('-c')[:7])
    top_verticals = list(DispositionBatch.objects.values('vertical__name').annotate(c=Count('id')).order_by('-c')[:7])
    recent        = list(DispositionBatch.objects.select_related('vertical', 'department').order_by('-uploaded_at')[:10])

    # ── Monthly trend (last 6 months) ────────────────────────
    from django.utils import timezone as tz
    from dateutil.relativedelta import relativedelta
    today = tz.now().date()
    monthly = []
    for i in range(5, -1, -1):
        m_start = (today.replace(day=1) - relativedelta(months=i))
        m_end   = (m_start + relativedelta(months=1))
        cnt     = DispositionBatch.objects.filter(uploaded_at__date__gte=m_start, uploaded_at__date__lt=m_end).count()
        monthly.append({'label': m_start.strftime('%b %Y'), 'count': cnt})

    verticals_all   = list(Vertical.objects.filter(is_active=True).order_by('name'))
    departments_all = list(Department.objects.filter(is_active=True).select_related('vertical').order_by('name'))

    # ── Per-user progress ────────────────────────────────────
    # The report said what happened to the material but never who moved it.
    # Filtered on the same window and scope as the tables above, so the two
    # cannot report different periods on one screen. Parsed dates are reused
    # rather than re-parsed: the filters above accept a malformed value by
    # ignoring it, and re-parsing here would apply a filter the rest of the
    # page did not.
    from datetime import datetime as _dt

    def _as_date(raw):
        try:
            return _dt.strptime(raw, '%Y-%m-%d').date() if raw else None
        except ValueError:
            return None

    progress = userprogress.leaderboard(
        date_from=_as_date(f_from),
        date_to=_as_date(f_to),
        vertical_id=f_vertical or None,
        department_id=f_dept or None,
    )

    # ── Dashboard scope ──────────────────────────────────────
    # The dashboard keeps its existing overview as the default and gains two
    # more views: every user at once, and one selected person on their own.
    # Driven by query parameters rather than JavaScript so a particular view
    # is a shareable URL and survives a reload.
    scope = userprogress.resolve_scope(request.GET.get('scope', ''))
    scope_user = request.GET.get('user', '').strip()
    user_options = userprogress.user_options()

    # The active filters as a query string, so the scope links can carry them.
    # Built here rather than assembled in the template: the scope switch must
    # not silently drop the period the reader chose, and a link that rebuilds
    # only some of the parameters is how that happens.
    from django.http import QueryDict

    base = QueryDict(mutable=True)
    base['tab'] = 'dash'
    for key, value in (
        ('status', f_status), ('vertical', f_vertical), ('department', f_dept),
        ('date_from', f_from), ('date_to', f_to),
    ):
        if value:
            base[key] = value
    scope_query = base.urlencode()

    user_dash = None
    if scope == userprogress.SCOPE_ONE_USER:
        # Nobody picked yet: fall back to the first person in the list rather
        # than showing an empty dashboard the reader would take for "this user
        # has done nothing".
        if not scope_user and user_options:
            scope_user = user_options[0]['key']
        if scope_user:
            user_dash = userprogress.user_dashboard(
                scope_user,
                date_from=_as_date(f_from),
                date_to=_as_date(f_to),
                vertical_id=f_vertical or None,
                department_id=f_dept or None,
            )

    return render(request, 'material_rework/report.html', {
        'progress':         progress,
        'scope':            scope,
        'scope_choices':    userprogress.SCOPE_CHOICES,
        'scope_overview':   userprogress.SCOPE_OVERVIEW,
        'scope_all_users':  userprogress.SCOPE_ALL_USERS,
        'scope_one_user':   userprogress.SCOPE_ONE_USER,
        'scope_user':       scope_user,
        'scope_query':      scope_query,
        'user_options':     user_options,
        'user_dash':        user_dash,
        'batches':          batches,
        'batch_counts':     batch_counts,
        'item_counts':      item_counts,
        'qty':              qty,
        'top_depts':        top_depts,
        'top_verticals':    top_verticals,
        'recent':           recent,
        'total_batches':    sum(batch_counts.values()),
        'total_items':      sum(item_counts.values()),
        'monthly':          monthly,
        'verticals_all':    verticals_all,
        'departments_all':  departments_all,
        'f_status':         f_status,
        'f_vertical':       f_vertical,
        'f_dept':           f_dept,
        'f_from':           f_from,
        'f_to':             f_to,
        'filtered_count':   batches.count(),
    })


@login_required
def report_download(request):
    if not (_is_md_admin(request.user) or _is_warehouse(request.user) or _is_production(request.user)):
        return HttpResponse(status=403)

    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    fmt = request.GET.get('format', 'excel')

    # ── Apply same filters as report_view ────────────────────
    f_status   = request.GET.get('status', '').strip()
    f_vertical = request.GET.get('vertical', '').strip()
    f_dept     = request.GET.get('department', '').strip()
    f_from     = request.GET.get('date_from', '').strip()
    f_to       = request.GET.get('date_to', '').strip()

    batches = DispositionBatch.objects.prefetch_related('items').select_related(
        'vertical', 'department', 'requested_by'
    )
    if f_status:
        batches = batches.filter(status=f_status)
    if f_vertical:
        batches = batches.filter(vertical_id=f_vertical)
    if f_dept:
        batches = batches.filter(department_id=f_dept)
    if f_from:
        try:
            from datetime import datetime as _dt
            batches = batches.filter(uploaded_at__date__gte=_dt.strptime(f_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if f_to:
        try:
            from datetime import datetime as _dt
            batches = batches.filter(uploaded_at__date__lte=_dt.strptime(f_to, '%Y-%m-%d').date())
        except ValueError:
            pass
    batches = batches.order_by('-uploaded_at')

    # ── Shared stage helpers ─────────────────────────────────
    def _hod_stage(batch, item):
        if item.status == 'hod_rejected':
            return 'HOD Rejected'
        if batch.status == 'pending_hod':
            return 'Awaiting HOD'
        return 'HOD OK'

    def _prod_stage(item):
        if item.status == 'pending_production':
            return 'Pending Prod.'
        if item.production_reviewed_at:
            return 'Prod. Done'
        return '—'

    def _wh_stage(item):
        return {
            'received':               'Received',
            'received_discrepancy':   'Received (Mismatch)',
            'pending_rework_handover': 'Pending Release',
            'in_rework':              'At Production',
            'pending_return_receipt': 'Pending Receipt',
            'accepted':               'No WH Step',
            'pending_warehouse':      'Pending WH',
            'rejected':               'Rejected',
        }.get(item.status, '—')

    def _hod_decision(batch):
        if batch.status == 'hod_rejected':
            return 'Rejected'
        if batch.status == 'pending_hod':
            return 'Pending'
        return 'Approved'

    # ── CSV ──────────────────────────────────────────────────
    if fmt == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="disposition_report.csv"'
        w = csv.writer(response)
        # Shared 17-column layout: batch header row uses batch fields,
        # item sub-rows use item fields in the same column positions.
        w.writerow([
            'Ref #', 'Source', 'Vertical', 'Department',
            'Submitted By', 'Submitted Email', 'Submit Date',
            'Batch Status', 'HOD Decision', 'HOD Date', 'HOD Comments', 'Email Subject',
            'Prod. Reviewed By', 'Prod. Reviewer Email', 'Prod. Review Date',
            'PPIC Reviewed By', 'PPIC Review Date', 'PPIC Comments',
            'Items', 'Total Qty', 'Accepted', 'Pending WH', 'Received', 'Rejected / Item Status',
            # Item-level columns (filled for item sub-rows only)
            'Serial Number', 'Country', 'Code', 'Batch', 'Mfg Date', 'Exp Date',
            'From Storage Location', 'To Storage Location',
            'Qty', 'Prod. Accepted Qty', 'WH Received Qty', 'WH Pending Qty',
            'Box No', 'Upload Remarks', 'Uploaded At',
            'Production Remarks', 'WH Remarks', 'Inward No', 'Outward No',
            # Appended (44-50) rather than slotted in next to their relatives:
            # every column below is position-commented, so inserting mid-list
            # would make those comments wrong.
            'Priority', 'Type of Rework', 'Location', 'Slot From', 'Slot To',
            'From Country', 'To Country',
        ])
        for b in batches:
            items_all = list(b.items.all())
            total_qty         = sum(int(i.qty) for i in items_all)
            prod_accepted_qty = sum(int(i.accepted_qty) for i in items_all)
            wh_received_qty   = sum(int(i.qty_received) for i in items_all)
            wh_pending_qty    = sum(int(i.pending_qty) for i in items_all)
            rejected_cnt      = sum(1 for i in items_all if i.status == 'rejected')
            w.writerow([
                b.reference_number or f'#{b.pk}',
                b.requested_by.name if b.requested_by else (b.get_source_display() if b.source else '—'),
                b.vertical.name, b.department.name,
                b.uploaded_by_name, b.uploaded_by_email,
                b.uploaded_at.strftime('%d %b %Y %H:%M') if b.uploaded_at else '',
                b.get_status_display(),
                _hod_decision(b),
                b.hod_reviewed_at.strftime('%d %b %Y %H:%M') if b.hod_reviewed_at else '—',
                b.hod_comments or '—',
                b.subject or '—',
                b.production_reviewed_by_name or '—',
                b.production_reviewed_by_email or '—',
                b.production_reviewed_at.strftime('%d %b %Y %H:%M') if b.production_reviewed_at else '—',
                b.ppic_reviewed_by_name or '—',
                b.ppic_reviewed_at.strftime('%d %b %Y %H:%M') if b.ppic_reviewed_at else '—',
                b.ppic_comments or '—',
                len(items_all), total_qty,
                prod_accepted_qty, wh_pending_qty, wh_received_qty, rejected_cnt,
                # 44-50: batch-level, so the two item-level country columns
                # stay blank on this row.
                b.get_priority_display(),
                b.rework_type.name if b.rework_type else '—',
                b.location.name if b.location else '—',
                b.slot_from.strftime('%d %b %Y') if b.slot_from else '—',
                b.slot_to.strftime('%d %b %Y') if b.slot_to else '—',
                '', '',
            ])
            for i_num, item in enumerate(items_all, 1):
                w.writerow([
                    f'  └ {i_num}',            # 1  Ref #
                    '',                         # 2  Source
                    '',                         # 3  Vertical
                    '',                         # 4  Department
                    '',                         # 5  Submitted By
                    '',                         # 6  Submitted Email
                    '',                         # 7  Submit Date
                    '',                         # 8  Batch Status
                    _hod_stage(b, item),        # 9  HOD Decision
                    '',                         # 10 HOD Date
                    '',                         # 11 HOD Comments
                    '',                         # 12 Email Subject
                    '',                         # 13 Prod. Reviewed By
                    '',                         # 14 Prod. Reviewer Email
                    '',                         # 15 Prod. Review Date
                    '',                         # 16 PPIC Reviewed By
                    '',                         # 17 PPIC Review Date
                    '',                         # 18 PPIC Comments
                    '',                         # 19 Items
                    '',                         # 20 Total Qty
                    '',                         # 21 Accepted
                    '',                         # 22 Pending WH
                    '',                         # 23 Received
                    item.get_status_display(),  # 24 Rejected / Item Status
                    item.serial_number or '',   # 25 Serial Number
                    item.country or '',         # 26 Country
                    item.material,              # 27 Code
                    item.batch_no or '',        # 28 Batch
                    item.mfg_date or '',        # 29 Mfg Date
                    item.exp_date or '',        # 30 Exp Date
                    item.from_storage_location or '',  # 31 From Storage
                    item.to_storage_location or '',    # 32 To Storage
                    int(item.qty),              # 33 Qty
                    int(item.accepted_qty),     # 34 Prod. Accepted Qty
                    int(item.qty_received),     # 35 WH Received Qty
                    int(item.pending_qty),      # 36 WH Pending Qty
                    item.box_no or '',          # 37 Box No
                    item.remark or '',          # 38 Upload Remarks
                    item.item_date or '',       # 39 Uploaded At
                    item.production_remarks or '',  # 40 Production Remarks
                    item.warehouse_remarks or '',   # 41 WH Remarks
                    item.inward_number or '',       # 42 Inward No
                    item.outward_number or '',      # 43 Outward No
                    '', '', '', '', '',             # 44-48 batch-level, blank here
                    item.from_country or '',        # 49 From Country
                    item.to_country or '',          # 50 To Country
                ])
        return response

    # ── Excel ────────────────────────────────────────────────
    wb = openpyxl.Workbook()

    # ── Helpers ──
    def hdr_fill(hex_color):
        return PatternFill('solid', fgColor=hex_color)

    def hdr_font(bold=True, color='FFFFFF'):
        return Font(bold=bold, color=color, size=10)

    def thin_border():
        s = Side(style='thin', color='D1D5DB')
        return Border(left=s, right=s, top=s, bottom=s)

    def set_col_widths(ws, widths):
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    def write_header_row(ws, row_num, headers, fill_hex, font_color='FFFFFF'):
        fill = hdr_fill(fill_hex)
        font = hdr_font(color=font_color)
        for col, text in enumerate(headers, 1):
            c = ws.cell(row=row_num, column=col, value=text)
            c.fill = fill
            c.font = font
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            c.border = thin_border()

    # ── Single sheet: Report + Summary at bottom ─────────────
    ws = wb.active
    ws.title = 'Disposition Report'
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = 'A2'

    report_headers = [
        'Ref #', 'Source', 'Vertical', 'Department',
        'Submitted By', 'Submitted Email', 'Submit Date',
        'Batch Status', 'HOD Decision', 'HOD Date', 'HOD Comments', 'Email Subject',
        'Prod. Reviewed By', 'Prod. Reviewer Email', 'Prod. Review Date',
        'PPIC Reviewed By', 'PPIC Review Date', 'PPIC Comments',
        'Items', 'Total Qty', 'Accepted', 'Pending WH', 'Received', 'Rejected / Item Status',
        # Item-level columns (cols 25-43)
        'Serial Number', 'Country', 'Code', 'Batch', 'Mfg Date', 'Exp Date',
        'From Storage Location', 'To Storage Location',
        'Qty', 'Prod. Accepted Qty', 'WH Received Qty', 'WH Pending Qty',
        'Box No', 'Upload Remarks', 'Uploaded At',
        'Production Remarks', 'WH Remarks', 'Inward No', 'Outward No',
        # Appended (44-50) — see the CSV header note above.
        'Priority', 'Type of Rework', 'Location', 'Slot From', 'Slot To',
        'From Country', 'To Country',
    ]
    write_header_row(ws, 1, report_headers, '06152B')
    ws.row_dimensions[1].height = 22
    set_col_widths(ws, [
        22, 14, 24, 20,          # 1-4
        22, 28, 16,              # 5-7
        14, 12, 16, 30, 30,      # 8-12
        22, 26, 16,              # 13-15
        22, 16, 30,              # 16-18 PPIC
        7, 9, 8, 10, 9, 18,     # 19-24
        22, 10, 18, 12, 10, 10,  # 25-30
        24, 24,                  # 31-32
        7, 12, 12, 12,           # 33-36
        10, 32, 14,              # 37-39
        32, 32, 12, 12,          # 40-43
        10, 20, 20, 13, 13,      # 44-48 priority / rework type / location / slot
        14, 14,                  # 49-50 from / to country
    ])

    batch_row_fills = {
        'completed':       hdr_fill('D1FAE5'),
        'hod_rejected':    hdr_fill('FEE2E2'),
        'production_done': hdr_fill('EDE9FE'),
        'hod_approved':    hdr_fill('DBEAFE'),
        'pending_hod':     hdr_fill('FEF3C7'),
    }
    item_sub_fill = PatternFill('solid', fgColor='F8FAFC')

    # ── Report data rows ──────────────────────────────────────
    row_idx = 2
    for b in batches:
        items_all = list(b.items.all())
        total_qty    = sum(int(i.qty) for i in items_all)
        accepted_cnt = sum(1 for i in items_all if i.status == 'accepted')
        pend_wh_cnt  = sum(1 for i in items_all if i.status == 'pending_warehouse')
        received_cnt = sum(1 for i in items_all if i.status == 'received')
        rejected_cnt = sum(1 for i in items_all if i.status == 'rejected')

        b_fill = batch_row_fills.get(b.status, PatternFill())
        batch_vals = [
            b.reference_number or f'#{b.pk}',
            b.requested_by.name if b.requested_by else (b.get_source_display() if b.source else '—'),
            b.vertical.name,
            b.department.name,
            b.uploaded_by_name,
            b.uploaded_by_email,
            b.uploaded_at.strftime('%d %b %Y %H:%M') if b.uploaded_at else '',
            b.get_status_display(),
            _hod_decision(b),
            b.hod_reviewed_at.strftime('%d %b %Y %H:%M') if b.hod_reviewed_at else '—',
            b.hod_comments or '—',
            b.subject or '—',
            b.production_reviewed_by_name or '—',
            b.production_reviewed_by_email or '—',
            b.production_reviewed_at.strftime('%d %b %Y %H:%M') if b.production_reviewed_at else '—',
            b.ppic_reviewed_by_name or '—',
            b.ppic_reviewed_at.strftime('%d %b %Y %H:%M') if b.ppic_reviewed_at else '—',
            b.ppic_comments or '—',
            len(items_all),
            total_qty,
            accepted_cnt,
            pend_wh_cnt,
            received_cnt,
            rejected_cnt,
            # 44-50: batch-level, so the two item-level country columns stay
            # blank on this row.
            b.get_priority_display(),
            b.rework_type.name if b.rework_type else '—',
            b.location.name if b.location else '—',
            b.slot_from.strftime('%d %b %Y') if b.slot_from else '—',
            b.slot_to.strftime('%d %b %Y') if b.slot_to else '—',
            '', '',
        ]
        for col_idx, val in enumerate(batch_vals, 1):
            c = ws.cell(row=row_idx, column=col_idx, value=val)
            c.fill = b_fill
            c.font = Font(bold=True, size=9)
            c.border = thin_border()
            c.alignment = Alignment(vertical='center', wrap_text=True)
        ws.row_dimensions[row_idx].height = 20
        row_idx += 1

        for i_num, item in enumerate(items_all, 1):
            # 30 columns total:
            #   cols  1-18 → batch-level headers (leave blank except col 1)
            #   cols 19-30 → dedicated item fields
            item_vals = [
                f'  └ {i_num}',       # 1  Ref #
                '',                   # 2  Source
                '',                   # 3  Vertical
                '',                   # 4  Department
                '',                   # 5  Submitted By
                '',                   # 6  Submitted Email
                '',                   # 7  Submit Date
                '',                   # 8  Batch Status
                _hod_stage(b, item),  # 9  HOD Decision
                '',                   # 10 HOD Date
                '',                   # 11 HOD Comments
                '',                   # 12 Email Subject
                '',                   # 13 Prod. Reviewed By
                '',                   # 14 Prod. Reviewer Email
                '',                   # 15 Prod. Review Date
                '',                   # 16 PPIC Reviewed By
                '',                   # 17 PPIC Review Date
                '',                   # 18 PPIC Comments
                '',                   # 19 Items
                '',                   # 20 Total Qty
                '',                   # 21 Accepted
                '',                   # 22 Pending WH
                '',                   # 23 Received
                item.get_status_display(),         # 24 Item Status
                item.serial_number or '',          # 25 Serial Number
                item.country or '',                # 26 Country
                item.material,                     # 27 Code
                item.batch_no or '',               # 28 Batch
                item.mfg_date or '',               # 29 Mfg Date
                item.exp_date or '',               # 30 Exp Date
                item.from_storage_location or '',  # 31 From Storage
                item.to_storage_location or '',    # 32 To Storage
                int(item.qty),                     # 33 Qty
                int(item.accepted_qty),            # 34 Prod. Accepted Qty
                int(item.qty_received),            # 35 WH Received Qty
                int(item.pending_qty),             # 36 WH Pending Qty
                item.box_no or '',                 # 37 Box No
                item.remark or '',                 # 38 Upload Remarks
                item.item_date or '',              # 39 Uploaded At
                item.production_remarks or '',     # 40 Production Remarks
                item.warehouse_remarks or '',      # 41 WH Remarks
                item.inward_number or '',          # 42 Inward No
                item.outward_number or '',         # 43 Outward No
                '', '', '', '', '',                # 44-48 batch-level, blank here
                item.from_country or '',           # 49 From Country
                item.to_country or '',             # 50 To Country
            ]
            for col_idx, val in enumerate(item_vals, 1):
                c = ws.cell(row=row_idx, column=col_idx, value=val)
                c.fill = item_sub_fill
                c.font = Font(size=9, italic=(col_idx == 1))
                c.border = thin_border()
                c.alignment = Alignment(vertical='center', wrap_text=True)
            ws.row_dimensions[row_idx].height = 16
            row_idx += 1

    # ── Summary section appended below all report rows ────────
    row_idx += 1  # blank separator
    ws.row_dimensions[row_idx - 1].height = 14
    sr = row_idx  # summary start row

    # Title
    ws.merge_cells(f'A{sr}:E{sr}')
    t = ws.cell(row=sr, column=1, value='Material Rework — Summary Report')
    t.font  = Font(bold=True, size=14, color='06152B')
    t.fill  = hdr_fill('E0F2FE')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[sr].height = 32

    gen_row = sr + 1
    c = ws.cell(row=gen_row, column=1, value=f'Generated on: {timezone.now().strftime("%d %b %Y %H:%M")}')
    c.font = Font(italic=True, size=9, color='64748B')
    ws.merge_cells(f'A{gen_row}:E{gen_row}')
    ws.row_dimensions[gen_row + 1].height = 8  # small gap

    hdr_row = gen_row + 2

    # Batch status block (cols A:B)
    c = ws.cell(row=hdr_row, column=1, value='BATCH STATUS')
    c.font = Font(bold=True, size=10, color='FFFFFF')
    c.fill = hdr_fill('06152B')
    ws.cell(row=hdr_row, column=2).fill = hdr_fill('06152B')
    ws.merge_cells(f'A{hdr_row}:B{hdr_row}')

    batch_status_map = {
        'pending_hod':     'Pending HOD Approval',
        'hod_approved':    'HOD Approved',
        'hod_rejected':    'HOD Rejected',
        'production_done': 'Production Done',
        'completed':       'Completed',
    }
    batch_counts_local = {sk: 0 for sk in batch_status_map}
    for b in batches:
        if b.status in batch_counts_local:
            batch_counts_local[b.status] += 1

    row = hdr_row + 1
    total_b = 0
    for status, label in batch_status_map.items():
        cnt = batch_counts_local[status]
        total_b += cnt
        ws.cell(row=row, column=1, value=label).font = Font(size=10)
        c = ws.cell(row=row, column=2, value=cnt)
        c.font = Font(bold=True, size=10)
        c.alignment = Alignment(horizontal='center')
        row += 1
    ws.cell(row=row, column=1, value='TOTAL BATCHES').font = Font(bold=True, size=10)
    c = ws.cell(row=row, column=2, value=total_b)
    c.font = Font(bold=True, size=10, color='0076C8')
    c.alignment = Alignment(horizontal='center')

    # Item status block (cols D:E)
    c = ws.cell(row=hdr_row, column=4, value='ITEM STATUS')
    c.font = Font(bold=True, size=10, color='FFFFFF')
    c.fill = hdr_fill('065F46')
    ws.cell(row=hdr_row, column=5).fill = hdr_fill('065F46')
    ws.merge_cells(f'D{hdr_row}:E{hdr_row}')

    item_status_map = {
        'pending_hod':        'Pending HOD',
        'pending_production': 'Pending Production',
        # The round trip, in the order a line travels it.
        'pending_rework_handover': 'Pending Release to Prod.',
        'in_rework':               'In Rework',
        'pending_return_receipt':  'Pending WH Receipt',
        'received_discrepancy':    'Received (Mismatch)',
        'pending_warehouse':  'Pending Warehouse',
        'accepted':           'Accepted (No WH)',
        'partially_accepted': 'Partially Accepted',
        'rejected':           'Rejected',
        'received':           'Received (WH)',
        'hod_rejected':       'HOD Rejected',
    }
    # Counted in Python on purpose: `batches` is prefetched with its items
    # (and both export branches walk b.items.all() anyway to total the
    # quantities), so these lines are already in memory. An aggregate query
    # here would be one MORE round trip, not one fewer.
    item_counts_local = {sk: 0 for sk in item_status_map}
    for b in batches:
        for item in b.items.all():
            if item.status in item_counts_local:
                item_counts_local[item.status] += 1

    row = hdr_row + 1
    total_i = 0
    for status, label in item_status_map.items():
        cnt = item_counts_local[status]
        total_i += cnt
        ws.cell(row=row, column=4, value=label).font = Font(size=10)
        c = ws.cell(row=row, column=5, value=cnt)
        c.font = Font(bold=True, size=10)
        c.alignment = Alignment(horizontal='center')
        row += 1
    ws.cell(row=row, column=4, value='TOTAL ITEMS').font = Font(bold=True, size=10)
    c = ws.cell(row=row, column=5, value=total_i)
    c.font = Font(bold=True, size=10, color='065F46')
    c.alignment = Alignment(horizontal='center')

    # ── Progress by user ─────────────────────────────────────
    # Its own sheet rather than more columns on an existing one: it is a
    # different shape entirely (one row per person, not per batch or per
    # line), and the same filters the screen applied are applied here so the
    # workbook and the page cannot disagree.
    def _as_date(raw):
        try:
            from datetime import datetime as _d
            return _d.strptime(raw, '%Y-%m-%d').date() if raw else None
        except ValueError:
            return None

    progress = userprogress.leaderboard(
        date_from=_as_date(f_from),
        date_to=_as_date(f_to),
        vertical_id=f_vertical or None,
        department_id=f_dept or None,
    )

    ws_users = wb.create_sheet('Progress by User')
    headers = (
        ['User', 'Email', 'Username', 'Role(s)', 'Account Active']
        + [label for _key, label in progress['columns']]
        + ['Total Actions', 'Units Released', 'Units Reworked', 'Units Received', 'Last Action']
    )
    ws_users.append(headers)
    hdr_fill = PatternFill(start_color='0F1F3A', end_color='0F1F3A', fill_type='solid')
    for cell in ws_users[1]:
        cell.fill = hdr_fill
        cell.font = Font(bold=True, color='FFFFFF', size=10)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    for entry in progress['rows']:
        ws_users.append(
            [
                entry['name'],
                entry['email'] or '',
                entry['username'] or '',
                ', '.join(entry['roles']) or 'no role',
                'Yes' if entry['is_active'] else 'No',
            ]
            + list(entry['cells'])
            + [
                entry['total'],
                entry['qty_released'],
                entry['qty_reworked'],
                entry['qty_received'],
                # Naive local time: Excel has no timezone type, and writing an
                # aware datetime makes openpyxl raise.
                timezone.localtime(entry['last_action']).replace(tzinfo=None)
                if entry['last_action'] else '',
            ]
        )

    totals_row = (
        [f"{progress['totals']['users']} user(s)", '', '', '', '']
        + list(progress['totals']['cells'])
        + [
            progress['totals']['total'],
            progress['totals']['qty_released'],
            progress['totals']['qty_reworked'],
            progress['totals']['qty_received'],
            '',
        ]
    )
    ws_users.append(totals_row)
    for cell in ws_users[ws_users.max_row]:
        cell.font = Font(bold=True, size=10)

    for index in range(1, len(headers) + 1):
        letter = get_column_letter(index)
        ws_users.column_dimensions[letter].width = 26 if index <= 4 else 15
    ws_users.freeze_panes = 'A2'

    # ── Serve ────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f'disposition_report_{timezone.now().strftime("%Y%m%d_%H%M")}.xlsx'
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _as_pk(raw):
    """A submitted id as an int, or None when it is not usable as one.

    Defined here rather than imported from `merilv.params`: it is three lines,
    and reaching across app boundaries for it would couple two unrelated
    modules together for no benefit.
    """
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _delete_master_rows(model, raw_ids):
    """Delete master rows by id, reporting what actually happened.

    Replaces nine copies of ``Model.objects.filter(pk=pk).delete()`` fed
    straight from ``request.POST``, which had three faults between them:

    * a non-numeric id reached the ORM and raised ``ValueError`` inside
      ``int()`` — a 500 error page, with the row still present. That is what
      "sometimes deletion fails" looked like to an operator;
    * a non-existent id deleted nothing, raised nothing, and was still counted
      as a success, so the screen said rows were gone when they were not;
    * the bulk loops incremented their counter before knowing whether anything
      had been removed.

    Returns ``(deleted, blocked, missing, invalid)`` — counted from the
    database's own answer, not from how many ids were submitted.

    ``RestrictedError`` is caught with ``ProtectedError``: Django raises the
    first for ``on_delete=RESTRICT`` and the second for ``PROTECT``, they do not
    share a base beyond ``Exception``, and catching only one leaves the other as
    an unhandled 500 the day someone changes a field.
    """
    deleted = blocked = missing = invalid = 0
    for raw in raw_ids:
        pk = _as_pk(raw)
        if pk is None:
            invalid += 1
            continue
        try:
            removed, _per_model = model.objects.filter(pk=pk).delete()
        except (ProtectedError, RestrictedError):
            blocked += 1
            continue
        if removed:
            deleted += 1
        else:
            missing += 1
    return deleted, blocked, missing, invalid


def _plural(noun, n):
    """`noun` pluralised for `n`, handling the -y case.

    Naive "+ s" produced "2 companys". English is not fully regular and this
    does not try to be — it covers the consonant+y rule these nouns actually
    hit (company -> companies) and adds "s" otherwise, which is correct for
    vertical, department and option.
    """
    if n == 1:
        return noun
    if len(noun) > 1 and noun.endswith('y') and noun[-2] not in 'aeiou':
        return noun[:-1] + 'ies'
    return noun + 's'


def _report_deletion(request, noun, deleted, blocked, missing, invalid):
    """Turn a deletion tally into one honest message.

    Deliberately does not collapse to "done": an operator needs to tell "it is
    still referenced" from "it was already gone" from "that request was
    malformed", and the old wording could not express any of the three.
    """
    parts = []
    if deleted:
        parts.append(f'{deleted} {_plural(noun, deleted)} deleted')
    if blocked:
        parts.append(f'{blocked} still in use')
    if missing:
        parts.append(f'{missing} already gone')
    if invalid:
        parts.append(f'{invalid} could not be read')

    summary = ', '.join(parts)
    if not parts:
        messages.error(request, f'Nothing to delete — no {noun} was selected.')
    elif blocked:
        messages.warning(
            request,
            f'{summary}. Rows still in use are referenced by existing batches — '
            f'delete or reassign those first.',
        )
    elif invalid:
        messages.warning(request, f'{summary}. Try again from the list.')
    elif missing and not deleted:
        messages.warning(request, f'{summary}. Nothing was removed.')
    else:
        messages.success(request, f'{summary}.')


@login_required
def masters_view(request):
    if not _is_md_admin(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)

    can_manage = _is_md_admin(request.user)

    if request.method == 'POST':
        if not can_manage:
            messages.error(request, 'Access denied. You have read-only access to Masters.')
            return redirect('material_rework:masters')
        action = request.POST.get('action')

        if action == 'add_company':
            cid_raw = request.POST.get('cid', '').strip()
            name    = request.POST.get('company_name', '').strip()
            acronym = request.POST.get('acronym', '').strip()
            country_raw = request.POST.get('country', '').strip()
            if name and acronym:
                country = int(country_raw) if country_raw and country_raw.upper() != 'NULL' else None
                cid     = int(cid_raw) if cid_raw else (Company.objects.order_by('-cid').values_list('cid', flat=True).first() or 0) + 1
                Company.objects.get_or_create(cid=cid, defaults={'company_name': name, 'acronym': acronym, 'country': country})
                messages.success(request, f'Company "{name}" added.')

        elif action == 'edit_company':
            cid_pk  = request.POST.get('id')
            name    = request.POST.get('company_name', '').strip()
            acronym = request.POST.get('acronym', '').strip()
            country_raw = request.POST.get('country', '').strip()
            is_active   = request.POST.get('is_active') == 'on'
            if cid_pk and name:
                country = int(country_raw) if country_raw and country_raw.upper() != 'NULL' else None
                Company.objects.filter(pk=cid_pk).update(company_name=name, acronym=acronym, country=country, is_active=is_active)
                messages.success(request, 'Company updated.')

        elif action == 'add_vertical':
            name = request.POST.get('name', '').strip()
            if name:
                Vertical.objects.get_or_create(name=name)
                messages.success(request, f'Vertical "{name}" added.')

        elif action == 'edit_vertical':
            vid       = request.POST.get('id')
            name      = request.POST.get('name', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            if vid and name:
                Vertical.objects.filter(pk=vid).update(name=name, is_active=is_active)
                messages.success(request, 'Vertical updated.')

        elif action == 'add_dept':
            dname    = request.POST.get('name', '').strip()
            vid      = request.POST.get('vertical_id')
            hod_raw  = request.POST.get('hod_email', '').strip()
            wh_raw   = request.POST.get('warehouse_email', '').strip()
            pr_raw   = request.POST.get('production_review_email', '').strip()
            # PPIC and Order Management were not on this form at all, so their
            # addresses could only be set in the Django admin — which is why
            # neither gate was notifying anyone. Both gates are routable, so
            # both are editable here.
            ppic_raw = request.POST.get('ppic_email', '').strip()
            om_raw   = request.POST.get('om_email', '').strip()
            if not dname or not vid or not hod_raw:
                messages.error(request, 'Name, Vertical and HOD Email are required.')
            elif not _parse_emails(hod_raw):
                messages.error(request, 'HOD Email must contain at least one valid email address.')
            elif _first_blocked_email(hod_raw):
                messages.error(request, f'HOD Email: {_BLOCKED_DOMAIN_MSG}')
            elif wh_raw and _first_blocked_email(wh_raw):
                messages.error(request, f'Warehouse Email: {_BLOCKED_DOMAIN_MSG}')
            elif pr_raw and _first_blocked_email(pr_raw):
                messages.error(request, f'Production Review Email: {_BLOCKED_DOMAIN_MSG}')
            elif ppic_raw and _first_blocked_email(ppic_raw):
                messages.error(request, f'PPIC Email: {_BLOCKED_DOMAIN_MSG}')
            elif om_raw and _first_blocked_email(om_raw):
                messages.error(request, f'Order Management Email: {_BLOCKED_DOMAIN_MSG}')
            else:
                Department.objects.get_or_create(
                    name=dname, vertical_id=vid,
                    defaults={
                        'hod_email': hod_raw, 'warehouse_email': wh_raw,
                        'production_review_email': pr_raw,
                        'ppic_email': ppic_raw, 'om_email': om_raw,
                    },
                )
                messages.success(request, f'Department "{dname}" added.')

        elif action == 'edit_dept':
            did      = request.POST.get('id')
            name     = request.POST.get('name', '').strip()
            hod_raw  = request.POST.get('hod_email', '').strip()
            wh_raw   = request.POST.get('warehouse_email', '').strip()
            pr_raw   = request.POST.get('production_review_email', '').strip()
            ppic_raw = request.POST.get('ppic_email', '').strip()
            om_raw   = request.POST.get('om_email', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            if not did or not name or not hod_raw:
                messages.error(request, 'Name and HOD Email are required.')
            elif not _parse_emails(hod_raw):
                messages.error(request, 'HOD Email must contain at least one valid email address.')
            elif _first_blocked_email(hod_raw):
                messages.error(request, f'HOD Email: {_BLOCKED_DOMAIN_MSG}')
            elif wh_raw and _first_blocked_email(wh_raw):
                messages.error(request, f'Warehouse Email: {_BLOCKED_DOMAIN_MSG}')
            elif pr_raw and _first_blocked_email(pr_raw):
                messages.error(request, f'Production Review Email: {_BLOCKED_DOMAIN_MSG}')
            elif ppic_raw and _first_blocked_email(ppic_raw):
                messages.error(request, f'PPIC Email: {_BLOCKED_DOMAIN_MSG}')
            elif om_raw and _first_blocked_email(om_raw):
                messages.error(request, f'Order Management Email: {_BLOCKED_DOMAIN_MSG}')
            else:
                Department.objects.filter(pk=did).update(
                    name=name, hod_email=hod_raw, warehouse_email=wh_raw,
                    production_review_email=pr_raw, ppic_email=ppic_raw,
                    om_email=om_raw, is_active=is_active,
                )
                messages.success(request, 'Department updated.')

        elif action == 'delete_company':
            _report_deletion(
                request, 'company',
                *_delete_master_rows(Company, [request.POST.get('id')]),
            )

        elif action == 'delete_vertical':
            _report_deletion(
                request, 'vertical',
                *_delete_master_rows(Vertical, [request.POST.get('id')]),
            )

        elif action == 'delete_dept':
            _report_deletion(
                request, 'department',
                *_delete_master_rows(Department, [request.POST.get('id')]),
            )

        elif action == 'bulk_delete_companies':
            _report_deletion(
                request, 'company',
                *_delete_master_rows(Company, request.POST.getlist('ids')),
            )

        elif action == 'bulk_delete_verticals':
            _report_deletion(
                request, 'vertical',
                *_delete_master_rows(Vertical, request.POST.getlist('ids')),
            )

        elif action == 'bulk_delete_departments':
            _report_deletion(
                request, 'department',
                *_delete_master_rows(Department, request.POST.getlist('ids')),
            )

        elif action in _SIMPLE_MASTER_ACTIONS:
            model, label, field = _SIMPLE_MASTER_ACTIONS[action]
            verb = action.split('_', 1)[0]
            if verb == 'add':
                name = request.POST.get(field, '').strip()
                if not name:
                    messages.error(request, f'{label} name is required.')
                elif model.objects.filter(name__iexact=name).exists():
                    messages.error(request, f'{label} "{name}" already exists.')
                else:
                    model.objects.create(name=name)
                    messages.success(request, f'{label} "{name}" added.')
            elif verb == 'edit':
                pk        = request.POST.get('id')
                name      = request.POST.get(field, '').strip()
                is_active = request.POST.get('is_active') == 'on'
                if not pk or not name:
                    messages.error(request, f'{label} name is required.')
                elif model.objects.filter(name__iexact=name).exclude(pk=pk).exists():
                    messages.error(request, f'{label} "{name}" already exists.')
                else:
                    model.objects.filter(pk=pk).update(name=name, is_active=is_active)
                    messages.success(request, f'{label} updated.')
            else:   # delete
                _report_deletion(
                    request, label.lower(),
                    *_delete_master_rows(model, [request.POST.get('id')]),
                )

        elif action == 'add_requested_by':
            name = request.POST.get('rq_name', '').strip()
            if name:
                RequestedByOption.objects.get_or_create(name=name)
                messages.success(request, f'"{name}" added to Requested By options.')
            else:
                messages.error(request, 'Name is required.')

        elif action == 'edit_requested_by':
            rq_id     = request.POST.get('id')
            name      = request.POST.get('rq_name', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            if rq_id and name:
                RequestedByOption.objects.filter(pk=rq_id).update(name=name, is_active=is_active)
                messages.success(request, 'Requested By option updated.')

        elif action == 'delete_requested_by':
            _report_deletion(
                request, 'option',
                *_delete_master_rows(RequestedByOption, [request.POST.get('id')]),
            )

        elif action == 'bulk_delete_requested_by':
            _report_deletion(
                request, 'option',
                *_delete_master_rows(RequestedByOption, request.POST.getlist('ids')),
            )

        elif action == 'import_companies':
            file = request.FILES.get('import_file')
            if not file:
                messages.error(request, 'No file selected.')
            else:
                try:
                    wb = openpyxl.load_workbook(file, data_only=True)
                    ws = wb.active
                    headers = [str(c.value or '').strip().lower().replace(' ', '').replace('_', '') for c in ws[1]]
                    col = {}
                    for i, h in enumerate(headers):
                        if h in ('cid', 'id'):
                            col['cid'] = i
                        elif 'companyname' in h or h == 'name':
                            col['company_name'] = i
                        elif 'acronym' in h or h == 'short':
                            col['acronym'] = i
                        elif 'country' in h:
                            col['country'] = i
                    created = updated = skipped = 0
                    for row in ws.iter_rows(min_row=2, values_only=True):
                        if not any(row):
                            continue
                        name    = str(row[col.get('company_name', 1)] or '').strip()
                        acronym = str(row[col.get('acronym', 2)] or '').strip()
                        if not name or not acronym:
                            skipped += 1
                            continue
                        cid_raw     = row[col.get('cid', 0)]
                        country_raw = row[col.get('country', 3)] if 'country' in col else None
                        try:
                            cid = int(cid_raw) if cid_raw not in (None, '') else None
                        except (ValueError, TypeError):
                            cid = None
                        if cid is None:
                            cid = (Company.objects.order_by('-cid').values_list('cid', flat=True).first() or 0) + 1
                        try:
                            country = int(country_raw) if country_raw not in (None, '', 'NULL') else None
                        except (ValueError, TypeError):
                            country = None
                        _, is_new = Company.objects.update_or_create(
                            cid=cid,
                            defaults={'company_name': name, 'acronym': acronym, 'country': country},
                        )
                        if is_new:
                            created += 1
                        else:
                            updated += 1
                    messages.success(request, f'Import done — {created} added, {updated} updated, {skipped} skipped.')
                except Exception as e:
                    messages.error(request, f'Import failed: {e}')

        elif action == 'import_verticals':
            file = request.FILES.get('import_file')
            if not file:
                messages.error(request, 'No file selected.')
            else:
                try:
                    wb = openpyxl.load_workbook(file, data_only=True)
                    ws = wb.active
                    headers = [str(c.value or '').strip().lower().replace(' ', '').replace('_', '') for c in ws[1]]
                    name_col = 0
                    for i, h in enumerate(headers):
                        if h in ('companyname', 'company_name', 'verticalname', 'vertical', 'name'):
                            name_col = i
                            break
                    created = skipped = 0
                    row_errors = []
                    for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                        if not any(row):
                            continue
                        name = str(row[name_col] or '').strip()
                        if not name:
                            row_errors.append(f'Row {row_num}: Vertical Name is required.')
                            continue
                        if len(name) > 200:
                            row_errors.append(f'Row {row_num}: Name too long (max 200 chars).')
                            continue
                        _, is_new = Vertical.objects.get_or_create(name=name)
                        if is_new:
                            created += 1
                        else:
                            skipped += 1
                    if row_errors:
                        for e in row_errors:
                            messages.warning(request, e)
                    messages.success(request, f'Import done — {created} added, {skipped} duplicate(s) skipped.')
                except Exception as e:
                    messages.error(request, f'Import failed: {e}')

        elif action == 'import_departments':
            file = request.FILES.get('import_file')
            if not file:
                messages.error(request, 'No file selected.')
            else:
                try:
                    wb = openpyxl.load_workbook(file, data_only=True)
                    ws = wb.active
                    headers = [str(c.value or '').strip().lower().replace(' ', '').replace('_', '') for c in ws[1]]
                    col = {}
                    for i, h in enumerate(headers):
                        if 'vertical' in h:
                            col['vertical'] = i
                        elif 'dept' in h or 'department' in h or h == 'name':
                            col['name'] = i
                        elif 'hod' in h:
                            col['hod_email'] = i
                        elif 'warehouse' in h or 'wh' in h:
                            col['warehouse_email'] = i
                    if 'vertical' not in col or 'name' not in col:
                        messages.error(request, 'Missing required columns: "Vertical" and "Department Name" must be present in the header row.')
                    else:
                        created = updated = skipped = 0
                        row_errors = []
                        for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                            if not any(row):
                                continue
                            vname = str(row[col['vertical']] or '').strip()
                            dname = str(row[col['name']] or '').strip()
                            hod   = str(row[col.get('hod_email', -1)] or '').strip() if 'hod_email' in col else ''
                            wh    = str(row[col.get('warehouse_email', -1)] or '').strip() if 'warehouse_email' in col else ''
                            errs = []
                            if not vname:
                                errs.append('Vertical is required')
                            if not dname:
                                errs.append('Department Name is required')
                            if hod and not _EMAIL_RE.match(hod):
                                errs.append(f'HOD Email "{hod}" is invalid')
                            elif hod and _is_blocked_domain(hod):
                                errs.append(f'HOD Email "{hod}": personal domains are not allowed')
                            if wh and not _EMAIL_RE.match(wh):
                                errs.append(f'Warehouse Email "{wh}" is invalid')
                            elif wh and _is_blocked_domain(wh):
                                errs.append(f'Warehouse Email "{wh}": personal domains are not allowed')
                            if errs:
                                row_errors.append(f'Row {row_num}: {"; ".join(errs)}.')
                                skipped += 1
                                continue
                            vertical, _ = Vertical.objects.get_or_create(name=vname)
                            _, is_new = Department.objects.update_or_create(
                                name=dname, vertical=vertical,
                                defaults={'hod_email': hod, 'warehouse_email': wh},
                            )
                            if is_new:
                                created += 1
                            else:
                                updated += 1
                        if row_errors:
                            for e in row_errors:
                                messages.warning(request, e)
                        messages.success(request, f'Import done — {created} added, {updated} updated, {skipped} skipped.')
                except Exception as e:
                    messages.error(request, f'Import failed: {e}')

        if _is_ajax(request):
            msgs = _ajax_messages(request)
            has_error = any('error' in (m['tags'] or '') for m in msgs)
            return JsonResponse({'ok': not has_error, 'messages': msgs},
                                status=400 if has_error else 200)
        return redirect('material_rework:masters')

    return render(request, 'material_rework/masters.html', {
        'verticals':          Vertical.objects.prefetch_related('departments').order_by('name'),
        'all_verticals':      Vertical.objects.filter(is_active=True).order_by('name'),
        'dept_count':         Department.objects.count(),
        'requested_by_opts': RequestedByOption.objects.all(),
        'countries':          Country.objects.all(),
        'rework_types':       ReworkType.objects.all(),
        'locations':          ReworkLocation.objects.all(),
        'can_manage':         can_manage,
    })


def _make_xlsx_response(filename, headers, sample_rows, col_widths=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Template'
    header_fill = PatternFill('solid', fgColor='1D4ED8')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
        if col_widths and col_idx - 1 < len(col_widths):
            ws.column_dimensions[cell.column_letter].width = col_widths[col_idx - 1]
    sample_font = Font(color='9CA3AF', italic=True, size=10)
    for r_idx, row in enumerate(sample_rows, start=2):
        for c_idx, val in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = sample_font
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@login_required
def download_template_verticals(request):
    # Both master templates belong to the Masters screen, which is
    # _is_md_admin-only, and md_users_template beside them already checks. These
    # two were @login_required alone, so every account on the platform could
    # pull them — harmless in content, inconsistent as a boundary.
    if not _is_md_admin(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)
    return _make_xlsx_response(
        filename='verticals_template.xlsx',
        headers=['Name'],
        sample_rows=[['Cardiology'], ['Orthopaedics'], ['Diagnostics']],
        col_widths=[40],
    )


@login_required
def download_template_departments(request):
    # Both master templates belong to the Masters screen, which is
    # _is_md_admin-only, and md_users_template beside them already checks. These
    # two were @login_required alone, so every account on the platform could
    # pull them — harmless in content, inconsistent as a boundary.
    if not _is_md_admin(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)
    return _make_xlsx_response(
        filename='departments_template.xlsx',
        headers=['Vertical', 'Department Name', 'HOD Email', 'Warehouse Email'],
        sample_rows=[
            ['Cardiology', 'Cath Lab', 'hod@merillife.com', 'warehouse@merillife.com'],
            ['Orthopaedics', 'Joint Replacement', 'hod2@merillife.com', ''],
        ],
        col_widths=[30, 30, 35, 35],
    )


# ── MD Role Groups ──────────────────────────────────────────────
MD_ROLE_GROUPS = [
    ('Material Rework Admin', 'Admin',      '#374151', 'cog'),
    # MR HOD stays assignable so the members it already has remain visible and
    # manageable on this screen, but no flow routes to it any more.
    ('MR HOD',                'HoD',        '#b45309', 'user-shield'),
    ('MR Order Management',   'Order Mgmt', '#7c3aed', 'file-signature'),
    ('MR PPIC',               'PPIC',       '#0369a1', 'clipboard-check'),
    ('MR Production',         'Production', '#1d4ed8', 'industry'),
    ('MR Warehouse',          'Warehouse',  '#065f46', 'warehouse'),
    ('Material Rework User',  'User',       '#6366f1', 'user'),
]
MD_ROLE_NAMES = [g[0] for g in MD_ROLE_GROUPS]

# ── MD user-management shared constants ──────────────────────────
_ERR_USER_NOT_FOUND = 'User not found.'

_MD_HEADER_MAP = {
    'email':      frozenset({'email', 'email_id', 'emailid', 'user_email', 'mail'}),
    'admin':      frozenset({'admin', 'md_admin', 'administrator'}),
    'hod':        frozenset({'hod', 'head_of_dept', 'head_of_department', 'head'}),
    'om':         frozenset({'om', 'order_management', 'ordermanagement', 'mr_om', 'order_mgmt'}),
    'ppic':       frozenset({'ppic', 'mr_ppic', 'planning', 'ppc'}),
    'production': frozenset({'production', 'prod'}),
    'warehouse':  frozenset({'warehouse', 'wh', 'ware_house'}),
    'user':       frozenset({'user', 'basic', 'basic_user', 'material_rework_user'}),
}
_MD_IMPORT_TRUTHY = frozenset({'true', 'yes', 'y', '1', 'x'})


# ── Per-action helpers for md_users_view ─────────────────────────

def _md_build_roles_list(group_names):
    names = set(group_names)
    return [
        {'name': gname, 'label': label, 'color': color, 'icon': icon}
        for gname, label, color, icon in MD_ROLE_GROUPS if gname in names
    ]


def _md_handle_update_roles(request, md_groups):
    from django.contrib.auth.models import User
    user_id = request.POST.get('user_id', '').strip()
    selected_names = request.POST.getlist('groups')
    try:
        target = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': _ERR_USER_NOT_FOUND}, status=404)
        messages.error(request, _ERR_USER_NOT_FOUND)
        return None
    with transaction.atomic():
        target.groups.remove(*list(md_groups.values()))
        for name in selected_names:
            if name in md_groups:
                target.groups.add(md_groups[name])
    signed_out = invalidate_user_sessions(target)
    messages.success(request, f'Roles updated for {target.get_full_name() or target.username}.')
    if _is_ajax(request):
        new_groups = [g.name for g in target.groups.filter(name__in=MD_ROLE_NAMES)]
        return JsonResponse({'ok': True, 'user_id': target.pk,
                             'roles': _md_build_roles_list(new_groups), 'user_groups': new_groups,
                             'sessions_invalidated': signed_out})
    return None


def _md_handle_create_user(request, md_groups):
    from django.contrib.auth.models import User
    email = request.POST.get('email', '').strip().lower()
    grp_names = request.POST.getlist('groups')

    if not email:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'Email is required.'}, status=400)
        messages.error(request, 'Email is required.')
        return None
    if not _EMAIL_RE.match(email):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'Enter a valid email address.'}, status=400)
        messages.error(request, 'Enter a valid email address.')
        return None
    if _is_blocked_domain(email):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': _BLOCKED_DOMAIN_MSG}, status=400)
        messages.error(request, _BLOCKED_DOMAIN_MSG)
        return None

    existing = User.objects.filter(email__iexact=email).first()
    if existing:
        user, user_created = existing, False
    else:
        username = email.split('@')[0]
        base_username, counter = username, 1
        while User.objects.filter(username=username).exists():
            username = f'{base_username}{counter}'
            counter += 1
        user = User.objects.create_user(username=username, email=email)
        user_created = True
    with transaction.atomic():
        user.groups.remove(*list(md_groups.values()))
        for name in grp_names:
            if name in md_groups:
                user.groups.add(md_groups[name])
    signed_out = invalidate_user_sessions(user) if not user_created else 0
    verb = 'added' if user_created else 'updated'
    message = f'User "{user.get_full_name() or user.username}" {verb} successfully.'
    if signed_out:
        message += ' Existing sessions were signed out so the new access applies immediately.'
    messages.success(request, message)
    if _is_ajax(request):
        initials = ((user.first_name[:1] + user.last_name[:1]) or user.username[:2]).upper()
        u_groups = [g.name for g in user.groups.filter(name__in=MD_ROLE_NAMES)]
        return JsonResponse({'ok': True, 'user': {
            'id': user.pk, 'username': user.username,
            'full_name': user.get_full_name() or user.username,
            'email': user.email, 'initials': initials,
            'is_active': user.is_active, 'is_superuser': user.is_superuser,
            'roles': _md_build_roles_list(u_groups), 'user_groups': u_groups,
        }, 'sessions_invalidated': signed_out})
    return None


def _md_handle_toggle_active(request):
    from django.contrib.auth.models import User
    user_id = request.POST.get('user_id', '').strip()
    try:
        target = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': _ERR_USER_NOT_FOUND}, status=404)
        messages.error(request, _ERR_USER_NOT_FOUND)
        return None
    if target == request.user:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'You cannot deactivate your own account.'}, status=400)
        messages.error(request, 'You cannot deactivate your own account.')
        return None
    target.is_active = not target.is_active
    target.save(update_fields=['is_active'])
    signed_out = invalidate_user_sessions(target) if not target.is_active else 0
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'user_id': target.pk, 'is_active': target.is_active,
                             'sessions_invalidated': signed_out})
    verb = 'activated' if target.is_active else 'deactivated'
    message = f'User "{target.get_full_name() or target.username}" {verb}.'
    if signed_out:
        message += ' Their active sessions were signed out.'
    messages.success(request, message)
    return None


def _md_handle_delete_user(request):
    from django.contrib.auth.models import User
    user_id = request.POST.get('user_id', '').strip()
    try:
        target = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': _ERR_USER_NOT_FOUND}, status=404)
        messages.error(request, _ERR_USER_NOT_FOUND)
        return None
    if target == request.user:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'You cannot remove your own account.'}, status=400)
        messages.error(request, 'You cannot remove your own account.')
        return None
    if target.is_superuser:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'Superuser accounts cannot be removed here.'}, status=400)
        messages.error(request, 'Superuser accounts cannot be removed here.')
        return None
    uid = target.pk
    target.delete()
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'user_id': uid})
    messages.success(request, 'User removed.')
    return None


def _process_import_rows(request, ws, cols, role_col_map, md_groups):
    from django.contrib.auth.models import User
    created_count = updated_count = skipped_count = error_count = 0
    error_rows = []
    for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        email = str(row[cols['email']] or '').strip().lower()
        if not email or '@' not in email:
            skipped_count += 1
            continue
        if _is_blocked_domain(email):
            error_count += 1
            error_rows.append(f'Row {row_num} ({email}): personal email domains are not allowed.')
            continue
        try:
            with transaction.atomic():
                try:
                    u = User.objects.get(email__iexact=email)
                    was_created = False
                except User.DoesNotExist:
                    username = email.split('@')[0]
                    base, counter = username, 1
                    while User.objects.filter(username=username).exists():
                        username = f'{base}{counter}'
                        counter += 1
                    u = User.objects.create_user(username=username, email=email, password=None)
                    was_created = True
                for group_name, col_idx in role_col_map:
                    if col_idx is None or group_name not in md_groups:
                        continue
                    val = str(row[col_idx] or '').strip().lower()
                    if val in _MD_IMPORT_TRUTHY:
                        u.groups.add(md_groups[group_name])
                    else:
                        u.groups.remove(md_groups[group_name])
            if was_created:
                created_count += 1
            else:
                updated_count += 1
        except Exception as exc:  # noqa: BLE001 — per-row errors must not abort the whole import
            error_count += 1
            error_rows.append(f'Row {row_num} ({email}): {exc}')
    parts = []
    if created_count: parts.append(f'{created_count} user(s) created')
    if updated_count: parts.append(f'{updated_count} role(s) updated')
    if skipped_count: parts.append(f'{skipped_count} row(s) skipped (blank/invalid email)')
    if error_count:   parts.append(f'{error_count} error(s)')
    messages.success(request, 'Import complete: ' + (', '.join(parts) or 'nothing to process') + '.')
    for msg in error_rows[:5]:
        messages.warning(request, msg)


def _md_handle_import_users(request, md_groups):
    file = request.FILES.get('import_file')
    if not file:
        messages.error(request, 'No file selected.')
        return None
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        raw_headers = [str(c.value or '').strip().lower().replace(' ', '_') for c in ws[1]]
        cols = {}
        for key, aliases in _MD_HEADER_MAP.items():
            for i, h in enumerate(raw_headers):
                if h in aliases:
                    cols[key] = i
                    break
        if 'email' not in cols:
            messages.error(request, 'Column "Email" not found. Please use the provided template.')
            return None
        role_col_map = [
            ('Material Rework Admin', cols.get('admin')),
            ('MR HOD',                cols.get('hod')),
            ('MR Order Management',   cols.get('om')),
            ('MR PPIC',               cols.get('ppic')),
            ('MR Production',         cols.get('production')),
            ('MR Warehouse',          cols.get('warehouse')),
            ('Material Rework User',  cols.get('user')),
        ]
        _process_import_rows(request, ws, cols, role_col_map, md_groups)
    except Exception as exc:  # noqa: BLE001 — workbook read failure surfaced to user
        messages.error(request, f'Import failed: {exc}')
    return None


def _md_handle_transfer_root_user(request):
    from django.contrib.auth.models import User, Group
    new_user_id = request.POST.get('user_id', '').strip()
    try:
        new_holder = User.objects.get(pk=new_user_id)
    except User.DoesNotExist:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': _ERR_USER_NOT_FOUND}, status=404)
        messages.error(request, _ERR_USER_NOT_FOUND)
        return None
    if new_holder == request.user and not request.user.is_superuser:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'You cannot assign Root User to yourself.'}, status=400)
        messages.error(request, 'You cannot assign Root User to yourself.')
        return None
    root_group, _ = Group.objects.get_or_create(name=ROOT_USER_GROUP)
    with transaction.atomic():
        root_group.user_set.clear()
        new_holder.groups.add(root_group)
    label = new_holder.get_full_name() or new_holder.username
    if _is_ajax(request):
        return JsonResponse({
            'ok': True,
            'message': f'Root User transferred to {label}.',
            'new_root_user_id': new_holder.pk,
            'new_root_user_name': label,
        })
    messages.success(request, f'Root User transferred to {label}.')
    return None


@login_required
def md_users_view(request):
    from django.contrib.auth.models import User, Group

    if not _is_md_admin(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)

    can_assign_roles = _is_md_admin(request.user)
    can_manage = _is_md_admin(request.user)

    for gname, *_ in MD_ROLE_GROUPS:
        Group.objects.get_or_create(name=gname)
    md_groups = {g.name: g for g in Group.objects.filter(name__in=MD_ROLE_NAMES)}

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        _action_handlers = {
            'update_roles':       lambda: _md_handle_update_roles(request, md_groups),
            'create_user':        lambda: _md_handle_create_user(request, md_groups),
            'toggle_active':      lambda: _md_handle_toggle_active(request),
            'delete_user':        lambda: _md_handle_delete_user(request),
            'import_users':       lambda: _md_handle_import_users(request, md_groups),
            'transfer_root_user': lambda: _md_handle_transfer_root_user(request),
        }
        if action == 'update_roles' and can_assign_roles:
            resp = _action_handlers[action]()
            if resp is not None:
                return resp
        elif action in _action_handlers and can_manage:
            resp = _action_handlers[action]()
            if resp is not None:
                return resp

        if _is_ajax(request):
            msgs = _ajax_messages(request)
            has_error = any('error' in (m['tags'] or '') for m in msgs)
            return JsonResponse({'ok': not has_error, 'messages': msgs},
                                status=400 if has_error else 200)
        return redirect('material_rework:md_users')

    users = (
        User.objects
        .all()
        .prefetch_related('groups')
        .order_by('first_name', 'last_name', 'username')
    )
    user_data = []
    for u in users:
        user_groups = {g.name for g in u.groups.all()}
        u_groups_list = list(user_groups & set(MD_ROLE_NAMES))
        user_data.append({
            'user':             u,
            'roles':            _md_build_roles_list(u_groups_list),
            'user_groups':      u_groups_list,
            'user_groups_json': json.dumps(u_groups_list),
            'initials':         ((u.first_name[:1] + u.last_name[:1]) or u.username[:2]).upper(),
            'is_root_user':     ROOT_USER_GROUP in user_groups,
        })

    current_root_user = _get_root_user()
    return render(request, 'material_rework/md_users.html', {
        'user_data':         user_data,
        'role_groups':       MD_ROLE_GROUPS,
        'md_role_names':     MD_ROLE_NAMES,
        'can_assign_roles':  can_assign_roles,
        'can_manage':        can_manage,
        'total_users':       len(user_data),
        'current_root_user': current_root_user,
        'root_user_group':   ROOT_USER_GROUP,
    })


@login_required
def download_md_users_template(request):
    if not _is_md_admin(request.user):
        return render(request, 'material_rework/access_denied.html', status=403)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'MD Users'

    # Headers — order matches MD_ROLE_GROUPS: Admin, HoD, Order Management,
    # PPIC, Production, Warehouse, User. Order Management is the gate the
    # Warehouse flow reviews at before PPIC.
    headers = ['Email', 'Admin', 'HoD', 'Order Management', 'PPIC', 'Production', 'Warehouse', 'User']
    ws.append(headers)

    # Style header row
    hdr_fill = PatternFill(start_color='0076C8', end_color='0076C8', fill_type='solid')
    hdr_font = Font(bold=True, color='FFFFFF')
    hdr_align = Alignment(horizontal='center')
    for cell in ws[1]:
        cell.fill   = hdr_fill
        cell.font   = hdr_font
        cell.alignment = hdr_align

    # Sample rows                        Email                         Admin  HoD    OM     PPIC   Prod   WH     User
    ws.append(['user@merillife.com',      'N', 'N', 'N', 'N', 'N', 'N', 'Y'])
    ws.append(['om@merillife.com',        'N', 'N', 'Y', 'N', 'N', 'N', 'N'])
    ws.append(['ppic@merillife.com',      'N', 'N', 'N', 'Y', 'N', 'N', 'N'])
    ws.append(['production@merillife.com', 'N', 'N', 'N', 'N', 'Y', 'N', 'N'])
    ws.append(['warehouse@merillife.com', 'N', 'N', 'N', 'N', 'N', 'Y', 'N'])
    ws.append(['admin@merillife.com',     'Y', 'N', 'N', 'N', 'N', 'N', 'N'])
    ws.append(['multi@merillife.com',     'N', 'N', 'Y', 'Y', 'N', 'N', 'N'])

    # Notes section
    ws.append([])
    note_row = ws.max_row + 1
    ws.cell(row=note_row,   column=1, value='Notes:')
    ws.cell(row=note_row,   column=2, value='Use Y/N, Yes/No, TRUE/FALSE, or 1/0 for role columns')
    ws.cell(row=note_row+1, column=2, value='A user can hold multiple roles — set Y in each applicable column')
    ws.cell(row=note_row+2, column=2, value='If the email does not exist, a new account is created (password reset required)')
    for r in range(note_row, note_row + 3):
        ws.cell(row=r, column=1).font = Font(bold=True, color='374151')
        ws.cell(row=r, column=2).font = Font(color='6B7280', italic=True)

    # Column widths
    ws.column_dimensions['A'].width = 34
    for col in 'BCDEFG':
        ws.column_dimensions[col].width = 13

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="md_users_template.xlsx"'
    wb.save(response)
    return response
