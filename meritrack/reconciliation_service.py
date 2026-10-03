"""MeriTrack — auto-reconciliation of a completed audit session against the
hospital's expected consignment inventory.

No external/SAP dependency: "expected" is the hospital's own on-shelf
ConsignedProduct inventory as known to the app. Triggered from
``ScanningSessionViewSet.complete_session`` when an audit session completes.
"""

import logging
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import ConsignedProduct, InventoryReconciliation, ReconciliationItem

logger = logging.getLogger(__name__)

# Scan actions that prove the rep physically handled the unit during the session.
_PRESENCE_ACTIONS = {
    "verify", "add", "use",
    "remove_expired", "remove_recalled", "remove_return",
    "transfer_out", "transfer_in",
}
# Actions that removed/consumed a unit that WAS on the shelf at session start.
_REMOVAL_ACTIONS = {"use", "remove_expired", "remove_recalled", "remove_return", "transfer_out"}


def _norm(value):
    return (value or "").strip()


def run_session_reconciliation(session, user=None):
    """Compute & persist an InventoryReconciliation for a completed session.

    Compares the serials physically scanned in the session against the
    ConsignedProduct units the system expected on-shelf for the session scope
    (hospital + optional department). The "expected at session start" set is
    reconstructed from durable data so the session's own adds/removals don't
    skew the result.

    Headline counts are derived from the per-item rows so the summary and the
    detail table always agree. Idempotent: one reconciliation per session;
    re-running replaces its items.

    Returns the InventoryReconciliation, or None if the session had no scans.
    """
    today = timezone.now().date()

    logs = list(session.scan_logs.all())
    if not logs:
        return None

    # ── Scope: ConsignedProduct rows for this hospital (+ department if set) ──
    scope = ConsignedProduct.objects.filter(department__hospital=session.hospital).select_related("department")
    if session.department_id:
        scope = scope.filter(department=session.department)

    # Serials this session removed/used — they WERE on the shelf at start.
    removed_serials = {
        _norm(lg.serial_number) for lg in logs if lg.action in _REMOVAL_ACTIONS and _norm(lg.serial_number)
    }

    # ── Expected-at-start (serialized): on_shelf now & not added this session,
    #    UNION units this session removed/used. Keyed by serial. ──
    expected = {}
    for cp in scope.exclude(serial_number=""):
        serial = _norm(cp.serial_number)
        if not serial:
            continue
        was_on_shelf_at_start = (
            cp.status == "on_shelf" and cp.scan_session_id != session.id
        ) or serial in removed_serials
        if was_on_shelf_at_start:
            expected[serial] = cp

    # ── Found (serialized): serials physically scanned this session ──
    found = {}
    for lg in logs:
        serial = _norm(lg.serial_number)
        if serial and lg.action in _PRESENCE_ACTIONS:
            found.setdefault(serial, lg)

    expected_serials = set(expected)
    found_serials = set(found)
    matched_serials = expected_serials & found_serials
    missing_serials = expected_serials - found_serials
    extra_serials = found_serials - expected_serials

    items = []

    def _expiry(cp, lg):
        if cp and cp.expiry_date:
            return cp.expiry_date
        return lg.expiry_date if lg else None

    for serial in sorted(matched_serials):
        cp, lg = expected[serial], found[serial]
        exp = _expiry(cp, lg)
        expired = bool(exp and exp < today)
        items.append(ReconciliationItem(
            consigned_product=cp, scan_log=lg,
            product_name=cp.product_name or lg.product_name,
            gtin_code=cp.gtin_code or lg.gtin_code,
            material_code=cp.material_code or lg.material_code,
            batch_number=cp.batch_number or lg.batch_number,
            serial_number=serial, barcode_data=lg.qr_data,
            match_status="expired_found" if expired else "matched",
            expected_quantity=1, found_quantity=1, expiry_date=exp,
            notes="Expected and scanned, but past expiry." if expired else "",
        ))

    for serial in sorted(missing_serials):
        cp = expected[serial]
        items.append(ReconciliationItem(
            consigned_product=cp,
            product_name=cp.product_name, gtin_code=cp.gtin_code,
            material_code=cp.material_code, batch_number=cp.batch_number,
            serial_number=serial, match_status="missing",
            expected_quantity=1, found_quantity=0, expiry_date=cp.expiry_date,
            notes="Expected on shelf but not scanned during the session.",
        ))

    for serial in sorted(extra_serials):
        lg = found[serial]
        items.append(ReconciliationItem(
            scan_log=lg,
            product_name=lg.product_name, gtin_code=lg.gtin_code,
            material_code=lg.material_code, batch_number=lg.batch_number,
            serial_number=serial, barcode_data=lg.qr_data,
            match_status="extra", expected_quantity=0, found_quantity=1,
            expiry_date=lg.expiry_date,
            notes="Scanned but not in expected inventory (newly added or foreign unit).",
        ))

    # ── No-serial products: quantity comparison by (material/gtin, batch). ──
    # Each line counts as one item; quantities are informative.
    def _key(material, gtin, batch):
        return (_norm(material).upper(), _norm(gtin), _norm(batch).upper())

    exp_qty, ns_meta = {}, {}
    ns_scope = scope.filter(serial_number="", status="on_shelf").exclude(scan_session_id=session.id)
    for cp in ns_scope:
        k = _key(cp.material_code, cp.gtin_code, cp.batch_number)
        exp_qty[k] = exp_qty.get(k, 0) + int(cp.quantity or 1)
        ns_meta.setdefault(k, (cp.product_name, cp.gtin_code, cp.material_code, cp.batch_number))

    fnd_qty = {}
    for lg in logs:
        if _norm(lg.serial_number) or lg.action not in _PRESENCE_ACTIONS:
            continue
        k = _key(lg.material_code, lg.gtin_code, lg.batch_number)
        fnd_qty[k] = fnd_qty.get(k, 0) + int(lg.quantity or 1)
        ns_meta.setdefault(k, (lg.product_name, lg.gtin_code, lg.material_code, lg.batch_number))

    for k in sorted(set(exp_qty) | set(fnd_qty)):
        e, f = exp_qty.get(k, 0), fnd_qty.get(k, 0)
        if e == 0:
            ms = "extra"
        elif f >= e:
            ms = "matched"
        else:
            ms = "missing"
        pname, g, m, b = ns_meta.get(k, (None, k[1], k[0], k[2]))
        items.append(ReconciliationItem(
            product_name=pname, gtin_code=g, material_code=m, batch_number=b,
            serial_number="", match_status=ms,
            expected_quantity=e, found_quantity=f,
            notes="No-serial product — quantity comparison.",
        ))

    # ── Derive headline counts from items (summary ↔ detail always agree). ──
    n_matched = sum(1 for it in items if it.match_status == "matched")
    n_expired = sum(1 for it in items if it.match_status == "expired_found")
    n_missing = sum(1 for it in items if it.match_status == "missing")
    n_extra = sum(1 for it in items if it.match_status == "extra")

    total_expected = n_matched + n_expired + n_missing
    total_scanned = n_matched + n_expired + n_extra
    found_ok = n_matched + n_expired  # expected units that were located

    if total_expected:
        accuracy = Decimal(str(round(100.0 * found_ok / total_expected, 1)))
        discrepancy = Decimal(str(round(100.0 * (n_missing + n_extra) / total_expected, 1)))
    else:
        accuracy = Decimal("100.0") if n_extra == 0 else Decimal("0.0")
        discrepancy = Decimal("0.0") if n_extra == 0 else Decimal("100.0")

    with transaction.atomic():
        recon, _created = InventoryReconciliation.objects.get_or_create(
            session=session,
            defaults={
                "recon_id": f"REC-{session.session_key[:10].upper()}-{uuid.uuid4().hex[:6].upper()}",
                "hospital": session.hospital,
                "department": session.department,
                "performed_by": user,
            },
        )
        recon.items.all().delete()  # replace on re-run
        recon.hospital = session.hospital
        recon.department = session.department
        recon.recon_type = "session_audit"
        recon.status = "completed"
        recon.total_expected = total_expected
        recon.total_scanned = total_scanned
        recon.matched = n_matched
        recon.missing = n_missing
        recon.extra = n_extra
        recon.expired_found = n_expired
        recon.accuracy_rate = accuracy
        recon.discrepancy_rate = discrepancy
        recon.completed_at = timezone.now()
        if user and not recon.performed_by_id:
            recon.performed_by = user
        recon.save()

        for it in items:
            it.reconciliation = recon
        if items:
            ReconciliationItem.objects.bulk_create(items)

    logger.info(
        "Reconciliation %s for session %s: expected=%d scanned=%d matched=%d "
        "missing=%d extra=%d expired=%d accuracy=%s%%",
        recon.recon_id, session.session_key, total_expected, total_scanned,
        n_matched, n_missing, n_extra, n_expired, accuracy,
    )
    return recon
