"""
Middleware to prevent caching of authentication and sensitive pages.
This helps prevent Zoho session caching issues.
"""

from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin


class NoCacheAuthMiddleware(MiddlewareMixin):
    """
    Middleware to add cache-busting headers to authentication-related pages.
    Prevents browser caching of login, logout, and OAuth pages.
    """

    # Pages that should not be cached
    AUTH_PATHS = [
        "/logout/",
        "/login/",
        "/oauth/",
        "/accounts/",
    ]

    def process_response(self, request, response):
        """Add no-cache headers to auth-related paths"""

        # Check if the current path is an auth-related path
        should_skip_cache = any(request.path.startswith(path) for path in self.AUTH_PATHS)

        if should_skip_cache:
            # Add cache-busting headers
            response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
            response["X-Frame-Options"] = "DENY"

            # Clear sensitive cookies on logout
            if request.path == "/logout/":
                response.delete_cookie("sessionid", path="/")
                response.delete_cookie("csrftoken", path="/")
        else:
            # Authenticated app pages: keep them out of the back/forward cache
            # so pressing Back after logout re-requests the page and lands on
            # login, rather than redisplaying it from cache.
            #
            # This replaces a pushState/popstate block that used to sit in
            # base.html and chase the same goal by calling location.reload() on
            # every popstate. That broke the Back button site-wide; the header
            # is the correct mechanism and costs the user nothing.
            #
            # Only set when the view has not chosen its own policy (never_cache
            # and friends already set one), and only for HTML, so JSON and file
            # responses keep whatever they asked for.
            user = getattr(request, "user", None)
            if (
                user is not None
                and user.is_authenticated
                and not response.has_header("Cache-Control")
                and response.get("Content-Type", "").startswith("text/html")
            ):
                response["Cache-Control"] = "no-store, must-revalidate, private"

        return response


class ForcePasswordChangeMiddleware(MiddlewareMixin):
    """Redirect EXTERNAL VENDOR users to change-password until they set one.

    The gate exists for one situation: an account that was handed a temporary
    password over email must not keep using it. That is how external vendor
    logins are issued.

    It does not apply to Meril staff. They authenticate through Zoho SSO, so
    their credentials live in Zoho and there is no local password for this page
    to change — being sent here is a dead end that blocks the module they were
    trying to open. The docstring always said "vendor users"; the code checked
    only `is_superuser`, so every internal user with the flag set was caught by
    it too.
    """

    CHANGE_PASSWORD_URL = "/vendorlink/vendor/change-password/"

    #: Session key python-social-auth writes on an SSO login.
    SSO_SESSION_KEY = "social_auth_last_login_backend"

    #: Internal domains. Spelled out here rather than imported from
    #: vendorlink.rbac: middleware loads before the app registry is ready, and
    #: a module-level import of an app package at that point is a startup risk.
    MERIL_DOMAINS = ("@merillife.com", "@meril.com", "@meril.in", "@merillife.in")

    EXEMPT_PREFIXES = (
        CHANGE_PASSWORD_URL,
        "/logout/",
        "/oauth/",
        "/static/",
        "/media/",
        "/admin/",
        "/favicon",
    )

    def process_request(self, request):
        if not request.user.is_authenticated:
            return None
        if request.user.is_superuser:
            return None
        if request.path == "/":
            return None
        if any(request.path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return None

        if self._is_internal(request):
            return None

        try:
            if request.user.profile.force_password_change:
                return redirect(self.CHANGE_PASSWORD_URL)
        except Exception:
            pass
        return None

    def _is_internal(self, request):
        """Whether this is Meril staff rather than an external vendor.

        Two signals, either sufficient. The session key is the direct one — it
        says this session was authenticated by Zoho. The domain check covers a
        staff member who reached a page on an older session, or before SSO ran.
        Both are free: a session read and a string compare, no extra query on
        a middleware that sees every request.
        """
        if request.session.get(self.SSO_SESSION_KEY):
            return True
        email = (request.user.email or "").strip().lower()
        return any(email.endswith(domain) for domain in self.MERIL_DOMAINS)
