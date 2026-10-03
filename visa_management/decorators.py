from functools import wraps

from django.shortcuts import redirect


def _visa_role(user):
    """Return the highest visa role for a user: 'admin' > 'hod' > 'applicant'."""
    if not user.is_authenticated:
        return None
    if user.is_superuser or user.groups.filter(name='Visa-Department').exists():
        return 'admin'
    if user.groups.filter(name='Visa-HOD').exists():
        return 'hod'
    return 'applicant'


def visa_login_required(view_fn):
    """Redirect to login if not authenticated."""
    @wraps(view_fn)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/')
        return view_fn(request, *args, **kwargs)
    return wrapper


def visa_require_role(*roles):
    """Require one of the given roles. Also enforces login."""
    def decorator(view_fn):
        @wraps(view_fn)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('/')
            role = _visa_role(request.user)
            if role not in roles:
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden('You do not have permission to access this page.')
            return view_fn(request, *args, **kwargs)
        return wrapper
    return decorator


# NOTE: visa_require_superadmin was removed. It restricted the Letter Template /
# Country Master / Company Master pages to superusers, excluding Visa-Department
# members even though they share the 'admin' role. Visa-Department now owns that
# master data, so those views use visa_require_role('admin') like the rest of
# the department pages.
