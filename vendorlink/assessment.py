"""3PL Capability Assessment — VendorLink Phase 3.

External vendors self-assess against the SOW's 6-section, 34-field form over a
unique token link — no vendor account needed. An admin invites, reviews,
approves/rejects, and may promote an approved assessment straight into a
Vendor Master record.

Kept out of views.py for the same reason commercial.py is: a fifth concern in
an already ~2,000-line file would make it the file nobody wants to open.

Access follows the platform's original two-tier rbac (`access_required` /
`admin_required`) rather than commercial.py's three-tier editor split — the
SOW draws no editor line here, and the pre-existing templates already encode
that: they gate Invite/Promote on `is_vm_admin`, not a separate `can_edit`.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import EmailMultiAlternatives, send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from product_scanning.feedback_notifications import _send_async

from .constants import CAPABILITY_ASSESSMENT_REQUIRED_FIELDS
from .forms import CapabilityAssessmentExternalForm, CapabilityAssessmentInviteForm
from . import mailer
from .models import CapabilityAssessment, Vendor
from .rbac import can_access_vendorlink, is_vendorlink_admin
from .utils import geonames_country_choices

access_required = user_passes_test(can_access_vendorlink, login_url="landing_page")
admin_required = user_passes_test(is_vendorlink_admin, login_url="vendorlink:assessment_list")


def _ctx(request, **extra):
    base = {"is_vm_admin": is_vendorlink_admin(request.user)}
    base.update(extra)
    return base


def _assessment_link(request, assessment):
    return request.build_absolute_uri(reverse("vendorlink:assessment_fill", args=[assessment.token]))


def _reviewer_name(user):
    return user.get_full_name() or user.username


# ── Internal: queue + invite ─────────────────────────────────────────────────
@login_required
@access_required
def assessment_list(request):
    status_filter = request.GET.get("status", "").strip()
    assessments = CapabilityAssessment.objects.all()
    if status_filter:
        assessments = assessments.filter(status=status_filter)

    return render(request, "vendorlink/assessment/list.html", _ctx(
        request,
        assessments=assessments,
        status_filter=status_filter,
        status_choices=CapabilityAssessment.STATUS_CHOICES,
        pending_count=CapabilityAssessment.objects.filter(status=CapabilityAssessment.STATUS_SUBMITTED).count(),
    ))


@login_required
@admin_required
def assessment_invite(request):
    form = CapabilityAssessmentInviteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["vendor_email"]
        assessment = CapabilityAssessment.objects.create(
            vendor_email=email,
            token=CapabilityAssessment.generate_token(),
            token_expires_at=timezone.now() + timedelta(days=form.cleaned_data["expires_days"]),
            invited_by=request.user,
        )
        link = _assessment_link(request, assessment)

        html_body = render_to_string("vendorlink/email/assessment_invite.html", {
            "assessment": assessment, "link": link, "invited_by_name": _reviewer_name(request.user),
        })
        text_body = (
            "Meril Life Sciences has invited you to complete a 3PL Capability Assessment.\n\n"
            f"Fill it in at your convenience — you can save progress and return to it later "
            f"using this same link:\n{link}\n\n"
            f"This link expires on {assessment.token_expires_at:%d %b %Y}.\n\n"
            "Regards,\nMeril Life Sciences — Global SCM"
        )
        msg = EmailMultiAlternatives(
            "Meril Life Sciences — 3PL Capability Assessment", text_body,
            settings.DEFAULT_FROM_EMAIL, [email],
        )
        msg.attach_alternative(html_body, "text/html")
        # cid: in the footer needs the image on the message — see vendorlink.mailer.
        mailer.attach_logo(msg)
        _send_async(msg)

        messages.success(request, f"Invite sent to {email}.")
        return redirect("vendorlink:assessment_review", pk=assessment.pk)

    return render(request, "vendorlink/assessment/invite.html", _ctx(request, form=form))


# ── Internal: review + decision ──────────────────────────────────────────────
@login_required
@access_required
def assessment_review(request, pk):
    assessment = get_object_or_404(CapabilityAssessment, pk=pk)
    # Opening the review page on a fresh submission is what "someone is now
    # looking at this" means — distinct from "submitted and still untouched",
    # which is what the Under Review tab is for.
    if assessment.status == CapabilityAssessment.STATUS_SUBMITTED and is_vendorlink_admin(request.user):
        assessment.status = CapabilityAssessment.STATUS_UNDER_REVIEW
        assessment.save(update_fields=["status"])

    can_decide = (
        is_vendorlink_admin(request.user)
        and assessment.status in (CapabilityAssessment.STATUS_SUBMITTED, CapabilityAssessment.STATUS_UNDER_REVIEW)
    )
    return render(request, "vendorlink/assessment/review.html", _ctx(
        request, assessment=assessment, link=_assessment_link(request, assessment), can_decide=can_decide,
    ))


def _notify_decision(assessment, approved):
    if not assessment.vendor_email:
        return
    name = assessment.company_name or "Vendor"
    if approved:
        subject = "Meril Life Sciences — 3PL Capability Assessment Approved"
        body = (
            f"Dear {name},\n\n"
            "Thank you for completing the 3PL Capability Assessment. We are pleased to let "
            "you know it has been approved by the Meril Global SCM team.\n\n"
            "Regards,\nMeril Life Sciences — Global SCM"
        )
    else:
        subject = "Meril Life Sciences — 3PL Capability Assessment Update"
        reason = f"\n\nReviewer notes: {assessment.review_notes}" if assessment.review_notes else ""
        body = (
            f"Dear {name},\n\n"
            "Thank you for completing the 3PL Capability Assessment. After review, we are "
            f"unable to proceed further at this time.{reason}\n\n"
            "Regards,\nMeril Life Sciences — Global SCM"
        )
    # Best-effort, same as the registration approve/reject notices — a bounced
    # or misconfigured mail server should never block the decision itself.
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [assessment.vendor_email], fail_silently=True)
    except Exception:
        pass


@login_required
@admin_required
@require_POST
def assessment_approve(request, pk):
    assessment = get_object_or_404(CapabilityAssessment, pk=pk)
    if assessment.status not in (CapabilityAssessment.STATUS_SUBMITTED, CapabilityAssessment.STATUS_UNDER_REVIEW):
        messages.warning(request, "Only a submitted assessment can be approved.")
        return redirect("vendorlink:assessment_review", pk=pk)

    assessment.status = CapabilityAssessment.STATUS_APPROVED
    assessment.reviewed_by = _reviewer_name(request.user)
    assessment.reviewed_at = timezone.now()
    assessment.review_notes = (request.POST.get("review_notes") or "").strip()
    assessment.save()
    _notify_decision(assessment, approved=True)

    messages.success(request, f"{assessment.company_name or assessment.vendor_email} approved.")
    return redirect("vendorlink:assessment_review", pk=pk)


@login_required
@admin_required
@require_POST
def assessment_reject(request, pk):
    assessment = get_object_or_404(CapabilityAssessment, pk=pk)
    if assessment.status not in (CapabilityAssessment.STATUS_SUBMITTED, CapabilityAssessment.STATUS_UNDER_REVIEW):
        messages.warning(request, "Only a submitted assessment can be rejected.")
        return redirect("vendorlink:assessment_review", pk=pk)

    notes = (request.POST.get("review_notes") or "").strip()
    if not notes:
        messages.error(request, "Enter a reason before rejecting an assessment.")
        return redirect("vendorlink:assessment_review", pk=pk)

    assessment.status = CapabilityAssessment.STATUS_REJECTED
    assessment.reviewed_by = _reviewer_name(request.user)
    assessment.reviewed_at = timezone.now()
    assessment.review_notes = notes
    assessment.save()
    _notify_decision(assessment, approved=False)

    messages.success(request, f"{assessment.company_name or assessment.vendor_email} rejected.")
    return redirect("vendorlink:assessment_review", pk=pk)


@login_required
@admin_required
def assessment_promote(request, pk):
    """Create a Vendor Master record from an approved assessment.

    Only identity fields carry over (name, country, email, warehouse address) —
    the nine Yes/No capability flags are never guessed from free text, so the
    new vendor lands with everything False and an admin fills them in from the
    written assessment, exactly as the template's own copy promises ("complete
    any missing fields").
    """
    assessment = get_object_or_404(CapabilityAssessment, pk=pk)
    if assessment.status != CapabilityAssessment.STATUS_APPROVED:
        messages.warning(request, "Only an approved assessment can be promoted.")
        return redirect("vendorlink:assessment_review", pk=pk)
    if assessment.vendor_id:
        messages.info(request, f"{assessment.vendor.vendor_name} was already created from this assessment.")
        return redirect("vendorlink:vendor_detail", pk=assessment.vendor_id)
    if not assessment.company_name.strip():
        messages.error(request, "Add a company name to the assessment before promoting it.")
        return redirect("vendorlink:assessment_review", pk=pk)

    if request.method == "POST":
        country = (request.POST.get("country") or "").strip()
        if not country:
            messages.error(request, "Select a country to create the vendor record.")
            return redirect("vendorlink:assessment_promote", pk=pk)

        vendor = Vendor.objects.create(
            vendor_name=assessment.company_name.strip(),
            country=country,
            email=assessment.vendor_email,
            warehouse_location=assessment.warehouse_location_desc[:300],
            created_by=request.user,
            is_active=True,
        )
        assessment.vendor = vendor
        assessment.save(update_fields=["vendor"])
        messages.success(request, f"{vendor.vendor_name} created in Vendor Master — complete any remaining fields.")
        return redirect("vendorlink:vendor_edit", pk=vendor.pk)

    return render(request, "vendorlink/assessment/promote.html", _ctx(
        request, assessment=assessment, country_choices=geonames_country_choices()[1:],
    ))


# ── External (public, token-based, no auth) ──────────────────────────────────
def assessment_fill(request, token):
    """The vendor-facing side: no login, reached only by knowing the token."""
    assessment = CapabilityAssessment.objects.filter(token=token).first()
    if not assessment:
        return render(request, "vendorlink/assessment/token_invalid.html", {"reason": "invalid"})

    if assessment.status in CapabilityAssessment.OPEN_STATUSES and assessment.is_token_expired:
        return render(request, "vendorlink/assessment/token_invalid.html", {"reason": "expired"})

    if not assessment.is_open_for_editing:
        if assessment.status == CapabilityAssessment.STATUS_REJECTED:
            return render(request, "vendorlink/assessment/token_invalid.html", {"reason": "closed"})
        return render(request, "vendorlink/assessment/submitted.html", {"assessment": assessment})

    form = CapabilityAssessmentExternalForm(request.POST or None, instance=assessment)
    just_saved = False
    if request.method == "POST" and form.is_valid():
        if request.POST.get("_action") == "submit":
            missing = [f for f in CAPABILITY_ASSESSMENT_REQUIRED_FIELDS if not (form.cleaned_data.get(f) or "").strip()]
            for field_name in missing:
                form.add_error(field_name, "This field is required before submitting.")
            if not missing:
                saved = form.save(commit=False)
                saved.status = CapabilityAssessment.STATUS_SUBMITTED
                saved.submitted_at = timezone.now()
                saved.save()
                return render(request, "vendorlink/assessment/submitted.html", {"assessment": saved})
        else:
            saved = form.save(commit=False)
            if saved.status == CapabilityAssessment.STATUS_INVITED:
                saved.status = CapabilityAssessment.STATUS_DRAFT
            saved.save()
            just_saved = True

    return render(request, "vendorlink/assessment/external_form.html", {
        "assessment": assessment, "form": form, "just_saved": just_saved,
    })
