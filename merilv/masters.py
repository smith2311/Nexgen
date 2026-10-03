"""Master-data console for Meril-V.

Materials, customers, couriers, cartons and the per-entity parsing rules. All of
it is admin-only: these tables drive how every scan is interpreted, so a bad
edit here breaks the warehouse floor rather than one user's screen.
"""

from __future__ import annotations

import json
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import ProtectedError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    ActivityLog,
    BillingTypeMaster,
    BoxProfile,
    CompanyMaster,
    CourierMaster,
    CustomerCourierMapping,
    CustomerMaster,
    Entity,
    MaterialMaster,
    OrganizationMaster,
    PlantMaster,
    PlantOrgMapping,
    SalesTypeMaster,
    StorageMaster,
)
from . import activitylog
from . import transit
from .pagination import paginate
from .params import int_or_none
from . import permissions as capabilities
from .permissions import capability_required
from .rbac import can_access_merilv, entities_for, is_merilv_admin
from .roles import ROLE_GROUPS as ROLE_GROUP_NAMES
from urllib.parse import quote

from .services import export, ingest

access_required = user_passes_test(can_access_merilv, login_url="landing_page")
admin_required = user_passes_test(is_merilv_admin, login_url="merilv:index")


#: Every master the console can open, grouped as the console nav shows them.
#: ``group`` is the <optgroup> heading; entries carrying ``table`` route through
#: the one shared :func:`org` view via ``?table=``.
MASTER_TABS: tuple[dict[str, str], ...] = (
    # The tree heads its own group deliberately. The four tables under it are
    # each one level of one hierarchy, and listing them after the whole-tree
    # view is what makes that readable from the navigation itself — the four
    # then run top-down, so their order in the strip IS their nesting.
    {"key": "org_tree",     "label": "Hierarchy",     "group": "Organisation structure", "icon": "layers", "url": "merilv:masters_org_tree"},
    {"key": "company",      "label": "Company",       "group": "Organisation structure", "icon": "building", "url": "merilv:masters_org", "table": "company"},
    {"key": "organization", "label": "Sales Org",     "group": "Organisation structure", "icon": "layers",   "url": "merilv:masters_org", "table": "organization"},
    {"key": "plant",        "label": "Plant",         "group": "Organisation structure", "icon": "building", "url": "merilv:masters_org", "table": "plant"},
    {"key": "storage",      "label": "Storage Location", "group": "Organisation structure", "icon": "box",   "url": "merilv:masters_org", "table": "storage"},
    {"key": "billing_type", "label": "Billing Type",  "group": "Document types",         "icon": "file",     "url": "merilv:masters_org", "table": "billing_type"},
    {"key": "sales_type",   "label": "Sales Type",    "group": "Document types",         "icon": "file",     "url": "merilv:masters_org", "table": "sales_type"},
    {"key": "materials",    "label": "Material",      "group": "Scan masters",           "icon": "package",  "url": "merilv:masters_materials"},
    {"key": "customers",    "label": "Customer",      "group": "Scan masters",           "icon": "users",    "url": "merilv:masters_customers"},
    {"key": "couriers",     "label": "Courier",       "group": "Dispatch masters",       "icon": "truck",    "url": "merilv:masters_couriers"},
    {"key": "boxes",        "label": "Carton",        "group": "Dispatch masters",       "icon": "box",      "url": "merilv:masters_boxes"},
    {"key": "entities",     "label": "Entity Rules",  "group": "Configuration",          "icon": "key",      "url": "merilv:masters_entities"},
    {"key": "roles",        "label": "Roles & Access", "group": "Configuration",         "icon": "users",    "url": "merilv:masters_roles"},
)


def _shell(active: str = "") -> dict:
    """Context for the shared console chrome (hero card + master nav).

    Deliberately query-free: the nav shows no per-table counts, so the chrome
    costs nothing to render on all twelve screens. Each screen's own record count
    is shown on its card head instead, from data it already has.

    ``mv_groups`` preserves :data:`MASTER_TABS` order within each group and the
    order the groups are first mentioned, so the nav never reshuffles — which for
    the organisation group means its four tables stay in hierarchy order.
    """
    groups: dict[str, list[dict]] = {}
    active_tab = None
    for tab in MASTER_TABS:
        href = reverse(tab["url"])
        if tab.get("table"):
            href = f"{href}?table={tab['table']}"
        entry = {**tab, "href": href, "is_active": tab["key"] == active}
        if entry["is_active"]:
            active_tab = entry
        groups.setdefault(tab["group"], []).append(entry)

    return {
        "mv_groups": [{"label": label, "tabs": tabs} for label, tabs in groups.items()],
        "mv_active_tab": active,
        "mv_active_label": active_tab["label"] if active_tab else "",
        # Only the spreadsheet-backed masters can offer a template. Entity Rules
        # and Roles are configuration, not columns, so the console's template
        # button hides itself on those two rather than 404ing.
        "mv_active_sheet": active if active in MASTER_SHEETS else "",
    }


def _ctx(request, active_tab="", **extra):
    context = {
        "is_mv_admin": is_merilv_admin(request.user),
        # Kept in step with views._ctx: the masters screens draw the Import
        # control, and master_import is gated on this capability — a button
        # whose endpoint answers 403 is worse than no button.
        "mv_can_master_upload": capabilities.can(request.user, "mv_master_upload"),
        "entities": entities_for(request.user),
        # Same list the Finish Delivery radios use — the courier master is the
        # other place a mode is chosen, and the two must offer the same set.
        "transit_modes": transit.choices(),
    }
    context.update(_shell(active_tab))
    context.update(extra)
    return context


def _log(request, action, target_type="", target_id="", **metadata):
    ActivityLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata,
    )


def _xlsx_response(buffer, filename):
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@admin_required
def console(request):
    """Console landing: the tab strip, then recent activity and per-entity counts.

    The old tile grid is gone — the tab strip in the shared chrome is now the way
    into each master, so a second set of navigation cards under it was just the
    same links twice. The two summary panels below it are unchanged.
    """
    per_entity = (
        MaterialMaster.objects.values("entity__code").annotate(n=Count("id")).order_by("-n")
    )
    recent = ActivityLog.objects.select_related("actor")[:10]
    totals = {
        "records": f"{sum(sheet['model'].objects.count() for sheet in MASTER_SHEETS.values()):,}",
        "tables": len(MASTER_SHEETS),
        "users": f"{User.objects.filter(groups__name__in=ROLE_GROUP_NAMES).distinct().count():,}",
    }
    return render(
        request,
        "merilv/masters.html",
        _ctx(request, per_entity=list(per_entity), recent=recent, totals=totals),
    )


# ─── Materials ───────────────────────────────────────────────────────────────
@login_required
@admin_required
def materials(request):
    rows = MaterialMaster.objects.select_related("entity")
    query = request.GET.get("q", "").strip()
    if query:
        rows = rows.filter(
            Q(material_code__icontains=query)
            | Q(scan_code__icontains=query)
            | Q(material_description__icontains=query)
            | Q(gtin__icontains=query)
            # The YMM061 columns are searchable too — the client identifies
            # materials by EAN and by product hierarchy, not only by code.
            | Q(ean_upc__icontains=query)
            | Q(material_group__icontains=query)
            | Q(material_group_desc__icontains=query)
            | Q(ph_level_01__icontains=query)
            | Q(ph_level_01_desc__icontains=query)
        )
    entity_code = request.GET.get("entity", "").strip()
    if entity_code:
        rows = rows.filter(entity__code=entity_code)

    # Grouping filters. The client asked for the material master to be grouped by
    # material group and product hierarchy ("Layout feature ... particularly for
    # Material Master grouping"); these are the server-side half of that.
    group_filter = request.GET.get("group", "").strip()
    if group_filter:
        rows = rows.filter(material_group=group_filter)
    ph_filter = request.GET.get("ph", "").strip()
    if ph_filter:
        rows = rows.filter(ph_level_01=ph_filter)

    # Plant and the two management flags, from the YMM061 restructure. The
    # flags are tri-state in the URL — absent means "either", so the default
    # list is unfiltered rather than silently hiding the non-serialised half.
    plant_filter = request.GET.get("plant", "").strip()
    if plant_filter:
        rows = rows.filter(plant=plant_filter)
    batch_filter = request.GET.get("batch_managed", "").strip()
    if batch_filter in ("1", "0"):
        rows = rows.filter(batch_managed=batch_filter == "1")
    serial_filter = request.GET.get("serial_managed", "").strip()
    if serial_filter in ("1", "0"):
        rows = rows.filter(serial_managed=serial_filter == "1")

    # Option lists are scoped to the chosen entity so the dropdowns only offer
    # values that can actually return rows.
    scoped = MaterialMaster.objects.all()
    if entity_code:
        scoped = scoped.filter(entity__code=entity_code)
    # ``.order_by()`` clears Meta.ordering before DISTINCT, and that call is
    # load-bearing. Django appends the ordering columns to the SELECT so it can
    # sort, and SELECT DISTINCT then dedupes on (value, entity_id,
    # material_code) — which is unique per row. The dropdowns were rendering
    # one <option> per material: 32,299 of them for a plant list with three
    # real values, and the same for material group (490 real) and PH level
    # (17). Deduping in Python instead would work but would drag every row
    # across for a list of a few dozen strings.

    def _options(field):
        return sorted(
            value
            for value in scoped.values_list(field, flat=True).order_by().distinct()
            if value
        )

    groups = _options("material_group")
    hierarchies = _options("ph_level_01")
    plants = _options("plant")

    return render(
        request,
        "merilv/masters_materials.html",
        _ctx(
            request,
            active_tab="materials",
            page_obj=paginate(request, rows),
            result_count=rows.count(),
            q=query,
            entity_code=entity_code,
            group_filter=group_filter,
            ph_filter=ph_filter,
            groups=groups,
            hierarchies=hierarchies,
            plants=plants,
            plant_filter=plant_filter,
            batch_filter=batch_filter,
            serial_filter=serial_filter,
        ),
    )


@login_required
@admin_required
def material_create(request):
    """Add one material by hand — the counterpart to the bulk import above.

    Useful for a single new SKU; anything more than a handful of rows belongs
    in a spreadsheet import instead.
    """
    if request.method == "POST":
        entity = Entity.objects.filter(code=request.POST.get("entity")).first()
        scan_code = (request.POST.get("scan_code") or "").strip().upper()
        material_code = (request.POST.get("material_code") or "").strip().upper() or scan_code
        errors = []
        if entity is None:
            errors.append("Choose a business unit.")
        if not scan_code:
            errors.append("Scan code is required.")
        if not errors and MaterialMaster.objects.filter(entity=entity, scan_code=scan_code).exists():
            errors.append(f"{scan_code} already exists for {entity.code}. Edit it from the list instead.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            material = MaterialMaster.objects.create(
                entity=entity,
                scan_code=scan_code,
                material_code=material_code,
                material_description=(request.POST.get("material_description") or "").strip(),
                gtin=(request.POST.get("gtin") or "").strip(),
                material_group=(request.POST.get("material_group") or "").strip(),
            )
            _log(request, "material_create", "MaterialMaster", material.pk, entity=entity.code)
            messages.success(request, f"{scan_code} added to {entity.code}.")
            return redirect("merilv:masters_materials")

    return render(request, "merilv/masters_material_create.html", _ctx(request))


#: Fields the inline material editor may write. Only keys actually present in the
#: POST are applied, so a modal showing a subset never blanks the rest.
MATERIAL_EDIT_FIELDS = (
    "material_code", "material_description", "gtin", "material_group",
    # YMM061 restructure. batch_managed is not listed: it is a boolean, and an
    # unchecked checkbox posts nothing at all, so "only keys present in the
    # POST are applied" would make it impossible to ever turn off. It is
    # handled explicitly in material_save instead. serial_managed is derived
    # from the profile by the model and is never written directly.
    "plant", "storage_location", "serial_profile",
)


@login_required
@admin_required
@require_POST
def material_save(request):
    """Update one material from the list's inline editor.

    Edit only — creating still goes through the dedicated page, which is where the
    entity/scan-code uniqueness rules are explained. ``scan_code`` and ``entity``
    together are the natural key, so both are re-validated here: renaming a scan
    code into one that already exists for that entity would otherwise fail on the
    database constraint with an unreadable error.
    """
    material = MaterialMaster.objects.filter(pk=int_or_none(request.POST.get("pk"))).first()
    if material is None:
        messages.error(request, "That material no longer exists.")
        return redirect("merilv:masters_materials")

    scan_code = (request.POST.get("scan_code") or "").strip().upper()
    if not scan_code:
        messages.error(request, "Scan code is required.")
        return redirect("merilv:masters_materials")

    clash = (
        MaterialMaster.objects.filter(entity=material.entity, scan_code=scan_code)
        .exclude(pk=material.pk)
        .exists()
    )
    if clash:
        messages.error(
            request,
            f"{scan_code} already exists for {material.entity.code}. Edit that row instead.",
        )
        return redirect("merilv:masters_materials")

    # Snapshot BEFORE the mutation. Comparing the POST against the old row
    # would report what was submitted; comparing the saved row reports what
    # actually landed — which matters here, because `MaterialMaster.save()`
    # derives `serial_managed` from `serial_profile`, and a derived change is
    # exactly the kind an auditor needs to see and a submitter cannot (#23).
    before = activitylog.snapshot(material)

    material.scan_code = scan_code
    for field in MATERIAL_EDIT_FIELDS:
        if field in request.POST:
            setattr(material, field, (request.POST.get(field) or "").strip())
    # An unchecked checkbox posts nothing, so the presence test above can never
    # clear a boolean. The form carries a hidden marker naming the checkbox it
    # is responsible for; when that marker is posted, absence means "off".
    if "batch_managed_present" in request.POST:
        material.batch_managed = MaterialMaster.coerce_flag(request.POST.get("batch_managed"))
    # serial_managed is derived from serial_profile in save() — never set here.
    material.save()

    material.refresh_from_db()
    _log(
        request, "material_update", "MaterialMaster", material.pk,
        entity=material.entity.code,
        changes=activitylog.diff_snapshots(before, activitylog.snapshot(material)),
    )
    messages.success(request, f"{scan_code} updated.")
    return redirect(_redirect_back(request, "merilv:masters_materials"))


def _redirect_back(request, fallback: str) -> str:
    """Return to the list the edit was launched from, filters and page intact.

    The modals post a ``next`` carrying the current query string; without it a
    save from page 40 of a filtered list would dump the admin back at page 1 of
    everything. Only same-path values are honoured, so ``next`` cannot be used to
    bounce a logged-in admin off-site.
    """
    target = (request.POST.get("next") or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return reverse(fallback)


# ─── Customers ───────────────────────────────────────────────────────────────
@login_required
@admin_required
def customers(request):
    rows, filters = _filtered_customers(request)
    return render(
        request,
        "merilv/masters_customers.html",
        _ctx(
            request,
            active_tab="customers",
            page_obj=paginate(request, rows),
            result_count=rows.count(),
            q=filters["q"],
            courier_filter=filters["courier"],
            plant_filter=filters["plant"],
            state_filter=filters["state"],
            entity_filter=filters["entity"],
            # The couriers a Default-courier cell can offer as suggestions, and
            # the same list the bulk-assign picker uses.
            courier_options=_active_courier_names(),
            # The slices a bulk assignment can be made through. Each is
            # derived from the data, so a field nobody populates renders no
            # picker at all rather than an empty one — `order_by()` clears
            # Meta.ordering before DISTINCT, without which the ordering column
            # joins the SELECT and every row yields its own "distinct" value.
            plant_options=sorted(
                value for value in CustomerMaster.objects.exclude(plant="")
                .values_list("plant", flat=True).order_by().distinct() if value
            ),
            state_options=sorted(
                value for value in CustomerMaster.objects.exclude(state="")
                .values_list("state", flat=True).order_by().distinct() if value
            ),
            entity_options=sorted(
                value for value in CustomerMaster.objects.exclude(entity__isnull=True)
                .values_list("entity__code", flat=True).order_by().distinct() if value
            ),
        ),
    )


#: Ceiling on one bulk courier assignment. The whole point is to touch thousands
#: of rows at once, so this is not a safety rail against size — it is a rail
#: against a *runaway* filter (an empty one on a much larger master than today's
#: 7,480). Raise it if the customer master grows past this legitimately.
BULK_COURIER_CAP = 20_000


def _filtered_customers(request):
    """The customer queryset the list shows, and the filters that produced it.

    One function so the list and the bulk assignment cannot disagree about what
    "the current selection" means — the bulk action re-derives the filter rather
    than trusting a list of ids from the client, so the count on the button and
    the rows actually written come from the same query.
    """
    rows = CustomerMaster.objects.select_related("entity")
    query = request.GET.get("q", "").strip() or request.POST.get("q", "").strip()
    if query:
        rows = rows.filter(
            Q(customer_code__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(city__icontains=query)
            | Q(gstin__icontains=query)
        )
    # #18 — where the Courier module's usage links land. Matched
    # case-insensitively and exactly (not `icontains`): "Gati" must not pull in
    # every customer of a courier merely named something containing "Gati".
    courier_filter = (
        request.GET.get("courier", "").strip() or request.POST.get("courier", "").strip()
    )
    if courier_filter:
        rows = rows.filter(courier_partner__iexact=courier_filter)
    # Plant is kept although it is empty on all 7,480 current customers — it
    # becomes useful the moment the extract carries it, and costs nothing while
    # it does not (the picker simply renders no options).
    plant_filter = (
        request.GET.get("plant", "").strip() or request.POST.get("plant", "").strip()
    )
    if plant_filter:
        rows = rows.filter(plant__iexact=plant_filter)
    # State is the slice that works on today's data: 100% populated across 41
    # distinct values, and couriers are chosen by region — "assign Blue Dart to
    # every customer in Gujarat" is the decision actually being made. City is
    # 99% populated too but runs to 1,470 distinct values, so it stays in the
    # search box rather than becoming a dropdown.
    state_filter = (
        request.GET.get("state", "").strip() or request.POST.get("state", "").strip()
    )
    if state_filter:
        rows = rows.filter(state__iexact=state_filter)
    entity_filter = (
        request.GET.get("entity", "").strip() or request.POST.get("entity", "").strip()
    )
    if entity_filter:
        rows = rows.filter(entity__code__iexact=entity_filter)
    return rows, {
        "q": query,
        "courier": courier_filter,
        "plant": plant_filter,
        "state": state_filter,
        "entity": entity_filter,
    }


@login_required
@admin_required
@require_POST
def customer_bulk_courier(request):
    """Assign (or clear) one courier across many customers at once.

    The pending half of #18. The import path has carried Courier Partner all
    along; what was missing was any way to fill it for an existing master, and
    7,480 customers through a per-row modal is not one.

    Two scopes, chosen by the ``scope`` field:

    ``selected``
        the ticked rows, by pk.
    ``filtered``
        everything matching the current search / courier / plant filter. The
        filter is **re-derived here** rather than trusting ids from the client:
        a client-supplied list is unbounded and unverifiable, and re-running the
        query means the count the button promised is the count that gets
        written.
    """
    action = request.POST.get("action", "assign")
    scope = request.POST.get("scope", "selected")

    if scope == "filtered":
        rows, filters = _filtered_customers(request)
        # Named in the confirmation message and in the activity log, so a bulk
        # write of thousands of rows records what it actually matched rather
        # than just how many.
        labels = {
            "q": "search", "courier": "courier", "plant": "plant",
            "state": "state", "entity": "entity",
        }
        described = ", ".join(
            f"{labels[key]} {value}" for key, value in filters.items() if value
        ) or "every customer"
    else:
        pks = [int_or_none(value) for value in request.POST.getlist("pks")]
        pks = [pk for pk in pks if pk is not None]
        if not pks:
            messages.error(request, "Select at least one customer first.")
            return redirect(_redirect_back(request, "merilv:masters_customers"))
        rows = CustomerMaster.objects.filter(pk__in=pks)
        described = f"{len(pks)} selected customer(s)"

    total = rows.count()
    if not total:
        messages.warning(request, "That selection matched no customers — nothing changed.")
        return redirect(_redirect_back(request, "merilv:masters_customers"))
    if total > BULK_COURIER_CAP:
        messages.error(
            request,
            f"That would change {total:,} customers, above the {BULK_COURIER_CAP:,} "
            "limit for one bulk assignment. Narrow the filter first.",
        )
        return redirect(_redirect_back(request, "merilv:masters_customers"))

    if action == "clear":
        # Its own named action, never the fallback for an empty picker.
        updated = rows.update(courier_partner="", courier_number="")
        _log(
            request, "customer_bulk_courier_clear", "CustomerMaster", "",
            count=updated, scope=scope, matched=described,
        )
        messages.success(request, f"Cleared the default courier on {updated:,} customer(s).")
        return redirect(_redirect_back(request, "merilv:masters_customers"))

    courier = canonical_courier_name(request.POST.get("courier_partner"))
    if not courier:
        # Not treated as "assign blank": wiping thousands of couriers by leaving
        # a dropdown alone is not a mistake anyone should be able to make.
        messages.error(
            request,
            "Choose a courier to assign, or use Clear courier if you meant to empty it.",
        )
        return redirect(_redirect_back(request, "merilv:masters_customers"))

    match = CourierMaster.objects.filter(courier_name__iexact=courier).first()
    if match is None:
        messages.error(
            request,
            f"“{courier}” is not in the Courier Master, so those customers would "
            "not appear against any courier. Add it there first.",
        )
        return redirect(_redirect_back(request, "merilv:masters_customers"))

    # The courier's own number travels with it: the outbound Finish Delivery
    # form reads both, and assigning a partner without its number leaves half
    # the link filled in.
    values = {"courier_partner": match.courier_name}
    if match.courier_number:
        values["courier_number"] = match.courier_number

    # One statement, not a save loop — nothing on CustomerMaster.save needs to
    # run for a courier change, and 7,480 round trips is not a bulk action.
    updated = rows.update(**values)
    _log(
        request, "customer_bulk_courier", "CustomerMaster", "",
        count=updated, courier=match.courier_name, scope=scope, matched=described,
    )
    messages.success(
        request,
        f"Assigned {match.courier_name} to {updated:,} customer(s) — {described}.",
    )
    return redirect(_redirect_back(request, "merilv:masters_customers"))


@login_required
@admin_required
def customer_create(request):
    """Add one ship-to/sold-to party by hand."""
    if request.method == "POST":
        code = (request.POST.get("customer_code") or "").strip().upper()
        errors = []
        if not code:
            errors.append("Customer code is required.")
        elif CustomerMaster.objects.filter(customer_code=code).exists():
            errors.append(f"Customer {code} already exists. Edit it from the list instead.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            entity = Entity.objects.filter(code=request.POST.get("entity")).first()
            customer = CustomerMaster.objects.create(
                customer_code=code,
                customer_name=(request.POST.get("customer_name") or "").strip(),
                entity=entity,
                plant=(request.POST.get("plant") or "").strip(),
                # Canonicalised for the same reason as in customer_save: the
                # link to a courier is by name, so a typo or a different casing
                # means the Courier module shows no customers against it (#18).
                courier_partner=canonical_courier_name(request.POST.get("courier_partner")),
                courier_number=(request.POST.get("courier_number") or "").strip(),
                city=(request.POST.get("city") or "").strip(),
                state=(request.POST.get("state") or "").strip(),
                country=(request.POST.get("country") or "").strip(),
                pincode=(request.POST.get("pincode") or "").strip(),
                gstin=(request.POST.get("gstin") or "").strip(),
                email=(request.POST.get("email") or "").strip(),
                mobile=(request.POST.get("mobile") or "").strip(),
                contact_person=(request.POST.get("contact_person") or "").strip(),
                address=(request.POST.get("address") or "").strip(),
            )
            _log(request, "customer_create", "CustomerMaster", customer.pk)
            messages.success(request, f"Customer {code} added.")
            return redirect("merilv:masters_customers")

    return render(
        request,
        "merilv/masters_customer_create.html",
        # The Default-courier datalist needs the names, or it renders empty and
        # the field silently degrades back to the free text that caused #18.
        _ctx(request, courier_options=_active_courier_names()),
    )


def _active_courier_names() -> list[dict]:
    """Active couriers for the Default-courier dropdown on both customer
    screens: ``[{"name", "number", "numbers"}]``, one entry per NAME.

    Inactive ones are omitted deliberately — suggesting a courier that has been
    retired is how a customer gets pointed at one.

    A courier name is not unique in Courier Master: the same carrier is held
    once per plant, with its own number each time. So the number a customer
    should get depends on the customer's plant, and both screens have that
    field on the form:

    * ``numbers`` maps plant -> number for every row held under this name. A
      form that knows its plant looks the number up here and gets the right
      account.
    * ``number`` is the fallback for a form with no plant filled in yet. It
      stays blank when the rows disagree, because "the first one we found" is
      how a customer quietly gets the wrong plant's account.

    The fallback used to be the only answer, which was safe but unhelpful: it
    went blank for exactly the carriers that have more than one plant, which
    is the case the plant map now answers properly.
    """
    by_name: dict[str, dict[str, str]] = {}
    for name, plant, number in (
        CourierMaster.objects.filter(is_active=True)
        .order_by("courier_name")
        .values_list("courier_name", "plant", "courier_number")
    ):
        by_name.setdefault(name, {})[(plant or "").strip()] = (number or "").strip()
    options = []
    for name, per_plant in sorted(by_name.items()):
        distinct = {number for number in per_plant.values() if number}
        options.append({
            "name": name,
            "number": next(iter(distinct)) if len(distinct) == 1 else "",
            # Rendered into a data-* attribute; Django escapes the quotes and
            # the browser hands them back intact through dataset.
            "numbers": json.dumps(per_plant, separators=(",", ":"), sort_keys=True),
        })
    return options


def canonical_courier_name(value: str) -> str:
    """Return ``value`` in the courier master's own spelling, if it is known.

    The customer -> courier link is by NAME, not by FK
    (``CustomerMaster.courier_partner`` and ``CustomerCourierMapping
    .courier_name`` are both char columns). That makes the link only as good as
    the spelling: "Blue dart", "BLUE DART" and "Blue Dart " are three different
    couriers as far as an exact match is concerned, and the Courier module then
    shows no customers against any of them.

    So a typed name that matches a courier case- and whitespace-insensitively is
    rewritten to the courier's own spelling. An unknown name is returned
    trimmed but otherwise untouched: imported customers legitimately reference
    couriers that are not in the master yet, and dropping that data would lose
    information the import was carrying.
    """
    name = (value or "").strip()
    if not name:
        return ""
    match = CourierMaster.objects.filter(courier_name__iexact=name).first()
    return match.courier_name if match else name


def courier_plants() -> dict[str, set[str]]:
    """Lower-cased courier name → the lower-cased plants it is held at.

    The master holds one carrier once per plant, so a name can stand for
    several rows with different partner codes. This says which.
    """
    from collections import defaultdict

    plants: dict[str, set[str]] = defaultdict(set)
    for name, plant in CourierMaster.objects.values_list("courier_name", "plant"):
        if name and name.strip():
            plants[name.strip().lower()].add((plant or "").strip().lower())
    return dict(plants)


def usage_key(name: str, plant: str, plants_by_name: dict[str, set[str]]):
    """Which bucket a customer — or a courier row — belongs in.

    ``(name, plant)`` when the master holds that name at more than one plant,
    and ``(name, None)`` when it holds it at one.

    The second case is not a shortcut, it is the point. Plants are separate
    businesses and a carrier serving two of them is two rows with two accounts,
    so their customers must not be pooled — Arts Express Solutions is 24ART at
    WGJ7 and 24AES at 3044, and the screen was showing all 920 customers
    against both because the bucket key was the name alone. But splitting a
    name that has only ONE row can only lose customers: any whose plant is
    spelled differently, or is blank, would match nothing and vanish from a
    screen where nothing was ever ambiguous. So the split happens exactly where
    there is something to split.

    A plant that matches no row of a multi-plant name falls back to a
    blank-plant row if the master has one — that row is the carrier "not tied
    to a plant" and is the natural catch-all. With no such row the customer is
    genuinely unplaceable, which :func:`courier_usage` reports rather than
    hides.
    """
    key = (name or "").strip().lower()
    at = (plant or "").strip().lower()
    known_plants = plants_by_name.get(key, set())
    if len(known_plants) <= 1:
        return (key, None)
    if at in known_plants:
        return (key, at)
    if "" in known_plants:
        return (key, "")
    return None


def courier_usage() -> tuple[dict[tuple, list[dict]], list[str], dict[str, list[dict]]]:
    """Which customers point at each courier, and which point at nothing.

    Returns ``(by_courier, unknown_names, unplaced)``.

    ``by_courier`` is keyed by :func:`usage_key` — the LOWER-CASED courier name
    plus, where the master holds that name at more than one plant, the plant.
    Lower-cased because the link is by name and the spellings in the wild do
    not agree, so a case-sensitive key would split one courier's customers
    across several buckets and show most couriers as unused. Plant-aware
    because a carrier at two plants is two rows with two accounts, and pooling
    their customers claimed every customer of the name for both.

    Both link sources are read: a customer's default courier
    (``CustomerMaster.courier_partner``) and any plant-specific override
    (``CustomerCourierMapping``). An override is labelled as such, because
    "this customer uses this courier out of this plant" is a different fact
    from "this is the customer's default".

    ``unmatched`` is keyed on the lower-cased name and holds customers whose
    courier IS in the master but at neither their plant nor a blank one. They
    are NOT dropped. The caller attaches them to every row of that name and
    labels them, because the alternative — a courier that plainly has hundreds
    of customers showing "none" — is a worse answer than the merge this split
    replaced. Not knowing which of a carrier's plants serves a customer is a
    fact about the data; hiding the customer is not a way of stating it.

    This is what happens when the two masters use different plant vocabularies
    — courier rows at MHPL/MLSIPL, customers at WGJ7/3044 — which is a real
    state of the dev data and a possible state of anyone's. Where the plants DO
    correspond, ``unmatched`` is empty and every customer lands on exactly one
    row.

    Three queries total, bucketed in Python — one query per courier would be a
    hundred queries on the real master.
    """
    from collections import defaultdict

    plants_by_name = courier_plants()
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    unknown: set[str] = set()
    unmatched: dict[str, list[dict]] = defaultdict(list)

    # courier_number → courier_name so we can resolve a customer whose
    # courier_partner holds a partner code instead of a name.
    number_to_name: dict[str, str] = {}
    for cname, cnum in CourierMaster.objects.exclude(courier_number="").values_list("courier_name", "courier_number"):
        if cname and cnum:
            number_to_name.setdefault(cnum.strip().lower(), cname.strip())

    def place(courier, entry):
        name = (courier or "").strip()
        if not name:
            return
        resolved = name
        if name.lower() not in plants_by_name:
            # Try treating it as a courier number.
            resolved = number_to_name.get(name.lower(), "")
            if not resolved or resolved.lower() not in plants_by_name:
                unknown.add(name)
                return

        # A customer's plant can be comma-separated after the multi-plant
        # import.  Try each plant in turn; the first match wins so the
        # customer is counted once, not once per plant.
        plant_raw = entry.get("plant") or ""
        parts = [p.strip() for p in plant_raw.split(",") if p.strip()]
        if not parts:
            parts = [""]
        placed = False
        for p in parts:
            key = usage_key(resolved, p, plants_by_name)
            if key is not None:
                buckets[key].append(entry)
                placed = True
                break
        if not placed:
            unmatched[resolved.lower()].append({
                **entry, "courier_name": resolved, "plant_unmatched": True,
            })

    # The overrides are read FIRST so the defaults can stand aside for them.
    #
    # A customer with a per-plant mapping would otherwise be counted twice for
    # that plant — once from CustomerMaster.courier_partner and again from the
    # mapping — which inflates every count on the screen. The mapping is
    # documented as taking precedence ("checked before the customer's default
    # courier", CustomerCourierMapping), so the default simply does not apply
    # where one exists.
    overrides = CustomerCourierMapping.objects.exclude(courier_name="").values_list(
        "customer_code", "courier_name", "plant",
    )
    overridden: set[tuple[str, str]] = set()
    names = dict(CustomerMaster.objects.values_list("customer_code", "customer_name"))
    for code, courier, plant in overrides:
        overridden.add((code, (plant or "").strip().lower()))
        place(courier, {
            "customer_code": code,
            # Named from the master, so an override reads like a customer
            # rather than a bare code on the screen it feeds.
            "customer_name": names.get(code, ""),
            "plant": plant,
            "is_override": True,
        })

    defaults = CustomerMaster.objects.exclude(courier_partner="").values_list(
        "customer_code", "customer_name", "courier_partner", "plant",
    )
    for code, customer_name, courier, plant in defaults:
        # A customer listed at several plants carries them comma-joined here
        # (see ingest._merge_customer_plant). Each is checked on its own, or a
        # row reading "3082,WGJ7" would dodge an override held for 3082.
        parts = [p.strip() for p in (plant or "").split(",") if p.strip()] or [""]
        remaining = [p for p in parts if (code, p.lower()) not in overridden]
        if not remaining:
            continue
        for part in remaining:
            place(courier, {
                "customer_code": code,
                "customer_name": customer_name,
                "plant": part,
                "is_override": False,
            })

    # One CUSTOMER counts once, however many ways it reaches this courier.
    #
    # A customer can hold a mapping per plant, and where the master has this
    # carrier at only one plant every one of those lands in the same bucket —
    # so a customer shipping from three plants was counted three times and the
    # screen read 3 customers for 1. The override is kept over the default
    # where both exist, matching which one actually decides at dispatch.
    for key, entries in list(buckets.items()):
        by_customer: dict[str, dict] = {}
        for entry in entries:
            kept = by_customer.get(entry["customer_code"])
            if kept is None or (entry["is_override"] and not kept["is_override"]):
                by_customer[entry["customer_code"]] = entry
        buckets[key] = list(by_customer.values())

    for entries in buckets.values():
        entries.sort(key=lambda entry: (entry["is_override"], entry["customer_code"]))
    for entries in unmatched.values():
        entries.sort(key=lambda entry: (entry["is_override"], entry["customer_code"]))
    return dict(buckets), sorted(unknown), dict(unmatched)


#: Fields the inline customer editor may write, applied only when present in the
#: POST — the modal shows the dispatch-relevant subset, and omitting a field must
#: leave it alone rather than blank it.
CUSTOMER_EDIT_FIELDS = (
    "customer_name", "plant", "courier_partner", "courier_number", "city", "state",
    "country", "pincode", "gstin", "email", "mobile", "contact_person", "address",
)


@login_required
@admin_required
@require_POST
def customer_save(request):
    """Update one customer from the list's inline editor.

    ``customer_code`` is the unique natural key and is deliberately NOT editable
    here: it is what every delivery, scan and courier mapping refers to by value
    (``CustomerCourierMapping.customer_code`` is a plain char column, not an FK),
    so renaming it in place would orphan those rows silently.
    """
    customer = CustomerMaster.objects.filter(pk=int_or_none(request.POST.get("pk"))).first()
    if customer is None:
        messages.error(request, "That customer no longer exists.")
        return redirect("merilv:masters_customers")

    before = activitylog.snapshot(customer)
    for field in CUSTOMER_EDIT_FIELDS:
        if field in request.POST:
            setattr(customer, field, (request.POST.get(field) or "").strip())
    # The link to a courier is by name, so the name has to be the courier's own
    # spelling or the Courier module shows no customers against it (#18).
    if "courier_partner" in request.POST:
        customer.courier_partner = canonical_courier_name(customer.courier_partner)
    if "entity" in request.POST:
        customer.entity = Entity.objects.filter(code=request.POST.get("entity")).first()
    customer.is_blocked = request.POST.get("is_blocked") == "1"
    customer.save()

    _log(
        request, "customer_update", "CustomerMaster", customer.pk,
        changes=activitylog.diff_snapshots(before, activitylog.snapshot(customer)),
    )
    messages.success(request, f"Customer {customer.customer_code} updated.")
    return redirect(_redirect_back(request, "merilv:masters_customers"))


# ─── Couriers ────────────────────────────────────────────────────────────────
@login_required
@admin_required
def couriers(request):
    rows = CourierMaster.objects.all()
    query = request.GET.get("q", "").strip()
    if query:
        rows = rows.filter(
            Q(courier_name__icontains=query)
            | Q(courier_number__icontains=query)
            | Q(vehicle_number__icontains=query)
            | Q(vehicle_type__icontains=query)
            | Q(gstin__icontains=query)
        )
    # #18 — which customers point at each courier. Read once for the whole
    # page and attached to each row, rather than a query per courier.
    usage, unlinked, unmatched = courier_usage()
    # The same key both sides, so a row and its customers cannot disagree about
    # whether the name is split by plant.
    plants_by_name = courier_plants()
    rows = list(rows)
    for courier in rows:
        key = usage_key(courier.courier_name, courier.plant, plants_by_name)
        name_key = (courier.courier_name or "").strip().lower()
        matched = usage.get(key, []) if key else []
        # Customers of this carrier whose own plant matches none of its rows.
        # Shown against every row of the name rather than against none: which
        # plant serves them is unknown, and answering "none" would report a
        # courier with hundreds of customers as unused. Labelled in the list so
        # the count is not read as a per-plant figure it is not.
        spillover = unmatched.get(name_key, [])
        # Attributes rather than a parallel dict, so the template reads
        # `c.customers` next to `c.courier_name` instead of doing a lookup.
        courier.customers = matched + spillover
        courier.customer_count = len(courier.customers)
        courier.matched_count = len(matched)
        courier.unmatched_count = len(spillover)
    return render(
        request,
        "merilv/masters_couriers.html",
        _ctx(
            request,
            active_tab="couriers",
            rows=rows,
            result_count=len(rows),
            q=query,
            unlinked_couriers=unlinked,
            # Flattened for the diagnostic banner. Counted once each, not once
            # per row they are shown against.
            unplaced_customers=[e for entries in unmatched.values() for e in entries],
            linked_customer_total=(
                sum(len(v) for v in usage.values())
                + sum(len(v) for v in unmatched.values())
            ),
        ),
    )


@login_required
@admin_required
@require_POST
def courier_save(request):
    """Create, update or deactivate one courier row."""
    pk = request.POST.get("pk")
    name = (request.POST.get("courier_name") or "").strip()
    if request.POST.get("action") == "delete" and pk:
        CourierMaster.objects.filter(pk=int_or_none(pk)).update(is_active=False)
        _log(request, "courier_deactivate", "CourierMaster", pk)
        messages.success(request, "Courier deactivated.")
        return redirect("merilv:masters_couriers")

    if not name:
        messages.error(request, "Courier name is required.")
        return redirect("merilv:masters_couriers")

    values = {
        "courier_name": name,
        "plant": (request.POST.get("plant") or "").strip(),
        "courier_number": (request.POST.get("courier_number") or "").strip(),
        "vehicle_number": (request.POST.get("vehicle_number") or "").strip(),
        "vehicle_type": (request.POST.get("vehicle_type") or "").strip(),
        # What the outbound Finish Delivery form pre-selects for this carrier.
        "mode_of_transit": (request.POST.get("mode_of_transit") or "").strip(),
        "gstin": (request.POST.get("gstin") or "").strip().upper(),
        "is_active": request.POST.get("is_active") == "1",
    }
    if pk:
        existing = CourierMaster.objects.filter(pk=pk).first()
        before = activitylog.snapshot(existing)
        CourierMaster.objects.filter(pk=pk).update(**values)
        _log(
            request, "courier_update", "CourierMaster", pk,
            changes=activitylog.diff_snapshots(
                before, activitylog.snapshot(CourierMaster.objects.filter(pk=pk).first())
            ),
        )
        messages.success(request, "Courier updated.")
    else:
        courier = CourierMaster.objects.create(**values)
        _log(request, "courier_create", "CourierMaster", courier.pk)
        messages.success(request, "Courier added.")
    return redirect("merilv:masters_couriers")


# ─── Cartons ─────────────────────────────────────────────────────────────────
@login_required
@admin_required
def boxes(request):
    rows = BoxProfile.objects.all()
    return render(
        request,
        "merilv/masters_boxes.html",
        _ctx(request, active_tab="boxes", rows=rows, result_count=rows.count()),
    )


#: BoxProfile.length_cm/width_cm/height_cm are DecimalField(max_digits=7,
#: decimal_places=2), so 99999.99 is the largest value the column can hold.
#: Anything larger used to reach Django's number formatting and raise
#: decimal.InvalidOperation as an uncaught 500.
MAX_BOX_DIMENSION_CM = 99999.99
MAX_BOX_DIVISOR = 9_999_999


@login_required
@admin_required
@require_POST
def box_save(request):
    pk = request.POST.get("pk")
    code = (request.POST.get("code") or "").strip()
    if request.POST.get("action") == "delete" and pk:
        BoxProfile.objects.filter(pk=int_or_none(pk)).update(is_active=False)
        _log(request, "box_deactivate", "BoxProfile", pk)
        messages.success(request, "Carton deactivated.")
        return redirect("merilv:masters_boxes")

    if not code:
        messages.error(request, "Carton code is required.")
        return redirect("merilv:masters_boxes")

    def _number(field, default=0):
        try:
            value = float(request.POST.get(field) or default)
        except (TypeError, ValueError, OverflowError):
            return default
        # inf/nan survive float() and then blow up inside Decimal formatting on
        # save, or persist as garbage. Treated as "not a number" here instead.
        if value != value or value in (float("inf"), float("-inf")):
            return default
        return value

    def _dimension(field):
        """A cm dimension, clamped to what the column can actually hold.

        ``length_cm`` and friends are ``DecimalField(max_digits=7,
        decimal_places=2)``, so anything from 100000 up raised
        ``decimal.InvalidOperation`` deep inside Django's number formatting —
        an uncaught 500 from a plain form post.
        """
        value = _number(field)
        if value < 0:
            value = 0
        return min(value, MAX_BOX_DIMENSION_CM)

    # PositiveIntegerField has no CHECK constraint on PostgreSQL, so a negative
    # max_qty used to persist happily and silently disable capacity enforcement.
    max_qty = int(_number("max_qty"))
    values = {
        "sku_name": (request.POST.get("sku_name") or "").strip(),
        "length_cm": _dimension("length_cm"),
        "width_cm": _dimension("width_cm"),
        "height_cm": _dimension("height_cm"),
        "divisor": min(int(_number("divisor", 5000)) or 5000, MAX_BOX_DIVISOR),
        "max_qty": max(max_qty, 0),
        "is_active": request.POST.get("is_active") == "1",
    }
    if pk:
        before = activitylog.snapshot(BoxProfile.objects.filter(pk=pk).first())
        BoxProfile.objects.filter(pk=pk).update(code=code, **values)
        _log(
            request, "box_update", "BoxProfile", pk,
            changes=activitylog.diff_snapshots(
                before, activitylog.snapshot(BoxProfile.objects.filter(pk=pk).first())
            ),
        )
        messages.success(request, "Carton updated.")
    else:
        box = BoxProfile.objects.create(code=code, **values)
        _log(request, "box_create", "BoxProfile", box.pk)
        messages.success(request, "Carton added.")
    return redirect("merilv:masters_boxes")


# ─── Entity rules ────────────────────────────────────────────────────────────
#: Fields the console exposes, grouped as they appear on the page.
ENTITY_BOOLEAN_FIELDS = (
    "strip_whitespace", "supports_serial", "ai21_is_material", "sanitise_batch",
    "allow_envelope", "positional_walker", "compact_gs1", "positional_rules", "trauma_legacy",
    "match_concat", "match_batch", "match_material", "triple_sort", "multi_row_consume",
    "smart_zero", "use_scan_qty", "strict_batch",
    "allow_customer_focus", "aggregate_delivery_lines", "enforce_box_capacity",
    "allow_partial_export", "is_active",
)
ENTITY_TEXT_FIELDS = (
    "name", "company_name", "concat_delimiter", "plant_priority",
    "good_storage_location", "damage_storage_location", "parse_rules_csv",
    "bt_location_priority",
)
ENTITY_NUMBER_FIELDS = (
    "pseudo_serial_digits", "near_expiry_months", "short_expiry_months", "month_divisor", "sort_order",
)


@login_required
@admin_required
def entities(request):
    rows = Entity.objects.all()
    return render(
        request,
        "merilv/masters_entities.html",
        _ctx(request, active_tab="entities", rows=rows, result_count=rows.count()),
    )


@login_required
@admin_required
def entity_create(request):
    """Add a new business unit.

    Only the identity is captured here — everything else (barcode rules,
    expiry thresholds, matching behaviour) defaults to the model's plain GS1
    settings and is tuned afterward on the full edit page, since a brand-new
    unit rarely needs anything unusual on day one.
    """
    if request.method == "POST":
        code = (request.POST.get("code") or "").strip().upper()
        if not code:
            messages.error(request, "Entity code is required.")
        elif Entity.objects.filter(code=code).exists():
            messages.error(request, f"Entity {code} already exists.")
        else:
            entity = Entity.objects.create(
                code=code,
                name=(request.POST.get("name") or "").strip() or code,
                company_name=(request.POST.get("company_name") or "").strip(),
            )
            _log(request, "entity_create", "Entity", entity.code)
            messages.success(request, f"{code} created — configure its parsing rules below.")
            return redirect("merilv:masters_entity_edit", pk=entity.pk)

    return render(request, "merilv/masters_entity_create.html", _ctx(request))


@login_required
@admin_required
def entity_edit(request, pk):
    entity = get_object_or_404(Entity, pk=pk)

    if request.method == "POST":
        for field in ENTITY_TEXT_FIELDS:
            if field in request.POST:
                setattr(entity, field, (request.POST.get(field) or "").strip())
        for field in ENTITY_BOOLEAN_FIELDS:
            setattr(entity, field, request.POST.get(field) == "1")
        for field in ENTITY_NUMBER_FIELDS:
            raw = request.POST.get(field)
            if raw not in (None, ""):
                try:
                    # OverflowError, not just ValueError: int(float("inf"))
                    # raises the former, and a bare float("inf") reached
                    # entity.save() as Decimal("Infinity").
                    number = float(raw)
                    if number != number or number in (float("inf"), float("-inf")):
                        raise ValueError(field)
                    setattr(entity, field, number if field == "month_divisor" else int(number))
                except (TypeError, ValueError, OverflowError):
                    messages.warning(request, f"{field.replace('_', ' ').title()} must be a number — left unchanged.")
        entity.save()
        _log(request, "entity_update", "Entity", entity.code)
        messages.success(request, f"{entity.code} updated.")
        return redirect("merilv:masters_entities")

    return render(request, "merilv/masters_entity_edit.html", _ctx(request, entity=entity))


#: Ceiling on an activity download. The log is append-only and grows for the
#: life of the install, so an unbounded export would build every row of it in
#: memory. Truncation is reported in the sheet rather than left silent — a
#: short spreadsheet that looks complete is the failure worth avoiding.
ACTIVITY_EXPORT_CAP = 20_000


def _filtered_activity(request):
    """One shared queryset builder for the log table and its download.

    Kept in one place so the two can never disagree about what "the current
    filtered list" is — the same lesson Delivery History and Scan History were
    built on.
    """
    logs = ActivityLog.objects.select_related("actor")
    action = request.GET.get("action", "").strip()
    if action:
        logs = logs.filter(action=action)
    return logs, action


@login_required
@access_required
def activity(request):
    logs, action = _filtered_activity(request)
    known_actions = ActivityLog.objects.values_list("action", flat=True).distinct().order_by("action")
    return render(
        request,
        "merilv/masters_activity.html",
        _ctx(request, page_obj=paginate(request, logs), action=action, known_actions=list(known_actions)),
    )


@login_required
@access_required
def activity_export(request):
    """Download the activity log as it is currently filtered.

    Columns mirror the screen, with two additions a spreadsheet can carry and a
    table cannot: the raw ``action`` slug beside its readable label (so the file
    is still machine-filterable) and the full metadata as JSON beside the
    summarised Details text.
    """
    logs, action = _filtered_activity(request)
    headers = [
        "When", "Who", "Action", "Action Code", "Target Type", "Target",
        "Details", "Raw Detail (JSON)",
    ]
    rows = []
    truncated = False
    for index, log in enumerate(logs[: ACTIVITY_EXPORT_CAP + 1]):
        if index >= ACTIVITY_EXPORT_CAP:
            truncated = True
            break
        actor = log.actor
        rows.append([
            # Naive local time: openpyxl cannot write a tz-aware datetime, and
            # the log is read in one timezone.
            timezone.localtime(log.created_at).replace(tzinfo=None),
            (actor.get_full_name() or actor.username) if actor else "System",
            activitylog.humanise_action(log.action),
            log.action,
            log.target_type,
            log.target_id,
            activitylog.detail_text(log.metadata),
            json.dumps(log.metadata, sort_keys=True, default=str) if log.metadata else "",
        ])

    if truncated:
        rows.append([
            f"Truncated at {ACTIVITY_EXPORT_CAP:,} rows — filter by action to export the rest."
        ])

    suffix = f"_{action}" if action else ""
    buffer = export.export_generic("Activity Log", headers, rows)
    return _xlsx_response(buffer, f"merilv_activity_log{suffix}.xlsx")


# ─── Organisation structure ──────────────────────────────────────────────────
#: The six structural masters, each editable through one shared form handler.
#: They share a shape — a code, a label, and at most one parent — so a single
#: page keeps the hierarchy visible instead of scattering it over six screens.
#: ``headers``/``columns`` additionally drive the per-table template download,
#: export and import — see :data:`MASTER_SHEETS` — so a column added here shows
#: up in all three at once and can never drift between them.
ORG_TABLES = {
    "company": {
        "model": CompanyMaster,
        "label": "Company",
        "title": "Company Master",
        "icon": "building",
        "pk_field": "company_code",
        "fields": ("company_code", "company_name"),
        "parent": None,
        "search": ("company_code__icontains", "company_name__icontains"),
        "headers": ("Company Code", "Company Name"),
        "columns": {
            "code": ("Company Code", "Company", "Code"),
            "name": ("Company Name", "Name", "Description"),
        },
    },
    "organization": {
        "model": OrganizationMaster,
        "label": "Sales organisation",
        "title": "Organization Master",
        "icon": "layers",
        "pk_field": "org_code",
        "fields": ("org_code", "org_name"),
        "parent": ("company", CompanyMaster, "company_code"),
        "search": ("org_code__icontains", "org_name__icontains"),
        "headers": ("Sales Org Code", "Sales Org Name", "Company Code"),
        "columns": {
            "code": ("Sales Org Code", "Org Code", "Organization Code", "Sales Org", "Code"),
            "name": ("Sales Org Name", "Org Name", "Organization Name", "Name"),
            "parent": ("Company Code", "Company"),
        },
    },
    "plant": {
        "model": PlantMaster,
        "label": "Plant",
        "title": "Plant Master",
        "icon": "building",
        "pk_field": "plant_code",
        "fields": ("plant_code", "plant_name"),
        "parent": ("organization", OrganizationMaster, "org_code"),
        "search": ("plant_code__icontains", "plant_name__icontains"),
        "headers": ("Plant Code", "Plant Name", "Sales Org Code"),
        "columns": {
            "code": ("Plant Code", "Plant", "Code"),
            "name": ("Plant Name", "Name"),
            "parent": ("Sales Org Code", "Org Code", "Organization", "Sales Org"),
        },
    },
    "storage": {
        "model": StorageMaster,
        "label": "Storage location",
        "title": "Storage Location",
        "icon": "box",
        "pk_field": "id",
        "fields": ("storage_code", "storage_name"),
        "parent": ("plant", PlantMaster, "plant_code"),
        "search": ("storage_code__icontains", "storage_name__icontains", "plant__plant_code__icontains"),
        "headers": ("Plant Code", "Storage Code", "Storage Name"),
        "columns": {
            "code": ("Storage Code", "Storage Loc", "Storage Location", "Code"),
            "name": ("Storage Name", "Description", "Name"),
            "parent": ("Plant Code", "Plant"),
        },
        # The only master whose identity is not its code alone: every plant has its
        # own DMG1/RT01, so deduplicating on the code would keep one row per code
        # across the whole file and drop the rest.
        "dedupe": ("code", "parent"),
    },
    "billing_type": {
        "model": BillingTypeMaster,
        "label": "Billing type",
        "title": "Billing Type Master",
        "icon": "file",
        "pk_field": "billing_type",
        "fields": ("billing_type", "billing_description"),
        "parent": None,
        "search": ("billing_type__icontains", "billing_description__icontains"),
        "headers": ("Billing Type", "Description"),
        "columns": {
            "code": ("Billing Type", "Billing Type Code", "Code"),
            "name": ("Description", "Billing Description", "Name"),
        },
    },
    "sales_type": {
        "model": SalesTypeMaster,
        "label": "Sales type",
        "title": "Sales Type Master",
        "icon": "file",
        "pk_field": "sales_type",
        "fields": ("sales_type", "sales_description"),
        "parent": None,
        "search": ("sales_type__icontains", "sales_description__icontains"),
        "headers": ("Sales Type", "Description"),
        "columns": {
            "code": ("Sales Type", "Sales Type Code", "Code"),
            "name": ("Description", "Sales Description", "Name"),
        },
    },
}

#: Order the org tabs appear in, and the default when ``?table=`` is absent.
ORG_TABLE_ORDER = ("company", "organization", "plant", "storage", "billing_type", "sales_type")


def _org_url(table: str) -> str:
    """The console URL for one structural master's tab."""
    return f"{reverse('merilv:masters_org')}?table={table}"


def _parent_lookup(table: str) -> str:
    """The ORM lookup from a row of ``table`` to its parent's code.

    Read off ORG_TABLES' own ``parent`` triple rather than retyped, so it
    cannot drift from the spec the rest of the module already agrees on.
    """
    spec = ORG_TABLES[table]
    if not spec["parent"]:
        return ""
    field_name, _parent_model, parent_pk = spec["parent"]
    return f"{field_name}__{parent_pk}"


#: The only structural masters that nest. The two document-type tables are
#: leaves - no parent, no children - so they are deliberately absent.
ORG_CHAIN = ("company", "organization", "plant", "storage")


def _child_table(table: str) -> str:
    """The structural master one level *below* ``table``, if any."""
    if table not in ORG_CHAIN:
        return ""
    index = ORG_CHAIN.index(table)
    return ORG_CHAIN[index + 1] if index + 1 < len(ORG_CHAIN) else ""


def _resolve_parent(table: str, code: str):
    """The parent row ``code`` names, or None.

    ``code`` is the parent's *code*, not its pk, because that is what a tree
    node and a child row both already carry - linking on pk would mean the
    template doing a second lookup for every node it draws.
    """
    spec = ORG_TABLES[table]
    if not spec["parent"] or not code:
        return None
    _field_name, parent_model, parent_pk = spec["parent"]
    return parent_model.objects.filter(**{parent_pk: code}).first()


def _org_ancestry(table: str, parent_row) -> list[dict]:
    """The chain from the top down to ``parent_row``, as breadcrumb entries.

    Walked upward from the parent and reversed, so each level links into *its*
    own table scoped to *its* parent. That scoping is the point: clicking
    "Sales Org" in a plant's breadcrumb should land on the sales orgs of that
    company, not on all of them.
    """
    chain = []
    child = table
    row = parent_row
    while row is not None:
        parent_table = ORG_TABLES[child]["parent"][0]
        parent_spec = ORG_TABLES[parent_table]
        code = getattr(row, parent_spec["pk_field"], "")
        grandparent = None
        if parent_spec["parent"]:
            grandparent = getattr(row, parent_spec["parent"][0], None)
        href = _org_url(parent_table)
        if grandparent is not None:
            grandparent_code = getattr(
                grandparent, ORG_TABLES[parent_spec["parent"][0]]["pk_field"], ""
            )
            if grandparent_code:
                href = f"{href}&parent={quote(str(grandparent_code))}"
        chain.append({
            "table": parent_table,
            "label": parent_spec["label"],
            "code": code,
            "name": getattr(row, parent_spec["fields"][1], "") or "",
            "href": href,
        })
        child = parent_table
        row = grandparent
    chain.reverse()
    return chain


def _org_rows(table: str):
    """The display queryset for one structural master, parents pre-joined."""
    if table == "organization":
        return OrganizationMaster.objects.select_related("company")
    if table == "plant":
        return PlantMaster.objects.select_related("organization")
    if table == "storage":
        return StorageMaster.objects.select_related("plant")
    return ORG_TABLES[table]["model"].objects.all()


@login_required
@admin_required
def org(request):
    """One structural master at a time, chosen by ``?table=``.

    These six used to share a single six-card page. They are now tabs on the
    console strip, which is what makes room for each to carry its own search,
    pagination and import/export controls — storage alone runs to four figures,
    and a sixth of a screen was never enough for it.

    The parent picker on the add form stays a full, unpaginated queryset: it is
    a dropdown of the *parent* table, so limiting it to page 1 would make every
    parent past the first thirty unreachable.
    """
    table = request.GET.get("table", "").strip() or ORG_TABLE_ORDER[0]
    spec = ORG_TABLES.get(table)
    if spec is None:
        messages.error(request, "Unknown master table.")
        return redirect("merilv:masters_org")

    rows = _org_rows(table)

    # -- Parent scope (?parent=<parent code>) -------------------------------
    # What turns five flat tables into one tree: a plant list can be "the
    # plants of THIS sales org" rather than all of them. An unresolvable code
    # falls back to the unscoped list with a message rather than rendering an
    # empty table, which would read as missing data instead of a bad link.
    parent_code = request.GET.get("parent", "").strip()
    parent_row = _resolve_parent(table, parent_code)
    if parent_code and parent_row is None:
        if spec["parent"]:
            parent_label = ORG_TABLES[spec["parent"][0]]["label"].lower()
            messages.warning(
                request,
                f"No {parent_label} with code \u201c{parent_code}\u201d - "
                f"showing every {spec['label'].lower()}.",
            )
        else:
            messages.warning(
                request,
                f"{spec['label']} sits at the top of the hierarchy - it has no parent to filter by.",
            )
        parent_code = ""
    if parent_row is not None:
        rows = rows.filter(**{_parent_lookup(table): parent_code})

    query = request.GET.get("q", "").strip()
    if query:
        matches = Q()
        for lookup in spec["search"]:
            matches |= Q(**{lookup: query})
        rows = rows.filter(matches)

    # The parent picker on the add form stays a full, unpaginated queryset: it
    # is a dropdown of the *parent* table, so limiting it to page 1 would make
    # every parent past the first thirty unreachable.
    parents = []
    if spec["parent"]:
        _, parent_model, _ = spec["parent"]
        parents = list(parent_model.objects.all())

    page_obj = paginate(request, rows)
    child_table = _child_table(table)
    return render(
        request,
        "merilv/masters_org.html",
        _ctx(
            request,
            active_tab=table,
            table=table,
            spec=spec,
            page_obj=page_obj,
            display_rows=[_org_display_row(row, spec) for row in page_obj],
            result_count=rows.count(),
            q=query,
            parents=parents,
            parent_field=spec["parent"][0] if spec["parent"] else "",
            headers=list(spec["headers"]),
            # Tree navigation (C6). `ancestry` is the breadcrumb above this
            # table, `parent_row`/`parent_code` scope it, and `child_table` is
            # what one of its rows drills down into.
            ancestry=_org_ancestry(table, parent_row),
            parent_row=parent_row,
            parent_code=parent_code,
            parent_pk=parent_row.pk if parent_row is not None else "",
            child_table=child_table,
            child_label=ORG_TABLES[child_table]["label"] if child_table else "",
            # Only the four tables that actually nest get the level strip.
            # Billing Type and Sales Type have no parent and no children, so
            # drawing them into a Company > Sales Org > Plant > Storage chain
            # would imply a billing type sits somewhere in the org hierarchy.
            org_chain=[
                {
                    "table": name,
                    "label": ORG_TABLES[name]["label"],
                    "href": _org_url(name),
                    "is_active": name == table,
                }
                for name in ORG_CHAIN
            ] if table in ORG_CHAIN else [],
        ),
    )


def _org_display_row(row, spec) -> dict:
    """Flatten one structural-master row to the code/name/parent the table shows.

    Done here rather than in the template so the six tables can share one markup
    block without it having to know each model's field names.
    """
    code_field, name_field = spec["fields"]
    parent_code = ""
    if spec["parent"]:
        field_name, _, parent_pk = spec["parent"]
        parent = getattr(row, field_name, None)
        parent_code = getattr(parent, parent_pk, "") if parent else ""
    return {
        "pk": row.pk,
        "code": getattr(row, code_field, ""),
        "name": getattr(row, name_field, ""),
        "parent": parent_code,
    }


@login_required
@admin_required
@require_POST
def org_save(request, table):
    """Create, update or delete one structural master row."""
    spec = ORG_TABLES.get(table)
    if spec is None:
        messages.error(request, "Unknown master table.")
        return redirect("merilv:masters_org")

    model = spec["model"]
    pk_field = spec["pk_field"]
    action = request.POST.get("action")
    pk = (request.POST.get("pk") or "").strip()
    landing = _org_url(table)

    if action == "delete":
        if not pk:
            messages.error(request, "Nothing selected to delete.")
            return redirect(landing)
        try:
            # Filtered on ``pk``, not on the spec's ``pk_field``: storage
            # locations are keyed by a surrogate id while every other table is
            # keyed by its code, and ``pk`` is the one spelling that is correct
            # for both.
            #
            # The value is passed through as-is. It used to go through
            # int_or_none(), which is right for storage and wrong for the other
            # five — their primary key *is* the code, so "WGJ7" became None,
            # filter(pk=None) matched nothing, and the screen still reported
            # "Plant deleted." Delete was silently a no-op on five of the six
            # tables. Django coerces a numeric string for the surrogate key on
            # its own, so nothing is lost by not helping it.
            deleted, _details = model.objects.filter(pk=pk).delete()
        except ProtectedError:
            # Structural masters are referenced by transactional data; removing
            # one silently would orphan deliveries and scans.
            messages.error(request, f"That {spec['label'].lower()} is still in use and cannot be deleted.")
            return redirect(landing)
        except (ValueError, TypeError):
            # A non-numeric pk aimed at the one table with an integer key.
            messages.error(request, f"That {spec['label'].lower()} could not be found.")
            return redirect(landing)
        if not deleted:
            # Never claim a deletion that did not happen — that is precisely how
            # the bug above stayed invisible.
            messages.error(request, f"That {spec['label'].lower()} no longer exists.")
            return redirect(landing)
        _log(request, f"{table}_delete", spec["model"].__name__, pk)
        messages.success(request, f"{spec['label']} deleted.")
        return redirect(landing)

    code_field, name_field = spec["fields"]
    code = (request.POST.get(code_field) or "").strip().upper()
    name = (request.POST.get(name_field) or "").strip()
    if not code:
        messages.error(request, f"{spec['label']} code is required.")
        return redirect(landing)

    values = {name_field: name}
    if spec["parent"]:
        field_name, parent_model, parent_pk = spec["parent"]
        parent_value = (request.POST.get(field_name) or "").strip()
        parent = parent_model.objects.filter(**{parent_pk: parent_value}).first() if parent_value else None
        if parent is None and table in ("storage",):
            messages.error(request, f"A {spec['label'].lower()} must belong to a plant.")
            return redirect(landing)
        values[field_name] = parent

    if table == "storage":
        # Storage locations use a surrogate key but are unique per plant+code.
        existing = StorageMaster.objects.filter(
            storage_code=code, plant=values.get("plant"),
        ).first()
        before = activitylog.snapshot(existing)
        row, _created = StorageMaster.objects.update_or_create(
            storage_code=code, plant=values.get("plant"), defaults={"storage_name": name}
        )
    else:
        before = activitylog.snapshot(model.objects.filter(**{pk_field: code}).first())
        row, _created = model.objects.update_or_create(**{pk_field: code}, defaults=values)

    _log(
        request, f"{table}_save", model.__name__, code,
        changes=activitylog.diff_snapshots(before, activitylog.snapshot(row)),
    )
    messages.success(request, f"{spec['label']} saved.")
    return redirect(landing)


# ─── Plant ↔ Sales Org mapping ───────────────────────────────────────────────
def _sync_primary_org(plant: PlantMaster) -> None:
    """Keep ``PlantMaster.organization`` pointing at the primary mapping.

    The FK stays as the primary denormalisation because a lot of code resolves a
    plant's org through it. Letting the two drift would mean the tree and the
    plant list disagreed about the same plant.
    """
    primary = plant.org_mappings.filter(is_primary=True).select_related("organization").first()
    target = primary.organization if primary else None
    if plant.organization_id != (target.pk if target else None):
        plant.organization = target
        plant.save(update_fields=["organization"])


@login_required
@admin_required
@require_POST
def plant_org_map(request, plant_code):
    """Link, unlink or re-designate the primary sales org for one plant.

    All three actions are one endpoint because they are one user intent — "fix
    this plant's org mapping" — and each has to touch two rows atomically:
    promoting a primary demotes the incumbent, and the plant's own FK is kept in
    step. The legacy tool did these as separate non-transactional requests,
    which could leave a plant with two primaries or none.
    """
    plant = get_object_or_404(PlantMaster, pk=plant_code)
    action = (request.POST.get("action") or "").strip()
    # Back to the hierarchy, which is where the mapping control lives and where
    # the result of the change is immediately visible.
    landing = reverse("merilv:masters_org_tree")

    if action == "link":
        org_code = (request.POST.get("organization") or "").strip()
        organization = OrganizationMaster.objects.filter(pk=org_code).first()
        if organization is None:
            messages.error(request, "Choose a sales organisation to map.")
            return redirect(landing)
        make_primary = request.POST.get("is_primary") == "1"
        with transaction.atomic():
            if make_primary:
                plant.org_mappings.filter(is_primary=True).update(is_primary=False)
            mapping, created = PlantOrgMapping.objects.get_or_create(
                plant=plant, organization=organization, defaults={"is_primary": make_primary},
            )
            if not created and make_primary and not mapping.is_primary:
                mapping.is_primary = True
                mapping.save(update_fields=["is_primary"])
            # A plant's first mapping is its primary whether or not the box was
            # ticked — a mapping set with no primary has no meaning.
            if not plant.org_mappings.filter(is_primary=True).exists():
                PlantOrgMapping.objects.filter(pk=mapping.pk).update(is_primary=True)
            _sync_primary_org(plant)
        _log(request, "plant_org_link", "PlantOrgMapping", f"{plant.pk}->{organization.pk}")
        messages.success(
            request,
            f"{organization.pk} mapped to {plant.pk}." if created
            else f"{organization.pk} was already mapped to {plant.pk}.",
        )
        return redirect(landing)

    if action == "unlink":
        # Keyed by org code, not the mapping's surrogate id: the page has no
        # reason to carry a primary key around, and the pair is already unique.
        mapping = plant.org_mappings.filter(
            organization_id=(request.POST.get("organization") or "").strip()
        ).first()
        if mapping is None:
            messages.warning(request, "That mapping no longer exists.")
            return redirect(landing)
        org_code = mapping.organization_id
        was_primary = mapping.is_primary
        with transaction.atomic():
            mapping.delete()
            # Removing the primary promotes the next remaining org rather than
            # leaving the plant primary-less.
            if was_primary:
                successor = plant.org_mappings.order_by("organization_id").first()
                if successor is not None:
                    successor.is_primary = True
                    successor.save(update_fields=["is_primary"])
            _sync_primary_org(plant)
        _log(request, "plant_org_unlink", "PlantOrgMapping", f"{plant.pk}->{org_code}")
        messages.success(request, f"{org_code} unmapped from {plant.pk}.")
        return redirect(landing)

    if action == "primary":
        mapping = plant.org_mappings.filter(
            organization_id=(request.POST.get("organization") or "").strip()
        ).first()
        if mapping is None:
            messages.warning(request, "That mapping no longer exists.")
            return redirect(landing)
        with transaction.atomic():
            plant.org_mappings.exclude(pk=mapping.pk).update(is_primary=False)
            PlantOrgMapping.objects.filter(pk=mapping.pk).update(is_primary=True)
            _sync_primary_org(plant)
        _log(request, "plant_org_primary", "PlantOrgMapping", f"{plant.pk}->{mapping.organization_id}")
        messages.success(request, f"{mapping.organization_id} is now primary for {plant.pk}.")
        return redirect(landing)

    messages.error(request, "Unknown mapping action.")
    return redirect(landing)


@login_required
@admin_required
def org_tree(request):
    """Company → Sales Org → Plant → Storage, as one nested view.

    The six structural masters are each a flat table on their own tab, which is
    why the client could not see the hierarchy: nothing showed how a row related
    to its parents. This assembles the whole chain in five queries.

    Plants are drawn under every org they map to, so a plant serving two orgs
    appears under both — that is the point of the mapping table, and a storage
    location under such a plant legitimately appears twice.
    """
    companies = list(CompanyMaster.objects.order_by("company_code"))
    orgs = list(OrganizationMaster.objects.select_related("company").order_by("org_code"))
    plants = {plant.pk: plant for plant in PlantMaster.objects.order_by("plant_code")}
    mappings = list(
        PlantOrgMapping.objects.select_related("plant", "organization").order_by(
            "-is_primary", "plant_id"
        )
    )

    storage_by_plant: dict[str, list] = defaultdict(list)
    for location in StorageMaster.objects.select_related("plant").order_by("plant_id", "storage_code"):
        storage_by_plant[location.plant_id].append(location)

    plants_by_org: dict[str, list[dict]] = defaultdict(list)
    for mapping in mappings:
        plants_by_org[mapping.organization_id].append(
            {"plant": mapping.plant, "is_primary": mapping.is_primary}
        )
    # Union in plants whose FK points at an org with no mapping row, mirroring
    # what the legacy tree did — otherwise a plant that predates the mapping
    # table would silently vanish from the hierarchy.
    mapped_pairs = {(mapping.plant_id, mapping.organization_id) for mapping in mappings}
    for plant in plants.values():
        if plant.organization_id and (plant.pk, plant.organization_id) not in mapped_pairs:
            plants_by_org[plant.organization_id].append({"plant": plant, "is_primary": True})

    orgs_by_company: dict[str, list] = defaultdict(list)
    for organization in orgs:
        orgs_by_company[organization.company_id].append(organization)

    tree = []
    for company in companies:
        company_orgs = []
        for organization in orgs_by_company.get(company.pk, []):
            org_plants = []
            for entry in plants_by_org.get(organization.pk, []):
                org_plants.append(
                    {
                        "plant": entry["plant"],
                        "is_primary": entry["is_primary"],
                        "storage": storage_by_plant.get(entry["plant"].pk, []),
                    }
                )
            company_orgs.append({"org": organization, "plants": org_plants})
        tree.append({"company": company, "orgs": company_orgs})

    # Anything unreachable from a company is listed separately rather than
    # dropped — an orphan is exactly what an admin needs to see and fix, and the
    # importer can create them by writing a row whose parent did not resolve.
    orphan_orgs = [organization for organization in orgs if organization.company_id is None]
    orphan_plants = [plant for plant in plants.values() if not plant.organization_id]

    return render(
        request,
        "merilv/masters_org_tree.html",
        _ctx(
            request,
            active_tab="org_tree",
            tree=tree,
            orphan_orgs=orphan_orgs,
            orphan_plants=orphan_plants,
            counts={
                "companies": len(companies),
                "orgs": len(orgs),
                "plants": len(plants),
                "storage": sum(len(v) for v in storage_by_plant.values()),
                "mappings": len(mappings),
            },
        ),
    )


# ─── Spreadsheet registry ────────────────────────────────────────────────────
# One entry per master, and the single source of truth for its column set. The
# per-table Template / Import / Export / Clear buttons and the whole-module
# "Download Template" / "Export All Data" / "Bulk Import Excel" actions are all
# driven from here, so a template can never disagree with what the importer
# expects or what the export produces.


def _org_export_rows(table: str) -> list[list]:
    spec = ORG_TABLES[table]
    code_field, name_field = spec["fields"]
    rows = []
    for row in _org_rows(table):
        cells = [getattr(row, code_field, ""), getattr(row, name_field, "")]
        if table == "storage":
            # Storage's headers lead with the plant, matching how SAP exports it.
            cells = [row.plant.plant_code if row.plant_id else "", *cells]
        elif spec["parent"]:
            parent = getattr(row, spec["parent"][0], None)
            cells.append(getattr(parent, spec["parent"][2], "") if parent else "")
        rows.append(cells)
    return rows


def _org_load(table: str, records) -> tuple[int, int, int]:
    spec = ORG_TABLES[table]
    model, pk_field = spec["model"], spec["pk_field"]
    code_field, name_field = spec["fields"]
    parent_spec = spec["parent"]

    created = updated = skipped = 0
    for record in records:
        code = (record.get("code") or "").strip().upper()
        if not code:
            skipped += 1
            continue
        name = (record.get("name") or "").strip()

        parent = None
        parent_given = parent_spec is not None and "parent" in record
        if parent_given:
            _, parent_model, parent_pk = parent_spec
            value = (record.get("parent") or "").strip()
            if value:
                parent = parent_model.objects.filter(**{f"{parent_pk}__iexact": value}).first()

        if table == "storage":
            # A storage location is meaningless without its plant, and its
            # uniqueness is (code, plant) — so an unresolvable parent is a skip,
            # not a row with a dangling reference.
            if parent is None:
                skipped += 1
                continue
            defaults = {"storage_name": name} if "name" in record else {}
            _, was_created = StorageMaster.objects.update_or_create(
                storage_code=code, plant=parent, defaults=defaults,
            )
        else:
            # Only what the sheet carried. Writing these unconditionally meant a
            # narrow company sheet blanked every name, and a narrow plant sheet
            # CLEARED the sales-org link on every plant in it — silently
            # dismantling the hierarchy the tree view is built on (#11-16).
            defaults = {}
            if "name" in record:
                defaults[name_field] = name
            if parent_given:
                defaults[parent_spec[0]] = parent
            _, was_created = model.objects.update_or_create(**{pk_field: code}, defaults=defaults)

        created += was_created
        updated += not was_created
    return created, updated, skipped


def _entity_lookup() -> dict:
    return {entity.code.upper(): entity for entity in Entity.objects.all()}


#: Header spellings that name the business unit on an imported sheet.
_ENTITY_HEADERS = frozenset({"entity", "entity code", "business unit", "unit"})


def _row_entity(raw_data, lookup: dict, fallback=None):
    """Resolve a row's entity from whichever column names it."""
    for header, value in (raw_data or {}).items():
        if str(header).strip().lower() in _ENTITY_HEADERS:
            code = str(value or "").strip().upper()
            if code:
                return lookup.get(code, fallback)
    return fallback


def _load_materials(records, entity=None) -> tuple[int, int, int]:
    lookup = _entity_lookup()
    created = updated = skipped = 0
    for record in records:
        target = _row_entity(record.get("raw_data"), lookup, entity)
        if target is None:
            # Materials are stored per entity; a row that names none and has no
            # fallback cannot be filed anywhere.
            skipped += 1
            continue
        defaults = {
            "material_code": record["material_code"],
            "material_description": record["material_description"],
            "gtin": record["gtin"],
            "material_group": record["material_group"],
            "raw_data": record["raw_data"],
        }
        # YMM061 columns the parser found. Only keys actually present are copied,
        # so importing a narrow sheet cannot blank the wide columns a previous
        # full YMM061 import already populated.
        for _header, field_name in MaterialMaster.YMM061_COLUMNS:
            if field_name in record and field_name not in defaults:
                defaults[field_name] = record[field_name]

        _, was_created = MaterialMaster.objects.update_or_create(
            entity=target, scan_code=record["scan_code"], defaults=defaults,
        )
        created += was_created
        updated += not was_created
    return created, updated, skipped


def _customer_code_key(code) -> str:
    """A customer code reduced to what identifies it.

    SAP pads customer codes to a fixed width, so the same party is ``1000280``
    in one extract and ``0001000280`` in the next. Every lookup in the module
    already treats the two as the same customer —
    ``entities.build_customer_master`` registers the stripped variant and
    ``resolve_customer_name`` tries it — so matching strictly on import was the
    only place a padded code became a *second* customer.
    """
    text = str(code or "").strip().upper()
    return text.lstrip("0") or text


def _customer_code_index() -> dict[str, str]:
    """Identifying key → the code that row is actually stored under.

    Read once per import rather than queried per row: a customer sheet is
    thousands of rows and this is the difference between one query and one
    query each. The stored spelling is what comes back, because that is the
    value deliveries, scans and courier mappings refer to — the row is updated
    under the code it already has, never renamed to whatever spelling the
    latest sheet happened to use.
    """
    index: dict[str, str] = {}
    for code in CustomerMaster.objects.values_list("customer_code", flat=True).iterator():
        if code:
            index.setdefault(_customer_code_key(code), code)
    return index


def _load_customers(records, entity=None) -> tuple[int, int, int]:
    """Upsert customers, matched on code.

    A record is a **partial** update: the parser emits a key only for a column
    the uploaded sheet actually had (see `_customer_master_from_sheet`), so the
    defaults below carry only those keys. A customer sheet without a courier
    column must leave `courier_partner` alone rather than blanking it — which is
    what would quietly undo every courier assignment the client makes by hand.

    Matching is padding-insensitive (see :func:`_customer_code_key`), so
    re-importing an SAP extract updates the customers already on file and adds
    only the genuinely new ones, instead of shadowing every existing row with a
    zero-padded twin.
    """
    lookup = _entity_lookup()
    index = _customer_code_index()
    created = updated = mapped = 0
    for record in records:
        values = dict(record)
        code = str(values.pop("customer_code") or "").strip()
        # Popped before the model sees it: these are rows for a different
        # table (see below), not fields of CustomerMaster.
        plant_couriers = values.pop("_plant_couriers", [])
        if not code:
            continue
        # Update the row under the code it is stored as; only a code nothing
        # matches is inserted, and then under the spelling the sheet gave.
        key = _customer_code_key(code)
        code = index.get(key, code)
        target = _row_entity(values.get("raw_data"), lookup, entity)
        # Only written when known: a sheet that omits the column should leave an
        # existing customer's entity alone rather than clearing it.
        if target is not None:
            values["entity"] = target
        # An imported courier name is canonicalised for the same reason a typed
        # one is: the link is by name, so a spreadsheet's casing decides whether
        # the customer shows up in the Courier module at all (#18).
        if values.get("courier_partner"):
            values["courier_partner"] = canonical_courier_name(values["courier_partner"])
        _, was_created = CustomerMaster.objects.update_or_create(customer_code=code, defaults=values)
        created += was_created
        updated += not was_created
        # So a second spelling of the same code LATER IN THE SAME FILE updates
        # the row this iteration just made rather than colliding with it.
        index.setdefault(key, code)

        # "This customer uses this courier out of this plant" — the fact a
        # globally-unique customer_code cannot hold. CustomerMaster keeps the
        # default; these carry the per-plant truth, and courier_usage already
        # reads them as a second link source.
        #
        # Replaced per customer rather than merged: the sheet is the authority
        # on where a customer ships from, so a plant it no longer lists must
        # stop claiming that customer's courier.
        if plant_couriers:
            wanted = {row["plant"].strip() for row in plant_couriers if row.get("plant")}
            CustomerCourierMapping.objects.filter(customer_code=code).exclude(
                plant__in=wanted
            ).delete()
            for row in plant_couriers:
                plant = (row.get("plant") or "").strip()
                if not plant:
                    continue
                CustomerCourierMapping.objects.update_or_create(
                    customer_code=code,
                    entity=values.get("entity"),
                    plant=plant,
                    defaults={
                        "courier_name": canonical_courier_name(row.get("courier_name") or ""),
                        "courier_number": (row.get("courier_number") or "").strip(),
                    },
                )
                mapped += 1
    return created, updated, 0


def _existing_courier(record):
    """The stored row a courier import record updates, or None to create one.

    Matched on the courier CODE within a PLANT, not on the name. The same
    carrier is held once per plant with its own partner code each time — the
    master itself says so, which is why `_active_courier_names` blanks the
    number when a name resolves to more than one. Matching on the name meant
    re-importing the full sheet collapsed a carrier's per-plant rows onto
    whichever one was found first, taking that plant's code and vehicle with
    it.

    Two ways a sheet has nothing to match on, both falling back to the name
    exactly as before:

    * **no Courier Number column** — the sheet has no opinion about codes, so
      there is no composite key to build.
    * **a blank code cell** — keying on ("", plant) would make every uncoded
      row in a plant the same courier and merge them into one.

    With a code but no Plant column the code alone decides, which is the most
    the sheet supports; ``order_by("pk")`` only makes that stable rather than
    dependent on however the database felt like returning rows.
    """
    code = (record.get("courier_number") or "").strip()
    if not code:
        return CourierMaster.objects.filter(
            courier_name__iexact=record["courier_name"]
        ).order_by("pk").first()
    rows = CourierMaster.objects.filter(courier_number__iexact=code)
    if "plant" in record:
        rows = rows.filter(plant__iexact=(record.get("plant") or "").strip())
    # The entity narrows it further where the sheet names one. Without this a
    # re-import collapsed two business units' rows for the same code and plant
    # onto whichever came back first — the same merge the plant fixed a round
    # earlier, one level up.
    if "entity_code" in record:
        wanted = (record.get("entity_code") or "").strip()
        rows = (
            rows.filter(entity__code__iexact=wanted) if wanted
            else rows.filter(entity__isnull=True)
        )
    return rows.order_by("pk").first()


def _load_couriers(records) -> tuple[int, int, int]:
    """Upsert couriers, matched on courier code + plant (see `_existing_courier`).

    A record is a **partial** update: the parser emits a key only for a column
    the uploaded sheet actually had (see `_courier_master_from_sheet`). So the
    loop below deliberately copies only the keys it is given rather than every
    field of the model — a sheet without a GSTIN column must leave the stored
    GSTIN alone, not blank it.
    """
    # Entity arrives as a CODE from the sheet and has to become the FK. Built
    # once: a courier sheet is small but this is a per-row lookup otherwise.
    entities = {
        code.lower(): pk
        for code, pk in Entity.objects.values_list("code", "pk")
    }

    created = updated = skipped = 0
    for record in records:
        record = dict(record)
        if "entity_code" in record:
            wanted = (record.pop("entity_code") or "").strip()
            if not wanted:
                # A column with an empty cell is a deliberate clear, matching
                # how every other field on this sheet behaves.
                record["entity_id"] = None
            elif wanted.lower() in entities:
                record["entity_id"] = entities[wanted.lower()]
            else:
                # Naming an entity that does not exist is a typo in the sheet,
                # not an instruction to file the courier under nothing — which
                # is how a row ends up matching no customer and reading as
                # "this courier has no customers".
                skipped += 1
                continue

        existing = _existing_courier(record)
        if existing:
            for field, value in record.items():
                setattr(existing, field, value)
            existing.save()
            updated += 1
        else:
            # Fields the sheet omitted fall back to the model's own defaults.
            CourierMaster.objects.create(**record)
            created += 1
    return created, updated, skipped


def _to_number(value, default=0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _load_boxes(records) -> tuple[int, int, int]:
    """Upsert cartons, matched on code.

    A record is a **partial** update: the parser emits a key only for a column
    the uploaded sheet actually had (see `parse_simple_master_grid`), so the
    defaults below are built from the keys present rather than from every field
    of the model. A carton sheet without a SKU Name column must leave the SKU
    Name alone rather than blanking it — which is the very column #20 asks for.
    """
    created = updated = skipped = 0
    for record in records:
        code = (record.get("code") or "").strip()
        if not code:
            skipped += 1
            continue

        defaults: dict[str, object] = {}
        if "sku_name" in record:
            defaults["sku_name"] = (record.get("sku_name") or "").strip()
        for field in ("length_cm", "width_cm", "height_cm"):
            if field in record:
                defaults[field] = _to_number(record.get(field))
        if "divisor" in record:
            # 0 or unparseable falls back to the standard 5000 rather than to a
            # divisor of zero, which would make volumetric weight undefined.
            defaults["divisor"] = int(_to_number(record.get("divisor"), 5000)) or 5000
        if "max_qty" in record:
            defaults["max_qty"] = int(_to_number(record.get("max_qty")))
        if "active" in record:
            active = str(record.get("active") or "").strip().lower()
            defaults["is_active"] = active not in ("no", "0", "false", "inactive", "n")

        _, was_created = BoxProfile.objects.update_or_create(code=code, defaults=defaults)
        created += was_created
        updated += not was_created
    return created, updated, skipped


BOX_COLUMNS = {
    "code": ("Box Code", "Code"),
    "sku_name": ("SKU Name", "SKU", "Name"),
    "length_cm": ("Length (cm)", "Length"),
    "width_cm": ("Width (cm)", "Width"),
    "height_cm": ("Height (cm)", "Height"),
    "divisor": ("Volumetric Divisor", "Divisor"),
    "max_qty": ("Max Units", "Max Qty"),
    "active": ("Active", "Status"),
}


def _org_sheet(table: str) -> dict:
    spec = ORG_TABLES[table]
    return {
        "label": spec["label"],
        "title": spec["title"],
        "sheet": spec["title"],
        "headers": spec["headers"],
        "model": spec["model"],
        "rows": lambda table=table: _org_export_rows(table),
        "parse_file": lambda upload, table=table: ingest.parse_simple_master(
            upload,
            ORG_TABLES[table]["columns"],
            dedupe=ORG_TABLES[table].get("dedupe", ()),
            filename=upload.name,
            key="code",
        ),
        "load": lambda records, entity=None, table=table: _org_load(table, records),
        "landing": lambda table=table: _org_url(table),
    }


MASTER_SHEETS: dict[str, dict] = {table: _org_sheet(table) for table in ORG_TABLE_ORDER}

# Entity/Scan Code/GTIN are this app's own keys and lead the sheet; the rest is
# YMM061 verbatim, so a downloaded template can be filled from an SAP extract by
# pasting and an export can be diffed against one.
_MATERIAL_YMM_HEADERS = tuple(header for header, _field in MaterialMaster.YMM061_COLUMNS)
_MATERIAL_YMM_FIELDS = tuple(field for _header, field in MaterialMaster.YMM061_COLUMNS)


def _material_cell(row, field):
    """One export cell, in the spelling SAP uses.

    The boolean columns go back out as "X"/"" rather than True/False, so an
    export stays diffable against the YMM061 extract it came from and can be
    re-imported without a hand edit — which is the whole point of the sheet
    matching YMM061 verbatim.
    """
    value = getattr(row, field, "")
    if field in MaterialMaster.YMM061_BOOLEAN_FIELDS:
        return "X" if value else ""
    return value


MASTER_SHEETS["materials"] = {
    "label": "Material",
    "title": "Material Master",
    "sheet": "Material Master",
    "headers": ("Entity", "Scan Code", "GTIN") + _MATERIAL_YMM_HEADERS,
    "model": MaterialMaster,
    "rows": lambda: [
        [r.entity.code, r.scan_code, r.gtin] + [_material_cell(r, f) for f in _MATERIAL_YMM_FIELDS]
        for r in MaterialMaster.objects.select_related("entity")
    ],
    "parse_file": lambda upload: ingest.parse_material_master(upload, upload.name),
    "load": _load_materials,
    "landing": lambda: reverse("merilv:masters_materials"),
    "entity_scoped": True,
}

MASTER_SHEETS["customers"] = {
    "label": "Customer",
    "title": "Customer Master",
    "sheet": "Customer Master",
    "headers": (
        "Customer Code", "Customer Name", "Entity", "Company Code", "Plant", "Courier Partner",
        "Courier Number", "City", "State", "Country", "Pincode", "GSTIN", "Email", "Mobile",
        "Contact Person", "Address", "Blocked",
    ),
    "model": CustomerMaster,
    "rows": lambda: [
        [
            c.customer_code, c.customer_name, c.entity.code if c.entity else "", c.company_code,
            c.plant, c.courier_partner, c.courier_number, c.city, c.state, c.country, c.pincode,
            c.gstin, c.email, c.mobile, c.contact_person, c.address, c.is_blocked,
        ]
        for c in CustomerMaster.objects.select_related("entity")
    ],
    "parse_file": lambda upload: ingest.parse_customer_master(upload, upload.name),
    "load": _load_customers,
    "landing": lambda: reverse("merilv:masters_customers"),
}

MASTER_SHEETS["couriers"] = {
    "label": "Courier",
    "title": "Courier Master",
    "sheet": "Courier Master",
    # "Mode of Transit" is appended, never inserted: the import resolves
    # columns by header name and treats an absent column as "no opinion"
    # (see ingest._courier_master_from_sheet), so an older sheet still
    # round-trips and only the new column is new.
    # "Entity" sits next to Plant because the two are read together and were
    # being confused for one another: without this column the entity was typed
    # into Plant, and a row tagged MHPL can never match a customer whose plant
    # is WGJ7. Appended like "Mode of Transit" before it, so an older sheet
    # without the column still round-trips and simply has no opinion.
    "headers": (
        "Courier Number", "Courier Name", "Entity", "Plant", "Vehicle Number",
        "Vehicle Type", "Mode of Transit", "GSTIN", "Active",
    ),
    "model": CourierMaster,
    "rows": lambda: [
        [
            c.courier_number, c.courier_name,
            c.entity.code if c.entity_id else "", c.plant, c.vehicle_number,
            c.vehicle_type, c.mode_of_transit, c.gstin, c.is_active,
        ]
        for c in CourierMaster.objects.select_related("entity")
    ],
    "parse_file": lambda upload: ingest.parse_courier_master(upload, upload.name),
    "load": lambda records, entity=None: _load_couriers(records),
    "landing": lambda: reverse("merilv:masters_couriers"),
}

MASTER_SHEETS["boxes"] = {
    "label": "Carton",
    "title": "Carton Master",
    "sheet": "Carton Master",
    "headers": (
        "Box Code", "SKU Name", "Length (cm)", "Width (cm)", "Height (cm)",
        "Volumetric Divisor", "Max Units", "Active",
    ),
    "model": BoxProfile,
    "rows": lambda: [
        [b.code, b.sku_name, b.length_cm, b.width_cm, b.height_cm, b.divisor, b.max_qty, b.is_active]
        for b in BoxProfile.objects.all()
    ],
    "parse_file": lambda upload: ingest.parse_simple_master(
        upload, BOX_COLUMNS, filename=upload.name, key="code",
    ),
    "load": lambda records, entity=None: _load_boxes(records),
    "landing": lambda: reverse("merilv:masters_boxes"),
}


def _sheet_or_404(key: str) -> dict:
    sheet = MASTER_SHEETS.get(key)
    if sheet is None:
        raise Http404(f"No master named {key!r}.")
    return sheet


def _landing_for(key: str) -> str:
    return MASTER_SHEETS[key]["landing"]()


# ─── Per-master template / export / import / clear ───────────────────────────
@login_required
@admin_required
def master_template(request, key):
    """A header-only workbook for one master, ready to be filled in and imported.

    Built on the fly from the registry rather than served as a checked-in file,
    so it always matches the current column set — and so no sample spreadsheet
    ever needs to live in the repository.
    """
    sheet = _sheet_or_404(key)
    buffer = export.export_generic(f"{sheet['title']} Template", list(sheet["headers"]), [])
    return _xlsx_response(buffer, f"merilv_{key}_template.xlsx")


@login_required
@admin_required
def master_export(request, key):
    sheet = _sheet_or_404(key)
    buffer = export.export_generic(sheet["title"], list(sheet["headers"]), sheet["rows"]())
    return _xlsx_response(buffer, f"merilv_{key}.xlsx")


@login_required
@admin_required
@capability_required("mv_master_upload")
@require_POST
def master_import(request, key):
    """Import one uploaded file into one master."""
    sheet = _sheet_or_404(key)
    landing = _landing_for(key)
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Choose an .xlsx or .csv file to import.")
        return redirect(landing)
    if not upload.name.lower().endswith((".xlsx", ".xls", ".csv")):
        messages.error(request, "Only .xlsx, .xls and .csv files are accepted.")
        return redirect(landing)

    entity = Entity.objects.filter(code=request.POST.get("entity")).first()
    if sheet.get("entity_scoped") and entity is None:
        messages.error(request, f"Choose the entity this {sheet['label'].lower()} file belongs to.")
        return redirect(landing)

    try:
        # The file-based parser, not the grid one: it searches the first 30 rows
        # for the header instead of assuming row 1, which is what makes a raw SAP
        # extract (title rows, blank rows, variant headers) importable at all.
        result = sheet["parse_file"](upload)
    except ingest.IngestError as exc:
        messages.error(request, str(exc))
        return redirect(landing)

    if not result.records:
        messages.error(request, "No usable rows were found in the file.")
        return redirect(landing)

    replace = request.POST.get("replace") == "1"
    with transaction.atomic():
        if replace:
            _clear_master(key, entity)
        created, updated, skipped = sheet["load"](result.records, entity=entity)

    _log(
        request, f"{key}_import", sheet["model"].__name__, entity.code if entity else "",
        created=created, updated=updated, skipped=skipped, replaced=replace,
    )
    for warning in result.warnings:
        messages.warning(request, warning)
    if skipped:
        messages.warning(request, f"{skipped:,} row(s) skipped — unresolved parent or missing key.")
    messages.success(
        request,
        f"Imported {created:,} new and updated {updated:,} existing {sheet['label'].lower()} rows.",
    )
    return redirect(landing)


def _clear_master(key: str, entity=None) -> int:
    """Delete every row of one master, optionally scoped to one entity."""
    sheet = MASTER_SHEETS[key]
    rows = sheet["model"].objects.all()
    if entity is not None and sheet.get("entity_scoped"):
        rows = rows.filter(entity=entity)
    deleted, _ = rows.delete()
    return deleted


@login_required
@admin_required
@require_POST
def master_clear(request, key):
    """Empty one master. Guarded by a typed confirmation in the UI."""
    sheet = _sheet_or_404(key)
    landing = _landing_for(key)
    entity = Entity.objects.filter(code=request.POST.get("entity")).first()

    try:
        deleted = _clear_master(key, entity)
    except ProtectedError:
        messages.error(
            request,
            f"Some {sheet['label'].lower()} rows are still referenced by transactional data "
            "and cannot be deleted.",
        )
        return redirect(landing)

    scope = f" for {entity.code}" if entity is not None and sheet.get("entity_scoped") else ""
    _log(request, f"{key}_clear", sheet["model"].__name__, entity.code if entity else "", deleted=deleted)
    messages.success(request, f"Cleared {deleted:,} {sheet['label'].lower()} row(s){scope}.")
    return redirect(landing)


@login_required
@admin_required
@require_POST
def master_row_delete(request, key):
    """Delete a single row of any spreadsheet-backed master.

    One endpoint for every master rather than one per model: the registry already
    knows the model and where to return to, and the operation is the same
    everywhere. ``pk`` is the spelling that works for both the code-keyed tables
    and storage's surrogate id.

    A hard delete is safe here — no model declares a ForeignKey to any of these
    masters (transactional rows carry courier/customer/box identifiers as plain
    char columns), so there is nothing to cascade into or orphan. The
    ``ProtectedError`` guard stays as cheap insurance in case that changes.
    """
    sheet = _sheet_or_404(key)
    landing = _landing_for(key)
    pk = (request.POST.get("pk") or "").strip()
    if not pk:
        messages.error(request, "Nothing selected to delete.")
        return redirect(landing)

    try:
        deleted, _ = sheet["model"].objects.filter(pk=pk).delete()
    except ProtectedError:
        messages.error(
            request,
            f"That {sheet['label'].lower()} is still referenced elsewhere and cannot be deleted.",
        )
        return redirect(landing)

    if deleted:
        _log(request, f"{key}_row_delete", sheet["model"].__name__, pk)
        messages.success(request, f"{sheet['label']} deleted.")
    else:
        messages.warning(request, f"That {sheet['label'].lower()} no longer exists.")
    return redirect(_redirect_back(request, "merilv:masters") if request.POST.get("next") else landing)


# ─── Whole-module export ─────────────────────────────────────────────────────
@login_required
@admin_required
def masters_export_all(request):
    specs = [
        export.SheetSpec(sheet["sheet"], list(sheet["headers"]), sheet["rows"]())
        for sheet in MASTER_SHEETS.values()
    ]
    return _xlsx_response(export.build_workbook(specs), "merilv_master_data.xlsx")
