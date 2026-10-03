# Meril-V — two bug fixes: master export hang, scanner audio 404s

**Date:** 2026-08-27
**Scope:** `merilv/services/export.py`, `static/merilv/js/scanner.js`,
`templates/merilv/outbound_session.html`, `merilv/tests/test_export_performance.py`
**Tests:** 681 existing Meril-V tests pass, +5 new regression tests

Found during a full automated pass over Meril-V (681 unit tests, 33 screens,
14 export endpoints, 3 viewports). Both bugs are fixed; both are covered against
regression.

---

## Bug 1 — Master-data export never finished (critical)

### Symptom

`Masters → Material → Export` (`/meril-v/masters/sheet/materials/export/`) hung
indefinitely. The request occupied a Daphne worker for the whole time, so it was
not just a slow download — it consumed a worker until the client or gateway gave
up.

### Measurements (before)

| Export | Rows | Cells | Time | Verdict |
|---|---:|---:|---:|---|
| Couriers | 8 | 56 | 0.0s | OK |
| Boxes | 9 | 72 | 0.0s | OK |
| Customers | 7,480 | 127,160 | **91.9s** | Unusable |
| Materials | 32,299 | 1,162,764 | **> 9 min** | Never finished |

The important part is not that it was slow — it is that throughput **fell** as
the sheet grew, which is the signature of a super-linear loop:

| Rows | Cells | Seconds | Cells/sec |
|---:|---:|---:|---:|
| 250 | 9,000 | 0.6 | 14,021 |
| 1,000 | 36,000 | 4.6 | 7,777 |
| 2,500 | 90,000 | 18.5 | 4,864 |
| 5,000 | 180,000 | 73.6 | 2,446 |
| 10,000 | 360,000 | 361.2 | **996** |

Doubling the rows roughly **quadrupled** the time. Extrapolated to 32,299 rows
that is about an hour.

### Root cause

Two separate costs in `_write_sheet()` (`merilv/services/export.py`), both in the
per-row loop:

```python
for row in spec.rows:
    values = [_cell_value(value) for value in row]
    worksheet.append(values)
    cells = worksheet[worksheet.max_row]      # (1) O(rows) — every iteration
    for cell, original in zip(cells, row):
        cell.border = style.border            # (2) a style write per cell
        if isinstance(original, datetime):
            cell.number_format = "DD-MM-YYYY HH:MM"
        elif isinstance(original, date):
            cell.number_format = "DD-MM-YYYY"
```

1. **`worksheet[worksheet.max_row]`** reads back the row that was just appended.
   That indexing walks the sheet, so it is O(rows) per row and **O(rows²)
   overall**. This is what made throughput collapse.
2. **A `Border` assigned to every cell.** openpyxl charges per style write, so
   1.16M assignments is expensive even with O(1) access.

What it was **not**: N+1 queries. `select_related("entity")` was already in
place, and building the Python row list accounted for only 6.1s of the total.

### Fix

```python
row_index = 1 if headers else 0
for row in rows:
    row_index += 1
    worksheet.append([_cell_value(value) for value in row])

    if draw_grid:
        for column in range(1, column_count + 1):
            worksheet.cell(row=row_index, column=column).border = style.border

    for column, original in enumerate(row, start=1):
        if isinstance(original, datetime):
            worksheet.cell(row=row_index, column=column).number_format = "DD-MM-YYYY HH:MM"
        elif isinstance(original, date):
            worksheet.cell(row=row_index, column=column).number_format = "DD-MM-YYYY"
```

- **Explicit row counter** replaces the sheet re-read. `worksheet.cell()` by
  coordinate is O(1), so the loop is linear.
- **Nothing is touched cell-by-cell unless it needs it.** A cell only gets a
  `worksheet.cell()` lookup if it carries a date requiring a number format. A
  master sheet of plain strings now costs one `isinstance` check per value and no
  style traffic at all.
- **New budget `MAX_GRID_CELLS = 120_000`.** Above it the cosmetic per-cell
  border grid is skipped.

### The one deliberate trade-off

Sheets above 120,000 body cells lose the per-cell border grid. Header styling,
frozen header row, column widths, date formats and all data are unchanged.

The reasoning: the grid is cosmetic, and on a 32,299-row master it costs more
than the whole rest of the export. Every sheet a human actually reads by eye — a
delivery note, a validation run, a BT receipt, a courier list — is far below the
budget and still gets the full grid. The two sheets that lose it (Materials,
Customers) are bulk data pulls destined for a filter or a VLOOKUP, not for
reading row by row.

If the grid is ever wanted on the big masters, the next step is openpyxl's
`write_only` mode, which can style cheaply at that scale but requires a
different construction API.

### Measurements (after)

| Export | Rows | Cells | Before | After | Speed-up |
|---|---:|---:|---:|---:|---:|
| Couriers | 8 | 56 | 0.0s | 0.01s | — |
| Boxes | 9 | 72 | 0.0s | 0.01s | — |
| Customers | 7,480 | 127,160 | 91.9s | **1.37s** | **67×** |
| Materials | 32,299 | 1,162,764 | > 9 min | **10.36s** | **> 52×** |

### Correctness verified

Confirmed by reading the generated workbooks back with openpyxl:

- Small sheet: header bold + `#0076C8` fill, `freeze_panes == "A2"`, body cells
  keep a `thin` border.
- Date formats: `DD-MM-YYYY HH:MM` and `DD-MM-YYYY` both still applied.
- Value coercion unchanged: `True → "Yes"`, `None → ""`, `int` stays `int`,
  `Decimal → float`, tz-aware datetimes stripped.
- 60,000-row sheet: 60,001 rows written, first and last rows byte-identical to
  input, header still styled, body grid correctly absent.
- Ragged rows (short and empty) do not raise.

---

## Bug 2 — Four scanner-audio 404s on every scan screen (minor)

### Symptom

Every load of `/meril-v/outbound/<pk>/` fired four 404s:

```
404  /static/merilv/audio/ok.mp3
404  /static/merilv/audio/danger.mp3
404  /static/merilv/audio/warn.mp3
404  /static/merilv/audio/neutral.mp3
```

`static/merilv/audio/` does not exist and never has.

### Impact

**No functional impact.** `scanner.js` catches the load failure per family and
falls back to a WebAudio tone, which is deliberate and was documented in the
source. A distinct sound per outcome (PRD FR7) still played.

The real cost was diagnostic: four red console lines on every reload of every
scanner screen, sitting exactly where a developer looks for real errors — and
scanner screens are where most manual debugging happens.

### Fix

The mp3 path is now opt-in rather than attempted-and-caught:

```js
if (cfg.useAudioFiles && cfg.audioBase) {
    (cfg.audioFamilies || ['ok', 'warn', 'danger', 'neutral']).forEach(function (fam) {
        var a = new Audio(cfg.audioBase + fam + '.mp3');
        a.preload = 'auto';
        a.addEventListener('error', function () { audioFiles[fam] = null; }, { once: true });
        audioFiles[fam] = a;
    });
}
```

`templates/merilv/outbound_session.html` now passes `"useAudioFiles": false`
beside `audioBase`. Behaviour is identical — tones were what played before — but
nothing is requested, so the console is clean.

### Switching to real audio later

1. Drop `ok.mp3`, `warn.mp3`, `danger.mp3`, `neutral.mp3` into
   `static/merilv/audio/`.
2. Set `"useAudioFiles": true` in the page config.

A file that then fails to load still degrades to its own tone, per family, so a
missing or corrupt file is never silent.

### Verified

- Failing requests on `/meril-v/outbound/3/`: **none** (was 4).
- WebAudio oscillator path still constructs and starts — sound is intact.
- No JS errors.

---

## Regression cover

`merilv/tests/test_export_performance.py` — 5 tests.

**Correctness**

- Small sheet keeps the grid and header styling.
- Sheet above `MAX_GRID_CELLS` drops the grid but preserves every value, row
  count, and header styling.
- Date/datetime number formats survive.
- Ragged rows do not raise.

**Complexity** — `test_cost_grows_roughly_linearly_with_row_count`

Times 500 rows against 4,000 rows (8× the data) and asserts the ratio stays
under 25×. Linear is ~8×; the old quadratic writer would be ~64× and in practice
far worse. This deliberately asserts the *shape of the curve*, not a throughput
figure, so it catches a reintroduced O(n²) loop without failing on a slow CI box.

---

## Files changed

| File | Change |
|---|---|
| `merilv/services/export.py` | Linear row indexing; per-cell work only where needed; new `MAX_GRID_CELLS` budget |
| `static/merilv/js/scanner.js` | mp3 preload gated behind `useAudioFiles` |
| `templates/merilv/outbound_session.html` | Passes `"useAudioFiles": false` |
| `merilv/tests/test_export_performance.py` | New — 5 regression tests |

## Verification run

- `pytest merilv/` → **681 passed**
- New export tests → **5 passed**
- All 33 Meril-V screens re-smoked → 200, no layout overflow, no template leaks,
  no JS errors, no failing sub-requests

## Not bugs (confirmed intended)

Two things that look wrong in a route sweep and are not:

- `/meril-v/zva05/` and `/meril-v/bt-scan/` both redirect to
  `/meril-v/outbound/`. ZVA05 and BT Scanning became *modes* of the Outbound
  Scanner; their consoles are reached from that mode's session list. Documented
  in `merilv/urls.py`.
- `/meril-v/deliveries/packing-list/` returns a file download (valid PDF,
  ~138 KB), not an HTML page.
