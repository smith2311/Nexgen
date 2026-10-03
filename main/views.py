import logging
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

from material_rework.access import can_access_material_rework
from meritrack.rbac import get_meritrack_home_url
from product_scanning.rbac import get_product_scanning_home_url
from warehouse_inspection.views import is_warehouse_user, is_warehouse_admin
from merilv.rbac import can_access_merilv
from vendorlink.rbac import can_access_vendorlink

from .models import UserProfile
from .utils import is_platform_admin


PS_GROUPS = {"Sales", "Product Scanner", "PS Salesperson", "PS Warehouse", "PS Viewer"}
MT_GROUPS = {"MT Rep", "MT Manager", "MT Logistics"}
PR_GROUPS = {"PR-Manager", "PR-Admin", "PR-Member"}
SUB_GROUPS = {"Sub-Manager", "Sub-Member"}
# The module's two roles. Both may read at least one section, so the tile
# appears for either; which sections open is grocery_stock.roles, and write
# permission is enforced per-endpoint by GroceryRolePermission.
GROCERY_GROUPS = {"Grocery-Admin", "Grocery-Manager"}
RECON_GROUPS = {"Recon-Admin", "Recon-User"}
# No VISA_GROUPS: the Visa Management tile is gated on a @merillife.com email
# rather than group membership, matching the sidebar nav in templates/base.html.
# Every Meril employee is a visa applicant by default — see
# visa_management.decorators._visa_role().


# Cloudflare's verification endpoint. A module constant so the one network
# call this module makes has its destination stated once, in plain sight.
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _verify_turnstile(token, remote_ip):
    if not settings.CLOUDFLARE_TURNSTILE_SECRET_KEY or not token:
        return True  # skip if not configured
    data = urllib.parse.urlencode({
        "secret": settings.CLOUDFLARE_TURNSTILE_SECRET_KEY,
        "response": token,
        "remoteip": remote_ip,
    }).encode()
    try:
        import json
        req = urllib.request.Request(TURNSTILE_VERIFY_URL, data=data)
        # The URL is the module-level https constant above, never anything a
        # caller supplies, so there is no scheme to smuggle here. The scanner
        # flags every urlopen whose target it cannot trace back to a literal.
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
            result = json.loads(resp.read())
            return result.get("success", False)
    except Exception:
        return True  # fail open on network error


class TurnstileLoginView(LoginView):
    template_name = "main/login.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["turnstile_site_key"] = settings.CLOUDFLARE_TURNSTILE_SITE_KEY
        return ctx

    def form_valid(self, form):
        token = self.request.POST.get("cf-turnstile-response", "")
        ip = (
            self.request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or self.request.META.get("REMOTE_ADDR", "")
        )
        if not _verify_turnstile(token, ip):
            form.add_error(None, "Security check failed. Please try again.")
            return self.form_invalid(form)
        return super().form_valid(form)


@login_required
@never_cache
def custom_logout(request):
    """
    Custom logout view that clears all session data
    and forces re-authentication.
    Shows a logout page with loader before redirecting.
    """
    # Clear all session data
    request.session.flush()

    # Log out the user
    logout(request)

    # Clear Zoho social auth session data
    if "social_auth_state" in request.session:
        del request.session["social_auth_state"]

    # Render logout page with loader
    response = render(request, "main/logout.html")

    # Set cache-busting headers to prevent browser caching
    response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["X-Frame-Options"] = "DENY"

    # Clear cookies to ensure complete logout from Zoho
    response.delete_cookie("sessionid", path="/")
    response.delete_cookie("csrftoken", path="/")

    return response


_ALLOWED_DOMAINS = {'merillife.com', 'meril.com', 'meril.in', 'merillife.in'}


def register_view(request):
    """Self-service registration — creates a Django auth user directly (no SSO required)."""
    if request.user.is_authenticated:
        return redirect('landing_page')

    error = None
    form_data = {}

    if request.method == 'POST':
        first_name   = request.POST.get('first_name', '').strip()
        last_name    = request.POST.get('last_name', '').strip()
        email        = request.POST.get('email', '').strip().lower()
        password     = request.POST.get('password', '')
        password2    = request.POST.get('password2', '')
        form_data    = {'first_name': first_name, 'last_name': last_name, 'email': email}

        domain = email.split('@')[-1] if '@' in email else ''

        if not all([first_name, last_name, email, password, password2]):
            error = 'All fields are required.'
        elif domain not in _ALLOWED_DOMAINS:
            error = 'Registration is restricted to Meril email addresses (@merillife.com, @meril.com, etc.).'
        elif len(password) < 8:
            error = 'Password must be at least 8 characters.'
        elif password != password2:
            error = 'Passwords do not match.'
        elif User.objects.filter(email__iexact=email).exists():
            error = 'An account with this email already exists. Please log in instead.'
        else:
            username = email.split('@')[0]
            base     = username
            counter  = 1
            while User.objects.filter(username=username).exists():
                username = f'{base}{counter}'
                counter += 1

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            login(request, user, backend='main.backends.UsernameOrEmailBackend')
            messages.success(request, f'Welcome, {first_name}! Your account has been created.')
            return redirect('landing_page')

    return render(request, 'main/register.html', {'error': error, 'form_data': form_data})


@login_required
@never_cache
def landing_page(request):
    """
    Landing page after SSO login.
    Displays applications available to the user based on their role/permissions.
    """

    user = request.user
    applications = []  # ← Start with empty list

    # Helper: plain is_staff is not enough for full access, because some staff
    # flags exist only to open Django admin for one module. Production platform
    # admins, however, may be staff accounts with the complete auth user/group
    # permission set rather than superusers.
    is_full_admin = is_platform_admin(user)

    # Speech & Language AI
    if user.groups.filter(name="HR").exists() or is_full_admin:
        applications.append(
            {
                "name": "Realtime Speech-to-Text Translator",
                "description": "Realtime speech to text translation for multiple languages",
                "url": "/rs2t/",
                "icon": "fas fa-microphone-alt",
                "color": "blue",
                "badge": "new",
                "stats": [
                    {"icon": "fas fa-wave-square", "text": "Live Translation"},
                ],
            }
        )

    # SA Product Scanning
    if user.groups.filter(name__in=PS_GROUPS).exists() or is_full_admin:
        ps_url = get_product_scanning_home_url(user)
        applications.append(
            {
                "name": "SA Product Scanning",
                "description": "QR code scanning for South Africa hospital product utilization and stock audit tracking",
                "url": ps_url,
                "icon": "fas fa-qrcode",
                "color": "purple",
                "badge": "new",
                "stats": [
                    {"icon": "fas fa-mobile-alt", "text": "Mobile Scanning"},
                    {"icon": "fas fa-hospital-user", "text": "Hospital Tracking"},
                    {"icon": "fas fa-calendar-times", "text": "Expiry Tracking"},
                ],
            }
        )

    # MeriTrack — Consignment & Inventory Tracking
    if user.groups.filter(name__in=MT_GROUPS).exists() or is_full_admin:
        mt_url = get_meritrack_home_url(user)
        applications.append(
            {
                "name": "MeriTrack",
                "description": "Consignment inventory tracking, SAP posting analytics, and hospital stock management",
                "url": mt_url,
                "icon": "fas fa-heartbeat",
                "color": "teal",
                "badge": "new",
                "stats": [
                    {"icon": "fas fa-hospital", "text": "Hospital Inventory"},
                    {"icon": "fas fa-barcode", "text": "Barcode Scanning"},
                    {"icon": "fas fa-chart-bar", "text": "SAP Analytics"},
                ],
            }
        )

    # Warehouse Inspection
    if is_warehouse_user(user):
        _wi_url = (
            reverse('warehouse_inspection:dashboard')
            if is_warehouse_admin(user)
            else reverse('warehouse_inspection:inspection_form')
        )
        applications.append({
            'name': 'Warehouse Inspection',
            'description': 'Warehouse condition management, inspection checklists, and compliance tracking',
            'url': _wi_url,
            'icon': 'fas fa-warehouse',
            'color': 'green',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-clipboard-check', 'text': 'Inspections'},
                {'icon': 'fas fa-list-check', 'text': 'Checklists'},
                {'icon': 'fas fa-chart-bar', 'text': 'Reports'},
            ]
        })

    # Material Rework
    # Same source as the sidebar — this list had four of the seven roles and
    # the two disagreed about who the module was for.
    if can_access_material_rework(user) or is_full_admin:
        applications.append({
            'name': 'Material Rework',
            'description': 'Upload, approve and track material rework through the complete workflow',
            'url': reverse('material_rework:dashboard'),
            'icon': 'fas fa-recycle',
            'color': 'blue',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-upload', 'text': 'Upload Batches'},
                {'icon': 'fas fa-clipboard-check', 'text': 'HOD Approval'},
                {'icon': 'fas fa-chart-bar', 'text': 'Full Report'},
            ]
        })

    # VendorLink — 3PL Vendor Database & Innovation Platform
    if can_access_vendorlink(user):
        applications.append({
            "name": "VendorLink",
            "description": "3PL Vendor Database & Innovation Platform — Vendor Master, commercials, capabilities and analytics",
            "url": reverse("vendorlink:index"),
            "icon": "fas fa-cubes",
            "color": "purple",
            "badge": "new",
            "stats": [
                {"icon": "fas fa-truck-field", "text": "Vendor Master"},
                {"icon": "fas fa-globe", "text": "Country Insights"},
                {"icon": "fas fa-shield-halved", "text": "Capability Matrix"},
            ],
        })

    # AI Sales Hub
    if user.groups.filter(name="AI Sales Dashboard").exists() or is_full_admin:
        applications.append(
            {
                "name": "AI Sales Hub",
                "description": "AI-powered lead generation, call intelligence, and follow-up automation",
                "url": reverse("ai_sales_agent_dashboard:dashboard_home"),
                "icon": "fas fa-robot",
                "color": "cyan",
                "badge": "new",
                "stats": [
                    {"icon": "fas fa-bullseye", "text": "Lead Generation"},
                    {"icon": "fas fa-phone-volume", "text": "Call Intelligence"},
                    {"icon": "fas fa-file-signature", "text": "Follow-up Drafting"},
                ],
            }
        )

    # Meril-V — warehouse validation & outbound operations
    if can_access_merilv(user):
        applications.append(
            {
                "name": "Meril-V",
                "description": "Barcode stock validation, outbound dispatch scanning, and master data",
                "url": reverse("merilv:index"),
                "icon": "fas fa-boxes-packing",
                "color": "teal",
                "badge": "new",
                "stats": [
                    {"icon": "fas fa-clipboard-check", "text": "Stock Validation"},
                    {"icon": "fas fa-truck-fast", "text": "Outbound Scanning"},
                    {"icon": "fas fa-barcode", "text": "Barcode Extractor"},
                ],
            }
        )

    # Logistics Performance
    if user.groups.filter(name__in=['Logistics Performance', 'LP-Admin', 'LP-Members']).exists() or is_full_admin:
        applications.append({
            'name': 'Logistics Performance',
            'description': 'Track shipment performance, manage location master, and monitor delivery TAT metrics',
            'url': '/logistics/',
            'icon': 'fas fa-shipping-fast',
            'color': 'blue',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-map-marker-alt', 'text': 'Location Master'},
                {'icon': 'fas fa-tags', 'text': 'Sales Type Master'},
                {'icon': 'fas fa-clock', 'text': 'TAT Tracking'},
            ],
        })

    # VAT Reconciliation
    if user.groups.filter(name__in=['VAT Reconciliation', 'VAT-Admin', 'VAT-Members']).exists() or is_full_admin:
        applications.append({
            'name': 'VAT Reconciliation',
            'description': 'Reconcile VAT on invoices, manage country & rate masters, and CHA email mappings',
            'url': '/vat/',
            'icon': 'fas fa-receipt',
            'color': 'purple',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-file-invoice', 'text': 'Invoice Upload'},
                {'icon': 'fas fa-percent', 'text': 'VAT Rate Master'},
                {'icon': 'fas fa-globe', 'text': 'Country Master'},
            ],
        })

    # Export Billing Dashboard
    if user.groups.filter(name__in=['Billing Dashboard', 'Billing-Admin', 'Billing-Members']).exists() or is_full_admin:
        applications.append({
            'name': 'Export Billing Dashboard',
            'description': 'Upload SAP billing dumps and analyse billing by subsidiary, country, region, LOB and month',
            'url': '/billing/',
            'icon': 'fas fa-file-invoice-dollar',
            'color': 'teal',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-cloud-upload-alt', 'text': 'Dump Upload'},
                {'icon': 'fas fa-globe', 'text': 'Country / Region'},
                {'icon': 'fas fa-chart-line', 'text': 'Monthly Trend'},
            ],
        })

    # PR Tracker
    if user.groups.filter(name__in=PR_GROUPS).exists() or is_full_admin:
        applications.append({
            'name': 'PR Tracker',
            'description': 'Assign, track, and close Purchase Requests with ageing and TAT visibility',
            'url': reverse('pr_tracker:home'),
            'icon': 'fas fa-file-invoice',
            'color': 'teal',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-gauge-high', 'text': 'Dashboard'},
                {'icon': 'fas fa-clock', 'text': 'Ageing / TAT'},
                {'icon': 'fas fa-hashtag', 'text': 'Tracking Master'},
            ],
        })

    # Visa Management — any authenticated Meril employee can apply (matches sidebar nav)
    if (user.email or "").endswith("@merillife.com") or is_full_admin:
        applications.append({
            'name': 'Visa Management',
            'description': 'Submit, approve, and issue business visa invitation letters',
            'url': reverse('visa:home'),
            'icon': 'fas fa-passport',
            'color': 'blue',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-user-check', 'text': 'HOD Approval'},
                {'icon': 'fas fa-file-alt', 'text': 'Letter Templates'},
                {'icon': 'fas fa-stamp', 'text': 'Issue & Track'},
            ],
        })

    # Logistics Cost (Subsidiary)
    if user.groups.filter(name__in=SUB_GROUPS).exists() or is_full_admin:
        applications.append({
            'name': 'Logistics Cost',
            'description': 'Upload and analyse subsidiary logistics cost entries',
            'url': reverse('sub:home'),
            'icon': 'fas fa-truck-ramp-box',
            'color': 'green',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-cloud-upload-alt', 'text': 'Upload'},
                {'icon': 'fas fa-gauge-high', 'text': 'Dashboard'},
                {'icon': 'fas fa-coins', 'text': 'Cost Analysis'},
            ],
        })

    # Grocery Stock & Cost Management — either grocery role (Admin, Manager)
    if user.groups.filter(name__in=GROCERY_GROUPS).exists() or is_full_admin:
        applications.append({
            'name': 'Grocery Stock Management',
            'description': 'Grocery item masters, suppliers, warehouses and bin locations, '
                           'with stock and cost management to follow',
            'url': reverse('grocery_stock:home'),
            'icon': 'fas fa-basket-shopping',
            'color': 'teal',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-box-open', 'text': 'Item Master'},
                {'icon': 'fas fa-truck-field', 'text': 'Suppliers'},
                {'icon': 'fas fa-warehouse', 'text': 'Warehouses'},
            ],
        })

    # 3 Way Reconciliation — either Recon role (Admin, Member)
    if user.groups.filter(name__in=RECON_GROUPS).exists() or is_full_admin:
        applications.append({
            'name': '3 Way Reconciliation',
            'description': 'Reconcile GSTR-1, E-Invoice and E-Way Bill document by '
                           'document, with reason remarks and a filing summary',
            'url': reverse('reconciliation:records'),
            'icon': 'fas fa-scale-balanced',
            'color': 'blue',
            'badge': 'new',
            'stats': [
                {'icon': 'fas fa-cloud-arrow-up', 'text': 'Import export'},
                {'icon': 'fas fa-triangle-exclamation', 'text': 'Exceptions'},
                {'icon': 'fas fa-chart-column', 'text': 'Filing Summary'},
            ],
        })

    # System Admin (full platform admins)
    if is_full_admin:
        applications.append(
            {
                "name": "System Admin",
                "description": "User management, system configuration, and logs",
                "url": "/admin/",
                "icon": "fas fa-cog",
                "color": "red",
                "stats": [
                    {"icon": "fas fa-shield-alt", "text": "Secure"},
                ],
            }
        )

    context = {
        "applications": applications,
    }

    return render(request, "main/landing_page.html", context)


def _get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


@login_required
@never_cache
def profile_view(request):
    profile = _get_or_create_profile(request.user)
    context = {
        "profile": profile,
        "active_tab": request.GET.get("tab", "profile"),
    }
    return render(request, "main/profile.html", context)


@login_required
@never_cache
@require_http_methods(["POST"])
def profile_update(request):
    profile = _get_or_create_profile(request.user)
    user = request.user

    # Update User fields
    user.first_name = request.POST.get("first_name", user.first_name).strip()
    user.last_name = request.POST.get("last_name", user.last_name).strip()
    user.email = request.POST.get("email", user.email).strip()
    user.save(update_fields=["first_name", "last_name", "email"])

    # Update Profile fields
    profile.phone = request.POST.get("phone", profile.phone).strip()
    profile.department = request.POST.get("department", profile.department).strip()
    profile.designation = request.POST.get("designation", profile.designation).strip()
    profile.location = request.POST.get("location", profile.location).strip()
    profile.bio = request.POST.get("bio", profile.bio).strip()

    if "avatar" in request.FILES:
        profile.avatar = request.FILES["avatar"]

    profile.save()
    messages.success(request, "Profile updated successfully.")
    return redirect(reverse("profile") + "?tab=profile")


@login_required
@never_cache
@require_http_methods(["POST"])
def settings_update(request):
    profile = _get_or_create_profile(request.user)
    form_type = request.POST.get("form_type", "preferences")

    if form_type == "notifications":
        profile.notif_email = request.POST.get("notif_email") == "on"
        profile.notif_browser = request.POST.get("notif_browser") == "on"
        profile.notif_shipment = request.POST.get("notif_shipment") == "on"
        profile.notif_orders = request.POST.get("notif_orders") == "on"
        profile.notif_inventory = request.POST.get("notif_inventory") == "on"
        profile.save(
            update_fields=["notif_email", "notif_browser", "notif_shipment", "notif_orders", "notif_inventory"]
        )
        messages.success(request, "Notification preferences saved.")
        return redirect(reverse("profile") + "?tab=notifications")

    profile.timezone = request.POST.get("timezone", profile.timezone)
    profile.save(update_fields=["timezone"])
    messages.success(request, "Preferences saved successfully.")
    return redirect(reverse("profile") + "?tab=settings")


def oauth_begin_clean(request, backend, *args, **kwargs):
    """Start an SSO flow, dropping any leftover post-login redirect.

    ``social_core.actions.do_auth`` writes ``session["next"]`` *only* when the
    incoming request actually carries one, and ``do_complete`` reads that value
    back without ever clearing it. A ``next`` stored by one sign-in therefore
    survives into the next one: land on ``/?next=/material-rework/...`` once --
    a bookmark, an ``@login_required`` bounce, a session that expired while on
    that page -- and from then on every plain "Sign in with Zoho" from the bare
    login page replays it, dropping the user straight into Material Rework
    instead of the launcher.

    Clearing it only when this request has no ``next`` of its own keeps the
    useful half of the behaviour: a fresh deep link is still honoured (which
    epcg_license and subsidiary_logistic both rely on), while a stale one
    becomes impossible to replay.
    """
    from social_django.views import auth as _social_auth_begin

    if "next" not in request.GET and "next" not in request.POST:
        request.session.pop("next", None)
    return _social_auth_begin(request, backend, *args, **kwargs)


def safe_oauth_complete(request, backend, *args, **kwargs):
    """
    Wraps social-auth's OAuth complete view with broad exception handling.

    When a user opens an email link then presses browser Back, the browser
    replays the OAuth callback URL with an already-consumed one-time code.
    Zoho rejects it, social-core raises an exception that SocialAuthExceptionMiddleware
    does NOT catch (it only handles SocialAuthBaseException subclasses), and the
    unhandled exception crashes the single Daphne worker — taking SSO down for
    every user.  This wrapper ensures any exception redirects cleanly to login.
    """
    from social_django.views import complete as _social_auth_complete

    try:
        return _social_auth_complete(request, backend, *args, **kwargs)
    except Exception as exc:
        logger.exception("OAuth complete failed for backend=%s: %s", backend, exc)
        messages.error(request, "Sign-in failed or the login link has expired. Please try again.")
        return redirect("/")
