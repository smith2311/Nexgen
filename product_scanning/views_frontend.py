from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .models import Feedback, Hospital
from .rbac import can_use_dashboard, can_use_scanner, dashboard_required, is_viewer_only, scanner_required


@login_required
@require_http_methods(["GET"])
def qr_scanning_form(request):
    """
    Render the mobile-friendly QR scanning form.
    Access: PS Salesperson, Product Scanner, Sales, Superuser — full scanning.
             PS Viewer — read-only view of the scanner window (cannot start/scan/submit).
    Warehouse-only users are redirected to the dashboard.
    PS Salesperson sees only hospitals mapped to their email; admins/managers see all.
    """
    user = request.user
    viewer = is_viewer_only(user)

    # Non-scanner, non-viewer: redirect appropriately
    if not can_use_scanner(user) and not viewer:
        if can_use_dashboard(user):
            from django.shortcuts import redirect
            return redirect("/product_scanning/dashboard/")
        raise PermissionDenied

    is_salesperson_only = (
        not user.is_superuser
        and user.groups.filter(name="PS Salesperson").exists()
        and not user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists()
    )
    if is_salesperson_only:
        hospitals = Hospital.objects.filter(salesperson_email__iexact=user.email).order_by("name")
    else:
        hospitals = Hospital.objects.all().order_by("name")
    context = {
        "hospitals": hospitals,
        "user": user,
        "page_title": "Product Scanning",
        "can_view_dashboard": can_use_dashboard(user),
        "can_use_scanner": can_use_scanner(user),
        "can_view_only": viewer,
    }
    return render(request, "product_scanning/qr_scanning_form.html", context)


@login_required
@dashboard_required
@require_http_methods(["GET"])
def scanning_dashboard(request):
    """
    Display dashboard with all scanned products and sessions.
    Access: PS Warehouse, Product Scanner, Sales, Superuser.
    Scanner-only users are redirected to the QR form.
    """
    context = {
        "page_title": "Scanning Dashboard",
        "user": request.user,
        "can_use_scanner": can_use_scanner(request.user),
        "can_use_dashboard": can_use_dashboard(request.user),
        "can_view_only": is_viewer_only(request.user),
        "can_view_dashboard": True,
    }
    return render(request, "product_scanning/scanning_dashboard.html", context)


@login_required
@dashboard_required
@require_http_methods(["GET"])
def session_detail(request, session_key):
    """
    View details of a specific scanning session with all products.
    Access: PS Warehouse, Product Scanner, Sales, Superuser.
    (Salesperson does not need to review past sessions via this page.)
    """
    viewer = is_viewer_only(request.user)
    context = {
        "session_key": session_key,
        "page_title": "Session Details",
        "user": request.user,
        "can_use_scanner": can_use_scanner(request.user),
        "can_use_dashboard": can_use_dashboard(request.user),
        "can_view_only": viewer,
        "can_view_dashboard": True,
        "can_upload_attachment": can_use_dashboard(request.user) and not viewer,
        "api_base": "/product_scanning/api",
        "dashboard_url": "/product_scanning/dashboard/",
        "scanner_url": "/product_scanning/",
    }
    return render(request, "product_scanning/session_detail.html", context)


@login_required
@require_http_methods(["GET"])
def reconciliation_export(request):
    """
    Export scanned data as SAP reconciliation file.
    Generates CSV/Excel with all session data.
    """
    hospitals = Hospital.objects.all()
    context = {
        "page_title": "SAP Reconciliation",
        "user": request.user,
        "hospitals": hospitals,
    }
    return render(request, "product_scanning/reconciliation_export.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# H2H STOCK TRANSFER FRONTEND VIEWS
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@dashboard_required
@require_http_methods(["GET"])
def transfer_dashboard(request):
    """
    Dashboard for Hospital-to-Hospital Stock Transfers.
    Shows ongoing, completed, and closed transfer sessions.
    """
    context = {
        "page_title": "Stock Transfers",
        "user": request.user,
        "can_use_scanner": can_use_scanner(request.user),
        "can_use_dashboard": can_use_dashboard(request.user),
    }
    return render(request, "product_scanning/transfer_dashboard.html", context)


@login_required
@scanner_required
@require_http_methods(["GET"])
def transfer_scanner_form(request):
    """
    Scanner form for initiating and scanning products in H2H transfers.
    """
    user = request.user
    salesperson_only = (
        not user.is_superuser
        and user.groups.filter(name="PS Salesperson").exists()
        and not user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists()
    )
    if salesperson_only:
        hospitals = Hospital.objects.filter(salesperson_email__iexact=user.email).order_by("name")
    else:
        hospitals = Hospital.objects.all().order_by("name")
    context = {
        "hospitals": hospitals,
        "user": user,
        "page_title": "New Stock Transfer",
        "can_view_dashboard": can_use_dashboard(user),
        "can_use_scanner": True,
    }
    return render(request, "product_scanning/transfer_scanner_form.html", context)


@login_required
@dashboard_required
@require_http_methods(["GET"])
def hospital_master(request):
    """
    Hospital Master management page.
    Allows warehouse/admin users to download the current hospital master as a
    CSV template and upload an updated file to bulk-upsert hospitals.
    Access: PS Warehouse, Product Scanner, Sales, Superuser.
    """
    from .models import Hospital as HospitalModel

    hospitals = HospitalModel.objects.all().order_by("name")
    total = hospitals.count()
    mapped = hospitals.filter(salesperson_email__isnull=False).exclude(salesperson_email="").count()
    unmapped = total - mapped
    context = {
        "page_title": "Hospital Master",
        "user": request.user,
        "can_use_scanner": can_use_scanner(request.user),
        "can_use_dashboard": can_use_dashboard(request.user),
        "can_view_only": is_viewer_only(request.user),
        "hospitals": hospitals,
        "total": total,
        "mapped": mapped,
        "unmapped": unmapped,
    }
    return render(request, "product_scanning/hospital_master.html", context)


@login_required
@dashboard_required
@require_http_methods(["GET"])
def gtin_material_master(request):
    """
    GTIN Material Mapping management page.
    Warehouse/admin users can add, edit, delete, and bulk-import GTIN→material code mappings.
    Access: PS Warehouse, Product Scanner, Sales, Superuser.
    """
    from .models import GTINMaterialMapping

    total = GTINMaterialMapping.objects.count()
    context = {
        "page_title": "GTIN Material Mapping",
        "user": request.user,
        "can_use_scanner": can_use_scanner(request.user),
        "can_use_dashboard": can_use_dashboard(request.user),
        "can_view_only": is_viewer_only(request.user),
        "total": total,
    }
    return render(request, "product_scanning/gtin_material_master.html", context)


@login_required
@dashboard_required
@require_http_methods(["GET"])
def transfer_detail(request, session_key):
    """
    View details of a specific H2H transfer session.
    """
    context = {
        "session_key": session_key,
        "page_title": "Transfer Details",
        "user": request.user,
        "can_use_scanner": can_use_scanner(request.user),
        "can_use_dashboard": can_use_dashboard(request.user),
        "can_upload_attachment": can_use_dashboard(request.user),
    }
    return render(request, "product_scanning/transfer_detail.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# SALESPERSON PERFORMANCE DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────


def _is_warehouse(user):
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=["PS Warehouse", "Product Scanner", "Sales"]).exists()


def _is_salesperson(user):
    return user.groups.filter(name="PS Salesperson").exists()


@login_required
@require_http_methods(["GET"])
def salesperson_dashboard(request):
    """
    Salesperson Performance Dashboard.

    Access:
      • PS Salesperson  → sees only their own data
      • PS Warehouse / Product Scanner / Sales / Superuser → can view all or pick a salesperson
    """
    user = request.user
    is_wh = _is_warehouse(user)
    is_sp = _is_salesperson(user)
    viewer = is_viewer_only(user)

    # Must have at least one recognised role
    if not is_wh and not is_sp and not viewer and not user.is_superuser:
        raise PermissionDenied

    # Build salesperson list from Hospital master mapping
    sp_list = []
    if is_wh or viewer:
        from product_scanning.views import SalespersonPerformanceViewSet

        sp_list = SalespersonPerformanceViewSet._discover_salesperson_emails()

    # The selected salesperson (warehouse/viewer may choose; salesperson sees only self)
    selected_sp_email = ""
    selected_sp_name = user.get_full_name() or user.username
    if is_wh or viewer:
        selected_sp_email = request.GET.get("sp_email", "")
        if selected_sp_email:
            match = next((s for s in sp_list if s["email"].lower() == selected_sp_email.lower()), None)
            if match:
                selected_sp_name = match["name"]
            else:
                selected_sp_email = ""
    else:
        selected_sp_email = user.email

    context = {
        "page_title": "Salesperson Performance Dashboard",
        "user": user,
        "is_warehouse": is_wh or viewer,
        "is_salesperson": is_sp,
        "can_use_scanner": can_use_scanner(user),
        "can_use_dashboard": can_use_dashboard(user),
        "sp_list": sp_list,
        "selected_sp_email": selected_sp_email,
        "selected_sp_name": selected_sp_name,
    }
    return render(request, "product_scanning/salesperson_dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK / BUG REPORT PORTAL  (platform-wide)
# ─────────────────────────────────────────────────────────────────────────────


def _feedback_module_choices_for_user(user):
    """
    Return module choices filtered to this user's role using the registry.
    Apps self-register in AppConfig.ready() — no changes needed here when
    a new NexGen app is added.
    """
    from product_scanning.feedback_registry import get_choices_for_user
    return get_choices_for_user(user)


@login_required
@require_http_methods(["GET"])
def feedback_portal(request):
    """
    Platform-wide user feedback portal.
    Any authenticated user can submit a ticket and view their own submissions.
    """
    user = request.user
    is_admin = user.is_superuser or user.is_staff

    own_tickets = (
        Feedback.objects.filter(submitted_by=user)
        .prefetch_related("replies")
        .order_by("-submitted_at")[:20]
    )

    from main.utils import safe_back_url

    context = {
        "page_title": "Feedback & Support",
        "user": user,
        "back_url": safe_back_url(request),
        "is_admin": is_admin,
        "own_tickets": own_tickets,
        "category_choices": Feedback.CATEGORY_CHOICES,
        "severity_choices": Feedback.SEVERITY_CHOICES,
        "module_choices": _feedback_module_choices_for_user(user),
        "can_use_scanner": can_use_scanner(user),
        "can_use_dashboard": can_use_dashboard(user),
        "api_base": "/product_scanning/api",
    }
    return render(request, "product_scanning/feedback_portal.html", context)


@login_required
@require_http_methods(["GET"])
def user_guide(request):
    """
    Role-specific user guide page.
    Admins (Product Scanner / Sales / superuser) → admin guide.
    Warehouse-only users                         → warehouse guide.
    Salesperson-only users                       → salesperson guide.
    """
    user = request.user
    if not (can_use_scanner(user) or can_use_dashboard(user)):
        raise PermissionDenied

    is_admin = (
        user.is_superuser
        or user.groups.filter(name__in=["Product Scanner", "Sales"]).exists()
    )
    is_warehouse = can_use_dashboard(user) and not can_use_scanner(user)

    if is_admin:
        role = "admin"
    elif is_warehouse:
        role = "warehouse"
    else:
        role = "salesperson"

    context = {
        "page_title": "User Guide",
        "role": role,
        "is_superuser": user.is_superuser,
        "user": user,
        "can_use_scanner": can_use_scanner(user),
        "can_use_dashboard": can_use_dashboard(user),
    }
    return render(request, "product_scanning/guide.html", context)


@login_required
@require_http_methods(["GET"])
def feedback_inbox(request):
    """
    Admin / superuser inbox — see all tickets, filter, reply, change status.
    """
    user = request.user
    if not (user.is_superuser or user.is_staff):
        raise PermissionDenied

    from product_scanning.feedback_registry import get_all_choices as all_module_choices

    total = Feedback.objects.count()
    open_count = Feedback.objects.filter(status="open").count()
    critical_count = Feedback.objects.filter(
        severity="critical", status__in=["open", "in_progress"]
    ).count()
    unread_count = Feedback.objects.filter(is_read_by_admin=False).count()

    context = {
        "page_title": "Feedback Inbox",
        "user": user,
        "is_admin": True,
        "total": total,
        "open_count": open_count,
        "critical_count": critical_count,
        "unread_count": unread_count,
        "category_choices": Feedback.CATEGORY_CHOICES,
        "severity_choices": Feedback.SEVERITY_CHOICES,
        "status_choices": Feedback.STATUS_CHOICES,
        "module_choices": all_module_choices(),   # admin sees all registered modules
        "api_base": "/product_scanning/api",
        "can_use_scanner": can_use_scanner(user),
        "can_use_dashboard": can_use_dashboard(user),
    }
    return render(request, "product_scanning/feedback_inbox.html", context)
