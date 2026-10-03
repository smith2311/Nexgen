"""
feedback_notifications.py
─────────────────────────────────────────────────────────────────────────────
Email notifications for the Feedback / Bug Report system.

Events
  • notify_feedback_submitted  – new ticket raised by any user
  • notify_feedback_reply      – admin/superuser replies to a ticket
  • notify_status_changed      – ticket status changes

Recipients
  • On submission  : manas.jain@merillife.com + all active superusers
  • On admin reply : the original submitter (if they have an email)
  • On user reply  : manas.jain@merillife.com + all active superusers
"""

import logging
import threading

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

# ── Primary escalation recipient ────────────────────────────────────────────
FEEDBACK_ADMIN_EMAILS = [
    "manas.jain@merillife.com",
]

CATEGORY_ICONS = {
    "bug": "🐛",
    "feature": "💡",
    "ux": "🎨",
    "performance": "⚡",
    "data": "📊",
    "other": "💬",
}

SEVERITY_COLORS = {
    "low": ("#d1fae5", "#065f46", "Low"),
    "medium": ("#fef3c7", "#92400e", "Medium"),
    "high": ("#fee2e2", "#991b1b", "High"),
    "critical": ("#fce7f3", "#9d174d", "🚨 Critical"),
}

STATUS_COLORS = {
    "open": ("#dbeafe", "#1e40af", "Open"),
    "in_progress": ("#fef3c7", "#92400e", "In Progress"),
    "resolved": ("#d1fae5", "#065f46", "Resolved"),
    "closed": ("#f3f4f6", "#374151", "Closed"),
}


def _admin_emails():
    """Superusers + hardcoded admin list."""
    su_emails = set(
        User.objects.filter(is_active=True, is_superuser=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    for em in FEEDBACK_ADMIN_EMAILS:
        su_emails.add(em)
    return list(su_emails)


def _send_async(msg):
    """Send an EmailMultiAlternatives in a background thread."""
    def _worker():
        try:
            msg.send(fail_silently=False)
        except Exception as exc:
            logger.error("Feedback email send failed: %s", exc)

    threading.Thread(target=_worker, daemon=True).start()


# ── Email builders ──────────────────────────────────────────────────────────

def _common_css():
    return """
body{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0;
  -webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}
.wrapper{max-width:620px;width:100%;margin:20px auto;background:#fff;border-radius:12px;
  overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.10)}
.hdr{background:linear-gradient(135deg,#0076c8,#00b5e2);color:#fff;padding:26px 28px}
.hdr h1{margin:0;font-size:1.25em;font-weight:700;letter-spacing:-.2px;word-break:break-word}
.hdr p{margin:6px 0 0;font-size:.87em;opacity:.85;word-break:break-word}
.body{padding:24px 28px}
.tbl{width:100%;border-collapse:collapse;margin:16px 0}
.tbl td{padding:9px 12px;font-size:.9em;border-bottom:1px solid #f0f0f0;word-break:break-word}
.tbl td:first-child{font-weight:600;color:#555;width:36%;background:#fafafa;white-space:nowrap}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.82em;font-weight:700}
/* ── CTA button — mobile-safe ─────────────────────────────────────────────
   display:inline-block is required; without it padding breaks on mobile
   email clients (Gmail Android, Outlook iOS, etc.)                       */
.cta{display:block;margin:22px 0;text-align:center;word-break:break-word}
.cta a{
  display:inline-block;
  background:linear-gradient(135deg,#0076c8,#00b5e2);
  color:#fff !important;
  padding:13px 26px;
  border-radius:8px;
  text-decoration:none;
  font-weight:600;
  font-size:.93em;
  line-height:1.5;
  max-width:calc(100% - 40px);
  box-sizing:border-box;
  word-break:break-word;
  overflow-wrap:break-word;
  -webkit-text-decoration:none}
.msg-box{background:#f8fafc;border-left:4px solid #0076c8;border-radius:0 8px 8px 0;
  padding:14px 16px;margin:16px 0;font-size:.91em;color:#374151;line-height:1.6;
  white-space:pre-wrap;word-wrap:break-word}
.ftr{background:#f9fafb;padding:12px 28px;text-align:center;
  font-size:.78em;color:#aaa;border-top:1px solid #eee}
/* ── Responsive breakpoint ──────────────────────────────────────────────── */
@media only screen and (max-width:480px){
  .wrapper{border-radius:0!important;margin:0 auto!important;width:100%!important}
  .hdr{padding:18px 16px!important}
  .hdr h1{font-size:1.1em!important}
  .body{padding:16px!important}
  .tbl td{padding:7px 8px!important;font-size:.84em!important}
  .tbl td:first-child{white-space:normal!important;width:auto!important}
  .cta a{
    display:block!important;
    padding:14px 12px!important;
    font-size:.88em!important;
    max-width:100%!important;
    text-align:center!important}
  .ftr{padding:10px 16px!important;font-size:.75em!important}
  .msg-box{padding:12px 12px!important;font-size:.87em!important}
}
"""


def _build_submission_email(feedback):
    """HTML/text email to admins: new feedback submitted."""
    cat_icon = CATEGORY_ICONS.get(feedback.category, "📌")
    cat_label = dict(feedback.CATEGORY_CHOICES).get(feedback.category, feedback.category)
    sev_bg, sev_fg, sev_label = SEVERITY_COLORS.get(feedback.severity, ("#f3f4f6", "#374151", feedback.severity))
    from product_scanning.feedback_registry import get_label as _get_label
    mod_label = _get_label(feedback.module)
    portal_url = f"{settings.SITE_URL}/feedback/inbox/"

    subject = (
        f"[NexGen Feedback] {cat_icon} {feedback.feedback_id} — "
        f"{feedback.title[:60]}{'…' if len(feedback.title) > 60 else ''}"
    )

    text_body = (
        f"New feedback has been submitted on NexGen.\n\n"
        f"Feedback ID : {feedback.feedback_id}\n"
        f"Category    : {cat_label}\n"
        f"Severity    : {sev_label}\n"
        f"Module      : {mod_label}\n"
        f"Submitted by: {feedback.submitter_name} ({feedback.submitter_role_label})\n"
        f"Email       : {feedback.submitted_by.email if feedback.submitted_by else 'N/A'}\n"
        f"Submitted at: {feedback.submitted_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"Title\n-----\n{feedback.title}\n\n"
        f"Description\n-----------\n{feedback.description}\n\n"
        f"Review & respond here: {portal_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_common_css()}</style></head>
<body><div class="wrapper">
  <div class="hdr">
    <h1>{cat_icon} New Feedback Submitted</h1>
    <p>A user has raised a ticket · {feedback.feedback_id}</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.94em;">Hello,<br><br>
    A new feedback ticket has been submitted on <strong>NexGen Automation</strong> and requires your attention.</p>
    <table class="tbl">
      <tr><td>Feedback ID</td><td><strong>{feedback.feedback_id}</strong></td></tr>
      <tr><td>Category</td><td>{cat_icon} {cat_label}</td></tr>
      <tr><td>Severity</td><td>
        <span class="badge" style="background:{sev_bg};color:{sev_fg};">{sev_label}</span>
      </td></tr>
      <tr><td>Module</td><td>{mod_label}</td></tr>
      <tr><td>Submitted By</td><td><strong>{feedback.submitter_name}</strong>
        &nbsp;<span style="color:#6b7280;font-size:.85em;">({feedback.submitter_role_label})</span></td></tr>
      <tr><td>Email</td><td>{feedback.submitted_by.email if feedback.submitted_by else 'N/A'}</td></tr>
      <tr><td>Submitted At</td><td>{feedback.submitted_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
    </table>
    <p style="font-weight:600;color:#1f2937;margin:20px 0 6px;">Title</p>
    <div class="msg-box" style="border-color:#0076c8;font-weight:600;">{feedback.title}</div>
    <p style="font-weight:600;color:#1f2937;margin:20px 0 6px;">Description</p>
    <div class="msg-box">{feedback.description.replace(chr(10), '<br>')}</div>
    {"<p style='color:#6b7280;font-size:.88em;margin-top:12px;'>📎 A screenshot was attached — view it in the portal.</p>" if feedback.screenshot else ""}
    <div class="cta"><a href="{portal_url}">📬 Open Feedback Inbox &amp; Reply</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    return subject, text_body, html_body


def _build_reply_email(reply):
    """HTML/text email to submitter when admin replies."""
    feedback = reply.feedback
    cat_icon = CATEGORY_ICONS.get(feedback.category, "📌")
    replier = reply.replied_by
    replier_name = replier.get_full_name() or replier.username if replier else "Support Team"
    portal_url = f"{settings.SITE_URL}/feedback/"

    subject = (
        f"[NexGen Feedback] {cat_icon} Reply on {feedback.feedback_id} — "
        f"{feedback.title[:50]}{'…' if len(feedback.title) > 50 else ''}"
    )

    text_body = (
        f"Hi {feedback.submitter_name},\n\n"
        f"Your feedback ticket ({feedback.feedback_id}) has received a response from "
        f"{replier_name}.\n\n"
        f"Response\n--------\n{reply.message}\n\n"
        f"View the full conversation here: {portal_url}\n\n— NexGen Support Team"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_common_css()}</style></head>
<body><div class="wrapper">
  <div class="hdr" style="background:linear-gradient(135deg,#7c3aed,#a78bfa);">
    <h1>💬 Response to Your Feedback</h1>
    <p>Ticket {feedback.feedback_id} · {feedback.title[:55]}{'…' if len(feedback.title) > 55 else ''}</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.94em;">Hi <strong>{feedback.submitter_name}</strong>,<br><br>
    The NexGen support team has replied to your feedback ticket.</p>
    <table class="tbl">
      <tr><td>Feedback ID</td><td><strong>{feedback.feedback_id}</strong></td></tr>
      <tr><td>Your Title</td><td>{feedback.title}</td></tr>
      <tr><td>Replied By</td><td>{replier_name}</td></tr>
      <tr><td>Replied At</td><td>{reply.replied_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
    </table>
    <p style="font-weight:600;color:#1f2937;margin:20px 0 6px;">Response</p>
    <div class="msg-box" style="border-color:#7c3aed;">{reply.message.replace(chr(10), '<br>')}</div>
    <div class="cta">
      <a href="{portal_url}" style="background:linear-gradient(135deg,#7c3aed,#a78bfa);">
        🔗 View Full Conversation
      </a>
    </div>
    <p style="color:#6b7280;font-size:.85em;text-align:center;">
      You can reply directly from the NexGen Feedback Portal.</p>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    return subject, text_body, html_body


def _build_user_reply_email(reply):
    """HTML/text email to admins when the submitter posts a follow-up reply."""
    feedback = reply.feedback
    cat_icon = CATEGORY_ICONS.get(feedback.category, "📌")
    replier_name = feedback.submitter_name
    portal_url = f"{settings.SITE_URL}/feedback/inbox/"

    subject = (
        f"[NexGen Feedback] {cat_icon} Follow-up on {feedback.feedback_id} — "
        f"{feedback.title[:50]}{'…' if len(feedback.title) > 50 else ''}"
    )

    text_body = (
        f"{replier_name} has posted a follow-up on ticket {feedback.feedback_id}.\n\n"
        f"Message\n-------\n{reply.message}\n\n"
        f"View here: {portal_url}\n\n— NexGen Automation"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_common_css()}</style></head>
<body><div class="wrapper">
  <div class="hdr" style="background:linear-gradient(135deg,#0f766e,#14b8a6);">
    <h1>🔄 Follow-up on Feedback Ticket</h1>
    <p>{feedback.feedback_id} · {feedback.title[:55]}{'…' if len(feedback.title) > 55 else ''}</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.94em;">Hello,<br><br>
    <strong>{replier_name}</strong> ({feedback.submitter_role_label}) has added a follow-up to feedback ticket
    <strong>{feedback.feedback_id}</strong>.</p>
    <p style="font-weight:600;color:#1f2937;margin:20px 0 6px;">Follow-up Message</p>
    <div class="msg-box" style="border-color:#14b8a6;">{reply.message.replace(chr(10), '<br>')}</div>
    <div class="cta">
      <a href="{portal_url}" style="background:linear-gradient(135deg,#0f766e,#14b8a6);">
        📬 Open Feedback Inbox
      </a>
    </div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    return subject, text_body, html_body


def _build_status_email(feedback, changed_by):
    """Email to submitter when ticket status is updated."""
    cat_icon = CATEGORY_ICONS.get(feedback.category, "📌")
    st_bg, st_fg, st_label = STATUS_COLORS.get(feedback.status, ("#f3f4f6", "#374151", feedback.status))
    changer_name = changed_by.get_full_name() or changed_by.username if changed_by else "Support Team"
    portal_url = f"{settings.SITE_URL}/feedback/"

    subject = (
        f"[NexGen Feedback] {cat_icon} Ticket {feedback.feedback_id} is now "
        f"'{st_label}'"
    )

    text_body = (
        f"Hi {feedback.submitter_name},\n\n"
        f"Your feedback ticket ({feedback.feedback_id}) status has been updated.\n\n"
        f"New Status : {st_label}\n"
        f"Updated By : {changer_name}\n"
        f"Updated At : {feedback.updated_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
        f"View here: {portal_url}\n\n— NexGen Support Team"
    )

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_common_css()}</style></head>
<body><div class="wrapper">
  <div class="hdr" style="background:linear-gradient(135deg,#0476c8,#00b5e2);">
    <h1>🔔 Ticket Status Updated</h1>
    <p>{feedback.feedback_id} · {feedback.title[:55]}{'…' if len(feedback.title) > 55 else ''}</p>
  </div>
  <div class="body">
    <p style="color:#444;font-size:.94em;">Hi <strong>{feedback.submitter_name}</strong>,<br><br>
    Your feedback ticket status has been updated by the support team.</p>
    <table class="tbl">
      <tr><td>Feedback ID</td><td><strong>{feedback.feedback_id}</strong></td></tr>
      <tr><td>New Status</td><td>
        <span class="badge" style="background:{st_bg};color:{st_fg};">{st_label}</span>
      </td></tr>
      <tr><td>Updated By</td><td>{changer_name}</td></tr>
      <tr><td>Updated At</td><td>{feedback.updated_at.strftime('%d %b %Y, %I:%M %p')}</td></tr>
    </table>
    <div class="cta"><a href="{portal_url}">🔗 View Your Feedback</a></div>
  </div>
  <div class="ftr">NexGen Automation · Meril Life Sciences · This is an automated notification.</div>
</div></body></html>"""

    return subject, text_body, html_body


# ── Public API ──────────────────────────────────────────────────────────────

def notify_feedback_submitted(feedback):
    """
    Send escalation email to all superusers + admin emails when a new
    feedback ticket is submitted.
    """
    recipients = _admin_emails()
    if not recipients:
        logger.warning("notify_feedback_submitted: no admin recipients configured.")
        return

    subject, text_body, html_body = _build_submission_email(feedback)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@merillife.com")

    msg = EmailMultiAlternatives(subject, text_body, from_email, recipients)
    msg.attach_alternative(html_body, "text/html")

    # Attach screenshot inline if present
    if feedback.screenshot:
        try:
            feedback.screenshot.open("rb")
            img_data = feedback.screenshot.read()
            feedback.screenshot.close()
            msg.attach(
                f"screenshot_{feedback.feedback_id}.png",
                img_data,
                "image/png",
            )
        except Exception as exc:
            logger.warning("Could not attach screenshot: %s", exc)

    _send_async(msg)
    logger.info("Feedback submission email queued for %s", recipients)


def notify_feedback_reply(reply):
    """
    Called after an admin/superuser posts a reply.
    Emails the original submitter so they know there's a response.
    If a user posts a follow-up reply, emails the admin team instead.
    """
    feedback = reply.feedback
    replier = reply.replied_by
    submitter = feedback.submitted_by

    is_admin_reply = replier and (replier.is_superuser or replier.email in FEEDBACK_ADMIN_EMAILS)

    if is_admin_reply:
        # Send notification to the submitter
        if not submitter or not submitter.email:
            logger.info("notify_feedback_reply: submitter has no email — skipping.")
            return
        subject, text_body, html_body = _build_reply_email(reply)
        recipients = [submitter.email]
    else:
        # Submitter/user replied — notify admins
        subject, text_body, html_body = _build_user_reply_email(reply)
        recipients = _admin_emails()

    if not recipients:
        return

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@merillife.com")
    msg = EmailMultiAlternatives(subject, text_body, from_email, recipients)
    msg.attach_alternative(html_body, "text/html")
    _send_async(msg)
    logger.info("Feedback reply email queued for %s", recipients)


def notify_status_changed(feedback, changed_by):
    """
    Email submitter when the ticket status changes (resolved / closed etc.).
    Skip if submitter has no email or if the submitter made the change.
    """
    submitter = feedback.submitted_by
    if not submitter or not submitter.email:
        return
    if changed_by and changed_by == submitter:
        return  # Don't spam them if they changed their own status

    subject, text_body, html_body = _build_status_email(feedback, changed_by)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@merillife.com")
    msg = EmailMultiAlternatives(subject, text_body, from_email, [submitter.email])
    msg.attach_alternative(html_body, "text/html")
    _send_async(msg)
    logger.info("Status-change email queued for %s", submitter.email)
