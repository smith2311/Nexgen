# Meril-V — Manual Testing Guidebook

**Version:** 1.0 · 2026-08-27
**Module:** Meril-V (Warehouse Validation & Outbound Operations) · `/meril-v/`

---

## How to use this book

Work the phases **in order**. The numbering is a real dependency chain, not
decoration:

- Phase 0 (Masters) gates everything — a wrong scan in Phase 1 is usually a
  missing material in Phase 0.
- Phase 4 cannot start until a Branch Transfer plan exists, and its three parts
  (4a → 4b → 4c) must run in sequence in one sitting, because each consumes what
  the previous one produced.

Skipping ahead produces empty screens you will mistake for bugs.

Each phase lists **preconditions**, numbered **steps**, and a **Watch for**
column naming the specific failure that step is designed to catch. Where a
subsystem has never run on this database, it says so.

### Before you start

1. **Log in as a superuser** for Phases 0–7. Phase 8 needs separate accounts.
2. **Open DevTools → Console and Network**, and keep them open. Most Meril-V bugs
   announce themselves there first.
3. Console should be **clean on load**. If you see four
   `/static/merilv/audio/*.mp3` 404s, you are on a build from before
   2026-08-27 — see `docs/MERILV_BUGFIX_EXPORT_AND_AUDIO.md`.

### What has already been verified automatically — don't re-test

| Area | Coverage |
|---|---|
| Unit tests | 681 passing |
| Screens render | 33/33 return HTTP 200 |
| Layout | No horizontal overflow at 390 / 768 / 1024 px |
| Touch targets | ≥ 42 px on touch devices |
| Templates | No leaked `{% %}` / `{{ }}` tags on any screen |
| Exports | All 14 endpoints return a valid, non-empty file |
| Export performance | Materials 32,299 rows in ~10s; Customers in ~1.4s |

Everything in this book is deliberately **outside** that coverage.

### Two behaviours that look like bugs and are not

- `/meril-v/zva05/` and `/meril-v/bt-scan/` **redirect to `/meril-v/outbound/`**.
  Both became *modes* of the Outbound Scanner. Reach their consoles from that
  mode's session list.
- `/meril-v/deliveries/packing-list/` **downloads a PDF** rather than rendering a
  page.

---

## Data state at time of writing

Knowing what is empty tells you which phases are first-run.

| Has data | | Empty — never executed |
|---|---|---|
| MaterialMaster | 32,299 | BtScanSession, BtScanBox, BtScan |
| CustomerMaster | 7,480 | Zva05Dump, Zva05Line, Zva05Scan |
| FinishedDelivery | 41 | ScanSession, ScanRecord |
| ActivityLog | 63 | BranchTransferReceipt (+ scans) |
| ConsolidationRow | 34 | SavedLayout |
| BoxProfile | 9 | PickerManager, UserPlantMapping |
| CourierMaster | 8 | |
| Entity | 5 | |
| ConsolidationRun | 5 | |
| OutboundSession | 3 | |
| DeliveryDump | 3 | |
| BranchTransferPlan | 2 | |
| ValidationRun | 1 | |

**Four subsystems have zero rows.** Their empty states render correctly, but
their actual logic has never run here: **BT Scanning, ZVA05, Extractor sessions,
Inbound receipts** (Phases 3, 4b, 4c, 5).

---

## Phase 0 — Master data

**Precondition:** none. Do this first.
**Why:** every later phase resolves materials, customers, couriers and entities
out of these tables.

| # | Step | Watch for |
|---|---|---|
| 0.1 | Open `/meril-v/masters/`. Check every tile's count against its table. | Materials should read 32,299, Customers 7,480. A zero means entity scoping dropped your rows. |
| 0.2 | Materials list: search by material code, then by EAN, then by product hierarchy. | The `group` and `ph` filters. One automated test here fails intermittently on a substring collision — if you see a wrong row, capture the search term. |
| 0.3 | Add one material by hand. Confirm it appears in the list, then edit it. | Required-field validation; whether the edit persists after reload. |
| 0.4 | Download the Material template, add 3 rows, re-import. | Whether a narrow import **blanks the YMM061 columns** on rows it didn't touch. |
| 0.5 | Import a deliberately broken file: wrong headers; empty file; `.txt` renamed to `.xlsx`; a row with a non-numeric quantity. | Must reject with a readable message. A 500 or a silent partial import is a bug. |
| 0.6 | Couriers, Boxes, Customers, Entities — create / edit / delete one of each. | Deleting something referenced by a delivery must be **refused** (PROTECT), not cascade. |
| 0.7 | Organisation tree, and plant → org mapping. Save a change and reload. | Tree renders; mapping survives reload. |
| 0.8 | Export Couriers, Boxes, **and Materials**. | Materials should complete in ~10s. If it hangs, the export fix is not deployed. |
| 0.9 | Masters → Activity log, then export it. | Your Phase 0 edits should all be listed with the right user. |

---

## Phase 1 — Outbound Scanner

**Precondition:** Phase 0 complete.
**Status:** best-covered area — 3 sessions and 41 deliveries exist. **Start here
to calibrate what "working" looks like** before moving to the empty subsystems.

| # | Step | Watch for |
|---|---|---|
| 1.1 | Upload a delivery dump. | Session created with the correct line count. |
| 1.2 | Scan a valid serial. | Row turns green, count increments, **a sound plays**. Sound is a synthesised tone, not an mp3 — that is correct. Silence after your first click/keypress is a real bug. |
| 1.3 | Scan the same serial again. | Rejected as duplicate, with a *different* sound from 1.2. |
| 1.4 | Scan a serial belonging to a different delivery. | Rejected with a clear reason. |
| 1.5 | Scan an expired / blocked-stock serial. | Correct stock condition shown, not a generic reject. |
| 1.6 | Undo the last scan. | Both the count *and* the row state revert. |
| 1.7 | Switch all four views: card / list / table / kanban. Reload. | View choice persists (localStorage). |
| 1.8 | Table view: tick several deliveries, wait ~10s, then bulk-finish. | **Selection must survive a poll tick** — state refreshes every few seconds and rebuilds rows. |
| 1.9 | Assign a box profile and a courier to a delivery. | Box dimensions carried into the export. |
| 1.10 | Finish a **partial** delivery. | Marked Partial; discrepancy export lists exactly the missing lines. |
| 1.11 | Export the session. | Filename is `Serial Format - <id>.xlsx`; opens in Excel; column order matches what the SAP team expects. |
| 1.12 | Open the same session in two browser tabs and scan in both. | **Untested by automation.** Look for a lost scan, a double count, or one tab overwriting the other. |
| 1.13 | Mid-session, disconnect Wi-Fi, scan, reconnect. | Should recover without losing the session or silently dropping the scan. |

---

## Phase 2 — Stock Validator

**Precondition:** Phase 0 complete. 1 existing run.

| # | Step | Watch for |
|---|---|---|
| 2.1 | Upload a physical scan file + an SAP stock dump. | Run completes and reports totals. |
| 2.2 | Seed **one of each case deliberately**: matched, missing, extra, batch mismatch. | Each is counted in the right bucket. This is the module's entire job. |
| 2.3 | Validation detail: filter and sort each status group. | Counts in the filter chips agree with the rows shown. |
| 2.4 | Export the run. | Opens cleanly; status column present. |
| 2.5 | Re-run with an empty file, then with a ~10k-row file. | Empty file rejected gracefully; large run completes and exports. |

---

## Phase 3 — Barcode Extractor

**Precondition:** Phase 0 complete.
**⚠ First run:** `ScanSession` and `ScanRecord` are **empty**. This logic has
never executed on this database.

| # | Step | Watch for |
|---|---|---|
| 3.1 | Paste raw barcodes into the parser. | GTIN, batch, expiry and serial each extracted into the right field. |
| 3.2 | Feed a GS1-128 barcode with FNC1 separators. | Application identifiers parsed, not treated as literal text. |
| 3.3 | Feed deliberate junk: a truncated code, a code with the wrong check digit. | Rejected with a reason, not silently accepted. |
| 3.4 | Create a scan session, scan several codes. | Records accumulate; running count correct. |
| 3.5 | Set a per-record condition (damaged / expired / OK). | Persists after reload. |
| 3.6 | Choose visible columns, save the layout, reload, then switch layouts. | **`SavedLayout` is also empty** — this is the first real exercise of the layout feature. |
| 3.7 | Finish the session, export, confirm it appears in history. | History export matches the session export. |

---

## Phase 4 — Branch Transfer → BT Scanning → Inbound

**Precondition:** Phase 0 complete.
**⚠ Strict chain — run 4a, 4b, 4c in sequence, in one sitting.** BT Scanning
consumes a Branch Transfer plan; Inbound receives what BT Scanning dispatched.
2 plans exist, but **0 scan sessions and 0 receipts** — links 2 and 3 have never
run.

### 4a — Branch Transfer (plan setup)

| # | Step | Watch for |
|---|---|---|
| 4a.1 | Upload a branch-transfer plan. | Lines parsed; receiving plant and colour zones correct. |
| 4a.2 | Run auto-assign. | Lines distributed sensibly across cartons/zones. |
| 4a.3 | Export the plan. | Matches what you uploaded. |

### 4b — BT Scanning (the floor console) — **first run ever**

| # | Step | Watch for |
|---|---|---|
| 4b.1 | Open a BT Scanning session against the 4a plan. | Session is created at all — this path has never executed. |
| 4b.2 | Create a carton, set its zone, scan items into it. | Items land in the intended carton. |
| 4b.3 | Scan an item **not** on the plan. | Rejected. |
| 4b.4 | Over-scan a line beyond its planned quantity. | Rejected or flagged — not silently accepted. |
| 4b.5 | Set line priority; change focus; undo a scan. | Each takes effect immediately. |
| 4b.6 | Finish the session. | Plan summary shows plan / dispatched / pending per plant and zone. **Compare against the legacy system if you still have it** — this table was ported from `bt-scanner.js`. |
| 4b.7 | Export. | Carton contents and zone totals correct. |

### 4c — Inbound Scanner (receiving) — **first run ever**

| # | Step | Watch for |
|---|---|---|
| 4c.1 | Open Inbound against the 4b dispatch. | The dispatch is visible to the receiving side. |
| 4c.2 | Scan cartons in. | Received counts climb correctly. |
| 4c.3 | **Under-receive deliberately** — leave one item unscanned. | Transit loss flagged with the exact shortfall. |
| 4c.4 | Scan an item that was never dispatched. | Rejected. |
| 4c.5 | Finish and export the receipt. | Discrepancies preserved in the file. |

---

## Phase 5 — ZVA05 auto-assign

**Precondition:** Phase 0 complete.
**⚠ First run:** `Zva05Dump` is empty.
**Note:** `/meril-v/zva05/` redirects to `/meril-v/outbound/` — that is intended.
Reach the console from that mode's dump list.

| # | Step | Watch for |
|---|---|---|
| 5.1 | Upload a ZVA05 dump. | Lines parsed with correct quantities. |
| 5.2 | Scan a serial. | Auto-assigns to the correct line without being told. |
| 5.3 | Scan a serial matching two candidate lines. | Deterministic, explainable choice. |
| 5.4 | Undo; change focus; finish. | Each behaves as in Phase 1. |
| 5.5 | Export and reconcile against the source dump. | Every assigned serial accounted for. |

---

## Phase 6 — Invoice Consolidator

**Precondition:** none.
**⚠ Recently changed:** the multi-file basket is new — it accumulates across
separate picks instead of the browser replacing the selection each time. All 5
existing runs show `Files = 1`, so multi-file has never been used in anger.

| # | Step | Watch for |
|---|---|---|
| 6.1 | Pick 3 PDFs, then pick 2 more from a **different folder**. | All 5 stay listed. Before the fix, the first 3 vanished silently. |
| 6.2 | Pick the same file twice. | Deduplicated, not listed twice. |
| 6.3 | Add a `.txt`. | Shown in red as rejected, and excluded from the upload. |
| 6.4 | Remove one file with its ×. | Count drops by one; the rest survive. |
| 6.5 | Drag files onto the dropzone instead of browsing. | Same result as browsing. |
| 6.6 | Switch PDF ↔ Excel tab mid-selection. | Basket clears; no stale files posted from the other tab. |
| 6.7 | Process 10+ real invoices, mixing **export (USD)** and **domestic (GST)** layouts in one run. | Rows attributed to the correct source file; both layouts recognised. |
| 6.8 | Include one corrupt / password-protected PDF. | Reported per file; the rest of the run still completes. |
| 6.9 | Export the consolidated run. | Row count equals the sum of the inputs. |

---

## Phase 7 — Reports, Monitoring, Delivery History

**Precondition:** Phases 1–6 have generated activity.
**⚠ Recently changed:** Reports moved from CSS bars to real Highcharts, and the
daily trend is now zero-filled so quiet days appear as gaps.

| # | Step | Watch for |
|---|---|---|
| 7.1 | Reports: switch 7 / 30 / 90 days. | All four charts redraw; no console errors. |
| 7.2 | Inspect the daily trend. | Quiet days render as **gaps**, not a compressed line. Charts load from the vendored Highcharts — turn off internet and they must still render. |
| 7.3 | Reports → User Activity, then drill into one user. | Per-user totals agree with the roster row. |
| 7.4 | Live Monitoring: open a scan session in another tab and scan. | Board updates without a manual refresh. |
| 7.5 | Leave Monitoring open 30+ minutes. | Poller does not leak memory or stop. |
| 7.6 | Delivery History: filter by date, entity, courier. Open a detail. | Filters combine correctly. |
| 7.7 | Update a courier on a finished delivery. | Persists; appears in the activity log. |
| 7.8 | **Print the packing-list PDF on paper** and check against the physical delivery. | Automation confirms a valid 138 KB PDF only — correctness on paper is unverified. |
| 7.9 | Delivery export and invoice export. | Open cleanly in Excel. |

---

## Phase 8 — Roles & permissions

**Precondition:** Phase 0 complete.
**⚠ Highest untested risk after the export bug.** All automated testing ran as a
superuser. Row-level scoping — what stops a picker reading a colleague's
throughput or another plant's stock — has **never been exercised by hand**.

**Use real, separate accounts.** Toggling your own groups does not exercise the
same paths.

| # | Step | Watch for |
|---|---|---|
| 8.1 | Create one Viewer, one Picker, one Manager, one Admin. | Role assignment sticks. |
| 8.2 | As **Viewer**: open history and reports. Then try to scan, and try Masters. | Read works; both writes blocked. |
| 8.3 | As **Picker**: scan a session. Then type `/meril-v/masters/` and `/meril-v/reports/users/` **directly into the address bar**. | Must be refused. A hidden menu link is not a permission. |
| 8.4 | As **Manager**: open Reports → User Activity. | Sees own team only — not the whole company. |
| 8.5 | Map a user to Plant A only. Try to open a Plant B session. | Refused. |
| 8.6 | As Picker, try to open another picker's session by URL. | Refused. |
| 8.7 | Assign, change, then remove a role. | Access changes take effect immediately, without a re-login. |
| 8.8 | Bulk-import users from the roles template, including one invalid row. | Valid rows imported; invalid row reported, not silently skipped. |

---

## Phase 9 — Hardware & the warehouse floor

**Precondition:** everything else passing.
**⚠ Cannot be simulated.** Layout and touch targets are already verified; this
phase needs the real devices.

| # | Step | Watch for |
|---|---|---|
| 9.1 | A real barcode gun on the outbound screen, for a full shift. | No dropped or doubled scans. |
| 9.2 | Gun in continuous mode — rapid consecutive scans. | Nothing lost; UI keeps up. |
| 9.3 | Check the gun's prefix/suffix and Enter handling. | Enter must not submit a form early or navigate away. |
| 9.4 | The actual warehouse tablet, in its actual browser and version. | Layout and scan focus behaviour. |
| 9.5 | Sound audible over floor noise; mute toggle works and persists. | Tone volume is fixed in code — if it is too quiet on real hardware, that is a finding. |
| 9.6 | Drop Wi-Fi mid-scan, then reconnect. | Session recovers; no lost scans. |
| 9.7 | Leave a scan session open for a full shift. | Poller does not leak or die. |
| 9.8 | Cold device, gloved hands, poor lighting. | Touch targets and contrast usable in situ. |

---

## Filing a bug

Capture these every time. The first three matter most — most Meril-V bugs turn
out to be role-scoped or data-scoped, and without them the report is not
reproducible.

1. **URL** — full path including query string.
2. **Role** — which account, and which Meril-V role it holds.
3. **Entity / plant** — scoping is the most common cause of "my rows vanished".
4. **Steps** — numbered, starting from a fresh page load.
5. **Expected vs actual** — one line each.
6. **Console** — DevTools → Console; copy any red lines.
7. **Network** — for a hang or a failed save, the failing request's status and
   response body.
8. **The file** — for any upload bug, attach the exact file. Column drift is
   invisible in a description.

### Triage shortcuts

Before filing, rule these out:

| Symptom | Likely cause |
|---|---|
| Rows missing from a master list | Entity/plant scoping, not data loss — check the entity selector |
| "Page froze" on a Masters export | Pre-2026-08-27 build; the export fix is not deployed |
| Four `audio/*.mp3` 404s in console | Pre-2026-08-27 build; harmless, sound still works via tones |
| `/zva05/` or `/bt-scan/` "redirects wrongly" | Intended — both are Outbound Scanner modes |
| Empty BT Scanning / ZVA05 / Extractor screen | No data has ever been created — you are the first run |
| Access denied where you expected access | Check the role *and* the plant mapping, in that order |

---

## Coverage summary

| Phase | Area | Data state | Risk |
|---|---|---|---|
| 0 | Master data | Populated | Medium — gates everything |
| 1 | Outbound Scanner | Populated | Low — best covered |
| 2 | Stock Validator | 1 run | Medium |
| 3 | Barcode Extractor | **Empty** | High — first run |
| 4a | Branch Transfer | 2 plans | Medium |
| 4b | BT Scanning | **Empty** | **High — first run** |
| 4c | Inbound | **Empty** | **High — first run** |
| 5 | ZVA05 | **Empty** | High — first run |
| 6 | Invoice Consolidator | 5 runs | Medium — recently changed |
| 7 | Reports / Monitoring | Populated | Low — recently changed |
| 8 | Roles & permissions | Partial | **Highest — never hand-tested** |
| 9 | Hardware | n/a | High — cannot be simulated |

**Suggested order if time is short:** Phase 8 (roles) → Phase 4 (the BT chain) →
Phase 1 (outbound, on real hardware). Those carry the most risk per hour spent.
