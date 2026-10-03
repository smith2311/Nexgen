"""Activity-log presentation, shared by the screen and the download.

``ActivityLog.metadata`` is a ``JSONField``, so anything that renders it has to
decide how. Keeping that decision here rather than in the template library
means the Excel download says exactly what the table says — the same reason
Delivery History and Scan History share one queryset builder.

Formatting only: nothing here touches the database.
"""

from __future__ import annotations

#: Keys whose meaning is not obvious from the key name alone.
LABELS = {
    "lines": "Lines",
    "rows": "Rows",
    "entity": "Entity",
    "created": "Created",
    "updated": "Updated",
    "skipped": "Skipped",
    "replaced": "Replaced",
    "plan": "Plan",
    "session": "Session",
    "batch": "Batch",
    "role": "Role",
    "plants": "Plants",
    "manager": "Manager",
    "assigned": "Assigned",
    "mapping": "Column mapping",
    "original": "Was",
    "replacement": "Now",
    "old": "Was",
    "new": "Now",
}

#: Acronyms the warehouse uses, so a slug does not title-case into "Bt"/"Zva05".
ACRONYMS = {
    "bt": "BT",
    "zva05": "ZVA05",
    "yvl06": "YVL06",
    "mb52": "MB52",
    "ymm061": "YMM061",
    "sku": "SKU",
    "gst": "GST",
    "pdf": "PDF",
}


def humanise_key(key) -> str:
    return LABELS.get(key, str(key).replace("_", " ").capitalize())


def humanise_value(value) -> str:
    """One metadata value as a person would read it.

    Rendering the dict straight gave Python's ``repr`` — ``{'lines': 183}``,
    quotes and braces and all, and a bare ``{}`` for the (very common)
    no-detail case. That is a debug dump, not a log entry.
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, dict):
        # A nested mapping (the column-map action does this) is summarised
        # rather than expanded — the expandable row on screen, and the raw
        # JSON column in the download, carry the full structure.
        return ", ".join(f"{k} = {v}" for k, v in value.items()) or "—"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "—"
    return str(value)


def detail_pairs(metadata) -> list[dict[str, str]]:
    """``metadata`` as label/value pairs, or ``[]`` when there is nothing.

    An empty list rather than a placeholder string so the caller decides how
    "no detail" looks — a dash on screen, a blank cell in a spreadsheet.
    """
    if not isinstance(metadata, dict) or not metadata:
        return []
    return [
        {"label": humanise_key(key), "value": humanise_value(value)}
        for key, value in metadata.items()
    ]


def detail_text(metadata) -> str:
    """The same pairs as one cell of text, for the Excel download."""
    return " · ".join(f"{pair['label']}: {pair['value']}" for pair in detail_pairs(metadata))


def humanise_action(action) -> str:
    """``bt_plan_upload`` -> ``BT plan upload``.

    The stored slug stays the filter value and the machine-readable record;
    this is only how it reads to a person.
    """
    if not action:
        return "—"
    words = [ACRONYMS.get(part.lower(), part) for part in str(action).split("_")]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


__all__ = [
    "ACRONYMS",
    "LABELS",
    "detail_pairs",
    "detail_text",
    "humanise_action",
    "humanise_key",
    "humanise_value",
]

# ─── Before/after diffs (#23) ────────────────────────────────────────────────
#: Never diffed: surrogate keys and bookkeeping timestamps change on every save
#: and say nothing about what an operator did. `updated_at` in particular would
#: appear in every single diff and drown the field that actually moved.
_UNDIFFED_FIELDS = frozenset({"id", "pk", "created_at", "updated_at"})


def snapshot(instance, fields=None) -> dict:
    """The comparable state of one model row, as plain JSON-able values.

    Related objects are reduced to their primary key rather than repr'd: a
    ``<Entity: MHPL — MHPL Orthopedics>`` in an audit row is neither stable
    across a rename nor comparable.
    """
    if instance is None:
        return {}
    names = fields or [
        field.name for field in instance._meta.concrete_fields
        if field.name not in _UNDIFFED_FIELDS
    ]
    state = {}
    for name in names:
        try:
            value = getattr(instance, name)
        except Exception:
            continue
        if hasattr(value, "pk"):
            value = value.pk
        elif isinstance(value, (dict, list)):
            # JSON columns compare fine but can be huge; keep them out of the
            # diff and let the row's own screen show them.
            continue
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            value = str(value)
        state[name] = value
    return state


def diff_snapshots(before: dict, after: dict) -> dict:
    """``{field: [before, after]}`` for the fields that actually moved.

    Only changed fields, deliberately. A material carries ~60 YMM061 columns;
    logging every one on every edit would bury the one that moved and multiply
    the table's size for nothing.
    """
    changes = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = [old, new]
    return changes
