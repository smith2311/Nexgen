"""Build an auto-generated email draft for a reconciled invoice.

Returns subject + body for review in the UI (not auto-sent).
"""
from ..models import VatInvoice


def build_draft(invoice):
    matched = invoice.recon_status == VatInvoice.ReconStatus.MATCHED
    subject = ("VAT Reconciliation Completed" if matched
               else "VAT Reconciliation Discrepancy Detected")

    def fmt(value):
        return "—" if value is None else f"{value}"

    body = (
        f"Dear Team,\n\n"
        f"Please find below the VAT reconciliation summary for the invoice:\n\n"
        f"Invoice Number      : {invoice.invoice_number}\n"
        f"Country             : {fmt(invoice.country)}\n"
        f"Taxable Amount      : {fmt(invoice.taxable_amount)}\n"
        f"Expected VAT        : {fmt(invoice.expected_vat)}\n"
        f"Invoice VAT         : {fmt(invoice.charged_vat)}\n"
        f"Variance            : {fmt(invoice.variance)}\n"
        f"Reconciliation Status: {invoice.recon_status}\n\n"
    )
    if matched:
        body += "The charged VAT is within the configured tolerance. No action required.\n"
    else:
        body += ("The variance exceeds the configured tolerance. "
                 "Please review the invoice and take corrective action.\n")
    body += "\nRegards,\nVAT Reconciliation System"

    return {"subject": subject, "body": body}
