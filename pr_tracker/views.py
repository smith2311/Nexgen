import io
import logging
import re
import threading
from datetime import datetime
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import models
from django.db import connections as _db_connections
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ui.decorators import uses_components

from .email_utils import (
    send_pr_added_notification, send_pr_assigned, send_pr_closed, send_pr_filled,
    send_pr_responsible, send_pr_updated,
)
from .models import PRAssignment, PRDocument, PRLog, TrackingMaster

User = get_user_model()
logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_TRUTHY = frozenset({'y', 'yes', 'true', '1', 'x'})

# ── Assignment documents ──────────────────────────────────────────────────────
#
# What may be uploaded alongside a PR assignment. The allow-list is on the
# extension rather than the browser-supplied content type, which is trivially
# forged; these files are served back as downloads and never rendered, so the
# risk being managed is someone parking an executable on the server, not
# cross-site scripting.
PR_DOC_EXTENSIONS = frozenset({
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.ppt', '.pptx',
    '.txt', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.zip', '.msg', '.eml',
})
PR_DOC_MAX_BYTES = 15 * 1024 * 1024     # per file
PR_DOC_MAX_COUNT = 10                   # per assignment


def _save_pr_documents(pr, files, user):
    """
    Store the uploaded documents against a PR.

    Returns ``(saved, problems)`` — the rows created, and a list of sentences
    naming anything that was refused. Nothing raises: a bad file among five
    should not lose the four good ones or the assignment itself, and the
    caller surfaces the problems as a warning next to the success message.
    """
    saved, problems = [], []
    files = [f for f in (files or []) if f]

    if len(files) > PR_DOC_MAX_COUNT:
        problems.append(
            f'Only the first {PR_DOC_MAX_COUNT} documents were kept '
            f'({len(files)} were selected).'
        )
        files = files[:PR_DOC_MAX_COUNT]

    for upload in files:
        name = (upload.name or 'document').strip()
        extension = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
        if extension not in PR_DOC_EXTENSIONS:
            problems.append(f'“{name}” was not saved — {extension or "that"} '
                            f'files are not accepted.')
            continue
        if upload.size > PR_DOC_MAX_BYTES:
            problems.append(f'“{name}” was not saved — it is larger than '
                            f'{PR_DOC_MAX_BYTES // (1024 * 1024)} MB.')
            continue

        saved.append(PRDocument.objects.create(
            pr=pr,
            file=upload,
            original_name=name[:255],
            size_bytes=upload.size or 0,
            content_type=(getattr(upload, 'content_type', '') or '')[:120],
            uploaded_by=user,
        ))

    return saved, problems


# ── Sending notifications without making the manager wait ────────────────────
#
# An assign used to hold the POST open until the mail server had accepted
# every message — three of them, one carrying up to 10 MB of attachments. The
# save was not slow; the SMTP conversation was, and it ran before the redirect.
#
# The send now happens on a worker thread and the screen returns at once. The
# cost is that nobody is watching when it fails, so a failure is written to the
# PR's activity log as 'notification_failed' and surfaced at the top of the PR
# detail screen. A mail that did not arrive is still visible — a moment later
# and in a different place, rather than not at all.

NOTIFICATION_FAILED = 'notification_failed'


def _run_in_background(fn, *args, **kwargs):
    """
    Run one notification job off the request thread.

    Synchronous when PR_TRACKER_EMAIL_ASYNC is False, which is how the tests
    run it: a thread would race the test transaction, and what is worth
    asserting is which mails are produced, not which thread produced them.
    """
    if not getattr(settings, 'PR_TRACKER_EMAIL_ASYNC', True):
        fn(*args, **kwargs)
        return

    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception('PR Tracker notification job failed')
        finally:
            # This thread opened its own connection; leaving it behind leaks
            # one per assign.
            _db_connections.close_all()

    threading.Thread(target=runner, daemon=True).start()


def _notify_assignment_job(pr_id, previous):
    """
    The background half of an assign: send, then record anything that failed.

    The PR is re-read rather than carried across the thread boundary, so the
    mail reflects the row as committed rather than as it looked mid-request.
    """
    pr = PRAssignment.objects.filter(pk=pr_id).first()
    if pr is None:
        return
    _sent, problems = _notify_assignment(pr, previous, list(pr.documents.all()))
    for problem in problems:
        _log(pr, NOTIFICATION_FAILED, None, problem)


def _notify_assignment_batch(jobs):
    """Every row's notifications, one after another on a single thread."""
    for pr_id, previous in jobs:
        _notify_assignment_job(pr_id, previous)


def _assign_upsert(pr_number, defaults):
    """
    Create or update the PR with this number, and report what it was before.

    Returns ``(pr, created, previous)`` where ``previous`` is
    ``(responsible_id, assigned_id)`` as they stood before this write.

    update_or_create() is deliberately not used: ``pr_number`` carries only
    db_index, not unique, so two rows can legitimately share a number — a
    duplicated line in an Excel sheet is all it takes. update_or_create()
    raises MultipleObjectsReturned on that, which reached the manager as a
    500 on the Assign screen with the upload half-applied. Here the newest
    matching row wins, which is the one the dashboard shows and therefore the
    one the manager believes they are editing.
    """
    existing = PRAssignment.objects.filter(pr_number=pr_number).order_by('-assigned_at').first()
    if existing is None:
        return PRAssignment.objects.create(pr_number=pr_number, **defaults), True, None

    previous = (existing.responsible_person_id, existing.assigned_person_id)
    for field, value in defaults.items():
        setattr(existing, field, value)
    existing.save()
    return existing, False, previous


def _notify_assignment(pr, previous, documents):
    """
    Send the assignment notifications this save actually warrants.

    THE RULE: a person is told when they are newly given the PR — that is,
    when the field naming them was empty and now is not, or named somebody
    else and now names them. Re-saving a PR without touching either field
    sends nothing, so editing a PR number or a tracking code does not spray
    mail at people who already know.

    This replaces a single ``email_sent`` flag on the PR, which meant that
    once *any* assignment mail had gone out for a PR number, re-assigning it
    notified nobody — neither the new responsible person nor the new member.
    The flag is still maintained for anything that reads it, but it no longer
    decides who is told.

    ``previous`` is ``(responsible_id, assigned_id)`` as they were before the
    save, or None for a PR that did not exist.

    Returns ``(sent, problems)`` — whether any mail actually went out, and a
    list of sentences naming anyone who should have been told and was not.
    Both halves matter: this used to return a bare True as soon as it had
    *decided* to send, so a person with no email address on file, or a mail
    the server refused, still ended with the screen reporting "Notifications
    sent". That is the failure that hides itself — the manager moves on, and
    nobody learns the member was never told until the PR goes stale.

    Only the assigned person's mail carries the documents; see
    send_pr_responsible for why the responsible person's does not.
    """
    old_resp, old_assigned = previous if previous else (None, None)
    sent = False
    problems = []

    def deliver(person, send, label, **kwargs):
        """Send one notification and record why, if it did not go."""
        nonlocal sent
        name = person.get_full_name() or person.username
        if not person.email:
            problems.append(
                f'{name} has no email address on file, so the {label} '
                f'notification could not be sent.')
            return
        if send(**kwargs):
            sent = True
        else:
            problems.append(
                f'The {label} notification to {name} could not be sent — '
                f'the mail server refused it. The assignment itself is saved.')

    if pr.assigned_person_id and pr.assigned_person_id != old_assigned:
        deliver(pr.assigned_person, send_pr_assigned, 'assignment',
                pr=pr, documents=documents)

    if pr.responsible_person_id and pr.responsible_person_id != old_resp:
        deliver(pr.responsible_person, send_pr_responsible,
                'responsible-person', pr=pr)

    if sent and not pr.email_sent:
        pr.email_sent = True
        pr.save(update_fields=['email_sent'])

    return sent, problems


# ── Role helpers ──────────────────────────────────────────────────────────────
#
# A user can hold several roles at once (e.g. someone in both the PR-Manager
# and PR-Admin Django groups) — so access checks work against the full set of
# roles held, not a single classification.
#
# PR-Admin is the module's administrator and carries every role with it: the
# dashboard, assigning, PR detail, the masters, the lot. It used to grant only
# 'admin', which meant the person responsible for the module could add users
# and tracking numbers but could not open the dashboard they were supporting.
# This is scoped to PR Tracker — it is not is_superuser and grants nothing
# anywhere else in NexGen.

def _pr_roles(user):
    """Set of PR Tracker roles this user holds — subset of {'manager', 'admin', 'member'}."""
    if not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {'manager', 'admin', 'member'}
    roles = set()
    if user.groups.filter(name='PR-Admin').exists():
        return {'manager', 'admin', 'member'}
    if user.groups.filter(name='PR-Manager').exists():
        roles.add('manager')
    if user.groups.filter(name='PR-Member').exists():
        roles.add('member')
    return roles


def _pr_role(user):
    roles = _pr_roles(user)
    if 'admin' in roles:
        return 'admin'
    if 'manager' in roles:
        return 'manager'
    if 'member' in roles:
        return 'member'
    return None


def _pr_require(*roles):
    def decorator(view_fn):
        @wraps(view_fn)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('/')
            if not _pr_roles(request.user) & set(roles):
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden('You do not have permission to access this page.')
            return view_fn(request, *args, **kwargs)
        return wrapper
    return decorator


def _pr_login_required(view_fn):
    @wraps(view_fn)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/')
        return view_fn(request, *args, **kwargs)
    return wrapper


def _log(pr, action, user, notes=''):
    PRLog.objects.create(pr=pr, action=action, performed_by=user, notes=notes)


# ── Excel helpers ─────────────────────────────────────────────────────────────

def _find_user(val):
    """Lookup User by email or username (case-insensitive)."""
    val = (val or '').strip()
    if not val:
        return None
    if '@' in val:
        return User.objects.filter(email__iexact=val).first()
    u = User.objects.filter(username__iexact=val).first()
    if u:
        return u
    parts = val.split()
    if len(parts) >= 2:
        return User.objects.filter(
            first_name__iexact=parts[0], last_name__iexact=parts[-1]
        ).first()
    return None


def _parse_date(val):
    if not val:
        return None
    if hasattr(val, 'date'):
        return val.date()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_int(val):
    try:
        return int(float(str(val))) if val is not None else None
    except (ValueError, TypeError):
        return None


def _parse_dec(val):
    try:
        return float(str(val)) if val is not None else None
    except (ValueError, TypeError):
        return None


def _header_key(val):
    return str(val or '').strip().lower().replace(' ', '_').replace('/', '_')


def _unique_username(email):
    username = email.split('@')[0]
    base, counter = username, 1
    while User.objects.filter(username=username).exists():
        username = f'{base}{counter}'
        counter += 1
    return username


# ── Admin Excel upload ────────────────────────────────────────────────────────

def _process_admin_excel(fileobj, assigned_by):
    """
    Assign many PRs from a sheet.

    Every row reports what actually happened to it. A name that matches no
    user, or a member who cannot open the PR Tracker, used to pass silently:
    the row said "Created", the PR was saved with nobody on it, no mail went
    anywhere, and the first anyone knew was the PR sitting untouched. The
    person named in the sheet is checked against the roles that decide what
    they can see, and anything wrong is said on the row.
    """
    wb = load_workbook(fileobj, data_only=True)
    ws = wb.active
    headers = [_header_key(c.value) for c in ws[1]]
    results, errors = [], 0

    # The groups that decide whether the named people can reach the PR at all.
    # Looked up once rather than per row.
    member_ids = set(User.objects.filter(
        groups__name='PR-Member').values_list('id', flat=True))
    manager_ids = set(User.objects.filter(
        groups__name='PR-Manager').values_list('id', flat=True))
    jobs = []

    for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        rd = dict(zip(headers, row))
        pr_number  = str(rd.get('pr_number', '') or '').strip()
        resp_val   = str(rd.get('responsible_person', '') or '').strip()
        assign_val = str(rd.get('assigned_person', '') or '').strip()

        if not pr_number:
            results.append({'row': row_num, 'pr': '—', 'status': 'error', 'msg': 'Missing PR Number'})
            errors += 1
            continue

        resp_user   = _find_user(resp_val)
        assign_user = _find_user(assign_val)

        notes = []
        if assign_val and not assign_user:
            notes.append(f'No user matches “{assign_val}” — nobody is assigned '
                         f'to this PR and no notification was sent.')
        elif assign_user and assign_user.id not in member_ids:
            notes.append(f'{assign_user.get_full_name() or assign_user.username} '
                         f'is not a PR Member, so this PR will not appear on '
                         f'their My PRs screen. Add the role in User Master.')
        if resp_val and not resp_user:
            notes.append(f'No user matches “{resp_val}” — no responsible person '
                         f'was set.')
        elif resp_user and resp_user.id not in manager_ids:
            notes.append(f'{resp_user.get_full_name() or resp_user.username} '
                         f'is not a PR Manager, so they cannot open this PR.')

        pr, created, before = _assign_upsert(pr_number, {
            'responsible_person': resp_user,
            'assigned_person':    assign_user,
            'assigned_by':        assigned_by,
        })

        # A bulk sheet carries no documents — it assigns many PRs at once, and
        # one set of files could not belong to all of them. Documents are the
        # manual assign screen's feature; see pr_assign.
        #
        # One thread per row would be a hundred threads for a hundred rows, so
        # the sheet's sends are collected and run as one job after the loop.
        jobs.append((pr.pk, before))

        action = 'Created' if created else 'Updated'
        _log(pr, 'assigned' if created else 'reassigned', assigned_by,
             f'Via Excel upload (row {row_num})')
        if notes:
            errors += 1
        results.append({'row': row_num, 'pr': pr_number, 'status': action,
                        'msg': ' '.join(notes)})

    if jobs:
        _run_in_background(_notify_assignment_batch, jobs)

    return results, errors


# ── Member Excel upload ───────────────────────────────────────────────────────

def _process_member_excel(fileobj, user):
    wb = load_workbook(fileobj, data_only=True)
    ws = wb.active
    headers = [_header_key(c.value) for c in ws[1]]
    results, errors = [], 0

    tracking_map = {
        t.tracking_number.upper(): t
        for t in TrackingMaster.objects.filter(is_active=True)
    }
    status_map = {v.lower().replace(' ', '_'): k for k, v in PRAssignment.STATUS_CHOICES}

    for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        rd = dict(zip(headers, row))

        def s(k): return str(rd.get(k, '') or '').strip()

        pr_number = s('pr_number')
        if not pr_number:
            results.append({'row': row_num, 'pr': '—', 'status': 'error', 'msg': 'Missing PR Number'})
            errors += 1
            continue

        try:
            pr = PRAssignment.objects.get(pr_number=pr_number, assigned_person=user)
        except PRAssignment.DoesNotExist:
            results.append({'row': row_num, 'pr': pr_number, 'status': 'error',
                            'msg': 'Not found or not assigned to you'})
            errors += 1
            continue

        old_status = pr.status
        old_fill_percent = pr.fill_percent

        if s('vertical'):            pr.vertical = s('vertical')
        pr.pr_received_date  = _parse_date(rd.get('pr_received_date'))  or pr.pr_received_date
        pr.pr_release_date   = _parse_date(rd.get('pr_release_date'))   or pr.pr_release_date

        tn_val = s('tracking_number')
        if tn_val:
            tm = tracking_map.get(tn_val.upper())
            if tm:
                pr.tracking_number = tm

        pr.no_of_line_items  = _parse_int(rd.get('no_of_line_items'))  or pr.no_of_line_items
        if s('description'):         pr.description = s('description')
        if s('requester_name'):      pr.requester_name = s('requester_name')
        if s('request_for_use'):     pr.request_for_use = s('request_for_use')
        if s('vendors_creation'):    pr.vendors_creation = s('vendors_creation').lower()
        if s('po_number'):           pr.po_number = s('po_number')
        pr.po_number_ageing_days = _parse_int(rd.get('po_number_ageing_days')) or pr.po_number_ageing_days
        pr.amount = _parse_dec(rd.get('amount')) or pr.amount
        if s('regularized_negotiation'): pr.regularized_negotiation = s('regularized_negotiation').lower()
        if s('grn_created'):         pr.grn_created = s('grn_created').lower()
        if s('advance_number'):      pr.advance_number = s('advance_number')
        if s('closure'):             pr.closure = s('closure')
        if s('remark'):              pr.remark = s('remark')

        raw_status = s('status')
        if raw_status:
            mapped = status_map.get(raw_status.lower().replace(' ', '_'))
            if mapped:
                pr.status = mapped

        pr.last_filled_by = user
        pr.last_filled_at = timezone.now()
        pr.save()

        _log(pr, 'detail_uploaded', user, f'Via Excel upload (row {row_num})')
        if pr.status == 'closed' and old_status != 'closed':
            send_pr_closed(pr, user)
        elif pr.fill_percent == 100 and old_fill_percent != 100:
            send_pr_filled(pr, user)
        elif pr.status != old_status:
            send_pr_updated(pr, user)

        results.append({'row': row_num, 'pr': pr_number, 'status': 'Updated'})

    return results, errors


# ── User Excel upload ─────────────────────────────────────────────────────────

def _process_user_excel(fileobj, manager_grp, member_grp):
    wb = load_workbook(fileobj, data_only=True)
    ws = wb.active
    headers = [_header_key(c.value) for c in ws[1]]
    results, errors = [], 0

    email_col = next((i for i, h in enumerate(headers) if h in ('email', 'email_id', 'user_email')), None)
    if email_col is None:
        raise ValueError('Column "Email" not found. Please use the provided template.')
    first_name_col = headers.index('first_name') if 'first_name' in headers else None
    last_name_col  = headers.index('last_name')  if 'last_name'  in headers else None
    manager_col = headers.index('manager') if 'manager' in headers else None
    member_col  = headers.index('member')  if 'member'  in headers else None
    role_col    = headers.index('role')    if 'role'    in headers else None

    def truthy(v):
        return str(v or '').strip().lower() in _TRUTHY

    for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        email = str(row[email_col] or '').strip().lower()
        if not email:
            continue
        if not _EMAIL_RE.match(email):
            results.append({'row': row_num, 'email': email, 'status': 'error', 'msg': 'Invalid email address'})
            errors += 1
            continue

        first_name = str(row[first_name_col] or '').strip() if first_name_col is not None else ''
        last_name  = str(row[last_name_col]  or '').strip() if last_name_col  is not None else ''

        wants_manager = truthy(row[manager_col]) if manager_col is not None else False
        wants_member  = truthy(row[member_col])  if member_col  is not None else False
        if role_col is not None:
            rv = str(row[role_col] or '').strip().lower()
            if rv in ('manager', 'pr-manager', 'pr manager'):
                wants_manager = True
            elif rv in ('member', 'pr-member', 'pr member'):
                wants_member = True
            elif rv == 'both':
                wants_manager = wants_member = True

        user = User.objects.filter(email__iexact=email).first()
        created = False
        if not user:
            user = User.objects.create_user(
                username=_unique_username(email), email=email,
                first_name=first_name, last_name=last_name,
            )
            created = True
        else:
            if first_name: user.first_name = first_name
            if last_name:  user.last_name = last_name
            user.save()

        # Independent ifs (not elif) so a row can grant both accesses at once.
        user.groups.remove(manager_grp, member_grp)
        if wants_manager:
            user.groups.add(manager_grp)
        if wants_member:
            user.groups.add(member_grp)

        results.append({'row': row_num, 'email': email, 'status': 'Created' if created else 'Updated'})

    return results, errors


# ── Excel template downloads ──────────────────────────────────────────────────

def _style_header(ws, headers, color='0B1F3A'):
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font      = Font(bold=True, color='FFFFFF')
        cell.fill      = PatternFill(fill_type='solid', fgColor=color)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[cell.column_letter].width = max(len(h) + 6, 16)
    ws.row_dimensions[1].height = 22


def admin_template_download(request):
    wb = Workbook()
    ws = wb.active
    ws.title = 'PR Assignments'
    headers = ['PR Number', 'Responsible Person', 'Assigned Person']
    _style_header(ws, headers)
    ws.append(['PR-2024-001', 'john.smith  (or  john@merillife.com)',
               'jane.doe  (or  jane@merillife.com)'])
    ws.cell(2, 1).font = Font(italic=True, color='888888')
    ws.cell(2, 2).font = Font(italic=True, color='888888')
    ws.cell(2, 3).font = Font(italic=True, color='888888')

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="PR_Assignment_Template.xlsx"'
    return resp


def member_template_download(request):
    wb = Workbook()
    ws = wb.active
    ws.title = 'PR Detail'
    headers = [
        'PR Number', 'Vertical', 'PR Received Date', 'PR Release Date',
        'Tracking Number', 'No of Line Items', 'Description',
        'Requester Name', 'Request For Use', 'Vendors Creation',
        'PO Number', 'PO Number Ageing Days', 'Amount',
        'Regularized/Negotiation', 'GRN Created', 'Advance Number',
        'Closure', 'Remark', 'Status',
    ]
    _style_header(ws, headers, color='0F7B6C')
    sample = [
        'PR-2024-001', 'Cardiology', '2024-01-15', '2024-01-20',
        'TRK001', '5', 'Surgical supplies', 'Dr. Sharma',
        'Clinical trial', 'Yes', 'PO-2024-001', '30',
        '50000.00', 'Regularized', 'No', 'ADV-001',
        'Closure notes', 'Sample remark', 'In Progress',
    ]
    ws.append(sample)
    for c in range(1, len(sample) + 1):
        ws.cell(2, c).font = Font(italic=True, color='888888')

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="PR_Detail_Template.xlsx"'
    return resp


@_pr_require('admin')
def user_template_download(request):
    wb = Workbook()
    ws = wb.active
    ws.title = 'PR Users'
    headers = ['First Name', 'Last Name', 'Email', 'Manager', 'Member']
    _style_header(ws, headers)
    ws.append(['Jane', 'Doe', 'jane.doe@merillife.com', 'Y', 'N'])
    ws.append(['John', 'Smith', 'john.smith@merillife.com', 'N', 'Y'])
    ws.append(['Asha', 'Rao', 'asha.rao@merillife.com', 'Y', 'Y'])
    for r in (2, 3, 4):
        for c in range(1, 6):
            ws.cell(r, c).font = Font(italic=True, color='888888')

    note_row = ws.max_row + 2
    ws.cell(row=note_row, column=1, value='Notes:').font = Font(bold=True, color='374151')
    ws.cell(row=note_row + 1, column=1,
            value='Use Y/N (or Yes/No, True/False, 1/0) to set roles.').font = Font(color='6B7280', italic=True)
    ws.cell(row=note_row + 2, column=1,
            value='Mark both Manager and Member as Y to give a user both accesses (e.g. someone who also follows up on a specific PO).').font = Font(color='6B7280', italic=True)
    ws.cell(row=note_row + 3, column=1,
            value='If the email does not already exist, a new account is created using First Name / Last Name.').font = Font(color='6B7280', italic=True)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="PR_Users_Template.xlsx"'
    return resp


# ── Home ──────────────────────────────────────────────────────────────────────

@_pr_login_required
@uses_components('data_table', 'master_page')
def pr_home(request):
    roles = _pr_roles(request.user)
    return render(request, 'pr_tracker/home.html', {
        'pr_role':  _pr_role(request.user),
        'pr_roles': roles,
        'is_pr_manager': 'manager' in roles,
        'is_pr_admin':   'admin' in roles,
        'is_pr_member':  'member' in roles,
        'has_no_pr_role': not roles,
    })


# ── Manager: Dashboard ────────────────────────────────────────────────────────

def _closure_ageing_report(closed_from, closed_to):
    """Pivot: closed PRs by assigned person × ageing days (po_number_ageing_days,
    frozen at closure). Columns are every distinct ageing value actually present
    in the filtered data — plus 'N/A' for closed PRs with no ageing value, so
    nothing is silently dropped."""
    qs = PRAssignment.objects.filter(status='closed')
    if closed_from:
        qs = qs.filter(closed_at__gte=closed_from)
    if closed_to:
        qs = qs.filter(closed_at__lte=closed_to)

    rows = (
        qs.values('assigned_person_id', 'assigned_person__first_name',
                   'assigned_person__last_name', 'assigned_person__username',
                   'po_number_ageing_days')
          .annotate(cnt=Count('id'))
    )

    day_values = sorted({r['po_number_ageing_days'] for r in rows if r['po_number_ageing_days'] is not None})
    has_na = any(r['po_number_ageing_days'] is None for r in rows)
    columns = day_values + (['N/A'] if has_na else [])

    persons = {}
    for r in rows:
        pid = r['assigned_person_id']
        if pid is None:
            key, name = 'unassigned', 'Unassigned'
        else:
            key = pid
            name = (
                f"{r['assigned_person__first_name'] or ''} {r['assigned_person__last_name'] or ''}".strip()
                or r['assigned_person__username']
            )
        if key not in persons:
            persons[key] = {'name': name, 'cells': {}, 'total': 0}
        day = r['po_number_ageing_days'] if r['po_number_ageing_days'] is not None else 'N/A'
        persons[key]['cells'][day] = persons[key]['cells'].get(day, 0) + r['cnt']
        persons[key]['total'] += r['cnt']

    grand_total = sum(p['total'] for p in persons.values())
    col_totals = [sum(p['cells'].get(c, 0) for p in persons.values()) for c in columns]
    col_pct = [round(t / grand_total * 100) if grand_total else 0 for t in col_totals]

    person_rows = sorted(persons.values(), key=lambda p: p['name'])
    for p in person_rows:
        p['cell_list'] = [p['cells'].get(c, 0) for c in columns]

    return {
        'columns':     columns,
        'persons':     person_rows,
        'col_totals':  col_totals,
        'col_pct':     col_pct,
        'grand_total': grand_total,
    }


@_pr_require('manager')
@uses_components('data_table', 'master_page')
def pr_dashboard(request):
    if request.method == 'POST' and request.POST.get('action') == 'delete':
        pks = request.POST.getlist('pks')
        prs = PRAssignment.objects.filter(pk__in=pks)
        count = prs.count()
        for pr in prs:
            pr.soft_delete(request.user.username)
        if count:
            messages.success(request, f'{count} PR(s) deleted.')
        else:
            messages.warning(request, 'No PRs were selected.')
        return redirect('pr_tracker:pr_dashboard')

    qs = PRAssignment.objects.select_related(
        'responsible_person', 'assigned_person', 'tracking_number'
    )
    status_f  = request.GET.get('status', '')
    member_f  = request.GET.get('member', '')
    search_f  = request.GET.get('q', '').strip()

    if status_f:
        qs = qs.filter(status=status_f)
    if member_f:
        qs = qs.filter(assigned_person_id=member_f)
    if search_f:
        qs = qs.filter(pr_number__icontains=search_f)

    total = PRAssignment.objects.count()
    stats = {
        'total':       total,
        'open':        PRAssignment.objects.filter(status='open').count(),
        'in_progress': PRAssignment.objects.filter(status='in_progress').count(),
        'po_raised':   PRAssignment.objects.filter(status='po_raised').count(),
        'grn_created': PRAssignment.objects.filter(status='grn_created').count(),
        'closed':      PRAssignment.objects.filter(status='closed').count(),
        'overdue':     PRAssignment.objects.filter(status='overdue').count(),
    }
    members = User.objects.filter(assigned_prs__isnull=False).distinct()

    closed_from_raw = request.GET.get('closed_from', '').strip()
    closed_to_raw   = request.GET.get('closed_to', '').strip()
    ageing_report = _closure_ageing_report(
        _parse_date(closed_from_raw), _parse_date(closed_to_raw),
    )

    return render(request, 'pr_tracker/dashboard.html', {
        'assignments':     qs[:200],
        'stats':           stats,
        'members':         members,
        'status_choices':  PRAssignment.STATUS_CHOICES,
        'filters':         {'status': status_f, 'member': member_f, 'q': search_f},
        'ageing_report':   ageing_report,
        'ageing_filters':  {'closed_from': closed_from_raw, 'closed_to': closed_to_raw},
        'pr_role':         _pr_role(request.user),
    })


# ── Manager: Assign (form + upload) ──────────────────────────────────────────

@_pr_require('manager')
@uses_components('data_table', 'master_page')
def pr_assign(request):
    # Each dropdown only offers users holding the matching PR Tracker role
    # (added via the User Master screen) — Responsible Person from PR-Manager,
    # Assigned Person from PR-Member. A dual-role user appears in both.
    responsible_users = User.objects.filter(
        is_active=True, groups__name='PR-Manager',
    ).order_by('first_name', 'username')
    assigned_users = User.objects.filter(
        is_active=True, groups__name='PR-Member',
    ).order_by('first_name', 'username')
    upload_results = None

    if request.method == 'POST':
        action = request.POST.get('action', 'manual')

        if action == 'upload':
            f = request.FILES.get('excel_file')
            if not f:
                messages.error(request, 'Please select an Excel file.')
            else:
                try:
                    results, errors = _process_admin_excel(f, request.user)
                    upload_results = results
                    created = sum(1 for r in results if r['status'] == 'Created')
                    updated = sum(1 for r in results if r['status'] == 'Updated')
                    if created or updated:
                        send_pr_added_notification(request.user, is_bulk=True, created=created, updated=updated)
                    msg = f'{created} PR(s) created, {updated} updated'
                    if errors:
                        msg += f', {errors} error(s)'
                        messages.warning(request, msg)
                    else:
                        messages.success(request, msg + '. Emails sent to assigned members.')
                except Exception as e:
                    messages.error(request, f'Error reading file: {e}')

        else:  # manual single assign
            pr_number  = request.POST.get('pr_number', '').strip()
            resp_id    = request.POST.get('responsible_person')
            assign_id  = request.POST.get('assigned_person')

            if not pr_number:
                messages.error(request, 'PR Number is required.')
            else:
                resp_user   = User.objects.filter(pk=resp_id).first()   if resp_id   else None
                assign_user = User.objects.filter(pk=assign_id).first() if assign_id else None

                # The dropdowns only offer people holding the right role, so
                # this normally passes — it catches the case where the role was
                # taken away in User Master after the page was opened. Assigning
                # to someone without PR-Member files the PR somewhere they
                # cannot see it, which reads to them as never being assigned.
                role_problems = []
                if assign_user and not assign_user.groups.filter(name='PR-Member').exists():
                    role_problems.append(
                        f'{assign_user.get_full_name() or assign_user.username} is not '
                        f'a PR Member, so this PR will not appear on their My PRs '
                        f'screen. Add the role in User Master.')
                if resp_user and not resp_user.groups.filter(name='PR-Manager').exists():
                    role_problems.append(
                        f'{resp_user.get_full_name() or resp_user.username} is not a '
                        f'PR Manager, so they cannot open this PR.')

                pr, created, before = _assign_upsert(pr_number, {
                    'responsible_person': resp_user,
                    'assigned_person':    assign_user,
                    'assigned_by':        request.user,
                })

                # Documents first, so the notifications can carry them. A
                # re-assignment adds to what is already on the PR rather than
                # replacing it — the earlier paperwork is still the PR's.
                new_docs, doc_problems = _save_pr_documents(
                    pr, request.FILES.getlist('documents'), request.user)
                if new_docs:
                    _log(pr, 'documents_uploaded', request.user,
                         ', '.join(d.original_name for d in new_docs))

                # Whether anyone is due a mail is known here; whether it
                # arrived is not, because the sending has been handed to a
                # worker thread. The wording below says exactly that much and
                # no more — a failure shows up on the PR's own screen.
                notifying = bool(
                    (pr.assigned_person_id and pr.assigned_person_id != (before or (None, None))[1])
                    or (pr.responsible_person_id and pr.responsible_person_id != (before or (None, None))[0])
                )
                _run_in_background(_notify_assignment_job, pr.pk, before)
                doc_problems += role_problems

                _run_in_background(
                    send_pr_added_notification,
                    request.user, False,
                    1 if created else 0, 0 if created else 1, pr_number,
                )

                _log(pr, 'assigned' if created else 'reassigned', request.user)
                action_word = 'created' if created else 'updated'
                note = f'PR {pr_number} {action_word}.'
                if new_docs:
                    note += f' {len(new_docs)} document(s) uploaded.'
                # Said plainly either way: "Email sent" on a save that
                # notified nobody — because neither person changed — is the
                # kind of reassurance that hides a problem.
                note += (' Notifications are being sent.' if notifying
                         else ' No new notifications were needed.')
                messages.success(request, note)
                for problem in doc_problems:
                    messages.warning(request, problem)
                return redirect('pr_tracker:pr_dashboard')

    return render(request, 'pr_tracker/assign.html', {
        'responsible_users': responsible_users,
        'assigned_users':    assigned_users,
        'upload_results':    upload_results,
        'pr_role':           _pr_role(request.user),
    })


# ── PR document download ──────────────────────────────────────────────────────

@_pr_require('manager', 'admin', 'member')
def pr_document_download(request, pk):
    """
    Serve one uploaded PR document — the target of every document link in an
    assignment email.

    Open to any PR Tracker role rather than to managers alone: the assigned
    member is exactly the person the link is written for, and the email that
    carries it is the one telling them to go and process the PR.

    Served through this view rather than as a bare MEDIA_URL path so the file
    is behind the login at all — MEDIA is served without authentication, and
    a quotation with pricing in it should not be readable by anyone who
    guesses the filename. The stored name carries a UUID for the same reason.
    """
    return _serve_document(pk, inline=False)


@_pr_require('manager', 'admin', 'member')
def pr_document_preview(request, pk):
    """
    Serve one document for reading in place, rather than downloading it.

    Only the types a browser can actually render are served this way; anything
    else falls back to a download, so the icon that leads here is never a dead
    end. A .docx has no in-browser renderer — the honest answer is to hand the
    file over rather than open an empty viewer.
    """
    return _serve_document(pk, inline=True)


def _serve_document(pk, inline):
    document = get_object_or_404(PRDocument, pk=pk, is_deleted=False)
    try:
        handle = document.file.open('rb')
    except (FileNotFoundError, ValueError):
        raise Http404('That document is no longer on the server.')

    preview_type = document.preview_type
    show_inline = inline and preview_type is not None

    response = FileResponse(
        handle,
        as_attachment=not show_inline,
        filename=document.original_name,
        content_type=(preview_type if show_inline
                      else 'application/octet-stream'),
    )
    # The one header that always matters: without it a browser may re-sniff
    # the bytes and decide a file we called an image is really HTML, which is
    # the whole reason the type is pinned to the extension above.
    response['X-Content-Type-Options'] = 'nosniff'

    if show_inline:
        # This site sends X-Frame-Options: DENY globally (settings.py), and
        # the middleware leaves a header alone once a view has set one. DENY
        # applies to our own pages too, so without this the viewer iframe is
        # refused by the browser and shows "refused to connect" — which is
        # exactly how this shipped. SAMEORIGIN keeps every other site from
        # framing the file while letting the PR screen do it.
        response['X-Frame-Options'] = 'SAMEORIGIN'
        # Modern spelling of the same rule, and nothing more. An earlier
        # version sent "sandbox; default-src 'none'", which also blocked the
        # browser's own PDF viewer from rendering the document it had been
        # handed. The protection here is the pinned type plus nosniff — none
        # of the previewable types is script-bearing HTML.
        response['Content-Security-Policy'] = "frame-ancestors 'self'"
    return response


# ── Manager: PR Detail (read) ─────────────────────────────────────────────────

@_pr_require('manager')
@uses_components('data_table', 'master_page')
def pr_detail(request, pk):
    pr   = get_object_or_404(PRAssignment, pk=pk)
    logs = pr.logs.select_related('performed_by')
    # Sending happens on a worker thread, so a failure cannot be a message on
    # the assign screen any more — it is recorded against the PR and shown
    # here, which is where someone checking up on a quiet PR will look.
    return render(request, 'pr_tracker/pr_detail.html', {
        'pr':      pr,
        'logs':    logs,
        'notification_failures': [
            log for log in logs if log.action == NOTIFICATION_FAILED],
        'pr_role': _pr_role(request.user),
    })


# ── Member: My PRs ────────────────────────────────────────────────────────────

@_pr_require('member')
@uses_components('data_table', 'master_page')
def my_prs(request):
    qs = PRAssignment.objects.filter(
        assigned_person=request.user
    ).select_related('responsible_person', 'tracking_number')

    upload_results = None
    if request.method == 'POST':
        f = request.FILES.get('excel_file')
        if not f:
            messages.error(request, 'Please select an Excel file.')
        else:
            try:
                results, errors = _process_member_excel(f, request.user)
                upload_results = results
                updated = sum(1 for r in results if r['status'] == 'Updated')
                msg = f'{updated} PR(s) updated'
                if errors:
                    msg += f', {errors} error(s) (check rows below)'
                    messages.warning(request, msg)
                else:
                    messages.success(request, msg + '.')
            except Exception as e:
                messages.error(request, f'Error reading file: {e}')

    stats = {
        'total':       qs.count(),
        'open':        qs.filter(status='open').count(),
        'in_progress': qs.filter(status='in_progress').count(),
        'po_raised':   qs.filter(status='po_raised').count(),
        'grn_created': qs.filter(status='grn_created').count(),
        'closed':      qs.filter(status='closed').count(),
        'overdue':     qs.filter(status='overdue').count(),
    }
    return render(request, 'pr_tracker/my_prs.html', {
        'assignments':    qs,
        'stats':          stats,
        'upload_results': upload_results,
        'pr_role':        _pr_role(request.user),
    })


# ── Member: Fill PR Detail ────────────────────────────────────────────────────

@_pr_require('member')
@uses_components('data_table', 'master_page')
def pr_fill(request, pk):
    pr = get_object_or_404(PRAssignment, pk=pk, assigned_person=request.user)
    tracking_numbers = TrackingMaster.objects.filter(is_active=True)

    if request.method == 'POST':
        p = request.POST
        old_status = pr.status
        old_fill_percent = pr.fill_percent

        pr.vertical         = p.get('vertical', '').strip()
        pr.pr_received_date = _parse_date(p.get('pr_received_date'))
        pr.pr_release_date  = _parse_date(p.get('pr_release_date'))

        tn_id = p.get('tracking_number')
        pr.tracking_number  = TrackingMaster.objects.filter(pk=tn_id).first() if tn_id else None

        pr.no_of_line_items       = _parse_int(p.get('no_of_line_items'))
        pr.description            = p.get('description', '').strip()
        pr.requester_name         = p.get('requester_name', '').strip()
        pr.request_for_use        = p.get('request_for_use', '').strip()
        pr.vendors_creation       = p.get('vendors_creation', '')
        pr.po_number              = p.get('po_number', '').strip()
        pr.amount                 = _parse_dec(p.get('amount'))
        pr.regularized_negotiation = p.get('regularized_negotiation', '')
        pr.grn_created            = p.get('grn_created', '')
        pr.advance_number         = p.get('advance_number', '').strip()
        pr.closure                = p.get('closure', '').strip()
        pr.remark                 = p.get('remark', '').strip()
        pr.status                 = p.get('status', pr.status)
        pr.last_filled_by         = request.user
        pr.last_filled_at         = timezone.now()
        pr.save()

        _log(pr, 'detail_updated', request.user)
        if pr.status == 'closed' and old_status != 'closed':
            send_pr_closed(pr, request.user)
        elif pr.fill_percent == 100 and old_fill_percent != 100:
            send_pr_filled(pr, request.user)
        elif pr.status != old_status:
            send_pr_updated(pr, request.user)

        messages.success(request, f'PR {pr.pr_number} details saved.')
        return redirect('pr_tracker:my_prs')

    return render(request, 'pr_tracker/pr_fill.html', {
        'pr':              pr,
        'tracking_numbers': tracking_numbers,
        'status_choices':  PRAssignment.STATUS_CHOICES,
        'vendor_choices':  PRAssignment.VENDOR_CHOICES,
        'grn_choices':     PRAssignment.GRN_CHOICES,
        'reg_choices':     PRAssignment.REG_CHOICES,
        'pr_role':         _pr_role(request.user),
    })


# ── Tracking Master ───────────────────────────────────────────────────────────

@_pr_require('admin')
@uses_components('data_table', 'master_page')
def tracking_master(request):
    if request.method == 'POST':
        action = request.POST.get('action', 'create')
        tn_val = request.POST.get('tracking_number', '').strip().upper()
        desc   = request.POST.get('description', '').strip()
        active = request.POST.get('is_active') == 'on'

        if action == 'edit':
            obj = get_object_or_404(TrackingMaster, pk=request.POST.get('pk'))
            obj.tracking_number = tn_val
            obj.description     = desc
            obj.is_active       = active
            obj.save()
            messages.success(request, f'"{tn_val}" updated.')

        elif action == 'delete':
            obj = get_object_or_404(TrackingMaster, pk=request.POST.get('pk'))
            obj.soft_delete(request.user.username)
            messages.success(request, 'Tracking number deleted.')

        else:
            if not tn_val:
                messages.error(request, 'Tracking number is required.')
            else:
                _, created = TrackingMaster.objects.get_or_create(
                    tracking_number=tn_val,
                    defaults={'description': desc, 'is_active': active},
                )
                if created:
                    messages.success(request, f'"{tn_val}" added.')
                else:
                    messages.warning(request, f'"{tn_val}" already exists.')

        return redirect('pr_tracker:tracking_master')

    tracking = TrackingMaster.objects.all()
    return render(request, 'pr_tracker/tracking_master.html', {
        'tracking': tracking,
        'pr_role':  _pr_role(request.user),
    })


# ── User Master ───────────────────────────────────────────────────────────────

@_pr_require('admin')
@uses_components('data_table', 'master_page')
def user_master(request):
    from django.contrib.auth.models import Group

    manager_grp, _ = Group.objects.get_or_create(name='PR-Manager')
    member_grp,  _ = Group.objects.get_or_create(name='PR-Member')

    upload_results = None

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add_user':
            email      = request.POST.get('email', '').strip().lower()
            first_name = request.POST.get('first_name', '').strip()
            last_name  = request.POST.get('last_name', '').strip()
            role       = request.POST.get('role', '')

            if not email:
                messages.error(request, 'Email is required.')
            elif not _EMAIL_RE.match(email):
                messages.error(request, 'Enter a valid email address.')
            else:
                user = User.objects.filter(email__iexact=email).first()
                created = False
                if not user:
                    user = User.objects.create_user(
                        username=_unique_username(email), email=email,
                        first_name=first_name, last_name=last_name,
                    )
                    created = True
                else:
                    if first_name: user.first_name = first_name
                    if last_name:  user.last_name = last_name
                    user.save()

                user.groups.remove(manager_grp, member_grp)
                if role in ('manager', 'both'):
                    user.groups.add(manager_grp)
                if role in ('member', 'both'):
                    user.groups.add(member_grp)

                verb = 'added' if created else 'updated'
                role_desc = {
                    'manager': 'PR Manager', 'member': 'PR Member', 'both': 'PR Manager & Member',
                }.get(role, 'no PR access')
                messages.success(request, f'User "{user.get_full_name() or user.username}" {verb} with {role_desc}.')
            return redirect('pr_tracker:user_master')

        elif action == 'import_users':
            f = request.FILES.get('excel_file')
            if not f:
                messages.error(request, 'Please select an Excel file.')
            else:
                try:
                    results, errors = _process_user_excel(f, manager_grp, member_grp)
                    upload_results = results
                    created = sum(1 for r in results if r['status'] == 'Created')
                    updated = sum(1 for r in results if r['status'] == 'Updated')
                    msg = f'{created} user(s) created, {updated} updated'
                    if errors:
                        msg += f', {errors} error(s)'
                        messages.warning(request, msg)
                    else:
                        messages.success(request, msg + '.')
                except Exception as e:
                    messages.error(request, f'Error reading file: {e}')

        else:
            user_id = request.POST.get('user_id')
            target  = get_object_or_404(User, pk=user_id)

            if action == 'make_manager':
                target.groups.add(manager_grp)
                messages.success(request, f'{target.get_full_name() or target.username} added as PR Manager.')
            elif action == 'make_member':
                target.groups.add(member_grp)
                messages.success(request, f'{target.get_full_name() or target.username} added as PR Member.')
            elif action == 'make_both':
                target.groups.add(manager_grp, member_grp)
                messages.success(request, f'{target.get_full_name() or target.username} given both Manager and Member access.')
            elif action == 'remove':
                target.groups.remove(manager_grp, member_grp)
                messages.success(request, f'{target.get_full_name() or target.username} removed from PR Tracker.')
            return redirect('pr_tracker:user_master')

    search = request.GET.get('q', '').strip()
    all_users = User.objects.filter(is_active=True).prefetch_related('groups').order_by('first_name', 'username')
    if search:
        all_users = all_users.filter(
            models.Q(first_name__icontains=search) |
            models.Q(last_name__icontains=search)  |
            models.Q(username__icontains=search)   |
            models.Q(email__icontains=search)
        )

    manager_ids = set(manager_grp.user_set.values_list('id', flat=True))
    member_ids  = set(member_grp.user_set.values_list('id', flat=True))

    users_data = []
    for u in all_users:
        is_mgr, is_mem = u.pk in manager_ids, u.pk in member_ids
        if is_mgr and is_mem:
            role = 'both'
        elif is_mgr:
            role = 'manager'
        elif is_mem:
            role = 'member'
        else:
            role = 'none'
        users_data.append({'user': u, 'pr_role': role})

    return render(request, 'pr_tracker/user_master.html', {
        'users_data':      users_data,
        'search':          search,
        'upload_results':  upload_results,
        'pr_role':         _pr_role(request.user),
    })
