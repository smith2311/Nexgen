"""MeriTrack frontend views — render HTML pages with context."""

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .models import Hospital
from .rbac import is_mt_manager, is_mt_rep, manager_required, rep_required


def _base_context(request, page_title):
    """Common context for all MeriTrack pages."""
    user = request.user
    return {
        "page_title": page_title,
        "user": user,
        "can_use_scanner": is_mt_rep(user),
        "can_view_dashboard": is_mt_manager(user),
        "can_view_performance": is_mt_manager(user) or is_mt_rep(user),
    }


@login_required
@rep_required
@require_http_methods(["GET"])
def scanner(request):
    """Main scanning form — reps use this to scan products during hospital visits."""
    user = request.user
    salesperson_only = (
        not user.is_superuser
        and user.groups.filter(name="MT Rep").exists()
        and not user.groups.filter(name__in=["MT Manager", "MT Logistics", "Product Scanner", "Sales"]).exists()
    )
    if salesperson_only:
        hospitals = Hospital.objects.filter(
            Q(salesperson_email__iexact=user.email) | Q(salespersons=user),
            is_active=True,
        ).distinct().order_by("name")
    else:
        hospitals = Hospital.objects.filter(is_active=True).order_by("name")

    context = {
        "hospitals": hospitals,
        "user": user,
        "page_title": "MeriTrack Scanner",
        "can_view_dashboard": is_mt_manager(user),
        "can_view_performance": True,
        "can_use_scanner": True,
    }
    return render(request, "meritrack/scanner.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def dashboard(request):
    """Manager dashboard — overview of all consignment data."""
    context = _base_context(request, "MeriTrack Dashboard")
    context["can_use_dashboard"] = True
    return render(request, "meritrack/dashboard.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def session_detail(request, session_key):
    """Session detail view."""
    context = _base_context(request, "Session Details")
    context["session_key"] = session_key
    context["is_manager"] = is_mt_manager(request.user)
    context["can_upload_attachment"] = is_mt_manager(request.user)
    return render(request, "meritrack/session_detail.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def inventory(request):
    """Hospital inventory tree view."""
    context = _base_context(request, "Consignment Inventory")
    context["hospitals"] = Hospital.objects.filter(is_active=True).order_by("name")
    return render(request, "meritrack/inventory.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def expiry_alerts(request):
    """Expiry alerts overview."""
    context = _base_context(request, "Expiry Alerts")
    return render(request, "meritrack/expiry_alerts.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def orders(request):
    """Stock orders management."""
    context = _base_context(request, "Stock Orders")
    context["hospitals"] = Hospital.objects.filter(is_active=True).order_by("name")
    return render(request, "meritrack/orders.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def analytics(request):
    """Usage analytics dashboard."""
    context = _base_context(request, "Usage Analytics")
    context["hospitals"] = Hospital.objects.filter(is_active=True).order_by("name")
    return render(request, "meritrack/analytics.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def hospital_master(request):
    """Hospital master data management."""
    hospitals = Hospital.objects.all().order_by("name")
    context = _base_context(request, "Hospital Master")
    context["hospitals"] = hospitals
    context["total"] = hospitals.count()
    context["active"] = hospitals.filter(is_active=True).count()
    return render(request, "meritrack/hospital_master.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def product_catalog(request):
    """Product catalog management."""
    from .models import ProductCatalog

    context = _base_context(request, "Product Catalog")
    context["total"] = ProductCatalog.objects.count()
    context["active"] = ProductCatalog.objects.filter(is_active=True).count()
    return render(request, "meritrack/product_catalog.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# SALESPERSON (REP) PERFORMANCE DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["GET"])
def salesperson_dashboard(request):
    """MT Rep Performance Dashboard — session and scan activity per rep.

    Managers see all reps and can drill-down; reps see only their own data.
    """
    from django.contrib.auth.models import User as _User

    from .models import Hospital as _Hospital
    from .models import ScanningSession as _SS

    user = request.user
    is_manager = user.is_superuser or user.groups.filter(name__in=["MT Manager", "MT Logistics"]).exists()
    is_rep = user.groups.filter(name="MT Rep").exists()

    if not is_manager and not is_rep and not user.is_superuser:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied

    # Build rep list for manager dropdown
    rep_list = []
    if is_manager:
        seen = set()
        rep_ids = _SS.objects.exclude(rep=None).values_list("rep", flat=True).distinct()

        def _hospital_count_for_email(email):
            return _Hospital.objects.filter(
                Q(salesperson_email__iexact=email) | Q(salespersons__email__iexact=email),
                is_active=True,
            ).distinct().count()

        mt_rep_ids = set(
            _User.objects.filter(groups__name="MT Rep", is_active=True).values_list("id", flat=True)
        )
        for uid in rep_ids:
            if uid not in mt_rep_ids:
                continue
            try:
                u = _User.objects.get(pk=uid)
                if u.email and u.email.lower() not in seen:
                    seen.add(u.email.lower())
                    rep_list.append(
                        {
                            "email": u.email,
                            "name": u.get_full_name() or u.username,
                            "hospital_count": _hospital_count_for_email(u.email),
                        }
                    )
            except _User.DoesNotExist:
                pass
        # Collect all assigned emails from salesperson_email + M2M salespersons
        assigned_emails = set()
        for e in (
            _Hospital.objects.exclude(salesperson_email="")
            .exclude(salesperson_email__isnull=True)
            .values_list("salesperson_email", flat=True)
            .distinct()
        ):
            assigned_emails.add(e.lower())
        for e in (
            _Hospital.objects.filter(salespersons__isnull=False)
            .values_list("salespersons__email", flat=True)
            .distinct()
        ):
            if e:
                assigned_emails.add(e.lower())
        for email in assigned_emails:
            if email not in seen:
                try:
                    u = _User.objects.get(email__iexact=email)
                    if u.id not in mt_rep_ids:
                        continue
                    seen.add(email)
                    rep_list.append(
                        {
                            "email": u.email,
                            "name": u.get_full_name() or u.username,
                            "hospital_count": _hospital_count_for_email(email),
                        }
                    )
                except _User.DoesNotExist:
                    pass
        rep_list.sort(key=lambda x: x.get("name", "").lower())

    selected_rep_email = ""
    selected_rep_name = user.get_full_name() or user.username
    if is_manager:
        selected_rep_email = request.GET.get("rep_email", "")
        if selected_rep_email:
            match = next((r for r in rep_list if r["email"].lower() == selected_rep_email.lower()), None)
            selected_rep_name = match["name"] if match else selected_rep_email
    else:
        selected_rep_email = user.email

    context = _base_context(request, "Rep Performance Dashboard")
    context.update(
        {
            "is_manager": is_manager,
            "is_rep": is_rep,
            "rep_list": rep_list,
            "selected_rep_email": selected_rep_email,
            "selected_rep_name": selected_rep_name,
        }
    )
    return render(request, "meritrack/salesperson_dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# USER GUIDE
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["GET"])
def user_guide(request):
    """Role-specific user guide for MeriTrack."""
    context = _base_context(request, "MeriTrack User Guide")
    # Same rule as is_mt_manager() — superuser, or MT Manager / MT Logistics.
    # It was spelled out inline here, which was a second copy of the group list
    # that could drift from rbac.MANAGER_GROUPS.
    context["is_manager"] = is_mt_manager(request.user)
    return render(request, "meritrack/guide.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# DESIGN TOKENS / UI KIT
# ─────────────────────────────────────────────────────────────────────────────
# The swatches are named by CSS custom property, and the template paints each
# one with `background: var(--mt-…)`. The hex beside a swatch is read out of the
# live stylesheet by JS on the page (getComputedStyle), so nothing here can
# drift from meritrack-core.css — this list only decides what gets shown.
_UI_KIT_PALETTE = [
    {
        "group": "Primary Blue",
        "swatches": [
            ("--mt-primary", "Primary"),
            ("--mt-primary-light", "Light / hover"),
            ("--mt-primary-dark", "Dark / active"),
            ("--mt-primary-deep", "Deep"),
            ("--mt-primary-lighter", "Tint"),
        ],
    },
    {
        "group": "Success Green",
        "swatches": [
            ("--mt-success", "Success"),
            ("--mt-success-dark", "Dark"),
            ("--mt-success-border", "Border"),
            ("--mt-success-light", "Tint"),
        ],
    },
    {
        "group": "Danger Red",
        "swatches": [
            ("--mt-danger", "Danger"),
            ("--mt-danger-dark", "Dark"),
            ("--mt-danger-border", "Border"),
            ("--mt-danger-light", "Tint"),
        ],
    },
    {
        "group": "Warning / Orange",
        "swatches": [
            ("--mt-warning", "Warning"),
            ("--mt-warning-dark", "Dark"),
            ("--mt-warning-text", "Text on tint"),
            ("--mt-warning-light", "Tint"),
            ("--mt-orange", "Orange"),
            ("--mt-orange-dark", "Orange dark"),
        ],
    },
    {
        "group": "Accents",
        "swatches": [
            ("--mt-info", "Info"),
            ("--mt-secondary", "Secondary / cyan"),
            ("--mt-purple", "Purple"),
            ("--mt-teal", "Teal"),
        ],
    },
]

_UI_KIT_GRAYS = [
    ("--mt-gray-50", "50"),
    ("--mt-gray-100", "100"),
    ("--mt-gray-200", "200"),
    ("--mt-gray-300", "300"),
    ("--mt-gray-400", "400"),
    ("--mt-gray-500", "500"),
    ("--mt-gray-600", "600"),
    ("--mt-gray-700", "700"),
    ("--mt-gray-800", "800"),
    ("--mt-gray-900", "900"),
]

_UI_KIT_TYPE = [
    ("mt-h1", "H1", "Poppins, bold", "--mt-text-h1"),
    ("mt-h2", "H2", "Poppins, bold", "--mt-text-h2"),
    ("mt-h3", "H3", "medium", "--mt-text-h3"),
    ("mt-h4", "H4", "medium", "--mt-text-h4"),
    ("mt-body-text", "Body", "regular", "--mt-text-body"),
    ("mt-small", "Small", "regular", "--mt-text-small"),
    ("mt-th-text", "Table Header", "medium, uppercase", "--mt-text-th"),
]

_UI_KIT_BUTTONS = [
    ("Primary", "btn-mt btn-mt-primary"),
    ("Secondary (filled)", "btn-mt btn-mt-secondary"),
    ("Outline", "btn-mt btn-mt-outline"),
    ("Ghost", "btn-mt btn-mt-ghost"),
    ("Success", "btn-mt btn-mt-success"),
    ("Danger", "btn-mt btn-mt-danger"),
    ("Warning", "btn-mt btn-mt-warning"),
]

_UI_KIT_ELEVATION = [
    ("None", "--mt-shadow-none"),
    ("Low", "--mt-shadow-sm"),
    ("Medium", "--mt-shadow-md"),
    ("Large", "--mt-shadow-lg"),
    ("X-Large", "--mt-shadow-xl"),
]


@login_required
@require_http_methods(["GET"])
def ui_kit(request):
    """Living design-token reference for MeriTrack.

    Renders straight from meritrack-core.css, so it stays in step with the
    stylesheet instead of restating it. Mirrors the
    `docs/design/meritrack/meritrack_design_tokens_ui_kit.png` sheet.
    """
    context = _base_context(request, "MeriTrack Design Tokens & UI Kit")
    context.update(
        {
            "palette": _UI_KIT_PALETTE,
            "grays": _UI_KIT_GRAYS,
            "type_scale": _UI_KIT_TYPE,
            "buttons": _UI_KIT_BUTTONS,
            "elevation": _UI_KIT_ELEVATION,
        }
    )
    return render(request, "meritrack/ui_kit.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# RECONCILIATION EXPORT
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@manager_required
@require_http_methods(["GET"])
def reconciliation_export(request):
    """SAP reconciliation export page — date-range CSV of scan logs."""
    context = _base_context(request, "Reconciliation Export")
    return render(request, "meritrack/reconciliation_export.html", context)


@login_required
@manager_required
@require_http_methods(["GET"])
def reconciliation_dashboard(request):
    """Inventory reconciliation dashboard — list of auto-generated reconciliations."""
    context = _base_context(request, "Reconciliations")
    context["can_view_dashboard"] = True
    return render(request, "meritrack/reconciliation_dashboard.html", context)


@login_required
@require_http_methods(["GET"])
def reconciliation_detail(request, recon_id):
    """Detail view for a single reconciliation (access enforced by the API queryset)."""
    context = _base_context(request, "Reconciliation Detail")
    context["recon_id"] = recon_id
    return render(request, "meritrack/reconciliation_detail.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# TRANSFER (H2H) SESSIONS
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@manager_required
@require_http_methods(["GET"])
def transfer_dashboard(request):
    """Hospital-to-Hospital transfer session overview."""
    context = _base_context(request, "Transfer Sessions")
    hospitals = Hospital.objects.filter(is_active=True).order_by("name")
    context["hospitals"] = hospitals
    return render(request, "meritrack/transfer_dashboard.html", context)


@login_required
@require_http_methods(["GET"])
def transfer_detail(request, session_key):
    """Transfer session detail view."""
    user = request.user
    is_manager = user.is_superuser or user.groups.filter(name__in=["MT Manager", "MT Logistics"]).exists()
    context = _base_context(request, "Transfer Session Details")
    context["session_key"] = session_key
    context["is_manager"] = is_manager
    return render(request, "meritrack/transfer_detail.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK / BUG REPORTING
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["GET"])
def feedback_portal(request):
    """Feedback & bug report portal — any authenticated MeriTrack user."""
    from product_scanning.models import Feedback
    from product_scanning.feedback_registry import get_choices_for_user
    user = request.user
    own_tickets = (
        Feedback.objects.filter(submitted_by=user)
        .prefetch_related("replies")
        .order_by("-submitted_at")[:20]
    )
    from main.utils import safe_back_url
    context = _base_context(request, "Feedback & Support")
    context.update({
        "back_url": safe_back_url(request),
        "is_admin": user.is_superuser or user.is_staff,
        "own_tickets": own_tickets,
        "category_choices": Feedback.CATEGORY_CHOICES,
        "severity_choices": Feedback.SEVERITY_CHOICES,
        "module_choices": get_choices_for_user(user),
        "api_base": "/product_scanning/api",
    })
    return render(request, "meritrack/feedback_portal.html", context)


@login_required
@require_http_methods(["GET"])
def feedback_inbox(request):
    """Feedback admin inbox — superusers and staff only."""
    from product_scanning.models import Feedback
    from product_scanning.feedback_registry import get_all_choices
    user = request.user
    if not (user.is_superuser or user.is_staff):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    context = _base_context(request, "Feedback Inbox")
    context.update({
        "is_admin": True,
        "total": Feedback.objects.count(),
        "open_count": Feedback.objects.filter(status="open").count(),
        "critical_count": Feedback.objects.filter(severity="critical", status__in=["open", "in_progress"]).count(),
        "unread_count": Feedback.objects.filter(is_read_by_admin=False).count(),
        "category_choices": Feedback.CATEGORY_CHOICES,
        "severity_choices": Feedback.SEVERITY_CHOICES,
        "status_choices": Feedback.STATUS_CHOICES,
        "module_choices": get_all_choices(),
        "api_base": "/product_scanning/api",
    })
    return render(request, "meritrack/feedback_inbox.html", context)
