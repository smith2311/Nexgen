#!/usr/bin/env python3
"""
Comprehensive End-to-End Test for Shipment Notification System
==============================================================

Tests the complete production flow:
  1. Salesperson mapping assignment (country/vertical → phone)
  2. Dashboard isolation (each phone sees only their data)
  3. Change detection (hash-based tracking)
  4. WhatsApp notification triggering on data changes
  5. Notification correctly routes to mapped phone
  6. Post-notification state (hash updated, no duplicate sends)

Test Phone Assignments:
  918128644751 → USA / MHPL       (Manas)
  918949039415 → Turkey / MEPL
  917574002903 → United Kingdom / MEPL

Usage:
  podman exec django_app python manage.py shell -c \
      "exec(open('shipment_notifications/e2e_shipment_notifications.py').read())"

NOT a pytest module, despite what it was called until now. Everything here runs
at IMPORT time and touches the database directly, so while it was named
``test_shipment_notifications.py`` pytest collected it, executed it during
collection, and aborted the entire run with

    RuntimeError: Database access not allowed, use the "django_db" mark, or the
    "db" or "transactional_db" fixtures to enable it.

That single error stopped `pytest` from running any of the repository's ~2,400
tests. It defines no ``test_*`` function and no ``Test*`` class; it is a manual
end-to-end check a human runs and reads. Renamed so its name says so — if you
want it to be a real test suite, wrap each section in a function and mark it
``@pytest.mark.django_db``.
"""
import os
import sys
from collections import defaultdict

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from shipment_notifications.models import (
    ExportShipment,
    SalespersonMapping,
    ShipmentAccessToken,
)
from shipment_notifications.services import (
    DashboardService,
    EngatiService,
    WhatsAppNotificationService,
    _test_mode,
    _test_phones,
)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
PHONE_ASSIGNMENTS = {
    "918128644751": {"country": "USA", "vertical": "MHPL"},
    "918949039415": {"country": "Turkey", "vertical": "MEPL"},
    "917574002903": {"country": "United Kingdom", "vertical": "MEPL"},
}

passed = 0
failed = 0
errors = []


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ PASS: {name}")
    else:
        failed += 1
        errors.append(name)
        print(f"  ✗ FAIL: {name}")
        if detail:
            print(f"          {detail}")


# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("SHIPMENT NOTIFICATION SYSTEM - COMPREHENSIVE TEST SUITE")
print("=" * 80)

# ═════════════════════════════════════════════════════════════════════════════
# TEST 1: SalespersonMapping setup
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 1: SalespersonMapping configuration")
print("─" * 80)

for phone, info in PHONE_ASSIGNMENTS.items():
    mapping = SalespersonMapping.objects.filter(country=info["country"], vertical=info["vertical"]).first()
    test(
        f'Mapping exists for {info["country"]}/{info["vertical"]}',
        mapping is not None,
        f'Expected mapping for {info["country"]}/{info["vertical"]}',
    )
    if mapping:
        test(
            f'Phone {phone} assigned to {info["country"]}/{info["vertical"]}',
            mapping.whatsapp_no == phone,
            f"Expected {phone}, got {mapping.whatsapp_no}",
        )

# Verify no duplicate mappings
all_mappings = SalespersonMapping.objects.all()
phone_counts = defaultdict(int)
for m in all_mappings:
    if m.whatsapp_no:
        phone_counts[m.whatsapp_no] += 1

for phone in PHONE_ASSIGNMENTS:
    test(
        f"Phone {phone} has unique mapping",
        phone_counts.get(phone, 0) == 1,
        f"Phone appears in {phone_counts.get(phone, 0)} mappings",
    )


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2: ShipmentAccessToken & dashboard URLs
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 2: ShipmentAccessToken & dashboard URLs")
print("─" * 80)

for phone in PHONE_ASSIGNMENTS:
    token_obj = ShipmentAccessToken.objects.filter(phone_number=phone).first()
    test(f"Token exists for {phone}", token_obj is not None)

    if token_obj:
        # Verify token can be verified back to the phone
        encrypted = token_obj.token
        verified_phone = ShipmentAccessToken.verify_token(encrypted, max_age_days=30)
        test(f"Token round-trip for {phone}", verified_phone == phone, f"Expected {phone}, got {verified_phone}")

        # Verify dashboard URL generation
        url = DashboardService.get_url_for_phone(phone)
        test(f"Dashboard URL generated for {phone}", url and "shipment/track/" in url, f"URL: {url}")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3: Dashboard data isolation
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 3: Dashboard data isolation (each phone sees only their country)")
print("─" * 80)

for phone, info in PHONE_ASSIGNMENTS.items():
    # Simulate what the dashboard view does
    mappings = SalespersonMapping.objects.filter(whatsapp_no=phone)
    if mappings.exists():
        q = Q()
        for m in mappings:
            q |= Q(country__iexact=m.country, vertical__iexact=m.vertical)
        shipments = ExportShipment.objects.filter(q)
    else:
        shipments = ExportShipment.objects.all()

    count = shipments.count()
    test(f'{phone} → {info["country"]}/{info["vertical"]}: {count} shipments', count > 0, "Expected >0 shipments")

    # Verify NO shipments from other countries leak in
    other_countries = set()
    for s in shipments[:50]:  # sample first 50
        if s.country.lower() != info["country"].lower() or s.vertical.lower() != info["vertical"].lower():
            other_countries.add(f"{s.country}/{s.vertical}")

    test(
        f"{phone}: no data leakage from other countries",
        len(other_countries) == 0,
        f"Found shipments from: {other_countries}" if other_countries else "",
    )

# Verify phone 1 and phone 2 see DIFFERENT data
p1_info = PHONE_ASSIGNMENTS["918128644751"]
p2_info = PHONE_ASSIGNMENTS["918949039415"]
p1_shipments = set(
    ExportShipment.objects.filter(country__iexact=p1_info["country"], vertical__iexact=p1_info["vertical"]).values_list(
        "id", flat=True
    )
)
p2_shipments = set(
    ExportShipment.objects.filter(country__iexact=p2_info["country"], vertical__iexact=p2_info["vertical"]).values_list(
        "id", flat=True
    )
)
test(
    "Phone 1 and Phone 2 see completely different shipments",
    len(p1_shipments & p2_shipments) == 0,
    f"Overlap: {len(p1_shipments & p2_shipments)} shipments",
)


# ═════════════════════════════════════════════════════════════════════════════
# TEST 4: Change detection mechanism
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 4: Change detection (hash-based)")
print("─" * 80)

# Pick one shipment per country for testing
test_subjects = {}
for phone, info in PHONE_ASSIGNMENTS.items():
    shipment = ExportShipment.objects.filter(country__iexact=info["country"], vertical__iexact=info["vertical"]).first()
    if shipment:
        test_subjects[phone] = shipment

# 4a: Verify hash computation is deterministic
for phone, shipment in test_subjects.items():
    hash1 = shipment.compute_change_hash()
    hash2 = shipment.compute_change_hash()
    test(f"Hash deterministic for shipment {shipment.id}", hash1 == hash2)

# 4b: Verify unnotified shipments are detected as changed
for phone, shipment in test_subjects.items():
    if not shipment.last_notified_hash:
        test(f"Unnotified shipment {shipment.id} detected as changed", shipment.has_changed_since_last_notification)

# 4c: Simulate "notify" → mark as notified → verify no longer flagged
test_shipment = list(test_subjects.values())[0]
original_hash = test_shipment.last_notified_hash
test_shipment.last_notified_hash = test_shipment.compute_change_hash()
test(
    "After marking notified, has_changed_since_last_notification = False",
    not test_shipment.has_changed_since_last_notification,
)

# 4d: Simulate data change → verify flagged again
old_status = test_shipment.status
test_shipment.status = "SIMULATED_CHANGE_FOR_TEST"
test(
    "After status change, has_changed_since_last_notification = True", test_shipment.has_changed_since_last_notification
)
# Restore
test_shipment.status = old_status
test_shipment.last_notified_hash = original_hash


# ═════════════════════════════════════════════════════════════════════════════
# TEST 5: Test mode configuration
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 5: Test mode configuration")
print("─" * 80)

test_mode = _test_mode()
test_phones = _test_phones()

test("Test mode is enabled", test_mode, f"SHIPMENT_NOTIFICATIONS_TEST_MODE={test_mode}")
test("Test phones configured", len(test_phones) >= 2, f"Found {len(test_phones)} phones: {test_phones}")

# Verify all assigned phones are in the test phone list
for phone in PHONE_ASSIGNMENTS:
    test(f"{phone} is in TEST_PHONE list", phone in test_phones, f"Missing from test phones: {test_phones}")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 6: Notification routing (dry run)
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 6: Notification routing simulation (dry run)")
print("─" * 80)

# Simulate the grouping logic from WhatsAppNotificationService
# but WITHOUT actually sending messages
mapping_cache = {}


def get_mapping(country, vertical):
    key = (country.lower(), vertical.lower())
    if key not in mapping_cache:
        mapping_cache[key] = SalespersonMapping.objects.filter(
            country__iexact=country,
            vertical__iexact=vertical,
        ).first()
    return mapping_cache[key]


# Get changed shipments from each test country
groups = defaultdict(list)
skipped = 0

for phone, info in PHONE_ASSIGNMENTS.items():
    shipments = ExportShipment.objects.filter(country__iexact=info["country"], vertical__iexact=info["vertical"])
    # Use all shipments, not just changed ones — routing test doesn't depend on notification state
    sample = list(shipments[:3])

    for shipment in sample:
        mapping = get_mapping(shipment.country, shipment.vertical)
        if not mapping or not mapping.whatsapp_no.strip():
            skipped += 1
            continue

        real_phone = mapping.whatsapp_no.strip()
        if test_mode:
            if real_phone in test_phones:
                dest_phone = real_phone
            else:
                dest_phone = test_phones[0]
        else:
            dest_phone = real_phone

        groups[dest_phone].append(shipment)

# Verify routing
for phone, info in PHONE_ASSIGNMENTS.items():
    shipments_for_phone = groups.get(phone, [])
    test(
        f'{phone} receives notifications for {info["country"]}/{info["vertical"]}',
        len(shipments_for_phone) > 0,
        f"Got {len(shipments_for_phone)} shipments routed",
    )
    # Verify correct country
    if shipments_for_phone:
        all_correct = all(
            s.country.lower() == info["country"].lower() and s.vertical.lower() == info["vertical"].lower()
            for s in shipments_for_phone
        )
        test(f'{phone}: all routed shipments match {info["country"]}/{info["vertical"]}', all_correct)

# Verify NO cross-routing
phones = list(PHONE_ASSIGNMENTS.keys())
for i, p1 in enumerate(phones):
    for p2 in phones[i + 1 :]:
        s1_ids = set(s.id for s in groups.get(p1, []))
        s2_ids = set(s.id for s in groups.get(p2, []))
        test(f"No cross-routing between {p1} and {p2}", len(s1_ids & s2_ids) == 0, f"Overlap: {s1_ids & s2_ids}")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 7: Engati API connectivity
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 7: Engati API connectivity")
print("─" * 80)

endpoint = getattr(settings, "ENGATI_API_ENDPOINT", "")
auth_token = getattr(settings, "ENGATI_BASIC_TOKEN", "")
test("Engati endpoint configured", bool(endpoint), f"ENGATI_API_ENDPOINT={endpoint}")
test("Engati auth token configured", bool(auth_token))


# ═════════════════════════════════════════════════════════════════════════════
# TEST 8: Simulate data change + send real notifications
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 8: Simulated data change → send WhatsApp notifications")
print("─" * 80)

print("\n  For each phone, picking ONE unnotified shipment, marking all others")
print('  as "already-notified" to isolate a clean trigger.\n')

send_results = {}
for phone, info in PHONE_ASSIGNMENTS.items():
    shipments = ExportShipment.objects.filter(country__iexact=info["country"], vertical__iexact=info["vertical"])
    changed = [s for s in shipments if s.has_changed_since_last_notification]

    if not changed:
        print(f'  ⚠  {phone}: no changed shipments for {info["country"]}/{info["vertical"]}')
        continue

    # Mark ALL but ONE as already notified (so only 1 triggers a message)
    notify_target = changed[0]
    for s in changed[1:]:
        s.last_notified_hash = s.compute_change_hash()
        s.save(update_fields=["last_notified_hash"])

    print(f'  📦 {phone} ({info["country"]}/{info["vertical"]}):')
    print(f"     Target shipment: {notify_target.invoice_no} | AWB: {notify_target.awb_no}")
    print(f"     Status: {notify_target.status} | Consignee: {notify_target.consignee_name}")
    print(f"     Marked {len(changed)-1} others as already-notified")

    # Build and send the Engati message directly
    dashboard_url = DashboardService.get_url_for_phone(phone)
    params = {
        "sc_shpmnt_no": notify_target.awb_no or notify_target.invoice_no or str(notify_target.source_eid),
        "sc_consignee_name": notify_target.consignee_name,
        "sc_inv_no": notify_target.invoice_no,
        "sc_inv_date": notify_target.invoice_date.strftime("%d-%m-%Y") if notify_target.invoice_date else "",
        "sc_etd": notify_target.etd.strftime("%d-%m-%Y") if notify_target.etd else "",
        "sc_current_status": notify_target.status,
        "sc_remarks": (notify_target.remarks or "")[:200],
        "sc_detail_url": dashboard_url,
    }

    result = EngatiService.send(phone, params)
    send_results[phone] = result

    if result.get("success"):
        # Mark as notified
        notify_target.last_notified_hash = notify_target.compute_change_hash()
        notify_target.whatsapp_sent_status = True
        notify_target.whatsapp_sent_at = timezone.now()
        notify_target.save(update_fields=["last_notified_hash", "whatsapp_sent_status", "whatsapp_sent_at"])
        print(f'     ✓ Message sent successfully (status {result.get("status_code")})')
    else:
        error = result.get("error") or result.get("body", "Unknown")
        print(f"     ✗ Send failed: {error}")

    test(
        f'Engati send to {phone} for {info["country"]}',
        result.get("success", False),
        f'Error: {result.get("error") or result.get("body")}' if not result.get("success") else "",
    )


# ═════════════════════════════════════════════════════════════════════════════
# TEST 9: Post-notification state verification
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 9: Post-notification state verification")
print("─" * 80)

for phone, info in PHONE_ASSIGNMENTS.items():
    if phone not in send_results or not send_results[phone].get("success"):
        continue

    # The shipment we sent a notification for should now be marked as notified
    shipments = ExportShipment.objects.filter(
        country__iexact=info["country"],
        vertical__iexact=info["vertical"],
        whatsapp_sent_status=True,
    )
    test(f"{phone}: notified shipments have whatsapp_sent_status=True", shipments.exists())

    # Re-running detection should NOT flag the same shipment
    recently_notified = shipments.order_by("-whatsapp_sent_at").first()
    if recently_notified:
        test(
            f"{phone}: notified shipment NOT flagged for re-notification",
            not recently_notified.has_changed_since_last_notification,
        )


# ═════════════════════════════════════════════════════════════════════════════
# TEST 10: Full WhatsAppNotificationService.send_grouped_notifications()
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 80)
print("TEST 10: Full notification service run (production simulation)")
print("─" * 80)

# First, simulate a status change on 1 shipment per phone
simulated_changes = {}
for phone, info in PHONE_ASSIGNMENTS.items():
    # Grab a shipment that HAS been notified (so we can change it)
    notified = (
        ExportShipment.objects.filter(
            country__iexact=info["country"],
            vertical__iexact=info["vertical"],
        )
        .exclude(last_notified_hash="")
        .first()
    )

    if notified:
        old_status = notified.status
        old_remarks = notified.remarks
        notified.status = "STATUS_UPDATED_TEST"
        notified.remarks = f'Test change at {timezone.now().strftime("%H:%M:%S")} for {phone}'
        notified.save(update_fields=["status", "remarks"])
        simulated_changes[phone] = {
            "shipment": notified,
            "old_status": old_status,
            "old_remarks": old_remarks,
        }
        print(f"  📝 {phone}: Changed shipment {notified.invoice_no}")
        print(f'     Status: "{old_status}" → "STATUS_UPDATED_TEST"')
        test(f"{phone}: modified shipment now flagged as changed", notified.has_changed_since_last_notification)

# Run the full notification service
print("\n  Running WhatsAppNotificationService.send_grouped_notifications()...\n")
result = WhatsAppNotificationService.send_grouped_notifications()

print(f"  Service result: {result}")
test(
    "Notification service sent messages",
    result.get("sent", 0) > 0,
    f'sent={result.get("sent")}, errors={result.get("errors")}',
)
test("No notification errors", result.get("errors", 0) == 0, f'errors={result.get("errors")}')

# Verify the simulated changes were notified
for phone, change_info in simulated_changes.items():
    shipment = change_info["shipment"]
    shipment.refresh_from_db()
    test(f"{phone}: changed shipment was notified (whatsapp_sent_at updated)", shipment.whatsapp_sent_at is not None)
    test(f"{phone}: hash updated after notification", not shipment.has_changed_since_last_notification)

# Restore original data
for phone, change_info in simulated_changes.items():
    shipment = change_info["shipment"]
    shipment.status = change_info["old_status"]
    shipment.remarks = change_info["old_remarks"]
    shipment.last_notified_hash = shipment.compute_change_hash()  # Keep it in sync
    shipment.save(update_fields=["status", "remarks", "last_notified_hash"])
    print(f"  ↩ {phone}: Restored shipment {shipment.invoice_no} to original status")


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("TEST RESULTS")
print("=" * 80)
print(f"\n  Total:  {passed + failed}")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")

if errors:
    print("\n  Failed tests:")
    for e in errors:
        print(f"    ✗ {e}")

print("\n" + "─" * 80)
print("DASHBOARD URLS (verify data isolation in browser):")
print("─" * 80)
for phone, info in PHONE_ASSIGNMENTS.items():
    url = DashboardService.get_url_for_phone(phone)
    count = ExportShipment.objects.filter(country__iexact=info["country"], vertical__iexact=info["vertical"]).count()
    print(f'\n  {phone} → {info["country"]}/{info["vertical"]} ({count} shipments)')
    print(f"  {url}")

print("\n" + "─" * 80)
print("WHATSAPP MESSAGES:")
print("─" * 80)
for phone, info in PHONE_ASSIGNMENTS.items():
    print(f'  {phone}: Check WhatsApp for notification about {info["country"]}/{info["vertical"]} shipment')

print("\n" + "=" * 80)
if failed == 0:
    print("✅ ALL TESTS PASSED - System ready for production")
else:
    print(f"⚠️  {failed} TESTS FAILED - Review errors above")
print("=" * 80 + "\n")

sys.exit(0 if failed == 0 else 1)
