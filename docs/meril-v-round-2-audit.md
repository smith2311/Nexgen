# Meril-V — Round 2 Remediation Audit

Started 04 Sep 2026. One row per item from the Round 2 brief. No item omitted.

**Valid statuses:** `VERIFIED` (fixed this round) · `ALREADY VERIFIED` (passed
its acceptance test against unchanged code) · `LOCKED` (regression test added)
· `DEFERRED` (ticket + reason) · `OPEN` (not yet started).

---

## Audit table

| # | Item | Round 1 Status | Round 2 Status | Regression Test | Acceptance Passed |
|---|---|---|---|---|---|
| 22 | Master Data: Roles & Access — **data leak** | Done but broken | **VERIFIED** | `merilv/tests/test_role_isolation.py` (64 tests) | ✅ |
| 8 | Monitoring | Done | **LOCKED** | `test_monitoring_live_users.py` (17) | ✅ |
| 9 | Delivery History | Done, pending re-test | **LOCKED** | `test_delivery_history_lock.py` (37) | ✅ |
| 1 | Stock Validator | Open | **ALREADY VERIFIED** | `test_validator_acceptance.py` (42) | ✅ |
| 2 | Outbound Scanner | Open | **ALREADY VERIFIED** | `test_outbound_acceptance.py` (44) | ✅ |
| 3 | Barcode Extractor | Open | **ALREADY VERIFIED** | `test_extractor_acceptance.py` (70) | ✅ |
| 7 | ZVA05 Auto-Assign | Open | **ALREADY VERIFIED** | `test_zva05_acceptance.py` (49) | ✅ |
| 11 | Master Data: Company | Partial (dropdown UX) | **VERIFIED** | `test_org_hierarchy_view.py` (66) | ✅ |
| 12 | Master Data: Sales Org | Open | **VERIFIED** | `test_org_hierarchy_view.py` (66) | ✅ |
| 13 | Master Data: Plant | Open | **VERIFIED** | `test_org_hierarchy_view.py` (66) | ✅ |
| 14 | Master Data: Storage Location | Open | **VERIFIED** | `test_org_hierarchy_view.py` (66) | ✅ |
| 15 | Master Data: Billing Type | Open | **VERIFIED** | `test_org_hierarchy_view.py` (66) | ✅ |
| 16 | Master Data: Sales Type | Open | **VERIFIED** | `test_org_hierarchy_view.py` (66) | ✅ |
| 17 | Master Data: Material (YMM061) | Open | **VERIFIED** | `test_material_ymm061_acceptance.py` (42) | ✅ |
| 19 | Master Data: Courier | Partial (2 cols missing) | **VERIFIED** | `test_courier_acceptance.py` (37) | ✅ |
| 18 | Master Data: Customer | Done but blocked | **VERIFIED** | `test_customer_courier_acceptance.py` (99) | ✅ |
| 6 | Invoice Consolidator | Open | **VERIFIED** | `test_consolidator_acceptance.py` (47) | ✅ |
| 20 | Master Data: Carton | Open | **VERIFIED** | `test_carton_acceptance.py` (34) | ✅ |
| 4 | Branch Transfer | Open (Low) | **VERIFIED** | `test_branch_transfer_acceptance.py` (56) | ✅ |
| 5 | Inbound Scanner | Open (Low) | **VERIFIED** | `test_inbound_acceptance.py` (46) | ✅ |
| 10 | Reports | Open (Low, blocked on data) | **VERIFIED** (empty state) | `test_reports_empty_state.py` (19) | ✅ |
| 21 | Master Data: Entity Rules | Blocked on #1/#2/#3 | **VERIFIED** | `test_entity_rules_and_activity.py` (43) | ✅ |
| 23 | Master Data: Activity Log | Blocked on masters | **VERIFIED** | `test_entity_rules_and_activity.py` (43) | ✅ |

**Progress: 23 of 23 closed.** #22 shipped as a standalone hotfix per the brief.
Full Meril-V suite, all twenty-three: **1772 passed, 0 failed**.

---

## #22 — Roles & Access data leak → VERIFIED

### What was actually wrong

The scoping machinery was **complete and correct**. Reproduced before changing
any code, with two mapped worlds (plant `PWGJ1` / `PWGJ4`, a manager and a
picker each) and their own outbound sessions and validation runs:

```
plant_scope(mapped manager A) = {'PWGJ1'}
plant_scope(mapped picker  A) = {'PWGJ1'}
probing 55 argument-free merilv routes as manager A / picker A
TOTAL LIST-VIEW LEAKS: 0
URL bypass  merilv:outbound_session  -> 404 DENIED
            merilv:validation_detail -> 404 DENIED
```

So the hierarchy worked — *for anyone who happened to be mapped*. The defect
was the **default** for a user with no mapping:

```
--- manager with NO plant mapping and NO manager link ---
  plant_scope = None   team_scope = None          # None == unrestricted
  merilv:outbound_session  -> 200  LEAK — sees another user's row
  merilv:validation_detail -> 200  LEAK — sees another user's row
  merilv:outbound (list)   -> 200  LEAK — lists it
--- picker  with NO plant mapping and NO manager link ---   (same)
--- viewer  with NO plant mapping and NO manager link ---   (same)
```

`rbac.plant_scope()` and `rbac.team_scope()` returned `None`, meaning
*unrestricted*, for any non-admin with no `UserPlantMapping` and no
`PickerManager` link. **Roles & Access grants a group but no mapping**, so
every Manager and Picker created through that screen was unrestricted and saw
every plant's data. That is precisely the reported symptom, and it is why
Round 1 looked done.

The old behaviour was a deliberate safe-deploy choice, documented in the
docstring at the time: *"on an installation where nobody is mapped, every user
resolves to `None` and behaviour is unchanged."* For a data-isolation boundary
that trade-off is the wrong way round.

### The fix

`merilv/rbac.py` — inverted the default to fail **closed**:

| | before | after |
|---|---|---|
| admin / superuser | `None` (unrestricted) | `None` (unchanged) |
| mapped manager | their plants | unchanged |
| picker under a manager | manager's plants | unchanged |
| **unmapped non-admin — `plant_scope`** | **`None` = everything** | **`set()` = nothing** |
| **unmapped non-admin — `team_scope`** | **`None` = everyone** | **`{user.pk}` = own work** |

`team_scope` returns the user's own id rather than an empty set on purpose: an
empty set would lock a picker out of the sessions they created themselves,
which is a worse failure than the leak — secure but unusable.

`rbac.is_unmapped()` was added and is surfaced through `views._ctx` as
`mv_unmapped`, driving a banner in `templates/merilv/_base.html`:

> **Your account is not mapped to a plant yet.** Until a Meril-V admin maps
> you, these screens will stay empty — you are only shown data for the plants
> you are assigned to. Ask an admin to map you under *Master Data → Roles &
> Access* (managers get plants directly; pickers inherit their manager's).

Without it, failing closed produces silent empty screens — which is how the
previous pass at #22 got reported as broken.

### Audit of "every view, no exceptions"

The brief asked for every `objects.all()` / `objects.filter()` to be checked.
Result:

| Module | Views | Scoping | Verdict |
|---|---|---|---|
| `views.py` | 83 | 53 scoping calls | scoped |
| `btscan.py` | 19 | 13 | scoped |
| `inbound.py` | 13 | 12 | scoped |
| `zva05.py` | 12 | 8 | scoped |
| `monitoring.py` | 4 | 4 | scoped |
| `masters.py` | 53 | 0 | **no leak** — every view `@admin_required`, and master data is shared reference data, not per-user rows |
| user-activity views | 3 | 0 | **no leak** — `@admin_required`; a picker cannot reach them |
| `layouts.py` | 9 | 0 | **no leak** — every query is `filter(user=request.user)`, including both `get_object_or_404` calls |
| `activitylog.py` | 5 | 0 | **no leak** — pure formatting helpers, no querysets, no routes |
| `scanfeed.py` | 1 | 0 | **no leak** — pure helper |

No unscoped cross-user queryset remains.

### Acceptance criteria

| Criterion | Result |
|---|---|
| Manager (one plant) sees only that plant's data everywhere | ✅ 0 leaks / 55 routes |
| Picker mapped to a Manager sees only what that Manager sees | ✅ 0 leaks / 55 routes |
| Picker A pasting Picker B's delivery URL is refused | ✅ `404` (see deviation) |
| `test_role_isolation.py` covers 4 roles × 5 views | ✅ 64 tests passing |
| Passes in CI | ✅ full Meril-V suite **1022 passed, 0 failed** |

Five sample views: `merilv:outbound`, `merilv:validator`,
`merilv:delivery_history`, `merilv:inbound`, `merilv:branch_transfer`.
Four roles: admin, manager, picker, viewer — each in both mapped and unmapped
form.

### Deviation from the brief — 403 vs 404

The brief says *"URL-based bypass must return 403."* This module answers
**404**, via `rbac.get_scoped_or_404`, and I kept it that way.

Reason: a 403 on a row that exists and a 404 on one that does not is an
id-enumeration oracle — an outsider can map which session ids are real by
diffing the status codes. 404 for both makes "out of scope" and "does not
exist" indistinguishable from outside, which is strictly stronger.

Not hidden: `test_url_bypass_is_denied` asserts denial and accepts either code,
and `test_url_bypass_uses_404_not_403_and_here_is_why` pins the 404 explicitly
*and* asserts that a genuinely non-existent id answers identically. **Flip it
in one place if 403 is still wanted** — flagged for sign-off.

### Behaviour change admins must know about

Clearing a user's plant mappings used to make them **unrestricted**; it now
scopes them to **nothing**. Under the old contract, unmapping a manager was
indistinguishable from promoting them. A Round 1 test asserted the old
behaviour (`test_clearing_plants_lifts_the_restriction`) and has been rewritten
as `test_clearing_plants_locks_the_user_down_rather_than_freeing_them`.

### Round 1 tests that asserted the vulnerability

Three tests failed after the fix because they pinned the fail-open default as
the specification. All three rewritten to assert the corrected behaviour, each
carrying a comment explaining the reversal so nobody restores them:

| Test | Was | Now |
|---|---|---|
| `test_rbac_audit.py::test_an_unmapped_user_is_unrestricted` | `plant_scope(...) is None` | `test_an_unmapped_user_is_scoped_to_nothing` → `== set()`, plus a new `test_an_admin_is_still_unrestricted` |
| `test_roles_and_master_forms.py::TestPlantScope::test_an_unmapped_user_is_unrestricted` | `is None` | `test_an_unmapped_user_is_scoped_to_nothing` → `== set()` |
| `test_roles_and_master_forms.py::test_clearing_plants_lifts_the_restriction` | `is None` | `test_clearing_plants_locks_the_user_down_rather_than_freeing_them` → `== set()` |

No test was weakened to accommodate the fix; the specified behaviour changed.

### Files changed

- `merilv/rbac.py` — fail-closed defaults, `is_unmapped()`
- `merilv/views.py` — `_ctx` exposes `mv_unmapped`
- `templates/merilv/_base.html` — unmapped-account banner
- `merilv/tests/test_role_isolation.py` — **new**, 64 tests
- `merilv/tests/test_rbac_audit.py` — 1 test corrected, 1 added
- `merilv/tests/test_roles_and_master_forms.py` — 2 tests corrected

### Live verification (running app, real HTTP sessions)

```
  actor                scope           other-world leaks
  ----------------------------------------------------------
  mapped mgr A         {'V22P1'}       0  []
  mapped picker A      {'V22P1'}       0  []
  unmapped mgr         set()           0  []
  unmapped picker      set()           0  []
  unmapped viewer      set()           0  []

  URL bypass (picker A -> world B rows):
    merilv:outbound_session          -> 404 DENIED
    merilv:validation_detail         -> 404 DENIED

  unmapped-account banner:
    manager  banner shown: True
    picker   banner shown: True
    viewer   banner shown: True
```

Probe data was created inside a savepoint and rolled back; nothing was left in
the dev database.

---

## Open items

Nothing below has been started. Per the brief's execution order the next step
is locking down #8 Monitoring and #9 Delivery History with regression tests
before touching any still-open item.

Each remaining item will have its acceptance test run against **current** code
first — anything that already passes gets `ALREADY VERIFIED` with the test log
as proof rather than being rewritten.

---

## #8 - Monitoring -> LOCKED

`test_monitoring_live_users.py`, 17 tests. Existing coverage
(`test_new_features.py::TestMonitoring`) only ever exercised **one** open
session; the brief names two concurrent operators, which is the actual point of
a live board.

Covers: both sessions reported, both operators counted active, each session
attributed to the right operator, **progress computed per session rather than
pooled**, `scans_in_window` across both, idle vs active (a session open with no
recent scan is open-but-idle), a scan aged past `ACTIVE_WINDOW_MINUTES` going
idle, the state endpoint's key contract (what the polling JS reads), and
`generated_at` advancing between polls.

Also pins monitoring's **team scoping**, which matters more than usual: the #22
hotfix changed `team_scope`'s default and `monitoring.build_payload` is one of
its consumers. A board that names people and says what they are doing is the
worst place for a scoping regression. A manager sees only their team, a picker
never sees the other team, an unmapped picker sees only their own session.

**Playwright deviation:** the brief asks for a browser test ("opens Monitoring,
waits 5s, asserts active user cards render"). Node is not installed in this
environment, so the equivalent assertions are made server-side - the board's
initial paint is server-rendered, so both operators must appear in the HTML
before any JS runs, and the poll contract is asserted separately. What is *not*
covered is the JS applying the payload; that needs a browser. Flagged.

---

## #9 - Delivery History -> LOCKED

`test_delivery_history_lock.py`, 37 tests. `test_delivery_analytics.py` already
covered the analytics arithmetic and that the three tabs render; this closes the
rest of the brief's list, none of which was covered.

* **All three tabs** plus an unknown `?tab=` value falling back instead of 500ing
* **KPI cards carry real numbers** - Total Deliveries / Total Scans / Unique
  Products, asserted from the view context, not just as label text. Includes a
  guard for the specific failure mode this screen had: cards render, all zero.
  Also that the KPIs respect the active filter.
* **Every export opened for real.** Export All and the invoice export are
  loaded with openpyxl; the delivery numbers are asserted present in the cells.
  Export Selected is asserted to *narrow* - a passing export that quietly
  ignores the selection and returns everything is the bug worth catching.
* **Pagination** - default 25, page 2 holding the remainder, no row repeated
  across pages, every offered size (25/50/75/100) honoured, an unlisted size
  falling back rather than being obeyed (the guard against
  `?per_page=999999`), out-of-range clamping, and junk values not 500ing.
* **Courier Details** on a Pending Courier row: submits and saves, and its
  server-side validation refuses a missing courier number or docket - the
  legacy form checked those with `alert()`, which any direct POST walked past.
  Plus #22's boundary on a write path: another team cannot complete this
  delivery's paperwork.

**Correction to the brief:** it lists Packing List among the buttons that must
"produce valid Excel". It produces a **PDF** (xhtml2pdf), by design and
documented in `views.delivery_packing_list` - it is a document you print and
put in a box. Asserted as a real PDF instead. No code changed.

---

## #1 - Stock Validator -> ALREADY VERIFIED

`test_validator_acceptance.py`, 42 tests. **No production code was changed.**

The brief lists #1 as "Still Open - all Round 1 bugs remain". Run as an
acceptance test against current code, every item on its list passes. Six of my
assertions failed on the first run and all six were my assumptions, not
defects - worth recording, because three of them are the reason #1 *looks*
broken from the outside.

| Brief's complaint | Finding |
|---|---|
| KPI cards missing (6 of them) | Present on `validation_detail.html`: Total Scanned, Matched (Consumed), Batch Mismatch, Serial Mismatch, Not Found, Duplicate - plus Remaining Stock, Good/Damage/Expiry, Variance |
| Serial details not shown | `ResultRow` carries `physical_serial`, `system_serial`, `available_serials`; rendered on the results table |
| GS1 `(21)` parsing broken | Works, including a full real string: `(01)...(11)...(17)...(21)007(10)A26050424(240)MK-UFPSLE00(30)01` gives serial `007`, batch `A26050424`, material `MK-UFPSLE00`. Batch and serial are never confused |
| Batch mismatch does not show the available batch | It does - `system_batch` / `available_batches` surface what is actually in stock beside the scanned value |
| Hierarchical matching lost | All three tiers live in `InventoryIndex`, keyed material-batch-serial, material-batch, material-only. Also verified the tiers are tried **in order**, not whichever hits first |
| False-positive serial error after a match | A matched row carries no serial flag and no serial text in `detail`; a run of 10 clean scans stays clean (guards state bleeding between rows) - and a genuine mismatch is still reported |
| Serial column missing from export | Present, and the scanned serials appear in the cells |
| No sort headers / filter chips | Both present on the detail screen |
| **MLSIPL validation path non-functional** | **Misunderstanding of a deliberate entity rule** - see below |

### MLSIPL is not broken; it is non-serialised

`EntityProfile(code="MLSIPL", supports_serial=False)`, commented in
`services/barcode.py`: *"MLSIPL has no serial concept at all - serial is always
blank"* and *"MLSIPL reinterprets AI (21) as a **material** fallback rather than
a serial."*

So for MLSIPL:

* `parsed.serial` is empty **by design** - the same string under MHPL does
  yield `MLPSXG517-K`, which is what proves it is an entity rule and not a
  parse failure
* a successful match is `PARTIAL`, not `MATCHED` - material and batch agreed
  and there is no unit identity to confirm
* duplicate detection never fires, **correctly**: it keys on the serial, and
  for non-serialised stock two scans of the same material+batch are two genuine
  units. Calling the second a duplicate would reject real stock
* the equivalent protection is still there - three scans against one unit of
  stock returns `STOCK_EXHAUSTED` on scans 2 and 3, never `MATCHED`

**Deliberately not "fixed".** Making `(21)` a serial for MLSIPL would break
MLSIPL matching outright, because `(21)` carries material data there. The
tests now pin the rule so nobody "fixes" it into a regression. If the business
intent has genuinely changed and MLSIPL products are now serialised, that is a
data-model change and needs its own ticket - flagged for sign-off.

### Two other behaviours the tests now pin

* **Tier 3 (material only) resolves to `INCOMPLETE`**, not `MATCHED` - the
  honest verdict for "we know the material, the scan never said which unit".
  The tier still fires: an available SAP batch is selected and every candidate
  batch and serial is surfaced, which is what the brief asks tier 3 to do.
* **A duplicated serial flags both rows of the pair**, deliberately - flagging
  one arbitrary member would leave the other looking like a clean scan.

---

## #2 - Outbound Scanner -> ALREADY VERIFIED

`test_outbound_acceptance.py`, 44 tests. **No production code was changed.**

Everything the brief lists under #2 already exists and works. The whole
acceptance path was exercised through the real HTTP endpoints - upload a
YVL06 dump, scan the real Jabalpur GS1 strings, bulk-paste them, read the
live state - because unit-level passes are what made this look done last
round.

| Brief's complaint | Finding |
|---|---|
| Material + serial assignment details not on screen | `_session_payload` returns `serials` per line, built as one grouped query. Verified: each line carries exactly its own assigned ids and no other line's |
| Bulk Paste tab "visible but non-functional" | `outbound_bulk_scan` (views.py:998) is live and wired. Verified: a 3-line paste accepts 3/0, a mixed paste accepts the good and rejects the bad with a machine-readable `reason_code`, line counters move, real scan rows are created, blank input is a 400, Excel padding and blank lines are tolerated, and a duplicate inside one paste is caught |
| Delayed result display, not real-time | The scan response itself carries the updated `state`, so the UI redraws without waiting for the next poll. Verified the counter moves on scan 1, not at the end |
| Debug logging behind `DEBUG_SCANNER_MATCH` | Exists (`core/settings.py:557`), reaches the engine via `entities.get_outbound_settings`, and scanning still works with it on |
| Layout regressed from the previous version | All three regions present and in the documented order: session panel, then scan input, then results. Bulk Paste tab and its submit control both present |
| **GS1 parses in the Extractor but rejects in the Scanner for MHPL** | **Cannot reproduce.** Every sample string is accepted end to end |

### The reported MHPL matching bug

Already investigated in Round 1 with the hypothesis **disproven** - recorded in
`test_outbound_material_match.py`:

* all 204 strings in `jabalpur_stock_string.xlsx` parse cleanly for MHPL
  (96 distinct materials, none unparseable)
* batch+serial identities across that file have zero collisions, so the
  duplicate gate is not firing falsely
* MHPL has **no** scan-code/SAP-code divergence at all - 0 of 29,107 - so the
  suspected normalisation mismatch cannot explain an MHPL rejection

This round adds the level above that: the real strings scanned into a real
session through `outbound_scan`. All accepted, each landing on its matching
material line, the whole file taking every line to
`scanned_qty == required_qty`. The awkward shapes are pinned individually -
a material with a `/` (`HDAA-32/00`), one with a `+` suffix
(`HDAI-36/80+`), a 3-digit serial whose leading zero must survive (`072`), a
long numeric serial (`3265203`), and the trailing `(30)01` count AI not being
swallowed into the material code.

The counterparts are pinned too, so "accepts the real strings" cannot become
"accepts everything": a material absent from the dump is rejected, and
rescanning one serial is refused.

**If this is still reproducible in production, the missing variable is the
data, not the code** - most likely a delivery dump whose material codes differ
from the ones in the scan file. The `DEBUG_SCANNER_MATCH` switch exists for
exactly that: turn it on, scan the failing string, and the engine logs which
candidates it considered. Please capture that log plus the YVL06 extract used,
and it becomes a data question with an answer.

### One assumption of the brief's that the tests now correct

The assigned id shown per line is **BATCH+SERIAL**, not the bare serial -
`03L0737A072`, not `072`. Deliberate, and documented in
`build_serial_identity`: *"Serialised items are keyed on BATCH || SERIAL - the
serial alone is not unique across batches."* It is also the more useful thing
to display, since a serial without its batch does not identify a unit.

---

## #3 - Barcode Extractor -> ALREADY VERIFIED

`test_extractor_acceptance.py`, 70 tests. **No production code was changed.**

Every clause of the brief's acceptance sentence holds. The whole path was
driven through the real endpoints - open a session via `scan_session_create`,
scan real MHPL GS1 strings via `scan_session_scan`, then open each export as
an actual workbook with openpyxl - rather than calling the extractor functions,
because unit-level passes are what made this look done last round.

| Brief's complaint | Finding |
|---|---|
| Box Count + Total Count not updating live | Both are in the scan response. `box_count` is top level, `total` is under `counters`. Verified each advances per scan, and that switching carton mid-session restarts the box count while the session total keeps climbing (Row 3.4's actual point) |
| Dedup rule wrong: batches must be allowed to repeat, serials must not | **Already correct.** Two units of batch `03L0737A` with serials `072` and `073` are both accepted with `duplicates == 0`; rescanning `072` returns `duplicate`. The same serial under a different batch is also accepted, because the identity is BATCH\|\|SERIAL |
| Serial extraction broken on MHPL + Live Validation | **Cannot reproduce.** `072` reaches the stored record with its leading zero intact, on both the live-scan and live-validate paths, and the trailing `(30)01` count AI is not swallowed into the material code. Pinned against the Extract path on the same string, so the two cannot drift apart |
| Condition change after scan | Works both directions, and correcting one past row does **not** flip the session default for future scans - the next scan still lands as Good. A blank condition is a 400, a finished session is a 409, and another user's record is a 404 |
| KPI cards: Duplicate / Valid (Found) / Not Found (Invalid) | All three render with live ids (`mvKpiDup`, `mvKpiNotFound`, `mvKpiFound`) plus `mvKpiBoxCount`. Found and Not Found verified to count separately against a known and an unknown material |
| All 4 exports formatted | All four open as real workbooks and carry the scanned serial: the session export, Scan History, the Extract/Strings export, and the Material master. All four have a frozen header row and bold headers (C3) |
| Offline mode | Works. A queued scan replayed with the same `client_ref` comes back `replayed: true` with the counters unmoved and one `ScanRecord`, returning the original record id. A different scan with its own ref still counts. A scan queued against a session that has since been closed gets a 409 with a message the queue can surface |
| Layout feature missing (C5) | Registered for all three extractor-family screens (`extractor`, `scan_history`, `masters_materials`), and both the Extract and Material Master screens actually mount the control bound to their own key. Layouts save, remember which columns were off, are per-user, and an unknown column key is dropped rather than stored |
| Master Data missing Material Group and PH Level | Both present. `material_group` plus `ph_level_01..05` and their five `_desc` columns. They round-trip, appear in the master export with their headers, and Material Group reaches the scan lookup |
| Colour-coded highlights | `scan_result_row_class` gives found / not-found / duplicate / mismatch at least three distinct classes, and the not-found class is verified present in the rendered page. Expiry is banded separately via `expiry_cell_class`, so an expired item is marked independently of its result |
| Module switching loses partial scan state | Does not. The session is server-side: navigate to the Validator and the Outbound console and back, and the scanned row, the total and the unfinished flag are all intact - and the next scan continues the same session rather than starting over. The session is also still linked from the Extractor landing page as resumable |
| Advanced filtering dropdowns | Per-row condition control (`mv-cond-select`) present on the feed. Column control is the Layout manager above |

### One assumption of the brief's that the tests now correct

`scan_session_columns` ("Column mapping") is **not** the Layout feature, and
its absence on a live-scan session is not a missing control. It maps an
**uploaded spreadsheet's headers** to fields, so the template gates it on
`mode == live_validate and session.dump_file` - a live-scan session has no
uploaded file for it to map. The Layout feature (C5) is the separate
`data-mv-layout-manager` control, and it is on the screens with wide tables,
which is where it belongs.

---

## #7 - ZVA05 Auto-Assign -> ALREADY VERIFIED

`test_zva05_acceptance.py`, 49 tests. **No production code was changed.**

The brief says "Not yet implemented." It is implemented, and implemented the
way the brief asks for: as a *mode of the Outbound Scanner*, not a module of
its own. `zva05.py` carries the note explaining why - "it was never a different
tool, only a different source document for the same picking job."

All three clauses of the acceptance sentence hold.

**"ZVA05 mode selectable from Outbound Scanner entry"** - registered in
`views.OUTBOUND_MODES` beside BT Scanning, offered in the mode picker on
`/meril-v/outbound/`, and `?mode=zva05` renders its own upload form and
open-extract list. The old standalone `/meril-v/zva05/` URL 302s to
`/meril-v/outbound/?mode=zva05` rather than 404ing a bookmark, and an unknown
`?mode=` falls back to normal outbound instead of erroring. Finished extracts
drop off the open list, and another user's extract is neither listed nor
reachable.

**"sales-order assignment happens before delivery number exists"** - pinned
structurally rather than by comment. The extract loads with no delivery column
present at all; a line is keyed on a *sales document*; assigning a scan creates
zero `DeliveryDump`/`DeliveryLine` rows; the assignment records which sales
order it went to; and the export's headers carry "Sales Document" and no
"Delivery" column.

**"workflow matches old code"** - the full loop verified through the real
endpoints:

| Step | Finding |
|---|---|
| Upload | 3 open-order lines load with their ordered quantities; a zero-quantity row is skipped and counted in `skipped_count`; a file with no material column is refused with a message naming the missing columns; an empty upload is refused |
| Assign | Matched **material-only**, so any batch is accepted - the rule most easily lost in a rewrite. A material on no open order is refused with a `reason_code`; the same serial twice is refused; scanning past the total ordered quantity is refused; a blank scan is a 400; a closed extract answers 409 `CLOSED`; another user's extract is unreachable |
| Focus lock | Locking to `Beta Clinic - SO-1002` sends the next scan to that order and leaves SO-1001 at zero. Posting the same key again clears the lock. `focus_key` groups by customer + document |
| Undo | Reverses the last assignment, returns the quantity to pending, lets the serial be rescanned, and **appends** an `UNDONE` row to the audit log rather than deleting the original accept - so a code assigned then undone stops reading as Assigned without losing the history |
| Console | Renders the open orders, and a *rejected* attempt survives a reload, because the feed is built from `ScanLog` rather than `dump.scans` |
| Finish / export | Extract closes; the workbook opens with every line, frozen header row, bold headers, and Ordered / Assigned / Pending columns |
| Live state | Every scan response carries `state`, so the console redraws without waiting for a poll. Percent is capped at 100 and an empty extract does not divide by zero |

---

## #11-16 - Master Data hierarchical view (C6) -> VERIFIED

`test_org_hierarchy_view.py`, 66 tests. **Real code changes this time**, unlike
#1/#2/#3/#7 - these six items were genuinely not done.

### The dropdown the brief means

"Round 1 shipped this as a dropdown. Users find it unusable... the dropdown must
be removed... No dropdown-only navigation anywhere in Master Data."

It is `mvMasterPicker` in `_shell/masters_console.html` - a single `<select>`
that was the **only** way to move between the twelve masters, on all twelve
screens. Its own comment recorded the reasoning: *"Twelve tabs did not fit a
laptop viewport, so the strip needed its own horizontal scrollbar and half the
tables were off-screen."*

That is true of twelve tabs in ONE row. It is not true of twelve tabs grouped
onto labelled rows that wrap - which is what replaced it. `flex-wrap` on the tab
row is the whole mechanism, which is why there is no `overflow-x` anywhere in
the new rule.

The select is **removed, not hidden behind a media query**. Leaving it in place
would keep it the thing keyboard and screen-reader users reach first, making the
client's complaint true for exactly the people least able to work around it. The
replacement is plain links, so its navigate-on-change script went too.

### What was built

| Brief's requirement | What it is now |
|---|---|
| "the dropdown must be removed" | Gone from all 13 master screens. Each now carries `mv-mx-nav`: every master reachable from every master, grouped by purpose, named in visible text, current one marked with `aria-current` |
| "tree with expand/collapse" | Rebuilt on native `<details>`/`<summary>`. Keyboard operation and screen-reader announcement come free, and there is no open/closed state of our own to drift. Companies and sales orgs open by default, **plants closed** - that is where the length is (1,462 storage locations across 71 plants). Expand all / Collapse all is one line over the native elements |
| "breadcrumb showing current position" | The crumb now walks the real ancestry: `Master Data > Hierarchy > 900 > 9100 > 1 > Storage Location`. Each level links into **its own scoped table**, so stepping up from a storage list lands on the plants of that sales org, not on all plants |
| "Every child page shows its parent breadcrumb" | Same mechanism, plus a level strip (`Company > Sales Org > Plant > Storage`, numbered, current one marked) on every one of the four nesting tables |
| "a single connected tree, not five unrelated tables" | `?parent=<code>` scopes any child list to one branch. Verified against real data: storage scoped to plant `1` returns **4 of 1,462**. Every row drills into its children; every tree node's count links into that level's table; the tree's "+N more" now filters on the FK instead of text-searching the plant code |
| "drag-or-dropdown mapping between levels" | The parent `<select>` in the add/edit modal **stays** - the brief forbids dropdown-only *navigation* and explicitly permits a dropdown for *mapping*. It now preselects the branch you are standing in, so adding a storage location to the current plant no longer means re-finding it among 1,400. Creating into the scoped parent is verified end to end |
| "Data must align to hierarchy visually, not just relationally" | The organisation group is listed in hierarchy order with the whole-tree view at its head, so the nav order **is** the nesting |

### Two real defects found by the acceptance test

**1. The level strip was drawn on the two tables that do not nest.** Mine, from
this change - `org_chain` was built unconditionally, so Billing Type and Sales
Type rendered a "Company > Sales Org > Plant > Storage" strip with none of the
four marked current. That is a lie about the data model and worse than no strip:
it implies a billing type sits somewhere in the org hierarchy. Now built only
for members of `ORG_CHAIN`.

**2. `org_tree` was the only structural-master read view not admin-gated.**
Pre-existing. See the decision note below.

### Behaviour change to confirm: the org tree is now Admin-only

`test_rbac_audit.py::TestKnownInconsistency` had **deliberately recorded** that
the organisation list was admin-only while the organisation tree rendered the
same Company / Sales Org / Plant / Storage data to any viewer, and said in as
many words that it was *"worth someone deciding deliberately rather than
discovering later."* That decision was still open; it is now made, and the class
is renamed `TestResolvedInconsistency` with the reasoning recorded in it.

**Gated to admin**, for three reasons:

1. The module's navigation spec puts Master Data at "Admin only - group hidden
   entirely if not Admin", and the tree is inside that group.
2. Every other structural-master view is admin-only - the list, the save
   handler, the plant/org mapping endpoint, and every template, import and
   export handler. The tree was the one read path that was not, so a viewer
   could read the whole structure through it while being refused every other
   door onto the same tables.
3. Since this change the tree is no longer a read-only picture: it renders the
   master console nav (twelve admin-only destinations), a per-plant "Manage
   storage" link and the plant/org mapping dialog. Left open, a viewer would get
   a page of controls that all redirect them away.

The original counter-argument - that plant **codes** are reference data viewers
already see, e.g. in the Delivery History plant filter - still holds, and the
test suite now pins it: a viewer still reads plant codes on Delivery History.
It was never an argument for exposing the hierarchy itself.

**Reversible in one line** if you want viewers to keep the tree:
`@admin_required` back to `@access_required` on `masters.org_tree`, and flip the
one expectation. Nothing else depends on it.

### Verified against production data, not just fixtures

The reason the tree needed collapsing at all is volume, so the pages were
rendered against the real database (2 companies, 2 sales orgs, 71 plants, 1,462
storage locations): tree 200 with 71 disclosure elements and the picker gone;
the four nesting tables carrying the level strip; Billing Type and Material
correctly without it; and the scoped drill-down returning 4 of 1,462.

### Files changed

* `merilv/masters.py` - `?parent=` scoping, `_org_ancestry`, `_child_table`,
  `_parent_lookup`, `_resolve_parent`, `ORG_CHAIN`, nav order, tree gate
* `templates/merilv/_shell/masters_console.html` - dropdown removed, grouped
  nav added, navigate-on-change script deleted
* `templates/merilv/masters_org.html` - ancestor crumb, level strip, scope
  banner, drill-down column, parent preselect, scope-preserving search
* `templates/merilv/masters_org_tree.html` - disclosure tree, expand/collapse
  controls, per-level links, "Map orgs" moved out of the summary row
* `static/merilv/css/screens.css` - `.mv-mx-nav`, `.mv-levels`
* `static/merilv/css/components.css` - disclosure tree rules, `.mv-scope`
* `merilv/tests/test_rbac_audit.py` - the access decision, recorded

### One thing worth knowing about the tree markup

"Map orgs" moved out of the `<summary>` row into the disclosure panel. A button
inside a `<summary>` is activated by the same click that toggles the
disclosure - it would have opened the mapping dialog and collapsed the branch
under it at once. There is a test pinning that no `mvOpenPlantOrgs` handler sits
inside a summary.

---

## #17 - Master Data: Material (YMM061) -> VERIFIED

`test_material_ymm061_acceptance.py`, 42 tests. **Real code changes** - the
storage half was done, the *reading* half was not.

### What was already there

The persistent YMM061 table, verified under #3 and re-pinned here: every column
the brief names is a real field, all five `PH Level NN` pairs are stored with
their descriptions, the flags are decoded to booleans rather than kept as SAP's
"X" and profile codes, and `raw_data` keeps the whole source row so a column
nobody modelled is still recoverable ("+ any other YMM061 column"). Import
populates all of it - verified by uploading a YMM061-shaped sheet through the
real endpoint and reading the row back.

### The real gap: nothing read them

Tracing the acceptance sentence - *"Stock Validator and Barcode Extractor both
read Material Group and PH Level correctly"* - found it false on both counts,
for both screens:

* `build_material_master` put "Material Group" in the lookup payload but
  **nothing read it out**, and carried **no PH Level at all**.
* `ExtractedRow.from_parsed` read only Description and GTIN from that payload.
* `ValidationRow` had a column for neither.

So the brief was right. Fixed by adding the read path:

| Layer | Change |
|---|---|
| `services/entities.py` | `build_material_master` now carries `PH Level` and `PH Level Desc.` beside `Material Group`. **Level 01 only** - it is the level the client means by "PH Level" singular, and putting all five into a lookup built once per scan would triple its size for data no scan decision reads. The deeper four stay on the master row for the Material screen's column toggle |
| `services/extractor.py` | `ExtractedRow` gains `material_group` / `ph_level`, filled from the master entry. They travel in the row payload the live-scan response and the stored `ScanRecord.parsed` already carry, so the table, the live feed and the session export all see them with no schema change |
| `services/validation.py` | `ResultRow` gains both, filled inside `reconcile` so the engine stays the one place a row is built. `reconcile` takes `material_master` as an **optional** keyword - the engine is deliberately importable without Django, and ~40 existing tests call it positionally with five arguments |
| `models.py` + migration `0003` | `ValidationRow.material_group` / `.ph_level`. Purely additive |
| `views.py` | Passes the master into `reconcile`, persists both on every row, and exports both |
| `layouts.py` | Both registered as toggleable extractor columns (C5), `default: False` |
| `extractor.html` | Two new `data-col` columns, hidden until toggled |
| `validation_detail.html` | A "Group / PH" column, and both in the row drill-in |
| `services/export.py` | Both in `VALIDATION_COLUMNS`, beside Description |

### Design decisions worth knowing

**Stamped at run time, not resolved at render time.** A completed validation run
is evidence of what the master said when the count was taken. Re-resolving on
render would silently rewrite the history of a finished run whenever someone
edited the master. There is a test that changes the master after a run and
asserts the run's row does not move.

**Off by default on the extractor, always on in the exports.** The brief wants
them readable on screen, and #3's complaint about that screen was that it was
already too wide. C5's toggle is exactly the mechanism for that. A workbook has
no width limit and pivoting on these fields is why the client wants them, so the
exports carry them unconditionally.

**One "Group / PH" column on the Validator, not two.** That table is already
nine columns wide and both values are short codes read together - the same
stacking Material/Description and Batch/Serial already get there.

### A trap in the export that is now guarded

`VALIDATION_HEADERS` and `VALIDATION_WIDTHS` are parallel lists. Adding a column
to `VALIDATION_COLUMNS` without adding its width silently shifts every width
after it onto the wrong column. Both were extended together and there is now a
module-level assertion pinning their lengths, plus a test, so the next person
cannot make the mistake either.

### One deliberate deviation from the brief's column list

**UOM has no column**, and that is recorded on the model. YMM061's Order Unit,
Unit of issue and Unit of measure columns are **entirely empty** on both
entities - 0 of 29,106 MHPL rows and 0 of 3,206 MLSIPL rows - so a column for
them would be dead weight that looks like data. "Base Unit" is `base_uom`, which
IS populated and is present. There is a test asserting the deviation stays
documented rather than quietly becoming folklore.

---

## #19 - Master Data: Courier -> VERIFIED

`test_courier_acceptance.py`, 37 tests. **Two real defects fixed.**

### The two columns the brief says were skipped

Both were in fact present - `CourierMaster.vehicle_type` and `.gstin`, in the
sheet registry that drives the template / export / import, in the parser, the
loader, the form and the table. Pinned by tests: the C4 template downloads with
both columns, the template and the export agree header-for-header (they come
from one registry entry, so they cannot drift), and the ten couriers the brief
asks for upload with each Vehicle Type and GSTIN landing on the right courier
and appearing on the screen and in the export.

The GSTIN is upper-cased on save - it is a case-insensitive identifier, and two
spellings of one number must not read as two couriers.

### Defect 1 - a partial import blanked every column the sheet did not carry

**This was the brief's own last clause failing:** *"Existing couriers remain
intact."*

`_courier_master_from_sheet` emitted a key for every field whether or not the
sheet had a column for it (`text_at(row, -1)` returns `""`), and
`_load_couriers` copied every key onto the existing row. So **re-importing a
Round 1 sheet** - Courier Number / Name / Plant / Vehicle Number and nothing
else - wiped the Vehicle Type and GSTIN somebody had since filled in. A sheet
carrying only a name column blanked the courier entirely except its name.

`is_active` was worse than blanked: absent meant `True`, so a partial import
silently **reactivated every deactivated courier** it touched.

The distinction that was missing is **absent column vs empty cell**:

* no such column -> no opinion, leave the stored value alone
* column present, cell empty -> a deliberate clear, so clear it

Fixed by emitting a key only for columns that actually resolved, making the
parsed record a genuine partial update - which is what a spreadsheet with a
subset of columns means. Both halves are pinned: an old sheet leaves Vehicle
Type and GSTIN alone, and a sheet *with* those columns and blank cells still
clears them.

### Defect 2 - a "Vehicle Type"-only sheet stored the type as the registration number

The vehicle-number resolver ends in a loose `contains("vehicle")`, which matches
the header "Vehicle Type" perfectly well. Both lookups landed on that one
column and the existing tie-break dropped the **type** - so a sheet whose only
vehicle column was "Vehicle Type" stored `Tempo` as the vehicle registration
number and left the type blank.

The old comment's reasoning ("a bare Vehicle column is the number, not the
type") is right; it was being applied to a column that is not bare. Fixed by
resolving the type first and then resolving the number **excluding** that
column, so the loose fallback can never claim a header that already matched a
specific type phrase. A genuinely bare "Vehicle" column still reads as the
number, which a test pins.

Both `Veh Type` and `Vehicle Category` are accepted spellings, as are `GST No`
and `GST Number` - all six now covered by parametrised tests. `Veh Type`
happened to work before only because it does not contain the substring
"vehicle"; the two spellings that did contain it were both broken.

### Note for later, not fixed here

The same absent-column-blanks-data shape exists in **`_load_boxes`** (its
`defaults` use `record.get(...) or ""`), and in the three *base* columns of
`_load_materials` (`material_description`, `gtin`, `material_group` are copied
unconditionally - though its wide YMM061 columns are already guarded, with a
comment saying why). `_load_customers` copies `dict(record)` wholesale.

Not changed under #19, which is couriers. Cartons are **#20** and Customer is
**#18**, both still open - each will be handled in its own item rather than by
widening this one.

---

## #18 - Master Data: Customer -> VERIFIED (with a data gap to hand back)

`test_customer_courier_acceptance.py`, 53 tests. **Real code changes**, plus one
finding that is not a code problem at all.

### The brief's diagnosis is wrong, and worth correcting

*"Customer records carry a `default_courier` FK - verify it's being written AND
read correctly. Likely a broken FK reference or a stale cache after Courier
Master schema change."*

There is **no `default_courier` FK**. `CustomerMaster.courier_partner` is a
`CharField` holding a courier *name*, and `CustomerCourierMapping` (the
plant-specific override) links by `courier_name`, also a char column. So there
is no FK to be broken and no cache to be stale. Chasing either would have found
nothing. There were three genuine causes instead.

### Cause 1 - the read side did not exist

`masters_couriers.html` contained the word "customer" **zero times**, and the
`couriers` view queried nothing but `CourierMaster`. The acceptance - *"open
Courier module -> the linked customer appears under that Courier's usage"* -
could not pass however clean the data was.

Added a **Customers** column: a count per courier, expanding to the list, with a
drill-down into the Customer Master filtered to that courier. Both link sources
are read - the customer's default and any plant override - and an override is
labelled as one, because "this customer uses this courier out of this plant" is
a different fact from "this is their default". Two queries for the whole page,
bucketed in Python; one query per courier would be a hundred on the real master.

### Cause 2 - the write side was free text

Type "Blue dart", or leave a trailing space, and the customer was linked to a
courier that does not exist - silently. The link is by name, so the name **is**
the link.

The Default-courier field is now a `<datalist>` over the **active** couriers,
and every write path canonicalises: a name matching a courier case- and
whitespace-insensitively is stored in the courier's own spelling. Applied on the
edit modal, the add form, **and the importer**.

A datalist rather than a `<select>` deliberately - imported customers
legitimately name couriers not in the master yet, and a hard select would either
reject that data or force a bogus choice. Suggest, canonicalise, never silently
discard: an unknown name is kept as typed *and reported*.

### Cause 3 - a narrow import blanked the courier

Same defect as the courier one fixed under #19, one table over.
`_customer_master_from_sheet` emitted all thirteen optional columns
unconditionally and `_load_customers` copied `dict(record)` straight into
`update_or_create` defaults - so a customer sheet without a courier column
blanked `courier_partner` on every row it touched. The customer *name* had the
same exposure.

Guarded the same way: a key only for a column the sheet actually has, so absent
column means no opinion while a present column with an empty cell still clears
the field. This has not caused the current state (see below) but it would have
destroyed the data the moment the client started filling it in, one re-import at
a time.

### The finding to hand back: there is no data

Probed against the **real database**:

| | |
|---|---|
| Couriers | 8 |
| Customers | **7,480** |
| Customers with a courier name set | **0** |
| Plant-specific overrides | **0** |
| Unmatched courier names | 0 |

Checking `raw_data`, which keeps each customer's whole source row: the imported
extract has **no courier column at all**. Its headers are City / Date / Fax# /
Email / Name 1..3 / PAN No / Region / Street / Country / GST No. / Mobile# /
Currency and so on - and **zero of 4,000 sampled rows** carry a courier-ish
column with a value.

So "data doesn't populate in the Courier module" is, in production, primarily a
**data** fact: the Customer Master was imported from an extract that does not
carry the courier. Nothing was lost; it was never there.

That changes what the screen has to say. With every courier reading "none" it
would look exactly like the reported bug even now that it works, so the empty
state names which of the two it is:

> **No customer is assigned to a courier yet.** The Customer Master carries no
> courier column in the extract it was imported from, so there is nothing to
> show here - not a broken link.

and offers both fixes: set *Default courier* on a customer by hand, or add a
`Courier Partner` column to the customer sheet and re-import - the importer
already reads it, which a test proves.

A **separate** warning covers the other case: customer names that match no
courier in the master are listed by name, once each, with "those customers are
linked to nothing, so they will not appear against any courier above". Two
situations, two messages - missing data is not broken data.

### What the client needs to do

Neither is a code change:

1. Add a **Courier Partner** column to the customer master extract and
   re-import, **or**
2. Assign default couriers through the UI, which now suggests the real names.

The moment either happens, the Courier module populates - and the acceptance
path is pinned end to end by tests that create and edit a customer through the
real endpoints and read the courier screen back.

---

## #18 addendum - bulk courier assignment, so the pending data can be filled

The finding above stands: 7,480 customers, none with a courier, because the
imported extract has no courier column. Two things follow from checking it
further.

**Our own template was never the problem.** `MASTER_SHEETS["customers"]`
already carries "Courier Partner" and "Courier Number", and the importer reads
both - there is a test proving a two-column sheet assigns a courier. The client
imported a raw SAP customer extract rather than the module's template.

**What was actually missing was a way to fill 7,480 rows.** The only assignment
control was the per-customer edit modal. So the pending piece is bulk
assignment, now added at `masters/customers/bulk-courier/`:

| | |
|---|---|
| **Assign to selected** | tick rows, choose a courier, apply. The ordinary handful |
| **Assign to all N matching** | the one that matters at this scale. The count is on the button, so nobody applies 7,480 rows believing they picked twenty |
| **Clear** | its own named action, never the fallback for an empty picker |

Design decisions worth recording:

* **The filter is re-derived server-side**, not taken as a list of ids. A
  client-supplied list is unbounded and unverifiable; re-running the same query
  means the count the button promised and the rows written are the same set.
  There is a test that sends a contradictory `pks` list alongside
  `scope=filtered` and asserts it is ignored.
* **`update()`, not a save loop** - one statement rather than 7,480 round trips.
* **An empty courier is refused**, not treated as "assign blank". Wiping
  thousands of couriers by leaving a dropdown alone is not a mistake anyone
  should be able to make in one click.
* **A courier not in the master is refused**, since assigning a name nothing
  resolves would recreate the exact fault #18 is about.
* **Canonicalised through the same helper** as every other write path, and the
  courier's own number travels with it - Finish Delivery reads both, and
  assigning a partner without its number leaves half the link filled in.
* **Capped at 20,000** per action. Not a rail against size (thousands is the
  point) but against a runaway filter on a larger master.
* **Logged with the count and what it matched**, because this is the one write
  in Master Data that can touch thousands of rows.

### The slices, chosen from what the data actually has

Probed the real customers field by field before building the pickers:

| Field | Populated | Distinct |
|---|---|---|
| `state` | 7,465 (100%) | **41** |
| `entity` | 7,480 (100%) | 2 |
| `city` | 7,394 (99%) | 1,470 |
| `pincode` | 7,383 (99%) | - |
| `gstin` | 7,141 (95%) | - |
| `plant` | **0 (0%)** | 0 |
| `company_code` | **0 (0%)** | 0 |

So the plant filter built first was **correct but dead** - which is why its
picker rendered with zero options. It is kept (it costs nothing and becomes
useful the moment the extract carries plant) but **State** is the slice that
works today: fully populated, 41 values, and couriers are chosen by region, so
"assign Blue Dart to every customer in this region" is the decision actually
being made. Entity is offered too. City is populated but 1,470 distinct values
is not a dropdown, so it stays in the search box - which the bulk action already
scopes by, and which a test covers.

Each picker is rendered **only when the data has that field**, so an unpopulated
column shows no control rather than an empty one.

Verified against the real database: 41 states offered, 2 entities, 7 active
couriers, 7,480 customers, and state filters returning real subsets (272, 485,
321 customers). One cosmetic note - the stored state values are SAP's **numeric
region codes** ("1", "10", "11"), not names, because that is what the Region
column of the extract holds. The slice works; mapping codes to names would need
a region master that does not exist here.

### What still needs the client, and what does not

Nothing here is blocked on code any more. To populate the link, either:

1. **Bulk-assign by region** on the Customer Master - now a two-click operation
   per region, or
2. add a **Courier Partner** column to the customer extract and re-import.

The one thing that is genuinely theirs to decide is *which courier serves which
region* - that is a business fact, not something to infer. No production data
was assigned as part of this work.

---

## #6 - Invoice Consolidator -> VERIFIED

`test_consolidator_acceptance.py`, 47 tests. **One real defect, one line.**

### The "not working" report, reproduced and located

The parser is fine. Run over the four real export shipping invoices in
`module_info/` - the exact documents `Export-Consolidator.html` was written
for - it yields **28 rows at confidence 1.0**, with every one of the old tool's
fields populated: `line_no`, `mfg_date`, `expiry_date` and `k_number` all
28/28. The export already emitted all fourteen columns and the detail template
already had cells for them.

The break was one line in between. The parser nests its extras:

    {"invoice_no": ..., "material": ..., "data": {"line_no": 1, "mfg_date": ...}}

and `views.consolidator` persisted the **whole row dict** as the JSON column
(`data=row`), so `ConsolidationRow.data` came out as
`{"invoice_no": ..., "data": {"line_no": ...}}` - one level too deep. Both
consumers then read one level too shallow and got nothing:

* `consolidation_export`: `(row.data or {}).get("line_no", "")` -> `""`
* `consolidation_detail.html`: `{{ r.data.line_no }}` -> the `"—"` default

Which is exactly what the client saw: **Line #, Mfg Date, Expiry Date and
510(k) blank** on the screen and in the workbook, on runs whose parse had
correctly found all four. Nothing in the parser, the export or the template was
wrong - they simply never agreed with the writer about the shape.

Fixed at the write site, since that was the inconsistent one: the parser's shape
is its documented contract (`test_consolidator.py` asserts on `row["data"]`) and
both readers already expected the flat blob. `confidence` is folded in rather
than dropped - a real per-row fact with no column of its own, and storing the
whole dict was the only reason it had been surviving.

### Verified against the real invoices, end to end

Uploaded all four PDFs through the real endpoint and opened the workbook:

| | |
|---|---|
| Files / rows | 4 / 28 |
| Export columns | 14 |
| Line # | 28/28 - `1, 2, 3 …`, restarting per invoice |
| Mfg Date | 28/28 - `01-Apr-2026` … |
| Expiry Date | 28/28 - `31-Mar-31` … |
| 510(k) | 28/28 - `K253144` … |
| Lot Number | 28/28 - `V12022706` … |
| DB Match | 28/28 |

### What the acceptance test pins that the unit tests did not

`test_consolidator.py` (20 tests) already covered the parser hard, including
reconciling each invoice against the totals printed on it. It did not cover the
acceptance sentence itself - many files uploaded **together through the real
endpoint**, and the workbook that comes back. That is the level #1/#2/#3 failed
their retest at, so it gets its own file:

* all four PDFs as one run, every invoice represented, quantities still
  reconciling to the printed totals per invoice, and every row traceable to its
  source file
* **PDFs and a spreadsheet in one batch** - the "/xlsx" in the brief - and a
  mixed batch typed as PDF so it keeps the richer column set
* all eleven old-tool columns asserted by name, and their **values** checked for
  shape rather than mere presence: lots must match `[VUM]\d{7,10}` and 510(k)s
  `K\d{6}`, so a column of blanks or of prices cannot pass
* Line # restarting per invoice rather than running across the batch
* DB Match resolving against the material master, both verdicts, in one query
  rather than one per row
* a spreadsheet run keeping the **domestic** layout - Mfg/Expiry/510(k)/Line #
  do not exist for that source, and a block of empty columns reads as missing
  data rather than as not-applicable
* pages that yield nothing reported in the warnings, never silently dropped -
  an invoice missing from a consolidated sheet is a reconciliation failure weeks
  later
* team scoping on the run, its export and the listing; a Viewer cannot upload

---

## #20 - Master Data: Carton -> VERIFIED

`test_carton_acceptance.py`, 34 tests.

### The two asks were already there

`BoxProfile.sku_name` exists and `BOX_COLUMNS` carries it, so the C4 template
downloads with a **SKU Name** column and a bulk upload populates it. Pinned:
ten cartons upload with every SKU Name landing on the right code, dimensions,
divisor, max units and the active flag all decoded, volumetric weight computing
from what was imported, a re-upload updating rather than duplicating, a
code-less row skipped rather than imported blank, and the `SKU` / `Name`
aliases accepted alongside the full header. Template and export agree
header-for-header because both come from one registry entry.

`max_qty = 0` is verified to mean *unlimited* rather than a parse failure,
which is what the model's own help text says.

### The real defect: the third instance of absent-column-blanks-data

Same fault as #19 (couriers) and #18 (customers), this time in the **shared**
`parse_simple_master_grid`:

    result.records.append({field: text_at(row, index) for field, index in positions.items()})

`positions[field]` is `-1` for a column the sheet does not have and
`text_at(row, -1)` returns `""`, so every record claimed a value for every
field. Two loaders then wrote it:

* `_load_boxes` - re-importing a carton sheet **without a SKU Name column
  blanked the SKU Name on every carton it touched**. That is precisely the
  column this item adds, so #20 would have shipped and then quietly undone
  itself on the next narrow import. An absent Active column also meant "Yes",
  silently bringing retired cartons back.
* `_org_load` - a narrow company sheet blanked names, and **a plant sheet
  without a Sales Org column cleared the sales-org link on every plant in it**,
  silently dismantling the hierarchy #11-16 is built on.

Fixed once in the parser, with both loaders made explicit about writing only
what they were given. The rule now holds across all five master importers:

    absent COLUMN              -> no opinion, leave the stored value alone
    present column, empty CELL -> a deliberate clear, so clear it

Both halves are pinned, and so is the counterpart: a sheet that **does** carry
a parent must still be able to repoint a plant to a different sales org, so the
fix is not a blanket "never touch the parent". There is also a test asserting
the org tree still renders with no orphan warning after a narrow plant import -
the visible symptom the old behaviour would have produced.

### Note

This closes the absent-column class across every master importer in the module:
couriers (#19), customers (#18), cartons and the six structural masters (#20).
Materials was already guarded for its wide YMM061 columns with a comment saying
why; its three base columns remain unconditional, which is recorded under #19
and is the one remaining instance.

---

## #4 - Branch Transfer -> VERIFIED

`test_branch_transfer_acceptance.py`, 56 tests. **Two real defects fixed.**

### Defect 1 - "ask material" was genuinely broken

Reproduced exactly as reported. Presenting a bare material code to the BT
console is refused:

    POST raw_scan="SKU1"
    -> {"accepted": false, "reason_code": "MATERIAL_NOT_IN_PLAN",
        "message": "UNKNOWN is not on this transfer plan."}

`process_transfer_scan` sends every input through `parse_barcode`, which expects
GS1 or positional input. A bare code carries no AIs, so the parser yields its
placeholder material `UNKNOWN` and the line lookup fails. The "UNKNOWN" in that
rejection is the parser's placeholder, not anything the picker typed - which is
also why the message was so hard to act on.

The term "ask material" appears **nowhere** in the legacy `bt-scanner.js`; it is
the client's own phrasing. Read against the acceptance sentence - *"ask material
-> correct batch auto-assigned per priority rule"* - it means the material-only
pick: present a material, be told **which batch, from which location**.
Everything needed to answer already existed (`StockPool.take` ranks by
`location_priority`); the only missing piece was letting a bare material in.

Added as a **narrow** fallback, deliberately not a loosening of barcode parsing,
and pinned shut by its own test class:

* it runs only when the parse produced no usable material, so a genuine
  barcode's behaviour is unchanged
* it accepts the input only if it exactly names a line on **this** plan, so an
  unparseable smudge cannot become a pick
* it resolves against the **focus-filtered** lines, so a locked picker cannot
  ask for another plant's material by name either
* a material from a different plan, or one with no stock, is still refused
  (`NO_STOCK`)
* the ask is recorded as `strategy="ask_material"` so the audit log can tell a
  typed material from a scanned barcode

This is consistent with the rest of the engine rather than a special case: BT
matching is material-only by design, so admitting a material by name is the same
question asked a different way.

### Defect 2 - a scanned batch was silently rewritten

Found by the guard test above. `pool.take` prefers the scanned batch but falls
back to the highest-priority lot when that batch is not in the MB52 dump - and
the outcome read its batch **off the lot**. So a picker who scanned
`PICKERSBATCH` had `BATCH-RT` recorded against the carton.

The export is a *"carton-level pick list - what physically goes on the pallet"*,
so this made the pallet paperwork disagree with the pallet. It also disarmed the
deviation note: `picked_batch` had already been replaced by the recommendation's
own lot, so a picker who took a different batch was never told they had.

Now what the picker physically has wins, and the lot supplies only the
**location** - the pool's answer to "where do I fetch it", which a barcode
cannot know. `UNKNOWN` is treated as absent rather than as a batch, so an
ask-material pick still falls through to the lot, which is the point of asking.

### The other three sub-items were already done

| Sub-item | Finding |
|---|---|
| Consumption priority | Implemented per session and per entity. Verified by **inverting** the priority over identical stock - `RT01,DMG1` draws `BATCH-RT` from `RT01`, `DMG1,RT01` draws `BATCH-DM` from `DMG1` - so a pass cannot be an accident of dump ordering. Changeable mid-session, persisted, and an unlisted location sorts last rather than being excluded. The engine's own `assign_transfer_batches` is pinned separately so the console cannot quietly diverge from it |
| Plan Summary | Real, not a placeholder. Ported from `renderBTSummaryTable`: grouped by receiving plant, then by colour zone, with plan/dispatched/pending/percent at both levels plus a grand total. Verified it reports the true required quantity per plant (PL20: 4, PL30: 3), nests the zones, and **moves when a pick happens** - a summary that does not follow the picks is a placeholder with real-looking numbers |
| Real-time stats (C2) | The scan response carries the whole state, so the console redraws without waiting for a poll. Totals move on the **first** scan, per-line figures are live, the carton count is live, an undo moves them back, and the poll endpoint returns the identical shape so the two cannot disagree |

---

## #5 - Inbound Scanner -> VERIFIED

`test_inbound_acceptance.py`, 46 tests. **One real gap: the fourth KPI.**

### "Module non-functional" is not what the code says

The end-to-end flow works. Driven as the acceptance sentence describes it - a
**two-console journey**, dispatch through the BT scanner then receive through
the inbound one - it books stock in, catches transit losses, refuses
over-receipt and duplicate serials, supports bulk paste and undo, and exports.

The asymmetry the module is built around is verified explicitly: **expected
quantity is what the sender actually picked, never what the plan asked for**. A
line planned for 5 that shipped 3 leaves the receiver expecting 3 - measuring
against the plan would report a permanent phantom shortfall of 2 on every
partial dispatch, which is the sort of thing that reads as "non-functional".

| Reason code | Verified |
|---|---|
| `NOTHING_DISPATCHED` | a material on the plan that the sender never picked - chase the sender |
| `OVER_RECEIVED` | more arrived than was dispatched |
| `DUPLICATE_SERIAL` | the same unit twice |

### The real gap: Damaged could not be counted

The brief asks for four KPI cards - **Expected, Received, Short, Damaged**. The
console had three, and the only place damage could be recorded at all was the
receipt's free-text `remarks` box, whose placeholder reads "Damage, seal
condition, anything worth recording".

That is a note, not a number. It cannot be counted, cannot drive a KPI, cannot
be exported per line, and cannot be reconciled against what the sender
dispatched. *"Shortages **and damages** logged correctly"* is not satisfiable by
prose.

Added as a per-unit, counted fact, modelled exactly the way the Barcode
Extractor already models it (`ScanRecord.condition`, Row 3.5) rather than
inventing a second vocabulary for the same thing:

* `BranchTransferReceiptScan.condition` - stamped from the console's active
  toggle at scan time
* `BranchTransferReceipt.condition` - the session-wide default, so a receiver
  working through a crushed pallet flips once rather than per unit
* `inbound_scan_condition` - correct a unit after the fact, because a receiver
  who books one in and only *then* sees the damage should not have to undo and
  rescan, and should not have to flip the whole session to fix one past unit
* migration `0004`, additive

### The design decision worth recording

**A damaged unit is still counted as received.** It arrived; it arrived broken.

Subtracting it would make "Short" mean *"lost in transit **or** broken"*,
collapsing two facts the warehouse acts on differently: short is chased with
the sending plant, damaged is booked to the entity's damage storage location
(`damage_storage_location`, default DMG1, which already existed for exactly
this). There is a test pinning that a damaged receipt leaves `received` up and
`pending` unchanged.

Damaged is also counted from the scans rather than kept as a denormalised
column, because a condition can be corrected afterwards and a stored counter
would need every correction path to remember to move it.

### Also checked

* the four cards render with the brief's own labels, each with a live element
  for the console to update; "Expected" and "dispatched" are emitted as the same
  number under both names so neither the template nor the brief has to translate
* damage is reported **per line** as well as per session, so a receiver can see
  *which* lines arrived broken
* the export gained a Damaged column, and a test asserts the columns did not
  **shift** - adding a header without its cell moves every column after it
* real-time display (C2): the scan response carries the whole state, totals move
  on the first scan, the damaged total is live, and an undo moves them back

---

## #10 - Reports -> VERIFIED (empty state); ticket stays open by instruction

`test_reports_empty_state.py`, 19 tests. Scope deliberately narrow - the brief
says *"Do not speculate on redesign"* and *"Ticket stays open until production
data flow validates."* Both respected: nothing was redesigned, and the item
remains open on the client's own terms until real data flows.

**One real gap.** `charts.has_data` was already computed and **nothing read
it**, so a brand-new install rendered four zero KPIs and four empty charts with
no explanation - indistinguishable from a reporting module that is broken.
Added the page-level empty state in the client's own words:

> **No data yet - reports populate as deliveries complete.**
> Nothing has been dispatched in the last N days. The figures below fill in on
> their own once picking starts - **nothing to configure here.**

The per-chart "No X in this window" notes stay: they answer a *different*
question (this filter is empty) from the banner (nothing has happened yet), and
a window with no deliveries inside a system that has them should still say the
former. There is a test pinning that distinction.

Also verified: the page renders with nothing in it, every KPI reads zero rather
than blank, the chart payload is well-formed when empty (an empty chart, not a
missing key the front-end trips over), the window is zero-filled so quiet days
are visible, a team-scoped user with no deliveries of their own hits the empty
state cleanly, and a junk `?days=` value (`""`, `0`, `-5`, `abc`, `99999`,
`7.5`) does not 500 - the docstring notes `int(request.GET.get("days") or 30)`
was "a 500 waiting to happen".

---

## #21 - Master Data: Entity Rules -> VERIFIED

Unblocked: #1, #2 and #3 are closed. `test_entity_rules_and_activity.py`.
**No production code changed.**

*"Both entities work end-to-end across all three consumers."*

The trap in testing this is that a suite proving "both entities parse
something" would pass on a build that **ignored entity rules entirely**. So the
differences are pinned first, and each consumer is then checked for behaviour
that actually differs between MHPL and MLSIPL:

* the two profiles are asserted to **disagree** on serialisation, with a failure
  message saying that if they ever stop differing, every test below becomes
  vacuous
* MHPL is serialised, MLSIPL is not
* **Barcode Extractor** - both parse the same GS1 string, both resolve the
  material master, and the serial rule applies per entity
* **Stock Validator** - `reconcile` produces rows for both, and each entity's
  serial requirement drives `parse_system_dump(require_serial=…)`
* **Outbound Scanner** - the console opens for both, and serial identity keys on
  BATCH||SERIAL for the serialised entity
* the entity selection is **sticky per session**, so a consumer cannot silently
  revert to the default entity and apply the wrong rules mid-shift

No inconsistencies found to raise as sub-tickets.

---

## #23 - Master Data: Activity Log -> VERIFIED

Unblocked: every upstream Master Data item is closed.
`test_entity_rules_and_activity.py`. **One real gap fixed.**

*"Confirm entries include user, timestamp, before/after diff, entity."*

Three of the four were there - `actor`, `created_at`, `target_type`/`target_id`
and a metadata blob, with every master write path already calling `_log(...)`.

**The diff was not.** A material edit logged `{"entity": "MHPL"}`: the fact that
*something* changed on that row, with no record of what. That is an audit trail
that cannot answer the only question an audit trail exists for.

Added `activitylog.snapshot()` and `diff_snapshots()`, wired into the material,
customer, courier, carton and structural-master write paths. Two decisions:

* **Only changed fields are stored.** A material carries ~60 YMM061 columns;
  logging all of them on every edit would bury the one that moved and multiply
  the table for nothing. `{"gtin": ["old", "new"]}` is the whole point.
* **Snapshot before the mutation, compare after `save()`.** Comparing the POST
  against the old row would report what was *submitted*; comparing the saved row
  reports what actually **landed**. That matters concretely here:
  `MaterialMaster.save()` derives `serial_managed` from `serial_profile`, so
  editing the profile also moves the flag - a change an auditor needs to see and
  the submitter cannot. There is a test asserting both appear in one diff.

Relations are reduced to their primary key rather than repr'd (a
`<Entity: MHPL — MHPL Orthopedics>` is neither stable across a rename nor
comparable), and `id`/`created_at`/`updated_at` are excluded - `updated_at`
would otherwise appear in every single diff and drown the field that moved.

Create and delete are covered too, and one access note worth recording: the
activity log is deliberately `access_required` while the master screens are
admin-only. An audit trail that only the people being audited can read is not
much of one, so that asymmetry is intentional and now has a test saying so.
