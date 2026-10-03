"""
MeriTrack Notification Service
──────────────────────────────────────────────────────────────────
Handles:
 • Expiry alert notifications (6M / 3M / 1M / 1W / Expired)
 • Session-completed notifications
 • Order status update notifications
 • Email dispatch (async)
"""

import logging
import threading

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

logger = logging.getLogger(__name__)

_TEST_EMAIL = getattr(settings, "TEST_NOTIFICATION_EMAIL", "manas.jain@merillife.com")


def mt_url(path):
    """Absolute URL into MeriTrack for use in mail. `path` is root-relative."""
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def _cta(url, label, color="#0056b3"):
    """A call-to-action button for notification mail.

    Built as a bgcolor'd table rather than a styled <a>: Outlook renders mail
    through Word, which drops padding and background on inline elements, so a
    plain anchor arrives as bare blue text. The literal URL is printed beneath
    it for clients that strip the markup entirely.
    """
    return f"""
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:22px auto 4px;">
      <tr><td align="center" bgcolor="{color}" style="border-radius:6px;">
        <a href="{url}" style="display:inline-block;padding:12px 30px;font-family:'Segoe UI',Arial,sans-serif;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:6px;">{label}</a>
      </td></tr>
    </table>
    <p style="text-align:center;margin:0 0 4px;font-size:11px;color:#98a2b3;word-break:break-all;">{url}</p>"""


def _get_manager_recipients():
    """Return all active MT Manager / logistics / superusers."""
    group_names = ["MT Manager", "MT Logistics"]
    group_users = User.objects.filter(groups__name__in=group_names, is_active=True)
    admin_users = User.objects.filter(is_active=True, is_superuser=True)
    return list((group_users | admin_users).distinct())


def _get_rep_for_hospital(hospital):
    """Find rep assigned to a hospital via salesperson_email."""
    if hospital and hospital.salesperson_email:
        return User.objects.filter(email__iexact=hospital.salesperson_email, is_active=True).first()
    return None


def _send_emails_async(subject, text_body, html_body, recipient_emails):
    """Send emails in a background thread."""

    def _send():
        from_email = settings.DEFAULT_FROM_EMAIL
        for email in recipient_emails:
            try:
                msg = EmailMultiAlternatives(subject, text_body, from_email, [email])
                msg.attach_alternative(html_body, "text/html")
                msg.send()
                logger.info(f"MeriTrack notification sent to {email}")
            except Exception as exc:
                logger.error(f"Failed to send MeriTrack email to {email}: {exc}")

    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY ALERTS
# ─────────────────────────────────────────────────────────────────────────────
TIER_CONFIG = {
    "6m": {"label": "Early Warning — 6 Months", "color": "#22c55e", "bg": "#dcfce7"},
    "3m": {"label": "Attention — 3 Months", "color": "#f59e0b", "bg": "#fef3c7"},
    "1m": {"label": "Urgent — 1 Month", "color": "#f97316", "bg": "#ffedd5"},
    "1w": {"label": "Critical — 1 Week", "color": "#ef4444", "bg": "#fee2e2"},
    "expired": {"label": "EXPIRED", "color": "#dc2626", "bg": "#fecaca"},
}

TIER_CATEGORY_MAP = {
    "6m": "expiry_6m",
    "3m": "expiry_3m",
    "1m": "expiry_1m",
    "1w": "expiry_1w",
    "expired": "expiry_now",
}


def notify_expiry_alert(product, tier):
    """Create in-app notification + send email for an expiry alert."""
    from .models import Notification

    config = TIER_CONFIG.get(tier, TIER_CONFIG["expired"])
    category = TIER_CATEGORY_MAP.get(tier, "expiry_now")
    hospital = product.department.hospital
    days = product.days_until_expiry or 0

    title = f"{config['label']}: {product.product_name or product.serial_number}"
    message = (
        f"Product {product.serial_number} at {hospital.name} / "
        f"{product.department.name} expires in {days} day(s) "
        f"(expiry: {product.expiry_date}). Action required."
    )

    recipients = _get_manager_recipients()
    rep = _get_rep_for_hospital(hospital)
    if rep and rep not in recipients:
        recipients.append(rep)

    for user in recipients:
        Notification.objects.get_or_create(
            recipient=user,
            category=category,
            title=title,
            defaults={
                "message": message,
                "hospital": hospital,
            },
        )

    # Email
    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    expiry_url = mt_url("/meritrack/expiry-alerts/")
    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, {config['color']}, #333); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .badge {{ display: inline-block; background: {config['bg']}; color: {config['color']}; padding: 4px 14px; border-radius: 20px; font-weight: 700; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">⚠️ Expiry Alert</h2><p style="margin:4px 0 0;opacity:.8">{config['label']}</p></div>
  <div class="body">
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Product</strong></td><td>{product.product_name or 'N/A'}</td></tr>
      <tr><td><strong>Serial</strong></td><td>{product.serial_number}</td></tr>
      <tr><td><strong>Hospital</strong></td><td>{hospital.name}</td></tr>
      <tr><td><strong>Department</strong></td><td>{product.department.name}</td></tr>
      <tr><td><strong>Expiry Date</strong></td><td>{product.expiry_date}</td></tr>
      <tr><td><strong>Days Left</strong></td><td><span class="badge">{days} day(s)</span></td></tr>
    </table>{_cta(expiry_url, 'View Expiry Alerts', config['color'])}
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = (
        f"Expiry Alert: {product.serial_number} at {hospital.name} expires {product.expiry_date}.\n"
        f"View expiry alerts: {expiry_url}"
    )
    _send_emails_async(
        f"[MeriTrack] {config['label']} — {product.serial_number}", text_body, html_body, recipient_emails
    )


# ─────────────────────────────────────────────────────────────────────────────
# SESSION COMPLETED
# ─────────────────────────────────────────────────────────────────────────────
def notify_transfer_closed(transfer, moved=0, skipped=0):
    """Notify managers, the destination hospital's rep, and the initiator that a
    hospital-to-hospital transfer has closed and stock has moved."""
    from .models import Notification

    from_hosp = transfer.from_hospital
    to_hosp = transfer.to_hospital

    title = f"Transfer received — {to_hosp.name}"
    message = (
        f"{moved} item(s) transferred from {from_hosp.name} to {to_hosp.name} "
        f"({transfer.transfer_id})."
    )
    if skipped:
        message += f" {skipped} item(s) had no matching source stock and need review."

    # Managers + the receiving hospital's rep + whoever initiated the transfer.
    recipients = _get_manager_recipients()
    dest_rep = _get_rep_for_hospital(to_hosp)
    if dest_rep and dest_rep not in recipients:
        recipients.append(dest_rep)
    if transfer.initiated_by and transfer.initiated_by not in recipients:
        recipients.append(transfer.initiated_by)

    for user in recipients:
        Notification.objects.create(
            recipient=user,
            category="transfer_closed",
            title=title,
            message=message,
            hospital=to_hosp,
        )

    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)
    if not recipient_emails:
        return

    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #f97316, #ea580c); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">🔄 Transfer Completed</h2><p style="margin:4px 0 0;opacity:.85">{transfer.transfer_id}</p></div>
  <div class="body">
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>From</strong></td><td>{from_hosp.name}</td></tr>
      <tr><td><strong>To</strong></td><td>{to_hosp.name}</td></tr>
      <tr><td><strong>Items moved</strong></td><td>{moved}</td></tr>
      <tr><td><strong>Unmatched</strong></td><td>{skipped}</td></tr>
    </table>
    <div style="text-align:center;margin:20px 0">
      <a href="{settings.SITE_URL}/meritrack/transfers/{transfer.session_key}/"
         style="background:linear-gradient(135deg,#f97316,#ea580c);color:#fff;padding:12px 28px;border-radius:7px;text-decoration:none;font-weight:600;">View Transfer</a>
    </div>
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""
    text_body = f"Transfer {transfer.transfer_id}: {moved} item(s) {from_hosp.name} → {to_hosp.name}. {skipped} unmatched."
    _send_emails_async(f"[MeriTrack] Transfer Completed — {to_hosp.name}", text_body, html_body, recipient_emails)


def _claim_submission_notice(session):
    """
    Atomically claim the one-shot 'session submitted' notification for this session.

    Returns True for the first caller and False for every duplicate, so a double-click,
    held Enter key, or client retry cannot re-fire the manager fan-out even if the calling
    view forgets to guard the status transition.
    """
    from .models import ScanningSession

    claimed = ScanningSession.objects.filter(pk=session.pk, submission_notified_at__isnull=True).update(
        submission_notified_at=timezone.now()
    )
    if not claimed:
        logger.info(
            "Submission notice already sent for session %s — skipping duplicate",
            session.session_key,
        )
    return bool(claimed)


def notify_session_completed(session):
    """Notify managers when a scanning session is completed.

    Fires at most once per session — see _claim_submission_notice().
    """
    from .models import Notification

    if not _claim_submission_notice(session):
        return

    scan_count = session.scan_logs.count()
    rep = session.rep
    rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else "Unknown"
    hospital = session.hospital

    title = f"Session completed — {hospital.name}"
    message = (
        f"{rep_name} completed a {session.get_session_type_display()} session "
        f"at {hospital.name} with {scan_count} scan(s)."
    )

    recipients = _get_manager_recipients()
    for user in recipients:
        Notification.objects.create(
            recipient=user,
            category="session_completed",
            title=title,
            message=message,
            hospital=hospital,
            session=session,
        )

    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    completed_at = session.completed_at or timezone.now()
    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #667eea, #764ba2); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .cta a {{ background: linear-gradient(135deg,#667eea,#764ba2); color: #fff; padding: 12px 28px; border-radius: 7px; text-decoration: none; font-weight: 600; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">✅ Session Completed</h2><p style="margin:4px 0 0;opacity:.8">{session.get_session_type_display()}</p></div>
  <div class="body">
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Hospital</strong></td><td>{hospital.name}</td></tr>
      <tr><td><strong>Rep</strong></td><td>{rep_name}</td></tr>
      <tr><td><strong>Type</strong></td><td>{session.get_session_type_display()}</td></tr>
      <tr><td><strong>Scans</strong></td><td>{scan_count}</td></tr>
      <tr><td><strong>Completed</strong></td><td>{completed_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
    </table>
    <div class="cta" style="text-align:center;margin:20px 0">
      <a href="{settings.SITE_URL}/meritrack/session/{session.session_key}/">Review Session</a>
    </div>
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = f"Session completed: {rep_name} at {hospital.name}, {scan_count} scans."
    _send_emails_async(f"[MeriTrack] Session Completed — {hospital.name}", text_body, html_body, recipient_emails)

    # Separate confirmation email to the submitting rep (if not already a manager recipient)
    if rep and rep.email and rep.email not in recipient_emails:
        _send_submitter_confirmation(session, rep, rep_name, scan_count)


def _send_submitter_confirmation(session, rep, rep_name, scan_count):
    """Green-header confirmation email to the rep who submitted the session."""
    hospital = session.hospital
    completed_at = session.completed_at or timezone.now()
    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #16a34a, #22c55e); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .cta a {{ background: linear-gradient(135deg,#16a34a,#22c55e); color: #fff; padding: 12px 28px; border-radius: 7px; text-decoration: none; font-weight: 600; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">✅ Submission Received</h2><p style="margin:4px 0 0;opacity:.8">MeriTrack</p></div>
  <div class="body">
    <p>Hi {rep_name},</p>
    <p>Your {session.get_session_type_display()} session at <strong>{hospital.name}</strong> has been submitted successfully and is now awaiting warehouse review.</p>
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Session</strong></td><td>{session.session_key}</td></tr>
      <tr><td><strong>Hospital</strong></td><td>{hospital.name}</td></tr>
      <tr><td><strong>Scans</strong></td><td>{scan_count}</td></tr>
      <tr><td><strong>Submitted</strong></td><td>{completed_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
    </table>
    <div class="cta" style="text-align:center;margin:20px 0">
      <a href="{settings.SITE_URL}/meritrack/session/{session.session_key}/">View Session</a>
    </div>
    <p style="color:#888;font-size:.85em;">You will receive another email once the warehouse confirms and closes this session.</p>
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = (
        f"Your session {session.session_key} at {hospital.name} was submitted with {scan_count} scan(s) "
        f"and is awaiting warehouse review."
    )
    _send_emails_async(
        f"[MeriTrack] Submission Received — {hospital.name}", text_body, html_body, [rep.email]
    )


# ─────────────────────────────────────────────────────────────────────────────
# ORDER STATUS UPDATE
# ─────────────────────────────────────────────────────────────────────────────
def notify_order_update(order, old_status, new_status):
    """Notify rep and managers of order status change (in-app + email)."""
    from .models import Notification

    title = f"Order {order.order_id} — {order.get_status_display()}"
    message = (
        f"Order {order.order_id} for {order.hospital.name} / {order.department.name} "
        f"status changed from {old_status} to {new_status}."
    )

    recipients = _get_manager_recipients()
    if order.ordered_by and order.ordered_by not in recipients:
        recipients.append(order.ordered_by)

    for user in recipients:
        Notification.objects.create(
            recipient=user,
            category="order_update",
            title=title,
            message=message,
            hospital=order.hospital,
        )

    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    item_count = order.items.count() if hasattr(order, "items") else 0
    orders_url = mt_url("/meritrack/orders/")
    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #0076c8, #00a3e0); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">📦 Order Status Update</h2><p style="margin:4px 0 0;opacity:.8">MeriTrack</p></div>
  <div class="body">
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Order ID</strong></td><td>{order.order_id}</td></tr>
      <tr><td><strong>Hospital</strong></td><td>{order.hospital.name}</td></tr>
      <tr><td><strong>Department</strong></td><td>{order.department.name if order.department else '—'}</td></tr>
      <tr><td><strong>Items</strong></td><td>{item_count}</td></tr>
      <tr><td><strong>Status</strong></td><td>{old_status} → <strong>{new_status}</strong></td></tr>
    </table>{_cta(orders_url, 'View Order')}
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = f"{message}\nView order: {orders_url}"
    _send_emails_async(
        f"[MeriTrack] Order {order.order_id} — {old_status} → {new_status}",
        text_body,
        html_body,
        recipient_emails,
    )


# ─────────────────────────────────────────────────────────────────────────────
# BATCH SUBMITTED (alias for session completed — used by complete_session
# and force_complete_session to notify managers)
# ─────────────────────────────────────────────────────────────────────────────
def notify_batch_submitted(session, submitted_by=None):
    """Notify MT Managers when a session is submitted/completed by a rep.

    Fires at most once per session — see _claim_submission_notice().
    """
    from .models import Notification

    if not _claim_submission_notice(session):
        return

    scan_count = session.scan_logs.count()
    rep = session.rep
    rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else "Unknown"
    submitter = submitted_by or rep
    submitter_name = (
        (f"{submitter.first_name} {submitter.last_name}".strip() or submitter.username) if submitter else rep_name
    )
    hospital = session.hospital

    title = f"Session submitted — {hospital.name}"
    message = (
        f"{submitter_name} submitted a {session.get_session_type_display()} session "
        f"at {hospital.name} with {scan_count} scan(s). Awaiting your confirmation."
    )

    recipients = _get_manager_recipients()
    for user in recipients:
        Notification.objects.create(
            recipient=user,
            category="session_completed",
            title=title,
            message=message,
            hospital=hospital,
            session=session,
        )

    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    completed_at = session.completed_at or timezone.now()
    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #0284c7, #0ea5e9); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .cta a {{ background: linear-gradient(135deg,#0284c7,#0ea5e9); color: #fff; padding: 12px 28px; border-radius: 7px; text-decoration: none; font-weight: 600; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">📋 Session Submitted — Action Required</h2><p style="margin:4px 0 0;opacity:.8">MeriTrack</p></div>
  <div class="body">
    <p>A scanning session has been submitted and is awaiting your confirmation.</p>
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Hospital</strong></td><td>{hospital.name}</td></tr>
      <tr><td><strong>Rep</strong></td><td>{rep_name}</td></tr>
      <tr><td><strong>Submitted By</strong></td><td>{submitter_name}</td></tr>
      <tr><td><strong>Type</strong></td><td>{session.get_session_type_display()}</td></tr>
      <tr><td><strong>Scans</strong></td><td>{scan_count}</td></tr>
      <tr><td><strong>Submitted At</strong></td><td>{completed_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
    </table>
    <div class="cta" style="text-align:center;margin:20px 0">
      <a href="{settings.SITE_URL}/meritrack/session/{session.session_key}/">Review &amp; Confirm</a>
    </div>
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = f"Session submitted: {submitter_name} at {hospital.name}, {scan_count} scans. Awaiting confirmation."
    _send_emails_async(f"[MeriTrack] Session Submitted — {hospital.name}", text_body, html_body, recipient_emails)

    # Separate confirmation email to the rep (if not already a manager recipient)
    if rep and rep.email and rep.email not in recipient_emails:
        _send_submitter_confirmation(session, rep, rep_name, scan_count)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION TERMINATED
# ─────────────────────────────────────────────────────────────────────────────
def notify_session_terminated(session, terminated_by=None, reason=""):
    """Notify managers and rep when a session is terminated/cancelled."""
    from .models import Notification

    terminator = terminated_by
    terminator_name = (
        (f"{terminator.first_name} {terminator.last_name}".strip() or terminator.username) if terminator else "System"
    )
    rep = session.rep
    rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else "Unknown"
    hospital = session.hospital

    title = f"Session terminated — {hospital.name}"
    message = f"{terminator_name} terminated a session at {hospital.name}. Reason: {reason or 'Not provided'}"

    recipients = _get_manager_recipients()
    # Also notify the rep
    if rep and rep not in recipients:
        recipients.append(rep)

    for user in recipients:
        Notification.objects.create(
            recipient=user,
            category="session_terminated",
            title=title,
            message=message,
            hospital=hospital,
            session=session,
        )

    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    session_url = mt_url(f"/meritrack/session/{session.session_key}/")
    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #dc2626, #ef4444); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">🚫 Session Terminated</h2><p style="margin:4px 0 0;opacity:.8">MeriTrack</p></div>
  <div class="body">
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Hospital</strong></td><td>{hospital.name}</td></tr>
      <tr><td><strong>Rep</strong></td><td>{rep_name}</td></tr>
      <tr><td><strong>Terminated By</strong></td><td>{terminator_name}</td></tr>
      <tr><td><strong>Reason</strong></td><td>{reason or 'Not provided'}</td></tr>
      <tr><td><strong>Session Key</strong></td><td>{session.session_key}</td></tr>
    </table>{_cta(session_url, 'View Session', '#dc3545')}
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = (
        f"Session terminated at {hospital.name} by {terminator_name}. Reason: {reason}\n"
        f"View session: {session_url}"
    )
    _send_emails_async(f"[MeriTrack] Session Terminated — {hospital.name}", text_body, html_body, recipient_emails)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION CLOSED (attachment uploaded + session confirmed)
# ─────────────────────────────────────────────────────────────────────────────
def notify_session_closed(session, closed_by=None):
    """Notify rep when a manager uploads the confirmation doc and closes the session."""
    from .models import Notification

    closer = closed_by
    closer_name = (f"{closer.first_name} {closer.last_name}".strip() or closer.username) if closer else "Manager"
    rep = session.rep
    rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else "Unknown"
    hospital = session.hospital

    title = f"Session confirmed & closed — {hospital.name}"
    message = (
        f"{closer_name} uploaded a confirmation document and closed your session at {hospital.name}. "
        f"Session {session.session_key} is now fully confirmed."
    )

    # Notify rep + managers
    recipients = _get_manager_recipients()
    if rep and rep not in recipients:
        recipients.append(rep)

    for user in recipients:
        Notification.objects.create(
            recipient=user,
            category="session_closed",
            title=title,
            message=message,
            hospital=hospital,
            session=session,
        )

    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #16a34a, #22c55e); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .cta a {{ background: linear-gradient(135deg,#16a34a,#22c55e); color: #fff; padding: 12px 28px; border-radius: 7px; text-decoration: none; font-weight: 600; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">✅ Session Confirmed &amp; Closed</h2><p style="margin:4px 0 0;opacity:.8">MeriTrack</p></div>
  <div class="body">
    <p>Your scanning session has been confirmed and closed by management.</p>
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Hospital</strong></td><td>{hospital.name}</td></tr>
      <tr><td><strong>Rep</strong></td><td>{rep_name}</td></tr>
      <tr><td><strong>Confirmed By</strong></td><td>{closer_name}</td></tr>
      <tr><td><strong>Session Key</strong></td><td>{session.session_key}</td></tr>
    </table>
    <div class="cta" style="text-align:center;margin:20px 0">
      <a href="{settings.SITE_URL}/meritrack/session/{session.session_key}/">View Session</a>
    </div>
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = f"Session {session.session_key} at {hospital.name} confirmed and closed by {closer_name}."
    _send_emails_async(f"[MeriTrack] Session Confirmed — {hospital.name}", text_body, html_body, recipient_emails)


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED PRODUCT ACKNOWLEDGED
# ─────────────────────────────────────────────────────────────────────────────
def notify_expired_acknowledged(product_ids, acknowledged_by, count):
    """Notify managers when expired products are acknowledged."""
    from .models import Notification

    ack_name = (
        (f"{acknowledged_by.first_name} {acknowledged_by.last_name}".strip() or acknowledged_by.username)
        if acknowledged_by
        else "Unknown"
    )
    title = f"Expired products acknowledged ({count})"
    message = f"{ack_name} acknowledged {count} expired product(s)."

    recipients = _get_manager_recipients()
    for user in recipients:
        Notification.objects.create(
            recipient=user,
            category="expiry_now",
            title=title,
            message=message,
        )

    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    expiry_url = mt_url("/meritrack/expiry-alerts/")
    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #d97706, #f59e0b); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">⚠️ Expired Products Acknowledged</h2></div>
  <div class="body">
    <p>{ack_name} acknowledged <strong>{count}</strong> expired product(s) in MeriTrack.</p>{_cta(expiry_url, 'View Expiry Alerts')}
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated Notification</div>
</div></body></html>"""

    text_body = f"{ack_name} acknowledged {count} expired product(s) in MeriTrack.\nView expiry alerts: {expiry_url}"
    _send_emails_async("[MeriTrack] Expired Products Acknowledged", text_body, html_body, recipient_emails)


def notify_warehouse_remark(session, remark_text, added_by, remark_type="session", product=None):
    """Notify the session rep when a warehouse/manager user adds a remark."""
    from .models import Notification

    if not session or not session.rep:
        return

    rep = session.rep
    hospital = session.hospital
    added_by_name = (
        (f"{added_by.first_name} {added_by.last_name}".strip() or added_by.username) if added_by else "Warehouse"
    )

    if remark_type == "product" and product is not None:
        title = f"Warehouse remark on product {product.serial_number}"
        message = (
            f"{added_by_name} added a remark on product {product.serial_number} "
            f"for your session at {hospital.name}: {remark_text}"
        )
    else:
        title = f"Warehouse remark on session {session.session_key}"
        message = f"{added_by_name} added a session remark for your session at " f"{hospital.name}: {remark_text}"

    Notification.objects.create(
        recipient=rep,
        category="session_completed",
        title=title,
        message=message,
        hospital=hospital,
        session=session,
    )

    recipient_emails = [rep.email] if rep.email else []
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)

    if not recipient_emails:
        return

    session_url = mt_url(f"/meritrack/session/{session.session_key}/")
    html_body = f"""
<!DOCTYPE html><html><head><meta charset=\"UTF-8\"></head><body>
<p>{message}</p>
<p>Session: {session.session_key}</p>{_cta(session_url, 'View Session')}
</body></html>"""
    _send_emails_async(
        f"[MeriTrack] {title}",
        f"{message}\nSession: {session.session_key}\nView session: {session_url}",
        html_body,
        recipient_emails,
    )


# ─────────────────────────────────────────────────────────────────────────────
# AWAITING-CONFIRMATION OVERDUE REMINDER (daily cron)
# ─────────────────────────────────────────────────────────────────────────────
def notify_awaiting_overdue(session, days_overdue):
    """
    Daily reminder to managers for a session stuck in awaiting-confirmation
    (completed) for more than the overdue threshold.
    Called by the send_awaiting_reminders management command.
    """
    recipients = _get_manager_recipients()
    recipient_emails = list({u.email for u in recipients if u.email})
    if _TEST_EMAIL and _TEST_EMAIL not in recipient_emails:
        recipient_emails.append(_TEST_EMAIL)
    if not recipient_emails:
        logger.warning("notify_awaiting_overdue: no manager recipients configured")
        return

    rep = session.rep
    rep_name = (f"{rep.first_name} {rep.last_name}".strip() or rep.username) if rep else "Unknown"
    hospital = session.hospital
    scan_count = session.scan_logs.count()
    completed_at = session.completed_at
    submitted_str = completed_at.strftime("%d %b %Y, %I:%M %p") if completed_at else "N/A"
    session_url = f"{settings.SITE_URL}/meritrack/session/{session.session_key}/"
    urgency_color = "#dc2626" if days_overdue >= 10 else "#d97706"
    day_label = f"{days_overdue} day{'s' if days_overdue != 1 else ''}"

    subject = f"[MeriTrack] ⚠️ Awaiting Confirmation — {day_label} Overdue — {hospital.name}"
    text_body = (
        f"ACTION REQUIRED: A session has been awaiting confirmation for {day_label}.\n\n"
        f"Session : {session.session_key}\n"
        f"Type    : {session.get_session_type_display()}\n"
        f"Hospital: {hospital.name}\n"
        f"Rep     : {rep_name}\n"
        f"Scans   : {scan_count}\n"
        f"Submitted: {submitted_str}\n\n"
        f"Please review and close this session, or put it on hold: {session_url}\n\n— MeriTrack"
    )

    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 600px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: {urgency_color}; color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .callout {{ background: #fef3c7; border-left: 4px solid {urgency_color}; padding: 12px 16px; border-radius: 0 6px 6px 0; margin-bottom: 16px; font-size: .92em; color: #78350f; }}
  .info td {{ padding: 8px 14px; font-size: 0.95em; border-bottom: 1px solid #f0f0f0; }}
  .cta a {{ background: {urgency_color}; color: #fff; padding: 12px 28px; border-radius: 7px; text-decoration: none; font-weight: 600; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">⚠️ Awaiting Confirmation — {day_label} Overdue</h2><p style="margin:4px 0 0;opacity:.9">MeriTrack</p></div>
  <div class="body">
    <div class="callout">This session has been waiting for confirmation for <strong>{day_label}</strong>.
    Daily reminders will continue until it is closed or put on hold.</div>
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Session</strong></td><td>{session.session_key}</td></tr>
      <tr><td><strong>Type</strong></td><td>{session.get_session_type_display()}</td></tr>
      <tr><td><strong>Hospital</strong></td><td>{hospital.name}</td></tr>
      <tr><td><strong>Rep</strong></td><td>{rep_name}</td></tr>
      <tr><td><strong>Scans</strong></td><td>{scan_count}</td></tr>
      <tr><td><strong>Submitted</strong></td><td>{submitted_str}</td></tr>
    </table>
    <div class="cta" style="text-align:center;margin:20px 0">
      <a href="{session_url}">📋 Review Session — Close / Put On Hold</a>
    </div>
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated daily reminder</div>
</div></body></html>"""

    _send_emails_async(subject, text_body, html_body, recipient_emails)
    logger.info(
        "Overdue reminder sent for session %s (%d days) to %d recipient(s)",
        session.session_key,
        days_overdue,
        len(recipient_emails),
    )


# ─────────────────────────────────────────────────────────────────────────────
# MONTHLY PENDING HOSPITAL VISITS REMINDER (cron, 20th of month)
# ─────────────────────────────────────────────────────────────────────────────
def notify_pending_hospitals_reminder(sp_email, sp_name, year, month, pending_hospitals):
    """
    Remind a rep (and managers) of hospitals not yet visited this month.
    Called by the send_monthly_reminders management command.
    Returns the list of notified email addresses.
    """
    import json as _json

    from .models import MonthlyReminderLog

    month_label = (
        f"{['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][month]} {year}"
    )
    dashboard_url = f"{settings.SITE_URL}/meritrack/sp-dashboard/"
    pending_count = len(pending_hospitals)
    now_str = timezone.now().strftime("%d %b %Y, %I:%M %p")

    pending_rows = "".join(
        f"<tr><td>{h.name}</td><td>{h.sap_code or '-'}</td><td>{h.city or h.province or '-'}</td></tr>"
        for h in pending_hospitals
    )

    subject = f"[MeriTrack] ⚠️ Pending Hospital Visits — {sp_name} | {month_label}"
    text_body = (
        f"Reminder: {sp_name} has {pending_count} hospital(s) not yet visited this month ({month_label}).\n\n"
        + "\n".join(f"  • {h.name} ({h.sap_code or 'N/A'})" for h in pending_hospitals)
        + f"\n\nView dashboard: {dashboard_url}\n\n— MeriTrack"
    )

    html_body = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; }}
  .wrapper {{ max-width: 620px; margin: 30px auto; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #f59e0b, #ef4444); color: #fff; padding: 24px 32px; }}
  .body {{ padding: 24px 32px; }}
  .info td {{ padding: 8px 14px; font-size: 0.93em; border-bottom: 1px solid #f0f0f0; }}
  .badge {{ display: inline-block; background: #fee2e2; color: #991b1b; padding: 4px 12px; border-radius: 20px; font-size: .85em; font-weight: 600; }}
  .hosp th {{ background: #fef3c7; color: #92400e; padding: 10px 12px; text-align: left; font-weight: 700; border-bottom: 2px solid #fde68a; }}
  .hosp td {{ padding: 9px 12px; border-bottom: 1px solid #f5f5f5; }}
  .cta a {{ background: linear-gradient(135deg,#f59e0b,#ef4444); color: #fff; padding: 12px 28px; border-radius: 7px; text-decoration: none; font-weight: 600; }}
  .footer {{ background: #f9fafb; padding: 14px 32px; text-align: center; font-size: 0.8em; color: #aaa; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h2 style="margin:0">⚠️ Pending Hospital Visits Reminder</h2><p style="margin:4px 0 0;opacity:.9">Monthly check-in — {month_label}</p></div>
  <div class="body">
    <p>The following hospitals have <strong>not yet been visited</strong> by <strong>{sp_name}</strong> this month.</p>
    <table class="info" width="100%" cellspacing="0">
      <tr><td><strong>Rep</strong></td><td>{sp_name}</td></tr>
      <tr><td><strong>Email</strong></td><td>{sp_email}</td></tr>
      <tr><td><strong>Month</strong></td><td>{month_label}</td></tr>
      <tr><td><strong>Pending</strong></td><td><span class="badge">{pending_count} pending</span></td></tr>
      <tr><td><strong>Reminder Date</strong></td><td>{now_str}</td></tr>
    </table>
    <h3 style="color:#92400e;font-size:1em;margin:18px 0 8px;">🏥 Hospitals Not Yet Visited</h3>
    <table class="hosp" width="100%" cellspacing="0">
      <thead><tr><th>Hospital</th><th>SAP Code</th><th>City/Province</th></tr></thead>
      <tbody>{pending_rows}</tbody>
    </table>
    <div class="cta" style="text-align:center;margin:22px 0">
      <a href="{dashboard_url}">📊 View Performance Dashboard</a>
    </div>
    <p style="color:#888;font-size:.85em;">Please ensure all pending hospitals are covered by end of month.</p>
  </div>
  <div class="footer">MeriTrack · Meril Life Sciences · Automated monthly reminder</div>
</div></body></html>"""

    all_emails = set()
    if sp_email:
        all_emails.add(sp_email)
    all_emails.update(u.email for u in _get_manager_recipients() if u.email)
    if _TEST_EMAIL:
        all_emails.add(_TEST_EMAIL)
    all_emails_list = list(all_emails)

    _send_emails_async(subject, text_body, html_body, all_emails_list)

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
        "Monthly reminder sent for %s (%s-%s): %d pending hospitals -> %s",
        sp_name, year, month, pending_count, all_emails_list,
    )
    return all_emails_list
