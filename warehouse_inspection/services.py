from collections import OrderedDict, defaultdict
from datetime import timedelta
from email.mime.image import MIMEImage

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Max, Q
from django.template.loader import render_to_string
from django.utils import timezone

from .models import (
    WarehouseInspectionBuilding,
    WarehouseInspectionLoginLog,
    WarehouseInspectionResponse,
    WarehouseInspectionSecondResponse,
    WarehouseInspectionSecondSubmission,
    WarehouseInspectionSubmissionEvent,
    WarehouseInspectionTicket,
)


WAREHOUSE_INSPECTION_CONTACT_EMAIL = 'scm.digitalsupport@merillife.com'
WAREHOUSE_INSPECTION_LOGO_STATIC_PATH = 'img/Meril-logo-bluebg.jpg'
WAREHOUSE_INSPECTION_LOGO_CID = 'meril-logo-bluebg'
WAREHOUSE_INSPECTION_ADMIN_GROUP = 'Warehouse Inspection Admin'
WAREHOUSE_INSPECTION_ALLOWED_EMAIL_DOMAIN = 'merillife.com'
ROOT_USER_GROUP = 'Root User'
WAREHOUSE_INSPECTION_LEGACY_ADMIN_GROUPS = (
    'admin',
    'manager',
    'warehouse manager',
)


def _clean_unique_emails(values):
    seen = set()
    emails = []
    for value in values:
        if not value:
            continue
        email = str(value).strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        emails.append(email)
    return emails


def _report_recipient_emails():
    """Active emails from the Report Recipients master (used for every
    Warehouse Inspection report email - daily activity report + summary)."""
    from .models import WarehouseInspectionReportRecipient

    recipient_emails = _clean_unique_emails(
        WarehouseInspectionReportRecipient.objects
        .filter(is_active=True)
        .order_by('email')
        .values_list('email', flat=True)
    )
    if not recipient_emails:
        return []

    email_query = Q()
    for email in recipient_emails:
        email_query |= Q(email__iexact=email)

    User = get_user_model()
    users = User.objects.prefetch_related('groups').filter(is_active=True).filter(email_query)
    allowed = []
    for user in users:
        if _is_report_recipient_admin(user):
            allowed.append(user.email)
    return _clean_unique_emails(allowed)


def _is_report_recipient_admin(user):
    if user.is_superuser or user.groups.filter(name=ROOT_USER_GROUP).exists():
        return True
    if not str(user.email or '').strip().lower().endswith(f'@{WAREHOUSE_INSPECTION_ALLOWED_EMAIL_DOMAIN}'):
        return False
    if user.is_staff:
        return True

    group_query = Q(name__iexact=WAREHOUSE_INSPECTION_ADMIN_GROUP)
    for group_name in WAREHOUSE_INSPECTION_LEGACY_ADMIN_GROUPS:
        group_query |= Q(name__iexact=group_name)
    return user.groups.filter(group_query).exists()


def _normalize_floor_name(value):
    return ' '.join(str(value or '').strip().lower().split())


def _parse_floor_email_map(value):
    if isinstance(value, dict):
        return {
            _normalize_floor_name(key): _clean_unique_emails(
                emails if isinstance(emails, (list, tuple, set)) else str(emails).split(',')
            )
            for key, emails in value.items()
        }

    floor_map = {}
    for item in str(value or '').split(';'):
        if '=' not in item:
            continue
        floor_name, emails = item.split('=', 1)
        floor_map[_normalize_floor_name(floor_name)] = _clean_unique_emails(
            emails.split(',')
        )
    return {key: emails for key, emails in floor_map.items() if key and emails}


def _debug_floor_email_map():
    return _parse_floor_email_map(
        getattr(settings, 'WAREHOUSE_INSPECTION_DEBUG_FLOOR_EMAILS', '')
    )


def _debug_internal_emails():
    emails = getattr(settings, 'WAREHOUSE_INSPECTION_DEBUG_INTERNAL_EMAILS', [])
    if isinstance(emails, str):
        emails = emails.replace(';', ',').split(',')
    return _clean_unique_emails(emails)


def _testing_cc_emails():
    """
    Always-on CC for whoever is actively testing the module, independent of
    DEBUG (the debug-mode overrides above replace the real recipients
    entirely, which isn't what "just cc me so I can watch" needs - this adds
    on top of whatever the real/debug recipients already are, in both modes).
    """
    emails = getattr(settings, 'WAREHOUSE_INSPECTION_TESTING_CC', [])
    if isinstance(emails, str):
        emails = emails.replace(';', ',').split(',')
    return _clean_unique_emails(emails)


def should_send_responsible_inspection_emails():
    if getattr(settings, 'DEBUG', False) and _debug_floor_email_map():
        return True
    return bool(getattr(settings, 'WAREHOUSE_INSPECTION_SEND_RESPONSIBLE_EMAILS', True))


def _warehouse_inspection_from_email():
    return settings.DEFAULT_FROM_EMAIL


def _warehouse_inspection_logo_path():
    path = finders.find(WAREHOUSE_INSPECTION_LOGO_STATIC_PATH)
    if path:
        return path
    fallback = settings.BASE_DIR / 'static' / 'img' / 'Meril-logo-bluebg.jpg'
    return str(fallback) if fallback.exists() else ''


def _attach_branding_logo(message, logo_path):
    if not logo_path:
        return
    with open(logo_path, 'rb') as logo_file:
        logo = MIMEImage(logo_file.read(), _subtype='jpeg')
    logo.add_header('Content-ID', f'<{WAREHOUSE_INSPECTION_LOGO_CID}>')
    logo.add_header('Content-Disposition', 'inline', filename='Meril-logo-bluebg.jpg')
    message.attach(logo)


def _subject_floor_part(floor_names):
    if not floor_names:
        return 'No floor'
    if len(floor_names) <= 3:
        return ', '.join(floor_names)
    return f'{len(floor_names)} floors'


def _submission_key(submission):
    return (submission._meta.label_lower, submission.pk)


def _response_submission_key(response):
    return (response.submission._meta.label_lower, response.submission_id)


def _is_second_submission(submission):
    return isinstance(submission, WarehouseInspectionSecondSubmission)


def _inspection_attempt_label(submission):
    return 'Second' if _is_second_submission(submission) else 'First'


def _latest_event_ids(submission_ids):
    """
    The latest WarehouseInspectionSubmissionEvent id per submission. Since a
    WarehouseInspectionSubmission row is now reused across unlimited same-day
    resubmits, its responses accumulate one batch per resubmit - this picks
    out only the most recent batch's event ids.
    """
    return list(
        WarehouseInspectionSubmissionEvent.objects
        .filter(submission_id__in=submission_ids)
        .values('submission_id')
        .annotate(latest_event_id=Max('event_id'))
        .values_list('latest_event_id', flat=True)
    )


def _ordered_response_query(response_model, submission_ids):
    if not submission_ids:
        return []
    queryset = response_model.objects.filter(submission_id__in=submission_ids)
    if response_model is WarehouseInspectionResponse:
        # Only show the latest resubmission's answers - not every past
        # attempt's rows for the same submission. event_id__isnull=True is a
        # fallback for rows saved before the event concept existed.
        latest_event_ids = _latest_event_ids(submission_ids)
        queryset = queryset.filter(Q(event_id__in=latest_event_ids) | Q(event_id__isnull=True))
    return list(
        queryset
        .select_related(
            'submission',
            'submission__building',
            'submission__condition_category',
            'submission__floor',
            'checkpoint',
            'floor',
        )
        .order_by(
            'submission__floor__floor_order',
            'submission__floor__floor_name',
            'checkpoint__checkpoint_order',
            'checkpoint__checkpoint_name',
        )
    )


def _ordered_submission_responses(submissions):
    submissions = list(submissions)
    primary_ids = [
        submission.pk for submission in submissions
        if not _is_second_submission(submission)
    ]
    second_ids = [
        submission.pk for submission in submissions
        if _is_second_submission(submission)
    ]
    responses = []
    responses.extend(_ordered_response_query(WarehouseInspectionResponse, primary_ids))
    responses.extend(_ordered_response_query(WarehouseInspectionSecondResponse, second_ids))
    return sorted(
        responses,
        key=lambda response: (
            response.submission.floor.floor_order if response.submission.floor else 0,
            response.submission.floor.floor_name if response.submission.floor else '',
            response.checkpoint.checkpoint_order,
            response.checkpoint.checkpoint_name,
            response.responded_at,
        ),
    )


def _group_responses_by_recipient(responses):
    grouped = OrderedDict()
    missing_responses = []
    for response in responses:
        to_emails = _response_recipient_emails(response)
        if not to_emails:
            missing_responses.append(response)
            continue

        for to_email in to_emails:
            key = to_email.lower()
            if key not in grouped:
                grouped[key] = {
                    'to_email': to_email,
                    'responses': [],
                }
            grouped[key]['responses'].append(response)
    return grouped, missing_responses


def _floor_groups_for_responses(responses):
    grouped = OrderedDict()
    for response in responses:
        submission = response.submission
        key = _submission_key(submission)
        if key not in grouped:
            grouped[key] = {
                'submission': submission,
                'floor': submission.floor or response.floor,
                'inspection_attempt': _inspection_attempt_label(submission),
                'responses': [],
            }
        grouped[key]['responses'].append(response)
    return list(grouped.values())


def _submission_status_recipients(responses):
    to_by_submission = defaultdict(list)
    cc_by_submission = defaultdict(list)
    for response in responses:
        to_by_submission[_response_submission_key(response)].extend(
            _response_recipient_emails(response)
        )
        cc_by_submission[_response_submission_key(response)].extend(
            _response_cc_emails(response)
        )
    return to_by_submission, cc_by_submission


def _missing_response_errors(missing_responses):
    errors_by_submission = defaultdict(list)
    for response in missing_responses:
        floor_name = response.floor.floor_name if response.floor else response.warehouse_floor
        checkpoint_name = response.checkpoint.checkpoint_name
        errors_by_submission[_response_submission_key(response)].append(
            f'Missing responsible email for {floor_name} - {checkpoint_name}.'
        )
    return errors_by_submission


def _response_floor_name(response):
    floor = response.floor or response.submission.floor
    return floor.floor_name if floor else response.warehouse_floor


def _response_recipient_emails(response):
    if getattr(settings, 'DEBUG', False):
        floor_key = _normalize_floor_name(_response_floor_name(response))
        return _debug_floor_email_map().get(floor_key, [])
    return _clean_unique_emails([response.responsible_email])


def _response_cc_emails(response):
    if getattr(settings, 'DEBUG', False):
        return _debug_internal_emails()
    return _clean_unique_emails([response.internal_department_email])


def _send_floor_group_email(to_emails, responses, subject_prefix, cc_emails=None):
    to_emails = _clean_unique_emails(to_emails)
    if not to_emails:
        return []

    floor_groups = _floor_groups_for_responses(responses)
    submissions = [floor_group['submission'] for floor_group in floor_groups]
    first_submission = submissions[0]
    floor_names = [floor_group['floor'].floor_name for floor_group in floor_groups]
    cc_emails = _clean_unique_emails(list(cc_emails or []) + _testing_cc_emails())
    to_email_keys = {email.lower() for email in to_emails}
    cc_emails = _clean_unique_emails(
        email for email in cc_emails if email.lower() not in to_email_keys
    )

    subject = (
        f'{subject_prefix} - '
        f'{first_submission.building.building_name} - '
        f'{first_submission.condition_category.condition_category_name} - '
        f'{_subject_floor_part(floor_names)}'
    )
    logo_path = _warehouse_inspection_logo_path()
    context = {
        'recipient_email': ', '.join(to_emails),
        'submission': first_submission,
        'submissions': submissions,
        'floor_groups': floor_groups,
        'floor_names': floor_names,
        'submitted_at': first_submission.submitted_at,
        'contact_email': WAREHOUSE_INSPECTION_CONTACT_EMAIL,
        'brand_logo_cid': WAREHOUSE_INSPECTION_LOGO_CID if logo_path else '',
        'yes_icon': 'Yes',
        'no_icon': 'No',
    }
    text_body = render_to_string('warehouse_inspection/email/submission.txt', context)
    html_body = render_to_string('warehouse_inspection/email/submission.html', context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=_warehouse_inspection_from_email(),
        to=to_emails,
        cc=cc_emails,
    )
    message.attach_alternative(html_body, 'text/html')
    _attach_branding_logo(message, logo_path)
    message.send(fail_silently=False)
    return cc_emails


def _send_recipient_group(to_email, responses):
    cc_emails = _clean_unique_emails(
        email
        for response in responses
        for email in _response_cc_emails(response)
    )
    return _send_floor_group_email(
        [to_email],
        responses,
        'Warehouse Inspection Report',
        cc_emails=cc_emails,
    )


def _append_submission_email_status(submissions, recipients, errors=None, sent_at=None):
    recipients = _clean_unique_emails(recipients)
    errors = [str(error).strip() for error in (errors or []) if str(error).strip()]
    for submission in submissions:
        existing_error = str(submission.email_error or '').strip()
        submission.email_to = _clean_unique_emails(
            list(submission.email_to or []) + recipients
        )
        if errors:
            submission.email_sent = False
            submission.email_error = ' '.join(
                item for item in [existing_error, *errors] if item
            )
        else:
            submission.email_error = existing_error or None
            submission.email_sent = bool(submission.email_to) and not submission.email_error
            if submission.email_sent:
                submission.email_sent_at = submission.email_sent_at or sent_at or timezone.now()
        submission.save(
            update_fields=[
                'email_to',
                'email_sent',
                'email_sent_at',
                'email_error',
            ]
        )


def send_inspection_submission_emails(submissions):
    """Send grouped inspection result emails and persist email status."""
    submissions = list(submissions)
    if not submissions:
        return {
            'recipients': [],
            'cc': [],
            'sent_count': 0,
            'failed_count': 0,
            'errors': [],
        }

    responses = _ordered_submission_responses(submissions)
    to_by_submission, cc_by_submission = _submission_status_recipients(responses)
    grouped, missing_responses = _group_responses_by_recipient(responses)
    missing_errors_by_submission = _missing_response_errors(missing_responses)
    sent_at = timezone.now()
    sent_recipients = []
    all_cc = []
    group_errors = []
    group_errors_by_submission = defaultdict(list)

    for group in grouped.values():
        to_email = group['to_email']
        group_responses = group['responses']
        group_submission_ids = {
            _response_submission_key(response) for response in group_responses
        }
        try:
            cc_emails = _send_recipient_group(to_email, group_responses)
        except Exception as exc:
            error = f'{to_email}: {exc}'
            group_errors.append(error)
            for submission_id in group_submission_ids:
                group_errors_by_submission[submission_id].append(error)
            continue

        sent_recipients.append(to_email)
        all_cc.extend(cc_emails)

    for submission in submissions:
        submission_key = _submission_key(submission)
        submission_errors = []
        submission_errors.extend(missing_errors_by_submission.get(submission_key, []))
        submission_errors.extend(group_errors_by_submission.get(submission_key, []))

        submission.email_to = _clean_unique_emails(
            to_by_submission.get(submission_key, [])
        )
        submission.email_cc = _clean_unique_emails(
            cc_by_submission.get(submission_key, [])
        )
        submission.email_sent = bool(submission.email_to) and not submission_errors
        submission.email_sent_at = sent_at if submission.email_sent else None
        submission.email_error = ' '.join(submission_errors)
        if not submission.email_to and not submission.email_error:
            submission.email_error = 'No responsible email configured.'
        submission.save(
            update_fields=[
                'email_to',
                'email_cc',
                'email_sent',
                'email_sent_at',
                'email_error',
            ]
        )

    missing_errors = [
        error
        for errors in missing_errors_by_submission.values()
        for error in errors
    ]
    return {
        'recipients': _clean_unique_emails(sent_recipients),
        'cc': _clean_unique_emails(all_cc),
        'sent_count': len(sent_recipients),
        'failed_count': len(group_errors),
        'errors': missing_errors + group_errors,
    }


def send_inspection_summary_email(submissions):
    """Send one consolidated all-floors email to configured summary recipients."""
    submissions = list(submissions)
    recipients = _report_recipient_emails()
    if not submissions or not recipients:
        return {
            'recipients': [],
            'cc': [],
            'sent_count': 0,
            'failed_count': 0,
            'errors': [],
        }

    responses = _ordered_submission_responses(submissions)
    if not responses:
        error = 'No inspection responses were available for the consolidated email.'
        _append_submission_email_status(submissions, [], [error])
        return {
            'recipients': [],
            'cc': [],
            'sent_count': 0,
            'failed_count': 1,
            'errors': [error],
        }

    sent_at = timezone.now()
    try:
        _send_floor_group_email(
            recipients,
            responses,
            'Warehouse Inspection Consolidated Report',
            cc_emails=[],
        )
    except Exception as exc:
        error = f"{', '.join(recipients)}: {exc}"
        _append_submission_email_status(submissions, [], [error], sent_at=sent_at)
        return {
            'recipients': [],
            'cc': [],
            'sent_count': 0,
            'failed_count': 1,
            'errors': [error],
        }

    _append_submission_email_status(submissions, recipients, sent_at=sent_at)
    return {
        'recipients': recipients,
        'cc': [],
        'sent_count': 1,
        'failed_count': 0,
        'errors': [],
    }


def send_inspection_submission_email(submission):
    """Compatibility wrapper for the single-submission API path."""
    if should_send_responsible_inspection_emails():
        send_inspection_submission_emails([submission])
    send_inspection_summary_email([submission])
    submission.refresh_from_db(
        fields=[
            'email_to',
            'email_cc',
            'email_sent',
            'email_sent_at',
            'email_error',
        ]
    )
    return submission


def default_report_date():
    """
    The day a morning digest should cover: YESTERDAY.

    This report is emailed by the 09:00 cron, so reporting on "today" would
    describe a day that has barely started - the day's inspections have not
    been done yet at 9am, which made every activity section read "no
    inspections were submitted today" while the open-ticket status section
    simultaneously listed open issues. A morning digest summarises the day
    that just ended.
    """
    return timezone.localdate() - timedelta(days=1)


def build_daily_warehouse_inspection_report(report_date=None):
    """
    End-to-end activity for `report_date` (default: yesterday, see
    default_report_date): who opened the module and when, every inspection
    actually submitted that day (who/what/yes-or-no per checkpoint), the
    subset answered 'No' ("problems"), plus the CURRENT open-issue state per
    building/floor/condition (that part is live state, not date-filtered).
    """
    report_date = report_date or default_report_date()

    login_summary = OrderedDict()
    for login in (
        WarehouseInspectionLoginLog.objects
        .filter(logged_in_at__date=report_date)
        .order_by('user_email', 'logged_in_at')
    ):
        key = login.user_email.lower()
        entry = login_summary.setdefault(key, {
            'user_email': login.user_email,
            'user_name': login.user_name,
            'first_login_at': login.logged_in_at,
            'last_login_at': login.logged_in_at,
            'login_count': 0,
        })
        entry['last_login_at'] = login.logged_in_at
        entry['login_count'] += 1

    events = list(
        WarehouseInspectionSubmissionEvent.objects
        .filter(created_at__date=report_date)
        .select_related(
            'submission',
            'submission__building',
            'submission__condition_category',
            'submission__floor',
        )
        .prefetch_related('responses__checkpoint')
        .order_by(
            'submission__building__building_order',
            'submission__floor__floor_order',
            'created_at',
        )
    )

    inspections = []
    problems = []
    for event in events:
        responses = list(event.responses.all())
        inspections.append({'event': event, 'submission': event.submission, 'responses': responses})
        # Carry the full scope onto each problem row - a bare checkpoint name
        # is not actionable without knowing which building/floor/condition it
        # came from, or when it was found.
        for response in responses:
            if response.response != 'no':
                continue
            problems.append({
                'response': response,
                'building_name': event.submission.building.building_name,
                'condition_name': event.submission.condition_category.condition_category_name,
                'floor_name': event.submission.floor.floor_name,
                'inspection_date': event.submission.inspection_date,
                'submitted_by_email': event.submitted_by_email,
                'submitted_at': event.created_at,
            })

    # Current open issues, with the age of each one. Not filtered by
    # report_date: this is "what is still wrong right now", which is the
    # point of the status section.
    today = timezone.localdate()
    open_tickets = []
    for ticket in (
        WarehouseInspectionTicket.objects
        .filter(status='open')
        .select_related(
            'building', 'floor', 'condition_category', 'checkpoint', 'last_response',
        )
        .order_by('building__building_order', 'floor__floor_order', 'checkpoint__checkpoint_order')
    ):
        opened_on = timezone.localtime(ticket.opened_at).date() if ticket.opened_at else None
        open_tickets.append({
            'ticket': ticket,
            'building_name': ticket.building.building_name,
            'floor_name': ticket.floor.floor_name,
            'condition_name': ticket.condition_category.condition_category_name,
            'checkpoint_name': ticket.checkpoint.checkpoint_name,
            'opened_on': opened_on,
            'as_of': today,
            'days_open': (today - opened_on).days if opened_on else 0,
            'last_comment': ticket.last_response.comments if ticket.last_response else '',
        })

    open_counts_by_building = {
        row['building_id']: row['open_count']
        for row in (
            WarehouseInspectionTicket.objects
            .filter(status='open')
            .values('building_id')
            .annotate(open_count=Count('ticket_id'))
        )
    }
    building_status = [
        {
            'building': building,
            'open_ticket_count': open_counts_by_building.get(building.building_id, 0),
            'is_open': open_counts_by_building.get(building.building_id, 0) > 0,
        }
        for building in (
            WarehouseInspectionBuilding.objects
            .filter(is_active=True)
            .order_by('building_order', 'building_name')
        )
    ]

    return {
        'report_date': report_date,
        'generated_on': today,
        'logins': list(login_summary.values()),
        'inspections': inspections,
        'problems': problems,
        'open_tickets': open_tickets,
        'building_status': building_status,
    }


def send_daily_warehouse_inspection_report(report_date=None):
    """Email the daily activity report to the active Report Recipients master."""
    report_date = report_date or default_report_date()
    recipients = _report_recipient_emails()
    if not recipients:
        return {
            'recipients': [],
            'cc': [],
            'sent_count': 0,
            'failed_count': 0,
            'errors': ['No report recipients configured - add one under Report Recipients.'],
        }

    report = build_daily_warehouse_inspection_report(report_date)
    logo_path = _warehouse_inspection_logo_path()
    context = {
        **report,
        'contact_email': WAREHOUSE_INSPECTION_CONTACT_EMAIL,
        'brand_logo_cid': WAREHOUSE_INSPECTION_LOGO_CID if logo_path else '',
    }
    text_body = render_to_string('warehouse_inspection/email/daily_report.txt', context)
    html_body = render_to_string('warehouse_inspection/email/daily_report.html', context)

    recipient_keys = {email.lower() for email in recipients}
    cc_emails = _clean_unique_emails(
        email for email in _testing_cc_emails() if email.lower() not in recipient_keys
    )

    message = EmailMultiAlternatives(
        subject=f'Warehouse Inspection - Daily Activity Report - {report_date.isoformat()}',
        body=text_body,
        from_email=_warehouse_inspection_from_email(),
        to=recipients,
        cc=cc_emails,
    )
    message.attach_alternative(html_body, 'text/html')
    _attach_branding_logo(message, logo_path)
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - surfaced to the cron command's output
        return {
            'recipients': [],
            'cc': [],
            'sent_count': 0,
            'failed_count': 1,
            'errors': [f"{', '.join(recipients)}: {exc}"],
        }

    return {
        'recipients': recipients,
        'cc': cc_emails,
        'sent_count': 1,
        'failed_count': 0,
        'errors': [],
    }
