"""
reconciliation/filters.py — one definition of "what the user is looking at".
=============================================================================
The records grid, the count beside it, the Excel export, the dashboard pivot
and "Remove filtered" all have to agree on which records the current filters
select. That agreement is the whole safety story behind "Remove filtered": the
button soft-deletes exactly what the grid is showing, and it can only be
trusted to do that if there is one filter implementation rather than five.

So there is one. Every screen calls ``filtered_records`` with the request, and
``describe`` turns the same parameters back into English for the confirmation
prompt — the user is told what is about to be removed in the same words the
filter was expressed in.
"""
from django.db.models import Q

from .models import ReconRecord
from .parsing import parse_date

# Columns the grid may sort on. An allow-list, not the user's string: an
# unchecked sort field is an ORM injection point and, more mundanely, a way to
# make the page 500 by typing a column name that does not exist.
SORTABLE = {
    "DocumentNumber", "DocumentType", "EntityGstin", "GstDocumentDate",
    # Taxable is what the grid shows and what reconciliation compares; the
    # document values stay sortable for anyone who exports and comes back.
    "GSTDocumentTaxableValue", "EINVDocumentTaxableValue", "EWBDocumentTaxableValue",
    "GSTDocumentValue", "EINVDocumentValue", "EWBDocumentValue",
    "MaxGap", "ReasonRemark", "Health", "Presence", "UpdatedAt",
}
DEFAULT_SORT = "DocumentNumber"

# Everything the search box looks in. Deliberately the identifiers a filing
# team actually has to hand — a document number off a query, a GSTIN off an
# email, an IRN or e-way bill number off the portal.
SEARCH_COLUMNS = (
    "DocumentNumber", "EntityGstin", "EntityName",
    "GSTBillToTradeName", "EINVBillToTradeName", "EWBBillToTradeName",
    "GSTBillToLegalName", "GSTBillToGstin",
    "EINVIrn", "EwbNumber", "GSTHsnOrSacCode",
)

STATUS_ACTIVE = ""
STATUS_DELETED = "deleted"
STATUS_ALL = "all"


class FilterSpec:
    """The parsed filters, plus the English description of them."""

    def __init__(self, data):
        self.q = (data.get("q") or "").strip()
        self.vendor = (data.get("vendor") or "").strip()
        self.date_from = parse_date(data.get("date_from"))
        self.date_to = parse_date(data.get("date_to"))
        # Separate from GST date (date_from/date_to) on purpose: the business
        # team reconciles per *upload batch*, not per invoice period — one
        # file goes in per month and the invoice dates on it spill across
        # months, so filtering by GST date mixes batches. Both filters can be
        # applied at once; they narrow independently.
        self.upload_from = parse_date(data.get("upload_from"))
        self.upload_to = parse_date(data.get("upload_to"))
        self.reason = (data.get("reason") or "").strip()
        self.presence = (data.get("presence") or "").strip()
        self.health = (data.get("health") or "").strip()
        self.segment = (data.get("segment") or "").strip()
        self.section = (data.get("section") or "").strip()
        self.status = (data.get("status") or STATUS_ACTIVE).strip()
        if self.status not in (STATUS_ACTIVE, STATUS_DELETED, STATUS_ALL):
            self.status = STATUS_ACTIVE
        # "cancelled=1" narrows to invoices the GST or e-invoice portal marked
        # Cancelled. The GST team reconciles these as a group — a cancellation
        # on either side has to be reflected on the other — so a one-click
        # filter is quicker than hunting through two reason remarks by name.
        self.cancelled = bool(data.get("cancelled")) and str(
            data.get("cancelled")).lower() not in ("0", "false", "")

    @property
    def is_narrowed(self):
        """True when at least one filter actually restricts the set.

        Record status alone does not count: "everything, including deleted" is
        not a narrowing, and "Remove filtered" must never run on it.
        """
        return any((self.q, self.vendor, self.date_from, self.date_to,
                    self.upload_from, self.upload_to,
                    self.reason, self.presence, self.health,
                    self.segment, self.section, self.cancelled))

    def describe(self):
        """The active filters as short phrases, for a confirmation prompt."""
        parts = []
        if self.q:
            parts.append(f'matching "{self.q}"')
        if self.vendor:
            parts.append(f"vendor {self.vendor}")
        if self.date_from and self.date_to:
            parts.append(f"dated {self.date_from:%d-%m-%Y} to {self.date_to:%d-%m-%Y}")
        elif self.date_from:
            parts.append(f"dated from {self.date_from:%d-%m-%Y}")
        elif self.date_to:
            parts.append(f"dated up to {self.date_to:%d-%m-%Y}")
        if self.upload_from and self.upload_to:
            parts.append(f"uploaded {self.upload_from:%d-%m-%Y} to {self.upload_to:%d-%m-%Y}")
        elif self.upload_from:
            parts.append(f"uploaded from {self.upload_from:%d-%m-%Y}")
        elif self.upload_to:
            parts.append(f"uploaded up to {self.upload_to:%d-%m-%Y}")
        if self.reason:
            parts.append(f'remark "{self.reason}"')
        if self.presence:
            parts.append(f"present in {self.presence}")
        if self.health:
            parts.append(f"health {self.health}")
        if self.segment:
            parts.append(f"segment {self.segment}")
        if self.section:
            parts.append(f"section {self.section}")
        if self.cancelled:
            parts.append("cancelled only")
        if self.status == STATUS_DELETED:
            parts.append("already removed")
        elif self.status == STATUS_ALL:
            parts.append("removed or not")
        return parts

    def as_dict(self):
        return {
            "q": self.q, "vendor": self.vendor,
            "date_from": self.date_from.isoformat() if self.date_from else "",
            "date_to": self.date_to.isoformat() if self.date_to else "",
            "upload_from": self.upload_from.isoformat() if self.upload_from else "",
            "upload_to": self.upload_to.isoformat() if self.upload_to else "",
            "reason": self.reason, "presence": self.presence,
            "health": self.health, "segment": self.segment,
            "section": self.section, "status": self.status,
            "cancelled": "1" if self.cancelled else "",
        }


def apply_status(queryset, status):
    """The default view is active records only.

    Without it, "all the data" would mean scrolling past everything anyone has
    ever removed. Switch Record status to find a deleted record and restore it,
    or to see everything regardless of state.
    """
    if status == STATUS_DELETED:
        return queryset.filter(IsDeleted=True)
    if status == STATUS_ALL:
        return queryset
    return queryset.filter(IsDeleted=False)


def filtered_records(data, queryset=None):
    """``(queryset, spec)`` for the filters in ``data`` (a QueryDict or dict)."""
    spec = FilterSpec(data)
    records = ReconRecord.objects.all() if queryset is None else queryset

    if spec.q:
        matches = Q()
        for column in SEARCH_COLUMNS:
            matches |= Q(**{f"{column}__icontains": spec.q})
        records = records.filter(matches)
    if spec.vendor:
        records = records.filter(EntityGstin=spec.vendor)
    if spec.date_from:
        records = records.filter(GstDocumentDate__gte=spec.date_from)
    if spec.date_to:
        records = records.filter(GstDocumentDate__lte=spec.date_to)
    if spec.upload_from:
        records = records.filter(UploadedAt__date__gte=spec.upload_from)
    if spec.upload_to:
        # __date__lte on a DateTimeField matches the whole day inclusive,
        # which is what "uploaded up to the 10th" has to mean to be useful.
        records = records.filter(UploadedAt__date__lte=spec.upload_to)
    if spec.reason:
        records = records.filter(ReasonRemark=spec.reason)
    if spec.presence:
        records = records.filter(Presence=spec.presence)
    if spec.health:
        records = records.filter(Health=spec.health)
    if spec.segment:
        records = records.filter(Segment=spec.segment)
    if spec.section:
        records = records.filter(Gstr1Section=spec.section)
    if spec.cancelled:
        # Either side's portal having cancelled the invoice counts — the GST
        # team watches for a cancellation on one side that was not reflected on
        # the other, which is exactly what this "or" catches.
        records = records.filter(
            Q(GSTDocumentStatus__iexact="Cancelled")
            | Q(EINVDocumentStatus__iexact="Cancelled"))

    return apply_status(records, spec.status), spec


def order_records(queryset, sort, direction):
    """Apply a checked sort. ``id`` is always the tie-break, so paging is
    stable — two documents with the same number would otherwise swap places
    between page 1 and page 2."""
    column = sort if sort in SORTABLE else DEFAULT_SORT
    prefix = "-" if direction == "desc" else ""
    return queryset.order_by(f"{prefix}{column}", "id"), column, direction
