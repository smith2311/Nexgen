"""Bridge between the :class:`~merilv.models.Entity` records and the engines.

The engines take plain dataclasses so they stay testable without a database.
This module is the only place that knows how to turn a stored ``Entity`` into
those dataclasses, and it also builds the in-memory master-data lookups the
parser needs.
"""

from __future__ import annotations

from typing import Mapping

from ..models import CustomerMaster, Entity, MaterialMaster
from .barcode import EntityProfile, PositionalRule, parse_positional_rules
from .outbound import OutboundSettings
from .validation import CONVENTION_MHPL, CONVENTION_MLSIPL, ValidationSettings


def get_profile(entity: Entity) -> EntityProfile:
    """Build the parser profile for a stored entity."""
    return EntityProfile(
        code=entity.code,
        name=entity.name,
        strip_whitespace=entity.strip_whitespace,
        supports_serial=entity.supports_serial,
        ai21_is_material=entity.ai21_is_material,
        sanitise_batch=entity.sanitise_batch,
        allow_envelope=entity.allow_envelope,
        positional_walker=entity.positional_walker,
        compact_gs1=entity.compact_gs1,
        positional_rules=entity.positional_rules,
        trauma_legacy=entity.trauma_legacy,
        pseudo_serial_digits=entity.pseudo_serial_digits or 5,
    )


def get_validation_settings(entity: Entity) -> ValidationSettings:
    """Build reconciliation settings for a stored entity.

    The expiry convention is inferred from the thresholds themselves: the two
    legacy families are distinguished by which of "near" and "short" is tighter,
    so there is no need for a separate flag that could drift out of sync.
    """
    near = float(entity.near_expiry_months)
    short = float(entity.short_expiry_months)
    convention = CONVENTION_MHPL if near < short else CONVENTION_MLSIPL

    return ValidationSettings(
        match_concat=entity.match_concat,
        match_batch=entity.match_batch,
        match_material=entity.match_material,
        concat_delimiter=entity.concat_delimiter,
        triple_sort=entity.triple_sort,
        multi_row_consume=entity.multi_row_consume,
        smart_zero=entity.smart_zero,
        use_scan_qty=entity.use_scan_qty,
        strict_batch=entity.strict_batch,
        plant_priority=entity.plant_priority_list,
        good_location=entity.good_storage_location,
        damage_location=entity.damage_storage_location,
        expiry_convention=convention,
        near_expiry_months=near,
        short_expiry_months=short,
        month_divisor=float(entity.month_divisor or 30),
    )


def get_outbound_settings(entity: Entity) -> OutboundSettings:
    from django.conf import settings as django_settings

    return OutboundSettings(
        # This module is the designated bridge to Django, so the debug switch
        # is read here and handed to the engine as data.
        debug_match=bool(getattr(django_settings, "DEBUG_SCANNER_MATCH", False)),
        enforce_box_capacity=entity.enforce_box_capacity,
        allow_customer_focus=entity.allow_customer_focus,
        allow_partial_export=entity.allow_partial_export,
        require_batch=True,
    )


def positional_rules_misconfigured(entity: Entity) -> bool:
    """True when an entity asks for positional parsing but supplies no rules.

    This is the MLSIPL failure. Its profile sets ``positional_rules = True``,
    but the rules themselves are configuration — in the legacy tool they were
    entered in the admin's parse-rules table (see the ``parseRules[len]``
    branch of ``parseBarcode`` in MLSIPL-Validator.html) — and that
    configuration was never migrated. ``parse_rules_csv`` is empty.

    The effect is silent: ``parse_barcode`` guards the positional pass with
    ``if profile.positional_rules and positional_rules``, so an empty rule set
    makes the strategy a no-op. A barcode that needs it simply falls through to
    the raw fallback and comes back with the whole string as an identifier,
    with nothing anywhere saying why. Callers use this to say so out loud.
    """
    return bool(entity.positional_rules) and not (entity.parse_rules_csv or "").strip()


def get_positional_rules(entity: Entity) -> dict[int, list[PositionalRule]]:
    if not entity.positional_rules or not entity.parse_rules_csv:
        return {}
    return parse_positional_rules(entity.parse_rules_csv)


def get_bt_location_priority(entity: Entity) -> list[str]:
    """Which storage location a branch-transfer auto-assign should draw from first."""
    return entity.bt_location_priority_list


def build_material_master(entity: Entity) -> dict[str, dict[str, object]]:
    """Load an entity's materials into the lookup shape the parser expects.

    Each material is registered under its scan code, its SAP code and its GTIN
    (left-padded to 14 digits), each in four spellings — as-is, uppercased, and
    both with leading zeros stripped. Labels, SAP and the delivery dump rarely
    agree on zero-padding, and resolving that at lookup time would mean a scan
    doing string surgery on every candidate.
    """
    lookup: dict[str, dict[str, object]] = {}

    def register(key: str, payload: dict[str, object]) -> None:
        if not key:
            return
        for variant in (key, key.upper(), key.lstrip("0"), key.lstrip("0").upper()):
            if variant:
                lookup.setdefault(variant, payload)

    # PH Level 01 only, not all five. It is the level the client means by "PH
    # Level" singular, and the deeper four are per-material reference data that
    # no scan decision reads — carrying them here would triple the size of a
    # lookup built once per scan for data only the Material screen's column
    # toggle uses.
    fields = (
        "scan_code", "material_code", "material_description", "gtin",
        "material_group", "ph_level_01", "ph_level_01_desc",
    )
    for scan_code, material_code, description, gtin, group, ph_level, ph_desc in (
        MaterialMaster.objects.filter(entity=entity).values_list(*fields).iterator()
    ):
        payload: dict[str, object] = {
            "Material Code": material_code,
            "Material": material_code,
            "Code": material_code,
            "Scan Code": scan_code,
            "Description": description,
            "GTIN": gtin,
            "Material Group": group,
            "PH Level": ph_level,
            "PH Level Desc.": ph_desc,
        }
        register(scan_code, payload)
        register(material_code, payload)
        if gtin:
            register(gtin, payload)
            register(gtin.zfill(14), payload)

    return lookup


def build_customer_master(entity: Entity | None = None) -> dict[str, str]:
    """Customer code → name, for resolving delivery dumps."""
    queryset = CustomerMaster.objects.all()
    if entity is not None:
        queryset = queryset.filter(entity__in=[entity, None])
    lookup: dict[str, str] = {}
    for code, name in queryset.values_list("customer_code", "customer_name").iterator():
        if not code:
            continue
        for variant in (code, code.upper(), code.lstrip("0"), code.lstrip("0").upper()):
            if variant:
                lookup.setdefault(variant, name)
    return lookup


def resolve_customer_name(lookup: Mapping[str, str], code: str, fallback_name: str = "") -> str:
    """Resolve a customer name, flagging codes that are missing from the master.

    An unrecognised code is surfaced rather than silently passed through — a
    delivery going out to an unknown customer is exactly the kind of thing the
    warehouse should notice before it ships.
    """
    for candidate in (code, (code or "").upper(), (code or "").lstrip("0")):
        if candidate and candidate in lookup:
            return lookup[candidate]
    if fallback_name and fallback_name in lookup:
        return lookup[fallback_name]
    if fallback_name:
        return fallback_name
    return f"Unknown customer ({code})" if code else "Unknown"


#: Seed data for a fresh install. Mirrors the five business units the legacy
#: system shipped, with each fork's behaviour expressed as configuration.
SEED_ENTITIES = [
    {
        "code": "MHPL",
        "name": "MHPL Orthopedics / Trauma",
        "company_name": "Meril Healthcare Pvt. Ltd.",
        "strip_whitespace": False,
        "supports_serial": True,
        "pseudo_serial_digits": 4,
        "trauma_legacy": True,
        # MHPL receives compact GS1 labels alongside the positional trauma
        # ones. Without this those fell past every strategy to the raw
        # fallback, which — because MHPL supports serials — dropped the entire
        # barcode string into the serial field and reported material and batch
        # as UNKNOWN. parse_barcode tries trauma_legacy first and returns on a
        # match, so enabling this cannot take a label the trauma parser
        # already handles; it only catches what was previously falling through.
        "compact_gs1": True,
        "near_expiry_months": 5,
        "short_expiry_months": 12,
        "month_divisor": 30,
        "allow_customer_focus": True,
        # Every one of these is a real MHPL plant carrying stock in the
        # client's SAP dumps, and all are already in PlantMaster. The first
        # three were the original list; the rest were appended after the Stock
        # Validator flagged 65 cleanly-matched rows as "Plant mismatch" purely
        # for being absent here. Order is consumption priority, so the original
        # three keep their position and the others follow by stock volume.
        "plant_priority": "WGJ1, WGJ7, WGJ4, CTN1, WHR1, WDL1, 9100, 9990",
        "sort_order": 10,
    },
    {
        "code": "MLSIPL",
        "name": "MLSIPL Orthopedics",
        "company_name": "Meril Life Sciences India Pvt. Ltd.",
        "supports_serial": False,
        "ai21_is_material": True,
        "sanitise_batch": True,
        "compact_gs1": True,
        "positional_rules": True,
        "aggregate_delivery_lines": True,
        "near_expiry_months": 6,
        "short_expiry_months": 3,
        "month_divisor": 30.44,
        "sort_order": 20,
    },
    {
        "code": "CARDIO",
        "name": "Cardio",
        "company_name": "Meril Life Sciences India Pvt. Ltd.",
        "compact_gs1": True,
        "aggregate_delivery_lines": True,
        "near_expiry_months": 6,
        "short_expiry_months": 3,
        "month_divisor": 30.44,
        "sort_order": 30,
    },
    {
        "code": "MICRO",
        "name": "Micro",
        "company_name": "Meril Life Sciences India Pvt. Ltd.",
        "allow_envelope": True,
        "positional_walker": True,
        "aggregate_delivery_lines": True,
        "near_expiry_months": 6,
        "short_expiry_months": 3,
        "month_divisor": 30.44,
        "sort_order": 40,
    },
    {
        "code": "MDPL",
        "name": "MDPL Diagnostics",
        "company_name": "Meril Diagnostics Pvt. Ltd.",
        "compact_gs1": True,
        "near_expiry_months": 6,
        "short_expiry_months": 3,
        "month_divisor": 30.44,
        "sort_order": 50,
    },
]

#: Legacy carton dimensions, used to seed :class:`~merilv.models.BoxProfile`.
SEED_BOXES = [
    {"code": "A-20", "length_cm": 56, "width_cm": 41, "height_cm": 32, "max_qty": 20, "sort_order": 10},
    {"code": "B-10-12", "length_cm": 41, "width_cm": 38, "height_cm": 32, "max_qty": 12, "sort_order": 20},
    {"code": "C-09-10 (Brown)", "length_cm": 41, "width_cm": 36, "height_cm": 22, "max_qty": 10, "sort_order": 30},
    {"code": "C-09-10 (White)", "length_cm": 41, "width_cm": 39, "height_cm": 22, "max_qty": 10, "sort_order": 40},
    {"code": "D-06", "length_cm": 39, "width_cm": 26, "height_cm": 22, "max_qty": 6, "sort_order": 50},
    {"code": "E-04", "length_cm": 28, "width_cm": 26, "height_cm": 22, "max_qty": 4, "sort_order": 60},
    {"code": "F-01", "length_cm": 28, "width_cm": 26, "height_cm": 11, "max_qty": 1, "sort_order": 70},
    {"code": "C-MVL", "length_cm": 172, "width_cm": 40, "height_cm": 32, "max_qty": 0, "sort_order": 80},
    {"code": "C-A-36", "length_cm": 56, "width_cm": 30, "height_cm": 40, "max_qty": 36, "sort_order": 90},
]


__all__ = [
    "SEED_BOXES",
    "SEED_ENTITIES",
    "build_customer_master",
    "build_material_master",
    "get_outbound_settings",
    "get_positional_rules",
    "positional_rules_misconfigured",
    "get_profile",
    "get_validation_settings",
    "resolve_customer_name",
]
