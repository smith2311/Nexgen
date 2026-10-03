"""Small view helpers shared across the platform apps."""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme


PLATFORM_ADMIN_PERMS = (
    "auth.add_user",
    "auth.change_user",
    "auth.delete_user",
    "auth.view_user",
    "auth.add_group",
    "auth.change_group",
    "auth.delete_group",
    "auth.view_group",
)


def is_platform_admin(user) -> bool:
    """True for accounts that should see the whole NexGen launcher.

    Some production administrators are deliberately not Django superusers, but
    they do hold the full user/group permission set in Django admin. The portal
    used to ignore those permissions and only honoured ``is_superuser``, so a
    fully-authorized staff account still saw a role-limited app list.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return bool(getattr(user, "is_staff", False) and user.has_perms(PLATFORM_ADMIN_PERMS))


def safe_back_url(request, fallback="/dashboard/"):
    """Where a page's "Back" button should point.

    Pages like the feedback portal are reachable from every app, so a
    hard-coded target sends people somewhere they were never working. This
    resolves the referrer instead, and falls back when it can't be trusted:

    * no referrer — deep link, bookmark, new tab, or a browser withholding it
    * off-site referrer — never bounce a user out of the platform
    * the current page — a Back button that reloads the page you're on
    * the login page — a dead end; the user lands straight back here

    SecurityMiddleware's default Referrer-Policy is ``same-origin``, so
    in-app navigation does send the header.
    """
    referer = request.META.get("HTTP_REFERER") or ""
    if not referer:
        return fallback

    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return fallback

    parts = urlsplit(referer)
    if parts.path in {request.path, settings.LOGIN_URL}:
        return fallback

    return parts.path + (f"?{parts.query}" if parts.query else "")


def invalidate_user_sessions(user) -> int:
    """Delete every live Django session authenticated as ``user``."""
    if not getattr(user, "pk", None):
        return 0

    removed = 0
    wanted = str(user.pk)
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get("_auth_user_id") or "") != wanted:
            continue
        session.delete()
        removed += 1
    return removed
