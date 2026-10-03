"""
notification_service.py
─────────────────────────────────────────────────────────────────────────────
Handles in-app notifications + email alerts for the product scanning app.

Events covered
  • notify_batch_submitted   – salesperson submits / force-submit
  • notify_session_terminated – warehouse manager terminates a session

Recipients
  • Warehouse managers : PS Warehouse group + superusers
                         + WAREHOUSE_MANAGER_EMAILS hardcoded list
  • Submitter          : the user who triggered complete/force-complete
                         (gets a separate confirmation email)
"""

import logging
import threading

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

logger = logging.getLogger(__name__)

# ── Hardcoded warehouse manager recipients ──────────────────────────────────
# Add more addresses here as needed; they receive ALL session event emails.
WAREHOUSE_MANAGER_EMAILS = [
    "manas.jain@merillife.com",
]

_TEST_EMAIL = getattr(settings, "TEST_NOTIFICATION_EMAIL", "manas.jain@merillife.com")


def _get_warehouse_recipients():
    """Return all active PS Warehouse users + superusers (Django user objects)."""
    wh_users = User.objects.filter(groups__name="PS Warehouse", is_active=True)
    admin_users = User.objects.filter(is_active=True, is_superuser=True)
    return list((wh_users | admin_users).distinct())


def _user_wants_inapp(user):
    """Return True if the user has browser/in-app notifications enabled."""
    try:
        return user.profile.notif_browser
    except Exception:
        return True


def _user_wants_shipment_notif(user):
    """Return True if the user wants shipment/scan-related in-app notifications."""
    try:
        return user.profile.notif_browser and user.profile.notif_shipment
    except Exception:
        return True


def _user_wants_email(user):
    """Return True if the user has email notifications enabled."""
    try:
        return user.profile.notif_email
    except Exception:
        return True


def _warehouse_emails():
    """
    Compile the final deduplicated list of warehouse manager email addresses,
    respecting each user's notif_email preference.
    Hardcoded addresses and TEST_NOTIFICATION_EMAIL always receive emails.
    """
    users = _get_warehouse_recipients()
    emails = {u.email for u in users if u.email and _user_wants_email(u)}
    for em in WAREHOUSE_MANAGER_EMAILS:
        emails.add(em)
    if _TEST_EMAIL:
        emails.add(_TEST_EMAIL)
    return list(emails)


# ── Email builders ──────────────────────────────────────────────────────────


def _build_submission_email(session, product_count):
    """
    Email to warehouse managers: 'New batch submitted – please review.'
    Returns (subject, text_body, html_body).
    """
    salesperson = session.scanned_by
    sp_name = (
        (f"{salesperson.first_name} {salesperson.last_name}".strip() or salesperson.username)
        if salesperson
        else "Unknown"
    )

    batch = session.batch
    hospital = session.hospital
    session_url = f"{settings.SITE_URL}/product_scanning/session/{session.session_key}/"
    completed_at = session.completed_at or timezone.now()

    is_h2h = session.session_type == "transfer"
    to_hospital = session.to_hospital
    type_label = "H2H Transfer" if is_h2h else "Stock Audit"
    dest_row = f"<tr><td>Destination</td><td>{to_hospital.name if to_hospital else 'N/A'}</td></tr>" if is_h2h else ""

    subject = (
        f"[NexGen] {'H2H Transfer' if is_h2h else 'New Batch'} Submitted — "
        f"{batch.batch_id if batch else 'N/A'} | {hospital.name if hospital else 'N/A'}"
    )

    text_body = (
        f"A {type_label} batch has been submitted and is awaiting your review.\n\n"
        f"Batch ID    : {batch.batch_id if batch else 'N/A'}\n"
        f"Session Type: {type_label}\n"
        f"Hospital    : {hospital.name if hospital else 'N/A'}\n"
        + (f"Destination : {to_hospital.name if to_hospital else 'N/A'}\n" if is_h2h else "")
        + f"Salesperson : {sp_name}\n"
        f"Products    : {product_count}\n"
        f"Submitted   : {completed_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"Review here: {session_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:600px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#0076c8,#00b5e2);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.85}}
.body{{padding:28px 32px}}
.tbl{{width:100%;border-collapse:collapse;margin:18px 0}}
.tbl td{{padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.tbl td:first-child{{font-weight:600;color:#555;width:38%;background:#fafafa}}
.badge{{display:inline-block;background:#dcfce7;color:#166534;padding:4px 12px;border-radius:20px;font-size:.85em;font-weight:600}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:linear-gradient(135deg,#0076c8,#00b5e2);color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>📦 {'H2H Transfer' if is_h2h else 'New Batch'} Submitted</h1>
    <p>A scanning session has been completed and is pending your review.</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.95em;">Hello,<br><br>
    A salesperson has submitted a product scanning batch. Please review and upload the confirmation document to close it.</p>
    <table class="tbl">
      <tr><td>Batch ID</td><td><strong>{batch.batch_id if batch else 'N/A'}</strong></td></tr>
      <tr><td>Session Type</td><td>{type_label}</td></tr>
      <tr><td>Hospital</td><td>{hospital.name if hospital else 'N/A'}</td></tr>
      {dest_row}
      <tr><td>Salesperson</td><td>{sp_name}</td></tr>
      <tr><td>Products Scanned</td><td><span class="badge">{product_count} items</span></td></tr>
      <tr><td>Submitted At</td><td>{completed_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
      <tr><td>Status</td><td><span style="background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:12px;font-size:.85em;font-weight:600;">Awaiting Confirmation</span></td></tr>
    </table>
    <div class="cta"><a href="{session_url}">📋 Review Session &amp; Upload Document</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    return subject, text_body, html_body


def _build_confirmation_email(session, product_count, submitter_name):
    """
    Confirmation email to the salesperson who submitted the session.
    Returns (subject, text_body, html_body).
    """
    batch = session.batch
    hospital = session.hospital
    session_url = f"{settings.SITE_URL}/product_scanning/session/{session.session_key}/"
    completed_at = session.completed_at or timezone.now()
    is_h2h = session.session_type == "transfer"
    type_label = "H2H Transfer" if is_h2h else "Stock Audit"

    subject = (
        f"[NexGen] Submission Confirmed — "
        f"{batch.batch_id if batch else 'N/A'} | {hospital.name if hospital else 'N/A'}"
    )

    text_body = (
        f"Hi {submitter_name},\n\n"
        f"Your {type_label} batch has been received and is now awaiting warehouse confirmation.\n\n"
        f"Batch ID : {batch.batch_id if batch else 'N/A'}\n"
        f"Hospital : {hospital.name if hospital else 'N/A'}\n"
        f"Products : {product_count}\n"
        f"Submitted: {completed_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"You can view your session here: {session_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:600px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.85}}
.body{{padding:28px 32px}}
.tbl{{width:100%;border-collapse:collapse;margin:18px 0}}
.tbl td{{padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.tbl td:first-child{{font-weight:600;color:#555;width:38%;background:#fafafa}}
.badge{{display:inline-block;background:#dcfce7;color:#166534;padding:4px 12px;border-radius:20px;font-size:.85em;font-weight:600}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>✅ Submission Confirmed</h1>
    <p>Your batch has been successfully submitted and is awaiting confirmation.</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.95em;">Hi {submitter_name},<br><br>
    Your <strong>{type_label}</strong> batch has been received by the warehouse team. You will be notified once it is reviewed and closed.</p>
    <table class="tbl">
      <tr><td>Batch ID</td><td><strong>{batch.batch_id if batch else 'N/A'}</strong></td></tr>
      <tr><td>Session Type</td><td>{type_label}</td></tr>
      <tr><td>Hospital</td><td>{hospital.name if hospital else 'N/A'}</td></tr>
      <tr><td>Products Scanned</td><td><span class="badge">{product_count} items</span></td></tr>
      <tr><td>Submitted At</td><td>{completed_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
      <tr><td>Status</td><td><span style="background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:12px;font-size:.85em;font-weight:600;">Awaiting Confirmation</span></td></tr>
    </table>
    <div class="cta"><a href="{session_url}">View My Session</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    return subject, text_body, html_body


def _build_termination_email(session, terminated_by_name, reason):
    """
    Email to warehouse managers when a session is terminated/cancelled.
    Returns (subject, text_body, html_body).
    """
    batch = session.batch
    hospital = session.hospital
    session_url = f"{settings.SITE_URL}/product_scanning/session/{session.session_key}/"
    terminated_at = session.completed_at or timezone.now()
    sp = session.scanned_by
    sp_name = (f"{sp.first_name} {sp.last_name}".strip() or sp.username) if sp else "Unknown"

    subject = (
        f"[NexGen] Session Terminated — "
        f"{batch.batch_id if batch else 'N/A'} | {hospital.name if hospital else 'N/A'}"
    )

    text_body = (
        f"A scanning session has been terminated.\n\n"
        f"Batch ID      : {batch.batch_id if batch else 'N/A'}\n"
        f"Hospital      : {hospital.name if hospital else 'N/A'}\n"
        f"Salesperson   : {sp_name}\n"
        f"Terminated By : {terminated_by_name}\n"
        f"Reason        : {reason}\n"
        f"Terminated At : {terminated_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"Session link: {session_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:600px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.85}}
.body{{padding:28px 32px}}
.tbl{{width:100%;border-collapse:collapse;margin:18px 0}}
.tbl td{{padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.tbl td:first-child{{font-weight:600;color:#555;width:38%;background:#fafafa}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:#6b7280;color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>🚫 Session Terminated</h1>
    <p>A scanning session has been cancelled and will not be processed.</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.95em;">A scanning session has been terminated. No further action is needed for this session.</p>
    <table class="tbl">
      <tr><td>Batch ID</td><td><strong>{batch.batch_id if batch else 'N/A'}</strong></td></tr>
      <tr><td>Hospital</td><td>{hospital.name if hospital else 'N/A'}</td></tr>
      <tr><td>Salesperson</td><td>{sp_name}</td></tr>
      <tr><td>Terminated By</td><td>{terminated_by_name}</td></tr>
      <tr><td>Reason</td><td style="color:#dc2626;">{reason}</td></tr>
      <tr><td>Terminated At</td><td>{terminated_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
      <tr><td>Status</td><td><span style="background:#fee2e2;color:#991b1b;padding:3px 10px;border-radius:12px;font-size:.85em;font-weight:600;">Cancelled</span></td></tr>
    </table>
    <div class="cta"><a href="{session_url}">View Session</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    return subject, text_body, html_body


# ── Async sender ────────────────────────────────────────────────────────────


def _do_send(emails_to_send):
    """Send emails synchronously. Used directly or inside a thread."""
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@merillife.com")
    for subject, text_body, html_body, recipients in emails_to_send:
        for email in recipients:
            try:
                msg = EmailMultiAlternatives(subject, text_body, from_email, [email])
                msg.attach_alternative(html_body, "text/html")
                msg.send()
                logger.info(f"Email sent to {email}: {subject}")
            except Exception as exc:
                logger.error(f"Failed to send email to {email}: {exc}")


def _send_emails_async(emails_to_send):
    """
    emails_to_send: list of (subject, text_body, html_body, [recipient_email])
    All sent in a single background daemon thread (suitable for web requests).
    Do NOT use from management commands — the process exits before the thread finishes.
    Use _do_send() directly for management commands.
    """
    t = threading.Thread(target=_do_send, args=(emails_to_send,), daemon=True)
    t.start()


# ── Public notification functions ───────────────────────────────────────────


def notify_batch_submitted(session, submitted_by=None):
    """
    Main entry point — call after a session is marked 'completed'.

    • Creates in-app Notification records for every PS Warehouse manager.
    • Emails warehouse managers: 'please review & upload document'.
    • Emails the submitter: 'submission confirmed'.

    submitted_by: the User who triggered the submit (falls back to session.scanned_by).

    Fires at most once per session: the submission_notified_at marker is claimed atomically,
    so duplicate submits (double-click, held Enter, client retry) are dropped here even if a
    caller forgets to guard the status transition.
    """
    from .models import Notification, ScanningSession  # local import to avoid circular

    claimed = ScanningSession.objects.filter(pk=session.pk, submission_notified_at__isnull=True).update(
        submission_notified_at=timezone.now()
    )
    if not claimed:
        logger.info(
            "notify_batch_submitted: already notified for session %s — skipping duplicate",
            session.session_key,
        )
        return 0, []

    product_count = session.scanned_products.count()

    salesperson = session.scanned_by
    sp_name = (
        (f"{salesperson.first_name} {salesperson.last_name}".strip() or salesperson.username)
        if salesperson
        else "Unknown"
    )

    batch = session.batch
    hospital = session.hospital

    title = f"New batch submitted — {batch.batch_id if batch else 'N/A'}"
    message = (
        f"{sp_name} submitted a batch of {product_count} product(s) "
        f"for {hospital.name if hospital else 'unknown hospital'}. "
        f"Please review and upload the confirmation document."
    )

    # ── In-app notifications (warehouse managers only) ──────────────────
    wh_users = _get_warehouse_recipients()
    for user in wh_users:
        if _user_wants_shipment_notif(user):
            Notification.objects.create(
                recipient=user,
                category=Notification.CATEGORY_BATCH_SUBMITTED,
                title=title,
                message=message,
                session=session,
            )
            logger.info(f"In-app notification created for {user.username}")

    # ── Email: warehouse managers ───────────────────────────────────────
    wh_emails = _warehouse_emails()
    subj_wh, txt_wh, html_wh = _build_submission_email(session, product_count)

    # ── Email: submitter confirmation ───────────────────────────────────
    submitter = submitted_by or salesperson
    submitter_name = (
        (f"{submitter.first_name} {submitter.last_name}".strip() or submitter.username) if submitter else None
    )
    submitter_email = submitter.email if (submitter and submitter.email) else None

    emails_to_send = [(subj_wh, txt_wh, html_wh, wh_emails)]
    if submitter_email and submitter_email not in wh_emails and _user_wants_email(submitter or salesperson):
        subj_c, txt_c, html_c = _build_confirmation_email(session, product_count, submitter_name or sp_name)
        emails_to_send.append((subj_c, txt_c, html_c, [submitter_email]))

    _send_emails_async(emails_to_send)

    return len(wh_users), wh_emails


def notify_session_terminated(session, terminated_by, reason):
    """
    Call after a session is marked 'cancelled' (terminate action).

    • Creates in-app Notification records for warehouse managers.
    • Emails warehouse managers about the termination.
    """
    from .models import Notification

    actor_name = f"{terminated_by.first_name} {terminated_by.last_name}".strip() or terminated_by.username
    batch = session.batch
    hospital = session.hospital

    title = f"Session terminated — {batch.batch_id if batch else 'N/A'}"
    message = (
        f"Session for {hospital.name if hospital else 'unknown hospital'} "
        f"was terminated by {actor_name}. Reason: {reason}"
    )

    wh_users = _get_warehouse_recipients()
    for user in wh_users:
        if _user_wants_shipment_notif(user):
            Notification.objects.create(
                recipient=user,
                category=Notification.CATEGORY_BATCH_SUBMITTED,
                title=title,
                message=message,
                session=session,
            )

    wh_emails = _warehouse_emails()
    subj, txt, html = _build_termination_email(session, actor_name, reason)
    _send_emails_async([(subj, txt, html, wh_emails)])

    return len(wh_users), wh_emails


def notify_session_closed(session, closed_by):
    """
    Call after a session is moved to 'closed' (upload & close action).

    • In-app notification to the salesperson who scanned it.
    • Email to the salesperson: 'your session has been confirmed & closed'.
    • Email to warehouse managers: 'session closed' (audit trail).
    """
    from .models import Notification

    batch = session.batch
    hospital = session.hospital
    salesperson = session.scanned_by

    actor_name = f"{closed_by.first_name} {closed_by.last_name}".strip() or closed_by.username
    sp_name = (
        (f"{salesperson.first_name} {salesperson.last_name}".strip() or salesperson.username)
        if salesperson
        else "Unknown"
    )

    product_count = session.scanned_products.count()
    session_url = f"{settings.SITE_URL}/product_scanning/session/{session.session_key}/"
    closed_at = session.completed_at or timezone.now()

    batch_id = batch.batch_id if batch else "N/A"
    hosp_name = hospital.name if hospital else "N/A"

    # ── In-app notification → salesperson ─────────────────────────────────
    if salesperson and _user_wants_shipment_notif(salesperson):
        Notification.objects.create(
            recipient=salesperson,
            category=Notification.CATEGORY_BATCH_SUBMITTED,
            title=f"Session closed — {batch_id}",
            message=(
                f"Your batch for {hosp_name} has been reviewed and closed by "
                f"{actor_name}. No further action is needed."
            ),
            session=session,
        )

    # ── In-app notification → warehouse managers ───────────────────────────
    wh_users = _get_warehouse_recipients()
    for user in wh_users:
        if user != salesperson and _user_wants_shipment_notif(user):
            Notification.objects.create(
                recipient=user,
                category=Notification.CATEGORY_BATCH_SUBMITTED,
                title=f"Session closed — {batch_id}",
                message=f"{sp_name}'s batch for {hosp_name} was closed by {actor_name}.",
                session=session,
            )

    # ── Email → salesperson ────────────────────────────────────────────────
    sp_email = salesperson.email if (salesperson and salesperson.email and _user_wants_email(salesperson)) else None
    wh_emails = _warehouse_emails()

    subj_sp = f"[NexGen] Session Closed — {batch_id} | {hosp_name}"
    txt_sp = (
        f"Hi {sp_name},\n\n"
        f"Your scanning session has been reviewed and officially closed.\n\n"
        f"Batch ID : {batch_id}\n"
        f"Hospital : {hosp_name}\n"
        f"Products : {product_count}\n"
        f"Closed By: {actor_name}\n"
        f"Closed At: {closed_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"View your session: {session_url}\n\n— NexGen Automation"
    )
    html_sp = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:600px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#0ea5e9,#0284c7);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.85}}
.body{{padding:28px 32px}}
.tbl{{width:100%;border-collapse:collapse;margin:18px 0}}
.tbl td{{padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.tbl td:first-child{{font-weight:600;color:#555;width:38%;background:#fafafa}}
.badge{{display:inline-block;background:#dbeafe;color:#1e40af;padding:4px 12px;border-radius:20px;font-size:.85em;font-weight:600}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:linear-gradient(135deg,#0ea5e9,#0284c7);color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>🔒 Session Closed</h1>
    <p>Your scanning batch has been reviewed and officially closed.</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.95em;">Hi {sp_name},<br><br>
    Your product scanning session has been confirmed and closed by the warehouse team.</p>
    <table class="tbl">
      <tr><td>Batch ID</td><td><strong>{batch_id}</strong></td></tr>
      <tr><td>Hospital</td><td>{hosp_name}</td></tr>
      <tr><td>Products Scanned</td><td><span class="badge">{product_count} items</span></td></tr>
      <tr><td>Closed By</td><td>{actor_name}</td></tr>
      <tr><td>Closed At</td><td>{closed_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
      <tr><td>Status</td><td><span style="background:#dcfce7;color:#166534;padding:3px 10px;border-radius:12px;font-size:.85em;font-weight:600;">Closed</span></td></tr>
    </table>
    <div class="cta"><a href="{session_url}">View Session</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    # ── Email → warehouse managers ─────────────────────────────────────────
    subj_wh = f"[NexGen] Session Closed — {batch_id} | {hosp_name}"
    txt_wh = (
        f"Session closed by {actor_name}.\n\n"
        f"Batch ID   : {batch_id}\n"
        f"Hospital   : {hosp_name}\n"
        f"Salesperson: {sp_name}\n"
        f"Products   : {product_count}\n"
        f"Closed At  : {closed_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"Session: {session_url}\n\n— NexGen Automation"
    )
    html_wh = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:600px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#64748b,#475569);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.85}}
.body{{padding:28px 32px}}
.tbl{{width:100%;border-collapse:collapse;margin:18px 0}}
.tbl td{{padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.tbl td:first-child{{font-weight:600;color:#555;width:38%;background:#fafafa}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:#475569;color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>✅ Session Closed</h1>
    <p>A scanning session has been confirmed and closed.</p>
  </div>
  <div class="body">
    <table class="tbl">
      <tr><td>Batch ID</td><td><strong>{batch_id}</strong></td></tr>
      <tr><td>Hospital</td><td>{hosp_name}</td></tr>
      <tr><td>Salesperson</td><td>{sp_name}</td></tr>
      <tr><td>Products Scanned</td><td>{product_count} items</td></tr>
      <tr><td>Closed By</td><td>{actor_name}</td></tr>
      <tr><td>Closed At</td><td>{closed_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
      <tr><td>Status</td><td><span style="background:#dcfce7;color:#166534;padding:3px 10px;border-radius:12px;font-size:.85em;font-weight:600;">Closed</span></td></tr>
    </table>
    <div class="cta"><a href="{session_url}">View Session</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    emails_to_send = [(subj_wh, txt_wh, html_wh, wh_emails)]
    if sp_email and sp_email not in wh_emails:
        emails_to_send.append((subj_sp, txt_sp, html_sp, [sp_email]))

    _send_emails_async(emails_to_send)

    return len(wh_users), wh_emails


def notify_expired_acknowledged(product_ids, acknowledged_by, count):
    """
    Send email to all warehouse managers when expired products are acknowledged.
    """
    from .models import ScannedProduct

    actor = acknowledged_by
    actor_name = (f"{actor.first_name} {actor.last_name}".strip() or actor.username) if actor else "Unknown"

    # Gather product details for the email
    products = ScannedProduct.objects.filter(pk__in=product_ids).select_related(
        "scanning_session__hospital",
        "scanning_session__batch",
    )

    product_rows = ""
    for p in products:
        session = p.scanning_session
        hospital_name = session.hospital.name if session.hospital else "N/A"
        batch_id = session.batch.batch_id if session.batch else "N/A"
        days_exp = (timezone.now().date() - p.expiry_date).days if p.expiry_date else 0
        product_rows += f"""
      <tr>
        <td>{p.serial_number or 'N/A'}</td>
        <td>{p.product_name or 'N/A'}</td>
        <td>{p.batch_number or 'N/A'}</td>
        <td>{hospital_name}</td>
        <td style="color:#dc2626;font-weight:600;">{p.expiry_date or 'N/A'}</td>
        <td style="color:#dc2626;font-weight:700;">{days_exp} days</td>
        <td>{batch_id}</td>
      </tr>"""

    now_str = timezone.now().strftime("%d %b %Y, %I:%M %p")
    dashboard_url = f"{settings.SITE_URL}/product_scanning/dashboard/"

    subject = f"[NexGen] {count} Expired Product(s) Acknowledged by {actor_name}"

    text_body = (
        f"{actor_name} acknowledged {count} expired product(s) on {now_str}.\n\n"
        f"View dashboard: {dashboard_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:640px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#f59e0b,#d97706);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.85}}
.body{{padding:28px 32px}}
.info-tbl{{width:100%;border-collapse:collapse;margin:12px 0}}
.info-tbl td{{padding:8px 12px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.info-tbl td:first-child{{font-weight:600;color:#555;width:35%;background:#fafafa}}
.prod-tbl{{width:100%;border-collapse:collapse;margin:16px 0;font-size:.88em}}
.prod-tbl th{{background:#f8fafc;padding:10px 12px;text-align:left;font-weight:600;color:#475569;border-bottom:2px solid #e5e7eb;font-size:.82em;text-transform:uppercase;letter-spacing:.3px}}
.prod-tbl td{{padding:9px 12px;border-bottom:1px solid #f0f0f0}}
.prod-tbl tr:hover{{background:#fefce8}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:linear-gradient(135deg,#f59e0b,#d97706);color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>⚠️ Expired Products Acknowledged</h1>
    <p>{count} expired product(s) have been reviewed and acknowledged.</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.95em;">Hello,<br><br>
    The following expired product(s) have been acknowledged by <strong>{actor_name}</strong>.</p>
    <table class="info-tbl">
      <tr><td>Acknowledged By</td><td><strong>{actor_name}</strong></td></tr>
      <tr><td>Products</td><td><span style="background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:12px;font-size:.85em;font-weight:600;">{count} item(s)</span></td></tr>
      <tr><td>Date/Time</td><td>{now_str}</td></tr>
    </table>
    <table class="prod-tbl">
      <thead>
        <tr><th>Serial No.</th><th>Product</th><th>Lot/Batch</th><th>Hospital</th><th>Expiry</th><th>Expired</th><th>Session</th></tr>
      </thead>
      <tbody>
        {product_rows}
      </tbody>
    </table>
    <div class="cta"><a href="{dashboard_url}">📋 View Audit Dashboard</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    wh_emails = _warehouse_emails()
    _send_emails_async([(subject, text_body, html_body, wh_emails)])

    return wh_emails


# ─────────────────────────────────────────────────────────────────────────────
# MONTHLY 20th REMINDER — PENDING HOSPITAL VISITS
# ─────────────────────────────────────────────────────────────────────────────


def notify_pending_hospitals_reminder(sp_email, sp_name, year, month, pending_hospitals):
    """
    Send a reminder email on the 20th of each month to the salesperson and
    all warehouse managers, listing hospitals that have not yet been visited.

    Args:
        sp_email: Salesperson email address (from Hospital master)
        sp_name: Salesperson display name
        year: Year to check
        month: Month to check (1-12)
        pending_hospitals: List of Hospital objects not yet visited

    Returns the list of email addresses that were notified.
    """
    import json as _json

    from .models import MonthlyReminderLog

    month_label = f"{['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][month]} {year}"
    dashboard_url = getattr(settings, "SITE_URL", "https://nexgen.e-meril.in") + "/product_scanning/sp-dashboard/"
    pending_count = len(pending_hospitals)
    now_str = timezone.now().strftime("%d %b %Y, %I:%M %p")

    pending_rows = "".join(
        f"<tr><td>{h.name}</td><td>{h.sap_code or '-'}</td><td>{h.province or '-'}</td></tr>" for h in pending_hospitals
    )

    subject = f"[NexGen] ⚠️ Pending Hospital Visits Reminder — {sp_name} | {month_label}"

    text_body = (
        f"Reminder: {sp_name} has {pending_count} hospital(s) not yet visited this month ({month_label}).\n\n"
        + "\n".join(f"  • {h.name} ({h.sap_code or 'N/A'})" for h in pending_hospitals)
        + f"\n\nView dashboard: {dashboard_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:620px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#f59e0b,#ef4444);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.9}}
.body{{padding:28px 32px}}
.meta-tbl{{width:100%;border-collapse:collapse;margin-bottom:18px}}
.meta-tbl td{{padding:9px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.meta-tbl td:first-child{{font-weight:600;color:#555;width:38%;background:#fafafa}}
.badge{{display:inline-block;background:#fee2e2;color:#991b1b;padding:4px 12px;border-radius:20px;font-size:.85em;font-weight:600}}
.hosp-tbl{{width:100%;border-collapse:collapse;margin-top:10px;font-size:.9em}}
.hosp-tbl th{{background:#fef3c7;color:#92400e;padding:10px 12px;text-align:left;font-weight:700;border-bottom:2px solid #fde68a}}
.hosp-tbl td{{padding:9px 12px;border-bottom:1px solid #f5f5f5;vertical-align:top}}
.hosp-tbl tr:hover td{{background:#fffbeb}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:linear-gradient(135deg,#f59e0b,#ef4444);color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>⚠️ Pending Hospital Visits Reminder</h1>
    <p>Monthly check-in — {month_label}</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.95em;">Hello,<br><br>
    This is an automated reminder that the following hospitals have <strong>not yet been visited</strong> by <strong>{sp_name}</strong> this month.</p>
    <table class="meta-tbl">
      <tr><td>Salesperson</td><td><strong>{sp_name}</strong></td></tr>
      <tr><td>Email</td><td>{sp_email}</td></tr>
      <tr><td>Month</td><td>{month_label}</td></tr>
      <tr><td>Pending Hospitals</td><td><span class="badge">{pending_count} pending</span></td></tr>
      <tr><td>Reminder Date</td><td>{now_str}</td></tr>
    </table>
    <h3 style="color:#92400e;font-size:1em;margin:18px 0 8px;">🏥 Hospitals Not Yet Visited</h3>
    <table class="hosp-tbl">
      <thead><tr><th>Hospital Name</th><th>SAP Code</th><th>Province</th></tr></thead>
      <tbody>{pending_rows}</tbody>
    </table>
    <div class="cta"><a href="{dashboard_url}">📊 View Performance Dashboard</a></div>
    <p style="color:#888;font-size:.85em;margin-top:18px;">Please ensure all pending hospitals are covered by end of month.</p>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    # Recipients: the salesperson + all warehouse managers
    all_emails = set()
    if sp_email:
        all_emails.add(sp_email)
    all_emails.update(_warehouse_emails())
    all_emails_list = list(all_emails)

    _send_emails_async([(subject, text_body, html_body, all_emails_list)])

    # Log the reminder to prevent duplicate sends this month
    try:
        MonthlyReminderLog.objects.get_or_create(
            salesperson_email=sp_email,
            year=year,
            month=month,
            defaults={
                "salesperson_name": sp_name,
                "pending_hospitals_count": pending_count,
                "pending_hospital_names": _json.dumps([h.name for h in pending_hospitals]),
            },
        )
    except Exception:
        logger.exception("Failed to log monthly reminder for %s %s-%s", sp_email, year, month)

    logger.info(
        "Monthly reminder sent for %s (%s-%s): %d pending hospitals → %s",
        sp_name,
        year,
        month,
        pending_count,
        all_emails_list,
    )
    return all_emails_list


# ─────────────────────────────────────────────────────────────────────────────
# WAREHOUSE REMARK NOTIFICATION — email salesperson when warehouse adds a remark
# ─────────────────────────────────────────────────────────────────────────────


def notify_warehouse_remark(session, remark_text, added_by, remark_type="session", product=None):
    """
    Send an email to the salesperson (session.scanned_by) when a warehouse
    person adds a remark on their scan — either batch-level or product-level.

    Args:
        session       : ScanningSession instance the remark belongs to.
        remark_text   : The text of the remark.
        added_by      : User who added the remark (warehouse person).
        remark_type   : 'session' for batch-level, 'product' for product-level.
        product       : ScannedProduct instance (only for product-level remarks).
    """
    salesperson = session.scanned_by
    if not salesperson or not salesperson.email:
        return

    batch = session.batch
    hospital = session.hospital

    batch_id = batch.batch_id if batch else "N/A"
    hosp_name = hospital.name if hospital else "N/A"
    session_url = f"{settings.SITE_URL}/product_scanning/session/{session.session_key}/"

    sp_name = f"{salesperson.first_name} {salesperson.last_name}".strip() or salesperson.username
    actor_name = (
        (f"{added_by.first_name} {added_by.last_name}".strip() or added_by.username) if added_by else "Warehouse Team"
    )

    now_str = timezone.now().strftime("%d %b %Y, %I:%M %p")

    if remark_type == "product" and product:
        type_label = "Product Remark"
        product_row = f"<tr><td>Product (S/N)</td><td>{product.serial_number}</td></tr>"
        product_row += f"<tr><td>Product Name</td><td>{product.product_name or 'N/A'}</td></tr>"
        subject_label = f"Product Remark on {product.serial_number}"
        text_product = f"Product     : {product.serial_number} — {product.product_name or 'N/A'}\n"
    else:
        type_label = "Batch Remark"
        product_row = ""
        subject_label = f"Batch Remark on {batch_id}"
        text_product = ""

    subject = f"[NexGen] Warehouse {type_label} — {subject_label} | {hosp_name}"

    text_body = (
        f"Hi {sp_name},\n\n"
        f"The warehouse team has added a {type_label.lower()} on your scan.\n\n"
        f"Batch ID    : {batch_id}\n"
        f"Hospital    : {hosp_name}\n"
        f"{text_product}"
        f"Added By    : {actor_name}\n"
        f"Date/Time   : {now_str}\n\n"
        f"Remark:\n{remark_text}\n\n"
        f"View your session: {session_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0}}
.wrapper{{max-width:600px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
.hdr{{background:linear-gradient(135deg,#7c3aed,#6d28d9);color:#fff;padding:28px 32px}}
.hdr h1{{margin:0;font-size:1.35em;font-weight:700}}.hdr p{{margin:6px 0 0;font-size:.9em;opacity:.85}}
.body{{padding:28px 32px}}
.tbl{{width:100%;border-collapse:collapse;margin:18px 0}}
.tbl td{{padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0}}
.tbl td:first-child{{font-weight:600;color:#555;width:38%;background:#fafafa}}
.remark-box{{background:#f5f3ff;border-left:4px solid #7c3aed;padding:14px 18px;border-radius:4px;margin:16px 0;font-size:.95em;color:#3b0764;line-height:1.6}}
.cta{{display:block;margin:22px 0;text-align:center}}
.cta a{{background:linear-gradient(135deg,#7c3aed,#6d28d9);color:#fff;padding:13px 30px;border-radius:7px;text-decoration:none;font-weight:600;font-size:1em}}
.ftr{{background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaa;border-top:1px solid #eee}}
</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>💬 Warehouse {type_label}</h1>
    <p>The warehouse team has added a remark on your scan.</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.95em;">Hi {sp_name},<br><br>
    A warehouse team member has reviewed your scan and left a remark that may require your attention.</p>
    <table class="tbl">
      <tr><td>Batch ID</td><td><strong>{batch_id}</strong></td></tr>
      <tr><td>Hospital</td><td>{hosp_name}</td></tr>
      {product_row}
      <tr><td>Added By</td><td>{actor_name}</td></tr>
      <tr><td>Date / Time</td><td>{now_str}</td></tr>
    </table>
    <p style="font-weight:600;color:#444;margin-bottom:6px;">Remark:</p>
    <div class="remark-box">{remark_text}</div>
    <div class="cta"><a href="{session_url}">View Session &amp; Respond</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    _send_emails_async([(subject, text_body, html_body, [salesperson.email])])
    logger.info("Warehouse remark notification sent to %s for session %s", salesperson.email, session.session_key)


# ── Awaiting overdue notifications ─────────────────────────────────────────


def _build_overdue_email(session, days_overdue):
    """Email to warehouse managers: session has been awaiting confirmation too long."""
    salesperson = session.scanned_by
    sp_name = (
        (f"{salesperson.first_name} {salesperson.last_name}".strip() or salesperson.username)
        if salesperson
        else "Unknown"
    )
    batch = session.batch
    hospital = session.hospital
    session_url = f"{settings.SITE_URL}/product_scanning/session/{session.session_key}/"
    completed_at = session.completed_at
    is_h2h = session.session_type == "transfer"
    type_label = "H2H Transfer" if is_h2h else "Stock Audit"
    product_count = session.scanned_products.count()

    subject = (
        f"[NexGen] ⚠️ Awaiting Confirmation — {days_overdue} Days Overdue — "
        f"{batch.batch_id if batch else 'N/A'} | {hospital.name if hospital else 'N/A'}"
    )

    text_body = (
        f"ACTION REQUIRED: A scanning session has been awaiting warehouse confirmation for {days_overdue} day(s).\n\n"
        f"Batch ID    : {batch.batch_id if batch else 'N/A'}\n"
        f"Session Type: {type_label}\n"
        f"Hospital    : {hospital.name if hospital else 'N/A'}\n"
        f"Salesperson : {sp_name}\n"
        f"Products    : {product_count}\n"
        f"Submitted   : {completed_at.strftime('%d %b %Y, %I:%M %p') if completed_at else 'N/A'}\n"
        f"Days Waiting: {days_overdue}\n\n"
        f"Please review and close this session, or put it on hold if more time is needed.\n"
        f"Review here: {session_url}\n\n— NexGen Automation"
    )

    submitted_str = completed_at.strftime("%d %b %Y, %I:%M %p") if completed_at else "N/A"
    urgency_color = "#dc2626" if days_overdue >= 10 else "#d97706"

    badge_style = (
        "display:inline-block;background:#fee2e2;color:#991b1b;padding:4px 12px;border-radius:20px;font-size:.85em;font-weight:600;"
        if days_overdue >= 10 else
        "display:inline-block;background:#fef3c7;color:#92400e;padding:4px 12px;border-radius:20px;font-size:.85em;font-weight:600;"
    )
    dest_row_inline = f'<tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Destination</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;">{session.to_hospital.name}</td></tr>' if is_h2h and session.to_hospital else ""

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0;">
<div style="max-width:600px;margin:30px auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08);">

  <!-- Header -->
  <div style="background:{urgency_color};padding:28px 32px;">
    <h1 style="margin:0;font-size:1.35em;font-weight:700;color:#ffffff;">⚠️ Session Awaiting Confirmation — {days_overdue} Day{'s' if days_overdue != 1 else ''} Overdue</h1>
    <p style="margin:6px 0 0;font-size:.9em;color:#ffffff;opacity:.9;">This session needs your attention. Please review and close, or put it on hold.</p>
  </div>

  <!-- Body -->
  <div style="padding:28px 32px;">
    <div style="background:#fef3c7;border-left:4px solid {urgency_color};padding:12px 16px;border-radius:0 6px 6px 0;margin-bottom:18px;font-size:.92em;color:#78350f;">
      This session has been waiting for warehouse confirmation for <strong>{days_overdue} day{'s' if days_overdue != 1 else ''}</strong>.
      Daily reminders will continue until the session is closed or put on hold.
    </div>

    <table style="width:100%;border-collapse:collapse;margin:18px 0;">
      <tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Batch ID</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;"><strong>{batch.batch_id if batch else 'N/A'}</strong></td></tr>
      <tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Session Type</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;">{type_label}</td></tr>
      <tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Hospital</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;">{hospital.name if hospital else 'N/A'}</td></tr>
      {dest_row_inline}
      <tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Salesperson</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;">{sp_name}</td></tr>
      <tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Products Scanned</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;">{product_count} items</td></tr>
      <tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Submitted At</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;">{submitted_str}</td></tr>
      <tr><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;width:38%;background:#fafafa;">Days Waiting</td><td style="padding:10px 14px;font-size:.93em;border-bottom:1px solid #f0f0f0;"><span style="{badge_style}">{days_overdue} day{'s' if days_overdue != 1 else ''}</span></td></tr>
    </table>

    <!-- CTA Button -->
    <div style="text-align:center;margin:28px 0;">
      <a href="{session_url}" target="_blank" style="background:{urgency_color};color:#ffffff;padding:14px 32px;border-radius:7px;text-decoration:none;font-weight:700;font-size:1em;display:inline-block;">📋 Review Session &amp; Close / Put On Hold</a>
    </div>
  </div>

  <!-- Footer -->
  <div style="background:#f9fafb;padding:16px 32px;text-align:center;font-size:.8em;color:#aaaaaa;border-top:1px solid #eeeeee;">
    NexGen Automation · Meril Life Sciences · This is an automated daily reminder.
  </div>

</div>
</body></html>"""

    return subject, text_body, html_body


def notify_awaiting_overdue(session, days_overdue):
    """
    Send a daily overdue-awaiting reminder to all warehouse managers and PS admins.
    Called by the send_awaiting_reminders management command.
    """
    wh_emails = _warehouse_emails()
    if not wh_emails:
        logger.warning("notify_awaiting_overdue: no warehouse recipients configured")
        return
    subject, text_body, html_body = _build_overdue_email(session, days_overdue)
    _do_send([(subject, text_body, html_body, wh_emails)])
    logger.info(
        "Overdue reminder sent for session %s (%d days) to %d recipient(s)",
        session.session_key,
        days_overdue,
        len(wh_emails),
    )
