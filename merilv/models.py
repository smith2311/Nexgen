"""Meril-V — warehouse validation & outbound operations.

Replaces the legacy standalone MerilValidator stack (Node/Express + Supabase +
vanilla JS). Authentication, user management and audit are deliberately *not*
re-implemented here: the platform already provides them, and the PRD lists
"rebuilding features already available in the target platform" as out of scope.

The legacy system shipped eight near-identical pages — four stock validators and
four outbound scanners, one per business unit. Those are collapsed into a single
validator and a single scanner driven by :class:`Entity`, which is the PRD's
"module consolidation via dropdown/unit abstraction".
"""

from django.conf import settings
from django.db import models

# The granular-capability catalogue, for Entity.Meta.permissions below. Safe to
# import here: permissions.py pulls in rbac.py, which imports no models at
# module level — every model it touches is imported inside the function that
# needs it, precisely so this direction stays open.
from . import permissions as _merilv_permissions

# ─────────────────────────────────────────────────────────────────────────────
# Shared configuration
# ─────────────────────────────────────────────────────────────────────────────

#: The material master's group column and the validation rows stamped from
#: it share one label, so an admin screen and an export header cannot drift
#: apart when it is reworded.
MATERIAL_GROUP_LABEL = "Material Group"


class Entity(models.Model):
    """A Meril business unit and the parsing/matching rules that apply to it.

    Every per-unit difference the legacy forks encoded in separate JS files —
    whether serials exist, how batches are sanitised, which barcode strategies
    are tried — lives here as data instead of code.
    """

    code = models.CharField(max_length=20, unique=True, verbose_name="Entity Code")
    name = models.CharField(max_length=150, verbose_name="Entity Name")
    company_name = models.CharField(max_length=200, blank=True, verbose_name="Legal Company Name")

    # ── Barcode parsing behaviour ──────────────────────────────────────
    strip_whitespace = models.BooleanField(
        default=True,
        verbose_name="Strip whitespace before parsing",
        help_text="Some hand scanners inject stray spaces into the barcode string.",
    )
    supports_serial = models.BooleanField(
        default=True,
        verbose_name="SAP tracks stock at serial level",
        help_text=(
            "Off for units that track stock only to batch level. This gates the "
            "Stock Validator's requirement for a serial column in the SAP dump; "
            "it does not stop a scan being given a serial for the dispatch sheet."
        ),
    )
    ai21_is_material = models.BooleanField(
        default=False,
        verbose_name="Treat GS1 (21) as material",
        help_text=(
            "Non-standard: a few units encode the material in the serial AI. "
            "Resolved per value — a code the Material Master knows, or one "
            "carrying letters, is read as the material; a bare running number "
            "is read as the unit's serial."
        ),
    )
    sanitise_batch = models.BooleanField(
        default=False,
        verbose_name="Strip punctuation from batch",
        help_text="Removes anything outside A-Z, 0-9 and hyphen before comparing.",
    )
    allow_envelope = models.BooleanField(
        default=False,
        verbose_name="Accept A…A envelope and ^/~ AI prefixes",
    )
    positional_walker = models.BooleanField(
        default=False,
        verbose_name="Walk unbracketed AIs positionally",
        help_text="Enables parsing of barcodes printed without parentheses.",
    )
    compact_gs1 = models.BooleanField(
        default=False,
        verbose_name="Accept compact GS1 (locked field order)",
    )
    positional_rules = models.BooleanField(
        default=False,
        verbose_name="Use length-keyed positional rules",
    )
    trauma_legacy = models.BooleanField(
        default=False,
        verbose_name="Accept Meril Trauma legacy layout",
    )
    pseudo_serial_digits = models.PositiveSmallIntegerField(
        default=5,
        verbose_name="Synthetic serial digits",
        help_text="Width of the random suffix given to non-serialised items.",
    )
    parse_rules_csv = models.TextField(
        blank=True,
        verbose_name="Positional parse rules (CSV)",
        help_text="One rule per line: Len, MatStart, MatLen, BatStart, BatLen, MfgStart, MfgLen, ExpStart, ExpLen",
    )

    # ── Expiry classification ──────────────────────────────────────────
    # The two legacy families disagreed on which threshold is tighter, so both
    # are explicit here rather than being unified silently.
    near_expiry_months = models.PositiveSmallIntegerField(default=6, verbose_name="Near Expiry (months)")
    short_expiry_months = models.PositiveSmallIntegerField(default=3, verbose_name="Short Expiry (months)")
    month_divisor = models.DecimalField(
        max_digits=5, decimal_places=2, default=30.44,
        verbose_name="Days per month",
        help_text="30 for the legacy MHPL engine, 30.44 elsewhere.",
    )

    # ── Validation engine toggles ──────────────────────────────────────
    match_concat = models.BooleanField(default=True, verbose_name="Enable concat-serial pass")
    match_batch = models.BooleanField(default=True, verbose_name="Enable batch-match pass")
    match_material = models.BooleanField(default=True, verbose_name="Enable material-only pass")
    concat_delimiter = models.CharField(max_length=4, blank=True, verbose_name="Concat delimiter")
    triple_sort = models.BooleanField(default=True, verbose_name="Plant → expiry → qty sorting")
    multi_row_consume = models.BooleanField(default=True, verbose_name="Split a scan across SAP rows")
    smart_zero = models.BooleanField(default=True, verbose_name="Match ignoring leading zeros")
    use_scan_qty = models.BooleanField(default=True, verbose_name="Honour quantity from scan file")
    strict_batch = models.BooleanField(default=False, verbose_name="Compare batches strictly")
    plant_priority = models.CharField(
        max_length=200, blank=True,
        verbose_name="Plant priority",
        help_text="Comma-separated, highest priority first. Example: WGJ1, WGJ7, WGJ4",
    )
    good_storage_location = models.CharField(max_length=20, default="RT01", verbose_name="Good stock S.Loc")
    damage_storage_location = models.CharField(max_length=20, default="DMG1", verbose_name="Damaged stock S.Loc")

    # ── Outbound behaviour ─────────────────────────────────────────────
    allow_customer_focus = models.BooleanField(default=False, verbose_name="Allow focusing a whole customer")
    aggregate_delivery_lines = models.BooleanField(
        default=False,
        verbose_name="Aggregate delivery lines",
        help_text="Sum quantities across repeated delivery/item/material/batch rows. Never merges different item numbers.",
    )
    enforce_box_capacity = models.BooleanField(default=False, verbose_name="Enforce box capacity")
    allow_partial_export = models.BooleanField(default=False, verbose_name="Allow exporting incomplete deliveries")

    # ── Branch transfer behaviour ───────────────────────────────────────
    bt_location_priority = models.CharField(
        max_length=200, blank=True, default="FG02, FG01, FG03",
        verbose_name="Branch transfer location priority",
        help_text=(
            "Comma-separated, highest priority first — which storage location's "
            "stock auto-batch-assign draws from first. Ported from the legacy "
            "tool's bt_location_priority setting."
        ),
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = "Entity"
        verbose_name_plural = "Entities"
        # The module's granular capabilities — Batch Correction, Courier Edit,
        # Document Edit, scanning, master upload. They hang off Entity because
        # Django needs a concrete model to own a Permission and Entity is the
        # module's root configuration object; nothing reads them THROUGH an
        # entity. Defined in merilv/permissions.py so the catalogue, the group
        # seeding and the Roles & Access matrix all read from one list.
        permissions = _merilv_permissions.MODEL_PERMISSIONS

    def __str__(self):
        return f"{self.code} — {self.name}" if self.name else self.code

    @property
    def plant_priority_list(self):
        """Plant priority as an uppercased list, highest priority first."""
        return [p.strip().upper() for p in (self.plant_priority or "").split(",") if p.strip()]

    @property
    def bt_location_priority_list(self):
        """Branch-transfer location priority as an uppercased list."""
        return [p.strip().upper() for p in (self.bt_location_priority or "").split(",") if p.strip()]


class ToolSetting(models.Model):
    """Admin-managed JSON configuration, keyed by name.

    The legacy ``global_config`` table, kept so operations staff can retune
    behaviour without a deployment.
    """

    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_settings_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "Tool Setting"
        verbose_name_plural = "Tool Settings"


class SavedLayout(models.Model):
    """A named, per-user column preset for one screen.

    The legacy "Layout Manager" (``script.js:3183+`` — named presets in
    ``localStorage``, "Standard View" / "Simple Check", including dynamic
    Material Master columns) generalised into one reusable mechanism instead
    of a screen-specific rebuild each time a row asks for it (Rows 3 and 17
    both did). Server-backed rather than per-browser, so a saved layout
    follows the user across devices instead of staying trapped in one
    browser's storage — the old tool's own limitation.

    ``screen_key`` names which table this belongs to (e.g. "scan_history",
    "masters_materials"); the set of columns actually available for a
    screen is defined in code (``merilv/layouts.py``), not stored here — a
    layout only records which of those columns are shown, in what order,
    and under what header text.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merilv_saved_layouts",
    )
    screen_key = models.CharField(max_length=60, db_index=True)
    name = models.CharField(max_length=100)
    #: [{"key": "material_code", "header": "Material", "enabled": true}, ...] —
    #: order in the list is display order. ``header`` overrides the column's
    #: registry label; absent/blank means "use the default label".
    columns = models.JSONField(default=list, blank=True)
    #: At most one active layout per user+screen — enforced by the
    #: conditional unique constraint below, not just application code.
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["screen_key", "name"]
        verbose_name = "Saved Layout"
        verbose_name_plural = "Saved Layouts"
        constraints = [
            models.UniqueConstraint(fields=["user", "screen_key", "name"], name="merilv_layout_unique_name"),
            models.UniqueConstraint(
                fields=["user", "screen_key"], condition=models.Q(is_active=True),
                name="merilv_layout_one_active",
            ),
        ]
        indexes = [models.Index(fields=["user", "screen_key"])]

    def __str__(self):
        return f"{self.name} ({self.screen_key})"


# ─────────────────────────────────────────────────────────────────────────────
# Master data
# ─────────────────────────────────────────────────────────────────────────────


class CompanyMaster(models.Model):
    company_code = models.CharField(max_length=20, primary_key=True)
    company_name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_code"]
        verbose_name = "Company"
        verbose_name_plural = "Companies"

    def __str__(self):
        return f"{self.company_code} — {self.company_name}"


class OrganizationMaster(models.Model):
    org_code = models.CharField(max_length=20, primary_key=True)
    org_name = models.CharField(max_length=200)
    company = models.ForeignKey(
        CompanyMaster, null=True, blank=True, on_delete=models.CASCADE, related_name="organizations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["org_code"]
        verbose_name = "Sales Organization"
        verbose_name_plural = "Sales Organizations"

    def __str__(self):
        return f"{self.org_code} — {self.org_name}"


class PlantMaster(models.Model):
    plant_code = models.CharField(max_length=20, primary_key=True)
    plant_name = models.CharField(max_length=200)
    organization = models.ForeignKey(
        OrganizationMaster, null=True, blank=True, on_delete=models.SET_NULL, related_name="plants",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plant_code"]
        verbose_name = "Plant"
        verbose_name_plural = "Plants"

    def __str__(self):
        return f"{self.plant_code} — {self.plant_name}"


class StorageMaster(models.Model):
    storage_code = models.CharField(max_length=20)
    storage_name = models.CharField(max_length=200)
    plant = models.ForeignKey(PlantMaster, on_delete=models.CASCADE, related_name="storage_locations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plant_id", "storage_code"]
        unique_together = [("storage_code", "plant")]
        verbose_name = "Storage Location"
        verbose_name_plural = "Storage Locations"

    def __str__(self):
        return f"{self.plant_id}/{self.storage_code}"


class PlantOrgMapping(models.Model):
    """Which sales organisations a plant belongs to.

    A plant can serve more than one sales org, with one of them designated
    primary. ``PlantMaster.organization`` models only the primary and cannot
    express the rest — the legacy tool kept both a single parent column *and* a
    join table, and only the former survived the rewrite. The mapping rows were
    carried across into :class:`LegacyPlantOrgMapping`, which nothing reads, so
    the relationship existed in the database but was invisible to the
    application. This is that join table, restored.

    ``PlantMaster.organization`` is kept as the primary denormalisation rather
    than dropped: a great deal of code resolves a plant's org through it, and
    the legacy hierarchy view read both and unioned them.
    """

    plant = models.ForeignKey(PlantMaster, on_delete=models.CASCADE, related_name="org_mappings")
    organization = models.ForeignKey(
        OrganizationMaster, on_delete=models.CASCADE, related_name="plant_mappings",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="One org per plant is the primary. Mirrors PlantMaster.organization.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["plant_id", "-is_primary", "organization_id"]
        verbose_name = "Plant / Sales Org Mapping"
        verbose_name_plural = "Plant / Sales Org Mappings"
        # A plant maps to a given org once. Deliberately not unique on plant
        # alone — that constraint is exactly what this table exists to lift.
        unique_together = [("plant", "organization")]
        indexes = [
            models.Index(fields=["organization", "is_primary"]),
        ]

    def __str__(self):
        return f"{self.plant_id} → {self.organization_id}{' (primary)' if self.is_primary else ''}"


class BillingTypeMaster(models.Model):
    billing_type = models.CharField(max_length=20, primary_key=True)
    billing_description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["billing_type"]
        verbose_name = "Billing Type"
        verbose_name_plural = "Billing Types"

    def __str__(self):
        return self.billing_type


class SalesTypeMaster(models.Model):
    sales_type = models.CharField(max_length=20, primary_key=True)
    sales_description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["sales_type"]
        verbose_name = "Sales Type"
        verbose_name_plural = "Sales Types"

    def __str__(self):
        return self.sales_type


class UserPlantMapping(models.Model):
    """Restricts which plants a user may operate on.

    Absence of a row does NOT mean unrestricted. A non-admin with no mapping of
    their own inherits their manager's, and one with neither is scoped to
    nothing — see ``rbac.plant_scope``, which explains why: a Manager or Picker
    created through Roles & Access gets a group but no mapping, and treating
    that as "unrestricted" showed every one of them every plant's data.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merilv_plants")
    plant = models.ForeignKey(PlantMaster, on_delete=models.CASCADE, related_name="user_mappings")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "plant")]
        verbose_name = "User Plant Access"
        verbose_name_plural = "User Plant Access"

    def __str__(self):
        return f"{self.user} → {self.plant_id}"


class UserEntityMapping(models.Model):
    """Restricts which entities a user may work in.

    The second axis of the same boundary. ``UserPlantMapping`` says which
    plants' stock a user may touch; this says which business units' consoles
    they may open at all. They are genuinely independent — an entity is a
    company (MHPL, MLSIPL, CARDIO…) with its own parsing rules and its own
    masters, while a plant is a physical site — and a user can legitimately be
    scoped on one, the other, or both.

    Absence of a row means unrestricted, which is the OPPOSITE of
    ``UserPlantMapping`` and is deliberate. Plant scoping fails closed because
    its mappings already exist and an unmapped account there is an oversight.
    Entity scoping is new: no installation has a single row yet, so failing
    closed would empty every picker's screen the moment this deploys, which is
    an outage rather than a boundary. The client's rule is conditional — "if a
    picker or manager IS MAPPED to a particular plant or entity, he should only
    see that one" — and opt-in is exactly that rule.

    ``rbac.entity_scope`` is the one place this asymmetry is decided; nothing
    else should test for rows directly.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merilv_entities",
    )
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="user_mappings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "entity")]
        ordering = ["entity__sort_order", "entity__code"]
        verbose_name = "User Entity Access"
        verbose_name_plural = "User Entity Access"

    def __str__(self):
        return f"{self.user} → {self.entity.code}"


class UserScope(models.Model):
    """One plant-and-entity pair a user may work in, bound together.

    ``UserPlantMapping`` and ``UserEntityMapping`` are two independent lists,
    and independence multiplies. A picker listed under plants Ahmedabad and
    Kolkata and entities MHPL and MLSIPL can reach all FOUR combinations,
    including Ahmedabad-MLSIPL, which may be a business unit that does not
    operate from that site at all.

    The 23 Sep MoM asks for the mapping as one thing — "User → Plant → Entity"
    — and the business team supplies it that way, one row per real posting. So
    a row here is a pair, not two facts filed separately:

        ramesh  Ahmedabad  MHPL     <- he may work this
        ramesh  Kolkata    MLSIPL   <- and this
                                    <- and NOT Ahmedabad-MLSIPL

    Authoritative when present. A user with even one row here is scoped by
    these pairs and the two single-axis tables are ignored for them, because
    two sources of truth for the same boundary is how a gap opens. A user with
    none keeps the existing behaviour exactly, so nothing changes for anybody
    until they are mapped — see ``rbac.pair_scope``.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merilv_scopes",
    )
    plant = models.ForeignKey(
        PlantMaster, on_delete=models.CASCADE, related_name="user_scopes",
    )
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="user_scopes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "plant", "entity")]
        ordering = ["entity__sort_order", "entity__code", "plant_id"]
        verbose_name = "User Plant + Entity Access"
        verbose_name_plural = "User Plant + Entity Access"

    def __str__(self):
        return f"{self.user} → {self.plant_id} / {self.entity.code}"


class PickerManager(models.Model):
    """Which manager a picker reports to.

    The client's hierarchy is Admin → Manager → Picker → Viewer, where a manager
    is mapped to plants and "picker only see the data of mapped Manager". A
    picker therefore has no plant mapping of their own in the normal case: their
    scope is inherited from this link, so moving a picker between managers moves
    their visible data with them and there is one place to change it.

    One manager per picker (``OneToOne`` on the picker side): the requirement is
    singular, and inheriting from two managers would make a picker's scope the
    union of two teams, which is the opposite of what the restriction is for.
    """

    picker = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merilv_manager_link",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merilv_team",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Picker / Manager Mapping"
        verbose_name_plural = "Picker / Manager Mappings"
        indexes = [models.Index(fields=["manager"])]
        constraints = [
            # A picker reporting to themselves would make the scope lookup
            # self-referential and grant them whatever they were meant to inherit.
            models.CheckConstraint(
                condition=~models.Q(picker=models.F("manager")),
                name="merilv_picker_is_not_own_manager",
            ),
        ]

    def __str__(self):
        return f"{self.picker} → {self.manager}"


class MaterialMaster(models.Model):
    """Scan code ↔ SAP material code ↔ GTIN, per entity.

    ``scan_code`` is what is printed on the label; ``material_code`` is what SAP
    knows the item as. They are frequently different, and resolving between them
    is the first step of every scan.
    """

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="materials")
    scan_code = models.CharField(max_length=100, verbose_name="Scan Code")
    material_code = models.CharField(max_length=100, verbose_name="SAP Material Code")
    material_description = models.CharField(max_length=400, blank=True, verbose_name="Description")
    gtin = models.CharField(max_length=20, blank=True, verbose_name="GTIN / EAN")
    material_group = models.CharField(max_length=100, blank=True, verbose_name=MATERIAL_GROUP_LABEL)

    # ── YMM061 material-master columns ─────────────────────────────────────
    # The full column set of SAP's YMM061 extract, which is what the client
    # maintains this master from. The exact header list is taken from the legacy
    # tool's own template row (Cardio-Validator.html), not inferred.
    #
    # `gtin` above stays the canonical barcode-resolution key — every scan
    # resolves through it. `ean_upc` is the YMM061 column of the same number:
    # they are usually equal, but the import must not silently rewrite the key
    # a scan depends on, so it is stored alongside rather than merged.
    material_type = models.CharField(max_length=60, blank=True, verbose_name="Material Type")
    material_type_desc = models.CharField(max_length=200, blank=True, verbose_name="Material Type Descr.")
    old_material_number = models.CharField(max_length=100, blank=True, verbose_name="Old Material Number")
    base_uom = models.CharField(max_length=20, blank=True, verbose_name="Base Unit of Measure")
    basic_material = models.CharField(max_length=100, blank=True, verbose_name="Basic Material")
    division = models.CharField(max_length=20, blank=True, verbose_name="Division")
    division_desc = models.CharField(max_length=200, blank=True, verbose_name="Division Desc.")
    ean_upc = models.CharField(max_length=40, blank=True, verbose_name="EAN / UPC")
    ean_category = models.CharField(max_length=40, blank=True, verbose_name="EAN Category")
    min_shelf_life = models.CharField(max_length=20, blank=True, verbose_name="Min. Rem. Shelf Life")
    total_shelf_life = models.CharField(max_length=20, blank=True, verbose_name="Total Shelf Life")
    item_category_group = models.CharField(max_length=40, blank=True, verbose_name="Gen. Item Cat. Grp")
    weight_unit = models.CharField(max_length=20, blank=True, verbose_name="Weight Unit")
    gross_weight = models.CharField(max_length=40, blank=True, verbose_name="Gross Weight")
    net_weight = models.CharField(max_length=40, blank=True, verbose_name="Net Weight")
    size_dimensions = models.CharField(max_length=200, blank=True, verbose_name="Size / Dimensions")
    valuation_class = models.CharField(max_length=40, blank=True, verbose_name="Valuation Class")
    price_group_desc = models.CharField(max_length=200, blank=True, verbose_name="Description p. Group")
    material_group_desc = models.CharField(max_length=200, blank=True, verbose_name="Material Group Desc.")
    material_group_desc_2 = models.CharField(max_length=200, blank=True, verbose_name="Material Grp Desc. 2")

    # ── Plant / storage / management flags ─────────────────────────────────
    # Requested as "uom, base unit, plant, storage location, batch managed,
    # serial managed". Four of those are here; the other two are already
    # covered or have no data behind them, and both cases are recorded so the
    # next person does not go looking:
    #
    # * "base unit" is ``base_uom`` above — 100% populated on both entities.
    # * "uom" has no YMM061 source. Order Unit, Unit of issue and Unit of
    #   measure are all present as headers and all **entirely empty** — 0 of
    #   29,106 MHPL rows and 0 of 3,206 MLSIPL rows. A column for them would be
    #   dead weight that looks like data, so none was added.
    #
    # ``storage_location`` maps to "Prod. stor. location", which is sparse in
    # practice (31 of 29,106 on MHPL, 0 on MLSIPL) — a material's storage
    # location really lives in StorageMaster, which holds 1,462 rows. It is
    # carried anyway because it was asked for and is occasionally set.
    plant = models.CharField(max_length=20, blank=True, verbose_name="Plant")
    storage_location = models.CharField(max_length=20, blank=True, verbose_name="Storage Location")
    #: Raw SAP serial-number profile ("0001", "F001", or blank). Kept alongside
    #: the derived flag so an operator can see *why* something is serialised.
    serial_profile = models.CharField(max_length=20, blank=True, verbose_name="Serial No. Profile")
    #: SAP writes "X" for batch-managed. Stored as a real boolean so the list
    #: can filter on it; the source letter stays in ``raw_data``.
    batch_managed = models.BooleanField(default=False, verbose_name="Batch Managed")
    #: Derived from the serial profile at import — SAP has no single yes/no
    #: column for it. Note that the sheet's "Serial number" column is a row
    #: counter (1, 2, 3 …), not a serial number, so it is deliberately not used.
    serial_managed = models.BooleanField(default=False, verbose_name="Serial Managed")

    # Product hierarchy, five levels, each a code plus its description. Five
    # separate pairs rather than one concatenated string because the client
    # groups and filters on individual levels — "PH Level's" in the feedback is
    # these.
    ph_level_01 = models.CharField(max_length=40, blank=True, verbose_name="PH Level 01")
    ph_level_01_desc = models.CharField(max_length=200, blank=True, verbose_name="PH Level 01 Desc.")
    ph_level_02 = models.CharField(max_length=40, blank=True, verbose_name="PH Level 02")
    ph_level_02_desc = models.CharField(max_length=200, blank=True, verbose_name="PH Level 02 Desc.")
    ph_level_03 = models.CharField(max_length=40, blank=True, verbose_name="PH Level 03")
    ph_level_03_desc = models.CharField(max_length=200, blank=True, verbose_name="PH Level 03 Desc.")
    ph_level_04 = models.CharField(max_length=40, blank=True, verbose_name="PH Level 04")
    ph_level_04_desc = models.CharField(max_length=200, blank=True, verbose_name="PH Level 04 Desc.")
    ph_level_05 = models.CharField(max_length=40, blank=True, verbose_name="PH Level 05")
    ph_level_05_desc = models.CharField(max_length=200, blank=True, verbose_name="PH Level 05 Desc.")

    raw_data = models.JSONField(default=dict, blank=True, verbose_name="Source Row")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["entity_id", "material_code"]
        unique_together = [("entity", "scan_code")]
        verbose_name = "Material"
        verbose_name_plural = "Material Master"
        indexes = [
            models.Index(fields=["entity", "material_code"]),
            models.Index(fields=["gtin"]),
            models.Index(fields=["scan_code"]),
            # The client groups the material list by product hierarchy and by
            # material group, so both are filtered on directly.
            models.Index(fields=["entity", "material_group"]),
            models.Index(fields=["entity", "ph_level_01"]),
        ]

    def __str__(self):
        return f"{self.material_code} ({self.entity_id})"

    #: YMM061 column header → model field. The single source of truth for the
    #: material master's import, export and template, so a header the client's
    #: extract actually contains cannot silently fail to map.
    YMM061_COLUMNS: tuple[tuple[str, str], ...] = (
        ("Material", "material_code"),
        ("Material Type", "material_type"),
        (MATERIAL_GROUP_LABEL, "material_group"),
        ("Old material number", "old_material_number"),
        ("Base Unit of Measure", "base_uom"),
        ("Basic material", "basic_material"),
        ("Division", "division"),
        ("EAN/UPC", "ean_upc"),
        ("EAN Category", "ean_category"),
        ("Min. Rem. Shelf Life", "min_shelf_life"),
        ("Total shelf life", "total_shelf_life"),
        ("Gen. item cat. grp", "item_category_group"),
        ("Weight unit", "weight_unit"),
        ("Gross Weight", "gross_weight"),
        ("Net Weight", "net_weight"),
        ("Size/dimensions", "size_dimensions"),
        ("Material Description", "material_description"),
        ("Valuation Class", "valuation_class"),
        ("Description p. group", "price_group_desc"),
        ("Material type descr.", "material_type_desc"),
        ("Material Group Desc.", "material_group_desc"),
        ("Material grp desc. 2", "material_group_desc_2"),
        ("Division Desc.", "division_desc"),
        ("Plant", "plant"),
        ("Prod. stor. location", "storage_location"),
        ("Serial no. profile", "serial_profile"),
        ("Batch management", "batch_managed"),
        ("PH Level 01", "ph_level_01"),
        ("PH Level 01 - Desc.", "ph_level_01_desc"),
        ("PH Level 02", "ph_level_02"),
        ("PH Level 02 - Desc.", "ph_level_02_desc"),
        ("PH Level 03", "ph_level_03"),
        ("PH Level 03 - Desc.", "ph_level_03_desc"),
        ("PH Level 04", "ph_level_04"),
        ("PH Level 04 - Desc.", "ph_level_04_desc"),
        ("PH Level 05", "ph_level_05"),
        ("PH Level 05 - Desc.", "ph_level_05_desc"),
    )

    #: YMM061 fields that are booleans in the model but letters in SAP.
    YMM061_BOOLEAN_FIELDS: frozenset[str] = frozenset({"batch_managed"})

    @staticmethod
    def coerce_flag(value) -> bool:
        """SAP's "X" (and its blank) as a real boolean.

        SAP writes an upper-case X for "yes" and leaves the cell empty for
        "no"; some extracts use Y/true/1 instead. Anything else — including the
        string "false" — is no.
        """
        if isinstance(value, bool):
            return value
        return str(value or "").strip().upper() in {"X", "Y", "YES", "TRUE", "1"}

    def apply_derived_flags(self) -> None:
        """Fill the flags SAP has no single column for.

        ``serial_managed`` is one of them: the extract carries a serial-number
        *profile* ("0001", "F001") and a serialization level, not a yes/no. A
        material is serialised when it has a profile. The sheet's "Serial
        number" column is a row counter and must never be read as one.
        """
        self.batch_managed = self.coerce_flag(self.batch_managed)
        self.serial_managed = bool((self.serial_profile or "").strip())

    def save(self, *args, **kwargs):
        # Derived here rather than only in the importer so a row typed in on
        # the admin form, or created by a test, is consistent with an imported
        # one. Callers doing bulk_create must invoke it themselves.
        self.apply_derived_flags()
        return super().save(*args, **kwargs)

    @property
    def product_hierarchy_path(self):
        """The five PH levels as one readable path, blanks dropped."""
        levels = [
            getattr(self, f"ph_level_0{index}") for index in range(1, 6)
        ]
        return " › ".join(level for level in levels if level)


class CustomerMaster(models.Model):
    """Ship-to / sold-to party, with the shipping metadata outbound needs."""

    customer_code = models.CharField(max_length=50, unique=True, verbose_name="Customer Code")
    customer_name = models.CharField(max_length=300, blank=True, verbose_name="Customer Name")
    entity = models.ForeignKey(
        Entity, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers",
    )
    company_code = models.CharField(max_length=20, blank=True)
    plant = models.CharField(max_length=200, blank=True)
    courier_partner = models.CharField(max_length=150, blank=True)
    courier_number = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    pincode = models.CharField(max_length=20, blank=True)
    gstin = models.CharField(max_length=30, blank=True, verbose_name="GSTIN")
    email = models.EmailField(blank=True)
    mobile = models.CharField(max_length=40, blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    is_blocked = models.BooleanField(default=False, verbose_name="Blocked")
    blocked_reason = models.CharField(max_length=300, blank=True)
    raw_data = models.JSONField(default=dict, blank=True, verbose_name="Source Row")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer_code"]
        verbose_name = "Customer"
        verbose_name_plural = "Customer Master"
        indexes = [
            models.Index(fields=["customer_code"]),
            models.Index(fields=["customer_name"]),
        ]

    def __str__(self):
        return f"{self.customer_code} — {self.customer_name}"


class CourierMaster(models.Model):
    courier_name = models.CharField(max_length=150)
    #: The business unit this row belongs to.
    #:
    #: Added because the sheet had nowhere else to put it, and people put it in
    #: ``plant``. The column ended up holding three different kinds of value at
    #: once — real plant codes (WGJ7, 3044), entity codes (MHPL, MLSIPL) and the
    #: word "Common" — and a row tagged MHPL can never match a customer, whose
    #: plant really is WGJ7. That is why a carrier with 152 customers at a plant
    #: showed a fraction of them: the two masters were speaking different
    #: vocabularies through one column.
    #:
    #: Nullable, because existing rows have no entity until somebody re-imports
    #: them, and a courier that does not name one still matches on plant alone
    #: exactly as it did before.
    entity = models.ForeignKey(
        "Entity", null=True, blank=True, on_delete=models.CASCADE,
        related_name="couriers", verbose_name="Entity",
    )
    #: The PHYSICAL site — WGJ7, 3044 — and only that. See ``entity`` above for
    #: what used to end up in here by mistake.
    plant = models.CharField(max_length=20, blank=True)
    #: The partner's own code — 24BD, 07BY, 24ART — despite the name. This is
    #: what the invoice sheet's third column carries: its header reads CURR,
    #: which is why it shipped a hardcoded "INR" for so long and was then
    #: briefly read as the customer code, but the column has always been the
    #: courier partner code rather than a currency.
    #:
    #: This is the DEFAULT, not the authority. CustomerMaster and
    #: CustomerCourierMapping each carry a courier_number of their own, and
    #: where they do, theirs wins: the same courier legitimately has different
    #: partner codes per customer — Arts Express is 24AES for 102 customers and
    #: 24ART for 189, By Hand is BH for 37 and HAND for 26. This master holds
    #: one row per courier and cannot express that, so overriding a customer's
    #: code with it flattened half of each customer base onto the wrong value.
    #: It answers for a delivery resolved by PLANT default, which has no
    #: customer to prefer, and for a customer row that names a courier without
    #: repeating its number. A separate ``courier_code`` field was added for
    #: this in 0007 and removed again in 0009: it duplicated what is already
    #: here, and because nothing ever populated it the export's name-fallback
    #: was all that ever reached SAP.
    courier_number = models.CharField(max_length=100, blank=True)
    vehicle_number = models.CharField(max_length=60, blank=True)
    # Free text rather than choices: the legacy tool hardcoded "R" in its
    # dispatch form, but the client's own courier partners use their own
    # vocabulary and a fixed list would reject valid entries on import.
    vehicle_type = models.CharField(max_length=60, blank=True, verbose_name="Vehicle Type")
    #: How this carrier moves stock — Road, Air, Rail or Sea. The Finish
    #: Delivery form used to default every dispatch to Road and leave the
    #: operator to correct it, because nothing in the masters said what a
    #: given courier actually is. Free text for the same reason
    #: ``vehicle_type`` is: the client's partners use their own vocabulary and
    #: a fixed choice list would reject valid rows on import. The form offers
    #: the four standard values and accepts whatever the master holds.
    mode_of_transit = models.CharField(max_length=20, blank=True, verbose_name="Mode of Transit")
    gstin = models.CharField(max_length=20, blank=True, verbose_name="GSTIN")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["courier_name"]
        verbose_name = "Courier"
        verbose_name_plural = "Courier Master"
        indexes = [models.Index(fields=["courier_name"])]

    def __str__(self):
        return self.courier_name


class CustomerCourierMapping(models.Model):
    """Plant-specific courier override for a customer.

    Checked before the customer's default courier when finishing a delivery.
    """

    customer_code = models.CharField(max_length=50)
    entity = models.ForeignKey(
        Entity, null=True, blank=True, on_delete=models.CASCADE, related_name="courier_mappings",
    )
    plant = models.CharField(max_length=20, blank=True)
    courier_name = models.CharField(max_length=150, blank=True)
    courier_number = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer_code"]
        unique_together = [("customer_code", "entity", "plant")]
        verbose_name = "Customer Courier Mapping"
        verbose_name_plural = "Customer Courier Mappings"

    def __str__(self):
        return f"{self.customer_code} → {self.courier_name}"


class BoxProfile(models.Model):
    """Packaging carton: dimensions, volumetric weight and unit capacity."""

    code = models.CharField(max_length=40, unique=True, verbose_name="Box Code")
    sku_name = models.CharField(max_length=200, blank=True, verbose_name="SKU Name")
    length_cm = models.DecimalField(max_digits=7, decimal_places=2, default=0, verbose_name="Length (cm)")
    width_cm = models.DecimalField(max_digits=7, decimal_places=2, default=0, verbose_name="Width (cm)")
    height_cm = models.DecimalField(max_digits=7, decimal_places=2, default=0, verbose_name="Height (cm)")
    divisor = models.PositiveIntegerField(default=5000, verbose_name="Volumetric Divisor")
    max_qty = models.PositiveIntegerField(
        default=0,
        verbose_name="Max Units",
        help_text="0 means unlimited.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = "Box Profile"
        verbose_name_plural = "Box Profiles"

    def __str__(self):
        return self.code

    @property
    def volumetric_weight(self):
        """Dimensional weight in kg, rounded to two decimals."""
        if not self.divisor:
            return 0
        volume = self.length_cm * self.width_cm * self.height_cm
        return round(float(volume) / self.divisor, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Outbound scanning
# ─────────────────────────────────────────────────────────────────────────────


class DeliveryDump(models.Model):
    """One uploaded YVL06 delivery extract."""

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="delivery_dumps")
    source_file = models.FileField(upload_to="merilv/delivery_dumps/%Y/%m/", verbose_name="YVL06 File")
    original_filename = models.CharField(max_length=300, blank=True)
    row_count = models.PositiveIntegerField(default=0, verbose_name="Lines Parsed")
    skipped_count = models.PositiveIntegerField(default=0, verbose_name="Rows Skipped")
    warnings = models.JSONField(default=list, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_delivery_dumps",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Delivery Dump"
        verbose_name_plural = "Delivery Dumps"

    def __str__(self):
        return f"{self.original_filename or self.source_file.name} ({self.row_count} lines)"


class DeliveryLine(models.Model):
    """A single delivery requirement: this much of this material, this batch."""

    dump = models.ForeignKey(DeliveryDump, on_delete=models.CASCADE, related_name="lines")
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="delivery_lines")

    delivery_no = models.CharField(max_length=50, verbose_name="Delivery No.")
    item_no = models.CharField(max_length=20, blank=True, verbose_name="Item")
    material = models.CharField(max_length=100)
    batch = models.CharField(max_length=100, blank=True)
    original_batch = models.CharField(max_length=100, blank=True)
    batch_overridden = models.BooleanField(default=False)

    plant = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=20, blank=True)
    company_code = models.CharField(max_length=20, blank=True)
    ean = models.CharField(max_length=20, blank=True, verbose_name="EAN")

    required_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0, verbose_name="Required Qty")
    scanned_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0, verbose_name="Scanned Qty")

    delivery_date = models.DateField(null=True, blank=True)
    customer_name = models.CharField(max_length=300, blank=True)
    bill_to_code = models.CharField(max_length=50, blank=True)
    bill_to_name = models.CharField(max_length=300, blank=True)
    ship_to_code = models.CharField(max_length=50, blank=True)
    ship_to_name = models.CharField(max_length=300, blank=True)
    ref_document = models.CharField(max_length=100, blank=True)
    customer_po_no = models.CharField(max_length=100, blank=True)
    port_of_loading = models.CharField(max_length=150, blank=True)
    discharging_place = models.CharField(max_length=150, blank=True)
    final_destination = models.CharField(max_length=150, blank=True)
    country_of_origin = models.CharField(max_length=100, blank=True)

    #: Item numbers merged into this line when the entity aggregates repeated
    #: rows. Only ever this line's own item — the merge key includes it.
    source_items = models.JSONField(default=list, blank=True)
    #: Ordering within the source file — the legacy matcher is order-sensitive.
    sequence = models.PositiveIntegerField(default=0)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["sequence", "id"]
        verbose_name = "Delivery Line"
        verbose_name_plural = "Delivery Lines"
        indexes = [
            models.Index(fields=["dump", "material", "batch"]),
            models.Index(fields=["delivery_no"]),
            models.Index(fields=["bill_to_code"]),
        ]

    def __str__(self):
        return f"{self.delivery_no}/{self.item_no} {self.material} {self.batch}"

    @property
    def pending_qty(self):
        return max(self.required_qty - self.scanned_qty, 0)

    @property
    def is_complete(self):
        return self.scanned_qty >= self.required_qty


class OutboundSession(models.Model):
    """A picker's working session against one delivery dump."""

    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_ABANDONED = "abandoned"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_ABANDONED, "Abandoned"),
    ]

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="outbound_sessions")
    dump = models.ForeignKey(
        DeliveryDump, null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions",
    )
    name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    #: Focus locks scanning to one delivery, or to all deliveries of one customer.
    selected_box = models.CharField(max_length=40, blank=True)

    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_outbound_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Outbound Session"
        verbose_name_plural = "Outbound Sessions"

    def __str__(self):
        return self.name or f"Session #{self.pk}"


class OperatorFocus(models.Model):
    """Where ONE operator is standing in one console.

    Focus used to be a column on the record itself —
    ``OutboundSession.focus_delivery_no`` and ``focus_key`` on the ZVA05 dump,
    the BT scan session and the inbound receipt. One value for the record meant
    one value for everybody looking at it, and the consoles poll state every two
    seconds: a picker clicking a delivery took over every other screen open on
    that record, unasked. Reported from the floor as two accounts' sessions
    merging.

    Sharing the RECORD is deliberate — a supervisor uploads a dump and the floor
    picks it (see views.scope_outbound_sessions). What is not shared is where
    each picker is standing in it.

    And it is not only a display bug. Focus is the picking lock: nothing may be
    scanned until the operator has named a target (``views._focus_gate``), and
    every scan resolves against that target. Two pickers on one focus means one
    of them is silently scanning stock into the delivery the other chose.

    Stored rather than kept in the HTTP session because it outlives a browser —
    a picker moving from a tablet to a bench workstation keeps their place — and
    because the live-operators board reads it to say what each person is working
    on (``merilv.monitoring``), which a cookie could not answer.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merilv_focus",
    )
    #: Which console and record this belongs to — ``"outbound:41"``. A string
    #: rather than four nullable foreign keys: the four consoles never share a
    #: row, and one table keeps the lookup and the cleanup in one place.
    scope = models.CharField(max_length=60, db_index=True)
    #: The focused target: a delivery number, or a console's own focus key.
    value = models.CharField(max_length=200, blank=True)
    #: Outbound's second focus mode. Blank in every other console.
    customer_code = models.CharField(max_length=50, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Operator Focus"
        verbose_name_plural = "Operator Focus"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "scope"], name="merilv_one_focus_per_user_per_scope",
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.scope} → {self.value or '(none)'}"


class OutboundScan(models.Model):
    """One accepted scan, assigned to a delivery line."""

    session = models.ForeignKey(OutboundSession, on_delete=models.CASCADE, related_name="scans")
    line = models.ForeignKey(
        DeliveryLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="scans",
    )

    #: ``BATCH||SERIAL`` for serialised items, or a synthetic ``[BATCH] … (#n)`` id.
    serial_identity = models.CharField(max_length=200, verbose_name="Serial Identity")
    serial_only = models.CharField(max_length=100, blank=True)
    material = models.CharField(max_length=100)
    batch = models.CharField(max_length=100, blank=True)
    mfg_code = models.CharField(max_length=10, blank=True)
    expiry_code = models.CharField(max_length=10, blank=True)
    box = models.CharField(max_length=40, blank=True)
    raw_scan = models.TextField(blank=True)

    #: Idempotency key minted by the browser before the scan is first sent.
    #: The offline queue reuses it when replaying a scan whose response was
    #: lost, so the server can recognise the retry as the same scan instead of
    #: booking it a second time. Blank for scans from any client that does not
    #: send one — including every row written before this field existed — which
    #: is why the uniqueness below is conditional rather than plain.
    client_ref = models.CharField(max_length=64, blank=True, db_index=True)

    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_outbound_scans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Outbound Scan"
        verbose_name_plural = "Outbound Scans"
        indexes = [
            models.Index(fields=["session", "serial_identity"]),
            models.Index(fields=["material", "batch"]),
        ]
        constraints = [
            # Belt to the view's braces: even if two replays race past the
            # view's lookup, the database refuses the second insert.
            models.UniqueConstraint(
                fields=["session", "client_ref"],
                condition=models.Q(client_ref__gt=""),
                name="merilv_outbound_scan_client_ref_once",
            ),
            # One serial, one unit. The engine already refuses a duplicate
            # serial and the edit endpoint re-checks under a row lock, but
            # neither is a boundary: two guns racing on the same session, or a
            # correction applied twice, could each pass their check and both
            # write. Now that every accepted scan is given a serial — a
            # running BATCH||001 where the label carries none — the pair is
            # genuinely unique and can be enforced where it cannot be raced.
            models.UniqueConstraint(
                fields=["session", "serial_identity"],
                name="merilv_outbound_scan_serial_once",
            ),
        ]

    def __str__(self):
        return self.serial_identity


class ScanLog(models.Model):
    """Every scan attempt, accepted or rejected.

    FR5 requires explicit reason codes rather than a bare failure, so the reason
    is stored as a machine-readable code alongside the operator-facing message.
    """

    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_WARNING = "warning"
    STATUS_DUPLICATE = "duplicate"
    STATUS_CHOICES = [
        (STATUS_SUCCESS, "Success"),
        (STATUS_ERROR, "Error"),
        (STATUS_WARNING, "Warning"),
        (STATUS_DUPLICATE, "Duplicate"),
    ]

    session = models.ForeignKey(
        OutboundSession, null=True, blank=True, on_delete=models.CASCADE, related_name="logs",
    )
    #: Set instead of ``session`` when the attempt came from the ZVA05 console.
    #: One audit table serves both consoles: the shape (raw scan + status +
    #: reason code + parsed fields) is identical, and keeping them together means
    #: "every scan attempt anywhere" stays a single query.
    zva05_dump = models.ForeignKey(
        "Zva05Dump", null=True, blank=True, on_delete=models.CASCADE, related_name="logs",
    )
    #: Set instead of the two above when the attempt came from the Branch
    #: Transfer Receiving console.
    receipt = models.ForeignKey(
        "BranchTransferReceipt", null=True, blank=True,
        on_delete=models.CASCADE, related_name="logs",
    )
    #: Set instead of the three above when the attempt came from the BT
    #: Scanning console (the carton-tracked floor console for a transfer plan).
    bt_scan_session = models.ForeignKey(
        "BtScanSession", null=True, blank=True,
        on_delete=models.CASCADE, related_name="logs",
    )
    raw_scan = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    reason_code = models.CharField(max_length=50, blank=True, verbose_name="Reason Code")
    message = models.CharField(max_length=400, blank=True)
    #: Which delivery the attempt was made against — the line it landed on when
    #: it was accepted, otherwise the delivery the session was focused on at the
    #: time. Recorded so the console can show one delivery's scan history on its
    #: own: an accepted scan can be traced through ``OutboundScan``, but a
    #: *rejection* belongs to no line, and without this there is nothing tying it
    #: to the delivery the operator was picking when it happened.
    delivery_no = models.CharField(max_length=50, blank=True, verbose_name="Delivery")
    parsed = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Scan Log"
        verbose_name_plural = "Scan Logs"
        indexes = [
            models.Index(fields=["session", "-created_at"]),
            models.Index(fields=["zva05_dump", "-created_at"]),
            models.Index(fields=["receipt", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.status}: {self.reason_code}"


class FinishedDelivery(models.Model):
    """A completed, exported delivery with its courier and packaging details."""

    #: The legacy tool split this across two actors: a picker finished scanning
    #: and the row appeared immediately as "pending courier", then a manager
    #: filled the docket, vehicle and box counts later. Restored here because
    #: the client's Delivery History screen is built around that split — the
    #: "Pending Courier" tab badge and its Fill popup have nothing to act on
    #: without it.
    STATUS_PENDING_COURIER = "pending_courier"
    STATUS_COMPLETE = "complete"
    STATUS_CHOICES = [
        (STATUS_PENDING_COURIER, "Pending Courier"),
        (STATUS_COMPLETE, "Complete"),
    ]

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="finished_deliveries")
    session = models.ForeignKey(
        OutboundSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="finished",
    )
    delivery_no = models.CharField(max_length=50)
    customer_code = models.CharField(max_length=50, blank=True)
    customer_name = models.CharField(max_length=300, blank=True)
    # Denormalised from the delivery's first line. Needed because this row
    # outlives the session it came from, and because plant-scoped users have to
    # be able to filter their own deliveries without a join through the dump.
    plant = models.CharField(max_length=20, blank=True, db_index=True)

    courier_partner = models.CharField(max_length=150, blank=True)
    courier_number = models.CharField(max_length=100, blank=True)
    vehicle_number = models.CharField(max_length=60, blank=True)
    docket_no = models.CharField(max_length=100, blank=True)
    mode_of_transit = models.CharField(max_length=60, blank=True)
    vehicle_type = models.CharField(max_length=60, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    box_count = models.PositiveIntegerField(default=0)
    boxes_used = models.CharField(max_length=200, blank=True, help_text="For example: A:2,B:1")

    # Defaults to complete so every row already in the table — all of which were
    # written by the current one-step finish, courier details included — keeps
    # its meaning after this migration rather than silently becoming pending.
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_COMPLETE, db_index=True,
    )

    is_partial = models.BooleanField(default=False, verbose_name="Exported with shortfall")
    line_count = models.PositiveIntegerField(default=0)
    scanned_count = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)

    picker = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_finished_deliveries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Finished Delivery"
        verbose_name_plural = "Finished Deliveries"
        indexes = [
            models.Index(fields=["delivery_no"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["picker", "-created_at"]),
            # Drives the "Pending Courier" tab badge, which counts on every load.
            models.Index(fields=["status", "-created_at"]),
        ]
        constraints = [
            # One finish per delivery per session. The view checks first and
            # folds a second submission's paperwork into the existing row, but
            # two requests can pass that check at the same moment — an operator
            # double-clicking Finish is exactly the shape that produces it — so
            # the database is the actual guarantee. Without it, delivery
            # 81090058 ended up with two rows: the pick at 05:42 stranded as
            # "pending courier" and a second at 07:34 holding the paperwork,
            # double-counting the delivery in every KPI.
            models.UniqueConstraint(
                fields=["session", "delivery_no"], name="merilv_one_finish_per_delivery",
            ),
        ]

    def __str__(self):
        return self.delivery_no

    @property
    def is_pending_courier(self):
        return self.status == self.STATUS_PENDING_COURIER

    @property
    def box_breakdown(self):
        """Decode ``boxes_used`` ("A:2,B:1") into the six per-type columns.

        Derived rather than stored: ``boxes_used`` is already the single writer
        of this data, and six more columns would be a second source of truth to
        keep in step. "Others" folds E and F together, matching the legacy
        table's own "Others - E & F" column.
        """
        counts = dict.fromkeys("ABCDEF", 0)
        for part in (self.boxes_used or "").split(","):
            letter, _, quantity = part.partition(":")
            letter = letter.strip().upper()
            if letter in counts:
                try:
                    counts[letter] = int(quantity.strip() or 0)
                except ValueError:
                    counts[letter] = 0
        counts["others"] = counts["E"] + counts["F"]
        return counts


class DispatchedSerial(models.Model):
    """A serial that has left the building, and may never be assigned again.

    ``OutboundScan`` already refuses a serial twice inside one session, but a
    session is scoped to one uploaded dump — so the same serial could be
    assigned again on the next upload, and nothing noticed. A serial identifies
    one physical unit; once it has been dispatched on a finished delivery,
    re-using it means two deliveries claim the same carton, and the SAP upload
    carries the same serial twice.

    Written when a delivery is finished, deliberately not when it is scanned: a
    scan can still be undone or deleted by an admin while the delivery is open,
    and burning the serial before dispatch would strand it. Rows are kept even
    if the delivery record they came from is removed (``SET_NULL``) — the
    guarantee asked for is permanence, so a deleted finish must not quietly
    release a serial back into circulation.

    Deliberately unique on ``serial`` alone rather than per entity: a serial is
    ``BATCH || NUMBER`` and a manufacturing batch is unique across Meril, so a
    collision between two entities would be a genuine data problem rather than
    two legitimate units. ``batch`` is indexed because that is how the scanner
    loads the guard — only serials sharing a batch with the dump on screen can
    possibly collide, which keeps the lookup bounded as this table grows.
    """

    serial = models.CharField(
        max_length=200, unique=True, verbose_name="Assigned Serial",
        help_text="BATCH + serial, exactly as exported in the SAP Serial Format sheet.",
    )
    entity = models.ForeignKey(
        Entity, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="dispatched_serials",
    )
    finished_delivery = models.ForeignKey(
        FinishedDelivery, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="dispatched_serials",
    )
    #: Denormalised so the rejection message can name where the serial went
    #: without a join, and so it survives the delivery record being removed.
    delivery_no = models.CharField(max_length=50, db_index=True)
    material = models.CharField(max_length=100, blank=True)
    batch = models.CharField(max_length=100, blank=True, db_index=True)
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_dispatched_serials",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Dispatched Serial"
        verbose_name_plural = "Dispatched Serials"
        indexes = [
            models.Index(fields=["batch", "serial"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.serial} → {self.delivery_no}"


# ─────────────────────────────────────────────────────────────────────────────
# Stock validation
# ─────────────────────────────────────────────────────────────────────────────


class ValidationRun(models.Model):
    """One reconciliation of a physical scan file against a SAP stock dump."""

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="validation_runs")
    name = models.CharField(max_length=200, blank=True)
    system_file = models.FileField(upload_to="merilv/system_dumps/%Y/%m/", verbose_name="SAP Stock Dump")
    scan_file = models.FileField(
        upload_to="merilv/scan_files/%Y/%m/", blank=True, null=True, verbose_name="Scan File",
    )
    system_filename = models.CharField(max_length=300, blank=True)
    scan_filename = models.CharField(max_length=300, blank=True)

    # ── Reconciliation KPIs ────────────────────────────────────────────
    total_scanned = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    total_system = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    matched_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    batch_matched_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    material_matched_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    variance_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    reconciliation_pct = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    settings_snapshot = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_validation_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Validation Run"
        verbose_name_plural = "Validation Runs"

    def __str__(self):
        return self.name or f"Validation #{self.pk}"


class ValidationRow(models.Model):
    """One scanned line and the verdict the matching engine reached for it."""

    STATUS_MATCHED = "matched"
    STATUS_PARTIAL = "partial"
    STATUS_WARNING = "warning"
    STATUS_VARIANCE = "variance"
    STATUS_VARIANCE_PARTIAL = "variance_partial"
    STATUS_BATCH_MISMATCH = "batch_mismatch"
    STATUS_STOCK_EXHAUSTED = "stock_exhausted"
    STATUS_MASTER_MISSING = "master_missing"
    STATUS_DUPE_SCAN = "dupe_scan"
    STATUS_OVER_SCAN = "over_scan"
    STATUS_PLANT_MISMATCH = "plant_mismatch"
    STATUS_EXPIRY_MISMATCH = "expiry_mismatch"
    STATUS_INCOMPLETE = "incomplete"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_MATCHED, "Matched"),
        (STATUS_PARTIAL, "Batch Match / Serial Diff"),
        (STATUS_WARNING, "Material OK / Batch Diff"),
        (STATUS_VARIANCE, "Not found in SAP"),
        (STATUS_VARIANCE_PARTIAL, "Variance (Partial)"),
        (STATUS_BATCH_MISMATCH, "Batch Mismatch"),
        (STATUS_STOCK_EXHAUSTED, "Stock Exhausted"),
        (STATUS_MASTER_MISSING, "Master Missing"),
        (STATUS_DUPE_SCAN, "Duplicate Scan"),
        (STATUS_OVER_SCAN, "Over Scan"),
        (STATUS_PLANT_MISMATCH, "Plant Mismatch"),
        (STATUS_EXPIRY_MISMATCH, "Expiry Mismatch"),
        (STATUS_INCOMPLETE, "Incomplete Barcode"),
        (STATUS_ERROR, "Parse Error"),
    ]

    STOCK_GOOD = "good"
    STOCK_DAMAGE = "damage"
    STOCK_EXPIRED = "expired"
    STOCK_NEAR_EXPIRY = "near_expiry"
    STOCK_SHORT_EXPIRY = "short_expiry"
    STOCK_NO_EXPIRY = "no_expiry"
    STOCK_CHOICES = [
        (STOCK_GOOD, "Good Stock"),
        (STOCK_DAMAGE, "Damage"),
        (STOCK_EXPIRED, "Expired"),
        (STOCK_NEAR_EXPIRY, "Near Expiry"),
        (STOCK_SHORT_EXPIRY, "Short Expiry"),
        (STOCK_NO_EXPIRY, "No Expiry"),
    ]

    run = models.ForeignKey(ValidationRun, on_delete=models.CASCADE, related_name="rows")

    # ── As scanned ─────────────────────────────────────────────────────
    raw_scan = models.TextField(blank=True)
    material = models.CharField(max_length=100, blank=True)
    physical_batch = models.CharField(max_length=100, blank=True)
    physical_serial = models.CharField(max_length=100, blank=True)
    scan_qty = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    consumed_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    condition = models.CharField(max_length=60, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    # ── As found in SAP ────────────────────────────────────────────────
    system_batch = models.CharField(max_length=100, blank=True)
    system_serial = models.CharField(max_length=100, blank=True)
    system_plant = models.CharField(max_length=20, blank=True)
    system_location = models.CharField(max_length=20, blank=True)
    system_expiry = models.CharField(max_length=40, blank=True)
    available_batches = models.JSONField(default=list, blank=True)
    available_serials = models.JSONField(default=list, blank=True)
    material_description = models.CharField(max_length=400, blank=True)
    #: Stamped from MaterialMaster at run time, not looked up when the screen
    #: renders (#17). A run is evidence of what the master said when the count
    #: was taken - re-resolving it later would silently rewrite the history of
    #: a completed validation whenever the master changed.
    material_group = models.CharField(max_length=100, blank=True, verbose_name=MATERIAL_GROUP_LABEL)
    ph_level = models.CharField(max_length=40, blank=True, verbose_name="PH Level")
    customer_code = models.CharField(max_length=50, blank=True)
    customer_name = models.CharField(max_length=300, blank=True)
    billing_document = models.CharField(max_length=60, blank=True)
    billing_date = models.CharField(max_length=40, blank=True)

    # ── Verdict ────────────────────────────────────────────────────────
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)
    status_priority = models.SmallIntegerField(default=0)
    match_type = models.CharField(max_length=30, blank=True)
    stock_status = models.CharField(max_length=20, choices=STOCK_CHOICES, blank=True)
    target_location = models.CharField(max_length=20, blank=True)
    detail = models.TextField(blank=True)
    flags = models.JSONField(default=list, blank=True)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]
        verbose_name = "Validation Row"
        verbose_name_plural = "Validation Rows"
        indexes = [
            models.Index(fields=["run", "status"]),
            models.Index(fields=["material"]),
        ]

    def __str__(self):
        return f"{self.material} — {self.get_status_display()}"


# ─────────────────────────────────────────────────────────────────────────────
# Barcode extractor — live scan & live validate
# ─────────────────────────────────────────────────────────────────────────────


class ScanSession(models.Model):
    """A named live-scan or live-validate session, kept for audit evidence."""

    MODE_EXTRACT = "extract"
    MODE_LIVE_SCAN = "live_scan"
    MODE_LIVE_VALIDATE = "live_validate"
    MODE_CHOICES = [
        (MODE_EXTRACT, "Extractor"),
        (MODE_LIVE_SCAN, "Live Scan"),
        (MODE_LIVE_VALIDATE, "Live Validate"),
    ]

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="scan_sessions")
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_LIVE_SCAN)
    name = models.CharField(max_length=200, blank=True, verbose_name="Session Name")
    condition = models.CharField(max_length=60, blank=True)
    box_no = models.CharField(max_length=40, blank=True)
    dump_file = models.FileField(
        upload_to="merilv/live_validate/%Y/%m/", blank=True, null=True, verbose_name="System Dump",
    )
    #: The dump parsed once, at upload, reduced to the four fields live-validate
    #: actually reads. Without it the console re-opened and re-parsed the whole
    #: spreadsheet on *every single scan* — a 5,000-carton session parsed its
    #: dump 5,000 times, in the latency-critical path between gun and beep.
    #: The file itself is kept as the audit artefact; this is the working copy.
    dump_snapshot = models.JSONField(default=list, blank=True)
    column_map = models.JSONField(default=dict, blank=True)
    is_finished = models.BooleanField(default=False)

    #: Denormalised counters so history lists don't need to aggregate.
    total_count = models.PositiveIntegerField(default=0)
    found_count = models.PositiveIntegerField(default=0)
    not_found_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_scan_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Scan Session"
        verbose_name_plural = "Scan Sessions"

    def __str__(self):
        return self.name or f"{self.get_mode_display()} #{self.pk}"


class ScanRecord(models.Model):
    """One barcode captured within a :class:`ScanSession`."""

    RESULT_FOUND = "found"
    RESULT_NOT_FOUND = "not_found"
    RESULT_DUPLICATE = "duplicate"
    RESULT_MATCH = "match"
    RESULT_MISMATCH = "mismatch"
    RESULT_NOT_IN_DUMP = "not_in_dump"
    RESULT_EXCESS = "excess"
    RESULT_CHOICES = [
        (RESULT_FOUND, "Found"),
        (RESULT_NOT_FOUND, "Not Found"),
        (RESULT_DUPLICATE, "Duplicate"),
        (RESULT_MATCH, "Match"),
        (RESULT_MISMATCH, "Mismatch"),
        (RESULT_NOT_IN_DUMP, "Not in Dump"),
        (RESULT_EXCESS, "Excess"),
    ]

    session = models.ForeignKey(ScanSession, on_delete=models.CASCADE, related_name="records")
    raw_scan = models.TextField(blank=True)
    material = models.CharField(max_length=100, blank=True)
    scan_code = models.CharField(max_length=100, blank=True)
    batch = models.CharField(max_length=100, blank=True)
    serial = models.CharField(max_length=100, blank=True)
    mfg_code = models.CharField(max_length=10, blank=True)
    expiry_code = models.CharField(max_length=10, blank=True)
    description = models.CharField(max_length=400, blank=True)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)
    comment = models.CharField(max_length=300, blank=True)
    #: Stamped from the session's active condition toggle at scan time, and
    #: independently correctable afterward — the legacy tool's per-row
    #: Good/Damage dropdown (Row 3.5). Free text like ScanSession.condition,
    #: not a strict choice field, since the old tool never enforced one either.
    condition = models.CharField(max_length=60, blank=True)
    #: Which physical carton this unit went into. Stamped from the session's
    #: active box at scan time; the operator can switch boxes mid-session
    #: without opening a new one (Row 3.4's per-box count needs this).
    box_no = models.CharField(max_length=40, blank=True)
    parsed = models.JSONField(default=dict, blank=True)
    #: Generated by the client before its first attempt and reused on any
    #: offline-queue replay, so a scan the server accepted but whose response
    #: never made it back is recognised as the same scan rather than counted
    #: twice. Mirrors OutboundScan.client_ref — the extractor's sessions could
    #: not safely use the offline queue without it. Blank for any client that
    #: does not send one, including every row written before this existed,
    #: which is why the uniqueness below is conditional rather than plain.
    client_ref = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Scan Record"
        verbose_name_plural = "Scan Records"
        indexes = [models.Index(fields=["session", "result"])]
        constraints = [
            # Belt to the view's braces: even if two replays race past the
            # view's lookup, the database refuses the second insert.
            models.UniqueConstraint(
                fields=["session", "client_ref"],
                condition=models.Q(client_ref__gt=""),
                name="merilv_scan_record_client_ref_once",
            ),
        ]

    def __str__(self):
        return f"{self.material or self.raw_scan[:24]} — {self.result}"


# ─────────────────────────────────────────────────────────────────────────────
# Branch transfer
# ─────────────────────────────────────────────────────────────────────────────


class BranchTransferPlan(models.Model):
    """An uploaded branch-transfer plan and the MB52 stock it is picked against."""

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="bt_plans")
    name = models.CharField(max_length=200, blank=True)
    plan_file = models.FileField(upload_to="merilv/bt_plans/%Y/%m/", verbose_name="BT Plan")
    stock_file = models.FileField(
        upload_to="merilv/bt_stock/%Y/%m/", blank=True, null=True, verbose_name="MB52 Stock Dump",
    )
    is_finished = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_bt_plans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Branch Transfer Plan"
        verbose_name_plural = "Branch Transfer Plans"

    def __str__(self):
        return self.name or f"BT Plan #{self.pk}"


class BranchTransferLine(models.Model):
    """One SKU to move between nodes, and how much of it has been picked."""

    plan = models.ForeignKey(BranchTransferPlan, on_delete=models.CASCADE, related_name="lines")
    unique_number = models.CharField(max_length=80, blank=True)
    supply_node_code = models.CharField(max_length=40, blank=True)
    node_code = models.CharField(max_length=40, blank=True, verbose_name="Sender Plant")
    storage_location = models.CharField(max_length=40, blank=True)
    receiving_parent_node_code = models.CharField(max_length=40, blank=True)
    receiving_node_code = models.CharField(max_length=40, blank=True, verbose_name="Receiver Plant")
    sku_code = models.CharField(max_length=100, verbose_name="SKU Code")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    picked_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    #: Booked in at the destination plant. Accumulates across every
    #: BranchTransferReceipt raised against this plan (goods can arrive in more
    #: than one vehicle load).
    received_qty = models.DecimalField(
        max_digits=12, decimal_places=3, default=0, verbose_name="Received Qty",
    )
    intransit_color_zone = models.CharField(max_length=40, blank=True)
    mode = models.CharField(max_length=40, blank=True)
    sequence = models.PositiveIntegerField(default=0)
    raw_data = models.JSONField(default=dict, blank=True)

    #: The batch a picker should physically draw from, worked out from the
    #: uploaded MB52 stock dump by ``services.transfers.assign_transfer_batches``
    #: (location-priority allocation — see ``bt-scanner.js:22,866-899`` in the
    #: legacy tool). Advisory only: the scan engine still matches on material
    #: alone, since whatever batch physically turns up is the one that ships.
    assigned_batch = models.CharField(max_length=100, blank=True)
    assigned_location = models.CharField(max_length=80, blank=True)
    #: Set once an operator has typed a batch in by hand, so a later
    #: auto-assign run does not silently overwrite their choice.
    batch_overridden = models.BooleanField(default=False)

    class Meta:
        ordering = ["sequence", "id"]
        verbose_name = "Branch Transfer Line"
        verbose_name_plural = "Branch Transfer Lines"
        indexes = [models.Index(fields=["plan", "sku_code"])]

    def __str__(self):
        return f"{self.sku_code} × {self.quantity}"

    @property
    def pending_qty(self):
        return max(self.quantity - self.picked_qty, 0)

    @property
    def pending_receipt_qty(self):
        """Still in transit: dispatched by the sender, not yet booked in.

        Measured against ``picked_qty``, not ``quantity`` — the receiving plant
        can only ever be sent what was actually picked, so a plan line for 5
        that shipped 3 leaves the receiver expecting 3. Comparing against the
        plan instead would report a permanent phantom shortfall of 2.
        """
        return max(self.picked_qty - self.received_qty, 0)

    @property
    def transit_shortfall(self):
        """Dispatched but never received — the lost-in-transit figure."""
        return max(self.picked_qty - self.received_qty, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Branch transfer receiving (inbound side)
# ─────────────────────────────────────────────────────────────────────────────
class BranchTransferReceipt(models.Model):
    """A receiving session at the destination plant, against a dispatched plan.

    The sending side of a transfer already exists (``BranchTransferPlan`` +
    ``picked_qty``); this is the other half. Kept as its own model rather than
    more fields on the plan because receiving happens at a different plant, by a
    different operator, at a different time — and often in several vehicle loads,
    so a plan can carry more than one receipt. The running totals live on
    ``BranchTransferLine.received_qty`` so they accumulate across those loads.
    """

    plan = models.ForeignKey(
        BranchTransferPlan, on_delete=models.CASCADE, related_name="receipts",
    )
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="bt_receipts")
    name = models.CharField(max_length=200, blank=True, verbose_name="Trip / Reference")
    vehicle_no = models.CharField(max_length=60, blank=True, verbose_name="Vehicle No.")
    docket_no = models.CharField(max_length=60, blank=True, verbose_name="Docket No.")
    #: The condition new scans are stamped with, so a receiver working
    #: through a crushed pallet flips once rather than per unit. Mirrors
    #: ``ScanSession.condition``.
    condition = models.CharField(max_length=60, blank=True, default="Good")
    remarks = models.TextField(blank=True)

    #: Optional lock onto one receiving plant, for a plan that fans out to several.

    is_finished = models.BooleanField(default=False)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_bt_receipts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Branch Transfer Receipt"
        verbose_name_plural = "Branch Transfer Receipts"
        indexes = [models.Index(fields=["plan", "-created_at"])]

    def __str__(self):
        return self.name or f"Receipt #{self.pk}"


class BranchTransferReceiptScan(models.Model):
    """One unit booked in at the destination.

    Only accepted scans land here — the rows drive ``received_qty`` and the
    duplicate-serial check, so a rejected row would corrupt both. Rejections go
    to ``ScanLog`` instead.
    """

    receipt = models.ForeignKey(
        BranchTransferReceipt, on_delete=models.CASCADE, related_name="scans",
    )
    line = models.ForeignKey(
        BranchTransferLine, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="receipt_scans",
    )
    material = models.CharField(max_length=100)
    batch = models.CharField(max_length=100, blank=True)
    serial = models.CharField(max_length=100, blank=True)
    raw_scan = models.TextField(blank=True)
    #: Stamped from the receipt's active condition toggle at scan time, and
    #: independently correctable afterward — the same shape as
    #: ``ScanRecord.condition`` in the extractor rather than a second
    #: vocabulary for the same fact. Free text for the same reason it is there:
    #: the legacy tool never enforced a fixed list either.
    #:
    #: A damaged unit is still RECEIVED. It is deliberately not subtracted from
    #: ``received_qty`` — doing so would make "Short" mean "lost in transit or
    #: broken", collapsing two facts the warehouse acts on differently: short is
    #: chased with the sending plant, damaged is booked to the entity's damage
    #: storage location.
    condition = models.CharField(max_length=60, blank=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_bt_receipt_scans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Branch Transfer Receipt Scan"
        verbose_name_plural = "Branch Transfer Receipt Scans"
        indexes = [
            models.Index(fields=["receipt", "-created_at"]),
            models.Index(fields=["material", "batch"]),
        ]

    def __str__(self):
        return f"{self.material} {self.serial}".strip()


# ─────────────────────────────────────────────────────────────────────────────
# BT Scanning — the floor console for a branch transfer
#
# Branch Transfer (above) is the *plan* side: upload the plan and the MB52
# stock, pre-assign recommended batches. BT Scanning is what the picker
# actually stands in front of, and it needs three things the plan screen has
# no place for, all of them present in the legacy bt-scanner.js: cartons
# ("Box 1", "Box 2", …) that scans are dropped into, a colour zone per carton
# so the receiving dock can sort a pallet by eye, and a per-session location
# priority so one site can draw FG02-first while another draws FG01-first.
#
# Picking still goes through services.transfers, and still moves the same
# BranchTransferLine.picked_qty — this is a different console for the same
# operation, not a parallel set of numbers.
# ─────────────────────────────────────────────────────────────────────────────


class BtScanSession(models.Model):
    """One picker's carton-tracked run against a branch-transfer plan."""

    #: The zones the legacy tool shipped, as the colour a dock worker sees.
    ZONE_CHOICES = [
        ("", "No zone"),
        ("Red", "Red"),
        ("Green", "Green"),
        ("Blue", "Blue"),
        ("Yellow", "Yellow"),
        ("Orange", "Orange"),
        ("Purple", "Purple"),
        ("White", "White"),
    ]

    plan = models.ForeignKey(
        BranchTransferPlan, on_delete=models.CASCADE, related_name="scan_sessions",
    )
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="bt_scan_sessions")
    name = models.CharField(max_length=200, blank=True)

    #: Locks scanning to one receiving plant, for a plan that fans out to
    #: several — the legacy "Focus Mode" toggle.

    #: Comma-separated storage locations, highest priority first. Blank means
    #: fall back to the entity's own configured order, so a site that has no
    #: opinion is not forced to invent one.
    location_priority = models.CharField(
        max_length=200, blank=True,
        help_text="Storage locations to draw from, best first. For example: FG02, FG01, FG03",
    )

    is_finished = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_bt_scan_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "BT Scan Session"
        verbose_name_plural = "BT Scan Sessions"

    def __str__(self):
        return self.name or f"BT Scan #{self.pk}"

    @property
    def priority_locations(self) -> list[str]:
        return [part.strip().upper() for part in self.location_priority.split(",") if part.strip()]


class BtScanBox(models.Model):
    """A physical carton within a BT scanning session."""

    session = models.ForeignKey(BtScanSession, on_delete=models.CASCADE, related_name="boxes")
    name = models.CharField(max_length=60)
    zone = models.CharField(max_length=20, blank=True, choices=BtScanSession.ZONE_CHOICES)
    sequence = models.PositiveIntegerField(default=0)
    is_closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "id"]
        verbose_name = "BT Scan Box"
        verbose_name_plural = "BT Scan Boxes"
        constraints = [
            models.UniqueConstraint(fields=["session", "name"], name="merilv_btbox_unique_name"),
        ]

    def __str__(self):
        return self.name


class BtScan(models.Model):
    """One accepted unit, picked into a carton.

    Accepted scans only — these rows are what ``picked_qty`` is reconciled
    against, so a rejected attempt here would inflate the picked total.
    Rejections go to ``ScanLog``, the same convention the other consoles use.
    """

    session = models.ForeignKey(BtScanSession, on_delete=models.CASCADE, related_name="scans")
    line = models.ForeignKey(
        BranchTransferLine, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="bt_scans",
    )
    box = models.ForeignKey(
        BtScanBox, null=True, blank=True, on_delete=models.SET_NULL, related_name="scans",
    )
    material = models.CharField(max_length=100)
    batch = models.CharField(max_length=100, blank=True)
    serial = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=20, blank=True)
    raw_scan = models.TextField(blank=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_bt_scans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "BT Scan"
        verbose_name_plural = "BT Scans"
        indexes = [
            models.Index(fields=["session", "-created_at"]),
            models.Index(fields=["material", "batch"]),
        ]

    def __str__(self):
        return f"{self.material} {self.serial}".strip()


# ─────────────────────────────────────────────────────────────────────────────
# ZVA05 auto-assign
# ─────────────────────────────────────────────────────────────────────────────


class Zva05Dump(models.Model):
    """An uploaded ZVA05 open-order extract.

    Used when deliveries have not been created in SAP yet: scans are assigned
    straight to open sales-order lines, matched on material alone.
    """

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="zva05_dumps")
    name = models.CharField(max_length=200, blank=True)
    source_file = models.FileField(upload_to="merilv/zva05/%Y/%m/", verbose_name="ZVA05 Extract")
    original_filename = models.CharField(max_length=300, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    #: Locks scanning to one "customer - sales document" group.
    is_finished = models.BooleanField(default=False)
    warnings = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_zva05_dumps",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ZVA05 Extract"
        verbose_name_plural = "ZVA05 Extracts"

    def __str__(self):
        return self.name or f"ZVA05 #{self.pk}"


class Zva05Line(models.Model):
    """One open sales-order line."""

    dump = models.ForeignKey(Zva05Dump, on_delete=models.CASCADE, related_name="lines")
    material = models.CharField(max_length=100)
    customer = models.CharField(max_length=150, blank=True)
    sales_document = models.CharField(max_length=60, blank=True)
    item_no = models.CharField(max_length=20, blank=True)
    required_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    scanned_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    sequence = models.PositiveIntegerField(default=0)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["sequence", "id"]
        verbose_name = "ZVA05 Line"
        verbose_name_plural = "ZVA05 Lines"
        indexes = [models.Index(fields=["dump", "material"])]

    def __str__(self):
        return f"{self.sales_document}/{self.item_no} {self.material}"

    @property
    def focus_key(self):
        """Groups lines the way an operator focuses them: one customer order."""
        return f"{self.customer} - {self.sales_document}".strip(" -")

    @property
    def pending_qty(self):
        return max(self.required_qty - self.scanned_qty, 0)


class Zva05Scan(models.Model):
    """One scan assigned to a ZVA05 order line."""

    dump = models.ForeignKey(Zva05Dump, on_delete=models.CASCADE, related_name="scans")
    line = models.ForeignKey(
        Zva05Line, null=True, blank=True, on_delete=models.SET_NULL, related_name="scans",
    )
    material = models.CharField(max_length=100)
    batch = models.CharField(max_length=100, blank=True)
    serial = models.CharField(max_length=100, blank=True)
    raw_scan = models.TextField(blank=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_zva05_scans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "ZVA05 Scan"
        verbose_name_plural = "ZVA05 Scans"
        indexes = [models.Index(fields=["dump", "material", "serial"])]

    def __str__(self):
        return f"{self.material} {self.serial}".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Invoice consolidation
# ─────────────────────────────────────────────────────────────────────────────


class ConsolidationRun(models.Model):
    """A batch of invoice PDFs or spreadsheets flattened into one export."""

    SOURCE_PDF = "pdf"
    SOURCE_SHEET = "sheet"
    SOURCE_CHOICES = [
        (SOURCE_PDF, "PDF Invoices"),
        (SOURCE_SHEET, "Excel / CSV"),
    ]

    name = models.CharField(max_length=200, blank=True)
    source_type = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_PDF)
    file_count = models.PositiveIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_consolidations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Consolidation Run"
        verbose_name_plural = "Consolidation Runs"

    def __str__(self):
        return self.name or f"Consolidation #{self.pk}"


class ConsolidationRow(models.Model):
    """One parsed invoice line."""

    run = models.ForeignKey(ConsolidationRun, on_delete=models.CASCADE, related_name="rows")
    source_file = models.CharField(max_length=300, blank=True)
    invoice_no = models.CharField(max_length=80, blank=True)
    invoice_date = models.CharField(max_length=40, blank=True)
    customer_name = models.CharField(max_length=300, blank=True)
    material = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=400, blank=True)
    batch = models.CharField(max_length=100, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    rate = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]
        verbose_name = "Consolidation Row"
        verbose_name_plural = "Consolidation Rows"

    def __str__(self):
        return f"{self.invoice_no} — {self.material}"


# ─────────────────────────────────────────────────────────────────────────────
# Audit trail
# ─────────────────────────────────────────────────────────────────────────────


class ActivityLog(models.Model):
    """Module-level audit trail for master-data edits and configuration changes."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merilv_activity",
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Activity Log"
        verbose_name_plural = "Activity Logs"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_id or 'system'}"


# ─────────────────────────────────────────────────────────────────────────────
# Legacy Supabase compatibility
# ─────────────────────────────────────────────────────────────────────────────


class LegacyProfile(models.Model):
    """Raw legacy ``profiles`` row retained beside the mapped Django user."""

    legacy_id = models.UUIDField(unique=True)
    email = models.EmailField(blank=True)
    username = models.CharField(max_length=150, blank=True)
    full_name = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=40, blank=True)
    avatar_url = models.TextField(blank=True)
    manager_legacy_id = models.UUIDField(null=True, blank=True)
    permissions = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    imported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merilv_legacy_profiles",
    )
    updated_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["email", "username"]
        verbose_name = "Legacy Profile"
        verbose_name_plural = "Legacy Profiles"
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["role"]),
        ]

    def __str__(self):
        return self.email or self.username or str(self.legacy_id)


class LegacyAuditLog(models.Model):
    """Legacy ``audit_logs`` row retained beside the mapped ActivityLog."""

    legacy_id = models.UUIDField(unique=True)
    actor_legacy_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=100)
    target_entity = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    activity = models.ForeignKey(
        ActivityLog,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="legacy_audit_rows",
    )
    created_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Legacy Audit Log"
        verbose_name_plural = "Legacy Audit Logs"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"{self.action} → {self.target_entity or self.target_id}"


class LegacyOutboundDraft(models.Model):
    user_email = models.EmailField(unique=True)
    state = models.JSONField(default=dict, blank=True)
    last_updated = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user_email"]
        verbose_name = "Legacy Outbound Draft"
        verbose_name_plural = "Legacy Outbound Drafts"

    def __str__(self):
        return self.user_email


class LegacyOutboundHistory(models.Model):
    legacy_id = models.CharField(max_length=100, unique=True)
    timestamp = models.DateTimeField(null=True, blank=True)
    user_email = models.EmailField(blank=True)
    mode = models.CharField(max_length=80, blank=True)
    total = models.IntegerField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "Legacy Outbound History"
        verbose_name_plural = "Legacy Outbound History"
        indexes = [models.Index(fields=["user_email", "-timestamp"])]

    def __str__(self):
        return self.legacy_id


class LegacyScanHistory(models.Model):
    legacy_id = models.CharField(max_length=100, unique=True)
    timestamp = models.DateTimeField(null=True, blank=True)
    user_email = models.EmailField(blank=True)
    mode = models.CharField(max_length=80, blank=True)
    total = models.IntegerField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "Legacy Scan History"
        verbose_name_plural = "Legacy Scan History"
        indexes = [models.Index(fields=["user_email", "-timestamp"])]

    def __str__(self):
        return self.legacy_id


class LegacyBranchTransferHistory(models.Model):
    legacy_id = models.UUIDField(unique=True)
    created_at = models.DateTimeField(null=True, blank=True)
    created_by = models.EmailField(blank=True)
    scanner_type = models.CharField(max_length=40, blank=True)
    receiver_code = models.CharField(max_length=80, blank=True)
    plant_name = models.CharField(max_length=200, blank=True)
    total_materials = models.IntegerField(default=0)
    total_picked = models.IntegerField(default=0)
    total_pending = models.IntegerField(default=0)
    total_boxes = models.IntegerField(default=0)
    color_zones = models.JSONField(default=list, blank=True)
    remarks = models.TextField(blank=True)
    plan_data = models.JSONField(default=list, blank=True)
    scan_data = models.JSONField(default=list, blank=True)
    box_data = models.JSONField(default=list, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Legacy BT History"
        verbose_name_plural = "Legacy BT History"
        indexes = [models.Index(fields=["scanner_type", "-created_at"])]

    def __str__(self):
        return f"{self.scanner_type} {self.created_at or self.legacy_id}"


class LegacyMessage(models.Model):
    legacy_id = models.UUIDField(unique=True)
    context_id = models.CharField(max_length=200)
    sender_legacy_id = models.UUIDField(null=True, blank=True)
    sender_name = models.CharField(max_length=200, blank=True)
    cipher = models.TextField(blank=True)
    status = models.CharField(max_length=40, blank=True)
    message_type = models.CharField(max_length=40, blank=True)
    file_url = models.TextField(blank=True)
    file_name = models.CharField(max_length=300, blank=True)
    read_by = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Legacy Message"
        verbose_name_plural = "Legacy Messages"
        indexes = [models.Index(fields=["context_id", "-created_at"])]

    def __str__(self):
        return f"{self.context_id}: {self.sender_name or self.sender_legacy_id}"


class LegacyMessageReaction(models.Model):
    legacy_id = models.UUIDField(unique=True)
    message_legacy_id = models.UUIDField(null=True, blank=True)
    user_legacy_id = models.UUIDField(null=True, blank=True)
    emoji = models.CharField(max_length=40)
    created_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Legacy Message Reaction"
        verbose_name_plural = "Legacy Message Reactions"

    def __str__(self):
        return self.emoji


class LegacyUserData(models.Model):
    legacy_id = models.UUIDField(unique=True)
    user_legacy_id = models.UUIDField(null=True, blank=True)
    tool_name = models.CharField(max_length=120)
    key = models.CharField(max_length=200, blank=True)
    file_data = models.JSONField(default=dict, blank=True)
    value = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tool_name", "key"]
        verbose_name = "Legacy User Data"
        verbose_name_plural = "Legacy User Data"
        indexes = [models.Index(fields=["tool_name", "key"])]

    def __str__(self):
        return f"{self.tool_name}/{self.key or self.legacy_id}"


class LegacyVehicle(models.Model):
    legacy_id = models.UUIDField(unique=True)
    vehicle_number = models.CharField(max_length=80)
    vehicle_type = models.CharField(max_length=120, blank=True)
    courier_code = models.CharField(max_length=120, blank=True)
    owner_name = models.CharField(max_length=200, blank=True)
    capacity_kg = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vehicle_number"]
        verbose_name = "Legacy Vehicle"
        verbose_name_plural = "Legacy Vehicles"
        indexes = [models.Index(fields=["vehicle_number"])]

    def __str__(self):
        return self.vehicle_number


class LegacyPlantOrgMapping(models.Model):
    legacy_id = models.BigIntegerField(unique=True)
    plant_code = models.CharField(max_length=40)
    org_code = models.CharField(max_length=40)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plant_code", "org_code"]
        verbose_name = "Legacy Plant Organization Mapping"
        verbose_name_plural = "Legacy Plant Organization Mappings"
        unique_together = [("plant_code", "org_code")]

    def __str__(self):
        return f"{self.plant_code} → {self.org_code}"


class LegacyUserPlantMapping(models.Model):
    legacy_id = models.BigIntegerField(unique=True)
    user_legacy_id = models.UUIDField()
    plant_code = models.CharField(max_length=40)
    created_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plant_code", "user_legacy_id"]
        verbose_name = "Legacy User Plant Mapping"
        verbose_name_plural = "Legacy User Plant Mappings"
        unique_together = [("user_legacy_id", "plant_code")]

    def __str__(self):
        return f"{self.user_legacy_id} → {self.plant_code}"
