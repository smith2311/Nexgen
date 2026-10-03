"""RBAC helpers for MeriTrack — role-based access control."""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

# Groups
REP_GROUPS = {"MT Rep", "MT Manager"}
MANAGER_GROUPS = {"MT Manager", "MT Logistics"}
LOGISTICS_GROUPS = {"MT Logistics"}


def is_mt_rep(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=REP_GROUPS).exists()


def is_mt_manager(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=MANAGER_GROUPS).exists()


def is_mt_logistics(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=LOGISTICS_GROUPS).exists()


def can_access_meritrack(user) -> bool:
    return is_mt_rep(user) or is_mt_manager(user)


def get_meritrack_home_url(user) -> str:
    if is_mt_rep(user):
        return "/meritrack/"
    if is_mt_manager(user):
        return "/meritrack/dashboard/"
    return "/meritrack/"


def rep_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_mt_rep(request.user):
            return view_func(request, *args, **kwargs)
        if is_mt_manager(request.user):
            return redirect("/meritrack/dashboard/")
        raise PermissionDenied

    return wrapper


def manager_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_mt_manager(request.user):
            return view_func(request, *args, **kwargs)
        if is_mt_rep(request.user):
            return redirect("/meritrack/")
        raise PermissionDenied

    return wrapper
