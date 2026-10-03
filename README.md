# Nexgen Django App (development)

This repository contains a minimal Django 4.2 application configured to run with PostgreSQL via Docker Compose.

Quick start:

1. Copy `.env.example` to `.env` and update credentials if needed.

2. Build and run with Docker Compose:

```bash
docker-compose up --build
```

3. The app will be available at http://localhost:8000

Notes:
- `docker-compose.yml` defines a `web` service and a `db` (Postgres) service.
- The `web` service runs `python manage.py migrate` then `runserver` on container start.

---

## Modules

NexGen is a single platform made up of many separate tools ("modules"),
covering everything from warehouse scanning to sales, finance, HR and
logistics. Below is a plain-language introduction to each — what it's for and
how to use it day to day, written for end users rather than developers. For
the full technical setup of any module, see the app's own folder.

### Getting In

Everyone starts at the same **login screen** — sign in with your company SSO
account or a username/password. After logging in you land on the **Home**
screen, a personalized launcher showing a card for every module you have
access to (only the ones you're allowed to use appear — if a module you need
is missing, ask an admin to grant you access). From Home you can also get to
your **Profile** (contact details, photo), **Preferences** (theme, language,
timezone), **Notifications**, and **Security** settings at any time.

### Module Index

| Module | What it's for |
|---|---|
| [Meril-V](#meril-v--warehouse-validation--outbound-scanning) | Warehouse stock validation & outbound dispatch scanning |
| [MeriTrack](#meritrack) | Tracking consignment stock inside hospitals |
| [Product Scanning](#product-scanning) | Hospital stock audits & transfers via barcode scans |
| [Nordic Scanning](#nordic-scanning) | Hospital stock audits & transfers (Nordic region) |
| [Warehouse Inspection](#warehouse-inspection) | Digital floor-walk safety/condition checklists |
| [Material Rework](#material-rework) | Approval workflow for returning/reworking materials |
| [AI Sales Agent Dashboard](#ai-sales-agent-dashboard) | Command center for AI-driven hospital sales outreach |
| [VendorLink](#vendorlink) | Sourcing & comparing 3PL logistics vendors, with AI bid evaluation |
| [Shipment Notifications](#shipment-notifications) | WhatsApp alerts + live tracking for export shipments |
| [Logistics Performance](#logistics-performance) | Delivery time, cost & courier performance reporting |
| [Subsidiary Logistics Cost](#subsidiary-logistics-cost) | Country-by-country logistics cost tracking |
| [Billing Dashboard](#billing-dashboard) | Visual dashboard for monthly SAP export billing data |
| [VAT Reconciliation](#vat-reconciliation) | Checking VAT on international invoices for errors |
| [Visa Management](#visa-management) | Employee business-visa request & approval workflow |
| [PR Tracker](#pr-tracker) | Tracking Purchase Requests from raise to closure |
| [RS2T — LiveTranslate](#rs2t--livetranslate) | Real-time speech translation for multilingual meetings |

---

### Meril-V — Warehouse Validation & Outbound Scanning

**What it's for:** Meril-V answers one simple question — *does what's
physically in the warehouse match what SAP says should be there?* It covers
both sides of that: checking stock already on the shelf, and making sure
what gets packed and shipped out is correct before it leaves the building.

It replaces an older, separate tool that Meril's warehouse teams used to log
into on its own — now everything lives inside NexGen, under one login.

**Where to find it:** Click **Meril-V** from the NexGen home/launcher. If you
don't see it, you haven't been given access yet — ask your Meril-V Admin or
Warehouse Manager to add you.

**What you can do with it:**

- **Stock Validator** — Upload the SAP stock report and a file of what was
  physically scanned in the warehouse. Meril-V compares the two and tells you
  what matches, what's missing, what's the wrong batch, and what's expired
  or damaged. Export the results as an Excel report.
- **Outbound Scanner** — Upload the delivery list for what needs to be
  shipped, then scan items as you pack them. Every scan gets an instant
  answer — accepted, wrong batch, wrong item, already packed, expired, or
  box full — so mistakes are caught before the box is sealed, not after.
  Once a delivery is fully packed, close it out with courier and vehicle
  details and export a ready-to-upload sheet for SAP.
- **Barcode Extractor** — A quick way to scan or paste barcodes and see what
  they contain (item, batch, serial, dates), without needing to run a full
  validation or delivery.
- **Branch Transfer & Inbound Scanner** — For moving stock between two Meril
  locations: one side scans what's being sent, the other side scans what
  arrives, so nothing goes missing in transit without anyone noticing.
- **ZVA05 Auto-Assign** — Lets a picker start scanning against an order
  before the formal delivery paperwork even exists in SAP.
- **Invoice Consolidator** — Upload a batch of invoice PDFs or spreadsheets
  and get one combined, searchable table instead of opening each file by
  hand.
- **Live Monitoring** — See at a glance who is scanning right now, what
  they're working on, and whether they've gone idle — useful for a
  supervisor keeping an eye on the floor without walking around.
- **Master Data** *(admin only)* — Where materials, customers, couriers,
  cartons and business-unit settings are managed. Everyday users won't need
  this section.

**Typical day-to-day use, as a warehouse picker:**
1. Open **Meril-V** → pick the tool you need (usually **Outbound Scanner** or
   **Stock Validator**).
2. Select your business unit from the dropdown.
3. Upload the file you were given (delivery list or stock report).
4. Scan items with a barcode scanner or type the code — the screen tells you
   immediately whether each scan was accepted and why not if it wasn't.
5. When finished, close out the session and download the export if one is
   needed.

For a complete walk-through with every feature explained in detail — including
answers to common questions like "why was my scan rejected?" — see the full
[Meril-V Module Guide](docs/MERILV_MODULE_GUIDE.md).

---

### MeriTrack

**What it's for:** MeriTrack is a mobile-friendly tool that lets field sales
reps (and their managers/logistics teams) keep track of medical device stock
sitting "on consignment" inside hospitals — scanning it, checking expiry
dates, reordering, and moving it between sites — instead of relying on
manual paperwork.

**What you can do with it:**
- **Scanner** — point your phone/tablet camera at a product's barcode to
  check in, add, remove, or record the use of an item on a hospital shelf.
- **Dashboard** — activity numbers, scanning-trend charts, expiring products,
  and a list of all past visits ("sessions").
- **Session Details** — everything scanned in one visit; download it as a
  spreadsheet, or attach a signed confirmation photo/document.
- **Inventory** — a live view of what stock is currently in each
  hospital/department, and how close it is to expiring.
- **Orders** — request more stock for a hospital, mark urgency, and track it
  through to delivery.
- **Usage Analytics** — charts of scanning activity over time and across
  hospitals.
- **Hospital Master / Product Catalog** — reference lists that keep hospital
  and product data consistent (bulk upload/download supported).
- **Rep Performance Dashboard** — how much each rep has scanned and visited.
- **Hospital-to-Hospital Transfers** — a dedicated scanning flow for moving
  stock between hospitals.
- **Reconciliations** — automatic checks that flag mismatches between what's
  recorded and what's actually on the shelf.
- **Feedback & Support** — report bugs/issues and track your tickets.

**Typical day-to-day use:**
1. Open MeriTrack and land on the Scanner screen.
2. Pick the hospital and department you're visiting, and choose why you're
   scanning (audit, restock, remove expired items, or transfer stock).
3. Scan each product's barcode; the app fills in the details automatically.
4. Finish the visit, review the scanned items, and submit (optionally
   attaching a signed sheet).
5. If stock is running low, place a reorder from the Orders screen.
6. Check the Dashboard occasionally for expiry warnings or overall activity.

---

### Product Scanning

**What it's for:** Helps field sales reps and warehouse staff keep track of
medical device stock sitting inside hospitals, so nobody loses sight of
what's where, what's about to expire, and what's moved between locations.

**What you can do with it:**
- **Cardio Stock Audit (QR scanner)** — pick a hospital, then scan each
  product's barcode; details (serial number, batch, expiry, product code)
  are read automatically instead of typed in.
- **Audit Dashboard** — a live view of every scan session sent in by the
  field team, for warehouse staff to review and close out.
- **Audit Session Details** — everything scanned in one visit, with the
  ability to attach a confirmation document and mark the visit done.
- **Stock Transfers (H2H)** — a dedicated flow for moving stock from one
  hospital to another, with its own transfer ID and confirmation step.
- **Expired product alerts** — automatically flags any scanned item past its
  expiry date so the warehouse team can follow up (e.g. arrange a recall).
- **Hospital Master / GTIN Material Mapping** — reference lists linking
  hospitals and barcodes to Meril's internal product codes.
- **Salesperson Performance Dashboard** — how active each rep has been, with
  reminders about hospitals not visited recently.
- **Notifications & Remarks** — automatic alerts on submission/closure, and
  notes warehouse staff and reps can leave on a product or visit.
- **Feedback & Support** and a built-in **User Guide** for each user type.

**Typical day-to-day use:**
1. A rep arrives at a hospital and starts a new stock check (or a transfer).
2. They scan every product's barcode, adding a note if something looks off.
3. They submit the visit for warehouse review.
4. The warehouse team is notified, opens the dashboard, and reviews it —
   checking especially for anything expired.
5. The warehouse team uploads a confirmation document to close out the visit
   (or flags an issue via a remark).
6. Expired stock shows up in the expired-products list for follow-up.

---

### Nordic Scanning

**What it's for:** The same hospital stock-tracking job as Product Scanning
— recording what's on the shelf, moving stock between hospitals, and
flagging expired items via barcode/QR scans — run for the Nordic region.

**What you can do with it:**
- **Cardio Stock Audit** — a mobile-friendly scanner form: pick a hospital,
  start a session, and scan products one by one.
- **Audit Dashboard / Session Details** — review all scanning sessions
  across hospitals, attach a confirmation document, and exchange remarks
  between salesperson and warehouse.
- **SAP Reconciliation Export** — generate a downloadable file of scanned
  stock data for matching against SAP records.
- **Stock Transfers dashboard, New Transfer & Transfer Details** — track and
  run hospital-to-hospital stock moves.
- **Hospital Master / GTIN Material Mapping** — admin reference lists
  linking hospitals and barcodes to product codes.
- **Salesperson Performance Dashboard** — how active each salesperson has
  been with visits and audits.
- **Notifications & monthly reminders** — alerts to warehouse managers on
  submission, and monthly reminders to reps about unvisited hospitals.
- **Expired product tracking** — flags and lists anything past its expiry
  date.

**Typical day-to-day use:**
1. A salesperson visiting a hospital opens the scanning screen and selects
   that hospital.
2. They scan each product's barcode; batch number and expiry are captured
   automatically.
3. They submit ("complete") the session, notifying the warehouse team.
4. Warehouse staff review the session and upload a confirmation document to
   close it.
5. For stock moving between two hospitals, a "Transfer" is used instead.
6. Managers check the performance dashboard and expired-product list
   periodically; reps get a monthly reminder for unvisited hospitals.

---

### Warehouse Inspection

**What it's for:** The digital checklist tool warehouse staff use on a phone
or tablet to walk a building floor-by-floor, check off safety and condition
items, snap proof photos, and submit an official inspection record —
replacing paper inspection sheets with a fast, camera-verified process.

**What you can do with it:**
- **Select Warehouse/Building, Condition Category & Floor** — pick exactly
  what's being inspected, so only the relevant checklist items show up.
- **Inspection Checklist** — answer each item Yes/No, add comments, and take
  a live photo as proof where needed.
- **Submit Inspection** — a final check ensures everything required is
  filled in, and blocks duplicate or excess same-day submissions.
- **Inspection History/Reports** — look back at past inspections and
  outcomes.
- **Admin Dashboard** *(managers)* — set up warehouses, floors, condition
  categories and checklist questions, including bulk-importing a checklist
  from Excel.
- **Manage Warehouse Users** *(admin)* — control who can use the app and
  whether they're an inspector or an admin.
- **Email Notifications** — results and a summary are emailed automatically
  once submitted.

**Typical day-to-day use:**
1. Log in and open the inspection app on your phone/tablet.
2. Pick the warehouse building you're currently in.
3. Choose the inspection type (e.g. safety, housekeeping) and the floor.
4. Go through the checklist, marking Yes/No, adding notes and a photo where
   needed.
5. Submit once everything required is completed.
6. The result is recorded and emailed to the relevant person/team for
   follow-up.

---

### Material Rework

**What it's for:** Lets teams request, review, and track the return, rework,
or repositioning of materials/products — moving them through a chain of
approvals (PPIC → Head of Department → Production → Warehouse) until the
process is fully closed out and recorded.

**What you can do with it:**
- **Upload Data** — submit a new batch of items (via Excel) to start a
  rework request.
- **My Uploads** — track the live status of a batch you submitted.
- **PPIC Review** — the planning team reviews certain batches before they go
  to department approval.
- **HOD Inbox** — a Head of Department approves or rejects pending requests
  from their team.
- **Production Review** — production accepts items or flags remaining
  quantities as still pending.
- **Warehouse Pending** — warehouse confirms receipt of quantities
  production flagged.
- **Full Report** — a complete audit trail of every batch: quantities, who
  approved what, and current status.
- **Masters / User Management** *(admin)* — manage reference lists (e.g.
  verticals, departments) and assign staff roles.
- **Print Labels** — printable box/item labels for an approved batch.
- **Email Notifications** — automatically alerts the right people at each
  step.

**Typical day-to-day use:**
1. A staff member fills out an Excel sheet with item details and uploads it.
2. If the request came from Order Management, PPIC reviews it first.
3. The relevant Head of Department approves or rejects it from their inbox.
4. Production reviews the batch and accepts items or marks leftovers pending.
5. Warehouse confirms the pending items in the system.
6. The submitter checks "My Uploads" for status; managers pull the "Full
   Report" for the complete history.

---

### AI Sales Agent Dashboard

**What it's for:** The command center for an AI-powered hospital sales
program — lets the sales team see, at a glance, what a team of automated "AI
agents" is doing to find hospital leads, research them, reach out, and move
them through the sales pipeline, without digging through spreadsheets or
emails.

**What you can do with it:**
- **Intelligence Hub (home dashboard)** — an overview of all AI agents,
  grouped by sales stage (finding leads, researching, reaching out,
  following up, closing).
- **Eagle-Eye Workflow view** — a bird's-eye map of every agent and how they
  hand work to one another.
- **Agent detail page** — a single agent's recent activity, output, and
  success/failure track record.
- **Hospital Accounts list** — searchable/filterable, with Hot/Warm/Cold
  indicators and pipeline stage (new lead, qualified, demo, proposal,
  negotiation, closed).
- **Hospital Account detail page** — a full profile with contact info, deal
  size, notes, next steps, and a timeline of everything the AI agents have
  done for that account.
- **Add/Edit Hospital** — manually add or update a hospital account.
- **Lead Pipeline Detail page** — for AI-discovered leads, a deep-dive
  combining research notes, qualification score, email sequences, call
  insights, proposals and meeting bookings.

**Typical day-to-day use:**
1. Start on the Intelligence Hub to check overnight AI activity.
2. Filter Hospital Accounts to "Hot" leads to see who's worth calling today.
3. Open a promising hospital to review emails sent, research found, and
   what's scheduled next.
4. Add a new hospital via the Add Hospital form if one comes in elsewhere.
5. Check the Eagle-Eye view periodically to confirm the pipeline is flowing.
6. Update a hospital's notes/next action after a call or meeting.

---

### VendorLink

**What it's for:** The tool supply-chain and procurement staff use to find,
vet, and hire third-party logistics (3PL) warehouse and shipping partners
around the world, then compare their prices with AI help so the best-value
vendor wins the business.

**What you can do with it:**
- **Vendor Directory & Capability Matrix** — a searchable master list of
  approved vendors and a grid of which ones have which certifications
  (temperature-controlled storage, customs clearance, GDP/ISO, last-mile,
  etc.).
- **Vendor Registration (public form) & Registration Queue** — new vendors
  apply themselves; staff approve, reject, or bulk-process applications.
- **Raise a Request** — describe a need (warehouse space, storage
  conditions, shipment volumes) and it's emailed to every active vendor in a
  chosen country.
- **My Requests / Bid Window** — tracks each request sent out, how many
  vendors it reached, and the open/close dates for quotes ("bids").
- **Commercial Database (Bid Comparison)** — compares vendors' price quotes
  side by side, line item by line item.
- **AI Bid Evaluation** — one click ("AI Evaluate") has an AI review all
  quotes and capabilities and recommend the cheapest vendor that can still
  deliver everything asked, flagging any blacklisted or missing-service
  vendor.
- **Vendor Scorecards** — rate a vendor's facility quality, responsiveness,
  compliance and reliability (and blacklist if needed); feeds the AI
  recommendations.
- **Vendor Portal** — a separate login where approved vendors view open
  requests and submit quotes.
- **Admin Console & Users** — manage access and master lists.

**Typical day-to-day use:**
1. Click "Raise a Request" describing a new warehousing/logistics need; it's
   emailed to all active vendors in the relevant country.
2. Vendors submit price quotes through the Vendor Portal.
3. Open the Commercial Database to compare all vendors' prices side by side.
4. Click "AI Evaluate" for an instant, reasoned recommendation of the
   best-value vendor.
5. Review the AI's reasoning, check scorecards if needed, and select the
   winning bid.
6. Selected and non-selected vendors are automatically notified by email.

---

### Shipment Notifications

**What it's for:** Automatically watches export shipments from dispatch to
delivery and sends the responsible salesperson a WhatsApp alert whenever
something changes, with a personal link to a live tracking dashboard — so
sales staff always know the latest status without chasing anyone.

**What you can do with it:**
- **Automatic WhatsApp alerts** — sent whenever a shipment's status, dates,
  airline, forwarder, or remarks change.
- **Personal Shipment Dashboard** — a secure, no-login web page (opened from
  the WhatsApp link) listing all shipments assigned to that salesperson:
  AWB number, status, consignee, country, dates, airline/forwarder, remarks.
- **Search and filter tools** — by AWB, invoice, consignee, airline, status,
  or invoice date range.
- **Mobile-friendly view** — a card layout on phones; a sortable, exportable
  table (copy/CSV/Excel/PDF/print) on desktop.
- **Secure, self-expiring access links** — unique per phone number, stop
  working after 30 days.
- **Expired-link message** — a simple notice to contact operations for a
  fresh link.

**Typical day-to-day use:**
1. Each morning, the system checks for shipment updates.
2. If anything changed, the salesperson gets a WhatsApp message with the
   key details.
3. The message includes a personal link to their Shipment Dashboard.
4. Tapping it opens the dashboard, showing all their assigned shipments.
5. They search/filter to find the shipment mentioned, or check any other.
6. After 30 days an old link stops working, prompting a request for a fresh
   one from operations.

---

### Logistics Performance

**What it's for:** Helps logistics and sales teams track and analyze how
well shipments are being delivered — delivery times, costs, and courier
performance — so they can spot delays or overspending and keep deliveries on
track.

**What you can do with it:**
- **Home menu** — jump to any tool in the module.
- **Logistics Dashboard** — a quick overview of shipment health: delivery
  time alerts and recent activity.
- **Performance Upload** — add shipment records one at a time or in a batch
  (e.g. Excel), including reviewing records still missing information.
- **Performance Report** — charts and summaries of delivery speed, cost,
  courier reliability and pincode-level results, drillable and downloadable.
- **Shipment Detail page** — everything about one shipment.
- **Location, Sales Type, Courier, Division, Zone, Mode, State & Plant
  Masters** — reference lists that keep shipment data consistent and
  error-free.

**Typical day-to-day use:**
1. Open the module home page and pick the task you need.
2. Add shipment records manually, or upload a batch file.
3. Fix anything flagged as incomplete/mismatched using the relevant
   reference list, then re-submit.
4. Check the Logistics Dashboard for a quick daily read on performance.
5. Open the Performance Report to see delivery-time, cost and reliability
   trends, drilling into specifics as needed.
6. Export a report to share with management.

---

### Subsidiary Logistics Cost

**What it's for:** Lets managers and team members track and review how much
each country subsidiary spends on sales, outbound logistics, and day-to-day
operations, so leadership can compare costs across countries and time
periods at a glance.

**What you can do with it:**
- **Home page** — shows which areas you can access (Dashboard, and for
  managers, Upload and Template Download) along with your role.
- **Dashboard** — every country's total sales, outbound logistics cost, and
  operational cost, with summary totals; filterable by date.
- **Upload Data** *(managers)* — drag-and-drop an Excel file of new cost
  figures; the system previews every row, flags anything missing or wrong,
  and shows valid vs. error counts before saving.
- **Download Template** — a ready-made Excel file with the right column
  headings and example rows.

**Typical day-to-day use:**
1. Open the module and land on the home screen showing your options.
2. Click "Dashboard" to see the latest logistics costs for all countries.
3. If adding new figures, download the template first (if you don't have
   the format already).
4. Fill in the template and go to "Upload Data."
5. Drag in the completed file, review the preview and fix/re-upload any
   error rows, then save.
6. Return to the Dashboard to see the new figures reflected.

---

### Billing Dashboard

**What it's for:** Lets a finance or sales-ops person upload the monthly
export sales/billing report from SAP and instantly turn it into a visual
dashboard of revenue by region, business line, country, and subsidiary — no
spreadsheet wrangling required.

**What you can do with it:**
- **Upload SAP Billing Dump** — drop in the monthly Excel billing file; it's
  read, checked, and built into a fresh snapshot in the background.
- **Upload History** — every file ever uploaded, with rows added, updated,
  skipped, or needing attention, and a processing status.
- **Billing Dashboard** — total revenue, invoice/line counts, subsidiary
  splits, reversals, pending items, a monthly trend chart, region/line-of-
  business breakdowns, a world map of country revenue, and a month-by-region
  table.
- **Filters** — by financial year, region, line-of-business, billing type,
  subsidiary scope, or excluding reversed entries.
- **Billing Data (grid)** — a searchable table of every billing line, with
  "All", "Exceptions", "Reversals" and "Pending" tabs.
- **Field Mapping report** — shows exactly which spreadsheet column matched
  which field for a given upload.
- **Export to Excel** and a **Snapshot picker** to switch between monthly
  uploads.

**Typical day-to-day use:**
1. At month-end, upload the latest SAP billing export file.
2. The system processes it in the background and reports how many records
   were added versus need review.
3. Open the Dashboard to see total billing and regional/product breakdowns.
4. Apply filters to focus on what's needed.
5. Check "Exceptions" in Billing Data for anything that couldn't be
   auto-categorized.
6. Export the filtered data to Excel for sharing or further reporting.

---

### VAT Reconciliation

**What it's for:** Helps a finance/logistics team check that the VAT (tax)
charged on international purchase invoices matches what it should actually
be, so they can catch overcharges or errors before paying or filing.

**What you can do with it:**
- **Dashboard** — how many invoices are processed, matched, or flagged as
  mismatched, plus trends and live currency exchange rates.
- **Invoice Upload** — upload scanned/photographed invoices (PDF/JPG/PNG).
- **Invoice Result page** — the amounts, translated text (if needed),
  currency conversion, expected VAT calculation, and match/mismatch status.
- **Country Master** — countries dealt with, each with language, currency,
  and an allowed VAT-difference tolerance.
- **VAT Rate Master** — the correct VAT percentage per country (and per
  product code where needed).
- **CHA Email Master** — customs-agent contacts per country, for emailing
  reconciled invoices.
- **Send to CHA** — draft and send an invoice by email to the relevant
  customs agent.
- Bulk upload (with review) supported for all three master lists.

**Typical day-to-day use:**
1. Check the dashboard for pending and flagged invoices.
2. Upload a new invoice received from a supplier.
3. Run the check — the system reads, translates if needed, converts
   currency, and calculates the expected VAT.
4. Open the result page to see whether it matches or shows a discrepancy.
5. If correct, email it to the country's customs agent for approval.
6. An admin periodically updates the Country, VAT Rate, or CHA lists.

---

### Visa Management

**What it's for:** Helps a company manage employee business-visa
applications end-to-end — from an employee requesting a visa, to their
manager approving it, to the visa/admin team preparing and issuing the
official invitation letter.

**What you can do with it:**
- **New Visa Application** — an employee enters travel, passport, and host-
  company details.
- **My Applications** — employees see the status of every request they've
  submitted (draft, awaiting approval, approved, issued, rejected, etc.).
- **Pending Approvals** *(Manager/HOD)* — approve or reject requests with a
  reason, plus a history of past decisions.
- **Admin Dashboard** *(Visa Team)* — the central screen for tracking every
  application in progress.
- **Application detail & letter preparation** — review an application, edit
  the invitation letter text, preview/print, and issue or reject.
- **Letter Templates** — standard wording for invitation letters by country
  and company, kept consistent.
- **Country Master / Company Master** — destination countries and sponsoring
  company entities with the details needed on letters.
- **User Master** — assign Manager/Visa Team access, including bulk setup
  via spreadsheet.
- **Automatic email notifications** at every step.

**Typical day-to-day use:**
1. An employee opens "New Visa Application" and enters trip/passport
   details.
2. Their manager gets an email, opens "Pending Approvals," and decides.
3. If approved, the visa team sees it on their dashboard and opens it.
4. The visa team reviews/adjusts the letter and prints or issues it.
5. The employee and manager are emailed once issued (or if rejected).
6. The employee checks progress anytime under "My Applications."

---

### PR Tracker

**What it's for:** Tracks Purchase Requests (PRs) as they move through
procurement — from being raised, assigned to a buyer, through PO creation
and goods receipt, to closure — so managers always know who's handling what
and how long it's taking.

**What you can do with it:**
- **Dashboard** *(Manager)* — counts of PRs by status (Open, In Progress, PO
  Raised, GRN Created, Closed, Overdue), a searchable/filterable list, and a
  closure-ageing report per staff member.
- **Assign PRs** *(Manager)* — hand a PR to a staff member individually or
  via spreadsheet upload; the assignee is emailed automatically.
- **My Assigned PRs** *(Member)* — a personal worklist with status counts
  and bulk-update-by-spreadsheet support.
- **Fill PR Details** *(Member)* — record vendor info, PO number, amount,
  receipt status, closing notes, and update the PR's status.
- **PR Detail** *(Manager)* — a read-only view of everything recorded plus
  the full activity log.
- **Tracking Master / User Master** *(Admin)* — maintain allowed tracking
  numbers and staff roles, with bulk import.
- **Template downloads** for bulk assigning, filling details, and adding
  users.

**Typical day-to-day use:**
1. A manager assigns new PRs to staff (typed or via spreadsheet); the staff
   member is emailed.
2. The staff member checks "My Assigned PRs" for new tasks.
3. They fill in vendor status, PO number, amount, and receipt status, and
   update progress.
4. Once fully received, they mark the PR "Closed" (logged automatically).
5. The manager checks the Dashboard for overall progress and overdue items.
6. An admin occasionally adds staff or adjusts Manager/Member access.

---

### RS2T — LiveTranslate

**What it's for:** Lets anyone in a meeting or conversation listen to speech
in one language and see it translated into another almost instantly — useful
for teams working across language barriers, so no one is left out of a
conversation.

**What you can do with it:**
- **Live translation screen** — tap the microphone to start capturing speech
  (mic or shared system audio) and watch a live, scrolling transcript
  alongside its translation.
- **Language selector** — choose the target language (English, Hindi,
  Spanish, French, German, Chinese, Japanese, Arabic, Russian, Portuguese,
  Italian, Korean).
- **Automatic language detection** — figures out what's being spoken and
  shows it as small tags.
- **Speaker recognition** — tries to tell multiple speakers apart in the
  transcript.
- **Live stats panel** — words captured, speech segments, and translation
  speed.
- **Floating caption window** — a small pop-out window that stays on top of
  other apps (handy during video calls).
- **Session history** — your last 10 sessions, with date, duration, word
  count, speakers, and languages.
- **Session summary** — an automatic recap of what was discussed.
- **Download & delete sessions** — export as text/JSON, or remove.

**Typical day-to-day use:**
1. Pick the language to translate into from the dropdown.
2. Tap the microphone to start listening.
3. Watch live captions appear and scroll into the full transcript.
4. Pop out the floating caption window to keep it visible over a video call.
5. Tap the mic again to stop — the session is saved with a summary.
6. Later, open "Session History" to review, download, or delete sessions.

---

For more on how these modules were built and how they fit together
technically, see each app's own folder under the project root.
