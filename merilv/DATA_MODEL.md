# Meril-V — Data Model & UI Contract

Single-file reference for rebuilding the Meril-V front end. Everything a new UI
needs is here: every table and field, every enum the UI must render, every screen
with the data it consumes and the actions it posts.

Mounted at `/meril-v/`, URL namespace `merilv`. Django app lives in `merilv/`.
Companion docs: [`README.md`](README.md) (setup/ops), [`../docs/MERILV_MODULE_GUIDE.md`](../docs/MERILV_MODULE_GUIDE.md) (end-user tour).

---

## Table of Contents

1. [What the module does](#1-what-the-module-does)
2. [Access tiers](#2-access-tiers)
3. [Entity — the configuration spine](#3-entity--the-configuration-spine)
4. [Master data](#4-master-data)
5. [Outbound scanning](#5-outbound-scanning)
6. [Stock validation](#6-stock-validation)
7. [Barcode extractor](#7-barcode-extractor)
8. [Branch transfer](#8-branch-transfer)
9. [ZVA05 auto-assign](#9-zva05-auto-assign)
10. [Invoice consolidation](#10-invoice-consolidation)
11. [Audit](#11-audit)
12. [Enums the UI must render](#12-enums-the-ui-must-render)
13. [Screen map](#13-screen-map)
14. [Domain rules a UI must respect](#14-domain-rules-a-ui-must-respect)

---

## 1. What the module does

Warehouse stock validation and outbound dispatch scanning. Six workflows:

| Workflow | Purpose | Matches on |
|---|---|---|
| **Stock Validator** | Reconcile a physical barcode scan file against a SAP stock dump | material + batch + serial (tiered) |
| **Outbound Scanner** | Pack against a YVL06 delivery dump, scan-by-scan | material + batch + serial |
| **Branch Transfer** | Pick stock for inter-plant movement | **material only** |
| **ZVA05 Auto-Assign** | Assign scans to open sales orders when deliveries don't exist in SAP yet | **material only** |
| **Barcode Extractor** | Parse/decode barcodes; live scan; live validate against a dump | n/a |
| **Invoice Consolidator** | Flatten many invoice PDFs/sheets into one export | n/a |

The legacy system had 8 near-identical pages (one validator + one scanner per
business unit). Here there is **one** of each, and every per-unit difference is a
field on `Entity`. Adding a business unit is a new row, not new code — so the UI
must never hardcode unit-specific behaviour.

---

## 2. Access tiers

Plain Django Groups, checked by predicates in `rbac.py`.

| Tier | Group name | Can do |
|---|---|---|
| Viewer | `Meril-V Viewer` | History, reports, monitoring — read-only |
| Picker | `Meril-V Picker` | + scanning, validation, transfers |
| Admin | `Meril-V Admin` | + master data, entity config, role assignment |

Also auto-granted to superusers and any `is_staff` user with an `@merillife.com`
address. A user holds **exactly one** tier — assigning a new one replaces the old.

```python
from merilv.rbac import can_access_merilv, can_operate_merilv, is_merilv_admin
```

Every view is decorated with one of these three. A UI should gate navigation the
same way: `is_mv_admin` is passed into template context on most pages.

---

## 3. Entity — the configuration spine

`Entity` is a business unit **and** the parsing/matching rules for it. Read these
values rather than assuming behaviour — the dummy-data generator and the engines
both do.

Seeded units: `MHPL`, `MLSIPL`, `CARDIO`, `MICRO`, `MDPL`.

### Identity
| Field | Type | Notes |
|---|---|---|
| `code` | char(20), unique | e.g. `MHPL` |
| `name` | char(150) | display name |
| `company_name` | char(200) | legal entity |
| `is_active` | bool | inactive units hidden from pickers |
| `sort_order` | small int | ordering in dropdowns |

### Barcode parsing behaviour
| Field | Default | Meaning |
|---|---|---|
| `strip_whitespace` | `True` | MHPL sets **False** — its scanners emit spaces inside the barcode |
| `supports_serial` | `True` | MLSIPL is `False` — tracks to batch level only |
| `ai21_is_material` | `False` | non-standard: GS1 `(21)` holds the material |
| `sanitise_batch` | `False` | strip punctuation + uppercase the batch |
| `allow_envelope` | `False` | accept `A…A` wrapper and `^`/`~` AI prefixes |
| `positional_walker` | `False` | walk unbracketed AIs positionally |
| `compact_gs1` | `False` | accept locked-order compact GS1 |
| `positional_rules` | `False` | length-keyed positional slicing |
| `trauma_legacy` | `False` | accept the Meril Trauma layout |
| `pseudo_serial_digits` | 5 | width of synthetic serial for non-serialised items (MHPL = 4) |
| `parse_rules_csv` | "" | one rule/line: `Len, MatStart, MatLen, BatStart, BatLen, MfgStart, MfgLen, ExpStart, ExpLen` |

### Expiry classification
| Field | Default | Meaning |
|---|---|---|
| `near_expiry_months` | 6 | MHPL = **5** |
| `short_expiry_months` | 3 | MHPL = **12** |
| `month_divisor` | 30.44 | days per month; MHPL = **30** |

⚠️ The two legacy families **disagree on which threshold is tighter**, so both are
explicit. For MHPL, Near (5) is tighter than Short (12) — do not assume ordering.

### Validation engine toggles
| Field | Default | Meaning |
|---|---|---|
| `match_concat` | `True` | try `batch+serial` against the SAP serial column |
| `concat_delimiter` | "" | joiner for the above |
| `match_batch` | `True` | enable the batch-match pass |
| `match_material` | `True` | enable the material-only pass |
| `triple_sort` | `True` | sort candidates plant → expiry → qty |
| `multi_row_consume` | `True` | one scan may span several SAP rows |
| `smart_zero` | `True` | match ignoring leading zeros |
| `use_scan_qty` | `True` | honour a quantity column in the scan file |
| `strict_batch` | `False` | compare batches strictly |
| `plant_priority` | "" | comma-separated, best first — MHPL: `WGJ1, WGJ7, WGJ4` |
| `good_storage_location` | `RT01` | target S.Loc for good stock |
| `damage_storage_location` | `DMG1` | target S.Loc for damaged stock |

### Outbound behaviour
| Field | Default | Meaning |
|---|---|---|
| `allow_customer_focus` | `False` | focus a whole customer, not just one delivery |
| `aggregate_delivery_lines` | `False` | sum qty across identical delivery/material/batch rows |
| `enforce_box_capacity` | `False` | reject scans once the selected carton is full |
| `allow_partial_export` | `False` | allow exporting an incomplete delivery |

**Derived:** `plant_priority_list` → uppercased list, best first.

### ToolSetting
Admin-managed JSON config, keyed by name (`key` unique, `value` JSONField). The
legacy `global_config` table. Lets ops retune behaviour without a deploy.

---

## 4. Master data

### MaterialMaster
Scan code ↔ SAP material code ↔ GTIN, **per entity**. Resolving between them is
step one of every scan.

| Field | Type | Notes |
|---|---|---|
| `entity` | FK → Entity | cascade |
| `scan_code` | char(100) | what's printed on the label |
| `material_code` | char(100) | what SAP calls it — **frequently different** |
| `material_description` | char(400) | |
| `gtin` | char(20) | GTIN/EAN |
| `material_group` | char(100) | |
| `raw_data` | JSON | the original import row |

Unique together: `(entity, scan_code)`. Indexed on `(entity, material_code)`, `gtin`, `scan_code`.
Currently loaded: **29,105 MHPL + 3,142 MLSIPL**.

### CustomerMaster
Ship-to / sold-to party plus the shipping metadata outbound needs.

`customer_code` (unique), `customer_name`, `entity` (FK, nullable), `company_code`,
`plant`, `courier_partner`, `courier_number`, `address`, `city`, `state`, `country`,
`pincode`, `gstin`, `email`, `mobile`, `contact_person`, `is_blocked`,
`blocked_reason`, `raw_data`.

### CourierMaster
`courier_name`, `plant`, `courier_number`, `vehicle_number`, `is_active`.

### CustomerCourierMapping
Plant-specific courier override, checked **before** the customer default when
finishing a delivery. `customer_code`, `entity`, `plant`, `courier_name`,
`courier_number`. Unique together `(customer_code, entity, plant)`.

### BoxProfile
Packaging carton. `code` (unique), `length_cm`, `width_cm`, `height_cm`,
`divisor` (default 5000), `max_qty` (**0 = unlimited**), `is_active`, `sort_order`.

**Derived:** `volumetric_weight` = `L×W×H / divisor`, 2dp.

### Organisation hierarchy
Four levels, each a separate table so the UI can render a tree:

```
CompanyMaster (company_code PK, company_name)
  └── OrganizationMaster (org_code PK, org_name, company FK)
        └── PlantMaster (plant_code PK, plant_name, organization FK nullable)
              └── StorageMaster (storage_code, storage_name, plant FK)
                    unique_together (storage_code, plant)
```
Plus two flat type masters: `BillingTypeMaster` (`billing_type` PK,
`billing_description`) and `SalesTypeMaster` (`sales_type` PK, `sales_description`).

Loaded: 67 plants, 1,462 storage locations, 103 sales types, 108 billing types.

⚠️ `PlantMaster.organization` is **nullable and currently unset** — the source SAP
export had no company/org code. A UI must tolerate orphan plants.

### UserPlantMapping
Restricts which plants a user may operate on. `user` FK, `plant` FK,
unique together.

---

## 5. Outbound scanning

```
DeliveryDump ──< DeliveryLine ──< OutboundScan >── OutboundSession
                                                        │
                                              ScanLog ───┤
                                        FinishedDelivery ┘
```

### DeliveryDump
One uploaded YVL06 extract. `entity` FK, `source_file`, `original_filename`,
`row_count`, `skipped_count`, `warnings` (JSON list), `uploaded_by`, `created_at`.

### DeliveryLine
One requirement: this much of this material, this batch.

**Identity:** `dump` FK, `entity` FK, `delivery_no`, `item_no`, `material`, `batch`,
`original_batch`, `batch_overridden` (bool — staff may override a batch)

**Location:** `plant`, `location`, `company_code`, `ean`

**Quantities:** `required_qty`, `scanned_qty` (both decimal 12,3)

**Parties / docs:** `delivery_date`, `customer_name`, `bill_to_code`, `bill_to_name`,
`ship_to_code`, `ship_to_name`, `ref_document`, `customer_po_no`, `port_of_loading`,
`discharging_place`, `final_destination`, `country_of_origin`

**Meta:** `source_items` (JSON — item numbers merged when the entity aggregates),
`sequence` (**order-sensitive**, the legacy matcher depends on it), `raw_data`

**Derived (use these in the UI):** `pending_qty` = `max(required − scanned, 0)`,
`is_complete` = `scanned >= required`

### OutboundSession
A picker's working session against one dump.

`entity` FK, `dump` FK (nullable), `name`, `status` (`active`/`completed`/`abandoned`),
`selected_box`, `operator` FK, timestamps.

**Focus** locks scanning to one delivery, or (if `allow_customer_focus`) to all
deliveries of one customer. The UI needs a clear focused/unfocused state.

It is **not** a column on this row. It used to be — `focus_delivery_no` and
`focus_customer_code` — and one value per session meant one value for everyone
working that session: a supervisor uploads a dump and the floor picks it, so
two pickers shared a single focus and one of them scanned into the delivery the
other had chosen. Focus now lives on `OperatorFocus` below, keyed on (record, user).
See `merilv/focus.py`.

**Focus is required, not optional.** Neither `outbound_scan` nor
`outbound_bulk_scan` will assign anything until one is set — they refuse with
`NO_FOCUS` (HTTP 409) before a single code is processed, and write nothing to
`ScanLog` for it. Unfocused, a scan is resolved against every open line in the
dump and lands on whichever delivery happens to want that material first, which
is a silent guess about the order being picked. `_session_payload` carries the
same rule to the console as `can_scan`.

A focused delivery is also the scope of everything the console shows: the
delivery list and the search box and All/Pending/Scanned buttons that filter it,
the per-line table, and the scan log (see `ScanLog.delivery_no`).

### OperatorFocus
Where one operator is standing in one record. Shared by all four consoles —
outbound, ZVA05, BT scanning and inbound receiving — not just this one.

`user` FK, `scope`, `value`, `customer_code`, `updated_at`.
Unique on (`user`, `scope`).

`scope` is `"{console}:{pk}"` — `outbound:41`, `zva05:7` — so one row answers
"where is this person in this record" and nothing else collides with it. `value`
holds a delivery number, order key or receiving plant depending on the console;
`customer_code` is used only by outbound, and only when
`Entity.allow_customer_focus` is on. The two are mutually exclusive: setting one
clears the other, and re-selecting what is already focused clears it.

Stored rather than kept in the HTTP session on purpose: it outlives a browser,
so a picker moving from a tablet to a bench workstation keeps their place, and
the live-operators board can report what each person is working on — which a
cookie could not answer.

### OutboundScan
One **accepted** scan, assigned to a line. `session` FK, `line` FK (nullable),
`serial_identity`, `serial_only`, `material`, `batch`, `mfg_code`, `expiry_code`,
`box`, `raw_scan`, `scanned_by`, `created_at`.

`serial_identity` is `BATCH||SERIAL` for serialised items, or a synthetic
`[BATCH] … (#n)` id otherwise.

### ScanLog
**Every** attempt, accepted or rejected — FR5 requires explicit reason codes.
`session` FK, `raw_scan`, `status` (`success`/`error`/`warning`/`duplicate`),
`reason_code`, `message`, `delivery_no`, `parsed` (JSON), `created_at`.

This is the feed for the live scan log panel. Render `reason_code` as a badge and
`message` as the operator-facing text.

`delivery_no` is which delivery the attempt was made against — the line it landed
on when accepted, otherwise the focused delivery. It is what lets the console
show one delivery's history on its own: an accepted scan can be traced through
`OutboundScan`, but a rejection belongs to no line, so without this there is
nothing tying it to the delivery being picked. Blank on rows written before the
column existed, and on the consoles whose target is not a delivery (ZVA05,
branch transfer) — the console shows an unattributed row rather than hiding it
on a guess.

### FinishedDelivery
A completed, exported delivery with courier and packaging details.

`entity` FK, `session` FK, `delivery_no`, `customer_code`, `customer_name`,
`courier_partner`, `courier_number`, `vehicle_number`, `docket_no`,
`mode_of_transit`, `vehicle_type`, `weight_kg`, `box_count`,
`boxes_used` (e.g. `"A:2,B:1"`), `is_partial`, `line_count`, `scanned_count`,
`payload` (JSON), `picker` FK, timestamps.

---

## 6. Stock validation

### ValidationRun
One reconciliation of a scan file against a SAP dump.

`entity` FK, `name`, `system_file`, `scan_file`, `system_filename`, `scan_filename`,
`settings_snapshot` (JSON — the config **as used**, for auditability),
`warnings` (JSON), `created_by`, `created_at`.

**KPIs (all decimal 14,3) — these drive the summary tiles:**
`total_scanned`, `total_system`, `matched_qty`, `batch_matched_qty`,
`material_matched_qty`, `variance_qty`, `reconciliation_pct` (decimal 6,2).

### ValidationRow
One scanned line and the verdict.

**As scanned:** `raw_scan`, `material`, `physical_batch`, `physical_serial`,
`scan_qty`, `consumed_qty`, `condition`, `expiry_date`

**As found in SAP:** `system_batch`, `system_serial`, `system_plant`,
`system_location`, `system_expiry`, `material_description`, `customer_code`,
`customer_name`, `billing_document`, `billing_date`

**Verdict:** `status`, `status_priority` (sort key), `match_type`, `stock_status`,
`target_location`, `detail`, `flags` (JSON list), `sequence`

A row carries **both** a `status` (how it matched) and a `stock_status` (what
condition the goods are in) — they are independent axes. A row can also carry
several `flags` at once. Design for that: badge + chips, not a single label.

---

## 7. Barcode extractor

### ScanSession
`entity` FK, `mode` (`extract`/`live_scan`/`live_validate`), `name`, `condition`,
`box_no`, `dump_file`, `column_map` (JSON), `is_finished`, `created_by`, timestamps.

Denormalised counters so history lists need no aggregation:
`total_count`, `found_count`, `not_found_count`, `duplicate_count`.

### ScanRecord
`session` FK, `raw_scan`, `material`, `scan_code`, `batch`, `serial`, `mfg_code`,
`expiry_code`, `description`, `result`, `comment`, `parsed` (JSON), `created_at`.

---

## 8. Branch transfer

### BranchTransferPlan
`entity` FK, `name`, `plan_file`, `stock_file` (MB52), `is_finished`,
`created_by`, timestamps.

### BranchTransferLine
`plan` FK, `unique_number`, `supply_node_code`, `node_code` (sender plant),
`storage_location`, `receiving_parent_node_code`, `receiving_node_code`
(receiver plant), `sku_code`, `quantity`, `picked_qty`, `intransit_color_zone`,
`mode`, `sequence`, `raw_data`.

**Derived:** `pending_qty` = `max(quantity − picked_qty, 0)`

All ten plan columns are mandatory on import. Matching is **material-only** —
any batch is accepted.

---

## 9. ZVA05 auto-assign

### Zva05Dump
`entity` FK, `name`, `source_file`, `original_filename`, `row_count`,
`skipped_count`, `is_finished`, `warnings`, `created_by`, timestamps.

The dump's own `focus_key` column is gone for the same reason
`OutboundSession`'s was — see `OperatorFocus` in §5. Note that
`Zva05Line.focus_key` below is a different thing and stays: it is a derived
grouping key on the line, not a record of where an operator is standing.

### Zva05Line
`dump` FK, `material`, `customer`, `sales_document`, `item_no`, `required_qty`,
`scanned_qty`, `sequence`, `raw_data`.

**Derived:** `focus_key` = `"{customer} - {sales_document}"` — how operators group
and focus. `pending_qty` = `max(required − scanned, 0)`

### Zva05Scan
`dump` FK, `line` FK, `material`, `batch`, `serial`, `raw_scan`, `scanned_by`,
`created_at`.

---

## 10. Invoice consolidation

### ConsolidationRun
`name`, `source_type` (`pdf`/`sheet`), `file_count`, `row_count`, `warnings`,
`created_by`, `created_at`.

### ConsolidationRow
`run` FK, `source_file`, `invoice_no`, `invoice_date`, `customer_name`, `material`,
`description`, `batch`, `quantity`, `rate`, `amount`, `data` (JSON), `sequence`.

⚠️ PDF extraction is **best-effort** — layouts vary too much to guarantee accuracy.
Every row should be shown as reviewable, not authoritative.

---

## 11. Audit

### ActivityLog
Module-level trail for master-data edits and config changes.
`actor` FK, `action`, `target_type`, `target_id`, `metadata` (JSON), `created_at`.
Indexed on `-created_at` and `(actor, -created_at)`.

---

## 12. Enums the UI must render

### ValidationRow.status — 14 values
| Value | Label |
|---|---|
| `matched` | Matched |
| `partial` | Batch Match / Serial Diff |
| `warning` | Material OK / Batch Diff |
| `variance` | Not found in SAP |
| `variance_partial` | Variance (Partial) |
| `batch_mismatch` | Batch Mismatch |
| `stock_exhausted` | Stock Exhausted |
| `master_missing` | Master Missing |
| `dupe_scan` | Duplicate Scan |
| `over_scan` | Over Scan |
| `plant_mismatch` | Plant Mismatch |
| `expiry_mismatch` | Expiry Mismatch |
| `incomplete` | Incomplete Barcode |
| `error` | Parse Error |

Suggested colour grouping: green (`matched`) · amber (`partial`, `warning`,
`plant_mismatch`, `expiry_mismatch`) · red (`variance`, `batch_mismatch`,
`dupe_scan`, `over_scan`, `error`) · grey (`master_missing`, `incomplete`,
`stock_exhausted`).

### ValidationRow.stock_status — 6 values
`good` · `damage` · `expired` · `near_expiry` · `short_expiry` · `no_expiry`

**Damage always wins over any expiry state** — a broken item is unsellable
regardless of date.

### Outbound ReasonCode — 16 values
`OK` · `NO_DELIVERY_DATA` · `UNPARSEABLE` · `EXPIRED_ITEM` · `MISSING_MATERIAL` ·
`MISSING_BATCH` · `DUPLICATE_SERIAL` · `DELIVERY_FULL` · `CUSTOMER_FULL` ·
`TARGET_MISMATCH` · `NO_FOCUS` · `WRONG_BATCH` · `OVERSCAN` · `WRONG_MATERIAL` ·
`BOX_FULL` · `MASTER_MISSING`

Since focus is required, a mis-picked item is explained by the *focused*
rejection path, which keeps the distinctions the unfocused one drew: the same
unit wanted on another delivery is `TARGET_MISMATCH` (naming that delivery), the
right material in a batch wanted nowhere is `WRONG_BATCH`, a material in no
delivery at all is `WRONG_MATERIAL`, and an item the focused delivery has
already filled is `DELIVERY_FULL` / `CUSTOMER_FULL`.

Each maps to an audio cue (PRD FR7) — `services/outbound.AUDIO_CUE`. A scanning UI
should play a distinct sound per outcome; pickers work by ear, not by reading.

`NO_FOCUS` is its own visual and audible category (`blocked` / `no_focus`), not a
duplicate or a rejection: nothing is wrong with the item, the operator has not
said where it is going. Sharing the duplicate family made the banner announce a
refused scan as DUPLICATE, which sends a picker to check the carton instead of
the delivery list.

### Other choice sets
- `ScanLog.status`: `success` · `error` · `warning` · `duplicate`
- `OutboundSession.status`: `active` · `completed` · `abandoned`
- `ScanSession.mode`: `extract` · `live_scan` · `live_validate`
- `ScanRecord.result`: `found` · `not_found` · `duplicate` · `match` · `mismatch` · `not_in_dump` · `excess`
- `ConsolidationRun.source_type`: `pdf` · `sheet`

---

## 13. Screen map

Namespace `merilv:`. **Tier** = minimum access needed.

### Launcher
| Route | Name | Tier |
|---|---|---|
| `/meril-v/` | `index` | Viewer |

### Stock Validator
| Route | Name | Tier | Notes |
|---|---|---|---|
| `validator/` | `validator` | Picker | upload form + last 10 runs |
| `validator/run/` | `validator_run` | Picker | POST: `system_file`, `scan_file`, `name`, entity |
| `validator/<pk>/` | `validation_detail` | Viewer | KPI tiles + row table |
| `validator/<pk>/export/` | `validation_export` | Viewer | multi-sheet xlsx |
| `validator/history/` | `validation_history` | Viewer | |

### Outbound Scanner
| Route | Name | Tier | Notes |
|---|---|---|---|
| `outbound/` | `outbound` | Picker | |
| `outbound/upload/` | `outbound_upload` | Picker | POST dump |
| `outbound/<pk>/` | `outbound_session` | Picker | **the scanning console** |
| `outbound/<pk>/scan/` | `outbound_scan` | Picker | POST one barcode → JSON |
| `outbound/<pk>/undo/` | `outbound_undo` | Picker | remove last scan |
| `outbound/<pk>/focus/` | `outbound_focus` | Picker | set/clear focus |
| `outbound/<pk>/state/` | `outbound_state` | Picker | **poll for live state** |
| `outbound/<pk>/finish/` | `outbound_finish` | Picker | courier/docket/box form |
| `outbound/<pk>/export/` | `outbound_export` | Picker | SAP serial upload sheet |
| `outbound/<pk>/line-batch/` | `outbound_line_batch` | Picker | override a line's batch |
| `outbound/<pk>/edit-scan/` | `outbound_edit_scan` | Picker | |

`scan`/`state` are the hot path — a new UI should keep them JSON and avoid full
page reloads.

### Other workflows
| Route | Name | Tier |
|---|---|---|
| `monitoring/` + `monitoring/state/` | `monitoring`, `monitoring_state` | Viewer |
| `zva05/`, `zva05/<pk>/` + `scan`/`undo`/`focus`/`finish`/`export` | `zva05*` | Picker |
| `deliveries/`, `deliveries/<pk>/` | `delivery_history`, `delivery_detail` | Viewer |
| `reports/` | `reports` | Viewer |
| `extractor/`, `extractor/parse/` | `extractor`, `extractor_parse` | Picker |
| `extractor/session/new/`, `<pk>/`, `<pk>/scan/`, `<pk>/columns/`, `<pk>/finish/`, `<pk>/export/` | `scan_session*` | Picker |
| `extractor/history/` | `scan_history` | Viewer |
| `branch-transfer/`, `<pk>/`, `<pk>/scan/`, `<pk>/export/` | `branch_transfer*` | Picker |
| `consolidator/`, `<pk>/`, `<pk>/export/` | `consolidator*` | Picker |

### Master Data console — Admin only
Every screen shares one chrome (hero card with the whole-module actions, plus the
master-picker dropdown), rendered by
`templates/merilv/_shell/masters_console.html` from context that
`masters._shell()` supplies. A screen opts in by passing `active_tab=` to `_ctx`
and including that file at the top of its `mv_body`.

The picker is a grouped `<select>` whose option values are the target URLs, so
navigation needs no lookup table mirroring `MASTER_TABS`. It replaced a
twelve-tab strip that did not fit a laptop viewport — half the tables sat behind
a horizontal scrollbar users did not find.

| Route | Name | Spreadsheet actions |
|---|---|---|
| `masters/` | `masters` | template/export for **all** masters at once |
| `masters/sheet/<key>/{template,export,import,clear}/` | `masters_template`, `masters_export`, `masters_import`, `masters_clear` | one master, by registry key |
| `masters/export/` | `masters_export_all` | every master, one sheet each (snapshot/backup) |
| `masters/materials/` (+ `new/`) | `masters_materials*` | key `materials` (entity-scoped) |
| `masters/customers/` (+ `new/`) | `masters_customers*` | key `customers` |
| `masters/couriers/` (+ `save/`) | `masters_couriers*` | key `couriers` |
| `masters/boxes/` (+ `save/`) | `masters_boxes*` | key `boxes` |
| `masters/organisation/?table=<t>` (+ `<t>/save/`) | `masters_org*` | keys `company` … `sales_type` |
| `masters/entities/` (+ `new/`, `<pk>/`) | `masters_entities*` | — (config, not a sheet) |
| `masters/roles/` (+ `assign/`, `remove/`) | `masters_roles*` | — |
| `masters/activity/` | `masters_activity` | — |

**`masters.MASTER_SHEETS` is the single source of truth for every master's column
set.** Template, export, import and the all-masters snapshot all read it, so a
template can never disagree with what the importer expects. Adding a master means
one registry entry, not four new views. Keys:
`company` · `organization` · `plant` · `storage` · `billing_type` · `sales_type` ·
`materials` · `customers` · `couriers` · `boxes` — and the same six structural
keys are what `masters/organisation/<table>/save/` accepts.

Import goes through each entry's `parse_file`, which searches the first 30 rows
for the header so a raw SAP extract with title/blank rows still loads.

Material and customer rows carry their own `Entity` column; the import form's
entity picker is the fallback for blank ones and is **required** for materials.
`Clear` empties one master — scoped to the selected entity where the master is
entity-scoped — and the import form's **Replace** checkbox does the same thing
first. Both demand a typed confirmation in the UI; keep that.

---

## 14. Domain rules a UI must respect

These come from the engines, not from styling preference. Breaking them makes the
UI lie about warehouse state.

1. **Everything is per-entity.** Never hardcode a unit's behaviour; read `Entity`.
   A new unit must work with no UI change.

2. **`status` and `stock_status` are independent axes** on a validation row, and
   `flags` is a *list*. One row can be `matched` + `near_expiry` +
   `["EXPIRY_MISMATCH", "PLANT_MISMATCH"]` simultaneously.

3. **Expiry classification uses the barcode's own `(17)` date**, not the SAP SLED
   column. SLED is used only for the separate Expiry Mismatch check (default
   tolerance 7 days). Don't relabel one as the other.

4. **Near vs Short expiry ordering is per-entity.** For MHPL, Near (5mo) is
   *tighter* than Short (12mo). Don't assume near < short.

5. **Damage beats every expiry state.** For MHPL the condition string must be
   exactly `Damage`; other units match on substrings (`dam`, `bad`, `broken`).

6. **Serial identity is `BATCH||SERIAL`,** not the serial alone — a serial is not
   unique across batches. Non-serialised items get a synthetic `[BATCH] … (#n)` id
   with a random suffix, which makes duplicate detection a deliberate no-op for
   them. That is correct: with no serial there is nothing to deduplicate on, and
   blocking the second scan of an identical carton would be wrong.

7. **Branch Transfer and ZVA05 match on material only** — batch is ignored. The UI
   must not show a batch mismatch warning in those flows.

8. **`DeliveryLine.sequence` is order-sensitive.** The matcher depends on the
   source file's row order. Never reorder lines server-side for display purposes
   in a way that feeds back into matching.

9. **Every scan attempt is logged**, accepted or rejected, with a machine-readable
   `reason_code`. The scan log is an audit record, not a transient toast.

10. **Audio feedback is a requirement, not a nicety** (PRD FR7). Pickers wear
    gloves and watch stock, not screens — distinct sounds per `ReasonCode`.

11. **Box capacity is optional per entity** (`enforce_box_capacity`), and
    `max_qty = 0` means unlimited.

12. **Courier resolution order when finishing:**
    `CustomerCourierMapping` (customer + entity + plant) → `CustomerMaster.courier_partner`
    → whatever the operator typed.

13. **Partial exports are gated** by `allow_partial_export`. When off, an
    incomplete delivery must produce a discrepancy report instead of a serial sheet.

---

## Appendix A — complete route reference

All **67** routes, dumped from the resolver so the names are exact. Reverse with
`{% url 'merilv:<name>' %}` / `reverse("merilv:<name>")`.

| `merilv:` name | Path |
|---|---|
| `index` | `/meril-v/` |
| `validator` | `/meril-v/validator/` |
| `validator_run` | `/meril-v/validator/run/` |
| `validation_detail` | `/meril-v/validator/<int:pk>/` |
| `validation_export` | `/meril-v/validator/<int:pk>/export/` |
| `validation_history` | `/meril-v/validator/history/` |
| `outbound` | `/meril-v/outbound/` |
| `outbound_upload` | `/meril-v/outbound/upload/` |
| `outbound_session` | `/meril-v/outbound/<int:pk>/` |
| `outbound_scan` | `/meril-v/outbound/<int:pk>/scan/` |
| `outbound_undo` | `/meril-v/outbound/<int:pk>/undo/` |
| `outbound_focus` | `/meril-v/outbound/<int:pk>/focus/` |
| `outbound_state` | `/meril-v/outbound/<int:pk>/state/` |
| `outbound_finish` | `/meril-v/outbound/<int:pk>/finish/` |
| `outbound_export` | `/meril-v/outbound/<int:pk>/export/` |
| `outbound_line_batch` | `/meril-v/outbound/<int:pk>/line-batch/` |
| `outbound_edit_scan` | `/meril-v/outbound/<int:pk>/edit-scan/` |
| `monitoring` | `/meril-v/monitoring/` |
| `monitoring_state` | `/meril-v/monitoring/state/` |
| `zva05` | `/meril-v/zva05/` |
| `zva05_detail` | `/meril-v/zva05/<int:pk>/` |
| `zva05_scan` | `/meril-v/zva05/<int:pk>/scan/` |
| `zva05_undo` | `/meril-v/zva05/<int:pk>/undo/` |
| `zva05_focus` | `/meril-v/zva05/<int:pk>/focus/` |
| `zva05_finish` | `/meril-v/zva05/<int:pk>/finish/` |
| `zva05_export` | `/meril-v/zva05/<int:pk>/export/` |
| `delivery_history` | `/meril-v/deliveries/` |
| `delivery_detail` | `/meril-v/deliveries/<int:pk>/` |
| `reports` | `/meril-v/reports/` |
| `extractor` | `/meril-v/extractor/` |
| `extractor_parse` | `/meril-v/extractor/parse/` |
| `scan_session_create` | `/meril-v/extractor/session/new/` |
| `scan_session` | `/meril-v/extractor/session/<int:pk>/` |
| `scan_session_scan` | `/meril-v/extractor/session/<int:pk>/scan/` |
| `scan_session_columns` | `/meril-v/extractor/session/<int:pk>/columns/` |
| `scan_session_finish` | `/meril-v/extractor/session/<int:pk>/finish/` |
| `scan_session_export` | `/meril-v/extractor/session/<int:pk>/export/` |
| `scan_history` | `/meril-v/extractor/history/` |
| `branch_transfer` | `/meril-v/branch-transfer/` |
| `branch_transfer_plan` | `/meril-v/branch-transfer/<int:pk>/` |
| `branch_transfer_scan` | `/meril-v/branch-transfer/<int:pk>/scan/` |
| `branch_transfer_export` | `/meril-v/branch-transfer/<int:pk>/export/` |
| `consolidator` | `/meril-v/consolidator/` |
| `consolidation_detail` | `/meril-v/consolidator/<int:pk>/` |
| `consolidation_export` | `/meril-v/consolidator/<int:pk>/export/` |
| `masters` | `/meril-v/masters/` |
| `masters_template` | `/meril-v/masters/sheet/<str:key>/template/` |
| `masters_export` | `/meril-v/masters/sheet/<str:key>/export/` |
| `masters_import` | `/meril-v/masters/sheet/<str:key>/import/` |
| `masters_clear` | `/meril-v/masters/sheet/<str:key>/clear/` |
| `masters_export_all` | `/meril-v/masters/export/` |
| `masters_materials` | `/meril-v/masters/materials/` |
| `masters_material_create` | `/meril-v/masters/materials/new/` |
| `masters_customers` | `/meril-v/masters/customers/` |
| `masters_customer_create` | `/meril-v/masters/customers/new/` |
| `masters_couriers` | `/meril-v/masters/couriers/` |
| `masters_courier_save` | `/meril-v/masters/couriers/save/` |
| `masters_boxes` | `/meril-v/masters/boxes/` |
| `masters_box_save` | `/meril-v/masters/boxes/save/` |
| `masters_org` | `/meril-v/masters/organisation/` (one table per `?table=` tab) |
| `masters_org_save` | `/meril-v/masters/organisation/<str:table>/save/` |
| `masters_entities` | `/meril-v/masters/entities/` |
| `masters_entity_create` | `/meril-v/masters/entities/new/` |
| `masters_entity_edit` | `/meril-v/masters/entities/<int:pk>/` |
| `masters_roles` | `/meril-v/masters/roles/` |
| `masters_roles_assign` | `/meril-v/masters/roles/assign/` |
| `masters_roles_remove` | `/meril-v/masters/roles/remove/` |
| `masters_activity` | `/meril-v/masters/activity/` |

---

## Appendix B — conventions not repeated per table

To keep the tables above readable, these are stated once:

- **Timestamps.** Nearly every table carries `created_at` (`auto_now_add`) and most
  also `updated_at` (`auto_now`). `ToolSetting` additionally has `updated_by`.
- **Ownership FKs** (`created_by`, `uploaded_by`, `operator`, `picker`, `scanned_by`,
  `triggered_by`, `actor`, `scored_by`) are all `null=True, on_delete=SET_NULL` —
  history survives user deletion, so a UI must handle a missing user gracefully.
- **`raw_data` / `parsed` / `payload` / `metadata`** are `JSONField`s holding the
  original source row or parse output. Useful for a "show raw" debug drawer.
- **`warnings`** is a JSON *list* of strings — render as a dismissible list, not one
  blob.

---

## Appendix C — test data

Generated dummy dumps for all six workflows live in
`dummy_template/meril_v_dummy_data/` (gitignored) with a README explaining the
expected outcome of every file. Useful for building UI against realistic volumes:
1,400 stock rows, 920 scans producing a 94.57% reconciliation across all match
tiers, 219 delivery lines, 545 barcodes in 5 format variants.
