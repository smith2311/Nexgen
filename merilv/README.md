# Meril-V

Django app for warehouse stock validation and outbound dispatch scanning.
Mounted at `/meril-v/` (see `core/urls.py`). For the end-user feature tour,
see [`docs/MERILV_MODULE_GUIDE.md`](../docs/MERILV_MODULE_GUIDE.md) — this file
is for whoever is running, configuring or developing the app itself.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Setup](#setup)
3. [Role-Based Access](#role-based-access)
4. [Master Data](#master-data)
5. [End-to-End Walkthrough With Dummy Data](#end-to-end-walkthrough-with-dummy-data)
6. [Running Tests](#running-tests)
7. [Known Limitations](#known-limitations)

---

## Architecture

```
merilv/
├── models.py           All tables: Entity, master data, sessions, scans, history
├── views.py             Validator, outbound scanner, extractor, branch transfer, consolidator
├── masters.py           Master-data console (materials, customers, couriers, cartons, org, entities)
├── roles.py             Role assignment (who gets Viewer/Picker/Admin)
├── zva05.py             ZVA05 auto-assign (separate module, same pattern as views.py)
├── inbound.py           Inbound Scanner — the receiving half of a branch transfer
├── scanfeed.py          Shared "every scan attempt" feed behind the console status filters
├── monitoring.py         Live "who's working on what" board
├── rbac.py              Group-based permission predicates
├── admin.py             Django admin registrations
├── urls.py              All routes, namespace "merilv"
├── services/            Business logic — NO Django imports, unit-tested standalone
│   ├── barcode.py        GS1/legacy barcode parser, driven by EntityProfile
│   ├── validation.py      Stock reconciliation engine (exact/batch/material tiers)
│   ├── outbound.py        Outbound scan → delivery-line matching + reason codes
│   ├── ingest.py          Spreadsheet readers (SAP dumps, master data, CSV/XLSX)
│   ├── extractor.py       Barcode extractor: extract / live scan / live validate
│   ├── transfers.py       Branch transfer picking + receiving + ZVA05 (material-only)
│   ├── export.py          Excel workbook builders (matches VendorLink's xlsx style)
│   ├── consolidator.py    Invoice PDF/spreadsheet parsing
│   └── entities.py        Bridges Entity model ↔ service dataclasses, seed data
├── management/commands/
│   └── seed_merilv.py     Creates the 5 default entities + carton profiles
└── tests/                212 tests — parser, engines, ingestion, every view, roles

templates/merilv/         All templates (project-level dir, per platform convention)
static/merilv/            merilv.css (scanner console styling) + scanner.js (shared scan-input logic)
```

**Why services/ has no Django imports:** the barcode parser and matching
engines are the part of this app most likely to need a fix under pressure —
"why did this scan get rejected?" — and they're much faster to reason about
and test as plain Python against dicts and dataclasses than through the ORM
and HTTP layer. `services/entities.py` is the only file that bridges the two
worlds (turning an `Entity` row into an `EntityProfile`/`ValidationSettings`
dataclass).

**Why one Validator and one Outbound Scanner instead of one per business
unit:** the legacy tool this replaces had 8 near-identical pages (one
validator + one scanner per unit), each a copy-paste fork with its own drift
and bugs. Here, every per-unit difference is a field on `Entity` — see
`services/barcode.EntityProfile`, `services/validation.ValidationSettings`,
and `services/outbound.OutboundSettings`. Adding a business unit is a new
`Entity` row, not new code.

## Setup

```bash
# From inside the running Django container (see the project's Podman notes for
# how to get a shell there):
python manage.py migrate merilv
python manage.py seed_merilv          # creates MHPL, MLSIPL, CARDIO, MICRO, MDPL + carton profiles
```

`seed_merilv` is idempotent — re-running it does nothing to existing records.
Pass `--reset` to overwrite tuned entity configuration back to the defaults
(only do this deliberately; it undoes any admin customisation).

No extra environment variables are needed. Meril-V uses the platform's
existing database, auth, and file storage.

## Role-Based Access

Access follows the platform-wide pattern: plain Django Groups, checked by
predicate functions in `rbac.py`, wrapped in `user_passes_test` decorators on
each view.

| Tier | Group name | Can do |
|---|---|---|
| Viewer | `Meril-V Viewer` | History, reports, monitoring — read-only |
| Picker | `Meril-V Picker` | Everything a Viewer can, plus scanning, validation, transfers |
| Admin | `Meril-V Admin` | Everything, plus Master Data (materials, customers, couriers, cartons, entities, org structure, role assignment) |

`rbac.py` also grants access automatically to Django superusers and to any
`@merillife.com` account with `is_staff=True` — those never need an explicit
group.

**Where it's managed:** Master Data → **Roles & Access**
(`/meril-v/masters/roles/`). Search for a user by username/name/email, pick a
role, and grant it. A user holds exactly one Meril-V role at a time —
assigning a new one replaces the old one, so it's always unambiguous which
permission is actually in effect. Removing access clears all three groups at
once. An admin cannot remove their own access from this page (prevents
accidental lockout — a superuser or another admin can still do it via the
Django admin if truly needed).

**In code**, the three tiers are plain predicate functions:

```python
from merilv.rbac import can_access_merilv, can_operate_merilv, is_merilv_admin

can_access_merilv(user)   # True for Viewer, Picker, Admin, superuser, staff
can_operate_merilv(user)  # True for Picker, Admin, superuser, staff
is_merilv_admin(user)     # True for Admin, superuser
```

Every view in the app is decorated with one of these three (see the top of
`views.py`, `masters.py`, `zva05.py`, `inbound.py`, `monitoring.py`).

## Master Data

Everything editable from **Meril-V → Master Data** (`/meril-v/masters/`),
admin-only:

| Master | List/manage | Single "Add" form | Bulk import |
|---|---|---|---|
| Materials | `masters/materials/` | ✅ `masters/materials/new/` | ✅ Excel/CSV |
| Customers | `masters/customers/` | ✅ `masters/customers/new/` | ✅ Excel/CSV |
| Couriers | `masters/couriers/` | ✅ inline form | — |
| Cartons | `masters/boxes/` | ✅ inline form | — |
| Organisation (company/org/plant/storage/billing/sales type) | `masters/organisation/` | ✅ inline forms per table | — |
| Entities (business units) | `masters/entities/` | ✅ `masters/entities/new/` | — |
| Roles & Access | `masters/roles/` | ✅ (see above) | — |

Bulk import for Materials/Customers has a **Replace** checkbox — checking it
deletes all existing rows for that entity/table before importing. Leave it
unchecked to merge (update existing codes, add new ones).

Adding a new **Entity** only asks for a code, display name and legal company
name; it's created with plain GS1 defaults (serials on, standard bracketed
barcode parsing, no special legacy formats) and redirects straight into the
full configuration page to tune anything that specific unit needs.

## End-to-End Walkthrough With Dummy Data

This section is a literal, copy-pasteable script for trying the whole system
with fake data — useful for training, demos, or verifying a deployment.
Everything below assumes you're logged in as (or have been granted) a
**Meril-V Admin**.

### Step 0 — Grant yourself/a test user access

1. Go to **Meril-V → Master Data → Roles & Access**.
2. Search for your test user, pick **Meril-V Picker** (or **Meril-V Admin** if
   they should also manage master data), click **Grant**.

### Step 1 — Confirm a business unit exists

`seed_merilv` already created `MHPL`, `MLSIPL`, `CARDIO`, `MICRO`, `MDPL`. For
this walkthrough we'll use **MHPL** (serials on, standard barcode format).

If you want your own throwaway unit instead: Master Data → Entities → **Add
entity** → code `DEMO`, name `Demo Unit` → **Create & configure rules** → just
save the defaults on the next screen.

### Step 2 — Add a dummy material

Master Data → Materials → **Add material**:

| Field | Value |
|---|---|
| Business unit | `MHPL` |
| Scan code | `DEMO01` |
| SAP material code | *(leave blank — defaults to `DEMO01`)* |
| Description | `Demo Bone Screw 4.5mm` |

Click **Add material**. This is what lets a barcode carrying `DEMO01` resolve
to a real item.

### Step 3 — Add a dummy customer

Master Data → Customers → **Add customer**:

| Field | Value |
|---|---|
| Customer code | `CUST-DEMO` |
| Customer name | `Demo Hospital` |
| Business unit | `MHPL` |
| Plant | `WGJ1` |
| Default courier partner | `Demo Logistics` |

Click **Add customer**.

### Step 4 — Outbound Scanner: upload a dummy delivery dump

Save the following as `demo_delivery.csv` (this matches a YVL06 export's
column names, which the ingest reader looks for):

```csv
Delivery No.,Item,Material,Batch,Delivery quantity,Plant,Sold-to party,Name 1
DL-DEMO-001,10,DEMO01,BATCH01,3,WGJ1,CUST-DEMO,Demo Hospital
```

This says: delivery `DL-DEMO-001` needs **3 units** of `DEMO01`, batch
`BATCH01`, for Demo Hospital.

1. Go to **Meril-V → Outbound Scanner**.
2. Select business unit `MHPL`.
3. Upload `demo_delivery.csv` as the delivery dump, give the session a name
   (e.g. "Demo run"), click **Load**.
4. You land on the scanner console. The right-hand panel shows delivery
   `DL-DEMO-001` at 0/3.

### Step 5 — Scan against it

In the scan box, type (or paste) each of these and press **Enter** one at a
time:

| Barcode | What happens |
|---|---|
| `(240)DEMO01(10)BATCH01(21)SN001` | ✅ Accepted — assigned to `DL-DEMO-001`, count becomes 1/3 |
| `(240)DEMO01(10)BATCH01(21)SN002` | ✅ Accepted — 2/3 |
| `(240)DEMO01(10)BATCH01(21)SN001` *(same again)* | ❌ **Duplicate scan** — this exact serial is already packed |
| `(240)DEMO01(10)WRONGBATCH(21)SN003` | ❌ **Wrong batch** — tells you it needs `BATCH01` |
| `(240)NOTREAL(10)BATCH01(21)SN004` | ❌ **Wrong material** — not required anywhere in this shipment |
| `(240)DEMO01(10)BATCH01(21)SN003` | ✅ Accepted — 3/3, delivery shows complete |
| `(240)DEMO01(10)BATCH01(21)SN004` | ❌ **Over-scan** — every line for this material is already full |

Try **Undo** after a scan — it removes the last one and drops the count back.
Try clicking the delivery card in the right panel to **focus** on it, then
scanning something for a different (nonexistent) delivery to see **target
mismatch** behaviour.

### Step 6 — Finish the delivery

1. Click **Finish delivery**, choose `DL-DEMO-001`.
2. Fill in courier (or leave blank to use the customer's default — Demo
   Logistics, from Step 3), a docket number, vehicle number.
3. Submit. Since all 3 units were scanned, it completes cleanly — no
   discrepancy report.
4. Find it under **Meril-V → Delivery History**, and export the SAP-ready
   serial upload sheet from there.

*(To see the discrepancy report instead: repeat from Step 4 with a fresh
delivery dump, but only scan 2 of the 3 required units before finishing.)*

### Step 7 — Stock Validator

Save two more files.

`demo_stock.csv` (a dummy SAP stock dump):

```csv
Material,Batch,Serial No,Unrestricted,Plant,SLED/BBD
DEMO01,BATCH01,SN101,1,WGJ1,2027-12-31
DEMO01,BATCH01,SN102,1,WGJ1,2027-12-31
DEMO01,BATCH02,SN103,1,WGJ1,2026-10-15
```

`demo_scan.csv` (a dummy physical scan file):

```csv
Scan,Condition
(240)DEMO01(10)BATCH01(21)SN101,Good
(240)DEMO01(10)BATCH01(21)SN999,Good
(240)DEMO01(10)BATCH02(21)SN103(17)261015,Good
```

Note the third barcode carries its own `(17)261015` — a GS1 expiry date
(`YYMMDD`, so 15 Oct 2026). This matters because the **Near/Short-Expiry/
Expired** classification is worked out from the expiry printed on the
*physical item's own barcode*, not from the SAP dump's `SLED/BBD` column —
that column is only used for the separate **Expiry Mismatch** check (comparing
the two, and flagging if they disagree by more than 7 days). Adjust the date
above to land inside MHPL's configured near/short-expiry window relative to
whenever you actually run this, so it demonstrates the flag rather than
showing plain "Good Stock".

1. Go to **Meril-V → Stock Validator**, select `MHPL`.
2. Upload `demo_stock.csv` as the SAP dump, `demo_scan.csv` as the scan file.
3. Click **Validate**. Expect:
   - `SN101` → **Matched** (exact material + batch + serial hit).
   - `SN999` → **Batch Match / Serial Diff** (material + batch match `BATCH01`,
     but SAP has no `SN999` — matched to a different physical unit under the
     same batch).
   - `BATCH02`'s `SN103` → **Matched**, classified as **Near Expiry** (from
     the barcode's own `(17)` date) and shown on the export's Expiry tab.
4. Export the workbook to see the full multi-sheet breakdown (Results /
   Damage / Expiry / Summary).

### Step 8 — Watch it live

Open **Meril-V → Monitoring** in a second tab while a session is open — it
shows your outbound session, who's operating it, what it's focused on, and
flips from **Working** to **Idle** if you stop scanning for 30 minutes.

### Step 9 — (Optional) Branch Transfer / ZVA05

Both follow the same shape as Outbound, but match on **material only** (no
batch check):

**Branch Transfer** — `bt_demo.csv`:
```csv
Unique Number,Supply Node Code,Node Code,Storage Location,Receiving Parent Node Code,Receiving Node Code,SKU Code,Quantity,Intransit Color Zone,Mode
U1,SN1,WGJ1,RT01,RP1,WGJ7,DEMO01,2,Green,Road
```
Upload under **Branch Transfer**, then scan `(240)DEMO01(10)ANYBATCH(21)SN201`
— it's accepted regardless of batch, since the plan doesn't specify one.

### Step 9b — (Optional) Receive the branch transfer back in with the Inbound Scanner

The transfer you just picked has only left the sending plant — nothing has been
booked in at the other end yet. **Meril-V → Inbound Scanner** closes that loop:

1. Go to **Inbound Scanner**. The plan appears in the dropdown *because it has picked
   quantity* — plans nobody has picked are deliberately not offered, since every
   scan against them would be refused.
2. Give the load a trip reference / vehicle / docket if you have one, then
   **Open receiving session**.  (The session is a goods receipt; the tool is the
   Inbound Scanner.)
3. Scan the same barcodes. Note what the console expects: **dispatched**
   quantity, not planned. A line planned for 5 that shipped 2 expects 2 —
   measuring against the plan would report a permanent phantom shortfall.
4. Things worth trying:
   - the same serial twice → **Duplicate serial**
   - one more than was dispatched → **Over received**
   - a material on the plan the sender never picked → **Nothing dispatched**
     (a *warning*, not an error — the goods are real, the paperwork is wrong)
   - a material not on the transfer at all → **Material not in plan**
5. **Close receipt.** Anything still outstanding is reported as short in
   transit, and the export carries a Planned / Dispatched / Received / Short
   column set per line.

**ZVA05** — `zva05_demo.csv`:
```csv
Material,Confirmed Qty,Customer,Sales Document,Item
DEMO01,2,CUST-DEMO,SO-DEMO-1,10
```
Upload under **ZVA05 Auto-Assign**, then scan the same barcode format — again
matched on material only, against the ordered quantity.

---

## Running Tests

```bash
podman exec django_app python -m pytest merilv/tests/ -q --ds=core.settings --nomigrations
```

`--nomigrations` is required — see the note below. Currently **212 tests**
cover the barcode parser (all format variants), the reconciliation engine
(match tiers, smart sort, post-checks), the outbound engine (every rejection
reason code), the inbound engine (dispatched-vs-planned, transit shortfall),
spreadsheet ingestion, every page end-to-end, and role assignment.

## Known Limitations

- **`--nomigrations` is required to run tests.** The platform's `main` app has
  a pre-existing migration bug unrelated to Meril-V (`force_password_change`
  is added twice across `main/migrations/0001_initial.py` and `0002_...py`),
  which breaks the migration chain on a fresh test database. Not something
  this app can fix from inside `merilv/`.
- **nginx + Podman**: if pages 502 right after a rebuild while `:7000` direct
  still works, that's nginx caching a stale container IP for `django_app` —
  run `podman restart nexgen_nginx`. Unrelated to any Meril-V code.
- **Invoice Consolidator (PDF mode)** is best-effort — layouts vary too much
  to guarantee perfect extraction, so every row carries a confidence score
  rather than a guarantee.
- **Backup/restore and live broadcast** (both PRD Phase-3 items in the legacy
  spec) are not implemented.
