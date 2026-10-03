import io
import logging
import re

from django.db import models
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from xhtml2pdf import pisa

from .models import VisaLetterTemplate

logger = logging.getLogger(__name__)
_PARTICULAR_RE = re.compile(r'^ {2,}(?P<label>[^:]+?) *: {2,}(?P<value>.*)$')

# Blank lines left between "For <company>" and the signatory name so the
# printed letter has room for a wet signature. Every seeded body (including
# STANDARD_LETTER_BODY, the is_default fallback) already carries exactly this
# many; normalize_letter_body enforces it for hand-edited bodies too.
SIGNING_BLANK_LINES = 4
_BLANK_LINE_HTML = '<p style="margin:0;">&nbsp;</p>'


def render_letter(application):
    """
    Find the best-matching letter template for (company, country, entry_type)
    and substitute all variables. Falls back to the default template (no
    company/country set) if no exact match exists. Returns the rendered
    string, or '' if neither exists.
    """
    entry_filter = models.Q(entry_type__in=[application.entry_type, 'both'])

    # Exact match first. A company+country pair may have several jurisdiction
    # variants (e.g. Delhi vs Mumbai) — the blank-jurisdiction row is the
    # primary/default one and always wins automatically; Visa-Dept can switch
    # to another jurisdiction variant manually from the letter detail page.
    tpl = VisaLetterTemplate.objects.filter(
        entry_filter,
        company=application.company,
        country=application.country,
        is_active=True,
    ).order_by(
        'jurisdiction',
        # prefer exact entry_type over 'both'
        'entry_type',
    ).first()

    # Fall back to the default template
    if tpl is None:
        tpl = VisaLetterTemplate.objects.filter(
            entry_filter,
            company__isnull=True,
            country__isnull=True,
            is_default=True,
            is_active=True,
        ).order_by('entry_type').first()

    if tpl is None:
        logger.warning(
            'Visa: no letter template (specific or default) found for company=%s country=%s entry_type=%s (app pk=%s)',
            application.company.code, application.country.name, application.entry_type, application.pk,
        )
        return ''

    return _render_template(tpl, application)


def render_letter_variant(application, template):
    """
    Render a specific VisaLetterTemplate (e.g. a non-default jurisdiction
    variant the applicant/Visa-Dept picked manually) rather than the
    automatically-selected one.
    """
    return _render_template(template, application)


def _render_template(template, application):
    ctx = _build_context(application)
    body = template.letter_body
    for key, value in ctx.items():
        body = body.replace('{{' + key + '}}', str(value) if value is not None else '')
    return body


def _particulars_table(rows):
    cells = ''.join(
        '<tr>'
        '<td style="padding:0 0 2pt 0;white-space:nowrap;">{label}</td>'
        '<td style="padding:0 6pt 2pt 6pt;">:</td>'
        '<td style="padding:0 0 2pt 0;">{value}</td>'
        '</tr>'.format(label=escape(label), value=escape(value))
        for label, value in rows
    )
    return '<table style="margin:0 0 11pt 18pt;border-collapse:collapse;">' + cells + '</table>'


def normalize_letter_body(text):
    """
    Trim the body and pin every run of 2+ blank lines to the standard signing
    gap of SIGNING_BLANK_LINES. Applied before rendering so a hand-edited or
    older letter gets the same signing space as a freshly seeded one, and so
    an accidentally huge run of blank lines can't push the letter onto a 2nd
    page.
    """
    return re.sub(
        r'\n{3,}',
        '\n' * (SIGNING_BLANK_LINES + 1),
        (text or '').strip(),
    )


def letter_to_html(text):
    """
    Convert the plain-text letter body to HTML for the print page and the PDF
    attachment. Blank-line-separated blocks become <p> paragraphs and single
    newlines become <br>.

    A run of more than one blank line is deliberate signing space: the first
    blank line is the ordinary paragraph gap (the <p> bottom margin), and each
    extra one becomes a real `&nbsp;` line so the space survives in the
    browser and in xhtml2pdf, neither of which honours empty markup.

    The indented "Label  :  value" passport-particulars lines become a real
    two-column <table> rather than space-padded text. The letter renders in
    Times New Roman, where a space is far narrower than an average character,
    so padding labels out to an equal *character count* never actually lines
    the colons up — it only looks aligned in a monospace code editor. A table
    aligns regardless of font metrics, and also sidesteps xhtml2pdf's
    unreliable `white-space: pre-wrap` support.
    """
    out = []
    for chunk in re.split(r'(\n{2,})', text):
        if not chunk:
            continue
        if chunk.startswith('\n\n'):
            out.extend([_BLANK_LINE_HTML] * (len(chunk) - 2))
            continue
        lines = chunk.split('\n')
        i = 0
        while i < len(lines):
            if _PARTICULAR_RE.match(lines[i]):
                rows = []
                while i < len(lines):
                    m = _PARTICULAR_RE.match(lines[i])
                    if not m:
                        break
                    rows.append((m.group('label').strip(), m.group('value').strip()))
                    i += 1
                out.append(_particulars_table(rows))
                continue
            j = i
            while j < len(lines) and not _PARTICULAR_RE.match(lines[j]):
                j += 1
            out.append(
                '<p style="margin:0 0 11pt 0;">'
                + '<br />'.join(escape(ln) for ln in lines[i:j])
                + '</p>'
            )
            i = j
    # Safe: every fragment in `out` was escape()d as it was appended.
    return mark_safe('\n'.join(out))  # nosec


def render_letter_pdf(application):
    """
    Render the already-generated letter (application.custom_letter_body) to a
    PDF for email attachment. Contains only the letter content — no Meril/
    NexGen header or footer branding — see templates/visa_management/email/letter_pdf.html.
    Returns PDF bytes, or None if there's no letter body to render.
    """
    letter_body = normalize_letter_body(application.custom_letter_body)
    if not letter_body:
        return None

    html = render_to_string('visa_management/email/letter_pdf.html', {
        'letter_body_html': letter_to_html(letter_body),
        'current_date': timezone.localdate(),
    })
    buf = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buf)
    if result.err:
        logger.error('Visa: failed to render letter PDF for application pk=%s', application.pk)
        return None
    return buf.getvalue()


def _build_context(app):
    from django.utils.formats import date_format

    def fmt(d):
        if d is None:
            return ''
        return date_format(d, 'd F Y')  # e.g. "15 January 2026"

    from django.utils import timezone
    company = app.company
    country = app.country

    return {
        'employee_name':       app.applicant_name,
        'mr_ms':               'Ms.' if app.gender == 'F' else 'Mr.',
        'designation':         app.designation,
        'joining_date':        fmt(app.joining_date),
        'he_she':              app.he_she,
        'his_her':             app.his_her,
        'him_her':             app.him_her,
        'visit_start_date':    fmt(app.visit_start_date),
        'visit_end_date':      fmt(app.visit_end_date),
        'host_company_name':   app.host_company_name,
        'host_company_address': app.host_company_address,
        'passport_name':       app.passport_name,
        'passport_number':     app.passport_number,
        'date_of_birth':       fmt(app.date_of_birth),
        'passport_issue_date': fmt(app.passport_issue_date),
        'passport_expiry_date': fmt(app.passport_expiry_date),
        'place_of_issue':      app.place_of_issue,
        'nationality':         app.nationality,
        'current_date':        fmt(timezone.localdate()),
        'company_name':        company.name,
        'company_description': company.description,
        'entry_type_text':     'Multiple Entry' if app.entry_type == 'multiple' else 'Single Entry',
        'country_name':        country.name,
        'embassy_name':        country.addressee_line2,
        'embassy_city':        country.embassy_city,
        'signatory_name':      company.signatory_name,
        'signatory_title':     company.signatory_title,
        'purpose_of_visit':    app.purpose_of_visit,
        'ref_no':              app.ref_no,
    }
