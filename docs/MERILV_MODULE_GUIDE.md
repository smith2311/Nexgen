# Meril-V — Module Guide

Meril-V is the warehouse validation and outbound dispatch module in NexGen
Automation. It lives at `/meril-v/`, is built as the Django app `merilv`, and
replaces a standalone legacy tool ("MerilValidator") that ran as a separate
Node/Express + Supabase application outside this platform.

This guide answers: what is it, why does it exist, what can it do, and who
should use which part of it.

---

## Table of Contents

1. [What Is Meril-V?](#what-is-meril-v)
2. [Why It Was Built](#why-it-was-built)
3. [How It's Different From the Old Tool](#how-its-different-from-the-old-tool)
4. [Who Uses It](#who-uses-it)
5. [Features](#features)
   - [Stock Validator](#1-stock-validator)
   - [Outbound Scanner](#2-outbound-scanner)
   - [Barcode Extractor](#3-barcode-extractor)
   - [Branch Transfer](#4-branch-transfer)
   - [Inbound Scanner](#4b-inbound-scanner)
   - [ZVA05 Auto-Assign](#5-zva05-auto-assign)
   - [Invoice Consolidator](#6-invoice-consolidator)
   - [Live Monitoring](#7-live-monitoring)
   - [Master Data Console](#8-master-data-console)
6. [Business Units (Entities) It Supports](#business-units-entities-it-supports)
7. [How Barcode Parsing Works](#how-barcode-parsing-works)
8. [Roles & Permissions](#roles--permissions)
9. [Where Things Live (For Admins/Developers)](#where-things-live-for-administratorsdevelopers)
10. [Frequently Asked Questions](#frequently-asked-questions)

---

## What Is Meril-V?

Meril-V is a set of connected warehouse tools that all revolve around one
question: **does what's physically in front of us match what SAP thinks
should be there?**

It covers two ends of the same problem:

- **Inbound / stock-on-hand** — "Is the stock on our shelves the stock SAP
  says we have?" → the **Stock Validator**.
- **Outbound / dispatch** — "Are we packing the right item, right batch, right
  quantity, onto the right delivery?" → the **Outbound Scanner** and its
  related tools (Branch Transfer, ZVA05).

Around those two cores it also provides a barcode diagnostic workstation, an
invoice consolidator, live operational visibility, and the master data
(materials, customers, couriers, cartons, plants) that all of the above run
on.

## Why It Was Built

Meril's warehouse teams were previously using a separate tool
("Meril-V Validation Ecosystem" / MerilValidator) hosted outside this
platform — its own login, its own database (Supabase), its own server. That
tool had grown into **eight near-identical pages**: a separate Stock
Validator and a separate Outbound Scanner for each business unit (MHPL,
MLSIPL, Cardio, Micro, MDPL). Each page was a copy-paste of the last one, so
each had its own drift and its own bugs — four different barcode readers and
five different matching engines doing the same job slightly differently.

Meril-V brings that functionality **into NexGen** so that:

- Warehouse staff log in once, with the same account they use for every other
  NexGen tool (no separate Supabase login).
- One Stock Validator and one Outbound Scanner serve *every* business unit —
  you pick the unit from a dropdown, and the parsing/matching rules for that
  unit are applied automatically.
- Master data (materials, customers, couriers) is shared and consistent
  instead of duplicated per business unit.
- All the audit trail, activity history and reporting is native to the
  platform instead of living in a separate system.

## How It's Different From the Old Tool

| | Old tool (MerilValidator) | Meril-V (this module) |
|---|---|---|
| Hosting | Separate Node/Express server, Supabase DB | Inside NexGen (Django, same database) |
| Login | Its own username/password system | Your existing NexGen login |
| Per-unit pages | 8 separate pages (4 validators + 4 scanners), each hand-copied | 1 validator + 1 scanner, unit selected from a dropdown |
| Barcode logic | 4 different parsers, none fully consistent | 1 parser, configured per business unit |
| Master data | Duplicated / inconsistent across pages | Centralized, shared |
| Extra tools | Barcode Extractor, Branch Transfer, ZVA05, Invoice Consolidator, QA dashboard | All ported except the QA dashboard (not needed — NexGen has its own testing) |
| Live visibility | None | Live Monitoring board (who's picking what, right now) |

Nothing about *how the barcodes are read or matched* changed on purpose —
the goal was a faithful, safer port, not a redesign of the warehouse logic.

## Who Uses It

| Role | What they can do |
|---|---|
| **Picker / Warehouse operator** | Scan for validation and outbound dispatch, run branch transfers, use the extractor, close deliveries |
| **Warehouse Supervisor / Viewer** | See everything above, plus history, reports and the live monitoring board, without needing to scan |
| **Meril-V Admin / Warehouse Manager** | All of the above, plus edit master data (materials, customers, couriers, cartons), configure business-unit rules, view the activity log |

Access is controlled the same way as the rest of NexGen — through your
account's group membership (e.g. "Meril-V Picker", "Warehouse Manager") or
`@merillife.com` staff status. If you can't see Meril-V in the sidebar, you
haven't been added to one of these groups yet — ask an admin.

---

## Features

### 1. Stock Validator

**Purpose:** Confirm that physically scanned inventory matches what SAP says
should be on the shelf.

**How it works:**
1. Pick the business unit (MHPL, MLSIPL, Cardio, Micro, MDPL).
2. Upload the **SAP stock dump** (an export of what SAP thinks is in stock).
3. Upload the **physical scan file** (what was actually scanned in the
   warehouse).
4. The engine reconciles the two and classifies every scanned item as:
   - **Matched** — material, batch and serial all agree.
   - **Batch Match / Serial Diff** — right material and batch, serial
     doesn't match (or is missing).
   - **Material OK / Batch Diff** — right material, wrong batch.
   - **Variance** — not found in SAP at all.
   - Plus flags for **duplicate scans**, **over-scans** (scanned more than
     SAP says exists), **expiry mismatches** (barcode date vs SAP date, more
     than 7 days apart), **plant mismatches**, and **incomplete barcodes**
     (missing batch/serial).
5. Review results on-screen (filterable by status, searchable), or export the
   full workbook (results + damage + expiry + summary sheets).

**Why it matters:** catches missing stock, wrong-batch shipments, and expired
or damaged stock before it becomes a bigger problem.

### 2. Outbound Scanner

**Purpose:** Make sure the right item, right batch, right quantity goes onto
the right delivery — and never lets a picker accidentally over-pack, pack the
wrong batch, or pack onto the wrong delivery.

**How it works:**
1. Upload a **YVL06 delivery dump** (SAP's export of what needs to be
   shipped, per delivery).
2. Optionally **focus** the scanner on one delivery (or one customer) — once
   focused, only items for that delivery/customer will be accepted.
3. Scan items one at a time. Every scan gets an instant, specific response
   — never just "rejected":
   - ✅ **Accepted** — assigned to the delivery, quantity updates live.
   - ❌ **Wrong batch** — tells you exactly which batch(es) are still needed.
   - ❌ **Wrong material** — this item isn't needed anywhere in the shipment.
   - ❌ **Over-scan** — every line for this material is already full.
   - ❌ **Expired item** — blocked outright, "do not pack."
   - ❌ **Duplicate serial** — this exact unit is already packed.
   - ⚠️ **Delivery full / Target mismatch** — distinguishes "this delivery
     is done" from "wrong item for this delivery."
   - ❌ **Box full** — if carton capacity limits are turned on for a business
     unit.
4. Assign a carton/box as you pack (optional, tracks capacity).
5. **Undo** the last scan, **correct a mistyped serial**, or **override a
   line's expected batch** (locked once anything's been scanned against it,
   so a correction can't silently misreport what physically shipped).
6. **Finish** a delivery: enter courier, docket number, vehicle details and
   box counts. If anything is still short, you get a discrepancy report
   before you can proceed (or an explicit "proceed anyway" if partial
   shipping is allowed for that unit).
7. Export the finished delivery as a SAP-ready serial upload sheet.

**Why it matters:** this is the tool a picker uses all day; every rejection
tells them exactly what went wrong so they don't have to guess.

### 3. Barcode Extractor

**Purpose:** A diagnostic and lightweight-scanning workstation, separate from
a full delivery/validation run.

Three modes:
- **Extract** — paste or upload a batch of barcodes and see every field
  broken out (material, batch, serial, mfg/expiry dates) without needing any
  dump files.
- **Live Scan** — scan items one by one and see found / not-found / duplicate
  counts build up against the material master, in real time.
- **Live Validate** — scan against an uploaded stock dump and see match /
  mismatch / not-in-dump / excess counts update live, e.g. for a walk-through
  physical verification. If the automatic column detection gets a dump's
  layout wrong, you can manually map its columns instead of re-exporting from
  SAP.

Sessions can be named and saved for audit history, and exported to Excel.

### 4. Branch Transfer

**Purpose:** Pick stock to move between two of Meril's own locations
(branches/plants), following an inter-branch transfer plan.

- Upload a **BT plan** (what needs to move — SKU and quantity, no batch
  specified) and, optionally, an **MB52 stock dump** (what's actually
  available to pick from).
- Scan items; matching is on material only (the plan doesn't dictate a
  batch — whichever batch you physically have is accepted, provided stock
  exists).
- Export the picked plan with actual batches/locations recorded.

### 4b. Inbound Scanner

**Purpose:** The inbound half of a branch transfer — confirm that everything the
sending plant dispatched actually turned up at the destination.

Before this existed, a transfer was only ever half-tracked: the sending plant
recorded what it picked, and nothing recorded what arrived. Stock could go
missing in transit with no system record of it.

**How it works:**
1. Go to **Inbound Scanner** and pick a transfer that has already been picked on the
   sending side. (Plans nobody has picked yet aren't offered — every scan
   against them would be refused.) Optionally record the trip reference,
   vehicle and docket number.
2. Scan items as they come off the vehicle. Matching is on **material only**,
   exactly like the picking side — the plan never dictated a batch, so whatever
   batch physically arrives is the correct one.
3. The expected quantity is what was **dispatched**, not what was planned. A
   line planned for 5 that only shipped 2 expects 2; measuring against the plan
   would show a shortfall that never existed.
4. Rejections tell you which of two very different problems you have:
   - **Nothing dispatched** — the item is on the plan but the sender never
     picked any. A warning, not an error: the carton in your hand is real, the
     sender's paperwork is wrong. Chase the sending plant.
   - **Over received** — more units arrived than were dispatched. Chase your
     own count, or the sender's under-reporting.
   - **Duplicate serial** — that exact unit is already booked in, including
     across earlier vehicle loads of the same transfer.
   - **Material not in plan** — not part of this transfer at all.
5. A transfer can be received in several loads — the running totals accumulate,
   so closing one vehicle's receipt doesn't close the transfer.
6. **Close the receipt.** Anything still outstanding is recorded as **short in
   transit**, and the export carries Planned / Dispatched / Received / Short per
   line.

**Why it matters:** this is the only place transit losses become visible. Both
plants' numbers now have to agree, and when they don't, the discrepancy is
attributable rather than a mystery.

### 5. ZVA05 Auto-Assign

**Purpose:** Assign scans to open sales orders **before** SAP deliveries have
even been created — useful when the delivery document doesn't exist yet but
picking needs to start.

- Upload a ZVA05 open-order extract (material + ordered quantity + customer/
  sales document).
- Scan items; matched on material only against the ordered quantity.
- Optionally focus on one customer's order so scans can't drift onto a
  different customer's stock.
- Undo, close, and export are all supported, same as the other scanners.

### 6. Invoice Consolidator

**Purpose:** Turn a batch of invoice PDFs (or Excel/CSV exports) into one
flat, searchable table — useful for reconciling shipping costs or preparing
consolidated reports without manually re-typing every invoice.

- Upload multiple PDF invoices, or a spreadsheet.
- The system extracts invoice number, date, customer, and line items
  (material, batch, quantity, rate, amount) automatically.
- PDF layouts vary, so each extracted row carries a confidence indicator —
  it's a best-effort reader, not a guarantee, and unreadable pages are
  flagged rather than silently skipped.
- Review, search and export the consolidated result.

### 7. Live Monitoring

**Purpose:** See who is working on what, right now — without interrupting
anyone to ask.

- Shows every open outbound session: which picker, which delivery/customer
  they're focused on (or "All deliveries" if unfocused), progress so far, and
  how long since their last scan.
- A picker is shown as **Working** if they've scanned within the last 30
  minutes, or **Idle** otherwise — this is worked out automatically from scan
  timestamps, so it stays accurate even if someone's device dies or they
  just walk away.
- Also lists open Barcode Extractor sessions (Live Scan / Live Validate) the
  same way.
- Auto-refreshes every 20 seconds while the tab is open (pauses if you switch
  tabs, so it doesn't hammer the server unnecessarily).

**Why it matters:** a supervisor can see bottlenecks (a picker stuck on one
delivery, a session gone idle) without walking the floor or radioing anyone.

### 8. Master Data Console

**Purpose:** The shared reference data every other tool depends on. Admin
only.

| Master | What it holds |
|---|---|
| **Materials** | Scan code ↔ SAP material code ↔ GTIN mapping, per business unit — this is what lets a label's printed code resolve to the right SAP item |
| **Customers** | Ship-to/sold-to parties, their courier defaults, contact and address info |
| **Couriers** | Courier partners, vehicle numbers, plant coverage |
| **Cartons** | Box dimensions, volumetric weight, unit capacity (used by the Outbound Scanner's box-packing feature) |
| **Organisation** | Company → sales organisation → plant → storage location hierarchy, plus billing-type and sales-type reference lists |
| **Entities** | Per-business-unit configuration: whether the unit uses serial numbers, how batches are cleaned up for comparison, which barcode formats to accept, expiry-warning thresholds, plant priority order, and more |

Materials and customers can be bulk-imported from Excel/CSV (with an option
to fully replace an existing set, or just update/add to it) and exported the
same way.

---

## Business Units (Entities) It Supports

Meril-V ships pre-configured for the five business units the legacy tool
covered:

| Code | Unit | Notes |
|---|---|---|
| **MHPL** | Orthopedics / Trauma | Uses serial numbers; supports the legacy "Meril Trauma" barcode format |
| **MLSIPL** | Orthopedics (India) | No serial number concept — tracked to batch level only |
| **CARDIO** | Cardio | Uses serial numbers |
| **MICRO** | Micro | Uses serial numbers; accepts a couple of extra legacy barcode formats |
| **MDPL** | Diagnostics | Uses serial numbers |

An admin can add a new business unit at any time from the **Entities**
section of Master Data — no code changes needed, since every unit's behavior
is just configuration.

## How Barcode Parsing Works

Every scan goes through the same reader, but its exact behavior is tuned per
business unit. In plain terms, the reader looks for:

- A **material code** (either Meril's own printed scan code, or a GTIN that
  gets looked up against the material master).
- A **batch number**.
- A **serial number** (for units that use them).
- Optional **manufacture/expiry dates**.

It understands several barcode "dialects" — standard GS1 bracketed codes
`(01)...(10)...`, compact codes with no brackets, and a couple of older
formats some hand-scanners still produce — so pickers don't need to know or
care which format a particular label uses.

If a barcode can't be matched to a material at all, the item is treated as
"unknown" rather than silently dropped, so it still shows up for a human to
resolve.

## Roles & Permissions

Access follows the same group-based system as the rest of NexGen:

- **Meril-V Viewer** / **Warehouse Supervisor** — read-only: history, reports,
  monitoring.
- **Meril-V Picker** / **Meril-V User** / **Warehouse Picker** — everyday
  operational use: all scanning tools, validator, extractor, transfers.
- **Meril-V Admin** / **Warehouse Manager** — all of the above, plus the
  Master Data console.
- **Superusers** and **`@merillife.com` staff accounts** get full access
  automatically.

If a tool or the whole module isn't showing up in your sidebar, it's a group
membership issue — ask an administrator to add you to the right group.

## Where Things Live (For Administrators/Developers)

- **App:** `merilv/` — Django app, registered in `INSTALLED_APPS`, mounted at
  `/meril-v/` in `core/urls.py`.
- **Business logic:** `merilv/services/` — the barcode parser, matching
  engine, outbound engine, spreadsheet ingestion, exporters, etc. These are
  plain Python with no Django dependency, so they're unit-tested in
  isolation from the web layer.
- **Views:** `merilv/views.py` (core tools), `merilv/masters.py` (master
  data), `merilv/zva05.py`, `merilv/monitoring.py`.
- **Templates:** `templates/merilv/` — follows the same design system as
  VendorLink (`vendorlink-ds.css`).
- **Seed data:** `python manage.py seed_merilv` creates the five default
  business units and standard carton profiles (safe to re-run; add `--reset`
  to overwrite tuned configuration back to defaults).
- **Tests:** `merilv/tests/` — 171 tests covering the barcode parser, the
  matching engine, the outbound engine, spreadsheet ingestion, and every
  page/workflow end to end.

## Frequently Asked Questions

**Do I need a separate login for Meril-V?**
No. It uses your existing NexGen account. There is no separate database or
password system anymore.

**What happened to the old MerilValidator tool / its data?**
Meril-V is a new build inside NexGen — it does not automatically import old
data from the previous Supabase-based system. Historical records there are
unaffected but live separately.

**Why does the Outbound Scanner reject my scan without accepting it, even
though the item looks right?**
It always tells you specifically why — wrong batch (and lists which batches
are actually needed), wrong material, already fully scanned, expired, a
duplicate serial, or that you're focused on a different delivery/customer.
Read the message; it's designed to be actionable, not just "error."

**Can I still scan without focusing on a specific delivery?**
Yes — focusing is optional. Without a focus, the scanner will try to match
the item against every open delivery in the loaded dump.

**What if my business unit isn't in the list?**
An admin can add it from Master Data → Entities. It just needs its parsing
and matching rules configured — no development work required.

**Can I fix a mistake after scanning?**
Yes — undo the last scan, or use "Correct a serial" if a specific serial was
misread. Changing a delivery line's expected batch is also possible, but only
before anything has been scanned against that line (it locks afterward, to
protect the accuracy of what's already been packed).

**Is the Invoice Consolidator 100% accurate?**
No — PDF invoice layouts vary too much to guarantee perfect extraction. Each
row is scored with a confidence level so low-confidence rows can be manually
double-checked before relying on them.

**Who do I ask for access or a new business unit?**
Your Meril-V Admin / Warehouse Manager, or whoever administers NexGen groups
for your team.
