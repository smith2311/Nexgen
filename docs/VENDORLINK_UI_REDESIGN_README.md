# VendorLink UI Redesign README

VendorLink is the 3PL vendor sourcing and bid comparison module inside NexGen.
This README is for redesign work. It documents how the UI currently appears,
where the experience feels fragmented, and how the redesigned UI should be
structured in a cleaner, more consistent way.

This is not a backend or Python implementation guide.

---

## 1. Purpose Of VendorLink

VendorLink helps Meril supply-chain and procurement teams manage third-party
logistics vendors from discovery to award.

The module covers:

- Approved vendor master data
- Vendor capabilities and certifications
- Vendor self-registration
- RFI / inquiry creation
- Vendor bid submission
- Bid comparison and awarding
- Commercial rate cards
- KPI performance tracking
- Vendor scorecards
- Admin user and master-data management
- Vendor-facing portal
- Geo Command Centre map
- MerilGPT bid/document assistance

The clean UI should make this full workflow feel like one connected procurement
workspace instead of a collection of separate pages.

---

## 2. Current UI Structure

### Main Entry

Current URL:

```text
/vendorlink/
```

Current behavior:

- Internal Meril users see the VendorLink launcher.
- Vendor users see the external vendor portal.
- Admin users see extra admin and registration sections.

### Current Internal Landing Page

The current landing page shows:

- Page title: `VendorLink`
- Short module description
- Admin Console banner for admins
- Geo Command Centre panel
- Pending registration alert
- Vendor registration KPI tiles
- Five module cards:
  - Vendor Master
  - Commercial Database
  - KPI Dashboard
  - Vendor Scorecard
  - Inquiries

Current visual character:

- Functional and data-rich
- Card-heavy
- Uses the VendorLink design system styling
- Several sections compete for attention
- Admin, map, registration, and module navigation all appear on the same page
- Useful, but visually busy

### Current Vendor Portal

External vendors see a separate portal-style page with:

- Top bar with `Meril VendorLink`
- Vendor greeting
- Company information
- Quote call-to-action
- Open requests awaiting quote
- Submitted bids
- Awarded requests
- Contact message for Meril SCM team

Current visual character:

- More focused than the internal UI
- Clear vendor-only scope
- Good separation between actions and history
- Should stay simpler than the internal workspace

---

## 3. Current Screen Groups

### Vendor Master

Current screens:

- Vendor Master home
- Vendor directory
- Add vendor
- Edit vendor
- Vendor detail
- Capability matrix
- Excel import/export

Current UI behavior:

- Users can browse vendors, filter by country, open detail pages, and edit
  records if they have permission.
- Capability data is shown both on vendor records and in a matrix.
- Vendor access can be granted from the vendor detail flow.

Current redesign need:

- Make the vendor directory feel like a clean master-data workspace.
- Keep filters visible and predictable.
- Make vendor detail easier to scan with clear sections:
  - Identity
  - Location
  - Contact
  - Capabilities
  - Commercial status
  - KPI / scorecard summary
  - Portal access

### Inquiries / Requests

Current screens:

- Request list
- Raise request form
- Request detail
- Bid submission page for vendors
- Attachments
- Compare / heatmap links

Current UI behavior:

- Internal users raise RFIs for a selected country.
- Active vendors in that country receive the inquiry.
- Vendors submit bids from the vendor portal.
- Staff compare bids and award a vendor.

Current redesign need:

- Treat this as the main workflow, not just another list.
- Request list should show status clearly:
  - Draft / sent
  - Bid window open
  - Bid window closed
  - Bids received
  - Awarded
  - Nominations closed
- Request detail should behave like a command page:
  - Request summary
  - Requirements
  - Attachments
  - Vendor responses
  - Bid comparison
  - AI evaluation
  - Award action

### Commercial Database / Bid Analytics

Current screens:

- Bid Analytics dashboard
- Bid detail
- Evaluation detail
- Bid comparison heatmap
- Request reports
- Monthly reports
- MerilGPT drawer

Current UI behavior:

- Users compare vendor quotes across RFIs.
- Heatmaps show price differences.
- AI evaluation recommends a vendor.
- MerilGPT answers questions about bids, documents, and reports.

Current redesign need:

- This area should feel like the decision cockpit.
- Keep the price comparison, AI evaluation, and award decision visually linked.
- Avoid hiding important outputs too deep inside accordions.
- Make the following immediately visible:
  - Lowest quote
  - Recommended vendor
  - Awarded vendor
  - Currency / INR conversion state
  - Missing capabilities or risks
  - Documents attached

### Commercial Rate Cards

Current screens:

- Rate-card list
- Create/edit rate card
- Rate-card detail
- Export PDF/Excel

Current UI behavior:

- Maintains standing commercial rates for vendors.
- Separate from live RFI bids.

Current redesign need:

- Clearly distinguish rate cards from RFI bids.
- Use calm table layout with expiry status.
- Show expired / expiring cards as priority indicators, not decorative badges.

### KPI Dashboard

Current screens:

- KPI dashboard
- New KPI record
- Edit KPI record
- Export

Current UI behavior:

- Tracks monthly vendor performance metrics.

Current redesign need:

- Make KPI trends easier to read.
- Show vendor, month, and metric health at a glance.
- Use consistent table and chart patterns.

### Vendor Scorecard

Current screens:

- Scorecard list
- New scorecard
- Vendor score history

Current UI behavior:

- Scores vendors on weighted criteria.
- Final score is computed from score inputs.

Current redesign need:

- Show score composition clearly.
- Make comments prominent because they explain the score.
- Add a clean vendor ranking view.

### Admin Console

Current screens:

- Admin console
- Users
- User detail
- Create user
- Masters
- Capability master
- Insights
- Historical data

Current UI behavior:

- Admin users manage access, roles, capabilities, activity, and exports.

Current redesign need:

- Admin should feel like a compact control center.
- Avoid mixing operational procurement work with admin configuration.
- Separate:
  - Users and roles
  - Master data
  - Activity insights
  - Historical exports

### Vendor Registration

Current screens:

- Public registration form
- Registration success page
- Registration admin queue
- Approve/reject flows

Current UI behavior:

- Vendors self-register publicly.
- Admin reviews registration.
- Approval can create the vendor record and portal access.

Current redesign need:

- Public form should feel trustworthy and simple.
- Admin queue should be review-oriented:
  - Pending
  - Approved
  - Rejected
  - Missing data
  - Duplicate risk

### Vendor Portal

Current screens:

- Vendor portal home
- Request list visible to vendor
- Request detail
- Bid submit
- Change password

Current UI behavior:

- Vendors only see RFIs relevant to them and their own bids.

Current redesign need:

- Keep vendor UI separate from internal UI.
- Make primary action obvious:
  - Quote open request
  - View submitted bid
  - See awarded request
- Avoid exposing internal analytics or admin terminology.

---

## 4. Current UI Problems To Solve

The current UI works, but the redesign should solve these experience issues:

- Too many visual systems exist side by side.
- Legacy pages and redesigned `/v2/` pages do not fully feel like one product.
- Landing page is useful but dense.
- Admin, registration, map, and module navigation compete for attention.
- Some workflows feel page-based instead of task-based.
- Bid evaluation and award decisions can feel buried.
- Vendor-facing UI is cleaner than some internal screens, but disconnected.
- Tables, cards, badges, buttons, and filters need stronger consistency.
- Important status information is sometimes shown as secondary text.
- AI/GPT features need clearer placement as decision support, not visual noise.

---

## 5. Target UI Direction

The redesigned VendorLink should feel like a professional procurement operating
system.

Target qualities:

- Clean
- Dense but readable
- Enterprise-grade
- Fast to scan
- Workflow-first
- Consistent across modules
- Clear separation between internal and vendor-facing views
- Minimal decoration
- Strong status hierarchy
- Tables for operational data
- Cards only where they represent meaningful grouped objects

Avoid:

- Marketing-style hero sections
- Large decorative gradients
- Too many nested cards
- Overuse of badges
- Hiding key actions below long content
- Making every module look visually unrelated

---

## 6. Proposed Clean Information Architecture

### Primary Internal Navigation

Recommended top-level VendorLink sections:

1. Dashboard
2. Vendors
3. Inquiries
4. Bid Comparison
5. Performance
6. Reports
7. Admin

### Dashboard

Purpose:

Show what needs attention today.

Recommended content:

- Open RFIs
- Bids awaiting review
- Pending awards
- Pending vendor registrations
- Expiring commercial rate cards
- Vendors with weak KPI / scorecard performance
- Quick actions:
  - Raise inquiry
  - Add vendor
  - Review registrations
  - Open bid comparison

### Vendors

Includes:

- Vendor directory
- Vendor detail
- Capability matrix
- Vendor import/export
- Vendor registration review

### Inquiries

Includes:

- RFI list
- Raise inquiry
- Request detail
- Vendor responses
- Attachments
- Award workflow

### Bid Comparison

Includes:

- Bid analytics
- Heatmap
- AI evaluation
- MerilGPT
- Bid detail
- Award decision support

### Performance

Includes:

- KPI dashboard
- KPI records
- Vendor scorecards
- Vendor ranking

### Reports

Includes:

- Request report
- Monthly report
- Bulk report
- Historical data exports

### Admin

Includes:

- Users
- Roles
- Capability master
- Other master data
- Insights
- System-level exports

---

## 7. Recommended Page Layout Pattern

Use one consistent page shell across internal VendorLink screens.

### Header Area

Each page should have:

- Breadcrumb
- Page title
- One-line description only when needed
- Primary action button
- Secondary actions grouped in a menu if there are many

Example:

```text
VendorLink / Inquiries

Inquiries
Track RFIs, vendor responses, bid windows and awards.

[Raise Inquiry] [Export] [More]
```

### Status Strip

Use a compact KPI/status row only when it helps the workflow.

Example for Inquiries:

```text
Open RFIs: 12 | Awaiting bids: 5 | Ready to award: 3 | Awarded this month: 8
```

### Filters

Filters should be:

- Always in the same position
- Collapsible only on small screens
- Applied visibly
- Easy to clear

Common filters:

- Search
- Country
- Continent
- Vendor
- Status
- Month
- Year
- Capability

### Main Content

Choose layout by task:

- Master data: table-first
- Workflow detail: summary + tabs
- Comparison: matrix / heatmap-first
- Dashboard: status widgets + priority lists
- Admin: compact grouped panels

### Detail Pages

Use tabs or clear sections:

- Overview
- Details
- Related records
- Attachments
- Activity / history

Avoid one long page where every section has the same visual weight.

---

## 8. Recommended Visual System

### Overall Style

Use a restrained enterprise UI style:

- White or near-white content background
- Dark text with strong hierarchy
- Subtle dividers
- Tables with clear row spacing
- Limited accent colors
- Status colors used consistently

### Color Semantics

Recommended meaning:

- Blue: primary actions / navigation
- Green: approved / awarded / healthy
- Amber: pending / expiring / needs attention
- Red: rejected / expired / risk
- Gray: neutral / inactive / metadata

Do not use color only for meaning. Pair color with text.

### Buttons

Button hierarchy:

- Primary: one per page section
- Secondary: normal actions
- Ghost/text: low-priority actions
- Danger: destructive actions only

Examples:

- Primary: `Raise Inquiry`, `Add Vendor`, `Award Bid`
- Secondary: `Export`, `Edit`, `View Report`
- Danger: `Delete`, `Reject`, `Withdraw Award`

### Tables

Tables should support:

- Search
- Filters
- Sort
- Clear row actions
- Empty state
- Export where relevant

Row actions should be predictable:

```text
View | Edit | More
```

### Cards

Use cards for:

- Dashboard priority items
- Vendor summary blocks
- Request summary blocks
- KPI summary blocks

Avoid:

- Cards inside cards
- Using cards for every paragraph
- Decorative card grids where a table would be easier to scan

---

## 9. Key Screens: Current Vs Target

### VendorLink Landing

Current:

- Shows admin banner, geo panel, registrations, and module cards together.
- Useful but crowded.

Target:

- Become an operational dashboard.
- Show what needs action first.
- Move broad module navigation into a clean internal navigation structure.
- Keep Geo Command Centre available, but not overpowering the primary workflow.

### Vendor Directory

Current:

- Table and filters.
- Add/import/export actions.

Target:

- Strong table-first master-data screen.
- Sticky filters.
- Clear vendor health/status indicators.
- Vendor detail opens into a structured page or drawer.

### Request List

Current:

- List of requests with filters and actions.

Target:

- Workflow board or table with strong statuses:
  - Bid open
  - Awaiting responses
  - Ready for comparison
  - Awarded
  - Closed

### Request Detail

Current:

- Shows request information and bid-related actions.

Target:

- Decision page with tabs:
  - Summary
  - Requirements
  - Vendor responses
  - Comparison
  - Award
  - Activity

### Bid Analytics

Current:

- Powerful but dense.
- Contains filters, grouped RFIs, heatmap, AI actions, MerilGPT.

Target:

- Make it the bid decision cockpit.
- Show top decision signals first.
- Keep MerilGPT available as a side panel.
- Clearly separate comparison data from AI recommendation.

### Admin Console

Current:

- Admin functions are available but spread across several screens.

Target:

- Compact admin hub.
- Separate user management, master data, and insights.
- Avoid mixing admin controls with procurement decisions.

### Vendor Portal

Current:

- Focused portal for vendors.

Target:

- Keep simple.
- Make open quote requests the first visible action.
- Keep submitted bids and awards easy to find.
- Maintain a different visual tone from internal screens, but keep brand
  consistency.

---

## 10. Suggested Redesign Priority

Recommended order:

1. Create a consistent VendorLink internal shell.
2. Redesign VendorLink landing into an action dashboard.
3. Redesign Vendor Directory and Vendor Detail.
4. Redesign Inquiries list and Request Detail.
5. Redesign Bid Analytics as a decision cockpit.
6. Redesign Admin Console.
7. Normalize Commercial, KPI, and Scorecard pages.
8. Refresh Vendor Portal last, keeping it simple.

This order is recommended because the landing, vendors, inquiries, and bid
comparison screens define the main user journey.

---

## 11. Main User Journeys To Preserve

### Internal Procurement Journey

```text
Open VendorLink
→ Check dashboard
→ Raise inquiry
→ Vendors submit bids
→ Compare bids
→ Run AI evaluation if needed
→ Award vendor
→ Generate report
```

### Vendor Onboarding Journey

```text
Vendor submits registration
→ Admin reviews registration
→ Admin approves
→ Vendor master record is created
→ Vendor receives portal access
```

### Vendor Response Journey

```text
Vendor logs in
→ Sees open requests
→ Opens request detail
→ Submits quote
→ Tracks submitted bid
→ Sees award status
```

### Performance Management Journey

```text
Open Performance
→ Review KPI dashboard
→ Add monthly KPI record
→ Create scorecard
→ Review vendor ranking
```

---

## 12. Clean UI Checklist

Use this checklist while redesigning each screen.

- Does the page have one clear primary purpose?
- Is the primary action obvious?
- Are statuses visible without opening detail pages?
- Are filters consistent with other VendorLink screens?
- Is the page table-first where data scanning matters?
- Are cards used only for meaningful grouped information?
- Are admin actions separated from operational actions?
- Is vendor-facing terminology simple and external-friendly?
- Are empty states useful?
- Are error states and permission states clear?
- Can a user understand the next step in under five seconds?

---

## 13. Recommended Final Experience

The redesigned VendorLink should feel like this:

- The landing page tells users what needs attention.
- Vendor data is easy to search, trust, and maintain.
- RFIs feel like tracked workflows, not isolated records.
- Bid comparison clearly supports a commercial decision.
- AI assistance helps explain and compare, without taking over the UI.
- Admin work is compact and separate.
- Vendors have a simple portal that only shows what matters to them.

The goal is not to make VendorLink look decorative. The goal is to make it feel
fast, confident, and operationally clear.
