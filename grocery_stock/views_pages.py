"""
Grocery Stock & Cost Management — web pages
─────────────────────────────────────────────────────────────────────────────
Server-rendered pages for the nexgen web app, following the pattern used by
logistics_performance: the Django view renders a shell, and the page's script
fills it from the DRF API in views.py. Nothing is duplicated — the browser and
a future Flutter client read exactly the same endpoints.

Access mirrors the API in roles.py: any signed-in grocery user may *read* every
master, and the write buttons are hidden for users whose role cannot write.
Hiding them is a convenience, not the control — GroceryRolePermission still
rejects the request if someone posts anyway.
"""
import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from ui.decorators import uses_components

from . import roles
from .models import (
    GroceryCategory,
    GroceryDepartment,
    GroceryGoodsReceipt,
    GroceryItem,
    GroceryLocation,
    GroceryStock,
    GroceryStockAdjustment,
    GroceryStockBatch,
    GroceryStockIssue,
    GroceryStockTransaction,
    GrocerySupplier,
    GroceryUnit,
    GroceryWarehouse,
)
from .pages import MASTER_ORDER, MASTER_SPECS
from .pages_txn import DOCUMENT_SPECS, REPORT_GROUPS, REPORT_SPECS

# Mirrors the write_roles on the transaction ViewSets: every document screen —
# receipts, issues, adjustments — is writable by Grocery-Manager, and by
# Grocery-Admin through the admin short-circuit in roles.has_role. No slug map
# any more because there is no longer a document any one role is shut out of.
DOCUMENT_WRITE_ROLES = (roles.MANAGER,)

# Current Stock and Stock Ledger are reached from the Stock Operations section,
# not the Reports hub — they answer "what is on the floor right now", which is
# the operations question. Every other report is an Insights screen.
OPERATION_REPORTS = {"stock", "ledger"}


def _require_grocery(user):
    """
    Either grocery role — or a superuser — may open the module.

    Deliberately broader than the write roles: a Grocery-Manager may not touch
    master data but has to read the item, supplier and warehouse lists to
    record a receipt, so gating the pages on write permission would lock the
    role out of screens it needs.
    """
    if not (user.is_superuser or roles.user_groups(user)):
        raise PermissionDenied("You do not have access to Grocery Stock Management.")


def _require_section(user, section):
    """
    Module access, then the section this screen belongs to.

    Hiding a card on the home page is a courtesy; this is the control. Without
    it a Grocery-Manager could open any master by typing its URL, which is the
    whole of what the role is meant not to reach.
    """
    _require_grocery(user)
    if not roles.can_see(user, section):
        raise PermissionDenied("Your grocery role does not include this section.")


@login_required
@uses_components("master_page")
def home(request):
    """
    Module landing page — one card per available section.

    Which sections appear is roles.visible_sections: Grocery-Admin gets all
    three, Grocery-Manager only Stock Operations. The queries behind a hidden
    section are skipped rather than computed and thrown away — the eight master
    counts are eight round trips a manager would never see.
    """
    _require_grocery(request.user)

    sections = roles.visible_sections(request.user)
    show_insights = roles.SECTION_INSIGHTS in sections
    show_operations = roles.SECTION_OPERATIONS in sections
    show_masters = roles.SECTION_MASTERS in sections

    today = timezone.localdate()
    item_count = GroceryItem.objects.count()

    # No per-card permission flag: the home page no longer shows an
    # Editable / Read only chip, and the master page itself already states the
    # role required. Computing it here meant eight role lookups per page load
    # that nothing rendered.
    cards = []
    if show_masters:
        counts = {
            "items": item_count,
            "categories": GroceryCategory.objects.count(),
            "brands": GroceryItem.objects.values("brand").exclude(
                brand=None).distinct().count(),
            "units": GroceryUnit.objects.count(),
            "suppliers": GrocerySupplier.objects.count(),
            "warehouses": GroceryWarehouse.objects.count(),
            "locations": GroceryLocation.objects.count(),
            "departments": GroceryDepartment.objects.count(),
        }
        cards = [
            {
                "slug": slug,
                "title": MASTER_SPECS[slug]["title"],
                "subtitle": MASTER_SPECS[slug]["subtitle"],
                "icon": MASTER_SPECS[slug]["icon"],
                # Tone comes from the spec, not a template {% cycle %}: cycle
                # reassigns every card whenever one is added or hidden, so the
                # same master changed colour depending on who was looking.
                "tone": MASTER_SPECS[slug]["tone"],
                "count": counts.get(slug, 0),
            }
            for slug in MASTER_ORDER
        ]

    # Phase 2 sections. Counts are cheap aggregates so the landing page stays
    # one round trip — the detailed KPIs load from stock/summary/ on the page.
    operation_cards = []
    if show_operations:
        operation_cards = [
            {
                "url": "grocery_stock:document_list", "arg": "receipts",
                "title": "Goods Receipts",
                "subtitle": "Record arrivals with batch, expiry and landed cost.",
                "icon": DOCUMENT_SPECS["receipts"]["icon"],
                "tone": "gs-tone-green",
                "count": GroceryGoodsReceipt.objects.count(),
            },
            {
                "url": "grocery_stock:document_list", "arg": "issues",
                "title": "Stock Issues",
                "subtitle": "Issue stock to a consuming department.",
                "icon": DOCUMENT_SPECS["issues"]["icon"],
                "tone": "gs-tone-amber",
                "count": GroceryStockIssue.objects.count(),
            },
            {
                "url": "grocery_stock:document_list", "arg": "adjustments",
                "title": "Stock Adjustments",
                "subtitle": "Physical count, damage, theft and expiry write-offs.",
                "icon": DOCUMENT_SPECS["adjustments"]["icon"],
                "tone": "gs-tone-purple",
                "count": GroceryStockAdjustment.objects.count(),
            },
            {
                "url": "grocery_stock:report", "arg": "stock",
                "title": "Current Stock",
                "subtitle": "Live quantity and value by item and warehouse.",
                "icon": REPORT_SPECS["stock"]["icon"],
                "tone": "gs-tone-blue",
                "count": GroceryStock.objects.filter(quantity_available__gt=0).count(),
            },
            {
                "url": "grocery_stock:report", "arg": "ledger",
                "title": "Stock Ledger",
                "subtitle": "Every movement in and out, with running balances.",
                "icon": REPORT_SPECS["ledger"]["icon"],
                "tone": "gs-tone-navy",
                "count": GroceryStockTransaction.objects.count(),
            },
        ]

    expiring_soon = GroceryStockBatch.objects.filter(
        remaining_quantity__gt=0, expiry_date__isnull=False,
        expiry_date__gte=today, expiry_date__lte=today + timedelta(days=30),
    ).count()

    return render(request, "grocery_stock/home.html", {
        "cards": cards,
        "operation_cards": operation_cards,
        "show_insights": show_insights,
        "show_operations": show_operations,
        "show_masters": show_masters,
        "is_admin": roles.is_grocery_admin(request.user),
        "item_count": item_count,
        "stock_lines": GroceryStock.objects.filter(quantity_available__gt=0).count(),
        "movement_count": GroceryStockTransaction.objects.count(),
        "expiring_soon": expiring_soon,
    })


@login_required
@uses_components("data_table", "adv_filter", "modal", "master_page")
def document_list(request, slug):
    """List screen for a draft-then-post document (GRN, Issue)."""
    _require_section(request.user, roles.SECTION_OPERATIONS)

    spec = DOCUMENT_SPECS.get(slug)
    if spec is None:
        raise Http404(f"No grocery document named '{slug}'.")

    return render(request, "grocery_stock/document_list.html", {
        "slug": slug,
        "spec": spec,
        "can_write": roles.has_role(request.user, *DOCUMENT_WRITE_ROLES),
        "columns": spec["list_columns"],
    })


@login_required
@uses_components("data_table", "modal", "master_page")
def document_form(request, slug, pk=None):
    """
    Create or edit one document.

    A posted document opens in the same screen, read-only — one place to look
    at a GRN whether it is a draft or history.
    """
    _require_section(request.user, roles.SECTION_OPERATIONS)

    spec = DOCUMENT_SPECS.get(slug)
    if spec is None:
        raise Http404(f"No grocery document named '{slug}'.")

    return render(request, "grocery_stock/document_form.html", {
        "slug": slug,
        "spec": spec,
        "document_id": pk,
        "can_write": roles.has_role(request.user, *DOCUMENT_WRITE_ROLES),
        "header_fields": spec["header_fields"],
        "line_columns": spec["line_columns"],
        "line_result_columns": spec.get("line_result_columns", []),
    })


@login_required
@uses_components("data_table", "adv_filter", "master_page")
def report(request, slug):
    """Read-only report screen (Current Stock, Stock Ledger)."""
    _require_section(
        request.user,
        roles.SECTION_OPERATIONS if slug in OPERATION_REPORTS
        else roles.SECTION_INSIGHTS,
    )

    spec = REPORT_SPECS.get(slug)
    if spec is None:
        raise Http404(f"No grocery report named '{slug}'.")

    return render(request, "grocery_stock/report_page.html", {
        "slug": slug,
        "spec": spec,
        "columns": spec["columns"],
        "filters": spec.get("filters", []),
        "toggles": spec.get("toggles", []),
        "fixed_params_json": json.dumps(spec.get("fixed_params", {})),
    })


@login_required
# data_table carries the :root design tokens and the .btn* classes that
# master_page.css and these templates both rely on. Declaring master_page alone
# leaves --navy undefined, which silently voids the header gradient and leaves
# every button unstyled.
@uses_components("data_table", "master_page")
def reports_hub(request):
    """Section 15 — one place listing every report, grouped by subject."""
    _require_section(request.user, roles.SECTION_INSIGHTS)

    groups = []
    for name, icon, slugs in REPORT_GROUPS:
        groups.append({
            "name": name,
            "icon": icon,
            "reports": [
                {
                    "slug": slug,
                    "title": REPORT_SPECS[slug]["title"],
                    "subtitle": REPORT_SPECS[slug]["subtitle"],
                    "icon": REPORT_SPECS[slug]["icon"],
                    "tone": REPORT_SPECS[slug]["tone"],
                }
                for slug in slugs if slug in REPORT_SPECS
            ],
        })

    return render(request, "grocery_stock/reports_hub.html", {"groups": groups})


@login_required
@uses_components("data_table", "master_page")
def dashboard(request):
    """Section 1 — KPIs and charts, all fed by /api/v1/dashboard/."""
    _require_section(request.user, roles.SECTION_INSIGHTS)
    return render(request, "grocery_stock/dashboard.html", {})


@login_required
@uses_components("data_table", "adv_filter", "modal", "master_page")
def master(request, slug):
    """
    One view for all eight masters.

    The spec in pages.py supplies the columns and form fields; the shared
    template and script do the rest, so a new master is a data change here
    rather than another near-identical template.
    """
    _require_section(request.user, roles.SECTION_MASTERS)

    spec = MASTER_SPECS.get(slug)
    if spec is None:
        raise Http404(f"No grocery master named '{slug}'.")

    return render(request, "grocery_stock/master_page.html", {
        "slug": slug,
        "spec": spec,
        # Masters are Grocery-Admin's alone (MasterViewSet.write_roles is
        # empty), so the button flag is a straight admin check.
        "can_write": roles.is_grocery_admin(request.user),
        # Serialised into the page for the script to read.
        "columns": spec["columns"],
        "fields": spec["fields"],
    })
