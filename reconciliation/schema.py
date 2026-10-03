"""
reconciliation/schema.py — the shape of the Reconciliation Export workbook.
═════════════════════════════════════════════════════════════════════════════
Every one of the 204 columns in the "Reconciliation Export" sheet becomes its
own database column, spelled exactly as the source file spells it. Nothing is
renamed, merged or dropped: ``GSTDocumentValue`` and ``EINVDocumentValue`` stay
two separate fields rather than collapsing into one "value", because the whole
point of the module is comparing them.

This list is the single source of truth. ``models.py`` builds the model's
fields from it, the reader keys uploaded rows by it, and the exporter writes it
back out under the same headings — so the workbook, the table and the export can
never drift apart. It was generated from the real export
(``dummy_tempelate/reconciliation/ReconciliationHubReport-*.xlsx``), not typed
by hand.

Seven derived analytical columns sit on top of the 204 (Presence, ReasonRemark,
Segment, Gstr1Section, Health, MaxGap, ManualReason). That mirrors the source
workbook itself, whose own data tab carried Segment / GSTR1 / Reason Remarks
columns alongside the raw export fields.
"""

# ── The 204 source columns, in workbook order ────────────────────────────────
ALL_COLUMNS = (
    "EntityGstin",
    "EntityName",
    "DocumentNumber",
    "DocumentType",
    "GstDocumentDate",
    "EINVDocumentDate",
    "EWBDocumentDate",
    "GSTDocumentValue",
    "EINVDocumentValue",
    "EWBDocumentValue",
    "GSTDocumentTaxableValue",
    "EINVDocumentTaxableValue",
    "EWBDocumentTaxableValue",
    "GSTDocumentTaxValue",
    "EINVDocumentTaxValue",
    "EWBDocumentTaxValue",
    "GSTIrn",
    "EINVIrn",
    "EWBIrn",
    "EwbNumber",
    "PartBStatus",
    "GstTransactionType",
    "EINVTransactionType",
    "EWBTransactionType",
    "IsAmendment",
    "GSTReverseCharge",
    "EINVReverseCharge",
    "EWBReverseCharge",
    "GSTPos",
    "EINVPos",
    "EWBPos",
    "GSTBillFromGstin",
    "EINVBillFromGstin",
    "EWBBillFromGstin",
    "GSTBillFromTradeName",
    "EINVBillFromTradeName",
    "EWBBillFromTradeName",
    "GSTBillFromLegalName",
    "EINVBillFromLegalName",
    "EWBBillFromLegalName",
    "GSTBillToGstin",
    "EINVBillToGstin",
    "EWBBillToGstin",
    "GSTBillToTradeName",
    "EINVBillToTradeName",
    "EWBBillToTradeName",
    "GSTBillToLegalName",
    "EINVBillToLegalName",
    "EWBBillToLegalName",
    "GstVsEinvSection",
    "GstVsEinvReason",
    "GstVsEwbSection",
    "GstVsEwbReason",
    "EinvVsGstSection",
    "EinvVsGstReason",
    "EinvVsEwbSection",
    "EinvVsEwbReason",
    "EwbVsEinvSection",
    "EwbVsEinvReason",
    "EwbVsGstSection",
    "EwbVsGstReason",
    "GSTINStatus",
    "GSTINTaxpayerType",
    "GSTCustom1",
    "EINVCustom1",
    "EWBCustom1",
    "GSTCustom2",
    "EINVCustom2",
    "EWBCustom2",
    "GSTCustom3",
    "EINVCustom3",
    "EWBCustom3",
    "GSTCustom4",
    "EINVCustom4",
    "EWBCustom4",
    "GSTCustom5",
    "EINVCustom5",
    "EWBCustom5",
    "GSTCustom6",
    "EINVCustom6",
    "EWBCustom6",
    "GSTCustom7",
    "EINVCustom7",
    "EWBCustom7",
    "GSTCustom8",
    "EINVCustom8",
    "EWBCustom8",
    "GSTCustom9",
    "EINVCustom9",
    "EWBCustom9",
    "GSTCustom10",
    "EINVCustom10",
    "EWBCustom10",
    "GSTHsnOrSacCode",
    "EINVHsnOrSacCode",
    "EWBHsnOrSacCode",
    "GSTDescription",
    "EINVDescription",
    "EWBDescription",
    "GSTUqc",
    "EINVUqc",
    "EWBUqc",
    "GSTQuantity",
    "EINVQuantity",
    "EWBQuantity",
    "GSTPricePerQuantity",
    "EINVPricePerQuantity",
    "EWBPricePerQuantity",
    "GSTRate",
    "EINVRate",
    "EWBRate",
    "GSTCessRate",
    "EINVCessRate",
    "EWBCessRate",
    "GSTStateCessRate",
    "EINVStateCessRate",
    "EWBStateCessRate",
    "GSTCessNonAdvaloremRate",
    "EINVCessNonAdvaloremRate",
    "EWBCessNonAdvaloremRate",
    "GSTOtherCharges",
    "EINVOtherCharges",
    "EWBOtherCharges",
    "GSTTaxableValue",
    "EINVTaxableValue",
    "EWBTaxableValue",
    "GSTIgstAmount",
    "EINVIgstAmount",
    "EWBIgstAmount",
    "GSTCgstAmount",
    "EINVCgstAmount",
    "EWBCgstAmount",
    "GSTSgstAmount",
    "EINVSgstAmount",
    "EWBSgstAmount",
    "GSTCessAmount",
    "EINVCessAmount",
    "EWBCessAmount",
    "GSTStateCessAmount",
    "EINVStateCessAmount",
    "EWBStateCessAmount",
    "GSTCessNonAdvaloremAmount",
    "EINVCessNonAdvaloremAmount",
    "EWBCessNonAdvaloremAmount",
    "GSTStateCessNonAdvaloremAmount",
    "EINVStateCessNonAdvaloremAmount",
    "EWBStateCessNonAdvaloremAmount",
    "Remarks",
    "GSTStateName",
    "EINVStateName",
    "EWBStateName",
    "GstPushStatus",
    "EINVPushStatus",
    "EWBPushStatus",
    "GSTDocumentStatus",
    "EINVDocumentStatus",
    "EWBDocumentStatus",
    "EINVCancelledReason",
    "EWBCancelledReason",
    "EINVCustom11",
    "EWBCustom11",
    "GSTCustom11",
    "EINVCustom12",
    "EWBCustom12",
    "GSTCustom12",
    "EINVCustom13",
    "EWBCustom13",
    "GSTCustom13",
    "EINVCustom14",
    "EWBCustom14",
    "GSTCustom14",
    "EINVCustom15",
    "EWBCustom15",
    "GSTCustom15",
    "EINVCustom16",
    "EWBCustom16",
    "GSTCustom16",
    "EINVCustom17",
    "EWBCustom17",
    "GSTCustom17",
    "EINVCustom18",
    "EWBCustom18",
    "GSTCustom18",
    "EINVCustom19",
    "EWBCustom19",
    "GSTCustom19",
    "EINVCustom20",
    "EWBCustom20",
    "GSTCustom20",
    "GSTDispatchFromGSTIN",
    "EINVDispatchFromGSTIN",
    "EWBDispatchFromGSTIN",
    "GSTDispatchFromTradeName",
    "EINVDispatchFromTradeName",
    "EWBDispatchFromTradeName",
    "GSTShipToGSTIN",
    "EINVShipToGSTIN",
    "EWBShipToGSTIN",
    "GSTShipToTradeName",
    "EINVShipToTradeName",
    "EWBShipToTradeName",
    "GSTShipToLegalName",
    "EINVShipToLegalName",
    "EWBShipToLegalName",
)

# ── Type buckets ─────────────────────────────────────────────────────────────
# The export writes everything as text, so each column needs to be told what it
# actually is before it can be stored or compared. Anything not named below is
# free text.

DATE_COLUMNS = frozenset((
    "GstDocumentDate",
    "EINVDocumentDate",
    "EWBDocumentDate",
))

INT_COLUMNS = frozenset((
    "GSTPos",
    "EINVPos",
    "EWBPos",
))

DECIMAL_COLUMNS = frozenset((
    "GSTDocumentValue",
    "EINVDocumentValue",
    "EWBDocumentValue",
    "GSTDocumentTaxableValue",
    "EINVDocumentTaxableValue",
    "EWBDocumentTaxableValue",
    "GSTDocumentTaxValue",
    "EINVDocumentTaxValue",
    "EWBDocumentTaxValue",
    "GSTQuantity",
    "EINVQuantity",
    "EWBQuantity",
    "GSTPricePerQuantity",
    "EINVPricePerQuantity",
    "EWBPricePerQuantity",
    "GSTRate",
    "EINVRate",
    "EWBRate",
    "GSTCessRate",
    "EINVCessRate",
    "EWBCessRate",
    "GSTStateCessRate",
    "EINVStateCessRate",
    "EWBStateCessRate",
    "GSTCessNonAdvaloremRate",
    "EINVCessNonAdvaloremRate",
    "EWBCessNonAdvaloremRate",
    "GSTOtherCharges",
    "EINVOtherCharges",
    "EWBOtherCharges",
    "GSTTaxableValue",
    "EINVTaxableValue",
    "EWBTaxableValue",
    "GSTIgstAmount",
    "EINVIgstAmount",
    "EWBIgstAmount",
    "GSTCgstAmount",
    "EINVCgstAmount",
    "EWBCgstAmount",
    "GSTSgstAmount",
    "EINVSgstAmount",
    "EWBSgstAmount",
    "GSTCessAmount",
    "EINVCessAmount",
    "EWBCessAmount",
    "GSTStateCessAmount",
    "EINVStateCessAmount",
    "EWBStateCessAmount",
    "GSTCessNonAdvaloremAmount",
    "EINVCessNonAdvaloremAmount",
    "EWBCessNonAdvaloremAmount",
    "GSTStateCessNonAdvaloremAmount",
    "EINVStateCessNonAdvaloremAmount",
    "EWBStateCessNonAdvaloremAmount",
))

# ── Derived analytical columns ───────────────────────────────────────────────
# Worked out by classify.py from the 204 above; stored so the grid can filter
# and sort on them without recomputing 10,000 rows on every page load.
DERIVED_COLUMNS = (
    "Presence",
    "ReasonRemark",
    "Segment",
    "Gstr1Section",
    "Health",
    "MaxGap",
    "ManualReason",
)

# Everything a record carries that came from, or was derived from, the file —
# i.e. everything an import writes and an "edit all fields" screen may change.
# The audit columns (CreatedBy, SourceFile, IsDeleted, …) are deliberately not
# here: they are the application's own bookkeeping, not the document's data.
WRITABLE_COLUMNS = ALL_COLUMNS + DERIVED_COLUMNS

# ── Field groups for the "edit all fields" drawer ────────────────────────────
# 204 inputs in one flat list is unusable. Each column belongs to exactly one
# of these groups, matched by prefix, so the drawer can show GST / E-Invoice /
# E-Way Bill side by side and keep the shared identity fields at the top.
FIELD_GROUPS = (
    ("Document", "Identity and document-level fields shared across all three sources"),
    ("GST", "GSTR-1 — what was filed"),
    ("EINV", "E-Invoice — what the IRP returned"),
    ("EWB", "E-Way Bill — what moved the goods"),
    ("Comparison", "The portal's own matched / mismatched verdicts"),
    ("Other", "Everything else"),
)

_COMPARISON_PREFIXES = ("GstVs", "EinvVs", "EwbVs")
_DOCUMENT_COLUMNS = frozenset((
    "EntityGstin", "EntityName", "DocumentNumber", "DocumentType",
    "IsAmendment", "Remarks", "GSTINStatus", "GSTINTaxpayerType",
))

# Columns whose name does not carry the prefix of the source they belong to.
# The e-way bill number and its Part-B status are e-way bill facts however
# they are spelled, and the detail modal exists to show each source's data
# together — leaving these in the generic Document group put the e-way bill
# number in a different panel from the e-way bill.
_COLUMN_OVERRIDES = {
    "EwbNumber": "EWB",
    "PartBStatus": "EWB",
}


def group_for(column):
    """Which drawer group ``column`` belongs to. Prefix order matters: the
    comparison columns start with "Gst"/"Einv"/"Ewb" too, so they are tested
    first or every one of them would land in the GST group."""
    if column in _COLUMN_OVERRIDES:
        return _COLUMN_OVERRIDES[column]
    if column in _DOCUMENT_COLUMNS:
        return "Document"
    if column.startswith(_COMPARISON_PREFIXES):
        return "Comparison"
    if column.startswith("EINV"):
        return "EINV"
    if column.startswith("EWB"):
        return "EWB"
    if column.startswith("GST") or column.startswith("Gst"):
        return "GST"
    return "Other"


def grouped_columns():
    """``[(group, label, [column, ...]), ...]`` in FIELD_GROUPS order."""
    buckets = {name: [] for name, _ in FIELD_GROUPS}
    for column in ALL_COLUMNS:
        buckets[group_for(column)].append(column)
    return [(name, label, buckets[name]) for name, label in FIELD_GROUPS if buckets[name]]
