"""
reconciliation/billing_types.py — the billing-type master.
=============================================================================
53 SAP billing-type codes, each declaring the shape a document of that type is
*supposed* to have: which of GST / E-Invoice / E-Way Bill it must carry, what
document type it must be, and the remark to file it under when it all lines up.

This is what turns the classifier from a pile of hand-written special cases
into a lookup. A row whose billing-type code is in this master is judged
against the master; a row whose code is blank or unknown falls through to the
older rule-by-rule logic in classify.py, unchanged.

The tuples below seed ``ReconBillingType`` and are only a starting point — the
Billing Type Master screen edits the table, and the classifier reads the table,
so a correction made there takes effect on the next re-run without a code
change. Generated from
``dummy_tempelate/reconciliation/Billing type_To update.xlsx``.

Two things in the master are worth knowing before reading classify.py:

* **"NA" means "not mandatory", not "must not exist."** Checked against the
  real 10,424-row export, at least eight billing types carry an NA source on
  95-100% of their rows — every ZCFU consignment challan has an E-Invoice
  despite EINV = NA. Flagging the *extra* source would have put roughly a fifth
  of a normal import into the exception queue for normal, over-compliant
  filing, so only a *missing* mandatory source counts against a row.

* **An NA source is also excluded from the amount comparison.** Branch Transfer
  Challans (ZSTI / ZSTA / ZSTR) are why: GST is deliberately Rs 0 there (not a
  taxable supply) while the E-Way Bill correctly carries the real goods value.
  Comparing whatever happens to be present flagged 711 completely normal
  branch-transfer rows in the real export.
"""
from dataclasses import dataclass

# -- Master "Type" -> the DocumentType the export actually spells -------------
# The master speaks SAP ("Service", "Scrap Invoice", "Jobwork"); the export
# speaks GST ("Invoice", "Credit Note", "Debit Note", "Challan"). A service
# sale and a scrap sale are both invoices on the portal, and a jobwork
# despatch is a challan.
#
# "Exempt" maps to None on purpose: an exempt supply should be a Bill of
# Supply, which is not one of the four document types this export can produce,
# so every exempt-coded row is a document-type exception by construction. No
# exempt-coded row exists in either sample export to check that against yet.
EXPECTED_DOCUMENT_TYPE = {
    "Invoice": "Invoice",
    "Service": "Invoice",
    "Scrap Invoice": "Invoice",
    "Credit note": "Credit Note",
    "Debit note": "Debit Note",
    "Challan": "Challan",
    "Jobwork": "Challan",
    "Exempt": None,
}


@dataclass(frozen=True)
class BillingTypeRule:
    """One row of the master, as the classifier consumes it."""

    code: str
    description: str
    doc_type: str            # the master's own vocabulary
    gst_applicable: bool
    einv_applicable: bool
    ewb_applicable: bool
    final_remark: str
    health: str              # "ok" / "warn" / "bad"
    other_conditions: str = ""
    # When set, this wording replaces the generic "Dispatched Pending" remark
    # for rows of this billing type. Lets an admin shift the status from a
    # hard-coded string to one the master owns (the Gujarat section used to
    # read "Dispatch Pending" as a literal; the business team wanted it driven
    # by master data instead). Blank means "use the shipped wording".
    pending_remark: str = ""

    @property
    def expected_document_type(self):
        """The DocumentType the export should be showing, or None for the
        Bill-of-Supply case the export cannot represent."""
        return EXPECTED_DOCUMENT_TYPE.get(self.doc_type)

    @property
    def expected_label(self):
        """What to name in a "Wrong DocumentsType" remark."""
        return self.expected_document_type or "Bill of Supply"

    def doc_type_ok(self, document_type):
        """True when the row's DocumentType is the one this billing type
        expects. Blank counts as wrong — a document with no type at all cannot
        be reconciled against a master that names one."""
        expected = self.expected_document_type
        if expected is None:
            return False
        return (document_type or "").strip().casefold() == expected.casefold()


# -- The master, as shipped ---------------------------------------------------
# (code, description, type, GST, E-INV, EWB, final remark, health, conditions)
SEED_ROWS = (
    ("FV",    "Invoice Contract",      "Service",        True , True , False,
     "All Filed Match-EWB-NA(Services)",
     "ok", ""),
    ("G2",    "Credit Memo",           "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("L2",    "Debit Memo",            "Debit note",     True , True , False,
     "All Filed Match-EWB-NA-DN",
     "ok", ""),
    ("YDEE",  "Demo - Employee -Tax",  "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("YDEX",  "Demo -Employee Exemp",  "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("YEVE",  "Sales - Taxable",       "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("YEVX",  "Sales Exempted",        "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("YRER",  "Credit for Returns",    "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("YRGE",  "FOC Grand Sche Tax",    "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("YRGX",  "FOC Grand Sch Exempt",  "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("YRIE",  "Free Reag W Inst TAX",  "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("YSHE",  "FOC Samp to Cust EX",   "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("YSME",  "FOC Samp to Cust EX",   "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("YSMX",  "FOC Sample Cust Exem",  "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("YSSE",  "FG Under Scheme-Exci",  "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("YSSX",  "FG Under Scheme-Exem",  "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("ZBUT",  "Export Bond/LUT",       "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZCFU",  "Consignment Fill up",   "Challan",        True , False, True ,
     "All Filed Match-EINV-NA-Consgn Fill",
     "ok", "It must be Challan"),
    ("ZCIR",  "Consignment Returns",   "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("ZCIS",  "Consignment Issue",     "Invoice",        True , True , False,
     "All Filed Match-EWB-NA-Consign Issue",
     "ok", ""),
    ("ZCN",   "Credit Note- Sales",    "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("ZCPU",  "Consignment Pickup",    "Challan",        True , False, True ,
     "All Filed Match-EINV-NA-Consgn Pick UP",
     "ok", "It must be Challan"),
    ("ZCSA",  "CN-Sales(Price Diff)",  "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("ZCSR",  "Credit Note- Service",  "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("ZDSA",  "Debit Memo",            "Debit note",     True , True , False,
     "All Filed Match-EWB-NA-DN",
     "ok", ""),
    ("ZDSR",  "Debit Note- Service",   "Debit note",     True , True , False,
     "All Filed Match-EWB-NA-DN",
     "ok", ""),
    ("ZF2",   "Sales invoice",         "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZFC",   "FOC sale- DC to Emp",   "Challan",        True , False, True ,
     "All Filed Match-EINV-NA-FOC",
     "ok", "It must be Challan"),
    ("ZGST",  "GST Stock Transfer",    "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZJBE",  "Job Work Sales-Taxab",  "Jobwork",        False, False, True ,
     "All Filed Match-EINV-NA-JOBWORK",
     "ok", "It must be Challan"),
    ("ZKB",   "Consign. Sales Fill",   "Challan",        True , False, True ,
     "All Filed Match-EINV-NA-Consgn Fill",
     "ok", "It must be Challan"),
    ("ZQCS",  "Service Sales",         "Service",        True , True , False,
     "All Filed Match-EWB-NA(Services)",
     "ok", ""),
    ("ZREO",  "Return without Refer",  "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("ZRER",  "Credit for Returns",    "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("ZRGE",  "RR Grand Sch-Taxable",  "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZRGX",  "RR Grand Sch-Exempt",   "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("ZRRE",  "Return- RC Taxable",    "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZRUT",  "Return W Ref-Export",   "Credit note",    True , True , False,
     "All Filed Match-EWB-NA-CN",
     "ok", ""),
    ("ZSAE",  "Comercial Invoice",     "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZSAX",  "Comercial- Exampted",   "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("ZSCE",  "Scrap Sales Taxable",   "Scrap Invoice",  True , True , True ,
     "All Field OK - EWB-NA- Scrap",
     "ok", ""),
    ("ZSD",   "Sub.Seq_DC_Warranty",   "Challan",        False, False, False,
     "All Filed Match-EWB-Warranty_service",
     "ok", "It must be Challan"),
    ("ZSER",  "Service Invoice",       "Service",        True , True , False,
     "All Filed Match-EWB-NA(Services)",
     "ok", ""),
    ("ZSFC",  "Scheme-FOC",            "Challan",        True , False, True ,
     "All Filed Match-EINV-NA-Scheme-FOC",
     "ok", "It must be Challan"),
    ("ZSHX",  "Sort Expiry Exempted",  "Exempt",         True , False, False,
     "All Field OK - EINV_EWB-NA_Exempted",
     "ok", "It must be Bill of Supply. If Invoice or Delivery Challan , Mark as \"Wrong DocumentsType"),
    ("ZSMP",  "Samples Invoice",       "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZSP",   "SC Invoice",            "Jobwork",        False, False, True ,
     "All Filed Match-EINV-NA-JOBWORK",
     "ok", "It must be Challan"),
    ("ZSR",   "Service Sale",          "Service",        True , True , False,
     "All Filed Match-EWB-NA(Services)",
     "ok", ""),
    ("ZSRE",  "Rate contract Taxabl",  "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZSTA",  "Branch Transfer (BT)",  "Challan",        False, False, True ,
     "All Filed Match - EINV-NA - LBT",
     "ok", "It it must be Challan. EWB Not applicable if within City/Vapi"),
    ("ZSTI",  "Branch Transfer (BT)",  "Challan",        False, False, True ,
     "All Filed Match - EINV-NA - LBT",
     "ok", "It it must be Challan. EWB Not applicable if within City/Vapi"),
    ("ZSTO",  "Branch Transfer (BT)",  "Invoice",        True , True , True ,
     "All Filed Match",
     "ok", "It Must be Invoice & must be match all Field"),
    ("ZSTR",  "Branch Transfer (BT)",  "Challan",        False, False, True ,
     "All Filed Match - EINV-NA - LBT",
     "ok", "It it must be Challan. EWB Not applicable if within City/Vapi"),
)


def seed_rules():
    """``{code: BillingTypeRule}`` straight from SEED_ROWS — used by the data
    migration that fills the table, and by tests that want the master without
    touching the database."""
    return {row[0]: BillingTypeRule(*row) for row in SEED_ROWS}
