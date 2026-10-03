"""Seed the VendorLink demo dataset straight into the database.

Covers the whole implemented chain: self-registrations (approved / pending /
rejected), the Vendor Master records they produce, vendor portal logins with
real generated temp passwords, admin scores, RFI requests across three
countries, and multiple price bids per request including revisions.

Deliberately does NOT decide winners. No evaluation rows are created — run the
AI evaluator from the request page (or `evaluate_vendorlink_bids`) and let the
model rank the bids and pick the best price itself.

    python manage.py seed_vendorlink_demo            # create / update
    python manage.py seed_vendorlink_demo --reset    # wipe demo rows, recreate
    python manage.py seed_vendorlink_demo --send-mail  # also email credentials
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from vendorlink.models import (
    CapabilityAssessment,
    Vendor,
    VendorBid,
    VendorCommercial,
    VendorEvaluation,
    VendorKPIRecord,
    VendorRegistration,
    VendorRequest,
    VendorScore,
    VendorScorecard,
)

# ── Test inboxes ────────────────────────────────────────────────────────────
# The six real addresses belong to the primary demo vendors. Extra vendors use
# +tag variants, which deliver to the same inbox but stay unique (the public
# form refuses a duplicate pending/approved email).
REAL = {
    "smith": "smithkansara23@gmail.com",
    "shivam": "shivamkansara23@gmail.com",
    "gpt": "onemerilgpt@gmail.com",
    "himanshu": "singh.himashu903333@gmail.com",
    "kailash": "kailashkoli839@gmail.com",
    "kailash2": "kailashkoli@outlook.com",
}
NAMES = {
    "smith": "Smith Kansara",
    "shivam": "Shivam Kansara",
    "gpt": "Meril GPT",
    "himanshu": "Himanshu Singh",
    "kailash": "Kailash Koli",
    "kailash2": "Kailash Koli",
}


def addr(key, label=None):
    """addr('smith') -> the real address; addr('smith', 'pending') -> +tag variant."""
    a = REAL[key]
    if not label:
        return a
    local, domain = a.split("@", 1)
    return f"{local}+{label}@{domain}"


C = "country"
# key, company, country, city, warehouse, contact-key, email-label, capability flags
# caps order: gdp, iso, temp, bonded, customs, lastmile, regulatory, sfdc, edi
VENDORS = [
    # ── Germany ──
    dict(company="PharmaLog Deutschland AG", country="Germany", state="Hesse", city="Frankfurt",
         warehouse="Cargo City Sud, Gebaude 553, 60549 Frankfurt",
         who="smith", label=None, mobile="+49 69 4820 1133",
         website="https://pharmalog-de.example",
         caps=[1, 1, 1, 1, 1, 1, 1, 0, 1],
         score=dict(facility=5, support=4, compliance=5, delivery=5,
                    notes="Two 2025 cold-chain launches with zero temperature excursions.")),
    dict(company="EuroCold Logistics SE", country="Germany", state="Hamburg", city="Hamburg",
         warehouse="Sudweststrasse 24, 21107 Hamburg",
         who="shivam", label=None, mobile="+49 40 7712 8890",
         website="https://eurocold.example",
         caps=[1, 1, 1, 1, 1, 1, 1, 1, 1],
         score=dict(facility=5, support=5, compliance=5, delivery=4,
                    notes="Only vendor with full 9/9 coverage. Premium pricing.")),
    dict(company="BudgetFreight GmbH", country="Germany", state="North Rhine-Westphalia", city="Duisburg",
         warehouse="Hafenstrasse 88, 47119 Duisburg",
         who="gpt", label=None, mobile="+49 203 5540 2210",
         website="https://budgetfreight.example",
         caps=[1, 1, 0, 0, 1, 1, 0, 0, 0],
         score=dict(facility=3, support=3, compliance=2, delivery=3,
                    notes="Ambient only. Cheap, but no cold-chain and no bonded facility.")),
    dict(company="Rhein Storage KG", country="Germany", state="North Rhine-Westphalia", city="Koln",
         warehouse="Industriepark Nord 12, 50829 Koln",
         who="himanshu", label=None, mobile="+49 221 3390 4471",
         website="https://rheinstorage.example",
         caps=[1, 1, 1, 1, 1, 1, 1, 0, 0],
         score=dict(facility=4, support=2, compliance=2, delivery=2,
                    blacklisted=True,
                    blacklist_reason="Two temperature excursions in the Q1-2026 audit; "
                                     "corrective actions not closed.",
                    notes="Do not award until a re-audit clears. Cheap quotes are misleading.")),
    dict(company="Nordwind Kontraktlogistik GmbH", country="Germany", state="Bremen", city="Bremen",
         warehouse="Am Speicher XI 7, 28217 Bremen",
         who="smith", label="nordwind", mobile="+49 421 6690 3312",
         website="https://nordwind-kl.example",
         caps=[1, 1, 1, 0, 1, 1, 1, 1, 1],
         score=dict(facility=4, support=4, compliance=4, delivery=5,
                    notes="Strong ambient contract logistics; no bonded facility.")),
    # ── India ──
    dict(company="Sunrise Supply Chain Pvt. Ltd.", country="India", state="Maharashtra", city="Mumbai",
         warehouse="Plot 14, MIDC Andheri East, Mumbai 400093",
         who="kailash", label=None, mobile="+91 22 6612 4400",
         website="https://sunrisescm.example",
         caps=[1, 1, 1, 0, 1, 1, 1, 1, 1],
         score=dict(facility=4, support=5, compliance=4, delivery=4,
                    notes="Very responsive. Strong last-mile across western India.")),
    dict(company="Meridian Coldchain India", country="India", state="Maharashtra", city="Pune",
         warehouse="Survey 88/2, Chakan MIDC Phase II, Pune 410501",
         who="kailash2", label=None, mobile="+91 20 4715 9080",
         website="https://meridiancc.example",
         caps=[1, 1, 1, 1, 1, 0, 1, 0, 1],
         score=dict(facility=4, support=3, compliance=4, delivery=4,
                    notes="Good cold storage; no last-mile network, needs a partner.")),
    dict(company="Apex Pharma Logistics Ltd.", country="India", state="Gujarat", city="Ahmedabad",
         warehouse="Block C, Sanand GIDC, Ahmedabad 382110",
         who="kailash", label="apex", mobile="+91 79 4008 7711",
         website="https://apexpharmalog.example",
         caps=[1, 1, 1, 1, 1, 1, 1, 0, 0],
         score=dict(facility=5, support=4, compliance=5, delivery=4,
                    notes="Bonded + cold storage in Gujarat. No EDI/SFDC integration yet.")),
    # ── UAE ──
    dict(company="Gulf Medipharm Logistics LLC", country="United Arab Emirates",
         state="Dubai", city="Dubai", warehouse="Warehouse 7, Dubai Logistics City, Jebel Ali",
         who="gpt", label="gulf", mobile="+971 4 883 5510",
         website="https://gulfmedipharm.example",
         caps=[1, 1, 1, 1, 1, 1, 1, 1, 0],
         score=dict(facility=5, support=4, compliance=5, delivery=4,
                    notes="GDP-certified, bonded, strong customs desk at Jebel Ali.")),
    dict(company="Emirates MedLogix FZE", country="United Arab Emirates",
         state="Sharjah", city="Sharjah", warehouse="Hamriyah Free Zone, Phase 2, Sharjah",
         who="shivam", label="medlogix", mobile="+971 6 526 4480",
         website="https://emiratesmedlogix.example",
         caps=[1, 1, 0, 1, 1, 1, 0, 0, 1],
         score=dict(facility=3, support=4, compliance=3, delivery=3,
                    notes="Bonded and customs-capable but ambient only; no regulatory desk.")),
    # ── Singapore / Netherlands ──
    dict(company="Straits Pharma Logistics Pte Ltd", country="Singapore",
         state="Central Region", city="Singapore", warehouse="8 Changi South Lane, Singapore 486354",
         who="himanshu", label="straits", mobile="+65 6214 7730",
         website="https://straitspharma.example",
         caps=[1, 1, 1, 1, 1, 1, 1, 1, 0],
         score=dict(facility=5, support=4, compliance=5, delivery=5,
                    notes="APAC transshipment hub; excellent customs turnaround at Changi.")),
    dict(company="Randstad MedSupply B.V.", country="Netherlands",
         state="South Holland", city="Rotterdam", warehouse="Waalhaven Zuidzijde 47, 3087 Rotterdam",
         who="kailash2", label="randstad", mobile="+31 10 429 6650",
         website="https://randstadmedsupply.example",
         caps=[1, 1, 1, 0, 1, 1, 1, 0, 1],
         score=dict(facility=4, support=4, compliance=4, delivery=4,
                    notes="Solid EU gateway via Rotterdam port; no bonded facility on-site.")),
]

# Registrations that never became vendors — exercise pending / rejected states.
NON_VENDOR_REGS = [
    dict(company="Sentinel 3PL Singapore Pte Ltd", country="Singapore", state="Central Region", city="Singapore",
         warehouse="21 Changi North Way, Singapore 498774",
         who="shivam", label="pending", mobile="+65 6542 7180",
         website="https://sentinel3pl.example", caps=[1, 1, 1, 1, 1, 1, 1, 1, 1],
         status=VendorRegistration.STATUS_PENDING, reason=""),
    dict(company="Harbour Line Freight Ltd", country="United Kingdom", state="Suffolk", city="Felixstowe",
         warehouse="Unit 12, Dock Road, Felixstowe IP11 3TR",
         who="himanshu", label="pending", mobile="+44 1394 55 2210",
         website="https://harbourline.example", caps=[1, 0, 0, 1, 1, 0, 0, 0, 1],
         status=VendorRegistration.STATUS_PENDING, reason=""),
    dict(company="QuickMove Cargo Services", country="India", state="Gujarat", city="Surat",
         warehouse="Shed 3, Sachin GIDC, Surat 394230",
         who="gpt", label="rejected", mobile="+91 261 227 8890",
         website="https://quickmovecargo.example", caps=[0, 0, 0, 0, 1, 1, 0, 0, 0],
         status=VendorRegistration.STATUS_REJECTED,
         reason="No GDP or ISO certification and no temperature-controlled storage; "
                "cannot support pharma verticals."),
    dict(company="Delta Ambient Warehousing", country="Germany", state="Saxony", city="Leipzig",
         warehouse="Zum Wasserwerk 4, 04356 Leipzig",
         who="kailash", label="rejected", mobile="+49 341 2280 6690",
         website="https://deltaambient.example", caps=[0, 1, 0, 0, 0, 1, 0, 0, 0],
         status=VendorRegistration.STATUS_REJECTED,
         reason="Ambient-only single site; no customs clearance for imported stock."),
]

#: The capability codes the per-vendor `caps` tuples above are positional
#: against, in order. Kept as a literal list rather than read from the master:
#: the seed data hard-codes nine booleans per vendor, so if the master were
#: read at runtime a tenth capability would silently shift every vendor's
#: answers one column across. Add a capability here and to every VENDORS entry
#: together, or not at all.
CAP_CODES = ["gdp_certification", "iso_certification", "temperature_capability",
             "bonded_warehouse", "customs_clearance", "last_mile_distribution",
             "regulatory_support", "sfdc_integration", "edi_capability"]


def _capability_rows(codes):
    """Capability master rows for ``codes``, seeding the master if it is empty.

    A fresh database seeded before any migration has run its data pass would
    otherwise attach nothing, and the demo would come up with every vendor
    showing no capabilities at all.
    """
    from vendorlink.capabilities import ensure_seeded
    from vendorlink.models import Capability

    ensure_seeded()
    by_code = {c.code: c for c in Capability.objects.filter(code__in=codes)}
    return [by_code[code] for code in codes if code in by_code]


def _declared(spec):
    """The Capability rows one VENDORS/REGISTRATIONS spec says yes to."""
    yes = [code for code, flag in zip(CAP_CODES, spec["caps"]) if flag]
    return _capability_rows(yes)


# ── Requests. Bids are keyed by company name; the AI picks the winner. ──────
REQUESTS = [
    dict(
        key="de-cold",
        subject="Cold-chain warehousing & hospital distribution — Germany",
        country="Germany",
        verticals="Pharmaceutical implants, sterile medical devices",
        scope="<p>Bonded storage of imported pharma stock at 2-8 &deg;C, customs clearance "
              "on inbound air freight, and last-mile distribution to hospitals across "
              "Germany. GDP compliance and regulatory documentation support required.</p>",
        space=("800", "1200"), temp=("2", "8"), monthly=("400", "600"),
        inbound=("40", "60"), outbound=("380", "560"),
        opens=-15, closes=-1, currency="EUR",
        bids=[
            # company, revision totals (each revision is one bid), notes
            ("BudgetFreight GmbH", [42000],
             "Ambient storage only; cold-chain can be sub-contracted on request."),
            ("Rhein Storage KG", [47000],
             "Full cold-chain available at our Koln site."),
            ("PharmaLog Deutschland AG", [62000, 58000],
             "Revised: 6% off storage after volume review. Includes GDP-validated 2-8 C zone."),
            ("EuroCold Logistics SE", [71500],
             "Premium tier with full EDI + SFDC integration included."),
            ("Nordwind Kontraktlogistik GmbH", [53500],
             "Cold-chain via partner site; no bonded facility of our own."),
        ],
    ),
    dict(
        key="in-dual",
        subject="Ambient + cold storage and distribution — India (West)",
        country="India",
        verticals="Cardiovascular stents, orthopaedic implants",
        scope="<p>Dual-zone warehousing (ambient and 2-8 &deg;C) near Mumbai/Pune with "
              "last-mile delivery to distributors across Maharashtra and Gujarat. "
              "EDI integration with SAP is required.</p>",
        space=("1500", "2000"), temp=("2", "8"), monthly=("900", "1200"),
        inbound=("70", "90"), outbound=("850", "1150"),
        opens=-9, closes=7, currency="INR",
        bids=[
            ("Meridian Coldchain India", [1079200],
             "Cold storage only — last-mile to be handled by Meril's nominated carrier."),
            ("Sunrise Supply Chain Pvt. Ltd.", [1340000, 1285000],
             "Revised after volume review. SAP EDI bridge included at no extra cost."),
            ("Apex Pharma Logistics Ltd.", [1198000],
             "Bonded + dual-zone at Sanand. EDI not supported; manual ASN files instead."),
        ],
    ),
    dict(
        key="ae-bonded",
        subject="Bonded warehousing & GCC re-export — UAE",
        country="United Arab Emirates",
        verticals="Diagnostics reagents, in-vitro devices",
        scope="<p>Bonded warehouse at Jebel Ali for consolidation and re-export across the "
              "GCC. Requires customs clearance and regulatory support for MoH "
              "registration. Ambient 15-25 &deg;C storage.</p>",
        space=("600", "900"), temp=("15", "25"), monthly=("250", "400"),
        inbound=("30", "45"), outbound=("230", "380"),
        opens=-4, closes=12, currency="AED",
        bids=[
            ("Emirates MedLogix FZE", [252000],
             "Hamriyah bonded facility. No in-house regulatory desk."),
            ("Gulf Medipharm Logistics LLC", [308000, 296500],
             "Revised. Jebel Ali bonded warehouse; MoH regulatory desk included."),
        ],
    ),
    dict(
        key="de-ambient",
        subject="Ambient overflow warehousing & EDI integration — Germany",
        country="Germany",
        verticals="Orthopaedic instrument sets, consumables",
        scope="<p>Ambient overflow warehousing with pick &amp; pack for instrument sets, "
              "plus EDI integration into Meril's SAP instance for ASN and inventory "
              "messaging. No temperature control needed.</p>",
        space=("1800", "2400"), temp=("", ""), monthly=("700", "900"),
        inbound=("55", "75"), outbound=("640", "830"),
        opens=-2, closes=18, currency="EUR",
        bids=[
            ("BudgetFreight GmbH", [28500],
             "Cheapest ambient option. No EDI capability — flat files by SFTP instead."),
            ("Nordwind Kontraktlogistik GmbH", [34200],
             "Full EDI + SFDC integration; Bremen site has capacity from August."),
            ("EuroCold Logistics SE", [39900],
             "Ambient zone at Hamburg with native EDI messaging."),
            ("PharmaLog Deutschland AG", [37400],
             "Ambient overflow at Frankfurt. EDI via middleware partner."),
        ],
    ),
]

# The seven bid cost lines, as a share of the quoted total.
COST_SPLIT = [
    ("storage_cost", Decimal("0.50")),
    ("inbound_cost", Decimal("0.15")),
    ("outbound_cost", Decimal("0.20")),
    ("pick_cost", Decimal("0.05")),
    ("packing_cost", Decimal("0.05")),
    ("last_mile_charges", Decimal("0.05")),
]


def split_total(total, has_last_mile=True):
    """Break a quoted total into the model's seven cost fields, exactly summing to it."""
    total = Decimal(str(total))
    out, used = {}, Decimal("0")
    split = list(COST_SPLIT)
    if not has_last_mile:
        # Re-weight the last-mile share onto storage for vendors that can't deliver.
        split = [(f, w + (Decimal("0.05") if f == "storage_cost" else Decimal("0")))
                 for f, w in split if f != "last_mile_charges"]
    for field, weight in split[:-1]:
        val = (total * weight).quantize(Decimal("0.01"))
        out[field] = val
        used += val
    out[split[-1][0]] = (total - used).quantize(Decimal("0.01"))
    if not has_last_mile:
        out["last_mile_charges"] = Decimal("0.00")
    out["manpower_charges"] = Decimal("0.00")
    return out


# ── Phase 2: Commercial Database rate cards ─────────────────────────────────
# Deliberately skips the two newest vendors (Straits Pharma, Randstad
# MedSupply) so the module still shows a vendor with "no rate card yet" —
# an all-green dataset would hide what the empty state even looks like.
# Dates are offsets from seed-time "today" so the expiry states (ok / soon /
# expired) stay meaningful no matter when this command is run.
COMMERCIAL_CARDS = [
    dict(company="PharmaLog Deutschland AG", currency="EUR",
         storage=14.50, inbound=3.20, outbound=4.80, pick=0.90, packing=1.10,
         customs=6.50, last_mile=2.40, manpower=1.80,
         additional="Fuel surcharge 4.5%; peak-season handling +8% Nov-Dec.",
         insurance="All-risk, EUR 3m in warehouse / EUR 1m in transit",
         contract_start_days_ago=100, contract_length_days=365, price_validity_days=200,
         notes="Long-standing GDP partner; renews on the same cycle as the Frankfurt lease."),
    dict(company="EuroCold Logistics SE", currency="EUR",
         storage=19.90, inbound=4.10, outbound=5.60, pick=1.20, packing=1.40,
         customs=7.80, last_mile=3.10, manpower=2.20,
         additional="Cold-chain monitoring & data-logger reporting included.",
         insurance="All-risk, EUR 5m in warehouse / EUR 2m in transit",
         contract_start_days_ago=60, contract_length_days=365, price_validity_days=180,
         notes="Premium tier — full 9/9 capability coverage justifies the higher rate."),
    dict(company="BudgetFreight GmbH", currency="EUR",
         storage=8.20, inbound=1.90, outbound=2.60, pick=0.50, packing=0.60,
         customs=None, last_mile=1.10, manpower=0.90,
         additional="No cold-chain surcharge — ambient only.",
         insurance="Basic, EUR 500k in warehouse",
         contract_start_days_ago=335, contract_length_days=365, price_validity_days=25,
         notes="Cheapest quote on file; renegotiate before the validity window closes."),
    dict(company="Rhein Storage KG", currency="EUR",
         storage=11.00, inbound=2.50, outbound=3.30, pick=0.70, packing=0.80,
         customs=5.20, last_mile=1.90, manpower=1.30,
         additional="",
         insurance="All-risk, EUR 2m in warehouse",
         contract_start_days_ago=370, contract_length_days=365, price_validity_days=-10,
         notes="Expired alongside the blacklist — do not renew until the re-audit clears."),
    dict(company="Nordwind Kontraktlogistik GmbH", currency="EUR",
         storage=12.80, inbound=2.90, outbound=3.90, pick=0.80, packing=0.95,
         customs=5.90, last_mile=2.10, manpower=1.50,
         additional="Bremen site has spare capacity from Q4.",
         insurance="All-risk, EUR 2.5m in warehouse / EUR 800k in transit",
         contract_start_days_ago=45, contract_length_days=365, price_validity_days=150,
         notes=""),
    dict(company="Sunrise Supply Chain Pvt. Ltd.", currency="INR",
         storage=420.00, inbound=95.00, outbound=140.00, pick=25.00, packing=30.00,
         customs=210.00, last_mile=65.00, manpower=48.00,
         additional="SAP EDI bridge included at no extra cost.",
         insurance="All-risk, INR 2 crore in warehouse / INR 60 lakh in transit",
         contract_start_days_ago=80, contract_length_days=365, price_validity_days=220,
         notes="Very responsive account team — first call for West India volumes."),
    dict(company="Meridian Coldchain India", currency="INR",
         storage=510.00, inbound=110.00, outbound=160.00, pick=None, packing=None,
         customs=240.00, last_mile=None, manpower=52.00,
         additional="Cold storage only — pair with a last-mile partner.",
         insurance="All-risk, INR 1.5 crore in warehouse",
         contract_start_days_ago=300, contract_length_days=365, price_validity_days=40,
         notes="Price validity closing soon — flag for renewal review."),
    dict(company="Apex Pharma Logistics Ltd.", currency="INR",
         storage=460.00, inbound=100.00, outbound=150.00, pick=28.00, packing=32.00,
         customs=225.00, last_mile=60.00, manpower=50.00,
         additional="",
         insurance="All-risk, INR 1.8 crore in warehouse / INR 50 lakh in transit",
         contract_start_days_ago=20, contract_length_days=365, price_validity_days=300,
         notes="Bonded + cold storage combo at Sanand; no EDI yet."),
    dict(company="Gulf Medipharm Logistics LLC", currency="AED",
         storage=48.00, inbound=11.00, outbound=15.50, pick=3.20, packing=3.80,
         customs=22.00, last_mile=8.50, manpower=6.00,
         additional="MoH regulatory desk included in the storage rate.",
         insurance="All-risk, AED 8m in warehouse / AED 2m in transit",
         contract_start_days_ago=90, contract_length_days=365, price_validity_days=190,
         notes=""),
    dict(company="Emirates MedLogix FZE", currency="AED",
         storage=39.00, inbound=9.20, outbound=12.80, pick=2.60, packing=3.00,
         customs=18.50, last_mile=7.00, manpower=5.20,
         additional="",
         insurance="Basic, AED 3m in warehouse",
         contract_start_days_ago=380, contract_length_days=365, price_validity_days=-20,
         notes="Lapsed — renew before quoting this vendor again."),
]

# ── Phase 3: 3PL Capability Assessment intake pipeline ──────────────────────
# Six prospective 3PLs (none of them in Vendor Master yet), one per status, so
# every tab on the assessment list actually has something in it. The
# "approved" one is also promoted into a real Vendor Master record by
# _seed_assessments() to demonstrate the full loop end to end.
_FULL_ASSESSMENT_ANSWERS = {
    "medtech_experience": "9 years serving MedTech clients across cardiovascular and orthopaedic verticals; references available on request.",
    "client_base": "Two global implant manufacturers and one diagnostics distributor.",
    "last_mile_delivery_desc": "Yes — owned fleet covers same-day hospital delivery within a 150km radius.",
    "cha_services": "Yes — in-house CHA desk handles import/export documentation and duty assessment.",
    "wms_name": "SAP EWM v9.5",
    "warehouse_size_sqm": "6,500",
    "operating_hours": "Monday-Saturday 07:00-20:00",
    "order_cutoff_time": "16:00 same-day dispatch",
    "warehouse_cutoff_time": "18:30",
    "emergency_delivery_cutoff": "22:00, metro coverage only, 3hr SLA",
    "returns_handling": "Dedicated returns bay with QA quarantine hold before restock or disposal.",
    "temperature_controlled_storage": "2-8C and 15-25C zones, continuous data-logger monitoring with SMS alerting.",
    "consignment_handling": "Manages consignment stock for 4 hospital accounts with monthly replenishment reporting.",
    "order_to_cash_service": "Yes — order processing, invoicing and collections support available as an add-on.",
    "batch_lot_tracking": "Full batch/lot tracking down to shelf location in the WMS.",
    "serial_number_tracking": "Serial tracking available for high-value implant lines.",
    "iso_9001_certified": "Certificate #ISO9001-2024-1187, valid through 2027.",
    "iso_13485_experience": "Two years operating an ISO 13485-aligned quality process, certification in progress.",
    "gdp_compliance": "GDP certificate #GDP-2025-0456, renewed annually, last audit clean.",
    "regulatory_support_desc": "In-house regulatory affairs contact for import registrations and MoH liaison.",
    "insurance_coverage": "Warehouse and goods-in-transit, combined limit of USD 4m.",
    "wms_platform": "SAP EWM v9.5",
    "real_time_inventory_visibility": "Yes, via customer-facing portal updated in real time.",
    "dashboard_reporting": "Weekly and monthly standard reports; custom reports available within 3 business days.",
    "order_tracking_visibility": "Track-and-trace portal plus proactive exception alerts by email.",
    "why_choose_us": "Purpose-built MedTech facility, strong regulatory bench, and same-day emergency delivery capability.",
    "cons": "No dedicated SFDC integration yet; EDI is file-based rather than real-time.",
    "local_presence": "Single site, but covers the full metro region with owned fleet.",
    "global_presence": "Regional only — no presence outside the home country today.",
    "dedicated_account_manager": "Yes — named account manager assigned at contract signing.",
    "implementation_timeline": "6-8 weeks from signed contract to first live shipment.",
    "remarks": "Open to a 90-day pilot before committing to a full annual contract.",
}

ASSESSMENTS = [
    dict(company="", email="prospect.andes@example.com", status="invited", fields={}),
    dict(company="TransBaltic Freight OU", email="prospect.transbaltic@example.com",
         status="draft", fields={
             "company_name": "TransBaltic Freight OU",
             "warehouse_location_desc": "Lootsi 4, 10151 Tallinn, Estonia",
             "wms_name": "Still deciding between two WMS vendors",
         }),
    dict(company="Kestrel Pharma Warehousing Ltd", email="prospect.kestrel@example.com",
         status="submitted", fields=dict({
             "company_name": "Kestrel Pharma Warehousing Ltd",
             "warehouse_location_desc": "Mombasa Road Industrial Area, Nairobi, Kenya",
         }, **_FULL_ASSESSMENT_ANSWERS)),
    dict(company="Meridian Gulf Cold Storage LLC", email="prospect.meridiangulf@example.com",
         status="under_review", fields=dict({
             "company_name": "Meridian Gulf Cold Storage LLC",
             "warehouse_location_desc": "Rusayl Industrial Estate, Muscat, Oman",
         }, **_FULL_ASSESSMENT_ANSWERS)),
    dict(company="Alpine MedTech Logistics AG", email="prospect.alpine@example.com",
         status="approved", promote_country="Switzerland", fields=dict({
             "company_name": "Alpine MedTech Logistics AG",
             "warehouse_location_desc": "Industriestrasse 12, 8304 Wallisellen, Switzerland",
         }, **_FULL_ASSESSMENT_ANSWERS),
         review_notes="Strong regulatory bench and emergency delivery SLA. Approved for onboarding."),
    dict(company="Value Cargo Basic Services", email="prospect.valuecargo@example.com",
         status="rejected", fields={
             "company_name": "Value Cargo Basic Services",
             "warehouse_location_desc": "Tejgaon Industrial Area, Dhaka, Bangladesh",
             "operating_hours": "Monday-Friday 09:00-17:00",
             "why_choose_us": "Lowest storage rate in the region.",
             "cons": "No cold-chain, no GDP certification, ambient-only single site.",
         },
         review_notes="No temperature-controlled storage and no GDP certification — does not meet the "
                       "minimum compliance bar for pharma/MedTech freight."),
]

# ── Phase 4: KPI Dashboard monthly history ──────────────────────────────────
# 5 months of history (this month back to 4 months ago) for a handful of
# vendors, chosen to tell a story: EuroCold is consistently excellent, Rhein
# Storage is visibly deteriorating (matching its blacklist), the rest sit in
# the realistic middle.
KPI_HISTORY = {
    "EuroCold Logistics SE": [
        dict(months_ago=4, inventory_accuracy=99.1, on_time_delivery=98.0, order_fulfilment_accuracy=98.5,
             damage_rate=0.2, response_time_hours=2.0, capa_closure_days=5.0, internal_complaints=0, cost_per_shipment=41.50),
        dict(months_ago=3, inventory_accuracy=99.0, on_time_delivery=98.4, order_fulfilment_accuracy=98.8,
             damage_rate=0.1, response_time_hours=1.8, capa_closure_days=4.0, internal_complaints=0, cost_per_shipment=42.00),
        dict(months_ago=2, inventory_accuracy=99.3, on_time_delivery=97.9, order_fulfilment_accuracy=99.0,
             damage_rate=0.2, response_time_hours=2.1, capa_closure_days=4.5, internal_complaints=1, cost_per_shipment=41.80),
        dict(months_ago=1, inventory_accuracy=99.2, on_time_delivery=98.6, order_fulfilment_accuracy=99.1,
             damage_rate=0.1, response_time_hours=1.9, capa_closure_days=3.0, internal_complaints=0, cost_per_shipment=42.30),
        dict(months_ago=0, inventory_accuracy=99.4, on_time_delivery=98.8, order_fulfilment_accuracy=99.2,
             damage_rate=0.1, response_time_hours=1.7, capa_closure_days=3.5, internal_complaints=0, cost_per_shipment=42.10),
    ],
    "Rhein Storage KG": [
        dict(months_ago=4, inventory_accuracy=95.0, on_time_delivery=92.0, order_fulfilment_accuracy=93.5,
             damage_rate=1.8, response_time_hours=6.0, capa_closure_days=14.0, internal_complaints=2, cost_per_shipment=27.00),
        dict(months_ago=3, inventory_accuracy=93.5, on_time_delivery=90.2, order_fulfilment_accuracy=92.0,
             damage_rate=2.4, response_time_hours=7.5, capa_closure_days=18.0, internal_complaints=3, cost_per_shipment=27.40),
        dict(months_ago=2, inventory_accuracy=91.0, on_time_delivery=87.5, order_fulfilment_accuracy=89.8,
             damage_rate=3.1, response_time_hours=9.0, capa_closure_days=22.0, internal_complaints=4, cost_per_shipment=28.10),
        dict(months_ago=1, inventory_accuracy=89.2, on_time_delivery=85.0, order_fulfilment_accuracy=87.5,
             damage_rate=3.9, response_time_hours=11.0, capa_closure_days=27.0, internal_complaints=5, cost_per_shipment=28.60),
        dict(months_ago=0, inventory_accuracy=87.0, on_time_delivery=82.4, order_fulfilment_accuracy=85.0,
             damage_rate=4.6, response_time_hours=13.5, capa_closure_days=31.0, internal_complaints=6, cost_per_shipment=29.20),
    ],
    "Sunrise Supply Chain Pvt. Ltd.": [
        dict(months_ago=2, inventory_accuracy=96.5, on_time_delivery=95.0, order_fulfilment_accuracy=96.0,
             damage_rate=0.6, response_time_hours=3.5, capa_closure_days=8.0, internal_complaints=1, cost_per_shipment=780.00),
        dict(months_ago=1, inventory_accuracy=97.0, on_time_delivery=95.8, order_fulfilment_accuracy=96.4,
             damage_rate=0.5, response_time_hours=3.2, capa_closure_days=7.0, internal_complaints=0, cost_per_shipment=790.00),
        dict(months_ago=0, inventory_accuracy=97.4, on_time_delivery=96.2, order_fulfilment_accuracy=96.8,
             damage_rate=0.4, response_time_hours=3.0, capa_closure_days=6.5, internal_complaints=0, cost_per_shipment=800.00),
    ],
    "Gulf Medipharm Logistics LLC": [
        dict(months_ago=2, inventory_accuracy=97.8, on_time_delivery=96.5, order_fulfilment_accuracy=97.0,
             damage_rate=0.3, response_time_hours=2.5, capa_closure_days=6.0, internal_complaints=0, cost_per_shipment=145.00),
        dict(months_ago=1, inventory_accuracy=98.0, on_time_delivery=96.9, order_fulfilment_accuracy=97.3,
             damage_rate=0.3, response_time_hours=2.4, capa_closure_days=5.5, internal_complaints=1, cost_per_shipment=147.50),
        dict(months_ago=0, inventory_accuracy=98.1, on_time_delivery=97.2, order_fulfilment_accuracy=97.5,
             damage_rate=0.2, response_time_hours=2.2, capa_closure_days=5.0, internal_complaints=0, cost_per_shipment=148.00),
    ],
    "BudgetFreight GmbH": [
        dict(months_ago=1, inventory_accuracy=93.0, on_time_delivery=90.5, order_fulfilment_accuracy=91.0,
             damage_rate=1.2, response_time_hours=8.0, capa_closure_days=12.0, internal_complaints=2, cost_per_shipment=19.50),
        dict(months_ago=0, inventory_accuracy=93.4, on_time_delivery=91.0, order_fulfilment_accuracy=91.6,
             damage_rate=1.1, response_time_hours=7.5, capa_closure_days=11.0, internal_complaints=1, cost_per_shipment=19.80),
    ],
}

# ── Phase 4: Vendor Scorecard ────────────────────────────────────────────────
# Scores track each vendor's narrative from VENDORS above. Sunrise gets two
# entries so its history/re-score view has something to show.
SCORECARDS = [
    dict(company="EuroCold Logistics SE", months_ago=0,
         cost=7, cost_c="Premium pricing, but justified by the coverage.",
         serviceability=10, serviceability_c="Full 9/9 capability coverage, zero cold-chain excursions.",
         wms_reports=9, wms_reports_c="Native EDI/SFDC reporting, always on time.",
         tat=9, tat_c="Consistently beats the agreed TAT.",
         notes="Top-ranked 3PL this quarter."),
    dict(company="Gulf Medipharm Logistics LLC", months_ago=0,
         cost=8, cost_c="Competitive for a bonded Jebel Ali facility.",
         serviceability=8, serviceability_c="Strong customs desk, GDP-certified.",
         wms_reports=7, wms_reports_c="Reports are manual but accurate.",
         tat=8, tat_c="Meets TAT reliably.",
         notes=""),
    dict(company="PharmaLog Deutschland AG", months_ago=0,
         cost=6, cost_c="Mid-tier pricing for the German market.",
         serviceability=9, serviceability_c="Two cold-chain launches with zero excursions.",
         wms_reports=8, wms_reports_c="Good dashboard visibility.",
         tat=8, tat_c="On-time consistently.",
         notes=""),
    dict(company="Sunrise Supply Chain Pvt. Ltd.", months_ago=3,
         cost=8, cost_c="Very competitive West India rates.",
         serviceability=7, serviceability_c="Responsive but SAP EDI bridge was still pending.",
         wms_reports=6, wms_reports_c="Reports needed manual follow-up.",
         tat=7, tat_c="Occasional delays during monsoon season.",
         notes="First scoring round — baseline before EDI go-live."),
    dict(company="Sunrise Supply Chain Pvt. Ltd.", months_ago=0,
         cost=8, cost_c="Rates unchanged, still very competitive.",
         serviceability=9, serviceability_c="SAP EDI bridge now live; noticeably more responsive.",
         wms_reports=8, wms_reports_c="Automated reports since the EDI go-live.",
         tat=8, tat_c="Monsoon delays no longer an issue this quarter.",
         notes="Re-scored after EDI integration — clear improvement across the board."),
    dict(company="BudgetFreight GmbH", months_ago=0,
         cost=9, cost_c="Cheapest ambient quote on file.",
         serviceability=4, serviceability_c="No cold-chain, no bonded facility — limited scope of service.",
         wms_reports=3, wms_reports_c="Flat-file SFTP only, no real dashboard.",
         tat=5, tat_c="Meets TAT for ambient freight only.",
         notes="Use for low-value ambient overflow only."),
    dict(company="Rhein Storage KG", months_ago=0,
         cost=7, cost_c="Cheap, but the quote is misleading given the compliance risk.",
         serviceability=2, serviceability_c="Two temperature excursions, corrective actions still open.",
         wms_reports=3, wms_reports_c="Reporting has become unreliable in recent months.",
         tat=3, tat_c="TAT has been slipping for three months running.",
         notes="Matches the blacklist decision — do not award further volume."),
]


class Command(BaseCommand):
    help = "Seed the VendorLink demo dataset (registrations, vendors, logins, requests, bids)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                           help="Delete existing demo rows before seeding.")
        parser.add_argument("--send-mail", action="store_true",
                           help="Actually email credentials to the test inboxes.")

    # ──────────────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        demo_emails = {v["company"]: addr(v["who"], v["label"]) for v in VENDORS}
        all_emails = set(demo_emails.values()) | {
            addr(r["who"], r["label"]) for r in NON_VENDOR_REGS}
        companies = [v["company"] for v in VENDORS] + [r["company"] for r in NON_VENDOR_REGS]
        assessment_emails = {a["email"] for a in ASSESSMENTS}

        if opts["reset"]:
            self._reset(companies, all_emails, assessment_emails)

        with transaction.atomic():
            group, _ = Group.objects.get_or_create(name="VendorUser")
            vendors, creds = self._seed_vendors(group, opts["send_mail"])
            self._seed_non_vendor_regs()
            n_req, n_bid = self._seed_requests(vendors)
            n_cards = self._seed_commercial(vendors)
            n_kpi = self._seed_kpi(vendors)
            n_scores = self._seed_scorecards(vendors)
            n_assess, promoted = self._seed_assessments()

        self._report(vendors, creds, n_req, n_bid, n_cards, n_kpi, n_scores, n_assess, promoted)

    # ──────────────────────────────────────────────────────────────────────
    def _reset(self, companies, emails, assessment_emails):
        reg_qs = VendorRegistration.objects.filter(vendor_name__in=companies)
        ven_qs = Vendor.objects.filter(vendor_name__in=companies)
        req_qs = VendorRequest.objects.filter(subject__in=[r["subject"] for r in REQUESTS])
        assess_qs = CapabilityAssessment.objects.filter(vendor_email__in=assessment_emails)
        # A promoted assessment's vendor won't be in `companies` (its name comes
        # from the assessment, not the VENDORS list) — pick those up too.
        promoted_ven_qs = Vendor.objects.filter(
            pk__in=assess_qs.exclude(vendor__isnull=True).values_list("vendor_id", flat=True)
        )
        counts = {
            "bids": VendorBid.objects.filter(vendor_request__in=req_qs).count(),
            "evaluations": VendorEvaluation.objects.filter(vendor_request__in=req_qs).count(),
            "requests": req_qs.count(),
            "scores": VendorScore.objects.filter(vendor__in=ven_qs).count(),
            "registrations": reg_qs.count(),
            "commercial_cards": VendorCommercial.objects.filter(vendor__in=ven_qs).count(),
            "kpi_records": VendorKPIRecord.objects.filter(vendor__in=ven_qs).count(),
            "scorecards": VendorScorecard.objects.filter(vendor__in=ven_qs).count(),
            "assessments": assess_qs.count(),
            "promoted_vendors": promoted_ven_qs.count(),
            "vendors": ven_qs.count(),
            "users": User.objects.filter(username__in=emails).count(),
        }
        VendorBid.objects.filter(vendor_request__in=req_qs).delete()
        VendorEvaluation.objects.filter(vendor_request__in=req_qs).delete()
        req_qs.delete()
        VendorCommercial.objects.filter(vendor__in=ven_qs).delete()
        VendorKPIRecord.objects.filter(vendor__in=ven_qs).delete()
        VendorScorecard.objects.filter(vendor__in=ven_qs).delete()
        assess_qs.delete()
        promoted_ven_qs.delete()
        VendorScore.objects.filter(vendor__in=ven_qs).delete()
        reg_qs.delete()
        ven_qs.delete()
        User.objects.filter(username__in=emails).delete()
        self.stdout.write(self.style.WARNING(
            "reset: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v)))

    # ──────────────────────────────────────────────────────────────────────
    def _seed_vendors(self, group, send_mail):
        """Approved registration -> Vendor Master + score + portal login."""
        from main.models import UserProfile
        from vendorlink.views import _generate_vendor_password

        now = timezone.now()
        reviewer = (User.objects.filter(is_superuser=True).first()
                    or User.objects.filter(is_staff=True).first())

        vendors, creds = {}, []
        for spec in VENDORS:
            email = addr(spec["who"], spec["label"])
            contact = NAMES[spec["who"]]

            vendor, _ = Vendor.objects.update_or_create(
                vendor_name=spec["company"],
                defaults=dict(
                    country=spec["country"], state=spec["state"], city=spec["city"],
                    warehouse_location=spec["warehouse"], contact_person=contact,
                    email=email, mobile=spec["mobile"], website=spec["website"],
                    is_active=True,
                ),
            )
            # Capabilities are M2M links to the master now, so they cannot ride
            # in update_or_create's defaults.
            vendor.capabilities.set(_declared(spec))
            reg, _ = VendorRegistration.objects.update_or_create(
                vendor_name=spec["company"],
                defaults=dict(
                    country=spec["country"], state=spec["state"], city=spec["city"],
                    warehouse_location=spec["warehouse"], contact_person=contact,
                    email=email, mobile=spec["mobile"], website=spec["website"],
                    status=VendorRegistration.STATUS_APPROVED,
                    reviewed_by=reviewer, reviewed_at=now - timedelta(days=12),
                    rejection_reason="", vendor=vendor,
                ),
            )
            reg.capabilities.set(_declared(spec))
            s = spec["score"]
            VendorScore.objects.update_or_create(
                vendor=vendor,
                defaults=dict(
                    facility_rating=s["facility"], support_rating=s["support"],
                    compliance_rating=s["compliance"], delivery_reliability=s["delivery"],
                    is_blacklisted=s.get("blacklisted", False),
                    blacklist_reason=s.get("blacklist_reason", ""),
                    notes=s.get("notes", ""), scored_by=reviewer,
                ),
            )

            # Portal login with a real generated temp password.
            parts = contact.split(None, 1)
            user, created = User.objects.get_or_create(
                username=email,
                defaults=dict(email=email, first_name=parts[0],
                              last_name=parts[1] if len(parts) > 1 else "", is_active=True),
            )
            pw = _generate_vendor_password()
            user.set_password(pw)
            user.email = email
            user.save()
            user.groups.add(group)
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.force_password_change = True
            profile.save()
            creds.append((spec["company"], email, pw))

            if send_mail:
                self._mail_credentials(reg, pw)

            vendors[spec["company"]] = dict(vendor=vendor, reg=reg, user=user)
        return vendors, creds

    def _mail_credentials(self, reg, pw):
        from django.conf import settings
        from django.core.mail import send_mail as _send
        site = getattr(settings, "SITE_URL", "") or "https://localhost:8443"
        try:
            _send(
                subject="Meril VendorLink — Approved & Login Credentials",
                message=(
                    f"Dear {reg.contact_person},\n\n"
                    f"Your vendor registration for {reg.vendor_name} has been approved.\n\n"
                    f"  Login URL : {site}/\n"
                    f"  Username  : {reg.email}\n"
                    f"  Password  : {pw}\n\n"
                    "This is a temporary password — you will be asked to set a new one "
                    "on first login.\n\nRegards,\nMeril Life Sciences — Global SCM"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[reg.email],
                fail_silently=True,
            )
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"  mail to {reg.email} failed: {exc}"))

    # ──────────────────────────────────────────────────────────────────────
    def _seed_non_vendor_regs(self):
        now = timezone.now()
        reviewer = (User.objects.filter(is_superuser=True).first()
                    or User.objects.filter(is_staff=True).first())
        for spec in NON_VENDOR_REGS:
            rejected = spec["status"] == VendorRegistration.STATUS_REJECTED
            reg, _ = VendorRegistration.objects.update_or_create(
                vendor_name=spec["company"],
                defaults=dict(
                    country=spec["country"], state=spec["state"], city=spec["city"],
                    warehouse_location=spec["warehouse"],
                    contact_person=NAMES[spec["who"]],
                    email=addr(spec["who"], spec["label"]),
                    mobile=spec["mobile"], website=spec["website"],
                    status=spec["status"],
                    reviewed_by=reviewer if rejected else None,
                    reviewed_at=now - timedelta(days=5) if rejected else None,
                    rejection_reason=spec["reason"], vendor=None,
                ),
            )
            reg.capabilities.set(_declared(spec))

    # ──────────────────────────────────────────────────────────────────────
    def _seed_requests(self, vendors):
        now = timezone.now()
        staff = (User.objects.filter(is_superuser=True).first()
                 or User.objects.filter(is_staff=True).first())
        n_req = n_bid = 0

        for spec in REQUESTS:
            recipients = list(
                Vendor.objects.filter(country=spec["country"], is_active=True)
                .exclude(email="").values_list("email", flat=True)
            )
            vr, _ = VendorRequest.objects.update_or_create(
                subject=spec["subject"],
                defaults=dict(
                    country=spec["country"],
                    intro_message="<p>Dear Partner,</p><p>Meril Life Sciences invites your "
                                  "quotation for the 3PL scope below.</p>",
                    header_message="<p>Meril Life Sciences Pvt. Ltd. — Global SCM</p>",
                    scope_of_work=spec["scope"],
                    footer_message="<p>Please submit your best pricing before the bid "
                                   "closing date.</p>",
                    product_verticals=spec["verticals"],
                    warehouse_space_from=spec["space"][0], warehouse_space_to=spec["space"][1],
                    storage_temp_from=spec["temp"][0], storage_temp_to=spec["temp"][1],
                    monthly_shipment_from=spec["monthly"][0],
                    monthly_shipment_to=spec["monthly"][1],
                    inbound_from=spec["inbound"][0], inbound_to=spec["inbound"][1],
                    outbound_from=spec["outbound"][0], outbound_to=spec["outbound"][1],
                    raised_by=staff,
                    recipients=",".join(recipients),
                    recipient_count=len(recipients),
                    status=(VendorRequest.STATUS_SENT if recipients
                            else VendorRequest.STATUS_NO_RECIPIENTS),
                    bid_open_at=now + timedelta(days=spec["opens"]),
                    bid_close_at=now + timedelta(days=spec["closes"]),
                ),
            )
            n_req += 1
            VendorBid.objects.filter(vendor_request=vr).delete()

            for company, totals, note in spec["bids"]:
                v = vendors.get(company)
                if not v:
                    self.stdout.write(self.style.WARNING(
                        f"  skipped bid: unknown vendor {company!r}"))
                    continue
                has_lm = v["vendor"].has_capability("last_mile_distribution")
                for i, total in enumerate(totals, start=1):
                    latest = i == len(totals)
                    VendorBid.objects.create(
                        vendor_request=vr, submitted_by=v["user"],
                        vendor_registration=v["reg"], currency=spec["currency"],
                        bid_number=i,
                        notes=note if latest else "Initial quote — superseded by a revision.",
                        status=VendorBid.STATUS_SUBMITTED,
                        # Staff flag the newest revision when a vendor re-quoted.
                        is_selected=latest and len(totals) > 1,
                        **split_total(total, has_lm),
                    )
                    n_bid += 1
        return n_req, n_bid

    # ──────────────────────────────────────────────────────────────────────
    def _seed_commercial(self, vendors):
        """Phase 2 — one standing rate card per vendor in COMMERCIAL_CARDS."""
        today = timezone.localdate()
        n = 0
        for spec in COMMERCIAL_CARDS:
            v = vendors.get(spec["company"])
            if not v:
                continue
            start = today - timedelta(days=spec["contract_start_days_ago"])
            VendorCommercial.objects.update_or_create(
                vendor=v["vendor"], currency=spec["currency"],
                defaults=dict(
                    storage_cost=spec["storage"], inbound_cost=spec["inbound"],
                    outbound_cost=spec["outbound"], pick_cost=spec["pick"],
                    packing_cost=spec["packing"], customs_clearance_cost=spec["customs"],
                    last_mile_charges=spec["last_mile"], manpower_charges=None,
                    additional_charges=spec["additional"],
                    contract_start=start,
                    contract_end=start + timedelta(days=spec["contract_length_days"]),
                    price_validity=today + timedelta(days=spec["price_validity_days"]),
                    insurance=spec["insurance"], notes=spec["notes"],
                ),
            )
            n += 1
        return n

    # ──────────────────────────────────────────────────────────────────────
    # Rate cards are quoted in the vendor's own currency — without this, KPI
    # records would silently default to USD and misstate INR/AED/EUR figures.
    KPI_CURRENCY = {
        "EuroCold Logistics SE": "EUR", "Rhein Storage KG": "EUR", "BudgetFreight GmbH": "EUR",
        "Sunrise Supply Chain Pvt. Ltd.": "INR", "Gulf Medipharm Logistics LLC": "AED",
    }

    def _seed_kpi(self, vendors):
        """Phase 4 — a few months of KPI history per vendor in KPI_HISTORY."""
        today = timezone.localdate().replace(day=1)
        n = 0
        for company, months in KPI_HISTORY.items():
            v = vendors.get(company)
            if not v:
                continue
            for row in months:
                months_ago = row["months_ago"]
                year = today.year
                month = today.month - months_ago
                while month <= 0:
                    month += 12
                    year -= 1
                period = date(year, month, 1)
                metrics = {k: val for k, val in row.items() if k != "months_ago"}
                metrics["currency"] = self.KPI_CURRENCY.get(company, "USD")
                VendorKPIRecord.objects.update_or_create(
                    vendor=v["vendor"], period=period, defaults=metrics,
                )
                n += 1
        return n

    # ──────────────────────────────────────────────────────────────────────
    def _seed_scorecards(self, vendors):
        """Phase 4 — weighted scoring events in SCORECARDS (some vendors twice,
        to demonstrate the re-score history view).

        Scorecards are immutable by design (a re-score is a new row), so there
        is no natural unique key to hang update_or_create off; idempotency is
        handled by checking for an existing entry on the same day before
        creating a new one.
        """
        staff = (User.objects.filter(is_superuser=True).first()
                 or User.objects.filter(is_staff=True).first())
        n = 0
        for spec in SCORECARDS:
            v = vendors.get(spec["company"])
            if not v:
                continue
            scored_at = timezone.now() - timedelta(days=spec["months_ago"] * 30)
            if VendorScorecard.objects.filter(vendor=v["vendor"], scored_at__date=scored_at.date()).exists():
                continue
            card = VendorScorecard.objects.create(
                vendor=v["vendor"],
                cost_score=spec["cost"], cost_comment=spec["cost_c"],
                serviceability_score=spec["serviceability"], serviceability_comment=spec["serviceability_c"],
                wms_reports_score=spec["wms_reports"], wms_reports_comment=spec["wms_reports_c"],
                tat_score=spec["tat"], tat_comment=spec["tat_c"],
                notes=spec["notes"], scored_by=staff,
            )
            # auto_now_add always stamps "now" on create — override to the
            # intended historical date so the re-score timeline is meaningful.
            VendorScorecard.objects.filter(pk=card.pk).update(scored_at=scored_at)
            n += 1
        return n

    # ──────────────────────────────────────────────────────────────────────
    def _seed_assessments(self):
        """Phase 3 — the intake pipeline in ASSESSMENTS, one per status.

        The "approved" entry is also promoted into a real Vendor Master
        record, so the whole invite -> fill -> review -> promote loop has at
        least one worked example sitting in the database already.
        """
        staff = (User.objects.filter(is_superuser=True).first()
                 or User.objects.filter(is_staff=True).first())
        reviewer_name = staff.get_full_name() if staff else "Global SCM"
        reviewer_name = reviewer_name or (staff.username if staff else "Global SCM")
        now = timezone.now()
        n, promoted = 0, []

        for spec in ASSESSMENTS:
            # get_or_create rather than update_or_create: the token must stay
            # stable across re-runs of this command, or every re-seed would
            # silently invalidate any assessment link already handed out.
            assessment, created = CapabilityAssessment.objects.get_or_create(
                vendor_email=spec["email"],
                defaults=dict(
                    token=CapabilityAssessment.generate_token(),
                    token_expires_at=now + timedelta(days=30),
                    invited_by=staff,
                    status=spec["status"],
                    **spec["fields"],
                ),
            )
            if not created:
                assessment.status = spec["status"]
                for field_name, value in spec["fields"].items():
                    setattr(assessment, field_name, value)

            if spec["status"] in ("submitted", "under_review", "approved", "rejected"):
                assessment.submitted_at = now - timedelta(days=10)
            if spec["status"] in ("approved", "rejected"):
                assessment.reviewed_by = reviewer_name
                assessment.reviewed_at = now - timedelta(days=3)
                assessment.review_notes = spec.get("review_notes", "")

            if spec["status"] == "approved" and spec.get("promote_country") and not assessment.vendor_id:
                vendor = Vendor.objects.update_or_create(
                    vendor_name=assessment.company_name,
                    defaults=dict(
                        country=spec["promote_country"], email=assessment.vendor_email,
                        warehouse_location=assessment.warehouse_location_desc[:300],
                        created_by=staff, is_active=True,
                    ),
                )[0]
                assessment.vendor = vendor
                promoted.append(vendor.vendor_name)

            assessment.save()
            n += 1
        return n, promoted

    # ──────────────────────────────────────────────────────────────────────
    def _report(self, vendors, creds, n_req, n_bid, n_cards, n_kpi, n_scores, n_assess, promoted):
        w = self.stdout.write
        w("")
        w(self.style.SUCCESS(
            f"Seeded {len(vendors)} vendors, "
            f"{VendorRegistration.objects.count()} registrations, "
            f"{n_req} requests, {n_bid} bids."))
        w(self.style.SUCCESS(
            f"Phase 2-4: {n_cards} rate cards, {n_kpi} KPI records, "
            f"{n_scores} scorecard entries, {n_assess} capability assessments."))
        w("")
        w("Bids per request:")
        for vr in VendorRequest.objects.order_by("id"):
            n = VendorBid.objects.filter(vendor_request=vr).count()
            if n:
                w(f"  [{vr.pk}] {vr.subject[:56]:<56} {n} bids  "
                  f"({vr.recipient_count} recipients, window {vr.bid_window_status})")
        w("")
        w("Capability Assessment pipeline (one per status):")
        for a in CapabilityAssessment.objects.filter(vendor_email__in=[s["email"] for s in ASSESSMENTS]).order_by("created_at"):
            w(f"  [{a.get_status_display():<14}] {a.company_name or '(no company name yet)':<38} {a.vendor_email}")
        if promoted:
            w("")
            w(self.style.SUCCESS(f"Promoted to Vendor Master: {', '.join(promoted)}"))
        w("")
        w("Vendor portal logins — temp password, forces a change on first login:")
        for company, email, pw in creds:
            w(f"  {email:<40} {pw:<10} {company}")
        w("")
        w(self.style.WARNING(
            "No evaluations were created on purpose — open a request and run the AI "
            "evaluator so the model picks the best-priced eligible vendor itself."))
