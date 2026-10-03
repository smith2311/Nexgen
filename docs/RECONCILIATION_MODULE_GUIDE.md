# GST Reconciliation — Module Guide

GST Reconciliation is the three-way filing check in NexGen Automation. It lives
at `/reconciliation/`, is built as the Django app `reconciliation`, and replaces
a pair of classic ASP.NET Web Forms pages (`Reconciliation.aspx` and
`ReconciliationDashboard.aspx`, kept for reference in
`dummy_tempelate/reconciliation/`).

This guide answers: what it does, how a document gets classified, who can do
what, and where to look when a number seems wrong.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [The Screens](#the-screens)
3. [Importing an Export](#importing-an-export)
4. [Previewing Before You Commit](#previewing-before-you-commit)
5. [How a Document Is Classified](#how-a-document-is-classified)
6. [Reason Remarks You Will See](#reason-remarks-you-will-see)
7. [Correcting a Document](#correcting-a-document)
8. [Removing and Restoring](#removing-and-restoring)
9. [Roles and Permissions](#roles-and-permissions)
10. [Where Things Live](#where-things-live)
11. [Known Issues and Judgement Calls](#known-issues-and-judgement-calls)
12. [FAQ](#faq)

---

## What It Does

One sale can exist in three places at once:

- **GSTR-1** — what was filed on the GST portal.
- **E-Invoice** — what the Invoice Registration Portal returned, with its IRN.
- **E-Way Bill** — what actually authorised the goods to move.

They are supposed to agree. When they do not, somebody has to find out which of
the three is wrong, and why, before the return is filed. That is the job.

The module takes the **Reconciliation Export** workbook produced by the
reconciliation hub, stores every one of its **204 columns** under its own name,
and then answers three questions per document:

| Question | Column |
|---|---|
| Which of the three sources carry it? | `Presence` — a G / E / W mask |
| How far do their **taxable values** disagree? | `MaxGap` — largest minus smallest |
| Is that a problem, and what kind? | `ReasonRemark`, `Health`, `Segment`, `Gstr1Section` |

**Everything compares taxable value.** Document value includes tax and other
charges, and the three sources do not always break those down the same way, so
comparing document values reports disagreements that are not disagreements.
Measured on the real 10,424-row export: 234 ZFC challans are thousands apart on
document value and match *to the paisa* on taxable value.

Nothing from the export is renamed, merged or dropped. `GSTDocumentValue` and
`EWBDocumentValue` stay two separate columns, because comparing them is the
entire point. It also means a file exported from this module can be corrected
in Excel and uploaded straight back.

---

## The Screens

### Records — `/reconciliation/`

The working screen. Upload an export, preview it, import it, then filter, sort,
correct and export.

- Four tiles across the top — **Documents / Clear / Watch / Exception**. Each
  one is a filter; click it to narrow the table, click it again to clear.
- The **Where** column is the G / E / W mask: a filled blue G means it is in
  GSTR-1, a filled purple E means the IRP has it, a filled teal W means an
  e-way bill exists.
- The **Gap** column only prints when the three sources disagree by more than
  fifty paise.
- Clicking any amount opens the full record.

### Filing summary — `/reconciliation/dashboard/`

The pivot the finance team already reconciles against, computed live:

```
EntityGstin → Segment → GSTR1 → Reason Remarks → DocumentType
```

with a coloured subtotal band at the GSTR1, Segment and Entity levels, a Grand
Total, and a letterhead per vendor. "Export filing summary" produces the same
thing as an `.xlsx` with the merges and colours intact.

Above the pivot sits the analytics half of the screen:

| Panel | What it answers |
|---|---|
| **Six tiles** | Documents · Clear · Watch · Exception · GST value · Gap exposure |
| **Documents by month** | Volume and how much of it is clean, month by month |
| **Reason remarks** | Which remarks carry the most documents |
| **GSTR-1 sections** | B2B / B2C / exports / notes / exception buckets |
| **Source footprint** | Which combinations of G / E / W documents sit in |
| **What needs attention** | Three or four specific sentences about this scope |
| **Vendor health** | Documents, clear %, exceptions and GST value per vendor |

Everything on the screen — tiles, charts, insights, vendor table and pivot —
comes from **one** computation over **one** filtered queryset, so no two panels
can describe different populations. The chart series are embedded in the page
rather than fetched, because the server already has the numbers.

Two figures are easy to confuse, so they are labelled:

* **GST value** is *document* value, and ties out exactly against the pivot's
  Grand Total on the same screen.
* **Gap exposure** is *taxable*, because that is what reconciliation compares.
  It is the sum of every document's largest taxable disagreement.

Removed records never appear here. A filing summary that counts documents
somebody deleted is not a filing summary.

#### Why the monthly chart is stacked

Document volume and exception volume differ by two orders of magnitude. Plotted
as separate lines on one axis the exceptions flatten into the baseline, and the
usual escape — a second y-axis — is the one chart form never worth using.
Stacking keeps a single axis, restores the total as the bar's height, and makes
the exception band comparable month to month.

#### Chart colours

Two palettes, each with one job.

**Status** (Clear / Watch / Exception) is `#15803d` / `#f59e0b` / `#be123c` and
is never reused as a series colour. It is deliberately *not* the module's chip
colours: as adjacent stacked segments, `#dc2626` and `#d97706` sit at ΔE 14.4
for normal vision — below the 15 floor — so red and amber blur together. The
re-stepped triple clears every check (worst adjacent pair ΔE 17.4 under
protanopia, 30.3 normal). The chips elsewhere are unchanged; they sit apart and
always carry their own text.

**Magnitude** is one flat blue. Colour encodes the entity, never its rank, so a
ranked bar chart does not get a light-to-dark ramp — that would repaint the
bars every time a filter changed the order.

Amber sits at 2.09:1 against white, below the 3:1 mark, so it ships with
relief: a legend, a value printed on every bar, and a **Table** toggle on every
chart that swaps it for the same numbers as text.

Chart.js 4.4.3 is vendored at `static/lib/chartjs/`, not pulled from a CDN —
`base.html` self-hosts every font and stylesheet, and the module has to work on
a machine that cannot reach jsdelivr.

### Billing type master — `/reconciliation/billing-types/`

The 53 SAP billing codes the classifier judges rows against, and the "Rows"
column showing how many stored documents each one is actually carrying — which
is how you tell whether editing a rule matters or is theoretical.

**Editing a rule changes nothing already imported.** The numbers only move when
you press *Re-run reason rules* on the Records page. That is deliberate: a
master edit can silently re-label several thousand rows, and it should never
happen as a side effect of pressing Save on one of them.

---

## Importing an Export

An import is two steps, and **nothing is written to the database between
them**.

1. **Read file** — the workbook is parsed, every cell is typed, every row is
   classified, and the result is written to a staging file. You see what the
   file says and what each row was classified as. You can walk away.
2. **Confirm import** — the staged rows are matched against what is already
   stored and written, all inside one transaction.

Both steps run in the background and report real counts — rows read, rows
matched, rows saved — not a spinner animating a guess.

If step 2 fails, the whole batch rolls back and **the staged file is kept**, so
you can simply press Confirm again without re-reading the workbook.

### The two modes

**Update matching document** (the default) matches a staged row to a stored one
on:

- the same vendor (`EntityGstin`), **and**
- the same document number, **and**
- the same document type, **and**
- the same GST date — *only when the staged row has one*.

A staged row with no date matches on the triple alone. That is deliberately
broad: it lets a partial re-upload correct a record whose date was never filled
in. When that broad match would hit more than one stored record, the import
**stops and names the document** rather than guessing. Put the date on that row
and upload again.

A match **revives** a record somebody removed. A match against a record pinned
by hand is **left completely alone** — see [Correcting a
Document](#correcting-a-document).

**Append** matches nothing and inserts every row as a new record.

Re-uploading a file only ever touches the documents actually in it. Nothing
here bulk-deletes or bulk-replaces.

### From the command line

```bash
python manage.py import_reconciliation "ReconciliationHubReport.xlsx"
python manage.py import_reconciliation export.xlsx --dry-run
python manage.py import_reconciliation export.xlsx --mode append --user smith
```

`--dry-run` reads and classifies, prints the distribution, and writes nothing —
the way to check a new export's shape before letting it near the table.

### Dates are day-first

`05-06-2026` is **5 June**, not 6 May. Parsing it the other way does not fail,
it silently puts the document in the wrong filing period, so only day-first
patterns are tried (then ISO, then an Excel serial number).

---

## Previewing Before You Commit

Nothing in this module makes you take a file on trust. Every point where data
moves — in or out — has a **Preview** beside it, and the preview is the file:
same rows, same layout, same colours, built from the exporter's own palette
rather than a second set of values chosen to look similar.

| Where | Button | Shows |
|---|---|---|
| Records → after *Read file* | **Preview all columns** | The staged upload across all 204 columns, before a single row is written. |
| Records → filter bar | **Preview export** | The records export under the current filters, with a *Download .xlsx* button in the modal. |
| Filing summary | **Preview** | The pivot exactly as the workbook lays it out, with *Download .xlsx*. |

The upload step's own eight-column table answers "was this classified
correctly". **Preview all columns** answers the other question — did all 204
columns land where I think they did — which is the one you want answered
*before* pressing Confirm, not after.

Previews show the first 40 rows (the filing summary shows whole vendor blocks
and cuts at a vendor boundary, never mid-block). The footer always states how
many of how many you are looking at; the file itself contains everything.

### About the colours

The layout comes from the legacy report (`GST_Filing_Summary_*.xls`), and so
does the idea of banding each subtotal level. The palette does not.

That report fills every GSTR-1 subtotal with pure `#FFFF00`. Those are the most
numerous rows in the sheet, so most of a filing summary arrived as saturated
yellow with black text on top — fine for one row, punishing across two hundred.

Here the four levels are **tinted rather than filled**, and the tint gets
stronger as the subtotal gets broader:

| Level | Fill | Contrast with the text |
|---|---|---|
| Section (GSTR1) total | `#FFF8E1` | 13.1 : 1 |
| Segment total | `#FDEADC` | 12.0 : 1 |
| Vendor total | `#E4F1DD` | 11.9 : 1 |
| Grand total | `#D6E8F7` | 11.1 : 1 |

Every one is far past the 4.5 : 1 accessibility floor, and the escalation makes
the hierarchy something you see rather than something you read. The same three
tints mark Clear / Watch / Exception on the Reason Remark column of the records
export. `reconciliation/tests/test_previews.py` holds the contrast floor and
the ordering to a test, so neither can be lost to a later tweak.

### What else the records export now carries

210 columns is a maze without help, so the file arrives with:

- the heading row **frozen**, and the first four identity columns frozen with
  it, so you never lose track of which document a cell belongs to;
- **auto-filter** on every column;
- **number formats** — `DD-MM-YYYY` on dates, thousands-separated amounts —
  set on the columns rather than the cells, which keeps a 10,000-row export
  fast to generate and small to send;
- the **Reason Remark** column tinted by health, so exceptions separate without
  being read.

---

## How a Document Is Classified

Order matters. Narrow exceptions are tested before the broad "all filed ok"
cases, so a genuinely broken document can never be swallowed by a rule that
only meant to say "nothing to see here".

```
1.  Facts            Presence (G/E/W) and MaxGap, always, for every row.
2.  Pinned?          A hand-pinned record keeps its remark and stops here.
3.  Exceptions       Not about amounts: GST value missing · invoice cancelled ·
                     the portal's DATE mismatch · in the IRP but not in
                     GSTR-1 · Part-B not updated.
4.  Document type    ZCPU · ZSCE · ZSP. These outrank everything generic.
5.  Availability     Is a source the billing type marks mandatory missing?
6.  Three-way        Taxable values compared, Rs 10 tolerance.
                     Over it → "Amount Mismatch".
7.  Fallbacks        Challans, e-way-bill-only documents, services, scrap,
                     consignment, low-value invoices, notes, exports.
8.  Unclassified     Anything left → "Unclassified - review".
```

### The document-type rules

They run before the generic amount and availability logic, so a generic
condition can never answer a question one of them owns.

| Type | Situation | Result |
|---|---|---|
| **ZCPU** (Consignment Pick-Up) | E-way bill applicable and absent | `EWB not generated` — never "Dispatched Pending" |
| **ZSCE** (Scrap Invoice) | GST + E-Invoice + E-Way Bill, all matching | `OK` |
| **ZSCE** | GST + E-Invoice match, no e-way bill | `All OK - EWB NA` — never an amount mismatch |
| **ZSP** (Job Work Challan) | E-way bill present, nothing disagrees | `OK` |
| **ZSP** | Mandatory e-way bill absent | `EWAY MISSING - MANDATORY - JOBWORK` |

### The three-way comparison

The applicable sources' **taxable** values are compared together — not
pairwise, so the spread across all three is what counts.

- spread **≤ Rs 10** → matching
- spread **> Rs 10** → `Amount Mismatch`

Which sources count is decided by the billing-type master: a source it marks
NA is left out, because a Branch Transfer challan is deliberately Rs 0 on GST
while its e-way bill carries the real goods value.

### What a clean row is called

The most specific rule that applies wins:

1. a named document-type rule uses its own wording (the table above);
2. otherwise the billing-type master's Final Remark (`All Filed Match…`);
3. otherwise plain `OK`.

### The portal's own verdicts

The reconciliation hub sets `GstVsEwbSection = EwbMismatched` with a reason
that reads literally `Document Value = N`. That is a document-value
comparison, which is the comparison this module no longer makes, so **it no
longer decides a remark** — our own taxable comparison does. Its *date*
mismatch verdict is still trusted, because a date is a date.

On the 10,424-row export this alone moved 945 rows out of the exception queue:
711 ZSTI branch transfers (GST is NA for them, so they are not compared) and
234 ZFC (which match exactly on taxable value).

### What the billing-type master means

Each of the 53 codes declares which of GST / E-Invoice / E-Way Bill a document
of that type **must** carry, what document type it must be, and the remark to
file a compliant one under.

Two things about it are worth knowing, because both have caused false positives
before:

- **"NA" means "not mandatory", not "must not exist."** Checked against the
  real 10,424-row export, at least eight billing types carry an NA source on
  95–100% of their rows — every ZCFU consignment challan has an E-Invoice
  despite EINV = NA. Only a *missing* mandatory source counts against a row.
  Flagging the extra would have put roughly a fifth of a normal import into the
  exception queue for over-compliant filing.

- **An NA source is left out of the amount comparison.** Branch Transfer
  Challans (ZSTI / ZSTA / ZSTR) are why: GST is deliberately Rs 0 there — not a
  taxable supply — while the e-way bill correctly carries the real goods value.

### Thresholds

| Rule | Value | Note |
|---|---|---|
| E-way bill needed | Rs 50,000 | Supply invoices only. A **Challan** needs one whenever it exists — goods are moving, so there is no low-value grace period. |
| B2CL vs B2CS | Rs 2,50,000 | Inter-state B2C only. There is a clean gap in the real data at exactly this figure. |
| Amount tolerance | Rs 10 | On **taxable** value, across all applicable sources at once. A spread of exactly Rs 10 still matches. |

---

## Reason Remarks You Will See

Health is what the colour means: **Clear** (green), **Watch** (amber),
**Exception** (red).

| Remark | Health | What it means |
|---|---|---|
| `All Filed Match…` | Clear | The billing type's own remark. Everything mandatory is present and the taxable values agree. |
| `OK` | Clear | Reconciles, with no more specific rule to name it. |
| `All OK - EWB NA` | Clear | ZSCE scrap sale: GST and e-invoice agree, no e-way bill needed. |
| `Date mismatch - GST vs EWB` | Exception | The portal's own date verdict. |
| `Mismatch - GST vs EINV` | Exception | Likewise. |
| `Amount Mismatch` | Exception | Applicable **taxable** values disagree by more than Rs 10. |
| `Missing in GSTR-1 - EINV generated` | Exception | An IRN exists but nothing was filed. |
| `Part-B not updated` | Watch | E-way bill raised, transport details never added. |
| `Dispatched Pending` | Watch | Billed or issued, e-way bill not raised. |
| `EWB not generated` | Watch | ZCPU consignment pickup, specifically. |
| `EWAY MISSING - MANDATORY - JOBWORK` | Exception | ZSP job work — an e-way bill is not optional here. |
| `No value recorded - EWB not generated` | Clear | An empty placeholder challan. Nothing to reconcile. |
| `Wrong DocumentsType - billing type …` | Exception | The master expects a different document type. The remark names both. |
| `Invoice Cancel` | Exception | Cancelled in GSTR-1 with no e-invoice behind it. |
| `EINV Cancelled - reversal booked` | Watch | Cancelled at the IRP. |
| `Unclassified - review` | Exception | No rule matched. Worth reporting — it usually means a new billing code. |

---

## Correcting a Document

Click any amount, or the pencil on the right of a row. The drawer shows **all
204 source columns**, grouped as Document / GST / E-Invoice / E-Way Bill /
Comparison, plus a Classification tab.

Fields are parsed exactly the way an import parses them, so `1,20,000` is a
number and `06-05-2026` is 6 May. Saving re-classifies the record.

### Pinning a classification

On the Classification tab, tick **Pin this classification** to keep your own
Reason Remark, Segment, GSTR-1 section and Health instead of the computed ones.
A pinned record:

- keeps its remark when the rules are re-run, and
- is **left alone entirely by a re-import**, so an operator's decision survives
  the next upload.

Its `Presence` and `MaxGap` are still refreshed, because those are statements
of fact about the row, not judgements about it — a pinned record should not sit
on a stale gap figure.

---

## Removing and Restoring

Every delete is a **soft delete**. Nothing is ever destroyed.

- **Remove** (the bin on a row) marks one record removed, stamped with who and
  when.
- **Remove filtered** soft-deletes exactly what the current filters are showing
  and **refuses to run unfiltered**. Record status alone does not count as a
  filter, so "everything, including deleted" can never trigger it.

To get something back: set **Record status** to *Deleted only*, find it, and
press the restore arrow. A re-import of a removed document also brings it back
rather than creating a second copy beside it.

---

## Roles and Permissions

Two roles, held as Django groups. Both are created by migration `0003`.

| Capability | Admin | User |
|---|:---:|:---:|
| Open the module and read every screen | Yes | Yes |
| Correct an individual document | Yes | Yes |
| Export records and the filing summary | Yes | Yes |
| Upload and import a reconciliation export | Yes | — |
| Re-run the reason rules over stored records | Yes | — |
| Remove records in bulk | Yes | — |
| Edit the billing-type master | Yes | — |

**Admin can do anything in the module.** Django superusers are always treated as
Admin: somebody who could grant themselves the group anyway gains nothing from
being made to.

The group names are `Recon-Admin` and `Recon-User`. The prefix is there so they
read unambiguously in Django's own group list, beside `Grocery-`, `PR-` and
`VAT-`; on screen they are simply **Admin** and **User**.

The split is about **blast radius**, not seniority. Fixing one document's HSN
code affects one document; changing a billing type's Final Remark silently
re-labels several thousand rows on the next re-run, and uploading the wrong file
rewrites whatever it matches. Those belong with the people who own the filing.

Hiding a button is a courtesy — every endpoint re-checks the role.

### Granting access

**Django admin → Reconciliation → Module access.** Find the person, pick a role:

    ( ) No access
    ( ) Admin — everything in the module
    ( ) User  — read, correct and export

The same page prints the capability table above, generated from
`roles.CAPABILITIES`, so whoever grants a role can see exactly what they are
granting and the screen cannot promise something the module does not allow.

The list view shows every account with its module role, filters by role
(including **No access**), and carries bulk actions for granting Admin, granting
User and revoking. Changing a role **replaces** it rather than adding to it, and
revoking removes the role without touching the account.

That screen is a proxy over the user table, not a role table of its own. The
thing being managed — membership of the two groups — already exists, and a
second record of the same fact is how somebody keeps access they were supposed
to lose. It writes group membership and nothing else, which is also what
`roles.py` reads.

Accounts are neither created nor deleted from it: people come from the
platform's own user list, creating an account from a module page is how
duplicate accounts get made, and deleting one is never what "remove their
access" meant.

> Note: opening Django admin at all requires `is_staff`, which is separate from
> these roles. A Recon-Admin without `is_staff` runs the module fully but cannot
> reach the access screen.

---

## Where Things Live

| File | What it holds |
|---|---|
| `reconciliation/schema.py` | The 204 columns and their types. Generated from the real export; the single source of truth for the model, the reader and the exporter. |
| `reconciliation/billing_types.py` | The 53-code master as shipped, and the rule dataclass. Seeds the editable table. |
| `reconciliation/classify.py` | Every reason-remark rule, in order. |
| `reconciliation/parsing.py` | Reading .xlsx / .csv; day-first dates; finding the heading row. |
| `reconciliation/importer.py` | Staging, matching, writing, re-running the rules. |
| `reconciliation/filters.py` | One definition of "what the user is looking at", shared by the grid, the export, the pivot and Remove filtered. |
| `reconciliation/pivot.py` | The rollup and its merged-cell arithmetic. |
| `reconciliation/analytics.py` | The tiles, charts, insights and vendor table — arithmetic only, no HTTP. |
| `reconciliation/exports.py` | Both `.xlsx` writers, and `PALETTE` — the one place any export colour is defined. |
| `reconciliation/previews.py` | The HTML previews, built from that same palette. |
| `reconciliation/views.py` | Three pages and the endpoints behind them. |
| `reconciliation/roles.py` | The two roles and the capability matrix, read by every view. |
| `reconciliation/admin.py` | Django admin, including the Module access screen. |
| `templates/reconciliation/` | The three screens. |
| `static/css/reconciliation/recon.css`, `static/js/reconciliation/recon.js` | The records screen's styling and behaviour. |
| `static/js/reconciliation/recon_preview.js` | The preview modal, shared by every screen that has one. |
| `static/js/reconciliation/recon_charts.js` | The four charts and their table views. Chart colours live here, beside the data they encode. |
| `static/lib/chartjs/` | Chart.js 4.4.3, vendored. Nothing is fetched at runtime. |

Records are stored in `recon_records`, the master in `recon_billing_types`, and
one row per upload in `recon_import_jobs` — which doubles as the import log.

Uploaded files are kept under `MEDIA_ROOT/reconciliation/uploads/`, timestamped
rather than overwritten: when an import turns out to have been wrong, the file
that caused it is still there to look at.

---

## Known Issues and Judgement Calls

**Resolved (2026-09-24): the portal's document-value verdict no longer wins.**
This guide previously recorded that all 945 `Value mismatch - GST vs EWB` rows
came from the portal's own verdict rather than our comparison, and that 711 of
them (ZSTI branch transfers) arguably should not have been flagged. Moving the
comparison to taxable value and demoting that verdict resolved all 945: the 711
are excluded because GST is NA for them, and the 234 ZFC match exactly on
taxable value. Exceptions on that file fell from 1,039 to 94.

**Challan codes folded into LBT.** ZSTI, ZSFC, ZSD and ZFC have no distinct
bucket in the source pivot's vocabulary, so they fall into LBT as the closest
"miscellaneous Challan" bucket. Worth checking against your SAP config if the
split matters.

**Exempt billing types are untested against real data.** No exempt-coded row
appears in either sample export. An exempt supply should be a Bill of Supply,
which is not one of the four document types the export produces, so every
exempt-coded row will come out as a document-type exception by construction.
Verify once one shows up in a real filing.

**ZSCE contradicts itself.** The master marks EWB Applicable while its own Final
Remark says "EWB-NA-Scrap". The ZSCE rule answers it directly instead: all three
present and matching is `OK`, a missing e-way bill is `All OK - EWB NA`, and
ZSCE is excluded from the generic missing-e-way-bill check entirely — otherwise
a ZSCE row whose real problem was two disagreeing figures would have been
reported as "Dispatched Pending".

---

## FAQ

**Can I upload the same file twice?**
Yes. In "Update matching document" mode the second upload updates the documents
it contains and inserts nothing. That is the normal way to apply a corrected
export.

**What happens to a document number that appears twice in one file?**
The last occurrence wins. It happens in this data, and letting two rows claim
the same stored record is what has to be avoided.

**Why is my exported figure different from the screen?**
It should not be. The filing summary export and the dashboard share the same
computation, and the records export shares the same filters as the grid. If they
differ, that is a bug worth reporting with the filters you had set.

**Can I edit an export and upload it back?**
Yes. Every stored column carries its original heading, so an export is a valid
input. That is why nothing is renamed.

**The reason remark looks wrong for a whole billing type.**
Fix the rule on the Billing Type Master, then press *Re-run reason rules* on the
Records page. Records pinned by hand keep their remark.

**A new billing code showed up and everything landed in "Unclassified".**
Add it to the master. Until then it falls through to the generic rules, which is
by design rather than a failure.

**Can I check what an export will contain before downloading it?**
Yes — that is what the Preview buttons are for. The preview is generated from
the same rows and the same palette as the file, so what you approve is what you
get.

**The summary no longer looks like the old yellow report.**
Correct, and deliberately. The layout is identical — letterhead, merged labels,
a band per subtotal level, Grand Total — but the bands are tinted instead of
filled, so the sheet stays readable across hundreds of rows. See
[About the colours](#about-the-colours).

**Does the export still open in Excel?**
Yes, and properly — both exports are genuine `.xlsx` workbooks. The ASP.NET
originals streamed an HTML table named `.xls`, which made Excel warn about the
file format every time.
