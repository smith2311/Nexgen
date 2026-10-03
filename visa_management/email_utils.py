import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def _site_url():
    """
    Base URL for every link in visa mail. Single source of truth is SITE_URL in
    .env (read by core.settings) — no hardcoded fallback here, so a missing or
    wrong value is visible in the mail rather than silently masked by a default.
    """
    return settings.SITE_URL


def _from_email():
    """
    Visa mail is sent from the SCM digital support mailbox, not the site-wide
    DEFAULT_FROM_EMAIL (noreply@), so recipients can reply and reach a person.
    Applies to this module only — other modules keep DEFAULT_FROM_EMAIL.
    """
    return getattr(settings, 'VISA_FROM_EMAIL', settings.DEFAULT_FROM_EMAIL)


def _visa_dept_recipients():
    """
    Everyone who should be told when an application reaches the Visa Department.

    Primary source is the Visa-Department group (managed from the User Master
    page), so adding a member there is enough to put them on the notification
    list. VISA_DEPT_EMAILS is still honoured on top for shared mailboxes or
    people without an account.
    """
    from django.contrib.auth import get_user_model

    emails = {
        e.strip().lower()
        for e in get_user_model().objects
        .filter(groups__name='Visa-Department', is_active=True)
        .values_list('email', flat=True)
        if e and e.strip()
    }
    emails.update(
        e.strip().lower()
        for e in getattr(settings, 'VISA_DEPT_EMAILS', [])
        if e and e.strip()
    )
    return sorted(emails)


def _send_html(subject, template_name, context, to_list, attachments=None):
    """Render an HTML email (with an auto-derived plain-text alternative) and send it."""
    if not to_list:
        # Logged rather than silently dropped — an empty recipient list is how
        # the Visa-Department notification went unnoticed for so long.
        logger.warning('Visa email "%s" not sent: no recipients (%s)', subject, template_name)
        return
    try:
        html_body = render_to_string(template_name, context)
        email = EmailMultiAlternatives(
            subject=subject,
            body=strip_tags(html_body),
            from_email=_from_email(),
            to=to_list,
            reply_to=[_from_email()],
        )
        email.attach_alternative(html_body, 'text/html')
        for filename, content, mimetype in attachments or []:
            email.attach(filename, content, mimetype)
        email.send()
    except Exception as exc:
        logger.error('Visa email failed to %s: %s', to_list, exc)


def send_hod_approval_request(application, is_new_account=False):
    """Email to HOD when employee submits application."""
    _send_html(
        subject=f'Visa Application — Approval Required: {application.applicant_name} [{application.ref_no}]',
        template_name='visa_management/email/hod_approval_request.html',
        context={
            'app': application,
            'review_url': f'{_site_url()}/visa/hod/',
            'is_new_account': is_new_account,
        },
        to_list=[application.hod_email],
    )


def send_submission_confirmation_to_applicant(application):
    """Email to employee confirming their application was submitted."""
    _send_html(
        subject=f'Application Submitted — {application.ref_no}',
        template_name='visa_management/email/submission_confirmation_applicant.html',
        context={
            'app': application,
            'view_url': f'{_site_url()}/visa/application/{application.pk}/',
        },
        to_list=[application.applicant_email],
    )


def send_hod_approved_to_visa_dept(application):
    """Email to Visa Dept after HOD approves."""
    _send_html(
        subject=(
            f'[{application.ref_no}] {application.applicant_name} — visa request '
            f'approved by {application.hod_name or "reporting head"}, ready to process'
        ),
        template_name='visa_management/email/hod_approved_visa_dept.html',
        context={
            'app': application,
            'review_url': f'{_site_url()}/visa/admin/application/{application.pk}/',
        },
        to_list=_visa_dept_recipients(),
    )


def send_hod_approved_to_applicant(application):
    """Email to employee after HOD approval."""
    _send_html(
        subject=f'Your visa application has been approved by your HOD [{application.ref_no}]',
        template_name='visa_management/email/hod_approved_applicant.html',
        context={'app': application},
        to_list=[application.applicant_email],
    )


def send_verify_details_to_applicant(application, pdf_bytes=None):
    """Email to employee after HOD approval asking them to verify their submitted
    data, with a PDF copy of the draft invitation letter attached (if available)."""
    attachments = None
    if pdf_bytes:
        attachments = [(f'Invitation_Letter_{application.ref_no}.pdf', pdf_bytes, 'application/pdf')]
    _send_html(
        subject=f'Please verify your details — {application.ref_no}',
        template_name='visa_management/email/verify_details_applicant.html',
        context={
            'app': application,
            'has_attachment': bool(pdf_bytes),
        },
        to_list=[application.applicant_email],
        attachments=attachments,
    )


def send_hod_rejected_to_applicant(application):
    """Email to employee when HOD rejects."""
    _send_html(
        subject=f'Your visa application was not approved — [{application.ref_no}]',
        template_name='visa_management/email/hod_rejected_applicant.html',
        context={
            'app': application,
            'apply_url': f'{_site_url()}/visa/apply/',
        },
        to_list=[application.applicant_email],
    )


def send_issued_to_applicant(application):
    """Email to employee when Visa Dept issues the letter."""
    _send_html(
        subject=f'[{application.ref_no}] Your visa invitation letter is ready',
        template_name='visa_management/email/issued_applicant.html',
        context={'app': application},
        to_list=[application.applicant_email],
    )


def send_rejected_to_applicant(application):
    """Email to employee when Visa Dept rejects."""
    _send_html(
        subject=f'Your visa application could not be processed — [{application.ref_no}]',
        template_name='visa_management/email/rejected_applicant.html',
        context={
            'app': application,
            'apply_url': f'{_site_url()}/visa/apply/',
        },
        to_list=[application.applicant_email],
    )
