"""
billing_dashboard/services/normalize.py

Header normalization + alias mapping — the layer that makes the pipeline
tolerant of SAP layout changes (the business's #1 reported failure point).

* ``normalize_header`` reduces any header spelling to a comparison key:
  "Billing Doc.", "Billing_Doc", "Billing-Doc", "billing doc" → "billingdoc".
* ``ALIASES`` maps that comparison key to a promoted model field name.
* ``map_headers`` resolves a dump's header row into:
    - ``field_by_col``  : {col_index → model field name}   (promoted columns)
    - ``rawkey_by_col`` : {col_index → stable JSON key}     (every column)
  Duplicate headers (SAP has a few, e.g. "Description" twice) are de-duplicated
  for the raw JSON by suffixing _2, _3, …; the promoted field takes the first
  non-empty occurrence.
"""
import hashlib
import re

# Comparison-key → promoted model field name.
ALIASES = {
    # Business key
    "billingdoc": "billing_doc",
    "billingdocument": "billing_doc",
    "item": "item",
    "material": "material",
    # Document classification
    "billingtype": "billing_type",
    "billtypedescr": "bill_type_descr",
    "billtypedescription": "bill_type_descr",
    "billingcategory": "billing_category",
    "documentcat": "document_category",
    "documentcategory": "document_category",
    "docstatus": "doc_status",
    "fistatus": "fi_status",
    "cancelled": "cancelled",
    "returns": "returns",
    # Dates
    "billingdate": "billing_date",
    "sostodt": "so_sto_date",
    "sostodate": "so_sto_date",
    "mfgdate": "mfg_date",
    "expirydate": "expiry_date",
    "dispatchdate": "dispatch_date",
    "deliverydt": "delivery_date",
    "deliverydate": "delivery_date",
    "fiscalyear": "fiscal_year",
    # Order / logistics
    "sosto": "so_sto",
    "sostoitm": "so_sto_item",
    "sostoitem": "so_sto_item",
    "ponumber": "po_number",
    "delivery": "delivery",
    # Material
    "materialgroup": "material_group",
    "materialtype": "material_type",
    "description": "description",
    "producthierarchy": "product_hierarchy",
    "phlevel01desc": "ph_level01_desc",
    "phlevel02desc": "ph_level02_desc",
    "hsnsaccode": "hsn_sac",
    "hsnsac": "hsn_sac",
    # Organisation
    "salesorg": "sales_org",
    "division": "division",
    "distrchannel": "distr_channel",
    "companycode": "company_code",
    "plant": "plant",
    "plantregion": "plant_region",
    "salesoffice": "sales_office",
    "salesofficedescription": "sales_office_description",
    "salesofficedescr": "sales_office_description",
    "salesgroup": "sales_group",
    # Customer
    "soldtoparty": "sold_to_party",
    "soldtoname": "sold_to_name",
    "soldtocity": "sold_to_city",
    "soldtoregion": "sold_to_region",
    "soldtocountry": "sold_to_country",
    "payer": "payer",
    "payername": "payer_name",
    "payercountry": "payer_country",
    "payerregion": "payer_region",
    "shiptoparty": "ship_to_party",
    "shiptoname": "ship_to_name",
    "shiptocountry": "ship_to_country",
    "shiptoregion": "ship_to_region",
    # Values
    "salesunit": "sales_unit",
    "billedqty": "billed_qty",
    "doccurrency": "doc_currency",
    "invoicevalueinlc": "invoice_value_lc",
    "invoicevalue": "invoice_value",
    "revenue": "revenue",
    "taxableamt": "taxable_amt",
    "taxamount": "tax_amount",
    "netamt": "net_amt",
    "grossamt": "gross_amt",
    "mrp": "mrp",
    "exchrateacct": "exchange_rate",
    # GL / speciality / trade
    "revenuegl": "revenue_gl",
    "revenuegldesc": "revenue_gl_desc",
    "speciality": "speciality",
    "exporttype": "export_type",
    "incoterms": "incoterms",
}

# Mandatory promoted fields — rows missing any of these are skipped (per spec).
MANDATORY_FIELDS = ("billing_doc", "item", "material")


def normalize_header(value) -> str:
    """Reduce a header to a comparison key: lowercase alphanumerics only."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    # Drop everything that isn't a letter or digit (spaces, . _ - / % etc.)
    return re.sub(r"[^a-z0-9]", "", text)


def _raw_key(value) -> str:
    """Stable, human-ish snake_case key for the raw JSON blob."""
    if value is None:
        return "col"
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "col"


def map_headers(header_row):
    """Resolve a header row.

    Returns (field_by_col, rawkey_by_col, header_signature).
      * field_by_col : {col_index → model field}, first occurrence wins
      * rawkey_by_col: {col_index → unique raw JSON key}
      * header_signature: short hash of the normalized layout (layout versioning)
    """
    field_by_col = {}
    rawkey_by_col = {}
    seen_fields = set()
    seen_rawkeys = {}
    norm_parts = []
    prev_norm = ""

    for idx, raw_header in enumerate(header_row):
        norm = normalize_header(raw_header)
        norm_parts.append(norm)

        # Promoted field (first occurrence of a given field wins)
        field = ALIASES.get(norm)
        # SAP labels the Sales-Office description ambiguously as just
        # "Description" in the column immediately after "Sales Office" (the
        # material "Description" earlier keeps the `description` field). Capture
        # it positionally as the Region source (.NET Sales_Office_Description).
        if norm == "description" and prev_norm == "salesoffice":
            field = "sales_office_description"
        if field and field not in seen_fields:
            field_by_col[idx] = field
            seen_fields.add(field)
        prev_norm = norm

        # Raw JSON key (de-duplicate)
        base = _raw_key(raw_header)
        if base in seen_rawkeys:
            seen_rawkeys[base] += 1
            key = f"{base}_{seen_rawkeys[base]}"
        else:
            seen_rawkeys[base] = 1
            key = base
        rawkey_by_col[idx] = key

    signature = hashlib.sha1("|".join(norm_parts).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return field_by_col, rawkey_by_col, signature


def missing_mandatory(field_by_col):
    """Return the list of mandatory fields the header row does NOT provide."""
    present = set(field_by_col.values())
    return [f for f in MANDATORY_FIELDS if f not in present]
