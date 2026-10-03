from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden

# ============================================
# ROLE-BASED DECORATORS (Your Existing)
# ============================================


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if request.user.groups.filter(name="Admin").exists():
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("Admin access required")

    return wrapper


def manager_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if request.user.groups.filter(name="Manager").exists():
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("Manager access required")

    return wrapper


def employee_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if request.user.groups.filter(name="Employee").exists():
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("Employee access required")

    return wrapper


def admin_or_manager_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if request.user.groups.filter(name__in=["Admin", "Manager"]).exists():
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("Admin or Manager access required")

    return wrapper


# ============================================
# FLEXIBLE GROUP DECORATOR (New - More Flexible)
# ============================================


def group_required(*group_names):
    """
    Flexible decorator to require one or more groups
    Usage: @group_required('Admin', 'Manager')
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.groups.exists():
                raise PermissionDenied("No role assigned to your account")

            user_groups = request.user.groups.values_list("name", flat=True)
            if not any(g in group_names for g in user_groups):
                raise PermissionDenied(f"Access denied. Required roles: {', '.join(group_names)}")

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


# ============================================
# SSO-ONLY DECORATOR (New)
# ============================================


def sso_required(view_func):
    """Only allow users authenticated via Zoho SSO"""

    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.social_auth.exists():
            return HttpResponseForbidden(
                "This resource requires SSO authentication. " "Please logout and login via Zoho Mail."
            )
        return view_func(request, *args, **kwargs)

    return _wrapped_view


# ============================================
# MERIL EMAIL DECORATOR (New)
# ============================================


def meril_email_required(view_func):
    """Ensure user has @merillife.com email"""

    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.email.endswith("@merillife.com"):
            raise PermissionDenied("Access restricted to Meril Life Sciences employees only")
        return view_func(request, *args, **kwargs)

    return _wrapped_view
