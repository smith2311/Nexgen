"""
Warehouse Inspection open-issue ("ticket") tracking.
────────────────────────────────────────────────────────────────
Tickets have no dedicated admin screen by design - they exist purely to
drive the daily reminder emails and the "is this warehouse open" status in
the daily activity report (see services.build_daily_warehouse_inspection_report).

sync_tickets_for_responses() is called once per submit, right after the new
WarehouseInspectionResponse rows are saved (see
serializers.WarehouseInspectionSubmissionCreateSerializer.create).

send_ticket_reminder_emails() is called once a day from the
send_warehouse_inspection_reminders management command.
"""
from collections import OrderedDict

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import WarehouseInspectionResponse, WarehouseInspectionTicket
from .services import (
    WAREHOUSE_INSPECTION_CONTACT_EMAIL,
    WAREHOUSE_INSPECTION_LOGO_CID,
    _attach_branding_logo,
    _clean_unique_emails,
    _debug_floor_email_map,
    _debug_internal_emails,
    _normalize_floor_name,
    _testing_cc_emails,
    _warehouse_inspection_from_email,
    _warehouse_inspection_logo_path,
    should_send_responsible_inspection_emails,
)


def sync_tickets_for_responses(responses):
    """
    Open, refresh, or close a WarehouseInspectionTicket for each checkpoint/floor
    represented in `responses` (freshly-created WarehouseInspectionResponse rows
    from one submit).

    A ticket opens the first time a checkpoint is answered 'No' for a floor,
    reopens if it had been closed and regresses back to 'No', and closes the
    next time that checkpoint/floor is answered 'Yes'. No ticket row is ever
    created for a checkpoint that has never failed - "no row" means "never an
    issue", not "resolved".
    """
    now = timezone.now()
    tagged = []

    for response in responses:
        assignment = response.floor_assignment
        building_id = assignment.floor.building_id if assignment else response.floor.building_id

        if response.response == 'no':
            ticket, created = WarehouseInspectionTicket.objects.get_or_create(
                checkpoint=response.checkpoint,
                floor=response.floor,
                defaults={
                    'building_id': building_id,
                    'condition_category_id': response.checkpoint.condition_category_id,
                    'floor_assignment': assignment,
                    'status': 'open',
                    'opened_at': now,
                    'opened_response': response,
                    'last_response': response,
                    'responsible_email': response.responsible_email,
                    'internal_department_email': response.internal_department_email,
                },
            )
            if not created:
                ticket.last_response = response
                ticket.floor_assignment = assignment
                ticket.responsible_email = response.responsible_email
                ticket.internal_department_email = response.internal_department_email
                if ticket.status != 'open':
                    ticket.status = 'open'
                    ticket.opened_at = now
                    ticket.opened_response = response
                    ticket.closed_at = None
                    ticket.closed_response = None
                ticket.save()
            response.ticket = ticket
            tagged.append(response)
        else:
            ticket = WarehouseInspectionTicket.objects.filter(
                checkpoint=response.checkpoint, floor=response.floor,
            ).first()
            if ticket:
                ticket.last_response = response
                if ticket.status == 'open':
                    ticket.status = 'closed'
                    ticket.closed_at = now
                    ticket.closed_response = response
                ticket.save()
                response.ticket = ticket
                tagged.append(response)

    if tagged:
        WarehouseInspectionResponse.objects.bulk_update(tagged, ['ticket'])


def _ticket_recipient_emails(ticket):
    if getattr(settings, 'DEBUG', False):
        floor_key = _normalize_floor_name(ticket.floor.floor_name)
        return _debug_floor_email_map().get(floor_key, [])
    return _clean_unique_emails([ticket.responsible_email])


def _ticket_cc_emails(ticket):
    if getattr(settings, 'DEBUG', False):
        return _debug_internal_emails()
    return _clean_unique_emails([ticket.internal_department_email])


def _group_tickets_by_recipient(tickets):
    grouped = OrderedDict()
    missing = []
    for ticket in tickets:
        to_emails = _ticket_recipient_emails(ticket)
        if not to_emails:
            missing.append(ticket)
            continue
        for to_email in to_emails:
            key = to_email.lower()
            if key not in grouped:
                grouped[key] = {'to_email': to_email, 'tickets': []}
            grouped[key]['tickets'].append(ticket)
    return grouped, missing


def _send_reminder_email(to_email, tickets):
    cc_emails = _clean_unique_emails(
        [email for ticket in tickets for email in _ticket_cc_emails(ticket)] + _testing_cc_emails()
    )
    to_email_keys = {to_email.lower()}
    cc_emails = _clean_unique_emails(email for email in cc_emails if email.lower() not in to_email_keys)

    today = timezone.localdate()
    first_ticket = tickets[0]
    subject = (
        f'Warehouse Inspection - Still Open - '
        f'{first_ticket.building.building_name} - {len(tickets)} item{"s" if len(tickets) != 1 else ""}'
    )
    logo_path = _warehouse_inspection_logo_path()
    rows = [
        {
            'ticket': ticket,
            # Local date, not the raw UTC .date(), so "days open" does not
            # jump a day for anything recorded late in the evening IST.
            'opened_on': timezone.localtime(ticket.opened_at).date() if ticket.opened_at else None,
            'as_of': today,
            'days_open': (today - timezone.localtime(ticket.opened_at).date()).days if ticket.opened_at else 0,
        }
        for ticket in tickets
    ]
    context = {
        'recipient_email': to_email,
        'rows': rows,
        'today': today,
        'contact_email': WAREHOUSE_INSPECTION_CONTACT_EMAIL,
        'brand_logo_cid': WAREHOUSE_INSPECTION_LOGO_CID if logo_path else '',
    }
    text_body = render_to_string('warehouse_inspection/email/reminder.txt', context)
    html_body = render_to_string('warehouse_inspection/email/reminder.html', context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=_warehouse_inspection_from_email(),
        to=[to_email],
        cc=cc_emails,
    )
    message.attach_alternative(html_body, 'text/html')
    _attach_branding_logo(message, logo_path)
    message.send(fail_silently=False)


def send_ticket_reminder_emails():
    """
    Resend a "still open" reminder for every ticket that:
      - is currently open,
      - was NOT opened today (today's immediate submission email already
        covered it), and
      - has not already been reminded today (cron-rerun idempotency).
    """
    if not should_send_responsible_inspection_emails():
        return {'recipients': [], 'cc': [], 'sent_count': 0, 'failed_count': 0, 'errors': []}

    today = timezone.localdate()
    tickets = list(
        WarehouseInspectionTicket.objects
        .filter(status='open')
        .exclude(opened_at__date=today)
        .exclude(last_reminder_sent_at__date=today)
        .select_related('checkpoint', 'floor', 'building', 'condition_category', 'last_response')
        .order_by('floor__floor_order', 'checkpoint__checkpoint_order')
    )
    if not tickets:
        return {'recipients': [], 'cc': [], 'sent_count': 0, 'failed_count': 0, 'errors': []}

    grouped, missing = _group_tickets_by_recipient(tickets)
    sent_at = timezone.now()
    sent_recipients = []
    errors = [
        f'Missing responsible email for {ticket.floor.floor_name} - {ticket.checkpoint.checkpoint_name}.'
        for ticket in missing
    ]
    failed_count = 0

    for group in grouped.values():
        to_email = group['to_email']
        group_tickets = group['tickets']
        try:
            _send_reminder_email(to_email, group_tickets)
        except Exception as exc:  # noqa: BLE001 - surfaced to the cron command's output
            errors.append(f'{to_email}: {exc}')
            failed_count += 1
            continue
        sent_recipients.append(to_email)
        WarehouseInspectionTicket.objects.filter(
            pk__in=[ticket.pk for ticket in group_tickets]
        ).update(last_reminder_sent_at=sent_at)

    return {
        'recipients': _clean_unique_emails(sent_recipients),
        'cc': [],
        'sent_count': len(sent_recipients),
        'failed_count': failed_count,
        'errors': errors,
    }
