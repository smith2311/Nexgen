# MeriTrack — User Guide

MeriTrack is a smart consignment management system for Meril medical devices.
It helps sales reps audit, scan, and manage consigned stock at hospital sites.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Scanner App](#scanner-app)
3. [Dashboard](#dashboard)
4. [Session Management](#session-management)
5. [Product Scanning](#product-scanning)
6. [CSV Export](#csv-export)
7. [Hospital & Department Management](#hospital--department-management)
8. [Product Catalog](#product-catalog)
9. [Stock Orders](#stock-orders)
10. [Activity & Audit Logs](#activity--audit-logs)
11. [Roles & Permissions](#roles--permissions)
12. [Troubleshooting](#troubleshooting)

---

## Getting Started

### Accessing the App

- Open your browser and go to `https://gpt.e-meril.in/meritrack/`
- Log in with your NexGen credentials
- You will see the **MeriTrack Scanner** page

### What You Can Do

| Role | Capabilities |
|------|-------------|
| **MT Rep** (Sales Rep) | Scan products, view own sessions, create orders |
| **MT Manager** | Full access: manage hospitals, force-complete sessions, view all data |
| **MT Logistics** | View orders, update order statuses |
| **Superuser** | Everything |

---

## Scanner App

The Scanner is the main tool for field reps. It follows a **3-step workflow**:

### Step 1: Setup

1. **Select Hospital** — Choose from the dropdown (filtered by your assigned hospitals)
2. **Select Department** — Optional; departments auto-load when you pick a hospital
3. **Session Type** — Choose one:
   - **Stock Audit** — Verify what's on the shelf
   - **Replenishment** — Add new products to shelf
   - **Expired Removal** — Remove expired products
   - **Hospital Transfer** — Transfer stock between hospitals
4. Click **Start Scanning**

### Step 2: Scan Products

- The camera opens automatically
- Point at any **QR code**, **DataMatrix**, **Code 128**, or **EAN barcode**
- Products are automatically parsed and logged
- You can change the **Action** during scanning:
  - **Verify** — Confirm product is on shelf
  - **Add** — Place new product on shelf
  - **Remove Expired** — Remove expired item
  - **Use** — Mark as used on patient

#### 2-Scan Workflow

Some products have separate barcodes for serial number and product details:

1. Scan the **serial number** barcode first (held in memory)
2. Scan the **product details** barcode (GTIN, batch, expiry)
3. Both are automatically merged

#### Manual Entry

If a barcode won't scan:
- Use the **Manual QR Input** field to paste raw barcode data
- Or use the **Manual Fields** form to type serial, GTIN, batch, expiry individually

#### Tips for Faster Scanning

- Hold the device **steady** at 15-20 cm distance
- Ensure **good lighting** — use the torch button if needed
- For Code 128 barcodes, hold steady for ~1 second (requires double-read confirmation)
- Use **pinch zoom** (two fingers) on the camera preview to zoom in

### Step 3: Review & Submit

1. Click **Finish Scanning** to see a summary
2. Review the product list
3. Click **Submit Session** to save
4. Optionally upload a confirmation document (photo/PDF)

---

## Dashboard

Access via the **Dashboard** button on the scanner page.

### KPI Cards

- Total sessions, products scanned, hospitals covered
- Expiry alerts breakdown (6M / 3M / 1M / 1W / Expired)

### Charts

- Monthly scanning activity
- Products by category
- Expiry timeline
- Hospital-wise distribution

### Session List

- View all sessions with filters (status, type, date range)
- Click a session to see its detail page

---

## Session Management

### Session States

| Status | Meaning |
|--------|---------|
| **Active** | Session in progress, can still scan |
| **Completed** | All scanning done, awaiting attachment |
| **Closed** | Attachment uploaded, session finalized |
| **Cancelled** | Session terminated/cancelled |

### Session Key Format

Each session gets a unique key: `MT-DDMMYY-INITIALS-SAPCODE-HHMMSS`

Example: `MT-010426-MK-SAP001-143025` means:
- Date: 01 Apr 2026
- Rep initials: MK
- Hospital SAP code: SAP001
- Time: 14:30:25

### Manager Actions

Managers can:
- **Force Complete** — Complete any session
- **Reject** — Send a completed session back to active (removes attachment)
- **Terminate** — Cancel an active session

---

## Product Scanning

### Supported Barcode Formats

- **QR Code** — Standard 2D QR
- **GS1 DataMatrix** — Medical device standard with AI codes
- **Code 128** — Linear barcode (common for serial numbers)
- **EAN-13 / EAN-8** — Product identification
- **UPC-A / UPC-E** — Product identification
- **PDF 417** — 2D stacked barcode
- **Code 39 / Code 93** — Industrial barcodes
- **ITF / Codabar** — Numeric barcodes

### GS1 Data Parsed

The system automatically extracts:

| GS1 AI | Field | Example |
|--------|-------|---------|
| (01) | GTIN Code | 08901234567890 |
| (10) | Batch/Lot Number | LOT2024A |
| (17) | Expiry Date | 2026-12-31 |
| (11) | Production Date | 2024-06-15 |
| (21) | Serial Number | SN001234 |
| (240) | Additional ID / Reference | REF-A1234 |
| (22) | Secondary Data / Reference | SEC-DATA |
| (30) | Quantity | 10 |

### Reference Numbers

When a QR code contains a reference number (GS1 AI 240 or AI 22), it is automatically:
- Captured from the barcode
- Stored in the database
- Displayed in session detail view
- Included in CSV exports

---

## CSV Export

### From Session Detail Page

1. Open any session detail page
2. Click the **Export CSV** button
3. A CSV file downloads with all scan data

### CSV Columns

Session Key, Session Type, Hospital, Department, Rep, Serial Number, GTIN Code, Material Code, Product Name, Batch Number, Reference Number, Expiry Date, Production Date, Action, Scanned At

### GTIN Preservation

- GTIN codes are properly quoted in CSV to prevent Excel from stripping leading zeros
- Files include UTF-8 BOM for correct character encoding in Excel

---

## Hospital & Department Management

### Hospital Master

- Access: Dashboard → Hospital Master tab
- **Import**: Upload a CSV file with hospital data
- **Export Template**: Download the CSV template for bulk import

### CSV Template Columns

name, sap_code, country, province, city, address, contact_person, phone, email, salesperson_email, plant

### Departments

- Departments are created per hospital
- Used for organizing consigned stock by ward/location
- Auto-loaded when selecting a hospital in the scanner

---

## Product Catalog

### Accessing

Dashboard → Product Catalog tab

### Features

- View all Meril products with material codes, GTIN, descriptions
- Search by code, GTIN, or description
- Import product catalog via CSV

### Auto-Mapping

When scanning a product, if the GTIN matches a catalog entry:
- Product name auto-fills
- Material code auto-maps
- This saves time during scanning

---

## Stock Orders

### Creating an Order

1. From the dashboard, go to **Orders**
2. Select hospital and department
3. Add products from the catalog
4. Set urgency (Normal / Urgent / Critical)
5. Submit

### Order Statuses

Draft → Submitted → Confirmed → Shipped → Delivered

Managers and logistics can update order statuses.

---

## Activity & Audit Logs

### What Gets Logged

Every action in MeriTrack is recorded in the audit log:

- Session started/completed/terminated/rejected
- Product scanned/added/removed/used/transferred
- Orders placed/updated
- Expiry alerts sent/acknowledged
- Attachments uploaded
- Scan deletions

### Viewing Logs

Managers can view audit logs in the dashboard to track all system activity.

### Log Fields

- **User** — Who performed the action
- **Action** — What was done
- **Description** — Details of the action
- **IP Address** — Where the request came from
- **Timestamp** — When it happened
- **Old/New Status** — Status changes

---

## Roles & Permissions

### Available Roles

| Role | Description |
|------|-------------|
| **MT Rep** | Field sales representative — scan, audit, create basic orders |
| **MT Manager** | Full management — all hospitals, force-complete sessions, manage users |
| **MT Logistics** | Order management — update shipment statuses |
| **Product Scanner** | Basic scanning access |
| **Sales** | Sales team member with scanning access |

### Hospital Visibility

- **MT Rep**: Only sees hospitals where `salesperson_email` matches their email
- **MT Manager / Superuser**: Sees all hospitals

---

## Troubleshooting

### Camera Not Working

- Ensure you granted camera permission in your browser
- Use HTTPS (camera requires secure context)
- Try refreshing the page
- Check if another app is using the camera

### Barcode Not Scanning

- Ensure good lighting
- Hold device steady at 15-20 cm
- Try the **Manual QR Input** field
- For damaged barcodes, use **Manual Fields** entry

### Session Already Exists Error

- Session keys are unique per person/hospital/time
- If you get a duplicate error, wait a few seconds and try again

### Products Not Showing in CSV

- Ensure the session is completed
- Check that products were actually scanned (visible in session detail)
- GTIN codes require the barcode to contain GS1 AI (01) data

### Slow Scanning Performance

- Reduce ambient light glare on the barcode
- Keep the scan area clean (within the square box)
- Close other camera-using apps
- For Code 128, wait for the confirmation beep (double-read verification)
