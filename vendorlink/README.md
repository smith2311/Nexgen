# VendorLink — User Manual

**The 3PL Vendor Database & Innovation Platform.**
One connected home for vendor identity, commercials, capabilities and performance.

---

## Contents

1. [What VendorLink is for](#1-what-vendorlink-is-for)
2. [Getting in](#2-getting-in)
3. [The landing page](#3-the-landing-page)
4. [Who can do what](#4-who-can-do-what)
5. [Vendor Master](#5-vendor-master)
6. [Getting a vendor onto the platform](#6-getting-a-vendor-onto-the-platform)
7. [Inquiries — raising an RFI and awarding it](#7-inquiries--raising-an-rfi-and-awarding-it)
8. [Commercial Database](#8-commercial-database)
9. [KPI Dashboard](#9-kpi-dashboard)
10. [Vendor Scorecard](#10-vendor-scorecard)
11. [Bid Analytics and MerilGPT](#11-bid-analytics-and-merilgpt)
12. [Geo Command Centre](#12-geo-command-centre)
13. [Reports and exports](#13-reports-and-exports)
14. [Admin Console](#14-admin-console)
15. [If you are a vendor](#15-if-you-are-a-vendor)
16. ["How do I…" quick index](#16-how-do-i-quick-index)
17. [When something looks wrong](#17-when-something-looks-wrong)

---

## 1. What VendorLink is for

Before VendorLink, choosing a third-party logistics provider meant a spreadsheet
per country, a mailbox full of quotations, and a decision nobody could
reconstruct six months later. VendorLink replaces that with one record of who
our 3PL vendors are, what they can do, what they charge, how they perform, and
why a particular one won a particular piece of business.

It answers five questions, and each module exists for exactly one of them:

| The question | Where it is answered |
|---|---|
| **Who are our vendors, and what can they do?** | Vendor Master + Capability Matrix |
| **What do they charge?** | Commercial Database |
| **Who should get this shipment?** | Inquiries (RFI) → bids → award |
| **How are they actually performing?** | KPI Dashboard + Vendor Scorecard |
| **What did we decide, and why?** | Bid Analytics, Reports, award history |

Nothing in VendorLink deletes history. A completed year stays readable, an
awarded RFI keeps every bid that lost, and a withdrawn award keeps its trail —
because an award and the bids behind it *are* the audit record of a commercial
decision.

---

## 2. Getting in

### Where to go

1. Open the **NexGen Automation** portal in your browser.
   - Local / office deployment: `http://localhost:7000`
   - Hosted deployment: the address your IT team gave you (e.g. `https://gpt.e-meril.in`)
2. Sign in with your **Meril account** (the Zoho sign-in button). Use your
   `@merillife.com` / `@meril.com` / `@meril.in` address.
3. In the left sidebar under **APPLICATIONS**, click **VendorLink**.

Direct link, once you are signed in: **`/vendorlink/`**

### If you cannot see VendorLink in the sidebar

You are signed in but have no VendorLink role yet. Ask a VendorLink
administrator to add you — see [Who can do what](#4-who-can-do-what). Access is
granted by group membership, not by request form, so it takes them under a
minute.

### If you are an external vendor

You do **not** use the Meril sign-in. See [If you are a vendor](#15-if-you-are-a-vendor).

---

## 3. The landing page

`/vendorlink/` is the launcher. Top to bottom:

**Admin Console banner** — administrators only. One click to every master
dataset and every user account.

**Geo Command Centre** — the world map of where our registered vendors actually
are. Covered in [section 12](#12-geo-command-centre).

**Vendor Registrations** — four tiles: total received, pending review, approved,
rejected. Click **View all** to open the approval queue. If anything is pending,
a highlighted bar appears above the tiles.

**Modules** — the five working areas, each a card showing live counts:

| Card | What it opens |
|---|---|
| Vendor Master | the directory of approved 3PL vendors |
| Commercial Database | rate cards, AI bid evaluations, Bid Analytics |
| KPI Dashboard | monthly performance metrics and trends |
| Vendor Scorecard | weighted scoring and ranking |
| Inquiries | RFIs you have raised and the responses to them |

---

## 4. Who can do what

VendorLink has four tiers. Your tier is set by which group your account belongs
to; superusers and Meril-domain staff accounts get full access automatically.

| Tier | Groups | Can |
|---|---|---|
| **Viewer** | VendorLink Viewer, Vendor Master Viewer | See everything. Export to Excel/PDF. Change nothing. |
| **Editor** | VendorLink User, Vendor Master User | The above, plus add and edit records — **but never delete**. |
| **Admin** | VendorLink Admin, Vendor Master Admin, Global SCM, Admin | Everything, including delete, the Admin Console, user management, assessment invites and approvals. |
| **Vendor** | VendorUser | Their own portal only: the RFIs aimed at their country, and their own bids. No access to internal master data. |

Two rules worth knowing:

- **The Editor/Admin line is drawn at delete.** An Editor can add a rate card and
  correct it; only an Admin can remove one.
- **The person who raised an RFI can award it**, even without the Admin group.
  It is their request; changing their own nomination should not need an
  escalation.

---

## 5. Vendor Master

**`/vendorlink/vendor-master/`** — the single source of truth for approved 3PL
vendors.

### Browsing

**Browse Directory** opens the full table: name, country, city, state, warehouse
location, contact person, email, mobile, website, and the capability flags.
Filter by country, search by name, sort by any column.

### A vendor's record

Click any row. The detail page shows identity, contacts, warehouse footprint and
the capabilities they hold, plus their KPI history and scorecards if any have
been entered.

### The nine capabilities

Every vendor is flagged against a master list of capabilities, which an
administrator can extend without a code change (Admin Console → Masters →
Capabilities):

GDP Certification · ISO Certification · Temperature Capability · Bonded
Warehouse · Customs Clearance · Last-Mile Distribution · Regulatory Support ·
SFDC Integration · EDI Capability

### Capability Matrix

**`/vendorlink/vendor-master/capability-matrix/`** — every vendor as a row,
every capability as a column. This is the screen to open when the question is
"who can actually do temperature-controlled bonded storage in this region".

### Adding and importing

- **Add Vendor** (Editor and above) — one form, with country and city pickers.
- **Import** — bulk-load from a spreadsheet. Download the template first
  (`Import → Download template`) so the columns match.
- **Export Excel** — the whole directory, any time, any tier.

---

## 6. Getting a vendor onto the platform

There are three routes in, and they are not interchangeable.

### Route A — the vendor registers themselves (most common)

1. Send the vendor this **public** link (no login needed):
   **`/vendorlink/register/`**
2. They fill in identity, contacts, location and capabilities.
3. They verify their email with a **one-time code**, and complete a **captcha**.
4. The submission lands in the approval queue and the tiles on the landing page
   update.
5. An administrator opens **`/vendorlink/registrations/`**, reviews it, and
   approves or rejects. Approving **creates the vendor's portal account** and
   mails them their credentials.

You can approve or reject in bulk from the queue.

### Route B — an administrator adds them directly

Vendor Master → **Add Vendor**. Use this for a vendor already known and
contracted, where a self-registration form would be ceremony.

### Route C — capability assessment first

For a vendor being evaluated rather than onboarded. See below.

### 3PL Capability Assessment

**`/vendorlink/assessment/`** (Admin)

A 6-section, 34-field self-assessment the vendor completes **without an
account**:

1. **Invite** — enter the vendor's email and how many days the link should stay
   valid. VendorLink mails them a unique token link.
2. **They fill it in** at that link. Each field carries its own "guidelines to
   fill" text.
3. **Review** — the assessment appears in the list. Read it, then approve or
   reject.
4. **Promote** — an approved assessment can be turned into a Vendor Master
   record in one click, carrying its answers across.

Links expire. An expired or already-used token shows the vendor a clear message
rather than a broken page.

---

## 7. Inquiries — raising an RFI and awarding it

This is the core commercial workflow. **`/vendorlink/requests/`**

### Raising a request

**New Request** (internal accounts only — vendor accounts can never raise one).

You provide the subject, the country, the requirements (space, temperature,
volumes, scope) and any attachments. On save, **every active vendor in that
country is emailed** and can see the RFI in their portal. The RFI gets a
reference in the form `RFI-2026-000004` — quote it in conversation and in
MerilGPT.

### What the vendors do

Each vendor submits a bid: total cost, currency, the services they are
including, and any notes or documents. They may submit revisions; each is kept.

### Comparing

Open the request. You see every vendor, every bid, and what each vendor can and
cannot do against what the request needs.

- **Compare** (`/vendorlink/bids/<id>/compare/`) — a heat-map of quotes across
  vendors and line items, cheapest highlighted. Exportable to Excel.
- **Evaluate** — the AI evaluator reads the request and every bid and
  recommends one vendor, with its reasoning. It applies the same rule a careful
  buyer would: **the cheapest vendor that also delivers every service the
  request needs**. A cheaper vendor missing a required service, or one marked
  blacklisted, is never recommended — and the evaluation says so explicitly.
- Currencies are never silently mixed. A total in AED is never compared against
  one in EUR as though they were the same number.

### Awarding

1. Choose the winning bid and click **Award**.
2. The vendor is **told by email**. They are not asked to confirm — the award is
   a decision, not a negotiation.
3. **Withdraw** reverses it, and the withdrawal stays in the record.
4. **Close nominations** on a request when the decision is final; **Reopen** if
   it genuinely is not.

Who may award: any VendorLink Admin, or the person who raised that RFI.

---

## 8. Commercial Database

**`/vendorlink/commercial/`**

Rate cards: what each vendor charges and on what contract terms — 13 fields per
card, covering storage, handling, transport, validity and payment terms.

| Action | Tier |
|---|---|
| Browse, filter, open a card | Viewer |
| Add a card, edit an existing one | Editor |
| Delete a card | Admin |
| Export to Excel, export one card to PDF | Viewer |

Use this when you need a standing price, not a quote for a specific shipment.
Quotes for a shipment are bids, and they live on the RFI.

---

## 9. KPI Dashboard

**`/vendorlink/kpi/`**

Eight metrics per vendor, **per month**:

| Metric | Unit |
|---|---|
| Inventory Accuracy | % |
| On-Time Delivery | % |
| Order Fulfilment Accuracy | % |
| Damage Rate | % |
| Response Time | hours |
| CAPA Closure Time | days |
| Internal Customer Complaints | count |
| (plus the period the row covers) | month |

Global SCM enters these — never the vendor. One row per vendor per month; a
quarterly view is a roll-up of three months, not a separate entry.

**New KPI record** to add a month. **Export** for the whole set. Trends are
drawn from the monthly rows, so a vendor with two years of history has a two-year
trend line without anything extra being typed.

---

## 10. Vendor Scorecard

**`/vendorlink/scorecard/`**

A weighted evaluation, scored out of 10 on four attributes:

| Attribute | Weight |
|---|---|
| Serviceability | **40%** |
| Cost | 20% |
| WMS / Reports | 20% |
| TAT (turnaround time) | 20% |

**Every score requires a comment.** That is deliberate: a bare 6/10 tells the
next reader nothing, and the comment is what makes a score defensible when a
vendor asks why they ranked where they did.

The Final Score is computed from the weights; vendors rank against each other on
it. **Vendor history** shows how one vendor's scores have moved over time.

---

## 11. Bid Analytics and MerilGPT

**`/vendorlink/bids/`**

A dashboard over every RFI and bid: active RFIs grouped by date, a price
heat-map across vendors, and totals by currency.

### MerilGPT

The chat drawer on this page answers questions in plain English. It knows three
things, and picks the right one for what you asked:

| You ask about | It answers from | Shown as |
|---|---|---|
| our vendors, bids, prices, who to pick | the live portal data | *Answered from VendorLink data* |
| a file you attached | that file, on its own terms | *Answered from your attached file* |
| a general question — a term, a calculation, background | its own knowledge | *Answered from general knowledge* |
| "how does this quote compare with our bids?" | both | *Answered from your file and VendorLink data* |

Each answer carries a small chip saying which of the four it used, so you can
see how your question was read before you judge the answer.

**Attachments.** Drag in a PDF, Word, Excel, CSV, JSON, text file or image. It
is read and answered on its own terms — a rate card is treated as a rate card, a
lab protocol as a lab protocol. It will not force a connection to vendor data
that you did not ask for. Documents stay attached to the conversation, so a
follow-up question three turns later still has them.

**Reports.** Ask for "a table", "a chart", "a comparison" and the answer comes
back with KPI tiles, a chart and a sortable table you can copy straight into
Excel, print, or expand to full width. Switch the chart between **Bars**,
**Line** and **Share** with the buttons on the card.

**What it will not do:** invent a vendor, a price or an RFI that is not in the
data. If the data does not answer your question, it says so.

---

## 12. Geo Command Centre

The dark map on the landing page: where our registered vendors actually are.

- **📍 Pins** mark every city holding registered vendors. A pin with a number
  badge holds more than one.
- **Hover a pin** for a card showing continent → country → state → city, then
  each vendor there with contact details and warehouse address.
- **Four dropdowns** — Continent → Country → State → City — each filtered by the
  one above it. Choosing from them moves the map; clicking the map moves them.
- **Drill by clicking**: a continent, then a country, then a state. **Back**,
  the breadcrumb, or **Escape** step out again.
- An **amber, dashed pin** means the city could not be resolved from the
  vendor's address, so the pin sits at the country's centre. The card says
  "approximate". It is never a guess dressed as a fact.

The four tiles beside the map — registered warehouses, registered vendors,
states/regions, cities — always describe whatever you are currently looking at.

---

## 13. Reports and exports

| Report | Where | What it gives |
|---|---|---|
| **Request report** | `/vendorlink/reports/request/<id>/` | One RFI in full: who quoted, who was cheapest, who won, and the gap between those two |
| **Monthly report** | `/vendorlink/reports/monthly/` | A whole year: RFIs per month, cheapest-available against actually-committed, which vendors keep winning |
| **Bulk report** | `/vendorlink/reports/bulk/` | Several requests at once |
| **Historical data** | Admin Console → Historical Data | Any date range, exported to Excel or PDF |

Every report is **computed on the server from the rows themselves** — not
written by the AI. Two people running the same report on the same day get the
same numbers, and those numbers reconcile against the screen they came from.

Excel exports exist on the vendor directory, capability matrix, commercial
database, KPI records, the quote heat-map and the historical data screen.

---

## 14. Admin Console

**`/vendorlink/admin/`** — administrators only.

| Section | What it does |
|---|---|
| **Console** | KPI overview, quick links, recent activity across the platform |
| **Users** | Every account that can touch VendorLink — staff and portal vendors. Create accounts, change roles, deactivate |
| **Masters** | A directory of every master dataset: vendors, commercials, countries, capabilities, registrations, requests |
| **Capabilities** | Add or retire a capability. New ones appear on every vendor form immediately — no code change, no deployment |
| **Insights** | Per-user activity: who is using what |
| **Historical Data** | Read and export any date range |

**Nothing here deletes a year.** The old year-end purge is gone; historical data
is read over a range instead, because an award and the bids behind it are the
audit trail for a commercial decision.

---

## 15. If you are a vendor

You reach VendorLink through your **own portal**, with the credentials emailed
to you when your registration was approved.

**What you can do:**

- See the RFIs raised for your country, with their requirements and attachments.
- Submit a bid: your total cost, currency, the services you are including, and
  any supporting documents. You may revise it.
- See which of your bids were awarded.
- Change your password (`/vendorlink/vendor/change-password/`).

**What you cannot do:** see other vendors' bids or prices, see internal master
data, or raise a request. Your view is your own.

**First registering?** Use `/vendorlink/register/`. You will verify your email
with a one-time code and complete a captcha, then wait for Meril to approve.
You will be emailed either way.

---

## 16. "How do I…" quick index

| I want to… | Go to |
|---|---|
| Find a vendor in a country | Vendor Master → Browse Directory → filter by country |
| See who can do bonded + temperature storage | Vendor Master → Capability Matrix |
| Onboard a new vendor | Send them `/vendorlink/register/`, then approve in Registrations |
| Add a vendor I already work with | Vendor Master → Add Vendor |
| Assess a vendor's capabilities formally | Assessment → Invite |
| Get quotes for a shipment | Inquiries → New Request |
| Compare the quotes I received | Open the request → Compare (heat-map) or Evaluate (AI) |
| Give the business to a vendor | Open the request → Award on their bid |
| Record what a vendor charges as standing rates | Commercial Database → Add |
| Log this month's performance numbers | KPI Dashboard → New KPI record |
| Score and rank vendors | Vendor Scorecard → New scorecard |
| See where all our vendors are | Landing page → Geo Command Centre |
| Ask a question in plain English | Bid Analytics → MerilGPT drawer |
| Understand a document I was sent | MerilGPT → attach it → ask |
| Produce a report for management | Reports → Monthly, or a single Request report |
| Give someone access | Admin Console → Users |
| Add a new capability to the master | Admin Console → Masters → Capabilities |
| Get the data out | Export Excel on almost any list screen |

---

## 17. When something looks wrong

**"I can't see VendorLink in the sidebar."**
Your account has no VendorLink group. An administrator adds it in
Admin Console → Users.

**"I can see records but every Add/Edit button is missing."**
You are a Viewer. Ask for the Editor tier.

**"I can edit but not delete."**
That is the Editor tier working as designed. Deletion is Admin-only,
deliberately.

**"The vendor says they never got the RFI email."**
Check that the vendor is **active** and that their country matches the request's
country — the mail goes to active vendors in the chosen country only.

**"A vendor's pin is in the wrong place on the map, or shows 'approximate'."**
Their warehouse address could not be matched to a known city. Open their Vendor
Master record and fill in the **City** and **State** fields explicitly; the map
uses those first.

**"MerilGPT answered about vendors when I asked about my PDF."**
Check the chip above the answer. If it says *Answered from VendorLink data* when
you wanted the file, re-ask naming the document ("in the attached file, what
does it say about…"). If it keeps happening on a particular kind of file, report
it with the question and the file type.

**"The numbers in the report don't match the screen."**
They should — reports are computed from the same rows. Send both to your
administrator; that is a bug, not a setting.

**"Two vendors look like duplicates."**
Country names used to be free text, so the same vendor could be stored under
"India", "india" and "INDIA". New entries are normalised automatically; an
administrator can clean historical rows.

---

## For administrators: where things live

| Concern | File |
|---|---|
| Access rules and tiers | `vendorlink/rbac.py` |
| URL map — every screen and endpoint | `vendorlink/urls.py` |
| Vendor Master, requests, registration | `vendorlink/views.py` |
| Commercial Database | `vendorlink/commercial.py` |
| KPI Dashboard + Vendor Scorecard | `vendorlink/performance.py` |
| Capability Assessment | `vendorlink/assessment.py` |
| Awarding | `vendorlink/awards.py` |
| Server-computed reports | `vendorlink/reports.py` |
| AI evaluation and MerilGPT | `vendorlink/ai_evaluator.py` |
| Attachment reading (PDF, Office, CSV) | `vendorlink/gptfiles.py`, `gptrag.py` |
| Admin Console | `vendorlink/admin_console.py` |
| Geo Command Centre | `vendorlink/UI/` (see its own README) |

Tests live beside the code as `vendorlink/test_*.py` and run with `pytest vendorlink/`.
