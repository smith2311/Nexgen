"""
reconciliation/classify.py — the reason-remark rules.
=============================================================================
Works out where a document lives (``Presence`` — the G/E/W map), how far its
three copies disagree (``MaxGap``), and then which bucket it belongs in: a
Segment, a GSTR-1 section and the Reason Remark a filing team actually reads.

── Everything compares TAXABLE value ───────────────────────────────────────
Document value includes tax and other charges, and the three sources do not
always break those down the same way, so comparing document values reports
disagreements that are not disagreements. Checked against the real 10,424-row
export: 234 ZFC challans differ by thousands on document value and match **to
the paisa** on taxable value. Every source that carries a document value in
that file also carries a taxable value, so there is nothing to fall back to and
no reason to.

This is also why the portal's own ``GstVsEwbSection = EwbMismatched`` verdict
is no longer taken at face value: its reason string is literally
``Document Value = N`` — it is a document-value comparison, which is exactly
the comparison we no longer make. The portal's *date* mismatch verdict is kept,
because a date is a date.

── Order matters ───────────────────────────────────────────────────────────
Rules are evaluated most-specific first, so a generic condition can never
overwrite a document-type rule:

  1. Facts — Presence and MaxGap, always, for every row.
  2. A classification pinned by hand.
  3. Exceptions that are not about amounts: cancellations, a document filed
     nowhere, Part-B never updated.
  4. **Document-type rules** — ZCPU, ZSCE, ZSP.
  5. Source availability — is a mandatory source missing?
  6. The three-way taxable comparison, with a Rs 10 tolerance.
  7. Everything else: challans, e-way-bill-only documents, services, scrap,
     consignment, low-value invoices, notes and exports.

── The vocabulary ──────────────────────────────────────────────────────────
A row that reconciles cleanly is labelled by the most specific rule that
applies to it: the named document-type rules use their own wording, a row
covered by the billing-type master uses that master's Final Remark, and
anything else is plain ``OK``. A row whose applicable sources disagree by more
than the tolerance is ``Amount Mismatch``.

The Segment / GSTR-1 vocabulary (B2B, B2CL, B2CL_CDNUR, B2CS, CDNR, DDNR,
WOPAY, WPAY, ITC_Rev, CONSG, JOBWORK, LBT, INVOICE CANCELLED,
WOPAY-INVOICE CANCELLED) comes from the source workbook's own Dashboard pivot:

* **WPAY / WOPAY** — ``GstTransactionType`` EXPWP / EXPWOP. SEZWOP folds into
  WOPAY as the closest match: it is also "without payment".
* **B2CL vs B2CS** — an inter-state B2C invoice above Rs 2,50,000 is B2CL.
  There is a clean gap in the data at exactly that threshold.
* **CDNR / DDNR** — a credit or debit note against a registered counterparty.
  Against an unregistered one it goes to B2CL_CDNUR regardless.
* **CONSG** — ZCFU (Consignment Fill-Up) or ZCPU (Consignment Pick-Up).
* ZSTI, ZSFC, ZSD and ZFC have no distinct bucket in the source pivot's
  vocabulary and fall through to LBT. Worth checking against your SAP config.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from . import schema

# The nationwide e-way bill threshold. Below this a supply invoice simply does
# not need one, so its absence is not a gap.
EWB_THRESHOLD = Decimal("50000")

# An inter-state B2C supply above this is B2CL rather than B2CS.
B2CL_THRESHOLD = Decimal("250000")

# How far the applicable sources may disagree before it is a mismatch rather
# than rounding. Rupees, on taxable value.
AMOUNT_TOLERANCE = Decimal("10")

ZERO = Decimal("0")

# ── the remark vocabulary ────────────────────────────────────────────────────
OK = "OK"
AMOUNT_MISMATCH = "Amount Mismatch"
UNCLASSIFIED = "Unclassified - review"
# The shipped wording for "billed or issued, e-way bill not raised yet". Each
# billing-type master row can override it via `pending_remark` — the Gujarat
# section's status now comes from the master, not from this constant.
DISPATCH_PENDING = "Dispatched Pending"

# Document-type rules name their own outcomes.
ZCPU_EWB_MISSING = "EWB not generated"
ZSCE_NO_EWB = "All OK - EWB NA"
ZSP_EWB_MISSING = "EWAY MISSING - MANDATORY - JOBWORK"
ZSP_ALL_FILED = "All Filed OK"

HEALTH_OK = "ok"
HEALTH_WARN = "warn"
HEALTH_BAD = "bad"

# The keys classify() decides. Everything else on a record is either from the
# file or audit.
DERIVED_KEYS = ("Presence", "ReasonRemark", "Segment", "Gstr1Section", "Health", "MaxGap")

SOURCES = ("GST", "EINV", "EWB")
SOURCE_LABELS = {"GST": "GSTR-1", "EINV": "E-Invoice", "EWB": "E-Way Bill"}


# ── small helpers ────────────────────────────────────────────────────────────

def _blank(value):
    return value is None or not str(value).strip()


def _text(values, column):
    value = values.get(column)
    return "" if value is None else str(value)


def _eq(value, expected):
    return (value or "").strip().casefold() == expected.casefold()


def _first_non_blank(*values):
    for value in values:
        if not _blank(value):
            return str(value).strip()
    return None


def _dec(value):
    """A Decimal, or None. Never raises on a stray string in a numeric column."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def entity_state_code(gstin):
    """First two digits of a GSTIN are the state code — 24 is Gujarat."""
    if not gstin or len(gstin) < 2:
        return None
    try:
        return int(gstin[:2])
    except ValueError:
        return None


def _section_for_challan(rule):
    """Which GSTR-1 bucket a master-covered Challan belongs to.

    FOC (ZFC, ZSFC) and the DC/warranty Challan (ZSD) have no distinct bucket
    in the source pivot's vocabulary, so they fold into LBT as the closest
    "miscellaneous Challan" bucket.
    """
    remark = rule.final_remark or ""
    return "CONSG" if "consgn" in remark.casefold() else "LBT"


def _note_section(is_credit_note, bill_to_gstin):
    """Where a credit or debit note goes: CDNR/DDNR against a registered
    counterparty, B2CL_CDNUR against an unregistered one."""
    if not _blank(bill_to_gstin):
        return "CDNR" if is_credit_note else "DDNR"
    return "B2CL_CDNUR"


def _b2c_section(default, gst_value, pos, entity_state):
    """B2CL when the supply is inter-state and above the threshold."""
    inter_state = (pos is not None and entity_state is not None
                   and pos != entity_state)
    return "B2CL" if (inter_state and gst_value > B2CL_THRESHOLD) else (default or "B2CS")


def _gstr1_section(*, gst_txn_type, is_note, is_credit_note, bill_to_gstin,
                   gst_value, pos, entity_state, sales_types=None):
    """The GSTR-1 section a document belongs to, worked out once.

    Sharing one answer between every rule below is deliberate: an earlier
    version hard-coded "B2B" on its fully-matched shortcut, which mislabelled
    large-value B2C invoices as B2B whenever all three sources happened to
    agree — the rows least likely to be looked at twice.

    The mapping comes from the sales-type master (see sales_types.py). A code
    the master does not cover falls through to the built-in rules below, so an
    unfamiliar transaction type still classifies — conservatively — instead of
    failing.
    """
    code = (gst_txn_type or "").strip().upper()
    rule = (sales_types or {}).get(code)

    if rule is not None:
        # Export business outranks the note rule, which is how the module has
        # always behaved: a credit note against an export is still export.
        if rule.is_export:
            return rule.gstr1_section
        if is_note:
            return _note_section(is_credit_note, bill_to_gstin)
        if rule.is_b2c:
            return _b2c_section(rule.gstr1_section, gst_value, pos, entity_state)
        return rule.gstr1_section

    # ── no row in the master: the original built-in rules ────────────────────
    if _eq(gst_txn_type, "EXPWP"):
        return "WPAY"
    if _eq(gst_txn_type, "EXPWOP") or _eq(gst_txn_type, "SEZWOP"):
        return "WOPAY"
    if is_note:
        return _note_section(is_credit_note, bill_to_gstin)
    if _eq(gst_txn_type, "B2C"):
        return _b2c_section("B2CS", gst_value, pos, entity_state)
    return "B2B"


# ── the row under consideration ──────────────────────────────────────────────

@dataclass
class Context:
    """Everything the rules need about one row, worked out once.

    Threading twenty keyword arguments through the rule functions was how the
    previous version did it, and it made the order of the rules — the thing
    that actually matters here — hard to see past the plumbing.
    """
    values: dict
    rule: Optional[object] = None
    bt_code: Optional[str] = None

    # Presence — the MAP. True when the source carries this document at all,
    # by ANY evidence: a date, a value, or its own identifier. Deliberately
    # not "has a value", so a document that exists in a source but was filed
    # without a figure still maps as present rather than vanishing.
    g: bool = False
    e: bool = False
    w: bool = False
    presence: str = "---"

    # Taxable values. The only figures reconciliation compares.
    gst: Optional[Decimal] = None
    einv: Optional[Decimal] = None
    ewb: Optional[Decimal] = None
    max_gap: Decimal = ZERO

    doc_type: str = ""
    is_invoice: bool = False
    is_credit_note: bool = False
    is_debit_note: bool = False
    is_note: bool = False
    is_challan: bool = False

    segment: str = "GST"
    section: str = "B2B"

    # The sales-type master, for the section mapping.
    sales_types: dict = field(default_factory=dict)

    # Filled in as the rules run, so the detail modal can say which rule fired.
    rule_id: str = ""
    compared: list = field(default_factory=list)

    @property
    def code(self):
        return (self.bt_code or "").upper()

    @property
    def gst_value(self):
        return self.gst if self.gst is not None else ZERO

    def present(self, source):
        return {"GST": self.g, "EINV": self.e, "EWB": self.w}[source]

    def taxable(self, source):
        return {"GST": self.gst, "EINV": self.einv, "EWB": self.ewb}[source]

    def applicable(self, source):
        """Whether this source is expected to carry the document's real value.

        With no billing-type rule, every source that is present counts. With
        one, a source the master marks NA is left out — a Branch Transfer
        challan is deliberately Rs 0 on GST while its e-way bill carries the
        real goods value, and comparing those two reported 711 perfectly
        normal rows as mismatches.
        """
        if self.rule is None:
            return True
        return {
            "GST": self.rule.gst_applicable,
            "EINV": self.rule.einv_applicable,
            "EWB": self.rule.ewb_applicable,
        }[source]

    def comparable(self):
        """``[(source, taxable value)]`` for the sources that must agree."""
        return [(source, self.taxable(source) or ZERO)
                for source in SOURCES
                if self.present(source) and self.applicable(source)]

    def gap(self, pairs=None):
        """The spread across ``pairs``; zero when fewer than two can be compared."""
        pairs = self.comparable() if pairs is None else pairs
        if len(pairs) < 2:
            return ZERO
        figures = [value for _, value in pairs]
        return max(figures) - min(figures)

    def matches(self, pairs=None):
        """True when everything comparable agrees within the tolerance."""
        return self.gap(pairs) <= AMOUNT_TOLERANCE


def build_context(values, rules, sales_types=None):
    """Everything in ``Context``, from one row of parsed values."""
    ctx = Context(values=values)
    ctx.sales_types = sales_types or {}

    # ── the MAP ──────────────────────────────────────────────────────────────
    # A source is present if it carries ANY sign of the document. A missing GST
    # value must not remove the row from the map; it is a fact about the row
    # for the rules below to deal with.
    ctx.g = (values.get("GstDocumentDate") is not None
             or values.get("GSTDocumentTaxableValue") is not None
             or values.get("GSTDocumentValue") is not None
             or not _blank(values.get("GSTIrn")))
    ctx.e = (values.get("EINVDocumentDate") is not None
             or not _blank(values.get("EINVIrn"))
             or values.get("EINVDocumentTaxableValue") is not None)
    ctx.w = (values.get("EWBDocumentDate") is not None
             or not _blank(values.get("EwbNumber"))
             or values.get("EWBDocumentTaxableValue") is not None)
    ctx.presence = ("G" if ctx.g else "-") + ("E" if ctx.e else "-") + ("W" if ctx.w else "-")

    # ── taxable values, and nothing else ─────────────────────────────────────
    ctx.gst = _dec(values.get("GSTDocumentTaxableValue"))
    ctx.einv = _dec(values.get("EINVDocumentTaxableValue"))
    ctx.ewb = _dec(values.get("EWBDocumentTaxableValue"))

    # MaxGap is reported across every source that carries the document, whether
    # or not the master expects it to agree — it answers "how far apart are
    # these copies", which is a question about the row, not a judgement of it.
    # A zero counts: GST 0 against a valued e-way bill is exactly the gap worth
    # looking at.
    present_values = [ctx.taxable(source) or ZERO
                      for source in SOURCES if ctx.present(source)]
    ctx.max_gap = (max(present_values) - min(present_values)
                   if len(present_values) > 1 else ZERO)

    ctx.doc_type = _text(values, "DocumentType").strip()
    folded = ctx.doc_type.casefold()
    ctx.is_invoice = folded == "invoice"
    ctx.is_credit_note = folded == "credit note"
    ctx.is_debit_note = folded == "debit note"
    ctx.is_note = ctx.is_credit_note or ctx.is_debit_note
    ctx.is_challan = folded == "challan"

    # Whichever of GSTCustom1 / EINVCustom1 / EWBCustom1 actually carries the
    # code — the export puts it under the source that raised the document.
    ctx.bt_code = _first_non_blank(values.get("GSTCustom1"),
                                   values.get("EINVCustom1"),
                                   values.get("EWBCustom1"))
    ctx.rule = (rules or {}).get(ctx.code) if ctx.bt_code else None

    ctx.section = _gstr1_section(
        gst_txn_type=values.get("GstTransactionType"),
        is_note=ctx.is_note, is_credit_note=ctx.is_credit_note,
        bill_to_gstin=values.get("GSTBillToGstin"),
        gst_value=ctx.gst_value, pos=values.get("GSTPos"),
        entity_state=entity_state_code(values.get("EntityGstin")),
        sales_types=ctx.sales_types,
    )
    return ctx


def _result(ctx, remark, segment, section, health, rule_id):
    ctx.rule_id = rule_id
    ctx.compared = ctx.comparable()
    return {
        "Presence": ctx.presence,
        "ReasonRemark": remark,
        "Segment": segment,
        "Gstr1Section": section,
        "Health": health,
        "MaxGap": ctx.max_gap,
    }


# ── the classifier ───────────────────────────────────────────────────────────

def classify_values(values, rules=None, context=None, sales_types=None):
    """Classify one row; returns the six derived columns.

    Pass ``context`` (a fresh ``Context``) to find out *why* — it comes back
    carrying ``rule_id`` and ``compared``, which is what the document detail
    modal shows.
    """
    ctx = build_context(values, rules or {}, sales_types or {})
    result = _run(ctx)
    if context is not None:
        context.__dict__.update(ctx.__dict__)
    return result


def _run(ctx):
    """The rule chain, in the order the module docstring lists."""
    values = ctx.values

    def done(remark, segment, section, health, rule_id):
        return _result(ctx, remark, segment, section, health, rule_id)

    # ── 2. a classification pinned by hand ───────────────────────────────────
    # The facts above are still refreshed; they are not what was pinned.
    if values.get("ManualReason") and not _blank(values.get("ReasonRemark")):
        return done(values.get("ReasonRemark"), values.get("Segment"),
                    values.get("Gstr1Section"), values.get("Health"), "pinned")

    # ── 3. exceptions that are not about amounts ─────────────────────────────
    outcome = _hard_exceptions(ctx, done)
    if outcome is not None:
        return outcome

    # ── 4. document-type rules — ZCPU, ZSCE, ZSP ─────────────────────────────
    if ctx.rule is not None:
        segment, section = _rule_buckets(ctx)
        outcome = _document_type_rules(ctx, done, segment, section)
        if outcome is not None:
            return outcome

    # ── 5 & 6. source availability, then the three-way taxable comparison ────
    if ctx.rule is not None:
        return _by_master(ctx, done)

    return _without_master(ctx, done)


def _hard_exceptions(ctx, done):
    """Cancellations, a document filed nowhere, Part-B — none amount-based."""
    values = ctx.values

    # A source is present but carries no taxable value: there is nothing to
    # reconcile against, and that is worth saying rather than silently
    # comparing zero.
    #
    # Segment is NAP, not REVIEW: the user's rule is that any document carrying
    # no GST value belongs on the Non-Applicable side of the filing summary.
    # The remark is still kept honest ("review") so the row surfaces as an
    # exception on the Records screen; it just does not inflate the GST
    # segment's totals in the summary.
    if ctx.g and ctx.gst is None and ctx.applicable("GST"):
        return done("GST value missing - review", "NAP", "UNMAPPED",
                    HEALTH_BAD, "gst-value-missing")

    if _eq(values.get("GSTDocumentStatus"), "Cancelled") and not ctx.e:
        return done("Invoice Cancel", "GST_Hold", "WOPAY-INVOICE CANCELLED",
                    HEALTH_BAD, "gst-cancelled")

    if _eq(values.get("EINVDocumentStatus"), "Cancelled"):
        return done("EINV Cancelled - reversal booked", "NAP", "INVOICE CANCELLED",
                    HEALTH_WARN, "einv-cancelled")

    # The portal's DATE verdict is kept; its VALUE verdict is not — that one
    # compares document values, which is the comparison this module no longer
    # makes. See the module docstring.
    ewb_reason = _text(values, "GstVsEwbReason").casefold()
    if _eq(values.get("GstVsEwbSection"), "EwbMismatched") and "document date" in ewb_reason:
        return done("Date mismatch - GST vs EWB", "GST", "EXCEPTION",
                    HEALTH_BAD, "portal-date-mismatch")

    einv_reason = _text(values, "GstVsEinvReason").casefold()
    if (_eq(values.get("GstVsEinvSection"), "EinvMismatched")
            and "document value" not in einv_reason):
        return done("Mismatch - GST vs EINV", "GST", "EXCEPTION",
                    HEALTH_BAD, "portal-einv-mismatch")

    # Filed at the IRP but not in GSTR-1 — only a problem where GSTR-1 was
    # expected. A billing type the master marks GST = NA (ZSP, ZJBE, the
    # branch transfers) is not "missing" from a return it never belonged in.
    if not ctx.g and ctx.e and ctx.applicable("GST"):
        return done("Missing in GSTR-1 - EINV generated", "GST", "EXCEPTION",
                    HEALTH_BAD, "missing-in-gstr1")

    if ctx.w and _eq(values.get("PartBStatus"), "N"):
        return done("Part-B not updated", "GST", "EXCEPTION", HEALTH_WARN, "part-b")

    return None


def _rule_buckets(ctx):
    """The Segment and GSTR-1 section a master-covered row belongs to."""
    rule = ctx.rule
    if rule.doc_type == "Challan":
        return "NAP", _section_for_challan(rule)
    if rule.doc_type == "Jobwork":
        return "NAP", "JOBWORK"
    if rule.doc_type == "Exempt":
        # No exempt-coded row exists in either sample export to check this
        # bucket against — verify once one shows up in a real filing.
        return "GST", "EXEMPT"
    # A billing type whose master row marks GST = NA has no GST filing to
    # belong to, however the SAP side spells it (Invoice, Service, Scrap
    # Invoice). It is Non-Applicable on the summary.
    if not rule.gst_applicable:
        return "NAP", ctx.section
    return "GST", ctx.section


def _document_type_rules(ctx, done, segment, section):
    """ZCPU, ZSCE and ZSP — the rules that outrank everything generic.

    Each returns None when its own conditions do not apply, so the row carries
    on to the ordinary master path rather than being swallowed here.
    """
    rule = ctx.rule

    # ── ZCPU — Consignment Pick-Up ───────────────────────────────────────────
    # E-Invoice is NA for this billing type, so the e-way bill is the only
    # thing that can be missing. Say that plainly; never "Dispatched Pending",
    # which describes an invoice waiting on dispatch, not a pick-up whose
    # e-way bill was never raised.
    if ctx.code == "ZCPU":
        if rule.ewb_applicable and not ctx.w:
            return done(ZCPU_EWB_MISSING, segment, section, HEALTH_WARN, "ZCPU/ewb-missing")
        return None

    # ── ZSCE — Scrap Invoice ─────────────────────────────────────────────────
    # The master marks EWB Applicable while its own Final Remark says
    # "EWB-NA-Scrap"; the two contradict each other, so ZSCE is answered here
    # rather than by the generic missing-source logic.
    if ctx.code == "ZSCE":
        if ctx.g and ctx.e and ctx.w and ctx.matches():
            return done(OK, segment, section, HEALTH_OK, "ZSCE/all-three-match")
        if ctx.g and ctx.e and not ctx.w and ctx.matches():
            # A missing e-way bill is not an amount mismatch here.
            return done(ZSCE_NO_EWB, segment, section, HEALTH_OK, "ZSCE/no-ewb")
        return None

    # ── ZSP — Challan / Job Work ─────────────────────────────────────────────
    # The e-way bill is mandatory for job work. This outranks the generic
    # missing-e-way-bill handling, which would otherwise call it "Dispatched
    # Pending" — a job work challan without one is not pending, it is wrong.
    # When the e-way bill is present the row reads "All Filed OK" (the business
    # team's wording), rather than the plain "OK" the generic path produces.
    if ctx.code == "ZSP":
        if rule.ewb_applicable and not ctx.w:
            return done(ZSP_EWB_MISSING, segment, section, HEALTH_BAD, "ZSP/ewb-missing")
        if ctx.w and ctx.matches():
            return done(ZSP_ALL_FILED, segment, section, HEALTH_OK, "ZSP/ewb-present")
        return None

    return None


def _by_master(ctx, done):
    """A row the billing-type master covers."""
    rule = ctx.rule
    segment, section = _rule_buckets(ctx)

    # Orthogonal to billing type: an EWB-only Challan moving B2B goods is an
    # ITC reversal, which the master has no column to express.
    if ctx.presence == "--W" and ctx.is_challan and _eq(
            ctx.values.get("EWBTransactionType"), "B2B"):
        return done("All Filed Ok-EWB-ITC_REV", "ITC_Rev", "ITC_Rev",
                    HEALTH_OK, "itc-reversal")

    if not rule.doc_type_ok(ctx.doc_type):
        found = ctx.doc_type if ctx.doc_type else "(blank)"
        return done(
            f"Wrong DocumentsType - billing type {ctx.bt_code} ({rule.description}) "
            f"expects {rule.expected_label}, found {found}",
            segment, section, HEALTH_BAD, "wrong-doc-type")

    # ── 5. is a mandatory source missing? ────────────────────────────────────
    # Only a MISSING mandatory source counts. A source the master marks NA but
    # which is present anyway is never flagged: at least eight billing types
    # carry an NA source on almost every row, and flagging the extra would put
    # roughly a fifth of a normal import into the exception queue.
    #
    # The Rs 50,000 threshold is a supply-invoice rule. A Challan exists
    # precisely because goods are moving, so it needs an e-way bill whenever it
    # exists — no low-value grace period.
    # ZSCE is excluded outright: the master marks EWB Applicable while its own
    # Final Remark says "EWB-NA-Scrap", and the rule above has already decided
    # that a scrap sale without an e-way bill is fine. Leaving it in the
    # missing list would let the generic "Dispatched Pending" wording answer a
    # ZSCE row whose real problem is that two of its figures disagree.
    ewb_carve_out = ctx.code == "ZSCE" or (
        rule.ewb_applicable and not ctx.w
        and ctx.gst_value < EWB_THRESHOLD and not ctx.is_challan)

    missing = []
    if rule.gst_applicable and not ctx.g:
        missing.append("GST")
    if rule.einv_applicable and not ctx.e:
        missing.append("E-Invoice")
    if rule.ewb_applicable and not ctx.w and not ewb_carve_out:
        missing.append("E-Way Bill")

    if missing:
        if missing == ["E-Way Bill"]:
            # A Challan with no value recorded anywhere is an empty placeholder
            # document with nothing to reconcile, not a dispatch waiting to
            # happen.
            if ctx.is_challan and not any(
                    (ctx.taxable(source) or ZERO) != ZERO for source in SOURCES):
                return done("No value recorded - EWB not generated", segment, section,
                            HEALTH_OK, "empty-challan")
            # Same label whether it is an Invoice or a Challan waiting on its
            # e-way bill: billed or issued, dispatch not documented. The exact
            # wording is master-driven — a billing-type row may set its own
            # `pending_remark` and the Gujarat section's label (used to be the
            # hard-coded string "Dispatch Pending") now flows from there.
            if ctx.is_invoice or ctx.is_challan:
                remark = (rule.pending_remark or "").strip() or DISPATCH_PENDING
                return done(remark, segment, section,
                            HEALTH_WARN, "dispatch-pending")

        return done(
            f"Missing {', '.join(missing)} - billing type {ctx.bt_code} requires it",
            segment, section, HEALTH_BAD, "missing-mandatory")

    # ── 6. the three-way taxable comparison ──────────────────────────────────
    # Filed under GST/EXCEPTION rather than the billing type's own buckets, so
    # every amount mismatch lands in one place on the filing summary however
    # it arose.
    if not ctx.matches():
        return done(AMOUNT_MISMATCH, "GST", "EXCEPTION", HEALTH_BAD, "amount-mismatch")

    # ── 7. it reconciles. The master's own wording is the most specific thing
    #       we have to say about it.
    return done(rule.final_remark or OK, segment, section, rule.health or HEALTH_OK,
                "master-final-remark")


def _without_master(ctx, done):
    """A row whose billing-type code is blank, or not one the master covers."""
    values = ctx.values
    section = ctx.section
    ewb_txn_type = values.get("EWBTransactionType")
    category = _text(values, "GSTCustom1").strip().upper()

    # ── 6. the three-way taxable comparison ──────────────────────────────────
    # With no master to say which sources should agree, every source that
    # carries the document is compared.
    if not ctx.matches():
        return done(AMOUNT_MISMATCH, "GST", "EXCEPTION", HEALTH_BAD, "amount-mismatch")

    # ── Challan with no billing-type match ───────────────────────────────────
    # Same policy as the master-driven Challan branch: a Challan needs an
    # e-way bill whenever it exists, and an e-invoice is not expected on one.
    # Always NAP, never GST/REVIEW.
    if ctx.is_challan:
        ch_section = "CONSG" if category in ("ZCFU", "ZCPU") else "LBT"
        if ctx.presence == "--W" and _eq(ewb_txn_type, "JW"):
            return done("All Filed Ok-EWB-Jobwork", "NAP", "JOBWORK", HEALTH_OK,
                        "challan-jobwork")
        if ctx.presence == "--W" and _eq(ewb_txn_type, "B2B"):
            return done("All Filed Ok-EWB-ITC_REV", "ITC_Rev", "ITC_Rev", HEALTH_OK,
                        "challan-itc-reversal")
        if ctx.w:
            return done(f"All Filed Ok-EWB-{ch_section}", "NAP", ch_section,
                        HEALTH_OK, "challan-ewb")
        if not any((ctx.taxable(source) or ZERO) != ZERO for source in SOURCES):
            return done("No value recorded - EWB not generated", "NAP", ch_section,
                        HEALTH_OK, "empty-challan")
        return done("Dispatched Pending", "NAP", ch_section, HEALTH_WARN,
                    "challan-dispatch-pending")

    # ── documents on the e-way bill portal only ──────────────────────────────
    if ctx.presence == "--W":
        if _eq(ewb_txn_type, "JW"):
            return done("All Filed Ok-EWB-Jobwork", "NAP", "JOBWORK", HEALTH_OK,
                        "ewb-only-jobwork")
        return done("EWB only - review", "NAP", "EWB ONLY", HEALTH_WARN, "ewb-only")

    if ctx.is_note:
        return done("All Filed Ok-EWB-NA-CN", "GST", section, HEALTH_OK, "note")

    if section in ("WPAY", "WOPAY"):
        # A cancelled export invoice is still worth a look.
        cancelled = _eq(values.get("GSTDocumentStatus"), "Cancelled")
        return done("Invoice Cancel" if cancelled else OK, "GST", section,
                    HEALTH_BAD if cancelled else HEALTH_OK, "export")

    # ── filed on all three, and the taxable values agree ─────────────────────
    if ctx.presence == "GEW":
        return done(OK, "GST", section, HEALTH_OK, "all-three-match")

    # ── GST + e-invoice, e-way bill legitimately absent ──────────────────────
    if ctx.presence == "GE-":
        hsn = _text(values, "GSTHsnOrSacCode").strip()
        # HSN 99xx is the services range; a service has nothing to move.
        if hsn.startswith("99") or category == "ZSER":
            return done("All Filed Ok-EWB-NA(Services)", "GST", section, HEALTH_OK,
                        "services")
        if "waste" in _text(values, "GSTDescription").casefold():
            return done("All Filed Ok-EWB-NA(Scrap)", "GST", section, HEALTH_OK, "scrap")
        if category in ("ZCSA", "ZCPU"):
            return done("All Filed Ok-EWB-NA(Consign.)", "GST", section, HEALTH_OK,
                        "consignment")
        if ctx.gst_value < EWB_THRESHOLD:
            return done("All Filed Ok-EWB-NA(<50K)", "GST", section, HEALTH_OK,
                        "below-threshold")

        sec9 = _text(values, "GSTCustom9").strip().upper()
        if sec9 in ("DCSAMPLE", "CHL"):
            return done("Only Invoice Material on DC", "GST", section, HEALTH_WARN,
                        "material-on-dc")

    if ctx.g and not ctx.w and ctx.is_invoice and ctx.gst_value >= EWB_THRESHOLD:
        return done("Dispatched Pending", "GST", section, HEALTH_WARN,
                    "invoice-dispatch-pending")

    if ctx.presence == "G-W":
        return done("EINV Not Applicable", "GST", section, HEALTH_OK, "einv-na")

    if ctx.presence == "G--" and ctx.is_invoice:
        return done("Dispatched Pending", "GST", section, HEALTH_WARN,
                    "invoice-dispatch-pending")

    return done(UNCLASSIFIED, "REVIEW", "UNMAPPED", HEALTH_BAD, "unclassified")


# ── why a row says what it says ──────────────────────────────────────────────
#
# One sentence per rule id, for the document detail modal. Written here rather
# than in the view so the explanation sits beside the rule it explains — the
# two go out of step the moment they live in different files.

RULE_EXPLANATIONS = {
    "pinned": "Someone pinned this classification by hand, so the rules leave it alone.",
    "gst-value-missing": "GSTR-1 carries this document but no taxable value, so there is nothing to reconcile against.",
    "gst-cancelled": "Cancelled in GSTR-1, with no e-invoice behind it.",
    "einv-cancelled": "Cancelled at the IRP, so a reversal is expected.",
    "portal-date-mismatch": "The portal itself reports the GST and e-way bill dates as different.",
    "portal-einv-mismatch": "The portal itself reports GSTR-1 and the e-invoice as mismatched.",
    "missing-in-gstr1": "An e-invoice exists but the document was never filed in GSTR-1.",
    "part-b": "An e-way bill exists but its Part-B transport details were never added.",
    "ZCPU/ewb-missing": "ZCPU is a consignment pick-up. Its e-invoice is not applicable, so the e-way bill is the only thing that can be missing — and it is.",
    "ZSCE/all-three-match": "ZSCE scrap sale: GSTR-1, the e-invoice and the e-way bill all carry it and their taxable values agree.",
    "ZSCE/no-ewb": "ZSCE scrap sale: GSTR-1 and the e-invoice agree, and a scrap sale does not need an e-way bill.",
    "ZSP/ewb-missing": "ZSP is a job-work challan, where the e-way bill is mandatory. None is present.",
    "ZSP/ewb-present": "ZSP job-work challan with its mandatory e-way bill present, and nothing left that disagrees.",
    "itc-reversal": "An e-way-bill-only challan moving B2B goods — an ITC reversal.",
    "wrong-doc-type": "The billing-type master expects a different document type from the one filed.",
    "empty-challan": "A challan with no value recorded in any source — a placeholder, not a dispatch.",
    "dispatch-pending": "Billed or issued, but the e-way bill has not been raised yet.",
    "missing-mandatory": "A source the billing-type master marks mandatory is not present.",
    "amount-mismatch": "The sources that should agree differ by more than the tolerance.",
    "master-final-remark": "Everything the billing-type master requires is present and the taxable values agree.",
    "challan-jobwork": "An e-way-bill-only challan moving job-work goods.",
    "challan-itc-reversal": "An e-way-bill-only challan moving B2B goods — an ITC reversal.",
    "challan-ewb": "A challan with its e-way bill raised.",
    "challan-dispatch-pending": "A challan whose goods are not yet covered by an e-way bill.",
    "ewb-only-jobwork": "Present only on the e-way bill portal, moving job-work goods.",
    "ewb-only": "Present only on the e-way bill portal, with nothing to reconcile it against.",
    "note": "A credit or debit note, which does not move goods.",
    "export": "An export supply.",
    "all-three-match": "All three sources carry it and their taxable values agree.",
    "services": "A service (HSN 99xx), which has nothing to move.",
    "scrap": "Described as waste, so no e-way bill is expected.",
    "consignment": "A consignment document, where no e-way bill is expected.",
    "below-threshold": "Below the Rs 50,000 e-way bill threshold, so none is required.",
    "material-on-dc": "Invoiced material issued on a delivery challan.",
    "invoice-dispatch-pending": "An invoice above the e-way bill threshold with no e-way bill raised.",
    "einv-na": "Filed in GSTR-1 with an e-way bill, and no e-invoice was required.",
    "unclassified": "No rule matched this shape. Usually a billing code the master does not cover yet.",
}


def explain(rule_id):
    """The sentence behind a rule id, or a neutral fallback."""
    return RULE_EXPLANATIONS.get(rule_id, "Classified by the standard rules.")


# ── convenience wrappers ─────────────────────────────────────────────────────

def _record_values(record):
    values = {column: getattr(record, column, None) for column in schema.ALL_COLUMNS}
    values["ManualReason"] = record.ManualReason
    values["ReasonRemark"] = record.ReasonRemark
    values["Segment"] = record.Segment
    values["Gstr1Section"] = record.Gstr1Section
    values["Health"] = record.Health
    return values


def classify_record(record, rules=None, sales_types=None):
    """Classify a ``ReconRecord`` in place. Returns the derived dict."""
    derived = classify_values(_record_values(record), rules, sales_types=sales_types)
    for key, value in derived.items():
        setattr(record, key, value)
    return derived


def explain_record(record, rules=None, sales_types=None):
    """``(derived, context)`` — the verdict, and how it was reached.

    Used by the document detail modal, which exists to answer "why does this
    row say that". Re-running the classifier for one record is cheap and means
    the explanation is produced by the rules themselves rather than by a second
    description of them that could fall out of step.
    """
    ctx = Context(values={})
    derived = classify_values(_record_values(record), rules, context=ctx,
                              sales_types=sales_types)
    return derived, ctx


def load_sales_types():
    """``{CODE: SalesTypeRule}`` from the sales-type master table.

    Falls back to the shipped seed when the table is empty or not yet
    migrated, so the classifier is usable from a test or a shell before the
    first migration runs.
    """
    from .models import ReconSalesType
    from .sales_types import seed_rules

    try:
        rows = list(ReconSalesType.objects.filter(is_active=True))
    except Exception:
        rows = []
    if not rows:
        return {code.upper(): rule for code, rule in seed_rules().items()}
    return {row.code.upper(): row.as_rule() for row in rows}


def load_masters():
    """Both masters, as keyword arguments for ``classify_*``.

    One call so a caller cannot load one master and forget the other::

        classify_record(record, **load_masters())
    """
    return {"rules": load_rules(), "sales_types": load_sales_types()}


def load_rules():
    """``{CODE: BillingTypeRule}`` from the billing-type master table.

    Falls back to the shipped seed if the table has not been migrated yet, so
    the classifier is usable from a test or a shell before the first migration
    runs. Keys are upper-cased because the export's own casing is not
    guaranteed.
    """
    from .billing_types import seed_rules
    from .models import ReconBillingType

    try:
        rows = list(ReconBillingType.objects.filter(is_active=True))
    except Exception:
        rows = []
    if not rows:
        return {code.upper(): rule for code, rule in seed_rules().items()}
    return {row.code.upper(): row.as_rule() for row in rows}
