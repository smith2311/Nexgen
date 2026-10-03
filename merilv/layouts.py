"""Saved column layouts — the legacy "Layout Manager" (``script.js:3183+``),
generalised into one reusable mechanism instead of a per-screen rebuild.

Each screen that wants this registers its available columns in
``SCREEN_COLUMNS``/``_material_master_columns()`` below; everything else —
list, save, activate, clear, delete — is generic and screen-agnostic. A
layout only ever records which of a screen's *known* columns are shown, in
what order, and under what header text: the column keys themselves are
whitelisted against the registry on every write, so a layout can never smuggle
in a key the screen does not actually have data for.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import MaterialMaster, SavedLayout
from .rbac import can_access_merilv

access_required = user_passes_test(can_access_merilv, login_url="landing_page")

#: Only fields ``masters_materials.html`` actually renders a
#: ``<td data-col="...">`` for — offering a column here that the table has no
#: cell for would be a checkbox that does nothing, so extending this list
#: means adding the matching cell in the template too. Labels are read from
#: the model's own ``verbose_name`` rather than retyped, so they can never
#: drift out of sync with the masters-console form for the same field.
_MATERIAL_MASTER_COLUMN_KEYS = (
    "material_code", "scan_code", "gtin", "ean_upc", "material_description", "material_group",
    "material_type", "division",
    "plant", "storage_location", "batch_managed", "serial_managed",
    "ph_level_01", "ph_level_01_desc", "ph_level_02", "ph_level_02_desc",
    "ph_level_03", "ph_level_03_desc", "ph_level_04", "ph_level_04_desc",
    "ph_level_05", "ph_level_05_desc",
)
#: Not real MaterialMaster fields (entity is a relation, updated_at needs no
#: verbose_name lookup) but the table renders a column for both.
_MATERIAL_MASTER_EXTRA_COLUMNS = (("entity", "Entity"), ("updated_at", "Updated"))
#: Shown by default — everything else exists but starts hidden, which is the
#: whole point: the YMM061 import carries far more columns than fit on screen
#: at once, and showing all of them unconditionally is what made the old
#: screen unreadable (Row 3.8 / 17.1).
_MATERIAL_MASTER_DEFAULTS = {
    "material_code", "scan_code", "gtin", "ean_upc", "material_description", "material_group",
    "entity", "updated_at",
}


def _material_master_columns() -> list[dict]:
    columns = [
        {
            "key": key,
            "label": str(MaterialMaster._meta.get_field(key).verbose_name),
            "default": key in _MATERIAL_MASTER_DEFAULTS,
        }
        for key in _MATERIAL_MASTER_COLUMN_KEYS
    ]
    columns.extend(
        {"key": key, "label": label, "default": key in _MATERIAL_MASTER_DEFAULTS}
        for key, label in _MATERIAL_MASTER_EXTRA_COLUMNS
    )
    return columns


#: Row 3 — Scan History's own column set.
_SCAN_HISTORY_COLUMNS = [
    {"key": "name", "label": "Session", "default": True},
    {"key": "mode", "label": "Mode", "default": True},
    {"key": "entity", "label": "Entity", "default": True},
    {"key": "operator", "label": "Operator", "default": True},
    {"key": "found", "label": "Found", "default": True},
    {"key": "total", "label": "Total", "default": True},
    {"key": "duplicates", "label": "Duplicates", "default": False},
    {"key": "not_found", "label": "Not Found", "default": False},
    {"key": "status", "label": "Status", "default": True},
    {"key": "created", "label": "Created", "default": True},
]


#: Extract mode's result table. The change request asks for the layout feature
#: "across every module"; the extractor was the one scanning screen with no
#: column control at all, even though its table is the widest thing a paste
#: produces. Keys match the data-col attributes in extractor.html — a key with
#: no matching cell would render a checkbox that does nothing.
_EXTRACTOR_COLUMNS = [
    {"key": "raw", "label": "Raw", "default": True},
    {"key": "material", "label": "Material", "default": True},
    {"key": "batch", "label": "Batch", "default": True},
    {"key": "serial", "label": "Serial", "default": True},
    {"key": "mfg", "label": "MFG", "default": True},
    {"key": "expiry", "label": "Expiry", "default": True},
    # Row 17 — read from the material master, not the barcode. Off by default
    # on purpose: the brief asks for them to be readable here, and the toggle
    # is what makes that possible without making the default table wider than
    # a laptop, which is the complaint #3 raised about this screen.
    {"key": "group", "label": "Material Group", "default": False},
    {"key": "ph", "label": "PH Level", "default": False},
    {"key": "valid", "label": "Valid", "default": True},
]


def _registry() -> dict[str, list[dict]]:
    """Screen key -> that screen's available columns. Built fresh per call —
    the Material Master list reflects the live model, not a stale cache."""
    return {
        "scan_history": _SCAN_HISTORY_COLUMNS,
        "extractor": _EXTRACTOR_COLUMNS,
        "masters_materials": _material_master_columns(),
    }


def _valid_keys(screen_key: str) -> set[str] | None:
    columns = _registry().get(screen_key)
    return {c["key"] for c in columns} if columns is not None else None


def _clean_columns(raw: object, valid_keys: set[str]) -> list[dict]:
    """Whitelist + normalise a submitted column list.

    Unknown keys are dropped rather than rejecting the whole save — a stale
    layout saved before a column was removed should degrade gracefully, not
    lock the user out of saving anything.
    """
    if not isinstance(raw, list):
        return []
    cleaned = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")
        if key not in valid_keys or key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "key": key,
            "header": str(entry.get("header") or "").strip()[:100],
            "enabled": bool(entry.get("enabled", True)),
        })
    return cleaned


@login_required
@access_required
def state(request, screen_key):
    """Every known column for this screen, the user's saved layouts, and
    which one (if any) is active."""
    columns = _registry().get(screen_key)
    if columns is None:
        return JsonResponse({"ok": False, "message": "Unknown screen."}, status=404)

    layouts = list(
        SavedLayout.objects.filter(user=request.user, screen_key=screen_key)
        .order_by("name")
        .values("id", "name", "columns", "is_active")
    )
    return JsonResponse({"ok": True, "available_columns": columns, "layouts": layouts})


@login_required
@access_required
@require_POST
def save(request, screen_key):
    """Create or update (by name) a named layout for the current user."""
    valid_keys = _valid_keys(screen_key)
    if valid_keys is None:
        return JsonResponse({"ok": False, "message": "Unknown screen."}, status=404)

    name = (request.POST.get("name") or "").strip()[:100]
    if not name:
        return JsonResponse({"ok": False, "message": "Name the layout before saving."}, status=400)

    try:
        raw_columns = json.loads(request.POST.get("columns") or "[]")
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "message": "Malformed column list."}, status=400)
    columns = _clean_columns(raw_columns, valid_keys)
    if not columns:
        return JsonResponse({"ok": False, "message": "A layout needs at least one column."}, status=400)

    activate = request.POST.get("activate") == "1"
    with transaction.atomic():
        layout, _created = SavedLayout.objects.update_or_create(
            user=request.user, screen_key=screen_key, name=name,
            defaults={"columns": columns},
        )
        if activate:
            SavedLayout.objects.filter(
                user=request.user, screen_key=screen_key, is_active=True,
            ).exclude(pk=layout.pk).update(is_active=False)
            if not layout.is_active:
                layout.is_active = True
                layout.save(update_fields=["is_active"])

    return JsonResponse({
        "ok": True,
        "layout": {"id": layout.pk, "name": layout.name, "columns": layout.columns, "is_active": layout.is_active},
    })


@login_required
@access_required
@require_POST
def activate(request, screen_key, pk):
    """Make one saved layout this user's active view for this screen."""
    layout = get_object_or_404(SavedLayout, pk=pk, user=request.user, screen_key=screen_key)
    with transaction.atomic():
        SavedLayout.objects.filter(
            user=request.user, screen_key=screen_key, is_active=True,
        ).exclude(pk=layout.pk).update(is_active=False)
        if not layout.is_active:
            layout.is_active = True
            layout.save(update_fields=["is_active"])
    return JsonResponse({"ok": True, "layout": {"id": layout.pk, "name": layout.name, "columns": layout.columns}})


@login_required
@access_required
@require_POST
def clear_active(request, screen_key):
    """Fall back to the screen's built-in default column set."""
    SavedLayout.objects.filter(user=request.user, screen_key=screen_key, is_active=True).update(is_active=False)
    return JsonResponse({"ok": True})


@login_required
@access_required
@require_POST
def delete(request, screen_key, pk):
    layout = get_object_or_404(SavedLayout, pk=pk, user=request.user, screen_key=screen_key)
    layout.delete()
    return JsonResponse({"ok": True})
