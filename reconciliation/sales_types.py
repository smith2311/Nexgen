"""
reconciliation/sales_types.py — the sales-type master.
=============================================================================
Which GSTR-1 section a document belongs to depends on how it was sold. The
export says so in ``GstTransactionType``: B2B, B2C, EXPWP, EXPWOP, SEZWOP, OTH.

That mapping used to be six ``if`` statements in classify.py, which meant two
things. A new transaction type — a scheme changes, SAP gains a code — could
only be handled by an engineer. And any code the list did not name fell through
to the final ``return "B2B"`` without saying so: in the two sample exports
**2,123 rows carry ``OTH``** and every one of them was being filed as B2B by
that fallback, invisibly.

So the mapping is a table. Add a row, and the next re-run of the rules files
those documents correctly; no release needed. The same shape as
billing_types.py, for the same reason.

── The three switches ──────────────────────────────────────────────────────
``gstr1_section`` is where documents of this type go.

``is_export`` marks the types whose section wins over the credit/debit-note
rule. A credit note against an export is still export business — that is how
the module has always behaved, and it is stated here rather than implied by the
order of some ``if`` statements.

``is_b2c`` turns on the B2CL/B2CS split: an inter-state B2C supply above
Rs 2,50,000 is B2CL, everything else B2CS. Only unregistered-consumer types
want it.

A code the table does not cover still falls through to the original built-in
rules, exactly as an unknown billing type does — so an unfamiliar export never
fails to classify, it just classifies conservatively until someone adds the row.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SalesTypeRule:
    """One row of the master, as the classifier consumes it."""

    code: str
    description: str
    gstr1_section: str
    is_export: bool = False
    is_b2c: bool = False
    notes: str = ""


# (code, description, gstr1_section, is_export, is_b2c, notes)
#
# Seeded to reproduce the behaviour the module already had, so turning the
# mapping into data changes no number on anybody's filing summary. OTH is the
# one worth a second look — see its note.
SEED_ROWS = (
    ("B2B", "Registered business supply", "B2B", False, False,
     "The default for anything sold to a GSTIN holder."),

    ("B2C", "Unregistered consumer supply", "B2CS", False, True,
     "Splits to B2CL when the supply is inter-state and above Rs 2,50,000."),

    ("EXPWP", "Export with payment of tax", "WPAY", True, False,
     "Export business, so this wins over the credit/debit-note rule."),

    ("EXPWOP", "Export without payment of tax", "WOPAY", True, False,
     "Export business, so this wins over the credit/debit-note rule."),

    ("SEZWOP", "SEZ supply without payment of tax", "WOPAY", True, False,
     "Folded into WOPAY as the closest match in the source pivot's own "
     "vocabulary: it is also 'without payment'."),

    ("OTH", "Other", "B2B", False, False,
     "2,123 rows in the two sample exports carry this, and every one was "
     "already being filed as B2B by the classifier's fallback. Seeded to keep "
     "that behaviour rather than change numbers silently — but if 'Other' "
     "should not be B2B on your return, this is the row to change."),
)


def seed_rules():
    """``{code: SalesTypeRule}`` straight from SEED_ROWS — used by the data
    migration that fills the table, and by tests that want the master without
    touching the database."""
    return {row[0]: SalesTypeRule(*row) for row in SEED_ROWS}
