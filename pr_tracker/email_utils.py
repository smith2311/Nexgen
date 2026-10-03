import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

logger = logging.getLogger(__name__)

_BRAND  = '#0B1F3A'   # navy — matches Excel template header color
_ACCENT = '#0F7B6C'   # teal — used for positive/closed notices

DEFAULT_NOTIFY_EMAIL = 'devang.modi@merillife.com'

# ── Attachment policy ────────────────────────────────────────────────────────
#
# Documents uploaded on the Assign screen travel with the notification, but a
# mail server will refuse a message that is too big — and it refuses it after
# the view has already reported success, so nobody finds out. Zoho's limit is
# around 20 MB for the whole encoded message; base64 inflates a file by about
# a third, so a 10 MB budget of actual bytes is the largest that is safely
# under it.
#
# The rule is deliberately all-or-nothing on the total rather than "attach
# what fits": a mail carrying two of five quotations, with no way to tell
# which two, is worse than one carrying none and saying so. Either every
# document is attached or none is, and the list of links is in the body
# either way — so the documents are always reachable, whichever branch ran.
MAX_ATTACH_TOTAL_BYTES = 10 * 1024 * 1024


def _site_url():
    return getattr(settings, 'SITE_URL', 'http://nexgen.e-meril.in')


def _documents_block(documents, attached):
    """
    The "Documents" section of an email body — shown ONLY when the files
    could not be attached.

    When they are attached there is nothing useful to add: every mail client
    already draws its own attachment strip with the names, the sizes and a
    download for each. Listing them again above it said the same thing twice
    on one screen, which is what this used to do.

    When the set was too big to attach, the opposite is true — there is no
    attachment strip, and these links are the only way to reach the files.
    So the block earns its place exactly when the paperclip is missing, and
    it says why it is missing.
    """
    if not documents or attached:
        return ''

    site = _site_url()
    items = ''.join(
        f'''
              <tr>
                <td style="padding:6px 0;">
                  <a href="{escape(site)}/pr/document/{doc.pk}/"
                     style="color:{_BRAND};font-size:13px;font-weight:600;text-decoration:none;">
                    &#128206; {escape(doc.original_name)}
                  </a>
                  <span style="color:#9CA3AF;font-size:11.5px;">&nbsp;({escape(doc.size_label)})</span>
                </td>
              </tr>'''
        for doc in documents
    )

    note = ('These are too large to attach, so they are linked below. You '
            'will need to be signed in to NexGen to open them.')

    return f'''
            <tr>
              <td colspan="2" style="padding-top:20px;">
                <div style="border-top:1px solid #E5E7EB;padding-top:14px;">
                  <div style="color:#111827;font-size:13px;font-weight:700;margin-bottom:2px;">
                    Documents ({len(documents)})
                  </div>
                  <div style="color:#6B7280;font-size:11.5px;margin-bottom:6px;">{escape(note)}</div>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{items}
                  </table>
                </div>
              </td>
            </tr>'''


def _html_shell(heading, subheading, rows, cta_url=None, cta_label=None,
                accent=_BRAND, documents=None, documents_attached=False):
    """Branded card-style HTML wrapper shared by every PR Tracker email."""
    row_html = ''.join(
        f'''
            <tr>
              <td style="padding:7px 0;color:#6B7280;font-size:13px;width:170px;vertical-align:top;">{escape(str(label))}</td>
              <td style="padding:7px 0;color:#111827;font-size:13px;font-weight:600;">{escape(str(value) if value not in (None, '') else '—')}</td>
            </tr>'''
        for label, value in rows
    )
    cta_html = ''
    if cta_url:
        cta_html = f'''
            <tr>
              <td colspan="2" style="padding-top:22px;">
                <a href="{escape(cta_url)}" style="background:{accent};color:#ffffff;text-decoration:none;
                   padding:11px 22px;border-radius:6px;font-size:13px;font-weight:600;display:inline-block;">
                  {escape(cta_label or 'View Details')}
                </a>
              </td>
            </tr>'''

    return f'''<html>
  <body style="margin:0;padding:0;background:#F3F4F6;font-family:'Segoe UI',Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F3F4F6;padding:28px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="540" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.10);">
            <tr>
              <td style="background:{_BRAND};padding:20px 28px;">
                <span style="color:#ffffff;font-size:16px;font-weight:700;letter-spacing:.3px;">NexGen &mdash; PR Tracker</span>
              </td>
            </tr>
            <tr>
              <td style="padding:28px;">
                <h2 style="margin:0 0 6px;color:#111827;font-size:18px;">{escape(heading)}</h2>
                <p style="margin:0 0 18px;color:#6B7280;font-size:13px;line-height:1.5;">{escape(subheading)}</p>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #E5E7EB;padding-top:14px;">
                  {row_html}
                  {_documents_block(documents or [], documents_attached)}
                  {cta_html}
                </table>
              </td>
            </tr>
            <tr>
              <td style="background:#F9FAFB;padding:14px 28px;border-top:1px solid #E5E7EB;">
                <span style="color:#9CA3AF;font-size:11px;">This is an automated notification from NexGen PR Tracker. Please do not reply to this email.</span>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>'''


def _attachable(documents):
    """
    ``(documents, attach)`` — the list to show, and whether to attach it.

    See MAX_ATTACH_TOTAL_BYTES. All or nothing on the total, and a document
    whose size is unknown (0 in the row, because the upload could not measure
    it) is treated as too big rather than assumed free: guessing low here is
    how a message silently exceeds the server's limit.
    """
    documents = list(documents or [])
    if not documents:
        return [], False
    total = sum(doc.size_bytes or 0 for doc in documents)
    unknown = any(not doc.size_bytes for doc in documents)
    return documents, (not unknown and total <= MAX_ATTACH_TOTAL_BYTES)


def _send(subject, html_body, to_list, plain_fallback=None, attachments=None):
    """
    Send one message, attaching the given PRDocument rows if asked.

    Returns True only if the message actually went out. The caller uses that
    to tell the user the truth: this used to swallow every failure and return
    nothing, so a refused login, a bad host or a message over the server's
    size limit all ended with the screen saying "Notifications sent" and
    nobody ever finding out. A mail that did not send is now a warning on the
    screen, not just a line in a log file no one reads.

    A file that cannot be read is still skipped with a log line rather than
    failing the send: the body carries a download link for every document, so
    a missing file on disk costs the recipient a click, not the notification.
    """
    if not to_list:
        return False
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_fallback or 'Please view this email in an HTML-capable mail client.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to_list,
        )
        msg.attach_alternative(html_body, 'text/html')

        for doc in (attachments or []):
            try:
                with doc.file.open('rb') as handle:
                    msg.attach(doc.original_name, handle.read(),
                               doc.content_type or 'application/octet-stream')
            except Exception as exc:
                logger.error('PR Tracker could not attach document %s: %s',
                             doc.pk, exc)

        msg.send(fail_silently=False)
        return True
    except Exception as exc:
        logger.error('PR Tracker email failed → %s: %s', to_list, exc)
        return False


def _person_name(user, fallback='—'):
    if not user:
        return fallback
    return user.get_full_name() or user.username


def send_pr_assigned(pr, documents=None):
    """
    Fire when a PR is assigned to a member — the person who will process it.

    This is the only notification that carries the uploaded documents. The
    person processing the PR is who the paperwork was uploaded for; the
    responsible person is told the PR exists and can open it on the site.

    ``documents`` is the PR's uploaded supporting files. Passed in rather than
    read off the relation so a caller that has just created them in the same
    request does not depend on the reverse manager being re-queried, and so a
    test can hand in exactly what it means to assert about.

    Returns True if the mail went out — False if this person has no email
    address on file, or the send failed.
    """
    if not pr.assigned_person or not pr.assigned_person.email:
        return False
    if documents is None:
        documents = list(pr.documents.all())
    documents, attach = _attachable(documents)

    url = f'{_site_url()}/pr/my/{pr.pk}/'
    member_name = _person_name(pr.assigned_person)

    html = _html_shell(
        heading='New Purchase Request Assigned',
        subheading=f'Dear {member_name}, a new Purchase Request has been assigned to you for processing.',
        rows=[
            ('PR Number', pr.pr_number),
            ('Responsible', _person_name(pr.responsible_person)),
            ('Assigned By', _person_name(pr.assigned_by)),
            ('Assigned On', pr.assigned_at.strftime('%d %b %Y, %I:%M %p')),
        ],
        cta_url=url,
        cta_label='Fill PR Details',
        documents=documents,
        documents_attached=attach,
    )
    return _send(
        subject=f'[PR Tracker] New PR Assigned: {pr.pr_number}',
        html_body=html,
        to_list=[pr.assigned_person.email],
        attachments=documents if attach else None,
    )


def send_pr_responsible(pr):
    """
    Fire when someone is made the Responsible Person for a PR.

    A separate mail from send_pr_assigned, not a second address on it. The two
    people have different jobs — one processes the PR, the other owns it — so
    they need different wording and a different call to action: "Fill PR
    Details" is not what the responsible person is being asked to do, and a
    single message addressed to both would have to say nothing useful to
    either.

    The uploaded documents deliberately do NOT travel with this mail. They
    are quotations and specifications meant for the person who will process
    the PR; the responsible person owns the PR rather than working it, and
    sending the same set of attachments twice puts commercial paperwork in a
    second inbox for no one's benefit. They can still open the PR on the site
    and read every document there — the call to action below goes to exactly
    that screen.

    Returns True if the mail went out — False if this person has no email
    address on file, or the send failed.
    """
    if not pr.responsible_person or not pr.responsible_person.email:
        return False

    url = f'{_site_url()}/pr/{pr.pk}/'
    resp_name = _person_name(pr.responsible_person)

    html = _html_shell(
        heading='You Are the Responsible Person',
        subheading=(
            f'Dear {resp_name}, you have been set as the Responsible Person '
            f'for the following Purchase Request. You will be notified as it '
            f'progresses.'
        ),
        rows=[
            ('PR Number', pr.pr_number),
            ('Assigned To', _person_name(pr.assigned_person, 'Not yet assigned')),
            ('Assigned By', _person_name(pr.assigned_by)),
            ('Assigned On', pr.assigned_at.strftime('%d %b %Y, %I:%M %p')),
        ],
        cta_url=url,
        cta_label='View PR Details',
    )
    return _send(
        subject=f'[PR Tracker] You are responsible for PR: {pr.pr_number}',
        html_body=html,
        to_list=[pr.responsible_person.email],
    )


def send_pr_added_notification(added_by, is_bulk, created=0, updated=0, pr_number=None):
    """Notify the default PR Tracker contact whenever a manager adds a PR — single entry or bulk Excel upload."""
    to_list = [DEFAULT_NOTIFY_EMAIL]
    by_name = added_by.get_full_name() or added_by.username

    if is_bulk:
        html = _html_shell(
            heading='Bulk PR Upload',
            subheading=f'{by_name} has uploaded a bulk PR assignment file.',
            rows=[
                ('Uploaded By', by_name),
                ('PRs Created', created),
                ('PRs Updated', updated),
            ],
            cta_url=f'{_site_url()}/pr/dashboard/',
            cta_label='View Dashboard',
        )
        subject = f'[PR Tracker] Bulk PR Upload by {by_name}'
    else:
        action = 'added' if created else 'updated'
        html = _html_shell(
            heading=f'PR {action.capitalize()}',
            subheading=f'{by_name} has {action} the following PR.',
            rows=[
                ('PR Number', pr_number),
                (f'{action.capitalize()} By', by_name),
            ],
            cta_url=f'{_site_url()}/pr/dashboard/',
            cta_label='View Dashboard',
        )
        subject = f'[PR Tracker] PR {action.capitalize()}: {pr_number} by {by_name}'

    _send(subject=subject, html_body=html, to_list=to_list)


def send_pr_updated(pr, updated_by):
    """Notify responsible person when member updates PR details."""
    if not pr.responsible_person or not pr.responsible_person.email:
        return
    url = f'{_site_url()}/pr/{pr.pk}/'
    updater = updated_by.get_full_name() or updated_by.username
    resp_name = pr.responsible_person.get_full_name() or pr.responsible_person.username

    html = _html_shell(
        heading='PR Details Updated',
        subheading=f'Dear {resp_name}, PR details have been updated by {updater}.',
        rows=[
            ('PR Number', pr.pr_number),
            ('Status', pr.get_status_display()),
            ('Updated By', updater),
        ],
        cta_url=url,
        cta_label='View PR Details',
    )
    _send(
        subject=f'[PR Tracker] PR Updated: {pr.pr_number}',
        html_body=html,
        to_list=[pr.responsible_person.email],
    )


def send_pr_filled(pr, filled_by):
    """Notify the responsible person once the assigned member has filled in all PR detail fields."""
    if not pr.responsible_person or not pr.responsible_person.email:
        return
    url = f'{_site_url()}/pr/{pr.pk}/'
    filler_name = filled_by.get_full_name() or filled_by.username
    member_name = (
        pr.assigned_person.get_full_name() or pr.assigned_person.username
        if pr.assigned_person else filler_name
    )
    resp_name = pr.responsible_person.get_full_name() or pr.responsible_person.username

    html = _html_shell(
        heading='PR Fully Filled',
        subheading=f'Dear {resp_name}, {member_name} has filled in all the required details for this PR.',
        rows=[
            ('PR Number', pr.pr_number),
            ('Status', pr.get_status_display()),
            ('Filled By', filler_name),
        ],
        cta_url=url,
        cta_label='View Full PR Details',
        accent=_ACCENT,
    )
    _send(
        subject=f'[PR Tracker] PR Fully Filled: {pr.pr_number}',
        html_body=html,
        to_list=[pr.responsible_person.email],
    )


def send_pr_closed(pr, closed_by):
    """Notify all PR-Managers (and assigned_by/responsible_person) when a PR is closed."""
    from django.contrib.auth.models import Group

    recipients = set()

    # Always notify the manager who assigned it
    if pr.assigned_by and pr.assigned_by.email:
        recipients.add(pr.assigned_by.email)
    # And the responsible person
    if pr.responsible_person and pr.responsible_person.email:
        recipients.add(pr.responsible_person.email)
    # Notify all active PR-Manager group members
    try:
        mgr_grp = Group.objects.get(name='PR-Manager')
        for u in mgr_grp.user_set.filter(is_active=True):
            if u.email:
                recipients.add(u.email)
    except Group.DoesNotExist:
        pass

    if not recipients:
        return

    closer      = closed_by.get_full_name() or closed_by.username
    member_name = (
        pr.assigned_person.get_full_name() or pr.assigned_person.username
        if pr.assigned_person else closer
    )
    url = f'{_site_url()}/pr/{pr.pk}/'

    html = _html_shell(
        heading='PR Closed',
        subheading=f'{member_name} has completed and closed the following PR.',
        rows=[
            ('PR Number', pr.pr_number),
            ('PO Number', pr.po_number),
            ('Amount', pr.amount),
            ('Closed By', closer),
            ('Fill %', f'{pr.fill_percent}%'),
        ],
        cta_url=url,
        cta_label='View Full PR Details',
        accent=_ACCENT,
    )
    _send(
        subject=f'[PR Tracker] PR Closed: {pr.pr_number} — by {closer}',
        html_body=html,
        to_list=list(recipients),
    )
