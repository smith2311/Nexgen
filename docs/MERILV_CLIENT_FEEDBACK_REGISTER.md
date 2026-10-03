# Meril-V — Client Feedback Change Register

Source: `dummy_tempelate/meril_v_dummy_data/Meril-V_Client_Feedback_Sheet.xlsx` (returned 18 Aug 2026)
Client reference code: `module_info/MerilValidator-main (1) - Copy/` (Node.js + vanilla JS, `public/*.html`)
Client reference docs: `module_info/PRD - Meril V.docx`, `Business Flow Documentation.docx`

The client filled 22 of 23 rows as "change needed = Yes". This register decomposes those 22 rows
into individually trackable items, because several rows bundle 5–13 separate requests.

**No screenshots were attached** to the returned sheet (checked the xlsx media parts — none), and no
`RowNN.png` files were sent alongside it. Anything ambiguous below is flagged NEEDS-CLARIFICATION.

---

## Status at a glance

| Row | Module | Status |
|---|---|---|
| 1 | Stock Validator | **Done** |
| 2 | Outbound Scanner | **Done** — all items. 2.4's concrete gaps against the legacy screen are closed (Focus Pending counter, delivery status filter, bulk-finish); a literal pixel-for-pixel reskin was not attempted, see note below. 2.5 turned out to hide a real bug — see "COMPLETED — Row 2.4 and 2.5" below. |
| 3 | Barcode Extractor | **Done** — all 13 items, including the Layout Manager (3.8) and the module-switcher tab bar (3.10). Only 3.3 was a verify-only (tied to Row 17). |
| 4 | Branch Transfer | **Done** — plus a new **BT Scanning** floor console (cartons, colour zones, per-session location priority) in the nav slot ZVA05 vacated, matching the old `bt-scanner.js`. |
| 5 | Inbound Scanner | **Done** — no legacy spec existed, so this brings it to parity with its sibling consoles (bulk paste, per-line serial detail, the shared mode-tab bar) rather than following an old-code spec. Ask the client to re-test and say what "not working" meant if anything is still off. |
| 6 | Invoice Consolidator | **Done** — rebuilt against the client's own `module_info/` template: the 13-column export-invoice reader, two source tabs, Rows/Quantity/Invoices KPIs and a DB-Match column. Verified against the totals printed on their four real invoices. |
| 7 | ZVA05 | **Done** — and no longer a module of its own: it is a scanning mode inside the Outbound Scanner, per the client. See "COMPLETED — ZVA05 folded into Outbound". |
| 8 | Monitoring | No change requested |
| 9 | Delivery History | **Done** |
| 10 | Reports | **Done** |
| 11–16 | Org Hierarchy masters | **Done** |
| 17 | Material Master | **Done** — including the Layout Manager's dynamic PH-Level/Material-Type/Division columns |
| 18 | Customer Master | **Verified** — no change needed |
| 19 | Courier Master | **Done** |
| 20 | Carton Master | **Done** |
| 21 | Entity Rules | No change requested |
| 22 | Roles & Access | **Done** — four-tier hierarchy, plus row-level scoping now enforced on every detail/mutation/export endpoint and not just the list screens (see "COMPLETED — row-level scoping across the whole module"). |
| 23 | Activity Log | No change requested |

Every item in this register is now done, addressed on its own honest terms, or explicitly out of scope by
design (see below). Nothing is left in a "needs the client" holding state without at least the concrete
work that could be done without them already delivered.

Two further passes were done after the feedback rows themselves were closed, both prompted by asking
"is this actually production ready?" rather than by a register row:

- **Row-level scoping across the whole module**, which closed Row 22's own follow-up note. Scoping only
  the list screens hid links without being a boundary; every pk-addressable endpoint enforces it now.
- **Offline-queue idempotency**, which closed Row 3.6's documented double-count hole — a real
  data-corruption risk in shipped code, not a cosmetic gap.
- **A production-readiness bug sweep** — four lost-update/defeated-guard concurrency bugs, ~17
  crash-on-malformed-input paths, two unbounded-work problems (including a per-scan spreadsheet
  re-parse), and five more paginator-truthiness bugs.

The remaining scope boundaries are listed under "Known scope boundaries" at the end of this document.

---

## Reference file map (old → new)

The client explicitly names old files to copy behaviour from. Mapping them to the Django rewrite:

| Old file | Lines | Governs | New code |
|---|---|---|---|
| `public/admin.html` "Org Hierarchy" tab | 2821 | Master data hierarchy + mapping | `merilv/masters.py`, `templates/merilv/masters_org.html` |
| `public/delivery-search.html` | 1276 | Delivery History (3 tabs) | `merilv/views.py:delivery_history` |
| `public/Export-Consolidator.html` | 1094 | Invoice Consolidator | `merilv/services/consolidator.py` |
| `public/Outbound-Scanner*.html` | 1283–1323 | Outbound Scanner (4 entity variants) | `merilv/services/outbound.py` |
| `public/Barcode-Extractor.html` | 6249 | Barcode Extractor | `merilv/services/extractor.py` |
| `public/MHPL/MLSIPL/Cardio/MDPL-Validator.html` | 1688–2044 | Stock Validator | `merilv/services/validation.py` |
| `public/ZPUR.html` | 2185 | ZVA05 Auto-Assign (likely) | `merilv/zva05.py` |
| `public/script.js` | 3901 | Shared parsing/matching | `merilv/services/barcode.py` |

---

## VERIFIED ROOT CAUSE #1 — MLSIPL parsing discards the material (Row 1, Row 3)

**Status: confirmed by reproduction against the live database. This is a real bug, not a config issue.**

Client reported two things that turn out to be the same defect:
- Row 1: "MLSIPL Validation: The MLSIPL validation process is currently not working."
- Row 1/3: "Serial numbers are not being fetched or extracted by the system."

Reproduction (live entity config, standard GS1 input `(01)08904367102345(10)BATCH123(17)271231(21)SER98765`):

| Entity | material | batch | serial | verdict |
|---|---|---|---|---|
| MHPL | `08904367102345` | `BATCH123` | `SER98765` | correct |
| CARDIO | `08904367102345` | `BATCH123` | `SER98765` | correct |
| MICRO | `08904367102345` | `BATCH123` | `SER98765` | correct |
| MDPL | `08904367102345` | `BATCH123` | `SER98765` | correct |
| **MLSIPL** | **`SER98765`** | `BATCH123` | **`''`** | **BROKEN — serial written into the material field, real material silently dropped** |

Root cause chain in `merilv/services/barcode.py`:

1. `_apply_ai_map()` line 349 sources material **only** from AI (240)/(241) — never AI (01) GTIN.
2. Line 350–351: MLSIPL has `ai21_is_material=True`, so when 240/241 are absent it assigns
   `material = found["21"]` — i.e. the **serial** becomes the material.
3. Line 452: the GTIN→material master lookup is guarded by `if not result.material and found.get("01")`.
   Because step 2 already populated `material` (wrongly), **`_resolve_gtin()` never runs for MLSIPL**.
4. Line 364–366: `supports_serial=False` for MLSIPL, so serial is blanked.

Every other entity works only because step 2 doesn't fire for them, leaving `material` empty so the
line-452 GTIN resolution can proceed.

**Fix direction:** AI (21)-as-material must be a genuine *last-resort fallback*, applied only after
GTIN resolution fails — not before it. Reorder so `_resolve_gtin()` runs first, then fall back to
AI (21) only if material is still empty. Note MLSIPL's `supports_serial=False` is intentional per the
profile docstring ("MLSIPL has no serial concept at all") — **NEEDS-CLARIFICATION**: the client now
appears to want serials for MLSIPL too, which contradicts that documented rule.

**Note on the MHPL serial complaint:** MHPL parses bracketed AI (21) correctly, as proven above. So
the client's "serials not extracted in MHPL" is *not* reproducible with standard bracketed GS1 input.
It is therefore either (a) a different real-world barcode format, or (b) a **display/export** bug
rather than a parsing bug — consistent with their separate complaints that serial details are missing
from the *screen* and the *export*. Needs a real MHPL barcode sample to pin down.

---

## VERIFIED ROOT CAUSE #2 — the client's org-mapping data was migrated into a DEAD table (Rows 11–16)

**Status: confirmed from both codebases. Requires a migration — this is not a template fix.**

The old app modelled **Plant ↔ Sales Org as many-to-many with a primary flag**, via a real join table:

```sql
-- server/sql/001_app_schema.sql:83-89
create table if not exists plant_org_mapping (
  plant_code text not null references plant_master(plant_code) on delete cascade,
  org_code   text not null references organization_master(org_code) on delete cascade,
  unique (plant_code, org_code)          -- NOT unique(plant_code): one plant, many orgs
);
```
plus an `is_primary boolean` (present in the deployed schema and read/written by the old UI, though missing from that checked-in DDL).

The Django rewrite kept only `PlantMaster.organization` — **a single nullable FK**. One plant, one sales org. The relationship is structurally inexpressible.

**The critical finding:** the client's M2M rows *were* carried across — into `LegacyPlantOrgMapping`
([models.py:1537](merilv/models.py#L1537)), a holding table with plain `CharField` codes and **no FKs**.
It is populated once by `services/legacy_import.py`, exposed only in Django admin, and referenced by
**no view, template, or query anywhere in the module**. So their hand-curated mapping data is sitting
in the database, invisible and unusable.

That data cannot be regenerated from a spreadsheet — neither the old per-tab importer nor its bulk
importer ever wrote `plant_org_mapping`. It was only ever created through the UI modal. **Do not drop
that table.**

Required work: a real `PlantOrgMapping(plant FK, organization FK, is_primary)` model, a data migration
from `LegacyPlantOrgMapping` (resolving codes to FKs, backfilling `is_primary` from
`PlantMaster.organization` where no legacy row exists), then the link/unlink/set-primary UI.

Also confirmed, so we don't waste effort:
- **Billing Type and Sales Type are global reference tables in BOTH apps** — no company/org parent.
  Five independent pieces of evidence. Their inclusion in rows 15–16 is about losing the shared
  console + breadcrumb context, **not** about parentage. Do not add FKs to them.
- **Drag-and-drop reparenting never existed.** The only drag-drop in the old app is file drop zones.
- The old `openPlantUserMapper` button is **dead code in the reference build** — its modal markup
  exists in no HTML file. Don't port it as-is.

Two incidental bugs found in current code while analysing, worth fixing in passing:
- `masters_org.html` storage add/edit modal has **all three field labels shifted by one** (asks for
  "Plant Code" in the field that becomes `storage_code`, and labels the plant dropdown "Storage Name").
  Data lands correctly; purely a labelling defect, but on the most-used of the six tables.
- The PK is **editable on the edit form** and `org_save` keys `update_or_create` on the submitted code,
  so renaming a code silently forks a second row instead of renaming. The old app made the code
  readonly on edit.

---

## VERIFIED ROOT CAUSE #3 — Delivery History is empty because the two-actor workflow was lost (Row 9)

**Status: confirmed. The queryset is NOT broken — I checked.**

`delivery_history` ([views.py:1014](merilv/views.py#L1014)) has no default filter, no entity/picker
scoping and no date window; both its filters are truthiness-guarded, and `paginate()` uses
`get_page()` which clamps rather than raises. **There is no filter bug.**

The table is empty because `FinishedDelivery.objects.create(...)` exists in exactly **two** places in
the repo: `outbound_finish` and one test fixture. No importer, no seed command.

And crucially — in the old app a row appeared **the instant the picker finished scanning**, with
`status='pending_courier'` and no courier data, showing *"Delivery X marked as complete. Manager will
fill courier details."* A manager filled the docket/vehicle/box counts later via the Fill popup.
The rewrite has **no pending state at all**: `outbound_finish` demands courier, box and docket data
up front in a single modal. So nothing ever reaches Delivery History until one person completes the
entire two-person job in one sitting.

**That is why the screen is blank — a lost workflow, not a broken query.** Fixed the model half of
this: added `status` with `pending_courier`/`complete` (default `complete`, so existing rows keep
their meaning) plus a `box_breakdown` property that decodes `boxes_used` into the six A–F columns.

Good news that cuts the estimate: **the A–F box data model and write path are already correct.**
`boxes_used` already stores `"A:2,B:1"` exactly as the old app did, and `outbound_finish` already
writes it from the finish modal. Only the *read* side (six table columns) was missing — no migration
needed for that, hence `box_breakdown` being derived rather than six new fields.

Cosmetic defect in the same template: `{% if page_obj %}` is falsy on an empty page (Django's `Page`
defines `__len__` but not `__bool__`), so the table and its pagination links vanish while the
"Showing 0–0 of 0" caption — which sits outside the `if` — still renders. Same pattern at
`consolidation_detail.html:40`.

---

## VERIFIED ROOT CAUSE #4 — the Consolidator is mis-targeted, not broken (Row 6)

`services/consolidator.py` is well-built and its dependencies are installed. The problem is it parses
a **different document family than the client uses**:

- It is tuned to **Indian GST tax invoices** — `GSTIN_RE`, `HSN_RE`, CGST/SGST/IGST noise filters.
- The old tool was tuned to **MLSIPL US export invoices** — `[VUM]\d{7,10}` lots, `K\d{6}` FDA 510(k)
  numbers, `$1,234.56` prices, `dd-MMM-yy` dates. None of GSTIN/HSN/CGST appears on those.
- Its line-item recogniser requires a line to **end in exactly three numbers** (qty/rate/amount) or the
  row is skipped. An export-invoice line ending `… 25-Mar-26 V0012345 K123456 $1,204.00` never matches,
  so a run "succeeds" with 0 rows — which is what "not working" looks like to a user.
- It also uses `page.extract_text()`; the old code **reconstructed lines from glyph X/Y coordinates**
  with a ±5pt tolerance, which for multi-column invoice tables decides whether a line is a row or a jumble.

Output columns also don't match: **Mfg Date, Expiry Date, 510(k), and DB Match are all missing**.
DB Match (validating each item code against the material master, green/red) was the tool's actual QA
value. `confidence` is computed per row and then discarded — no field stores it, so the operator can't
sort worst-first and review.

Fix is additive: a second parser profile alongside the GST one, glyph-coordinate line reconstruction,
four extra `ConsolidationRow` fields plus `confidence`, and a material-master join. **Not** a rewrite —
its spreadsheet path and warning discipline are better than the old code's `console.error`-and-continue.

---

## COMPLETED — Rows 11–16, Org Hierarchy

Root cause #2 is resolved. What shipped:

- **`PlantOrgMapping` model** — the real join table, `unique_together(plant, organization)` plus
  `is_primary`. `PlantMaster.organization` is kept as the primary denormalisation (a lot of code
  resolves a plant's org through it) and is now actively synced to whichever mapping is primary.
- **Data migration `0007_recover_plant_org_mappings`** — moves rows out of the dead
  `LegacyPlantOrgMapping`, resolving text codes to FKs and skipping any that no longer resolve, then
  backfills a primary from `PlantMaster.organization` for plants the join table never covered. Verified
  reversible and idempotent (reverse → 0 rows → re-forward → 67 rows). On this DB it backfilled 67
  plants and recovered 0 legacy rows, because the legacy import was never run here; on the client's
  environment it will recover their curated mappings.
- **Hierarchy tree** (`masters_org_tree.html`, new "View Hierarchy" tab) — Company → Sales Org →
  Plant → Storage in five queries, each level colour-coded with its own guide rule. A plant mapped to
  two orgs renders under both. Orphans (org with no company, plant with no org) are surfaced in a
  warning banner rather than silently dropped, since the importer can create them.
- **Mapping management** — link / unlink / set-primary from the tree, all `transaction.atomic()`.
  Invariants enforced: exactly one primary while any mapping exists; unlinking the primary promotes a
  successor; unlinking the last clears the FK; a plant's first mapping is automatically primary;
  re-linking is idempotent. Keyed by org code, so no surrogate key reaches the page.
- **The two incidental bugs** — storage modal labels now come from `spec.columns` (role-keyed) instead
  of `headers` (export-ordered), so they can't drift; the PK is locked on edit, with the unlock handled
  in the shared `wireModals` so every master gets it.

**+10 permanent tests.** Not done in this row: the wide 12-column whole-hierarchy import, and
bulk row delete. Both are additive and neither was named in the feedback.

---

## COMPLETED — Row 17, Material Master YMM061

"PH Level's" was ambiguous in the feedback. **Resolved from the reference code, not guessed:**
`Cardio-Validator.html:1454` carries the legacy tool's own downloadable template row, which gives the
authoritative YMM061 column set — **33 columns**, including `PH Level 01`–`05` each paired with a
`- Desc.`, plus `EAN/UPC` and `EAN Category`. So "PH Level's" is five product-hierarchy levels, not one
field.

Shipped:
- **All 33 YMM061 columns** on `MaterialMaster`, with `YMM061_COLUMNS` on the model as the single
  header→field map driving import, export and template together.
- `gtin` is left as the canonical barcode-resolution key and `ean_upc` stored alongside it. They are
  usually the same number, but an import must not silently rewrite the key every scan resolves through.
- **Exact-header matching** for these columns. `Material Group`, `Material Group Desc.` and
  `Material grp desc. 2` all contain "material group", and the five PH pairs all contain "PH Level" —
  a `contains()` lookup collapses them onto one column. There is a test for exactly this.
- **A narrow re-import cannot blank the wide columns.** Only keys the parser actually found are copied,
  so re-importing a scan-code/GTIN sheet no longer wipes a previous full YMM061 load.
- **Grouping** — material-group and PH-Level-01 dropdowns on the material list, populated from the
  values present for the chosen entity so no filter can return nothing. Search now covers EAN, group,
  group description and hierarchy. Indexed on `(entity, material_group)` and `(entity, ph_level_01)`.
- Material list shows GTIN, EAN/UPC, group + description, and the joined hierarchy path.

**+6 tests.** Verified against the real 33-column header row.

Note: this is the *storage and grouping* half of the client's request. The other half — "one screen
with layout feature" — is the saveable column-layout preset, still **NEEDS-CLARIFICATION** (item 3.8).
The columns now exist to build it on.

---

## COMPLETED — Row 22, four-tier hierarchy with data scoping

Client request: *"add a manager role also User Hierarchy like Admin-Manager-Picker (picker only see the
data of mapped Manager) -Viewer, Manager Mapped with their plant"*.

Shipped:
- **Manager tier** added between Picker and Admin — can do everything a picker can, within its plants,
  but cannot touch master data. `Warehouse Manager` was historically an *admin* group; it is a manager
  now, which is what the name says. Admin stays an explicit grant.
- **`PickerManager`** model — one manager per picker (`OneToOne` on the picker side; inheriting from two
  managers would make a picker's scope the union of two teams, the opposite of a restriction). A DB
  `CheckConstraint` blocks self-reporting, which would make the scope lookup self-referential.
- **`UserPlantMapping`** already existed and is now actually used — it is the manager↔plant half.
- **Two scope axes**, deliberately separate from the permission tier:
  - `plant_scope(user)` — admin unrestricted; a user with their own mapping is scoped to it; a picker
    with none inherits their manager's; anyone else unrestricted.
  - `team_scope(user)` — needed because `FinishedDelivery` records *who* picked but not *which plant*,
    so the rule can only be answered there by team membership.
- **Applied to real queries**, not just available as helpers: delivery history (team), validation
  history (team), outbound sessions (plant, via delivery lines).
- **Admin UI** — a "Data scope" control per user row, showing plants / "via {manager}" / "All plants",
  with the plant checklist for managers and the reports-to dropdown for pickers.

**Safe-rollout property, deliberate:** unmapped users resolve to `None` (unrestricted), so an
installation where nobody is mapped behaves exactly as before. Scoping switches on per-user as mappings
are created — no flag day, no risk of locking the warehouse out on deploy.

`None` vs `set()` is a real distinction in this API — `None` is "no restriction", an empty set is
"restricted to nothing". Callers must use `scope_queryset`/`scope_queryset_by_team` rather than testing
truthiness, which collapses the two. Documented on both functions.

**+15 tests**, including explicit cross-team leak assertions (a picker must not see another manager's
plants or another team's deliveries) and that clearing a mapping lifts the restriction rather than
locking the user out.

**Closed in the production-readiness pass** — see "COMPLETED — row-level scoping across the whole
module" below. At the time this row was first written only three list screens were scoped, which hid
the links but was not a boundary; every detail, mutation and export endpoint is now scoped too.

---

## ANSWERED FROM THE OLD CODE — questions we do NOT need to ask

The client said "refer our old code" and gave us the folder. Eight of the twelve questions we had
drafted are answered by it. Reference paths are relative to
`module_info/MerilValidator-main (1) - Copy/`.

**The old app's real architecture** (found late, and it reframes three rows): `public/src/core/`
holds the actual logic — `outbound-scanner.js` is 6,261 lines, with **`bt-scanner.js` (2,638) and
`zva05-mode.js` (686) layered on as *modes* of the same screen**, toggled by `toggleBTMode()`
(`bt-scanner.js:184`) and a ZVA05 button injected beside it (`zva05-mode.js:49-61`). The client said
exactly this in row 7 — "It is also a part of outbond scanner like BT mode" — and they were describing
the file layout, not making a suggestion. Rows 2, 4 and 7 are **one screen with three modes**, not
three modules.

| Q | Question | Answer from the code |
|---|---|---|
| **Q1** | Pending-courier flow? | **Yes, that was the old behaviour.** Finish wrote `status:'pending_courier'` and told the picker *"Manager will fill courier details."* Nothing to ask — build it. |
| **Q2** | What is the "Layout feature"? | **`LayoutManager`, `script.js:3183+`.** Named column presets in `localStorage` (`sapVal_layouts_v3`): `{name, columns:[{key,label,header,width}]}`. Ships "Standard View" and "Simple Check". Users toggle/reorder columns, **rename headers**, set widths. Includes dynamic Material-Master columns (`mm_`, `_isMaster`, `_mmKey`) — that is the "Material Master grouping" part. **Per-browser, so private.** |
| **Q3** | MLSIPL serials? | **The old tool had none either.** A `phySer` / "Phy Serial" column exists **only** in `MHPL-Validator.html:1581`; MLSIPL, Cardio and MDPL have no serial column. Current Django behaviour matches the old tool. Still worth confirming, but as "do you want *new* behaviour?", not "which is right?". |
| **Q4** | MHPL failing barcode? | **Probably already fixed.** MHPL genuinely had serials, the new parser extracts them correctly, and the fault was the `"Unknown"` placeholder being displayed as data — fixed this session. Ask them to re-test rather than to send samples. |
| **Q7** | Branch Transfer consumption priority | **Found, explicit.** `bt-scanner.js:22` — `locationPriority`, default `["FG02","FG01","FG03"]`, user-editable and stored in `localStorage` under `bt_location_priority`. Allocation (`:866-899`): collect every (batch, location) with qty>0 → sort by that priority list (unlisted locations sort last, `99999`) → **pick the first option whose single location covers the full quantity**; if none does, take the highest-priority option and draw down across locations in priority order. |
| **Q8** | "Switch between modules at the same time" | **Mode switching inside one screen**, per the architecture note above — not browser tabs. |
| **Q10** | Offline scanning | **The old app lost scans too.** `sw.js` is network-only by design ("Bypass cache entirely"), returning a 503 on failure. IndexedDB (`script.js:308-327`) caches **only master data** (`materialMaster`, `customerMaster`, shown as "(cached)") — there is no scan queue. So this is a **new feature request, not a regression.** |
| **Q6** | Inbound Scanner "not working" | **There is no inbound module in the old app at all.** No `Inbound-*.html`, and zero matches for inbound/receiving/GRN across `public/`. `ZPUR.html` is *"RCV Pro — Return Credit Validator"*, i.e. returns/credit, not goods receipt. So the Inbound Scanner is **new in the rewrite** and "refer to the old code" cannot help — this one genuinely needs their spec. |

Two extra findings worth acting on, neither of which the client asked for directly:

- **The old Stock Validator had 12 KPI cards, not 9** (`script.js:340-352`): Total SAP Stock, Total
  Scanned, **Serial Match, Batch Match, Material Only** (three separate match tiers), Variance,
  Remaining SAP, Damage, Expired, **Near Expiry, Short Expiry** (split), Good Stock. We built the 9
  from their screenshot, which is what they asked for — but the finer breakdown exists if wanted.
- **Those KPI cards were user-configurable** — `kpiCardSettings` in `localStorage`
  (`script.js:361, 452`), with renameable titles. Same family of feature as the Layout manager.

---

## COMPLETED — Row 9, Delivery History (all 7 items)

Built from the legacy screen, but re-engineered rather than transcribed. The old version did its
filtering, sorting and pagination in the browser over an array it had loaded whole, validated the
courier form with two `alert()` calls, and duplicated its filter logic per export so the exports
drifted from the table.

| Client item | Delivered |
|---|---|
| 3 tabs restored | Delivery Log / Summary Analytics / Finished Deliveries, as server-rendered links so a filtered view is bookmarkable |
| Analytics dashboard | Total Deliveries, Total Scans, Unique Products, Avg Scans/Delivery, Total Boxes + Top Materials (20), Top Pickers, Date-wise Summary — all DB aggregates, not row-walking |
| Export buttons | Excel (23 cols), Invoice Format (the legacy 14 SAP columns verbatim), Packing List PDF, Refresh, select-all |
| Advanced filters | From/To date, status, plant, entity, search across delivery/customer/docket/courier/plant, 5 sort options, 25/50/75/100 page sizes |
| Table columns | Courier No, Docket, Boxes, A, B, C, D, E&F, Weight, Status — `box_breakdown` decodes `boxes_used` |
| Pagination | Real Prev/Next + working page-size selector |
| Courier Fill popup | Modal with docket barcode field, vehicle, weight, six box counts and a live total |

**Robustness work the old code did not have:**

- **All validation moved server-side.** Courier number and docket required, weight must parse, box
  counts cannot be negative. The old form's two `alert()` calls were bypassed by any direct POST.
- **`?pks=` is intersected with the already-scoped queryset**, never used to look rows up directly.
  Without that, anyone could export another team's delivery by guessing an id. There is a test that
  forges a pk and asserts nothing comes back.
- **`sort` and `per_page` are whitelisted.** An arbitrary `sort` reaches the ORM; an unbounded
  `per_page` is a cheap way to exhaust memory.
- **One shared `_filtered_deliveries()`** for the table, the analytics and all three exports, so they
  cannot disagree about what "the current selection" is.
- **The two-actor flow is inferred, not declared** — a picker who supplies no courier paperwork gets
  `pending_courier`; one who does completes in a single step. No new checkbox for the picker to get
  wrong, and the existing one-step flow still works unchanged.
- **Packing list is a template**, rendered via xhtml2pdf, replacing ~350 lines of jsPDF coordinate
  maths that nobody could safely edit.

**Fixed the "no table structure at all" bug properly.** It was `{% if page_obj %}`: a Django `Page`
defines `__len__` but not `__bool__`, so an empty page is falsy and took the table headers *and* the
pagination controls with it — while the "Showing 0–0 of 0" caption sat outside the branch and kept
rendering. Now gated on `paginator.count`. **The same pattern still exists in
`consolidation_detail.html:40`** and should be fixed when that row is picked up.

Also added `FinishedDelivery.plant`, denormalised from the delivery's first line — the row outlives
its session, and plant-scoped users need to filter their own deliveries without a join through the dump.

**+21 tests.**

---

## COMPLETED — Rows 2, 4 and 7 (Outbound Scanner, Branch Transfer, ZVA05 — 10 of 11 items)

Built together on purpose: the old code's own architecture (`outbound-scanner.js` as the base scanner,
`bt-scanner.js` and `zva05-mode.js` layered on as *modes*) means these three rows share one engine and,
now, one navigation surface. See the "ANSWERED FROM THE OLD CODE" section above for the reasoning.

| Client item | Delivered |
|---|---|
| 2.1 Material details + assigned serials not shown per line | New **Lines** view on the Outbound console (`static/merilv/js/scanner.js:renderLinesView`) — per-line material, batch, required/scanned/pending and every assigned serial. The data (`OutboundScan.serial_only` grouped by line) was already computed server-side; it was just never rendered. |
| 2.2 Bulk paste of serials missing | New Scan/Bulk Paste tabs on the console, backed by `outbound_bulk_scan` (`merilv/views.py`). Ported from `outbound-scanner.js:1579-1627` (split on newline/comma/tab, drop tokens ≤5 chars) — each code runs through the exact same `outbound.process_scan` gates, in order, inside one transaction, so a 2,000-line paste behaves identically to scanning them one at a time and cannot leave a partial line count if it fails midway. |
| 2.3 Results only appear after finish — need real-time | Verified already working: `outbound_state` polls every 2s and repaints deliveries/totals/focus. No fix needed here; the client's screenshot likely predates the poll being wired in. |
| 7.1 ZVA05 should be a mode of Outbound, like BT | New shared `_shell/scan_mode_tabs.html`, included on all three consoles (Outbound, Branch Transfer, ZVA05). The underlying sessions/plans/dumps stay separate models — delivery-line+batch matching genuinely differs from material-only matching against a plan or order, and merging them would risk the working ZVA05/BT flows for no behavioural gain — but a picker now reaches all three from one tab bar instead of hunting through the module launcher, which is what the client's own words ("It is also a part of outbound scanner like BT mode") were actually asking for. |
| 4.1 "Ask material" batch assignment not working | This was the client's name for `assignBTBatches` (`bt-scanner.js:835-902`) — pre-recommending which batch a picker should draw from stock before they start scanning. New `services/transfers.assign_transfer_batches()` + an **Auto-assign batches** button on the Branch Transfer plan screen, wired to a new `BranchTransferLine.assigned_batch` column with a manual per-line override input next to it. |
| 4.2 Missing consumption priority rules | The rule itself, ported exactly: every (batch, location) with stock is ranked by `Entity.bt_location_priority` (new field, defaults to the legacy `"FG02, FG01, FG03"`, unlisted locations sort last); the first priority-ranked option whose own quantity covers the line in full wins, else the highest-priority option's batch is taken and drawn down across its own locations. Configurable per entity from the Entity edit screen (Outbound & Access card), replacing the old tool's per-browser `localStorage` setting with a shared one everyone sees the same value for. |
| 4.3 Plan Summary doesn't show actual requirements | New plant → colour-zone summary table (`_bt_plant_summary`), ported from `renderBTSummaryTable` (`bt-scanner.js:653-790`) — plan/dispatched/pending/percent per zone, per plant, and a grand total. Uses `BranchTransferLine.receiving_node_code` / `intransit_color_zone`, both already on the model. |
| 4.4 No real-time stats during transfer | New `branch_transfer_state` endpoint + `static/merilv/js/branch_transfer.js`, polling every 2s exactly like the Outbound console. Replaces the previous behaviour of accepting a scan and reloading the whole page to see the new totals. |

**Robustness work beyond the legacy tool:**

- **The batch-assignment algorithm fixes a real gap in the original**: the legacy `assignBTBatches` never
  accounted for stock already committed to a manually-overridden or previously-assigned line before
  handing out the same units to the next one. `assign_transfer_batches()` runs a reservation pass first —
  every line that already carries a batch has its *pending* quantity (not the full plan quantity, another
  correctness improvement) subtracted from the pool before any new recommendation is made, so two lines can
  never be pointed at the same physical stock.
- **A picked-against line's batch is still immutable** — the same "undo the scans first" rule as
  `outbound_line_batch`, now applied to `branch_transfer_line_batch` too.
- **A deviation from the recommended batch is a note, not a rejection.** `process_transfer_scan` still
  matches on material alone; if the picker's physical batch differs from `assigned_batch` the message says
  so, but the scan is accepted — physical reality wins, matching the module's own documented design rule.
- **Bulk paste is one atomic transaction**, not N independent requests — a failure partway through cannot
  leave the delivery lines partially updated, and the in-memory line quantities are advanced after each
  accepted code so duplicate-serial and over-scan gates see the paste's own prior lines correctly.
- **A closed plan/session rejects scans and bulk pastes** (`branch_transfer_scan` now checks
  `plan.is_finished`, matching the check `outbound_scan`/`zva05.scan` already had — this plan-level check was
  missing before this work).

**At this point in the work, not yet done:** 2.4 and 2.5 — both were picked up and closed later; see
"COMPLETED — Row 2.4 and 2.5" further down for what was actually delivered for each.

**+16 engine tests (`test_transfers.py`) + 16 view tests. Full regression: 338 passed, 0 failed.**

---

## COMPLETED — Row 3, Barcode Extractor (9 of 13 items)

| Client item | Delivered |
|---|---|
| 3.1 Serial extraction fails (MHPL, Live Validation) | **Root-caused, not just patched.** Both `live_scan` and `live_validate` compared the "Unknown" non-serial placeholder as if it were a real serial, so the *second* scan of an identical non-serialised batch was rejected outright — this is what "extraction fails" looked like to an operator. Same defect class as the MLSIPL fix earlier in this register, just not yet propagated to the extractor. |
| 3.2 Dedup logic: duplicate batch legal, duplicate serial not | Fixed at the root: both engine functions and the two view-level seen-set rebuilds now gate on `is_non_serial()` before ever comparing an identity, instead of a bare truthiness check that let "Unknown" through. |
| 3.4 Live Scan must show Box Count and Total Count | Per-scan `box_no` (new field), a Box input the operator can change mid-session without opening a new one, and a "Box Count" KPI tile scoped to whichever box is currently active — ported from the legacy tool's box dropdown + live count (`lsUpdateBoxCountDisplay`). |
| 3.5 Allow correcting condition Good↔Damage after scan | New per-scan `condition` field, a Good/Damage toggle that sets the default for new scans, and an independent per-row dropdown to correct any past scan's condition — mirrors the legacy tool's `lsChangeCondition`/`lvChangeCondition` exactly, including that the toggle and the correction are two separate mechanisms. |
| 3.7 KPI cards: Duplicate / Valid (Found) / Not Found | **Verified already present** — Total / Found·Matched / Not Found / Duplicates tiles already existed; no fix needed. |
| 3.9 Visually highlight serial-mismatch, batch-mismatch, expiry | Every scan-feed row now carries a left-accent + tint class from its result family (`scan_result_row_class`), reused from the same colour tokens the Result badge already used. |
| 3.11 Export buttons for Scan History | New `scan_history_export` — the session *list* itself, honouring whatever search/status/mode/sort filters are on screen (one shared `_filtered_scan_sessions()` builder, so the table and the export can never disagree). Per-session export already existed. |
| 3.12 Excel export formatting: dates, numbers, colour, headers, borders | `build_workbook()` (shared by every export in the module) now borders every cell and gives `date`/`datetime` values a real `DD-MM-YYYY` / `DD-MM-YYYY HH:MM` number format instead of leaving Excel to guess. Fixes formatting on every export at once, not just the extractor's. |
| 3.13 Dropdown-driven advanced filtering + sorting | Scan History gained search (session name / entity), a status filter (Open/Finished), and a whitelisted sort (`SCAN_HISTORY_SORTS`) — same whitelist-before-`order_by()` pattern as Delivery History. Also fixed the same `{% if page_obj %}` empty-page-is-falsy bug documented under Row 9, found here as a drive-by while touching this template. |

**Verified, not re-done:**

- **3.3** (master data population / YMM061) is the same work as Row 17, completed earlier in this register — nothing further to do here.

**At this point in the work, not yet done:** 3.6, 3.8 and 3.10 — all three were picked up and closed in
the next pass; see "COMPLETED — Row 5 (Inbound Scanner) and Row 3's remaining items (3.6, 3.8, 3.10)"
further down for what was actually delivered for each.

**+16 engine tests (`test_extractor.py`) + 9 view tests + 5 export-formatting tests. Full regression: 369 passed, 0 failed.**

---

## COMPLETED — Row 6, Invoice Consolidator

The client's own instruction was "rebuild per `Export-Consolidator.html`" — and that file turned out to be
a different invoice *layout* entirely, not a rebuild of the same one. The existing PDF parser targets a
domestic GST tax invoice (trailing qty/rate/amount columns, GSTIN, HSN codes); the legacy screen this row
points at was built specifically for **MLSIPL's export invoices** — USD unit pricing with no GST at all, a
lot number shaped like `V`/`U`/`M` plus 7–10 digits, a trailing FDA 510(k) clearance number, and two
`D-Mon-YY` dates per line (manufacture, expiry). Against a GST-shaped parser, every export invoice's pages
came back "no line items recognised" — which is exactly what "not working" looked like.

**Delivered:** a second parsing strategy, `_parse_export_page_items()`, ported from the legacy screen's own
`extractInvoiceDataPDF()`. `parse_invoice_pdf()` tries the domestic strategy first per page; only when that
finds nothing does it try the export layout — the two are mutually exclusive in any real invoice, so this
never double-counts a page.

**A real bug in the legacy code was fixed while porting it, not carried forward.** The old JS took
`line.match(/[A-Z0-9]+.../)`'s *first* match unconditionally as the item code — on a line like
`"2 EA V1234-01 Widget …"` that regex matches the bare quantity `"2"` before it ever reaches the real code,
because a lone digit satisfies `[A-Z0-9]+` just as well as a material number. The port scans every candidate
match and keeps the first one that is actually code-shaped, reusing the domestic strategy's own
`_looks_like_code()` validator — one function, one definition of "looks like a code," used by both layouts.

Also fixed the same `{% if page_obj %}` empty-page-is-falsy bug documented under Row 9, found here at
`consolidation_detail.html:38-40` exactly as flagged.

**+12 engine tests (`test_consolidator.py`) + 2 view tests.**

---

## COMPLETED — Row 10, Reports

"Unable to check without data" is a data problem, not a bug — `reports()` is a live aggregate over
`FinishedDelivery` and `OutboundScan`, and both were empty in the client's environment, so every tile read
zero. There is nothing to fix in the report itself.

**Delivered:** `manage.py seed_merilv_demo_activity` — a management command that seeds realistic demo
deliveries and scans across the seeded entities, spread over a configurable day range, so Reports has
something to show for a walkthrough. Deliberately **not** wired into `transfer_merilv_data` or the deploy
pipeline — this is review data, never real customer activity, and stays a manually-invoked, opt-in step.

Idempotent by construction: every row it writes carries a recognisable marker (`delivery_no` starting with
`DEMO-`, session name `"Demo Seed Activity"`), so `--clear` removes exactly what a previous run added —
verified real customer data is never touched. `--seed` makes a run reproducible.

Run it with `podman exec django_app python manage.py seed_merilv_demo_activity --days 30` whenever a demo
environment needs the Reports screen populated; `--clear` to remove it again afterward.

**+6 tests (`test_seed_demo_activity.py`).**

---

## COMPLETED — Row 5 (Inbound Scanner) and Row 3's remaining items (3.6, 3.8, 3.10)

The last four items with no reproduction step or no legacy spec to build against. Each is addressed on
its own terms rather than left blocked.

### Row 5 — Inbound Scanner: brought to parity, not guessed at

There was never anything to diagnose "not working" against — the earlier archaeology through the old
codebase confirmed no inbound/receiving module exists there at all, so this screen is new in the rewrite
by necessity. Rather than leave it blocked indefinitely, it was brought up to the same standard as its
already-fixed sibling consoles:

- **Bulk paste** (`inbound.bulk_scan`) — a truck unloads as a batch; a receiver handed the packing list
  can now paste every barcode on it at once, gated through the exact same engine as scanning one at a time.
- **Per-line received serials** — `payload_for()` now reports which serials actually booked in against
  each plan line, surfaced in a new column, matching the Outbound console's Lines view.
- Both wired into the shared mode-tab bar (below).

If the client's original complaint was something else entirely, this at least removes the two concrete
gaps that existed relative to the rest of the module — re-test and report anything still wrong.

### Row 3.10 — the mode-tab bar now spans every scanning console

Row 7's shared tab bar (Outbound / Branch Transfer / ZVA05) is exactly what Row 3.10 was asking for,
generalised: it now includes Inbound Scanner and the Barcode Extractor too, including inside an *open*
Live Scan/Live Validate session, not just on the landing pages. Nothing about switching loses state —
every tab is a plain link to that mode's own page, and every session lives in the database regardless of
which mode's screen is currently open.

### Row 3.8 / 17.1 — the Layout Manager, generalised rather than rebuilt per screen

The legacy `LayoutManager` (`script.js:3183+`) was a single screen's worth of column-preset logic,
duplicated by hand wherever another screen wanted it. Rather than port that once per row that asked for
it, it is now one reusable mechanism:

- `SavedLayout` — a named, per-**user** (not per-browser, unlike the original) column preset for one
  screen, enforced to at most one *active* layout per user+screen by a conditional database constraint,
  not just application code.
- `merilv/layouts.py` — five generic endpoints (list/save/activate/clear/delete) that work for any screen;
  a screen only has to register which columns it offers. Submitted column keys are whitelisted against
  that registry on every save, so a request can never smuggle in a column the screen has no data for.
- `layout_manager.js` — one "Columns" button + panel, dropped onto any table via
  `data-mv-layout-manager data-screen-key="..." data-table="#selector"`, with the table's own cells tagged
  `data-col="key"`. Show/hide, reorder (up/down, not full drag-and-drop — see note below), and rename any
  header, all without needing the table's row-rendering code to know the layout system exists.
- **Live on two screens**, matching where the client actually asked for it: **Scan History** (Row 3) and
  **Material Master** (Row 17) — the latter is where this earns its keep, since the YMM061 import carries
  far more columns (Material Type, Division, all five PH Levels and their descriptions) than anyone wants
  on screen by default, and they were previously nowhere to be seen at all.

**Scope note, stated rather than hidden:** the legacy tool's column reordering was real drag-and-drop;
this ships up/down move buttons instead. They reach the same end state with less code to get wrong, but
if the client specifically wants drag-and-drop, that is a frontend-only follow-up on top of the same
backend. Applying this to further screens (Stock Validator's own results table, where the feature
originated, is the natural next one) is now a template change plus a registry entry, not new plumbing.

### Row 3.6 — offline scan queueing

The honest gap stated plainly earlier in this register — a dropped connection loses whatever scan was
mid-flight, and the old tool never solved this either (its service worker was network-only, no queue).

**Delivered:** `offline_queue.js`, a localStorage-backed queue, live on the Outbound Scanner console —
the highest-traffic one and the one this was raised against most directly. A scan only queues on a
genuine network failure (`fetch()` rejecting, never an HTTP 4xx/5xx, which resolves normally and must
still reach the operator as a real rejection). Queued scans replay through the exact same single-scan
endpoint, oldest first, automatically on the browser's `online` event and every 15 seconds while anything
is queued; a definitive server answer of any kind — accepted or rejected — clears an entry, only a
renewed network failure leaves it queued.

**Known limitation, stated rather than glossed over:** if a scan actually reached the server and was
accepted, but the response was lost in transit (the connection dropped between send and receive — a
narrower case than simply being offline), replaying it is caught as a duplicate by the engine's own
duplicate-serial check for a serialised item, so nothing is double-counted. A **non-serialised** item has
no such guard and could in that specific narrow window be counted twice. Closing that completely needs a
server-side idempotency key (the client submits a request id, the server skips a repeat) — a real but
separable change, not included here.

**Rolled out to Outbound only.** The same module is designed to drop onto Branch Transfer, ZVA05, Inbound
and the Barcode Extractor's live consoles with a two-line change each (load the script, wrap that
console's own submit function) — mechanical repetition of a now-proven pattern, not new design work, and
a reasonable next slice on its own rather than five consoles' worth of surface area added in one pass.

**Verification:** the queue module is pure logic (localStorage + a caller-supplied send function, no
direct `fetch()` inside it), so its enqueue/drain/retry behaviour was verified with a standalone Node
script simulating a browser `localStorage` — this project has no in-browser JS test harness, so that is a
one-off correctness check rather than a suite that runs in CI. The Django side (the scan endpoints
themselves) is unchanged and stays covered by the existing pytest suite.

**+38 tests across this section** (9 inbound bulk-scan/serials, 15 layout mechanism, 5 standalone
offline-queue checks run manually, plus template/view coverage folded into the existing suites).
**Full regression: 410 passed, 0 failed.**

---

## COMPLETED — Row 2.4 and 2.5

The last two items, both closed out rather than left in a "needs the client" state.

### Row 2.5 — a real bug, found by auditing rather than by reproduction

There was never a reproduction step for "core functions generally not working." Rather than leave that
unactionable, the outbound scanning pipeline (scan, undo, finish) was audited line by line for anything
that could produce exactly that kind of vague, intermittent-feeling breakage — and it turned up a real one:

**Nothing stopped a scan, or an undo, from landing against a delivery that had already been finished and
exported.** A session stays open until every delivery inside it is finished, so scanning delivery B after
finishing delivery A is completely normal — but a stray scan that happened to match *A's* lines (a
mis-scan, a picker not realising which delivery was focused, a second gun still pointed at A's stock) was
still silently accepted, incrementing `DeliveryLine.scanned_qty` behind the back of the `FinishedDelivery`
row already handed to the courier. The same gap existed in reverse on Undo — reversing a scan that
belonged to an already-finished delivery decremented the same counters just as invisibly. Both now reject
with a clear message (`DELIVERY_ALREADY_FINISHED`) instead of silently drifting the exported record out of
sync with what the database actually says was scanned.

This is exactly the shape of bug a client describes as "sometimes things just don't work right" without
ever pinning down — no error dialog, no crash, just numbers that quietly stopped matching reality. Whether
it's *the* bug behind 2.5 can't be confirmed without the client re-testing, but it is a genuine defect that
is now fixed regardless.

**+4 tests** (`TestScanAgainstFinishedDelivery`), covering: the scan-side rejection, that a *different*,
still-open delivery in the same session is unaffected, the bulk-paste path rejecting the same way, and the
undo-side rejection.

### Row 2.4 — closing the concrete gaps, not attempting a reskin

Re-read against the legacy `Outbound-Scanner.html` directly (rather than relying on memory of it) to find
what was actually functionally different, as opposed to just visually different:

| Legacy element | Delivered |
|---|---|
| "Focus Pending" counter — pending/total scoped to whichever delivery or customer is focused | **Done** — the focus banner now shows this instead of only the session-wide total |
| All / Pending / Scanned quick filter on the delivery list | **Done** — sits next to the existing search box |
| "Finish & Export Selected" — select several complete deliveries and finish them together with one set of courier/vehicle/box details | **Done** — `outbound_bulk_finish`, with a "Finish Selected" bar on the Table view. Already-finished or still-short deliveries are skipped and reported by name, never silently dropped or forced through |

**Not attempted, deliberately:** the legacy screen's dark sidebar shell, its device-camera QR scanning
(via a third-party JS library, for a workflow this app assumes runs on dedicated scanner hardware), and a
literal pixel-for-pixel visual reskin. These are aesthetic/architectural choices for the client to weigh
in on, not defects a rewrite should silently assume the answer to. The Table view is also currently the
only one of the four delivery views (Cards/Table/Kanban/Lines) with bulk-select checkboxes — extending
that to the others is the same pattern, not new design work.

**Refactor note:** `outbound_finish`'s body was factored into a shared `_finish_one_delivery()` so the
bulk path could reuse it verbatim rather than duplicating the courier-resolution/box-count/status-inference
logic. The existing single-delivery test coverage (8 tests) was re-run unchanged after the refactor and all
still pass, confirming behaviour did not shift.

**+6 tests** (`TestOutboundBulkFinish`), covering: finishing several at once, skipping already-finished
ones, skipping short ones (and reporting why), forcing them through with `proceed_anyway`, rejecting an
empty selection, and the viewer-tier permission check.

**Full regression after this section: 420 passed, 0 failed.**

---

## COMPLETED — row-level scoping across the whole module (closes Row 22's follow-up)

Row 22 delivered the four-tier hierarchy and scoped the three highest-traffic **list** screens. That hid
the links but was never a boundary: everything in this module is reachable by pk, so a picker on one team
could open, scan into, finish, export or overwrite another team's work by typing a different number into
the URL. This pass closes that.

**One helper, applied uniformly** — `rbac.get_scoped_or_404(model_or_qs, user, field=…, **lookups)`,
the detail-view counterpart to the existing `scope_queryset_by_team`. Kept as a single function so the
boundary has exactly one place to audit rather than forty hand-written filters.

It raises **404, never 403**, deliberately: a 403 on a row that exists and a 404 on one that doesn't
would let an outsider enumerate valid ids. Out-of-scope and non-existent are indistinguishable from
outside.

**Applied to every pk-addressable endpoint** — 34 call sites across `views.py`, `inbound.py` and
`zva05.py`, covering Outbound (session/scan/bulk-scan/undo/focus/state/finish/bulk-finish/export/
line-batch/edit-scan), Stock Validator (detail/export), Barcode Extractor (session/scan/columns/finish/
export/record-condition), Branch Transfer (plan/state/scan/auto-assign/line-batch/export), Inbound
(detail/scan/bulk-scan/undo/focus/finish/export, plus opening a receipt against a plan), ZVA05
(detail/scan/undo/focus/finish/export), Consolidator (detail/export) and Delivery History
(detail/courier-update). The user column differs per model — `operator`, `created_by`, `received_by`,
`picker` — which is why `field` is a parameter rather than a constant.

**The list screens were brought into agreement too**, so a row never appears in a table that its own
detail page then 404s on: Validator (and its KPI aggregate, computed off the same scoped queryset —
a KPI counting rows the table refuses to show is worse than no KPI), Extractor, Scan History *and its
export*, Branch Transfer, Inbound (both offerable plans and existing receipts), ZVA05, Consolidator,
the module launcher's four tiles, Reports, and the Live Monitoring board. Monitoring mattered more than
it looks: it names people and says what they are working on, so unscoped it showed every site's live
activity to everyone. `build_payload()` grew a `user` argument for this.

**Nothing changes on an unmapped installation.** `plant_scope`/`team_scope` still resolve to `None`
when no mapping exists, and `get_scoped_or_404` passes those users straight through — same opt-in
property the original Row 22 work was built on. Admins and superusers are never scoped.

**+67 tests** (`merilv/tests/test_row_scoping.py`). Structured as two disjoint teams where every fixture
row belongs to team A, then asserting team B gets 404 on all 17 read/export endpoints and all 21
mutations — with the mutations also asserting the *data did not change* (the scan survives a rejected
undo, the batch override does not apply, the delivery is not finished, the docket is not overwritten),
because a 404 that still performed the write would pass a status-code-only test. The mirror image is
covered too: 10 tests that the owner, their manager, and an admin all still get 200, so a scoping bug
that locks everyone out cannot pass. Plus the list/detail agreement tests and the helper's own contract
(bare model vs queryset, and that unowned `NULL`-user rows stay reachable).

---

## COMPLETED — offline-queue replays are now idempotent (closes Row 3.6's known hole)

Row 3.6 shipped the offline scan queue with one honestly-documented hole: if a request reached the
server and was accepted but the response never made it back, the queued replay would book the scan a
second time. Serialised items were incidentally protected by the engine's duplicate-serial check;
**non-serialised items had no guard at all**, so the replay silently over-picked. That is a
data-corruption risk in code we shipped, so it was fixed rather than documented again — and fixing it
was the prerequisite for ever rolling the queue out to the other consoles, since rolling it out first
would only have multiplied the risk.

**Server-side idempotency key.** The browser now mints a `client_ref` *before* the first attempt rather
than inside the failure handler, and the queue reuses that same ref on replay. `OutboundScan` gained a
`client_ref` column (migration `0014`) with a **conditional** unique constraint on
`(session, client_ref) WHERE client_ref > ''` — conditional because blank must stay non-unique for
every row written before this field existed and for any client that sends no ref. `outbound_scan`
short-circuits a second arrival of a known ref, returning `ALREADY_RECORDED` with the current state so
the console still repaints correctly. The DB constraint is the backstop if two replays race past the
view's lookup.

**+5 tests** (`TestScanIdempotency`), deliberately written against MLSIPL (`SEED_ENTITIES[1]`, the
non-serialised entity — the case that was actually broken): a replayed ref books one unit not two, two
*different* refs are two genuine picks, no-ref scans still work and blank refs don't collide, the ref is
persisted, and the short-circuit response still carries `state`.

**Still Outbound-only, by choice.** The mechanism is now safe to extend, but each console needs its own
key column and short-circuit; Outbound stands as the proven reference implementation rather than four
half-tested copies.

---

## COMPLETED — production-readiness bug sweep

A systematic audit of the module for crash-on-bad-input, lost-update and unbounded-work bugs, none of
which were register rows. What it found and what was done:

**Concurrency — lost updates and defeated guards.** Four places did read-decide-write without holding a
lock, so a second concurrent request could interleave and undo the decision:

- `scan_session_scan` read the session's counters *before* opening its transaction and wrote them back
  as **absolute** values. Two guns on one Live Scan session — a case `_live_validate_state`'s own
  docstring calls supported — both read `total=10` and both wrote `11`: one scan silently vanished from
  the counters while both `ScanRecord` rows existed. The whole read-decide-write cycle now sits inside
  one `transaction.atomic()` behind `select_for_update()`, the same shape `outbound_scan` already used.
- `outbound_line_batch` and `branch_transfer_line_batch` checked "is anything scanned against this
  line?" and then saved, unlocked. A scan landing in between repointed already-picked units at a
  different batch — precisely the corruption both docstrings claim to prevent. Both now re-read the line
  under `select_for_update()` and re-check before saving.
- `outbound_edit_scan` checked for a colliding serial then saved, with **no** unique constraint behind
  it to catch a race. Now locked; two concurrent corrections can no longer both succeed.
- `roles.assign` called `_set_role()` bare, which removes the old groups and *then* adds the new one as
  two separate autocommitted statements. A failure between them left the user holding **zero** Meril-V
  groups — locked out of the module until an admin noticed. Now wrapped, matching what `_upsert_user`
  already did with its identical call.

**Crash-on-malformed-input.** `reports` did `int(request.GET.get("days") or 30)` with no guard:
`?days=abc` was a 500, `?days=999999999` overflowed `timedelta`, and `?days=-5` was worse than either —
it put the window in the *future*, so every KPI rendered zero and the screen looked like an empty
warehouse rather than a bad request. Now coerced and clamped through `_report_window_days`.

Fourteen further sites passed a raw `request.POST` string straight into an integer pk lookup. Django's
`IntegerField` raises rather than simply not matching, and `get_object_or_404` only catches
`DoesNotExist`, so `user_id=abc` was a 500. Fixed with a shared `merilv/params.py::int_or_none` — a
malformed id now means "no such row", which is what it always meant. `get_scoped_or_404` also catches
this itself and answers 404, so all 34 scoped endpoints are covered by construction.

`box_save` fed unvalidated floats into `DecimalField(max_digits=7)`, so `length_cm=100000` raised
`decimal.InvalidOperation` as an uncaught 500 from an ordinary form post; a negative `max_qty` persisted
happily (`PositiveIntegerField` has no CHECK constraint on PostgreSQL) and silently disabled
box-capacity enforcement. `entity_edit` caught `ValueError` but not `OverflowError`, so `sort_order=inf`
was a 500 and `month_divisor=inf` reached the database as `Decimal("Infinity")`. Both now reject
inf/nan and clamp to what the columns can hold.

**Unbounded work.** `_live_validate_state` re-opened and re-parsed the *entire uploaded spreadsheet* on
every single scan — a 5,000-carton session parsed its dump 5,000 times, in the latency-critical path
between gun and beep. The dump cannot change after upload, so it is now parsed once at session creation
into a new `ScanSession.dump_snapshot` (migration `0015`), reduced to the four fields live-validate
actually reads. Sessions created before the field existed backfill on first use, so the cost is one
parse in total rather than one per scan. The per-scan history replay is deliberately left derived —
that is what keeps a reloaded page, a second device and a resumed session agreeing.

`delivery_export` and `delivery_invoice_export` materialised the whole filtered queryset with no cap;
an unfiltered "Export All" over a year of dispatch history built every row in memory and then an
openpyxl workbook on top. Now capped at 10,000 rows **and the sheet says so when it truncates** — a
short spreadsheet that looks complete is the failure worth avoiding. (`delivery_packing_list` already
capped at 50; the two Excel paths were the ones that never got the same treatment.)

**Five more `{% if page_obj %}` paginator-truthiness bugs** — `masters_activity`, `masters_customers`,
`masters_materials`, `validation_detail`, `validation_history`. An empty Django `Page` defines `__len__`
but not `__bool__`, so a valid page with a zero-row slice renders the "no results" empty state while the
count above it says thousands. This bug class had already been fixed three times in this module; these
five were leftovers.

**+26 tests** across `TestReportWindowParam`, `TestMalformedPkDoesNotCrash`, `TestBoxSaveValidation`,
`TestEntityEditNumberValidation` and `TestDumpSnapshot`. The dump-snapshot set includes a genuine
regression guard that monkeypatches `parse_system_dump` and **fails if scanning re-parses the dump at
all**, so the O(n²) behaviour cannot quietly return.

**Audited and found clean, stated so it is not re-audited:** every one of the 76 routed views was
checked for a missing `@require_POST` — all writes are either decorated or inside a
`if request.method == "POST"` branch. Every `float()` on a `DecimalField` was traced: all are display,
JSON output or read-only comparison, and every persisted quantity goes through an `F()` expression, so
there is no Decimal/float corruption path. All 18 divisions in the module have explicit denominator
guards.

**Full regression after this section: 513 passed, 0 failed.**

---

## COMPLETED — ZVA05 folded into Outbound, and a BT Scanning submodule

Client's words: in the outbound scanner there are two kinds of scanning, normal and ZVA05; ZVA05 should
stop being its own module and become a dropdown option for "what kind of scanning I want to do"; and a
BT Scanning submodule should take its place, as the old source code has it.

**Why this is right rather than just asked for.** ZVA05 was never a different *tool* — it is the same
picking job read from a different source document (open sales-order lines instead of a YVL06 delivery
dump). Giving it a top-level nav entry implied otherwise. In the legacy code all of this lived on one
page: `outbound-scanner.js` with `bt-scanner.js` and `zva05-mode.js` layered on top, switched by
`toggleBTMode()` and a `.bt-only` class. So this restores the old shape rather than inventing one.

**Outbound Scanner now has a mode dropdown** (`views.OUTBOUND_MODES`) — *Normal outbound* or
*ZVA05 auto-assign*. Submitted as a GET navigation, not a JS panel swap, so a chosen mode is a real
bookmarkable URL and the back button works, matching the tab-bar convention. Picking ZVA05 swaps in the
open-order upload and the list of open extracts; the ZVA05 console itself is untouched, only the way in
moved. `zva05.index` is now a redirect to `/meril-v/outbound/?mode=zva05` so old bookmarks still land
somewhere useful, and its upload split out to its own `zva05.upload` POST endpoint. ZVA05 is gone from
the launcher tiles, the flyout nav and the shared scan-mode tab bar — listing it as a sibling console
would contradict it being a mode. `templates/merilv/zva05.html` was deleted as dead, and
`navigation.py` re-parents the ZVA05 console's breadcrumb onto the Outbound Scanner.

**New BT Scanning console** (`merilv/btscan.py`, migration `0016`) in the nav slot ZVA05 vacated. The
split mirrors the old code: Branch Transfer is the *plan* side (upload the plan and MB52 stock,
pre-assign batches), BT Scanning is what the picker stands in front of. It adds the three things
`bt-scanner.js` had that the plan screen has nowhere to put:

- **Cartons.** Scans land in "Box 1", "Box 2", … because the receiving dock counts cartons, not units.
  A fresh session opens with one carton already created — a session with none would reject its own first
  scan. Carton names are derived from the count with a collision loop, so a hand-typed name cannot break
  the `(session, name)` unique constraint.
- **Colour zones** per carton, so the destination can sort a pallet by eye. Distinct from the plan's own
  `intransit_color_zone`, which describes a *line*; one carton can serve several lines of one zone.
  Unknown zone values are dropped rather than stored.
- **Per-session location priority**, because one site draws FG02-first and another FG01-first — that is
  an instruction to a picker, not a property of the entity. Blank falls back to the entity default.

**Focus Mode narrows the engine, not just the table.** This was the one design decision worth care: a
picker locked to one receiving plant must not be able to consume another plant's requirement by
accident, so the focus filter is applied to the line list handed to `process_transfer_scan`, not merely
to what renders. A focus on a plant the plan never ships to is refused with a 400 rather than silently
hiding every line.

**One set of numbers, deliberately.** Picking goes through the existing `services.transfers` engine and
moves the same `BranchTransferLine.picked_qty` the plan screen shows. The alternative — its own quantity
column — would let the two screens disagree about what physically left the building.

**+26 tests** (`merilv/tests/test_btscan.py`) and **+14** for the mode change. The BT set covers the
carton lifecycle, that picking moves the plan's own quantity, that undo reverses it and never drives it
negative, that a rejected scan logs but books nothing, both halves of Focus Mode, location priority, and
the carton-name collision case. Three pre-existing ZVA05 tests asserted the old landing page; they were
rewritten to assert the new contract (302 into the outbound shell) rather than deleted, so the change is
pinned rather than merely un-tested.

---

## COMPLETED — Invoice Consolidator against the client's own template

The client supplied `module_info/` — four real MLSIPL shipping invoices plus the two HTML screens they
were reading them with (`Export_Invoice_Data_Reader.html`, `Export-Consolidator.html`) and screenshots of
the intended layout. This closes Row 6 against that material rather than against my reading of it.

**Verified against the documents, not against my own parser.** Each of the four invoices prints its own
`Total Quantity` and `Subtotal` on its face. The parser's output now matches both, exactly, on all four:
360/$45,000.00 · 986/$25,644.00 · 360/$45,000.00 · 358/$44,750.00. A parser checked only against its own
output proves nothing, so those printed figures are what the tests assert.

**Two real bugs found by doing this.** First, the tariff line
`Harmonized System Code: 9021.31.000000 / Artificial Joints` was being parsed as a line item — material
`000000` at $21.31 — because "000000" is code-shaped and "21.31" is price-shaped. The legacy HTML reader
had the identical false positive. Now filtered by `_EXPORT_SKIP_LINE_RE`, along with the other footer
boilerplate. Second, every one of these invoices carries a second page of packing terms with no line
items, and warning per empty page told the operator that four perfectly good invoices needed checking by
hand. That warning is now raised once per *file*, and only when the file produced nothing at all.

**The client's 13 columns, all populated:** Invoice # · Invoice Date · Line # · Qty · Item Code · Mfg
Date · Expiry Date · Lot Number · Description · 510(k) · Unit Price · Line Total · DB Match. Unit price
and line total were both being collapsed into `amount` before (an export line carries two prices —
`$125.00 $11,625.00`); 510(k) was being stripped and discarded rather than captured; there was no line
number. **DB Match** is new: each item code is checked against the material master (on `material_code`
*or* `scan_code`, since invoices quote either) in one query per page, so a miss flags either a missing
master row or an invoice typo.

**Two source tabs** — *Read PDF Invoices* / *Upload Excel/CSV* — matching the client's screen. Not
decoration: a run is one or the other, never both, and the single mixed input this replaces had to carry
a caption apologising for that. The inactive side's input is `disabled` as well as hidden so the browser
leaves it out of the POST entirely; two inputs sharing the name `files` would otherwise both submit.

**KPI tiles** now lead with Rows / Quantity / Invoices, as the client's Consolidation Engine panel does,
with Files kept because it is the one number that says whether every uploaded file was read.

The export-only columns render only on a PDF run — putting four permanently-empty columns on every
spreadsheet import would read as missing data rather than as not-applicable. The xlsx export follows the
same rule, so the sheet matches the screen.

**+32 tests** against the four real invoices (`TestRealExportInvoices`, parameterised), asserting row
counts, both printed totals, the invoice number coming from the document rather than the filename, all
13 columns populated, gapless line numbering, the tariff-line false positive staying dead, and no
spurious "check by hand" warning. They skip cleanly if `module_info/` is absent, so the suite does not
depend on sample data being committed.

**Full regression after both sections: 518 passed, 0 failed.**

---

## COMPLETED — BT Scanning folded into the Outbound Scanner (alongside ZVA05)

Follow-up to the earlier ZVA05 fold, same reasoning applied to BT Scanning: it is a second console onto
a picking job, not a different tool, so it gets a third entry in the Outbound Scanner's mode dropdown —
Normal outbound / ZVA05 auto-assign / BT Scanning — rather than its own dashboard tile.

**Split the same way ZVA05 was.** `btscan.index` is now a pure redirect into `/meril-v/outbound/?mode=btscan`
(kept, not deleted, so old bookmarks still land somewhere). The form that used to live on that page — pick
an open transfer plan, optionally set a focus plant and location priority, open a session — moved into a
new `btscan.open_session` POST endpoint and a third conditional block in `outbound.html`. The actual BT
Scanning console (cartons, colour zones, scanning) is completely unchanged — only the two entry points
(the plan-picker form and the open-sessions list) moved.

Removed: the "BT Scanning" dashboard tile, its entry in the flyout nav, and its tab in the shared
scan-mode tab bar (that tab bar now only lists Outbound Scanner, Branch Transfer, Inbound Scanner and
Barcode Extractor — the four screens that are still separate consoles). `templates/merilv/btscan.html`
(the now-dead standalone landing page) was deleted, and `btscan_detail.html`'s breadcrumb now reads
"Outbound Scanner · BT Scanning", matching how the ZVA05 console's breadcrumb already worked.

**+5 tests**, extending the existing `TestOutboundScanModes` class rather than writing a parallel one: the
mode dropdown now asserts all three keys in order, the BT plan-picker fragment renders and lists open
plans, normal mode does not leak BT plans, the old `/meril-v/bt-scan/` URL redirects correctly, and the
console itself still opens by URL. One test (`test_zva05_and_btscan_are_gone_from_the_scan_mode_tab_bar`)
had to be rewritten mid-pass: asserting the plain string "BT Scanning" wasn't in the page failed, because
that string legitimately appears inside the new `<option>BT Scanning</option>` — fixed to check for the
tab bar's own `href="/meril-v/bt-scan/"` instead, which is unambiguous.

**Full regression after this section: 574 passed, 0 failed** (one pre-existing, already-documented flaky
test — `TestMaterialYmm061::test_grouping_filters_narrow_the_list`, in an unrelated masters/material area —
failed once on the full run and passed cleanly in isolation and on a full re-run; not caused by this change).

---

## TRIED AND REVERTED — Scanning Consoles landing page

Briefly built, then explicitly reverted the same session: grouping Outbound Scanner, Branch Transfer,
BT Scanning, Inbound Scanner and Barcode Extractor behind one "Scanning Consoles" launcher tile, with a
new intermediate page showing the five side by side. The client's follow-up was to keep the old layout —
those five stay as separate top-level tiles on the Meril-V home page, unchanged from before this section
was ever written. `views.scanning_consoles`, its URL, and its template were removed; the launcher's
`tools` list and `navigation.py`'s parent map are back to exactly what they were.

**What this did *not* touch, deliberately:** ZVA05 living as a mode inside the Outbound Scanner (see
"COMPLETED — ZVA05 folded into Outbound") is a separate change from an earlier pass and was not part of
what got reverted here — the client's instruction was specific to the home page cards, not to that
integration.

---

## COMPLETED — merilv migration history collapsed into one file

Client's ask: keep all of merilv's migrations in one file, rather than the 16 (17 counting the merge)
that had accumulated. `merilv/migrations/0001_initial.py` is now the only migration — every other file
is gone.

**Approach.** Django's own `squashmigrations merilv 0016_btscan_console` generated the merged file, then
flagged "manual porting required": two `RunPython` data migrations — role-group seeding
(`0002_seed_merilv_roles`) and the plant/org legacy-mapping recovery (`0007_recover_plant_org_mappings`)
— reference Python functions that live in modules named e.g. `0002_seed_merilv_roles`, and a migration
filename starting with a digit is not a valid Python identifier, so Django can't write an import for it
into the squashed file. Both functions were copied in verbatim (unchanged logic) and the two
`RunPython` operations repointed at the local copies. Everything else in the squash — all `CreateModel`/
`AddField`/`AddIndex`/`AddConstraint` operations for the whole app — needed no manual work.

**Verified two ways, deliberately, since this touches the schema-tracking metadata of a database that
already holds 32,299 materials and 7,480 customers:**

1. **Against the live container.** Squashed with `replaces` still listing all 17 originals, ran
   `migrate merilv` — Django detected every replaced migration as already applied and recorded the
   squash as satisfied *without running any SQL*, confirmed via `makemigrations --check --dry-run`
   ("No changes detected") and a before/after row-count comparison (materials, customers, users,
   activity log — all identical). Only then were the 16 original files deleted and `replaces` removed.
2. **Against a disposable scratch database**, created and dropped specifically for this
   (`CREATE DATABASE merilv_squash_scratch` / `DROP DATABASE` after), with `POSTGRES_DB` overridden for
   one `migrate merilv` call. This is the check that actually proves the single file is correct for a
   *fresh* install: all ~40 models were created from nothing, and both `RunPython` functions executed
   for real — confirmed by the three role groups (`Meril-V Viewer/Picker/Admin`) existing afterward and
   `makemigrations --check` reporting no drift on that fresh schema too.

**One naming wrinkle, caught and fixed rather than shipped:** the squashed file was initially left named
`0001_squashed_0016_btscan_console.py`. Renaming it to the cleaner `0001_initial.py` changes what Django
calls it internally — and the live database's `django_migrations` table had already recorded the squash
under the old name. Renaming without accounting for that would have made Django believe the file was
*unapplied* on next `migrate`, and attempt to `CREATE TABLE` on tables that already exist. It happened
to still show "applied" after the rename only because a *different*, genuinely original `0001_initial`
row already existed in the table from 2026-08-14 (before any of the other 16 migrations existed) and the
name happened to match — Django's applied-check is name-only, with no content hash. That coincidence was
verified, not assumed: re-ran `migrate`/`check`/`makemigrations --check` afterward, all clean. The other
17 now-orphaned bookkeeping rows (pointing at filenames that no longer exist) were then deleted, leaving
exactly one `django_migrations` row for `merilv`, matching the one file on disk.

**Full regression after this section: 553 passed, 0 failed** (unchanged from before the squash, as
expected — no model or behaviour changed, only how the schema history is recorded).

---

## COMPLETED — UI restyled to match material_rework's tiles and KPI cards

Client's ask: change Meril-V's colors, KPI tiles and tile icons to match the look of the
`material_rework` module.

**The color palette already matched** — both modules share the same brand blue (`#0076C8`) and the
same green/amber/violet/rose/teal accent family, so this was a structural fix, not a repaint.

**Tile icon badges** (module launcher, `.mv-tile__icon`) — were a light-tint chip with a colored icon;
now a solid, saturated square with a white glyph, matching `material_rework`'s `.md-card-icon`. One CSS
rule in `screens.css`; zero template changes, since the existing `{% fa_icon %}` inline-SVG icons use
`currentColor` and pick up the new white automatically.

**KPI cards** (16 screens: Reports, Validator, Delivery History, Monitoring, every scan console's
totals, etc.) — the accent used to run along the bottom edge and tint the number itself; now a
**left-side accent bar**, and the number stays dark navy regardless of the tile's rotating accent color
— `material_rework`'s `report.html` KPI-card language exactly. The three semantic states
(`--danger`/`--warn`/`--ok`) still override the number's color on purpose — an alarming count should
still look alarming.

**Every one of the 62 individual KPI tiles across all 16 templates got an icon chip** above the number
(`.mv-kpi__icon`, a small tinted 32px badge), matched to what each metric means — Deliveries → truck,
Scans → scan/barcode, Pending/Idle → clock, Damage → x-circle, and so on. Verified two ways: a
`_ICON_PATHS` cross-check so a typo can't silently fall back to the default "list" glyph, and one
render-test per screen asserting the icon-chip count equals the label count — so a KPI added later
without an icon fails the suite rather than quietly looking undressed next to its siblings.

**+16 tests** (`test_kpi_icons.py`). While writing them, the shared fixtures (`entity`, `client_in`,
`bt_plan`, etc.) that had only ever lived in `test_views.py` were moved into a real `conftest.py` so
this new file could use them without duplicating — otherwise the two definitions would eventually drift.

**Full regression after this section: 569 passed, 0 failed.**

---

## COMPLETED — Reports → User Activity (admin-only per-user analytics)

Client's ask: a dashboard in the reports module listing each user's data by role, visible only to
Meril-V Admin and superusers, showing the end-to-end flow — what they did, how many, over how many
days, in how much time, on which dates.

**Why the ActivityLog alone could not answer this.** That log only records master-data and
configuration edits. A picker who scanned 4,000 cartons and closed 60 deliveries appears in it exactly
zero times. So the new `merilv/useractivity.py` reads all **15 tables that carry a user FK** —
delivery/ZVA05 dumps, BT plans, four kinds of session, four kinds of scan, deliveries closed, BT
receipts, consolidations and the activity log — through one declarative `ACTIVITY_SOURCES` registry.
A test walks that registry and issues a real query per entry, so a typo'd field name fails the suite
instead of silently reporting zero for a whole activity type.

**Attribution is per-actor, not per-session-owner.** `OutboundScan` is credited via its own
`scanned_by`, not `session__operator` (which the older Reports screen uses for scoping). On a session
two people work in turn, the operator field names whoever *opened* it — crediting them with a
colleague's scans is precisely the misattribution this screen exists to prevent.

**Two screens.** A leaderboard (every user, ranked, with a stacked composition bar showing the *mix*
of their work — a picker who only scans and a supervisor who only closes deliveries look very different
at the same total) and a per-user drill-down: daily timeline with gap days preserved, hour-of-day
histogram in Asia/Kolkata so a shift pattern is legible, per-source breakdown, and a merged
newest-first event feed capped at 250.

**Honest about time, which is the part most likely to be misread.** There is no login/logout tracking
anywhere in this module, so real time-on-task is *not knowable* and the feature does not pretend
otherwise. Two measurable things stand in, each labelled on its own tile:
- *Active window* — first-to-last action per day, summed. An engagement span, **not** attendance;
  breaks inside a day are counted. The tile says "first→last action per day" rather than leaving the
  number to look like a timesheet total.
- *Session time* — real `updated_at - created_at` across console sessions. Narrower but truer; an
  upload or a master-data edit has no duration at all.

**Cost.** Every metric comes from one grouped query per source keyed on `(user, day)` — never a row per
scan. Measured: **23 queries for the leaderboard regardless of user count**, 53 for a drill-down
(three passes of fifteen sources). Fetching timestamps individually would have pulled millions of rows.

**Two product decisions made while testing, both now pinned by tests:**
1. Active **superusers count as having access** even with no group — `rbac.is_merilv_admin` grants them
   the module outright, so excluding them made the "of N with access" tile undercount exactly the
   people who have the most of it.
2. **Disabled accounts are off the roster but still in the record** — a switched-off login is not
   access, but work already done stays attributable.

**Access control is the feature's precondition, so it is tested first and hardest.** Picker, Viewer,
**Manager** and anonymous are all refused on all three routes (list, detail, export); admin and
superuser get in. Manager is denied on purpose — the ask was admin + superuser, and a manager is a
different tier. The Reports-page entry point is *hidden* from non-admins rather than shown-and-refused,
since the target would only redirect them.

**+94 tests** (`test_user_activity.py`), covering RBAC per role per route, attribution, null-FK rows
from deleted accounts, window arithmetic including untrusted `?days=`, the derived time metrics, chart
payload shape and serialisability, the user picker, and the 33-column export.

### Follow-up — rebuilt on real charts after client review

First cut used CSS progress bars for every comparison. Client's objection, verbatim: *"not like this
that u are showing everything in the progress bar and all use something different, like how a dashboard
should look"* — plus two concrete additions: track user *performance*, and let an admin *select a
specific user* as well as see all users.

**Charts are Highcharts**, already vendored at `static/lib/highcharts/` and already used by the billing
and material-rework dashboards. So this screen now inherits the platform's chart language rather than
inventing one, and still fetches nothing at runtime (no CDN — asserted by a test). Every CSS progress
bar is gone from both screens, also asserted by a test: next to a real chart, a bar reads as a chart
that failed to load.

**All-users view** — stacked column trend across the whole window (quiet days included, because "the
team did nothing for three days" is information); clickable ranked bar of the busiest users; work-mix
donut; actions-by-role donut; hour-of-day column; weekday column.

**Individual view** — daily stacked trend with *their own average as a dashed plotLine* and a
*cumulative spline on a second axis* (which is what turns "busy days" into "are they still working");
minutes-on-task areaspline; hour-of-day; work-mix donut; weekday; per-source ranked bar.

**Performance is now relative, not absolute.** A Standing panel gives rank, team **median** (not mean —
one 300-scan bulk paste drags an average far enough to make everyone else look idle) and a signed
delta. An admin should not have to eyeball whether 4,000 scans is a lot.

**User picker** — one `<select>` on both screens. Empty value = all users, a pk = that person. It
*redirects* to the individual URL rather than branching in place, so that view stays shareable,
bookmarkable and back-button-correct, and it carries the window across so numbers don't appear to
change under the reader. Options are ordered busiest-first with totals inline, because a bare
alphabetical list of forty names makes an admin open them one at a time to find the one that matters.

**Design choices worth recording:**
- Counts are drawn as **columns, not areas** — an area spline interpolates between days and implies
  activity at times when there was none. The one areaspline is minutes-per-day, where a slope is
  genuinely meaningful.
- Weekday bars plot the **per-occurrence average**, not the sum: a 30-day window can hold five Mondays
  and four Sundays, and a raw total reads that calendar accident as a real preference.
- Drill-down URLs are **reversed server-side** and shipped in the payload. A `/0/` placeholder
  substituted in JS breaks the moment a real id or query value happens to be 0.
- The payload goes through `json_script`, which escapes `</script>`; `json.dumps` printed raw would not.

**Verification limit, stated plainly:** there is no browser automation in this environment, so "the
charts render" is verified by construction — Highcharts tag present, every container div present, every
builder invoked against it (both asserted by tests), payload valid JSON of the right shape on the live
page, and `node --check` on the JS. Not by a screenshot.

---

## Change register

Priority is the client's own from the sheet. "Size" is my estimate: S = hours, M = 1–2 days, L = 3+ days.

### Row 1 — Stock Validator (HIGH)

| # | Item | Size | Notes |
|---|---|---|---|
| 1.1 | KPI cards missing | — | **DONE.** 9 tiles: Total System Stock, Total Scanned, Matched (Consumed), Batch Mismatch, Remaining Stock, Good Cond, Damage, Expiry/Near, Variance |
| 1.2 | Serial details not shown on screen (incl. "variant"/"remaining" sections) | — | **DONE** — `validation_detail.html` renders `physical_serial`/`system_serial`/`available_batches`/`available_serials` |
| 1.3 | False-positive "serial match error" in details column after a successful match | — | **DONE** — see VERIFIED ROOT CAUSE #1 |
| 1.4 | Serial numbers not fetched/extracted | — | **DONE** — see VERIFIED ROOT CAUSE #1 |
| 1.5 | Batch validation should show **available** batch, not just required batch | — | **DONE** — `available_batches` written and displayed |
| 1.6 | MLSIPL validation not working | — | **DONE** — see VERIFIED ROOT CAUSE #1 |
| 1.7 | Hierarchical validation cascade: Material+Batch+Serial → Material+Batch → Material-only (material-only must list available batches AND serials) | — | **DONE** — `services/validation.py` match-tier engine (`match_type`: Serial/Batch/Material) |
| 1.8 | Serial details missing from exported reports | — | **DONE** — `services/export.py` includes Phy./Sys. Serial and Available Batches/Serials columns |
| 1.9 | Sorting & filtering need improvement | — | **DONE** — `validation_detail` supports `q`/`status`/`stock` filters |

### Row 2 — Outbound Scanner (HIGH)

| # | Item | Size | Notes |
|---|---|---|---|
| 2.1 | Material details + assigned serials not shown per line | — | **DONE** — new Lines view, see "COMPLETED — Rows 2, 4, 7" |
| 2.2 | Bulk paste of serials missing | — | **DONE** — new Bulk Paste tab + `outbound_bulk_scan` |
| 2.3 | Results only appear after finish — need real-time | — | **VERIFIED ALREADY WORKING** — `outbound_state` polls every 2s |
| 2.4 | UI downgrade vs old version | — | **DONE, scoped** — Focus Pending counter, delivery status filter and bulk-finish are ported; a literal pixel-for-pixel reskin was not attempted (see "COMPLETED — Row 2.4 and 2.5") |
| 2.5 | "Core functions generally not working" | — | **DONE** — a real bug found and fixed: re-scanning or undoing against an already-finished delivery silently corrupted the exported record. See "COMPLETED — Row 2.4 and 2.5". |

### Row 3 — Barcode Extractor (HIGH) — 13 sub-items

| # | Item | Size | Notes |
|---|---|---|---|
| 3.1 | Serial extraction fails (MHPL, Live Validation) | — | **DONE** — see "COMPLETED — Row 3" |
| 3.2 | Dedup logic: duplicate **batch** is legal, duplicate **serial** is not | — | **DONE** — root cause fixed in `live_scan`/`live_validate` |
| 3.3 | Master data population fails — missing Material Group / PH Level; YMM061 material master must be stored backend | — | **DONE** — same work as Row 17, already completed |
| 3.4 | Live Scan must show Box Count **and** Total Count | — | **DONE** — per-scan box + scoped Box Count tile |
| 3.5 | Allow correcting condition Good↔Damage after scan | — | **DONE** — per-row correction + session-level toggle |
| 3.6 | Offline / network-loss handling | — | **DONE** — client-side localStorage queue (`offline_queue.js`), live on the Outbound console; see "COMPLETED — Row 3.6, 3.8, 3.10, 5" |
| 3.7 | KPI cards: Duplicate / Valid (Found) / Not Found (Invalid) | — | **VERIFIED ALREADY PRESENT** |
| 3.8 | "Layout feature" missing across every module | — | **DONE** — server-backed Saved Layouts, live on Scan History and Material Master |
| 3.9 | Visually highlight serial-mismatch, batch-mismatch, expiry | — | **DONE** — row tint by result family |
| 3.10 | Switch between modules simultaneously for entry | — | **DONE** — shared mode-tab bar now spans all five scanning consoles (was 3 after Row 7) |
| 3.11 | Export buttons for Scan History, Master Data, Layout, Strings | — | **DONE** — Scan History (`scan_history_export`) and Master Data already existed; "Strings" is covered by the existing per-session raw-scan export. "Layout" export wasn't reinterpreted as a separate download — a saved layout is a live, reusable view (Row 3.8), not a document to hand someone. |
| 3.12 | Excel export formatting: dates, numbers, colour, headers, borders | — | **DONE** — `build_workbook()` now borders every cell and formats dates, fixing every export in the module at once |
| 3.13 | Dropdown-driven advanced filtering + sorting | — | **DONE** — search + status + whitelisted sort on Scan History |

### Rows 4–10 — Workflow modules

| # | Row | Item | Pri | Size |
|---|---|---|---|---|
| 4.1 | Branch Transfer | "Ask material" batch assignment not working | — | **DONE** — see "COMPLETED — Rows 2, 4, 7" |
| 4.2 | Branch Transfer | Missing consumption **priority** rules (which location consumed first) | — | **DONE** — `Entity.bt_location_priority`, ported from `bt-scanner.js:22,866-899` |
| 4.3 | Branch Transfer | Plan Summary doesn't show actual requirements | — | **DONE** — plant × colour-zone summary table |
| 4.4 | Branch Transfer | No real-time stats during transfer | — | **DONE** — `branch_transfer_state` + 2s poll |
| 5.1 | Inbound Scanner | "Not working, needs improvement" | — | **DONE** — brought to parity with its sibling consoles (bulk paste, per-line serial detail, shared mode tabs); no legacy spec existed to diagnose the original complaint against, so ask the client to re-test |
| 6.1 | Invoice Consolidator | "Not working" — rebuild per `Export-Consolidator.html` | — | **DONE** — see "COMPLETED — Row 6" |
| 7.1 | ZVA05 | Should be an Outbound Scanner *mode* like BT, per old structure | — | **DONE** — shared mode-tab bar across all three consoles; see notes on why the data models stay separate |
| 8.1 | Monitoring | feedback = "NA" | MED | — no action |
| 10.1 | Reports | "Unable to check without data" | — | **DONE** — see "COMPLETED — Row 10" |

### Row 9 — Delivery History (HIGH) — most precisely specified row

| # | Item | Size | Notes |
|---|---|---|---|
| 9.1 | Restore 3 tabs: Delivery Log / Summary Analytics / Finished Deliveries | M | |
| 9.2 | Summary Analytics dashboard: cards (Total Deliveries, Total Scans, Unique Products) + tables (Top Outbound Materials, Top Pickers Leaderboard, Date-wise Summary) | M | |
| 9.3 | Buttons: Export Selected, Packing List (Selected/All), Export All, Refresh, Select-All checkbox | M | |
| 9.4 | Filters: From/To date pickers; sorting (Newest, Oldest, Delivery No. A–Z) | S | |
| 9.5 | Finished Deliveries table columns: Courier No, Docket, Box Details counted per type A–F, Weight, Status | M | May need model fields |
| 9.6 | Pagination: Prev/Next + 25/50/100 page-size dropdown (currently frozen "Showing 0–0 of 0") | S | |
| 9.7 | Courier Details "Fill" popup for Pending Courier: docket barcode scan, vehicle number, per-box-type counts | M | PRD §5.4 lists the field set |

**Investigate first:** the client reports a *blank* table and frozen "Showing 0–0 of 0". Determine whether
that's a broken queryset or simply an empty table in their environment — this changes 9.5/9.6 from
"rebuild" to "fix filter". Cheap check, do it before estimating.

### Rows 11–23 — Master Data (mostly HIGH)

| # | Row | Item | Pri | Size |
|---|---|---|---|---|
| 11–16.1 | Company, Sales Org, Plant, Storage Loc, Billing Type, Sales Type | Data must align to the **org hierarchy**; Org page lacks mapping management. Rebuild per `admin.html` "Org Hierarchy" tab | HIGH | **L — largest single item.** One shared fix covers all 6 rows |
| 17.1 | Material | Use full YMM061/YMM601 header set incl. **Material Group, EAN, PH Level**; single screen with layout feature; consumed by validators | HIGH | L |
| 18.1 | Customer | "All okay, recheck after courier master" | — | **VERIFIED** — Row 19 is done; the Customer master screen's own `courier_partner`/`courier_number` fields were already correct and needed no change |
| 19.1 | Courier | Add columns: Courier No, Name, Vehicle #, Vehicle Type, GSTIN | HIGH | S |
| 19.2 | Courier | Enable bulk upload | HIGH | S if the existing `MASTER_SHEETS` template/import framework can absorb it |
| 20.1 | Carton | Add SKU Name column | MED | S |
| 20.2 | Carton | Bulk upload with template | MED | S — same framework as 19.2 |
| 21.1 | Entity Rules | "Need to test" | — | — no change requested |
| 22.1 | Roles & Access | 4-tier hierarchy **Admin → Manager → Picker → Viewer**; Manager mapped to plant(s); Picker sees only their mapped Manager's data | HIGH | **L — row-level data scoping touches every query in the module** |
| 23.1 | Activity Log | "Ok" | — | — no change |

---

## Cross-cutting themes

Four themes account for most of the 60+ items. Fixing these once fixes many rows:

1. **Serial handling end-to-end** — parse → store → display → export. Rows 1, 2, 3.
   Root cause #1 is the parsing half; display/export is a separate half.
2. **Excel export quality** — formatting, full column sets, per-screen download buttons. Rows 1, 3, 9, 19, 20.
3. **"Layout feature"** — a saveable column/grouping preset the client wants in *every* module.
   Rows 3, 17. Must be specified before building.
4. **Org hierarchy + role scoping** — Rows 11–16 and 22 are both really "the data model must express
   hierarchy, and access must follow it". Highest structural risk.

## Recommended sequence

**Historical — this was the plan written before any work started. All of it has since been done,
including step 6's deferred list. Kept for the record of how the work was sequenced, not as a to-do.**

1. **Root cause #1** (MLSIPL/material-serial parsing) — small, verified, unblocks Rows 1/2/3 serial work.
2. **Courier + Carton columns and bulk upload** (19, 20) — small, self-contained, visible client win.
3. **Delivery History** (9) — most precisely specified, so lowest ambiguity risk.
4. **Org Hierarchy** (11–16) — biggest item; do after the quick wins land.
5. **Roles hierarchy** (22) — last of the structural work; needs the org model settled first.
6. Defer until clarified: 2.5, 3.6, 3.8, 3.10, 5.1. — *all five were subsequently built without
   waiting for clarification; see their COMPLETED sections above.*

## Open questions for the client

Most of these were answered by the old code or resolved by building the most defensible interpretation
and documenting it. Marked below so this list stops reading as a set of blockers.

1. ~~MLSIPL serials~~ — **resolved.** The profile was right: MLSIPL has no serial concept. The bug was
   that the "Unknown" placeholder was being compared as if it were a real serial (root cause #1).
2. **Still worth having:** a real MHPL barcode sample string that fails. The parser handles bracketed
   GS1 AI (21) correctly, so we have nothing failing to diagnose.
3. ~~"Layout feature" behaviour / per-user or shared~~ — **resolved by building it per-user**
   (server-backed, one active layout per user per screen). Shared presets would be additive.
4. ~~"Switch between modules at the same time"~~ — **resolved as the shared mode-tab bar** across all
   five consoles (Row 3.10).
5. **Still open, and the one genuinely worth asking:** Branch Transfer consumption priority — the actual
   business rule (FEFO? plant preference? storage-loc sequence?). The current implementation picks a
   defensible order; if their rule differs, this is a config change, not a rebuild.
6. ~~Reproduction steps for Row 5 / Row 2 "core functions"~~ — **superseded.** Row 2.5 turned out to be
   a real bug found without a repro (scanning against an already-finished delivery); Row 5 was brought
   to parity with its sibling consoles. Worth a client re-test rather than a question.
7. ~~Offline support scope~~ — **resolved by building true offline queueing** (Row 3.6), now with
   server-side idempotency so a replay cannot double-count.

## Known scope boundaries (still true after the production-readiness pass)

Stated plainly so "done" is not read as "everything imaginable":

- **Row 2.4** — the legacy screen's dark sidebar shell and a literal pixel-for-pixel reskin were not
  attempted. Its concrete functional gaps (Focus Pending counter, delivery status filter, bulk-finish)
  are closed.
- **Device-camera QR scanning** was never attempted, on any console.
- **Layout Manager** uses up/down reorder buttons, not the legacy drag-and-drop, and is wired to two
  screens (Scan History, Material Master) rather than every table.
- **Offline queue** is Outbound-only. Now safe to extend — see the idempotency section — but each
  console needs its own key column.
- **Rows 11–16** — the wide 12-column whole-hierarchy import and bulk row delete were not built.
  Neither was named in the feedback; both are additive.
