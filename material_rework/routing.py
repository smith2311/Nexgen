"""Who reviews a rework batch, in what order.

Routing used to be one line in ``upload_view``::

    is_om = requested_by.name.lower() in ('order management', 'order_management')
    initial_status = 'pending_ppic' if is_om else 'pending_hod'

That encoded two flows in a string comparison. The business runs more than
two, they differ per requester *and* per kind of rework, and they change — so
the chain each batch follows is now data (:class:`~material_rework.models.RoutingRule`)
that admins edit on the Routing Rules screen, and this module is the engine
that reads it.

**A chain is some gates plus one terminal stage.** ``om`` and ``ppic`` are
gates: a team looks at the batch and approves or rejects it. ``production`` and
``warehouse`` are terminals — they are not approve/reject screens but the entry
points to work that already existed, so a chain ends at exactly one of them:

* ``production`` — the Production Review screen, where production says how much
  of each line it will take and books the slot. It then starts the rework round
  trip (warehouse releases → production reworks → warehouse counts back in).
* ``warehouse`` — straight into that round trip with no review at all, for
  rework production raised itself. There is nothing for production to approve
  when production is the requester, so the batch arrives in the warehouse
  queue already carrying the slot the uploader committed to.

Two rules matter more than the rest:

**A batch's chain is frozen when it is created.** ``resolve()`` is called once,
and the result is stored on the batch. Everything afterwards reads the batch's
own copy. Editing a routing rule therefore changes where *new* batches go and
can never re-route, strand or skip a stage on work already moving — which is
what would happen if the chain were looked up fresh on each hand-off.

**The fallback requires more review, not less.** A batch whose requester has no
rule (someone added a Requested By option and no rule for it) gets the longest
chain rather than the shortest. Getting an unnecessary approval is a nuisance;
silently skipping one is a material loss.

HOD is deliberately absent from the stage vocabulary. It was the default gate
for everything except Order Management and is no longer part of any flow.
"""

from __future__ import annotations

# ── Stages ─────────────────────────────────────────────────────────────────

STAGE_OM = "om"
STAGE_PPIC = "ppic"
STAGE_PRODUCTION = "production"
STAGE_WAREHOUSE = "warehouse"

#: Gates: a team approves or rejects the batch and it moves on.
GATE_STAGES = (STAGE_OM, STAGE_PPIC)

#: Terminals: the chain ends here and existing machinery takes over. A chain
#: has exactly one, as its last entry.
TERMINAL_STAGES = (STAGE_PRODUCTION, STAGE_WAREHOUSE)

ALL_STAGES = GATE_STAGES + TERMINAL_STAGES

STAGE_LABELS = {
    STAGE_OM: "Order Management",
    STAGE_PPIC: "PPIC",
    STAGE_PRODUCTION: "Production",
    STAGE_WAREHOUSE: "Warehouse",
}

#: Choices for forms and the admin screen, in the order a chain normally runs.
STAGE_CHOICES = [(code, STAGE_LABELS[code]) for code in ALL_STAGES]

#: Per stage: the batch status while it waits there, the status its lines
#: carry, the Department field holding that team's notification address, and
#: the group that may act. Kept in one place so adding a stage does not mean
#: hunting through views for four parallel lookups that must agree.
STAGE_META = {
    STAGE_OM: {
        "label": "Order Management",
        "batch_status": "pending_om",
        "item_status": "pending_om",
        "email_field": "om_email",
        "group": "MR Order Management",
        "inbox": "material_rework:om_list",
    },
    STAGE_PPIC: {
        "label": "PPIC",
        "batch_status": "pending_ppic",
        "item_status": "pending_ppic",
        "email_field": "ppic_email",
        "group": "MR PPIC",
        "inbox": "material_rework:ppic_list",
    },
    STAGE_PRODUCTION: {
        "label": "Production",
        "batch_status": "pending_production",
        "item_status": "pending_production",
        "email_field": "production_review_email",
        "group": "MR Production",
        "inbox": "material_rework:production_list",
    },
    STAGE_WAREHOUSE: {
        "label": "Warehouse",
        "batch_status": "pending_warehouse_release",
        # Straight into the round trip's first leg: warehouse releases the
        # material to production. No review status of its own, because there
        # is no review.
        "item_status": "pending_rework_handover",
        "email_field": "warehouse_email",
        "group": "MR Warehouse",
        "inbox": "material_rework:warehouse",
    },
}

#: Used when no rule matches. The longest chain on purpose — see the module
#: docstring. Anything shorter would let an unrouted requester skip review.
DEFAULT_CHAIN = (STAGE_OM, STAGE_PPIC, STAGE_PRODUCTION)

#: How the chain was decided, recorded on the batch so a reviewer looking at a
#: batch months later can see why it took the path it took.
ORIGIN_REWORK_TYPE = "rework_type"
ORIGIN_REQUESTED_BY = "requested_by"
ORIGIN_DEFAULT = "default"


class RoutingError(ValueError):
    """A chain that cannot be run, raised by :func:`validate_chain`."""


# ── Validation ─────────────────────────────────────────────────────────────

def validate_chain(stages):
    """Return the chain as a clean list, or raise :class:`RoutingError`.

    Enforced here rather than only in the admin form so a chain written by a
    migration, a shell or a fixture cannot put a batch somewhere no screen
    will ever show it.
    """
    if not isinstance(stages, (list, tuple)):
        raise RoutingError("A flow must be a list of stages.")

    chain = [str(s).strip() for s in stages if str(s).strip()]
    if not chain:
        raise RoutingError("A flow must have at least one stage.")

    unknown = [s for s in chain if s not in ALL_STAGES]
    if unknown:
        raise RoutingError(
            "Unknown stage(s): %s. Valid stages are %s."
            % (", ".join(unknown), ", ".join(ALL_STAGES))
        )

    if len(set(chain)) != len(chain):
        seen, repeats = set(), []
        for stage in chain:
            if stage in seen and stage not in repeats:
                repeats.append(STAGE_LABELS[stage])
            seen.add(stage)
        raise RoutingError(
            "A stage cannot appear twice in one flow: %s." % ", ".join(repeats)
        )

    terminals = [s for s in chain if s in TERMINAL_STAGES]
    if not terminals:
        raise RoutingError(
            "A flow must end at %s or %s — that is where the material actually moves."
            % (STAGE_LABELS[STAGE_PRODUCTION], STAGE_LABELS[STAGE_WAREHOUSE])
        )
    if len(terminals) > 1:
        raise RoutingError(
            "A flow can end at only one of %s or %s."
            % (STAGE_LABELS[STAGE_PRODUCTION], STAGE_LABELS[STAGE_WAREHOUSE])
        )
    if chain[-1] not in TERMINAL_STAGES:
        raise RoutingError(
            "%s must be the last stage; %s cannot come after it."
            % (STAGE_LABELS[terminals[0]], STAGE_LABELS[chain[-1]])
        )

    return chain


# ── Resolution ─────────────────────────────────────────────────────────────

def resolve(requested_by, rework_type):
    """The chain a new batch should follow, and why.

    Returns ``(chain, origin, rule)``. The rework type wins outright over the
    requester: assigning a type such as Damage is how a batch is sent straight
    to whoever should handle it, so it has to be able to override the
    requester's longer flow rather than being merged into it.
    """
    from .models import RoutingRule

    for candidate, origin in (
        (RoutingRule.for_rework_type(rework_type), ORIGIN_REWORK_TYPE),
        (RoutingRule.for_requested_by(requested_by), ORIGIN_REQUESTED_BY),
    ):
        if candidate is None:
            continue
        try:
            return validate_chain(candidate.stages), origin, candidate
        except RoutingError:
            # A rule saved before a stage was renamed, or edited straight in
            # the database. Skipped rather than raised: an unroutable rule
            # must not make uploading impossible, and the fallback below
            # reviews more heavily than any real rule would.
            continue

    return list(DEFAULT_CHAIN), ORIGIN_DEFAULT, None


# ── Walking a chain ────────────────────────────────────────────────────────

def first_stage(chain):
    """Where a batch on this chain starts."""
    return chain[0] if chain else None


def next_stage(chain, current):
    """The stage after ``current``, or None at the end of the chain.

    Returns None for a ``current`` that is not on the chain at all, which is
    the honest answer for a batch whose chain was edited underneath it — the
    caller reports it rather than guessing a destination.
    """
    chain = list(chain or [])
    if current not in chain:
        return None
    index = chain.index(current)
    return chain[index + 1] if index + 1 < len(chain) else None


def is_terminal(stage):
    return stage in TERMINAL_STAGES


def terminal_of(chain):
    """The stage a chain ends at, or None if it has no terminal."""
    for stage in reversed(list(chain or [])):
        if stage in TERMINAL_STAGES:
            return stage
    return None


def label(stage):
    return STAGE_LABELS.get(stage, stage or "—")


def describe(chain):
    """A chain as people read it: "Order Management → PPIC → Production"."""
    return " → ".join(label(s) for s in (chain or [])) or "—"


def batch_status_for(stage):
    meta = STAGE_META.get(stage)
    return meta["batch_status"] if meta else ""


def item_status_for(stage):
    meta = STAGE_META.get(stage)
    return meta["item_status"] if meta else ""


def email_field_for(stage):
    meta = STAGE_META.get(stage)
    return meta["email_field"] if meta else ""


def group_for(stage):
    meta = STAGE_META.get(stage)
    return meta["group"] if meta else ""
