from django.conf import settings

from material_rework.access import can_access_material_rework
from merilv.rbac import can_access_merilv
from vendorlink.rbac import can_access_vendorlink
from main.utils import is_platform_admin

# Display names for Matomo's per-module reporting, keyed by URL application
# namespace (the `app_name` in each app's urls.py). Matomo was only ever told
# the page URL and title, so hits landed in one flat list with no way to see
# per-module totals; base.html now sends this as a custom dimension and as the
# first segment of the page title, which Matomo splits into a hierarchy.
#
# An app missing from this map still reports separately — the fallback
# title-cases its namespace — so a new app needs no change here.
MATOMO_MODULE_NAMES = {
    "product_scanning": "Product Scanning",
    "meritrack": "MeriTrack",
    "shipment_notifications": "Shipment Notifications",
    "ai_sales_agent_dashboard": "AI Sales Agent",
    "logistics": "Logistics Performance",
    "vat": "VAT Reconciliation",
    "warehouse_inspection": "Warehouse Inspection",
    "material_rework": "Material Rework",
    "billing": "Billing Dashboard",
    "visa": "Visa Management",
    "pr_tracker": "PR Tracker",
    "sub": "Subsidiary Logistics",
    "vendorlink": "VendorLink",
    "rs2t": "LiveTranslate",
    # URL prefixes too, for the path fallback below — it runs whenever a URL
    # doesn't resolve (a bare app prefix that has no route of its own, a 404),
    # and these are the prefixes that don't title-case into the right name.
    "shipment": "Shipment Notifications",
    "ai-sales-agent": "AI Sales Agent",
    "pr": "PR Tracker",
}


def _matomo_module(request):
    """Human-readable module for the current request.

    Prefers the resolved URL namespace over the path, so it stays correct if a
    URL prefix is ever changed. `resolver_match` is None on error pages and for
    templates rendered outside the request cycle, hence the guards.
    """
    match = getattr(request, "resolver_match", None)
    namespace = ""
    if match is not None:
        namespace = (getattr(match, "app_name", "") or getattr(match, "namespace", "") or "").strip()

    if namespace:
        return MATOMO_MODULE_NAMES.get(namespace, namespace.replace("_", " ").title())

    # `main` has no app_name and owns the catch-all, so everything unnamespaced
    # lands here: the portal home plus auth/oauth pages.
    first = (getattr(request, "path", "") or "").strip("/").split("/")[0]
    if not first:
        return "Home"
    return MATOMO_MODULE_NAMES.get(first, first.replace("_", " ").replace("-", " ").title())


def _matomo_enabled(request):
    """Whether this request's page should carry the tracker.

    Two gates. `settings.MATOMO_ENABLED` is the manual kill switch, and the
    request host must be one of `settings.MATOMO_HOSTS` — see the note there for
    why the host, and not DEBUG, is what separates production from everything
    else here.

    Fails closed on purpose: a host Django itself rejects, or a template rendered
    with no request at all, tracks nothing rather than reporting under a blank
    or unexpected origin.
    """
    if not settings.MATOMO_ENABLED or request is None:
        return False
    try:
        host = request.get_host()
    except Exception:  # DisallowedHost, or a request object without a host
        return False
    # get_host() keeps the port (`gpt.e-meril.in:8020`); the allowlist is names.
    return host.split(":")[0].strip().lower() in settings.MATOMO_HOSTS


def app_settings(request):
    return {
        "APP_DEBUG": settings.DEBUG,
        "MATOMO_ENABLED": _matomo_enabled(request),
        "matomo_module": _matomo_module(request),
    }


def nav_access(request):
    """
    Exact-match sidebar access flags, exposed to every template as `nav`.

    Replaces the fragile `"<name>" in user_groups|join:","` substring checks in
    base.html. Those false-positive whenever one group name is a substring of
    another — e.g. a user in "AI Sales Dashboard" matched the bare "Sales" token
    and wrongly saw the Stock Audit (Product Scanning) link. Here group
    membership is tested by exact name, and the merillife.com domain via
    endswith. Safe for anonymous users (every flag resolves to False).
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav": {}}

    is_super = is_platform_admin(user)
    groups = set(user.groups.values_list("name", flat=True))
    is_meril = (user.email or "").endswith("@merillife.com")

    def in_groups(*names):
        return any(name in groups for name in names)

    # Warehouse Inspection: section visibility + admin-vs-inspector landing
    wi_admin = is_super or (is_meril and in_groups(
        "Warehouse Inspection Admin", "Warehouse Manager", "warehouse manager"))
    wi_any = is_super or (is_meril and in_groups(
        "Warehouse Inspection Admin", "Warehouse Inspection User",
        "Warehouse Manager", "warehouse manager"))

    nav = {
        "product_scanning": is_super or in_groups(
            "Product Scanner", "Sales", "PS Salesperson", "PS Warehouse"),
        "translator": is_super or in_groups("HR"),
        "meritrack": is_super or in_groups("MT Rep", "MT Manager", "MT Logistics"),
        "ai_sales": is_super or in_groups("AI Sales Dashboard"),
        "logistics": is_super or in_groups(
            "Logistics Performance", "LP-Admin", "LP-Members"),
        "vat": is_super or in_groups(
            "VAT Reconciliation", "VAT-Admin", "VAT-Members"),
        "billing": is_super or in_groups(
            "Billing Dashboard", "Billing-Admin", "Billing-Members"),
        # Visa Management — any authenticated Meril employee (department/HOD/applicant)
        "visa": is_super or is_meril,
        "pr_tracker": is_super or in_groups("PR-Manager", "PR-Admin", "PR-Member"),
        "subsidiary": is_super or in_groups("Sub-Manager", "Sub-Member"),
        "warehouse_inspection": wi_any,
        "warehouse_inspection_admin": wi_admin,
        # Delegates to the module's own list so the sidebar cannot drift from
        # the roles the module actually defines. It used to name two of the
        # seven here, which is why assigning MR Order Management or MR PPIC
        # produced a role with no way to navigate to it. The Meril-staff grant
        # is kept alongside so nobody who can see it today loses it.
        "material_rework": can_access_material_rework(user) or (is_meril and user.is_staff),
        # Grocery Stock — any of the seven roles from specification section 18.
        # Read access is open to all of them (the Auditor is read-only and the
        # Department User needs the item list), so visibility is role presence,
        # not write permission.
        "grocery": is_super or in_groups(
            "Grocery-Admin", "Grocery-Purchase-Manager", "Grocery-Store-Manager",
            "Grocery-Warehouse-Executive", "Grocery-Finance",
            "Grocery-Department-User", "Grocery-Auditor"),
        # GST Reconciliation — either Recon role. The module exposes one
        # vendor's filings to whoever can open it, so visibility is role
        # presence rather than merely being signed in.
        "reconciliation": is_super or in_groups("Recon-Admin", "Recon-User"),
        # Meril-V delegates to its own rbac helper so the sidebar link and the
        # landing tile can never disagree — main.views gates the tile on the
        # same function.
        "merilv": can_access_merilv(user),
        "vendorlink": can_access_vendorlink(user),
        "platform_admin": is_super,
    }
    return {"nav": nav}


def lp_divisions(request):
    """
    Make the Division master available to EVERY template as `lp_divisions`,
    so dropdowns / filter pills can be rendered with a loop instead of hard-coded
    options. Sourced from DivisionMaster (non-deleted, distinct division names).
    Wrapped in try/except so pages still load if the table is missing
    (e.g. before migrations run).
    """
    try:
        from logistics_performance.models import DivisionMaster
        seen, divisions = set(), []
        for name in (DivisionMaster.objects
                     .filter(is_deleted=False).filter(code=101)
                     .order_by("division")
                     .values_list("division", flat=True)):
            if name and name not in seen:
                seen.add(name)
                divisions.append({
                    "code": name,
                    "label": name,
                    "badge_class": "badge-muted",
                })
    except Exception:
        divisions = []
    return {"lp_divisions": divisions}


def lp_masters(request):
    """
    Expose the logistics master lists (zones, modes, states, couriers) to every
    template as `lp_masters`, so advanced-filter dropdowns can be populated
    dynamically from the masters. Wrapped in try/except so pages still load if a
    table is missing (e.g. before migrations run).
    """
    out = {"zones": [], "modes": [], "states": [], "couriers": []}
    try:
        from logistics_performance.models import (
            ZoneMaster, ModeMaster, StateMaster, CourierMaster,
        )
        out["zones"]  = list(ZoneMaster.objects.order_by("zone").values_list("zone", flat=True))
        out["modes"]  = list(ModeMaster.objects.order_by("mode").values_list("mode", flat=True))
        out["states"] = list(StateMaster.objects.order_by("state").values_list("state", flat=True))
        out["couriers"] = [
            {"code": c["courier_code"], "name": c["courier_name"]}
            for c in CourierMaster.objects.order_by("courier_name")
                                  .values("courier_code", "courier_name")
        ]
    except Exception:
        pass
    return {"lp_masters": out}


#: URL namespaces where the floating Feedback button is suppressed.
#:
#: These modules are dense operational screens that already own the
#: bottom-right corner — Meril GPT's composer, the scanner console's status
#: rail, Material Rework's wide action tables — so a fixed z-index-1200 pill
#: sits on top of the controls rather than beside them. Feedback stays one click
#: away in the sidebar on every page.
#:
#: A named list because base.html used to spell this out as a chain of
#: `namespace != "..."` comparisons, and Material Rework was simply never added
#: to the chain.
FEEDBACK_FAB_EXCLUDED = frozenset({"vendorlink", "merilv", "material_rework"})


def feedback_fab(request):
    """Whether the floating Feedback button should render on this page.

    Exposed as a list rather than a boolean so base.html's `not in` test reads
    as the rule it is, and so a template can name the excluded namespaces if it
    ever needs to explain itself.
    """
    return {"FEEDBACK_FAB_EXCLUDED": FEEDBACK_FAB_EXCLUDED}
