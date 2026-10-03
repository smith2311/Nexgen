"""
Product Scanning — Role-Based Access Control helpers
=====================================================

Roles governing access within this app:

  PS Salesperson   → QR Scanner only (scan products, start/complete sessions)
  PS Warehouse     → Dashboard only (view sessions, upload attachments, manage data)
  PS Viewer        → Dashboard read-only (view sessions/details, no actions)
  Product Scanner  → Both functions (parent / admin role)

Legacy groups that retain full access:
  Sales            → Both (backward-compat)
  Superuser        → Everything

Usage in views:
    from .rbac import scanner_required, dashboard_required, is_viewer_only
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

# ---------------------------------------------------------------------------
# Group name constants  — single source of truth; update here when adding roles
# ---------------------------------------------------------------------------
SCANNER_GROUPS = {"PS Salesperson", "Product Scanner", "Sales"}
DASHBOARD_GROUPS = {"PS Warehouse", "PS Viewer", "Product Scanner", "Sales"}


# ---------------------------------------------------------------------------
# Low-level permission checks
# ---------------------------------------------------------------------------


def can_use_scanner(user) -> bool:
    """True if the user may access the QR scanner and start/scan sessions."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=SCANNER_GROUPS).exists()


def can_use_dashboard(user) -> bool:
    """True if the user may access the dashboard and session details."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=DASHBOARD_GROUPS).exists()


def is_viewer_only(user) -> bool:
    """True if the user has ONLY PS Viewer access (read-only dashboard, no actions)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return False
    ACTION_GROUPS = {"PS Warehouse", "Product Scanner", "PS Salesperson", "Sales"}
    in_action_group = user.groups.filter(name__in=ACTION_GROUPS).exists()
    in_viewer_group = user.groups.filter(name="PS Viewer").exists()
    return in_viewer_group and not in_action_group


def can_use_product_scanning(user) -> bool:
    """True if the user has *any* product-scanning access."""
    return can_use_scanner(user) or can_use_dashboard(user)


def get_product_scanning_home_url(user) -> str:
    """Return the most appropriate landing URL for a product-scanning user."""
    if can_use_scanner(user):
        return "/product_scanning/"
    if can_use_dashboard(user):
        return "/product_scanning/dashboard/"
    return "/product_scanning/"


# ---------------------------------------------------------------------------
# View decorators
# ---------------------------------------------------------------------------


def scanner_required(view_func):
    """
    Decorator: user must have scanner access.
    - Warehouse-only users are redirected to the dashboard.
    - Users with no access get PermissionDenied (→ 403).
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if can_use_scanner(request.user):
            return view_func(request, *args, **kwargs)
        if can_use_dashboard(request.user):
            return redirect("/product_scanning/dashboard/")
        raise PermissionDenied

    return wrapper


def dashboard_required(view_func):
    """
    Decorator: user must have dashboard access.
    - Scanner-only users are redirected to the QR form.
    - Users with no access get PermissionDenied (→ 403).
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if can_use_dashboard(request.user):
            return view_func(request, *args, **kwargs)
        if can_use_scanner(request.user):
            return redirect("/product_scanning/")
        raise PermissionDenied

    return wrapper
