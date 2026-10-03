import io
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ui.decorators import uses_components

from .decorators import _visa_role, visa_login_required, visa_require_role
from .email_utils import (
    send_hod_approval_request,
    send_hod_approved_to_applicant,
    send_hod_approved_to_visa_dept,
    send_hod_rejected_to_applicant,
    send_issued_to_applicant,
    send_rejected_to_applicant,
    send_submission_confirmation_to_applicant,
    send_verify_details_to_applicant,
)
from .letter_renderer import (
    letter_to_html,
    normalize_letter_body,
    render_letter,
    render_letter_pdf,
    render_letter_variant,
)
from .models import VisaApplication, VisaApplicationLog, VisaCompany, VisaCountry, VisaLetterTemplate

User = get_user_model()

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_TRUTHY = frozenset({'y', 'yes', 'true', '1', 'x'})


# ── Helpers ────────────────────────────────────────────────────────────────

def _unique_username(email):
    username = email.split('@')[0]
    base, counter = username, 1
    while User.objects.filter(username=username).exists():
        username = f'{base}{counter}'
        counter += 1
    return username


def _header_key(val):
    return str(val or '').strip().lower().replace(' ', '_').replace('/', '_')


def _style_header(ws, headers, color='0B1F3A'):
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(fill_type='solid', fgColor=color)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[cell.column_letter].width = max(len(h) + 6, 16)
    ws.row_dimensions[1].height = 22


def _process_visa_user_excel(fileobj, dept_grp, hod_grp):
    """
    Bulk-assign visa roles from an uploaded sheet. Mirrors
    pr_tracker.views._process_user_excel. Accepts either Y/N columns
    ("Department" / "HOD") or a single free-text "Role" column.
    """
    wb = load_workbook(fileobj, data_only=True)
    ws = wb.active
    headers = [_header_key(c.value) for c in ws[1]]
    results, errors = [], 0

    email_col = next(
        (i for i, h in enumerate(headers) if h in ('email', 'email_id', 'user_email')), None
    )
    if email_col is None:
        raise ValueError('Column "Email" not found. Please use the provided template.')

    first_name_col = headers.index('first_name') if 'first_name' in headers else None
    last_name_col = headers.index('last_name') if 'last_name' in headers else None
    dept_col = headers.index('department') if 'department' in headers else None
    hod_col = headers.index('hod') if 'hod' in headers else None
    role_col = headers.index('role') if 'role' in headers else None

    def truthy(v):
        return str(v or '').strip().lower() in _TRUTHY

    for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        email = str(row[email_col] or '').strip().lower()
        if not email:
            continue
        if not _EMAIL_RE.match(email):
            results.append({'row': row_num, 'email': email, 'status': 'error',
                            'msg': 'Invalid email address'})
            errors += 1
            continue

        first_name = str(row[first_name_col] or '').strip() if first_name_col is not None else ''
        last_name = str(row[last_name_col] or '').strip() if last_name_col is not None else ''

        wants_dept = truthy(row[dept_col]) if dept_col is not None else False
        wants_hod = truthy(row[hod_col]) if hod_col is not None else False
        if role_col is not None:
            rv = str(row[role_col] or '').strip().lower()
            if rv in ('department', 'visa-department', 'visa department'):
                wants_dept = True
            elif rv in ('hod', 'visa-hod', 'visa hod'):
                wants_hod = True
            elif rv == 'both':
                wants_dept = wants_hod = True

        user = User.objects.filter(email__iexact=email).first()
        created = False
        if user is None:
            user = User.objects.create(
                username=_unique_username(email), email=email,
                first_name=first_name, last_name=last_name, is_active=True,
            )
            # No password — claimed on first Zoho sign-in via
            # main.pipeline.associate_by_email, which preserves these groups.
            user.set_unusable_password()
            user.save()
            created = True
        else:
            if first_name:
                user.first_name = first_name
            if last_name:
                user.last_name = last_name
            user.save()

        # Independent ifs (not elif) so one row can grant both roles.
        user.groups.remove(dept_grp, hod_grp)
        if wants_dept:
            user.groups.add(dept_grp)
        if wants_hod:
            user.groups.add(hod_grp)

        role_label = (
            'Department & HOD' if (wants_dept and wants_hod)
            else 'Department' if wants_dept
            else 'HOD' if wants_hod
            else 'Applicant (no role)'
        )
        results.append({'row': row_num, 'email': email,
                        'status': 'Created' if created else 'Updated', 'msg': role_label})

    return results, errors


def _log(application, action, user, notes=''):
    VisaApplicationLog.objects.create(
        application=application,
        action=action,
        performed_by=user,
        notes=notes,
    )


def _get_or_create_hod_user(name, email):
    """
    Return (user, is_new) for the HOD identified by email, creating a stub
    Django account if none exists yet. The stub has no usable password —
    when this HOD first signs in via Zoho SSO, main.pipeline.associate_by_email
    finds this pre-provisioned account by email and links to it automatically,
    preserving the Visa-HOD group membership assigned here.
    """
    group, _ = Group.objects.get_or_create(name='Visa-HOD')
    user = User.objects.filter(email__iexact=email).first()
    is_new = False
    if user is None:
        first_name, _, last_name = name.strip().partition(' ')
        user = User.objects.create(
            username=email,
            email=email,
            first_name=first_name,
            last_name=last_name,
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        is_new = True
    user.groups.add(group)
    return user, is_new


# ── Home ────────────────────────────────────────────────────────────────────

@visa_login_required
@uses_components("data_table", "master_page")
def visa_home(request):
    return render(request, 'visa_management/home.html', {
        'visa_role': _visa_role(request.user),
        'is_superuser': request.user.is_superuser,
    })


# ── Employee views ──────────────────────────────────────────────────────────

@visa_login_required
@uses_components("data_table", "master_page")
def my_applications(request):
    apps = VisaApplication.objects.filter(applicant=request.user).select_related('country', 'company')
    return render(request, 'visa_management/my_applications.html', {
        'applications': apps,
        'role': _visa_role(request.user),
    })


@visa_login_required
@uses_components("data_table", "master_page")
def application_detail(request, pk):
    app = get_object_or_404(VisaApplication, pk=pk)
    role = _visa_role(request.user)
    # Applicants can only see their own
    if role == 'applicant' and app.applicant != request.user:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()
    return render(request, 'visa_management/application_detail.html', {
        'app': app,
        'logs': app.logs.select_related('performed_by'),
        'role': role,
    })


@visa_login_required
@uses_components("data_table", "master_page")
def apply_new(request):
    companies = VisaCompany.objects.filter(is_active=True)
    countries = VisaCountry.objects.filter(is_active=True)

    if request.method == 'POST':
        return _save_application(request, None, companies, countries)

    return render(request, 'visa_management/apply.html', {
        'companies': companies,
        'countries': countries,
        'app': None,
    })


@visa_login_required
@uses_components("data_table", "master_page")
def apply_edit(request, pk):
    app = get_object_or_404(VisaApplication, pk=pk, applicant=request.user)
    if app.status not in ('draft',):
        messages.error(request, 'This application can no longer be edited.')
        return redirect('visa:application_detail', pk=pk)

    companies = VisaCompany.objects.filter(is_active=True)
    countries = VisaCountry.objects.filter(is_active=True)

    if request.method == 'POST':
        return _save_application(request, app, companies, countries)

    return render(request, 'visa_management/apply.html', {
        'companies': companies,
        'countries': countries,
        'app': app,
    })


def _save_application(request, app, companies, countries):
    """Shared save logic for apply_new and apply_edit."""
    p = request.POST
    action = p.get('action', 'draft')  # 'draft' or 'submit'
    errors = []

    # Validate
    hod_email = p.get('hod_email', '').strip()
    if hod_email and hod_email == request.user.email:
        errors.append('You cannot be your own reporting head.')

    if errors:
        companies = VisaCompany.objects.filter(is_active=True)
        countries = VisaCountry.objects.filter(is_active=True)
        messages.error(request, errors[0])
        return render(request, 'visa_management/apply.html', {
            'companies': companies, 'countries': countries, 'app': app, 'post': p,
        })

    try:
        company = VisaCompany.objects.get(pk=p['company'])
        country = VisaCountry.objects.get(pk=p['country'])
    except (VisaCompany.DoesNotExist, VisaCountry.DoesNotExist, KeyError):
        messages.error(request, 'Invalid company or country selection.')
        return redirect('visa:apply_new')

    if app is None:
        app = VisaApplication(applicant=request.user)

    app.applicant_name = p.get('applicant_name', '').strip()
    app.applicant_email = request.user.email
    app.designation = p.get('designation', '').strip()
    app.company = company
    app.joining_date = p.get('joining_date') or None
    app.gender = p.get('gender', 'M')
    app.hod_name = p.get('hod_name', '').strip()
    app.hod_email = hod_email
    app.country = country
    app.visit_start_date = p.get('visit_start_date') or None
    app.visit_end_date = p.get('visit_end_date') or None
    app.entry_type = p.get('entry_type', 'multiple')
    app.host_company_name = p.get('host_company_name', '').strip()
    app.host_company_address = p.get('host_company_address', '').strip()
    app.purpose_of_visit = p.get('purpose_of_visit', '').strip()
    app.applicant_remark = p.get('applicant_remark', '').strip()
    # Title Case: passports are often filled in all-caps or all-lowercase, and
    # the invitation letter reads as a formal name, so normalise every word to
    # a leading capital. Mirrored as a blur handler in visa_apply.js.
    app.passport_name = p.get('passport_name', '').strip().title()
    app.passport_number = p.get('passport_number', '').strip().upper()
    app.date_of_birth = p.get('date_of_birth') or None
    app.passport_issue_date = p.get('passport_issue_date') or None
    app.passport_expiry_date = p.get('passport_expiry_date') or None
    app.place_of_issue = p.get('place_of_issue', '').strip()
    app.nationality = p.get('nationality', 'INDIAN').strip().upper()

    if action == 'submit':
        app.status = 'pending_hod'

    app.save()

    # The date fields above are assigned raw POST strings ('2026-07-25'). Django
    # coerces them on save, but this in-memory instance keeps the strings — and
    # the |date: filter renders an empty string for a str, so every date in the
    # submit-time emails came out blank. Reload so the fields are real date
    # objects before the templates touch them.
    app.refresh_from_db()

    if action == 'submit':
        _log(app, 'submitted', request.user, app.applicant_remark)
        hod_user, hod_is_new = _get_or_create_hod_user(app.hod_name, app.hod_email)
        send_hod_approval_request(app, is_new_account=hod_is_new)
        send_submission_confirmation_to_applicant(app)
        messages.success(request, f'Application {app.ref_no} submitted. Approval request sent to {app.hod_name}.')
        return redirect('visa:application_detail', pk=app.pk)

    messages.success(request, f'Application saved as draft ({app.ref_no}).')
    return redirect('visa:apply_edit', pk=app.pk)


# ── HOD views ───────────────────────────────────────────────────────────────

@visa_login_required
@uses_components("data_table", "master_page")
def hod_dashboard(request):
    role = _visa_role(request.user)
    email = request.user.email

    # Auto-assign HOD group if user's email matches any pending application
    if VisaApplication.objects.filter(hod_email=email).exists():
        group, _ = Group.objects.get_or_create(name='Visa-HOD')
        request.user.groups.add(group)

    if role == 'admin':
        # Superuser / Visa-Department can see all pending HOD requests
        pending = VisaApplication.objects.filter(
            status='pending_hod'
        ).select_related('country', 'company', 'applicant')
        actioned = VisaApplication.objects.filter(
            status__in=['hod_approved', 'hod_rejected', 'processing', 'issued', 'rejected']
        ).select_related('country', 'company')[:20]
    else:
        pending = VisaApplication.objects.filter(
            hod_email=email, status='pending_hod'
        ).select_related('country', 'company')
        actioned = VisaApplication.objects.filter(
            hod_email=email, status__in=['hod_approved', 'hod_rejected', 'processing', 'issued', 'rejected']
        ).select_related('country', 'company')[:20]

    return render(request, 'visa_management/hod_dashboard.html', {
        'pending': pending,
        'actioned': actioned,
        'role': role,
    })


@visa_login_required
@uses_components("data_table", "master_page", "modal")
def hod_review(request, pk):
    role = _visa_role(request.user)
    if role == 'admin':
        app = get_object_or_404(VisaApplication, pk=pk)
    else:
        app = get_object_or_404(VisaApplication, pk=pk, hod_email=request.user.email)
    return render(request, 'visa_management/hod_review.html', {
        'app': app,
        'logs': app.logs.select_related('performed_by'),
        'role': role,
    })


@visa_login_required
@require_POST
def hod_approve(request, pk):
    role = _visa_role(request.user)
    if role == 'admin':
        app = get_object_or_404(VisaApplication, pk=pk, status='pending_hod')
    else:
        app = get_object_or_404(VisaApplication, pk=pk, hod_email=request.user.email, status='pending_hod')
    remark = request.POST.get('remark', '').strip()
    app.status = 'hod_approved'
    app.hod_actioned_at = timezone.now()
    app.hod_remark = remark
    app.custom_letter_body = render_letter(app)
    app.save()
    _log(app, 'hod_approved', request.user, remark)
    send_hod_approved_to_visa_dept(app)
    send_hod_approved_to_applicant(app)
    pdf_bytes = render_letter_pdf(app)
    send_verify_details_to_applicant(app, pdf_bytes)
    messages.success(request, f'{app.ref_no} approved and forwarded to Visa Department.')
    return redirect('visa:hod_dashboard')


@visa_login_required
@require_POST
def hod_reject(request, pk):
    role = _visa_role(request.user)
    if role == 'admin':
        app = get_object_or_404(VisaApplication, pk=pk, status='pending_hod')
    else:
        app = get_object_or_404(VisaApplication, pk=pk, hod_email=request.user.email, status='pending_hod')
    reason = request.POST.get('reason', '').strip()
    remark = request.POST.get('remark', '').strip()
    if not reason:
        messages.error(request, 'A rejection reason is required.')
        return redirect('visa:hod_review', pk=pk)
    app.status = 'hod_rejected'
    app.hod_actioned_at = timezone.now()
    app.hod_rejection_reason = reason
    app.hod_remark = remark
    app.save()
    _log(app, 'hod_rejected', request.user, f'{reason}\n\nRemark: {remark}' if remark else reason)
    send_hod_rejected_to_applicant(app)
    messages.success(request, f'{app.ref_no} rejected. Employee has been notified.')
    return redirect('visa:hod_dashboard')


# ── Visa Department views ────────────────────────────────────────────────────

@visa_require_role('admin')
@uses_components("data_table", "master_page")
def admin_dashboard(request):
    # Visa-Department (and superadmin) should only ever see requests the HOD
    # has already acted on — not-yet-approved/rejected requests stay HOD-only.
    qs = VisaApplication.objects.exclude(
        status__in=['draft', 'pending_hod']
    ).select_related('country', 'company', 'applicant')

    # Filters
    status_filter = request.GET.get('status', '')
    company_filter = request.GET.get('company', '')
    country_filter = request.GET.get('country', '')

    if status_filter:
        qs = qs.filter(status=status_filter)
    if company_filter:
        qs = qs.filter(company_id=company_filter)
    if country_filter:
        qs = qs.filter(country_id=country_filter)

    stats = {
        'total':      VisaApplication.objects.count(),
        'pending_hod': VisaApplication.objects.filter(status='pending_hod').count(),
        'hod_approved': VisaApplication.objects.filter(status='hod_approved').count(),
        'issued':     VisaApplication.objects.filter(status='issued').count(),
    }

    visible_status_choices = [
        c for c in VisaApplication.STATUS_CHOICES if c[0] not in ('draft', 'pending_hod')
    ]

    return render(request, 'visa_management/admin_dashboard.html', {
        'applications': qs[:100],
        'stats': stats,
        'companies': VisaCompany.objects.filter(is_active=True),
        'countries': VisaCountry.objects.filter(is_active=True),
        'status_choices': visible_status_choices,
        'filters': {'status': status_filter, 'company': company_filter, 'country': country_filter},
        'role': 'admin',
        'is_superuser': request.user.is_superuser,
    })


@visa_require_role('admin')
@uses_components("data_table", "master_page", "modal")
def admin_detail(request, pk):
    app = get_object_or_404(VisaApplication, pk=pk)
    no_template = app.status == 'hod_approved' and not app.custom_letter_body
    jurisdiction_variants = VisaLetterTemplate.objects.filter(
        company=app.company, country=app.country, is_active=True,
    ).order_by('jurisdiction')
    return render(request, 'visa_management/admin_detail.html', {
        'app': app,
        'logs': app.logs.select_related('performed_by'),
        'no_template': no_template,
        'role': 'admin',
        'is_superuser': request.user.is_superuser,
        'jurisdiction_variants': jurisdiction_variants if jurisdiction_variants.count() > 1 else None,
    })


@visa_require_role('admin')
@require_POST
def admin_save_letter(request, pk):
    app = get_object_or_404(VisaApplication, pk=pk)
    remark = request.POST.get('remark', '').strip()
    app.custom_letter_body = request.POST.get('letter_body', '')
    if remark:
        app.visa_dept_remark = remark
    app.save()
    _log(app, 'letter_edited', request.user, remark)
    messages.success(request, 'Letter saved.')
    return redirect('visa:admin_detail', pk=pk)


@visa_require_role('admin')
@require_POST
def admin_switch_letter_jurisdiction(request, pk, template_pk):
    app = get_object_or_404(VisaApplication, pk=pk)
    template = get_object_or_404(
        VisaLetterTemplate, pk=template_pk, company=app.company, country=app.country,
    )
    app.custom_letter_body = render_letter_variant(app, template)
    app.save()
    label = template.jurisdiction or 'default'
    _log(app, 'letter_edited', request.user, f'Switched to "{label}" jurisdiction letter')
    messages.success(request, f'Letter switched to the "{label}" jurisdiction version.')
    return redirect('visa:admin_detail', pk=pk)


@visa_require_role('admin')
def admin_print(request, pk):
    app = get_object_or_404(VisaApplication, pk=pk)
    # Pin every blank-line run to the standard 4-line signing gap, so the
    # printed letter always leaves room for a signature without extra spacing
    # left in the body pushing it onto a 2nd page.
    letter_body = normalize_letter_body(app.custom_letter_body)
    return render(request, 'visa_management/admin_print.html', {
        'app': app,
        'letter_body_html': letter_to_html(letter_body),
    })


@visa_require_role('admin')
@require_POST
def admin_approve(request, pk):
    app = get_object_or_404(VisaApplication, pk=pk)
    remark = request.POST.get('remark', '').strip()
    app.status = 'issued'
    app.processed_at = timezone.now()
    app.visa_dept_remark = remark
    app.save()
    _log(app, 'issued', request.user, remark)
    send_issued_to_applicant(app)
    messages.success(request, f'{app.ref_no} marked as issued. Employee notified.')
    return redirect('visa:admin_dashboard')


@visa_require_role('admin')
@require_POST
def admin_reject(request, pk):
    app = get_object_or_404(VisaApplication, pk=pk)
    reason = request.POST.get('reason', '').strip()
    remark = request.POST.get('remark', '').strip()
    if not reason:
        messages.error(request, 'A rejection reason is required.')
        return redirect('visa:admin_detail', pk=pk)
    app.status = 'rejected'
    app.visa_dept_notes = reason
    app.visa_dept_remark = remark
    app.processed_at = timezone.now()
    app.save()
    _log(app, 'rejected', request.user, f'{reason}\n\nRemark: {remark}' if remark else reason)
    send_rejected_to_applicant(app)
    messages.success(request, f'{app.ref_no} rejected. Employee notified.')
    return redirect('visa:admin_dashboard')


# ── Letter Template management (superuser-only) ──────────────────────────────

@visa_require_role('admin')
@uses_components("data_table", "master_page")
def template_list(request):
    templates = VisaLetterTemplate.objects.select_related('company', 'country')
    return render(request, 'visa_management/template_list.html', {
        'templates': templates,
        'companies': VisaCompany.objects.filter(is_active=True),
        'countries': VisaCountry.objects.filter(is_active=True),
    })


@visa_require_role('admin')
@uses_components("data_table", "master_page")
def template_create(request):
    companies = VisaCompany.objects.filter(is_active=True)
    countries = VisaCountry.objects.filter(is_active=True)
    if request.method == 'POST':
        return _save_template(request, None, companies, countries)
    return render(request, 'visa_management/template_edit.html', {
        'tpl': None, 'companies': companies, 'countries': countries,
    })


@visa_require_role('admin')
@uses_components("data_table", "master_page")
def template_edit(request, pk):
    tpl = get_object_or_404(VisaLetterTemplate, pk=pk)
    companies = VisaCompany.objects.filter(is_active=True)
    countries = VisaCountry.objects.filter(is_active=True)
    if request.method == 'POST':
        return _save_template(request, tpl, companies, countries)
    return render(request, 'visa_management/template_edit.html', {
        'tpl': tpl, 'companies': companies, 'countries': countries,
    })


def _save_template(request, tpl, companies, countries):
    p = request.POST
    is_default = p.get('is_default') == 'on'

    company = country = None
    if not is_default:
        try:
            company = VisaCompany.objects.get(pk=p['company'])
            country = VisaCountry.objects.get(pk=p['country'])
        except (VisaCompany.DoesNotExist, VisaCountry.DoesNotExist, KeyError):
            messages.error(request, 'Invalid company or country.')
            return redirect('visa:template_list')

    if tpl is None:
        tpl = VisaLetterTemplate()
    tpl.company = company
    tpl.country = country
    tpl.is_default = is_default
    tpl.entry_type = p.get('entry_type', 'both')
    tpl.subject_line = p.get('subject_line', '').strip()
    tpl.letter_body = p.get('letter_body', '').strip()
    tpl.is_active = p.get('is_active') == 'on'
    tpl.save()
    messages.success(request, 'Letter template saved.')
    return redirect('visa:template_list')


# ── Country master (superuser-only) ───────────────────────────────────────────

@visa_require_role('admin')
@uses_components("data_table", "master_page")
def country_list(request):
    countries = VisaCountry.objects.all()
    return render(request, 'visa_management/country_list.html', {
        'countries': countries,
    })


@visa_require_role('admin')
@uses_components("data_table", "master_page")
def country_create(request):
    if request.method == 'POST':
        return _save_country(request, None)
    return render(request, 'visa_management/country_edit.html', {'country': None})


@visa_require_role('admin')
@uses_components("data_table", "master_page")
def country_edit(request, pk):
    country = get_object_or_404(VisaCountry, pk=pk)
    if request.method == 'POST':
        return _save_country(request, country)
    return render(request, 'visa_management/country_edit.html', {'country': country})


def _save_country(request, country):
    p = request.POST
    name = p.get('name', '').strip()
    code = p.get('code', '').strip().upper()
    if not name or not code:
        messages.error(request, 'Country name and code are required.')
        return redirect('visa:country_list')
    if country is None:
        country = VisaCountry()
    country.name = name
    country.code = code
    country.default_entry_type = p.get('default_entry_type', 'multiple')
    country.addressee_line1 = p.get('addressee_line1', 'The Visa Officer,').strip()
    country.addressee_line2 = p.get('addressee_line2', '').strip()
    country.embassy_city = p.get('embassy_city', '').strip()
    country.is_active = p.get('is_active') == 'on'
    try:
        country.save()
        messages.success(request, f'Country "{country.name}" saved.')
    except Exception as e:
        messages.error(request, f'Could not save: {e}')
    return redirect('visa:country_list')


# ── User Master ───────────────────────────────────────────────────────────────
#
# Mirrors pr_tracker.views.user_master: one page that lists users, lets you set
# their visa role inline, and adds a user by email (creating the account if it
# does not exist yet, exactly as _get_or_create_hod_user does at submit time).
#
# NOTE ON PRIVILEGE: unlike PR Tracker — where PR-Admin grants PR-Manager /
# PR-Member and cannot grant itself — 'admin' here IS Visa-Department. So a
# Visa-Department member can grant Visa-Department to anyone, including
# promoting a colleague. That is deliberate (it matches the requested
# behaviour), but it means adding someone to Visa-Department also hands them
# the ability to hand that out further.

@visa_require_role('admin')
@uses_components("data_table", "master_page")
def user_master(request):
    dept_grp, _ = Group.objects.get_or_create(name='Visa-Department')
    hod_grp, _ = Group.objects.get_or_create(name='Visa-HOD')

    upload_results = None

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add_user':
            email = request.POST.get('email', '').strip().lower()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            role = request.POST.get('role', '')

            if not email:
                messages.error(request, 'Email is required.')
            elif not _EMAIL_RE.match(email):
                messages.error(request, 'Enter a valid email address.')
            else:
                user = User.objects.filter(email__iexact=email).first()
                created = False
                if user is None:
                    user = User.objects.create(
                        username=_unique_username(email),
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                        is_active=True,
                    )
                    # No password — the account is claimed on first Zoho SSO
                    # login via main.pipeline.associate_by_email, which matches
                    # on email and preserves the groups assigned here.
                    user.set_unusable_password()
                    user.save()
                    created = True
                else:
                    if first_name:
                        user.first_name = first_name
                    if last_name:
                        user.last_name = last_name
                    user.save()

                # Set the role rather than accumulate it.
                user.groups.remove(dept_grp, hod_grp)
                if role in ('department', 'both'):
                    user.groups.add(dept_grp)
                if role in ('hod', 'both'):
                    user.groups.add(hod_grp)

                verb = 'created' if created else 'updated'
                role_desc = {
                    'department': 'Visa Department',
                    'hod': 'Visa HOD',
                    'both': 'Visa Department & HOD',
                }.get(role, 'no visa role')
                messages.success(
                    request,
                    f'User "{user.get_full_name() or user.username}" {verb} with {role_desc}.',
                )
            return redirect('visa:user_master')

        if action == 'import_users':
            f = request.FILES.get('excel_file')
            if not f:
                messages.error(request, 'Please select an Excel file.')
            else:
                try:
                    results, errors = _process_visa_user_excel(f, dept_grp, hod_grp)
                    upload_results = results
                    created = sum(1 for r in results if r['status'] == 'Created')
                    updated = sum(1 for r in results if r['status'] == 'Updated')
                    msg = f'{created} user(s) created, {updated} updated'
                    if errors:
                        messages.warning(request, f'{msg}, {errors} error(s)')
                    else:
                        messages.success(request, msg + '.')
                except Exception as e:
                    messages.error(request, f'Error reading file: {e}')
            # Fall through so the results table renders instead of redirecting.

        elif action in ('make_department', 'make_hod', 'make_both', 'remove'):
            target = get_object_or_404(User, pk=request.POST.get('user_id'))
            name = target.get_full_name() or target.username

            if action == 'make_department':
                target.groups.add(dept_grp)
                messages.success(request, f'{name} added to Visa Department.')
            elif action == 'make_hod':
                target.groups.add(hod_grp)
                messages.success(request, f'{name} added as Visa HOD.')
            elif action == 'make_both':
                target.groups.add(dept_grp, hod_grp)
                messages.success(request, f'{name} given both Visa Department and HOD access.')
            elif action == 'remove':
                target.groups.remove(dept_grp, hod_grp)
                messages.success(request, f'{name} removed from visa roles (now an applicant).')
            return redirect('visa:user_master')

    search = request.GET.get('q', '').strip()
    users = User.objects.filter(is_active=True).prefetch_related('groups').order_by('first_name', 'username')
    if search:
        from django.db.models import Q
        users = users.filter(
            Q(first_name__icontains=search) | Q(last_name__icontains=search)
            | Q(username__icontains=search) | Q(email__icontains=search)
        )

    dept_ids = set(dept_grp.user_set.values_list('id', flat=True))
    hod_ids = set(hod_grp.user_set.values_list('id', flat=True))

    # Emails named as reporting head on at least one application — surfaces
    # people who need Visa-HOD but were never granted it, which silently
    # blocks the applications waiting on them.
    hod_emails = {
        e.lower() for e in
        VisaApplication.objects.exclude(hod_email='').values_list('hod_email', flat=True)
    }

    users_data = []
    for u in users:
        is_dept, is_hod = u.pk in dept_ids, u.pk in hod_ids
        if is_dept and is_hod:
            role = 'both'
        elif is_dept:
            role = 'department'
        elif is_hod:
            role = 'hod'
        else:
            role = 'none'
        users_data.append({
            'user': u,
            'role': role,
            # True when they are an application's HOD but lack the group.
            'needs_hod': (u.email or '').lower() in hod_emails and not is_hod,
        })

    return render(request, 'visa_management/user_master.html', {
        'users_data': users_data,
        'search': search,
        'upload_results': upload_results,
        'dept_count': len(dept_ids),
        'hod_count': len(hod_ids),
        'needs_hod_count': sum(1 for d in users_data if d['needs_hod']),
        'role': 'admin',
        'is_superuser': request.user.is_superuser,
    })


@visa_require_role('admin')
def user_template_download(request):
    """Blank Excel template for the User Master bulk upload."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'Visa Users'
    headers = ['First Name', 'Last Name', 'Email', 'Department', 'HOD']
    _style_header(ws, headers)
    ws.append(['Jane', 'Doe', 'jane.doe@merillife.com', 'Y', 'N'])
    ws.append(['John', 'Smith', 'john.smith@merillife.com', 'N', 'Y'])
    ws.append(['Asha', 'Rao', 'asha.rao@merillife.com', 'Y', 'Y'])
    for r in (2, 3, 4):
        for c in range(1, 6):
            ws.cell(r, c).font = Font(italic=True, color='888888')

    note = ws.max_row + 2
    ws.cell(row=note, column=1, value='Notes:').font = Font(bold=True, color='374151')
    for offset, text in enumerate((
        'Use Y/N (or Yes/No, True/False, 1/0) to set roles. The sample rows above are examples — delete them.',
        'Department = Visa Department (processes applications, manages master data).',
        'HOD = reporting head who approves or rejects their team\'s applications.',
        'Mark both as Y to give a user both roles. Leave both blank to remove all visa roles.',
        'If the email has no account yet, one is created and claimed on their first Zoho sign-in.',
    ), start=1):
        ws.cell(row=note + offset, column=1, value=text).font = Font(color='6B7280', italic=True)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="Visa_Users_Template.xlsx"'
    return resp


# ── Company master (superuser-only) ───────────────────────────────────────────

@visa_require_role('admin')
@uses_components("data_table", "master_page")
def company_list(request):
    companies = VisaCompany.objects.all()
    return render(request, 'visa_management/company_list.html', {
        'companies': companies,
    })


@visa_require_role('admin')
@uses_components("data_table", "master_page")
def company_create(request):
    if request.method == 'POST':
        return _save_company(request, None)
    return render(request, 'visa_management/company_edit.html', {'company': None})


@visa_require_role('admin')
@uses_components("data_table", "master_page")
def company_edit(request, pk):
    company = get_object_or_404(VisaCompany, pk=pk)
    if request.method == 'POST':
        return _save_company(request, company)
    return render(request, 'visa_management/company_edit.html', {'company': company})


def _save_company(request, company):
    p = request.POST
    code = p.get('code', '').strip().upper()
    name = p.get('name', '').strip()
    if not code or not name:
        messages.error(request, 'Company code and name are required.')
        return redirect('visa:company_list')
    if company is None:
        company = VisaCompany()
    company.code = code
    company.name = name
    company.cin = p.get('cin', '').strip()
    company.registered_address = p.get('registered_address', '').strip()
    company.description = p.get('description', '').strip()
    company.tel = p.get('tel', '').strip()
    company.email = p.get('email', '').strip()
    company.website = p.get('website', '').strip()
    company.signatory_name = p.get('signatory_name', '').strip()
    company.signatory_title = p.get('signatory_title', '').strip()
    company.is_active = p.get('is_active') == 'on'
    try:
        company.save()
        messages.success(request, f'Company "{company.code}" saved.')
    except Exception as e:
        messages.error(request, f'Could not save: {e}')
    return redirect('visa:company_list')
