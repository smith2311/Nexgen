# Product Scanning Application - Stakeholder Guide

**Version 1.0**  
**Date: May 2026**  
**NexGen E-Meril Platform**

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Problem & Solution](#business-problem--solution)
3. [Key Features](#key-features)
4. [User Roles & Access](#user-roles--access)
5. [How It Works](#how-it-works)
6. [Use Cases](#use-cases)
7. [Benefits & Value Proposition](#benefits--value-proposition)
8. [Technical Overview](#technical-overview)
9. [Getting Started](#getting-started)
10. [FAQs](#faqs)

---

## Executive Summary

The **Product Scanning Application** is a comprehensive inventory tracking and management system designed for Meril Life Sciences to monitor medical device inventory across hospitals and distribution channels. The application enables real-time tracking of products through QR code scanning, providing complete visibility of stock location, expiry dates, and product movement between hospitals.

### At a Glance

- **Purpose**: Track medical device inventory at hospitals using GS1-compliant QR code scanning
- **Users**: Sales personnel (field scanners) and warehouse managers (dashboard operators)
- **Primary Functions**: Stock audits, expired product detection, hospital-to-hospital transfers
- **Technology**: AI-powered GS1 barcode parser with OpenAI enhancement
- **Impact**: Eliminates manual tracking, reduces expired stock, improves inventory visibility

---

## Business Problem & Solution

### The Problem

Medical device companies face critical challenges in inventory management:

1. **Lack of Visibility**: No real-time knowledge of which products are at which hospitals
2. **Expired Products**: Medical devices expire and need tracking to prevent usage beyond expiry dates
3. **Stock Movement**: Hospital-to-hospital transfers are difficult to track and verify
4. **Manual Processes**: Paper-based inventory tracking is error-prone and time-consuming
5. **Compliance Risk**: Regulatory requirements demand accurate product tracking and expiry management
6. **Lost Revenue**: Products lost in the field or expired at hospitals represent financial loss

### The Solution

The Product Scanning Application provides:

- **Digital Scanning**: QR code-based product scanning using mobile devices or desktop cameras
- **Automated Tracking**: Automatic capture of product details (serial number, batch, expiry date, material code)
- **Smart Parsing**: AI-powered GS1 barcode parser that extracts all product information automatically
- **Real-Time Visibility**: Dashboard showing all scanned products, their locations, and status
- **Expiry Monitoring**: Automatic detection and reporting of expired or near-expiry products
- **Transfer Tracking**: Complete audit trail of stock movements between hospitals
- **Role-Based Access**: Separate interfaces for field personnel and warehouse managers

---

## Key Features

### 1. **QR Code Scanning with GS1 Support**

- Scans GS1 DataMatrix and standard barcodes
- Automatically extracts:
  - Serial Number (AI 21)
  - GTIN/Material Code (AI 01)
  - Batch/Lot Number (AI 10)
  - Expiry Date (AI 17)
  - Production Date (AI 11)
  - Best Before Date (AI 15)
  - Packaging Date (AI 13)
  - Reference Numbers (AI 240/22)
- AI-powered parsing fills missing fields using OpenAI
- Fallback regex parsing for offline scenarios

### 2. **Session-Based Scanning**

- Create scanning sessions tied to specific hospitals and batches
- Two session types:
  - **Normal Scan**: Stock audit at a hospital
  - **H2H Transfer**: Hospital-to-hospital stock movement
- Scan multiple products without re-entering hospital/batch information
- Session states: Active → Completed → Closed
- Duplicate prevention (same serial within 30 seconds)

### 3. **Hospital & Batch Management**

- Master data for hospitals with SAP integration
- Hospital details: Name, SAP code, province, location, contact person, salesperson
- Batch grouping for organizing multiple scanning sessions
- GTIN-to-Material mapping for automatic material code lookup

### 4. **Expired Product Reporting**

- Automatic detection of expired products during session completion
- Consolidated expired product report with:
  - Days expired calculation
  - Hospital and session context
  - Acknowledgment workflow for warehouse team
- Email notifications to warehouse managers

### 5. **Hospital-to-Hospital (H2H) Stock Transfer**

- Dedicated transfer workflow separate from normal scanning
- Track stock movement from source to destination hospital
- Transfer ID format: `DDMMYY-Initials-FROM-TO-TOCODE`
- Complete transfer history and audit trail
- Confirmation attachment upload (delivery documents, receipts)

### 6. **Dashboard & Reporting**

**For Warehouse Managers:**
- View all submitted scanning sessions
- Session details with product lists
- Expired product reports
- Upload confirmation attachments
- Terminate/force-complete sessions if needed
- Export session data to Excel
- H2H transfer tracking dashboard

**For Salespersons:**
- Personal dashboard showing their sessions
- Session status tracking
- Ability to complete and submit sessions

### 7. **Notifications & Alerts**

- In-app notifications for warehouse managers
- Email alerts when sessions are submitted
- Email notifications when sessions are terminated
- Warehouse remark notifications
- Configurable recipient lists

### 8. **Audit Trail & Compliance**

- Complete audit log of all actions:
  - Session started/completed/terminated
  - Products scanned/removed
  - Attachments uploaded
  - Session exports
- Track who did what, when, and from where (IP address)
- User agent tracking for security
- Immutable history for regulatory compliance

### 9. **Remarks & Comments**

- Add remarks to individual scanned products
- Add remarks to entire scanning sessions
- Warehouse manager can add follow-up remarks
- Automated email notifications when warehouse adds remarks

### 10. **Master Data Management**

- Hospital master with full CRUD operations
- GTIN-Material mapping upload (Excel)
- Bulk import capabilities
- SAP integration ready

---

## User Roles & Access

The application implements role-based access control (RBAC) with three primary roles:

### 1. **PS Salesperson** (Field Scanner)
**Access**: QR Scanner Interface Only

**Responsibilities:**
- Create scanning sessions at hospitals
- Scan product QR codes
- Complete and submit sessions
- View their own session history

**Typical Users**: Field sales representatives, territory managers

---

### 2. **PS Warehouse** (Dashboard Operator)
**Access**: Dashboard Interface Only

**Responsibilities:**
- Review submitted scanning sessions
- View all products scanned across sessions
- Upload confirmation attachments to close sessions
- Monitor expired product reports
- Acknowledge expired products
- Terminate sessions if needed
- Export data for reporting

**Typical Users**: Warehouse managers, inventory managers, operations team

---

### 3. **Product Scanner** (Admin/Full Access)
**Access**: Both Scanner + Dashboard

**Responsibilities:**
- All scanner functions
- All dashboard functions
- Manage master data (hospitals, GTIN mappings)
- Force-complete sessions
- Full administrative control

**Typical Users**: Product managers, system administrators, senior operations staff

---

### 4. **Sales** (Legacy Full Access)
**Access**: Both Scanner + Dashboard (backward compatibility)

---

### 5. **Superuser**
**Access**: Full system access including Django admin

---

## How It Works

### Workflow 1: Stock Audit at Hospital (Normal Scan)

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Salesperson visits hospital                              │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Creates new scanning session                             │
│    - Selects hospital                                        │
│    - Selects/creates batch ID                                │
│    - Session type: Normal Scan                               │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Scans product QR codes                                   │
│    - App auto-extracts serial, batch, expiry, material code │
│    - Can add manual notes/remarks                            │
│    - Scans multiple products in one session                  │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Completes session                                         │
│    - Reviews scanned products                                │
│    - Submits for warehouse review                            │
│    - Status: Active → Completed                              │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Warehouse receives notification                           │
│    - Email + in-app notification sent                        │
│    - Reviews session in dashboard                            │
│    - Checks for expired products                             │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. Warehouse uploads confirmation attachment                 │
│    - Uploads proof document (PDF, image)                     │
│    - Session status: Completed → Closed                      │
│    - Workflow complete                                       │
└─────────────────────────────────────────────────────────────┘
```

---

### Workflow 2: Hospital-to-Hospital Transfer (H2H)

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Salesperson initiates transfer                            │
│    - Source hospital (FROM): Stock leaving                   │
│    - Destination hospital (TO): Stock receiving              │
│    - Auto-generated Transfer ID                              │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Scans products being transferred                          │
│    - Same QR scanning process                                │
│    - Products linked to transfer session                     │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Completes transfer session                                │
│    - Submits to warehouse for confirmation                   │
│    - Notification sent to warehouse team                     │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Warehouse confirms transfer                               │
│    - Reviews transferred products                            │
│    - Uploads delivery confirmation document                  │
│    - Transfer status: Completed → Closed                     │
└─────────────────────────────────────────────────────────────┘
```

---

### Workflow 3: Expired Product Detection

```
┌─────────────────────────────────────────────────────────────┐
│ During Scanning: Product with past expiry date detected     │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ On Session Completion: Auto-create expired product report   │
│    - Record added to Expired Product Reports                │
│    - Days expired calculated                                 │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Warehouse Dashboard: View expired products section           │
│    - Filter by hospital, date range                          │
│    - Sort by days expired                                    │
│    - View hospital and salesperson details                   │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Warehouse Action: Acknowledge expired product                │
│    - Mark as acknowledged                                    │
│    - Add action notes (recalled, destroyed, etc.)            │
│    - Timestamp and user tracking                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Use Cases

### Use Case 1: Monthly Hospital Stock Audit

**Scenario**: Sales team conducts monthly inventory audit at City Hospital

**Steps**:
1. Salesperson arrives at hospital with mobile device
2. Opens Product Scanner app, creates new session
3. Selects "City Hospital" and batch "MAY2026-AUDIT"
4. Walks through hospital storage, scanning all Meril products
5. App auto-captures: Serial numbers, expiry dates, batch numbers
6. Completes session with 147 products scanned
7. Warehouse receives notification, reviews inventory
8. Identifies 3 products expired, initiates recall
9. Uploads confirmation, closes session

**Outcome**: Complete inventory visibility, expired stock identified and removed

---

### Use Case 2: Emergency Stock Transfer

**Scenario**: Hospital A has excess catheters, Hospital B needs them urgently

**Steps**:
1. Salesperson creates H2H transfer: Hospital A → Hospital B
2. Scans 25 catheters being transferred
3. Submits transfer session
4. Products physically moved to Hospital B
5. Warehouse confirms receipt with delivery document
6. Transfer marked as closed

**Outcome**: Stock movement tracked, both hospitals' inventories updated, audit trail maintained

---

### Use Case 3: Expired Product Recall

**Scenario**: Warehouse identifies 6-month-old scanning sessions with expired products

**Steps**:
1. Warehouse opens Expired Products Report dashboard
2. Filters by date range: Last 6 months
3. Exports report to Excel
4. Identifies 23 expired products across 5 hospitals
5. Coordinates with sales team for product recall
6. Marks products as acknowledged with recall notes

**Outcome**: Proactive expired product management, compliance maintained

---

### Use Case 4: New Product Launch Distribution

**Scenario**: Meril launches new stent, distributed to 50 hospitals

**Steps**:
1. Sales team receives batch IDs for the launch: "STENT-2026-LAUNCH"
2. Each salesperson scans delivered products at their hospitals
3. All sessions linked to same batch ID
4. Warehouse dashboard shows real-time distribution status
5. Management sees: 50 hospitals, 2,300 stents, all sessions closed

**Outcome**: Launch tracking, distribution visibility, proof of delivery

---

## Benefits & Value Proposition

### For Sales Team
- ✅ **Faster scanning**: No manual data entry, automatic field extraction
- ✅ **Mobile-friendly**: Scan from anywhere using phone camera
- ✅ **Error reduction**: AI-powered parsing minimizes mistakes
- ✅ **Session history**: Track all past scans and sessions
- ✅ **Proof of work**: Digital record of hospital visits and stock checks

### For Warehouse/Operations
- ✅ **Real-time visibility**: Know exactly what's where at any time
- ✅ **Expired product alerts**: Automatic detection and reporting
- ✅ **Audit trail**: Complete history of all stock movements
- ✅ **Data export**: Excel exports for analysis and reporting
- ✅ **Workflow automation**: Email notifications, automated status updates
- ✅ **Transfer tracking**: H2H stock movement visibility

### For Management
- ✅ **Inventory insights**: Data-driven decisions on stock allocation
- ✅ **Compliance**: Regulatory-compliant product tracking
- ✅ **Cost reduction**: Minimize expired stock losses
- ✅ **Performance metrics**: Track scanning activity by salesperson, hospital, region
- ✅ **Scalability**: System handles growing product and hospital volume

### For the Business
- 💰 **Reduced Losses**: Less expired stock = less write-offs
- 📊 **Better Forecasting**: Historical data enables demand prediction
- ⚡ **Faster Recalls**: Quick identification of affected products
- 🔒 **Risk Mitigation**: Compliance with medical device regulations
- 🎯 **Customer Satisfaction**: Hospitals trust accurate inventory management

---

## Technical Overview

### Technology Stack

**Backend**:
- Django 4.x (Python web framework)
- PostgreSQL database
- Django REST Framework for APIs

**Frontend**:
- HTML5, CSS3, JavaScript
- Bootstrap for responsive UI
- AJAX for real-time interactions

**AI/ML**:
- OpenAI GPT integration for GS1 barcode enhancement
- Custom GS1 Application Identifier parser
- Regex fallback for offline scenarios

**Infrastructure**:
- Docker containerization
- Linux server deployment
- Email service integration

### Data Model

**Core Entities**:
- `Hospital`: Hospital master data with SAP codes
- `Batch`: Grouping mechanism for sessions
- `ScanningSession`: A scanning event at a hospital
- `ScannedProduct`: Individual product scan record
- `TransferSession`: H2H stock transfer
- `ExpiredProductReport`: Auto-generated expired product records
- `GTINMaterialMapping`: GTIN to SAP material code lookup
- `AuditLog`: Complete audit trail

### Security Features

- Role-based access control (RBAC)
- Django authentication & authorization
- IP address tracking
- User agent logging
- Session security with unique keys
- XSS and CSRF protection

### Integration Points

- **SAP**: Hospital SAP codes, Material codes ready for integration
- **Email**: SMTP integration for notifications
- **Export**: Excel/CSV export capabilities
- **OpenAI**: Optional AI-powered parsing enhancement

---

## Getting Started

### For Salespersons (Scanner Access)

**Step 1: Access the Application**
- Navigate to: `https://nexgen.e-meril.in/product_scanning/`
- Login with your credentials
- You'll see the QR Scanning Form

**Step 2: Create a Session**
1. Select Hospital from dropdown
2. Enter or select Batch ID (e.g., `MAY2026-AUDIT-01`)
3. Session Type: Select "Normal Scan" (or "Transfer" for H2H)
4. For transfers, also select "To Hospital"
5. Click "Start Scanning Session"

**Step 3: Scan Products**
1. Click "Scan QR Code" to activate camera
2. Point camera at product QR code/barcode
3. App auto-fills: Serial, Batch, Expiry, Material Code
4. Verify information, add notes if needed
5. Click "Add Product"
6. Repeat for all products

**Step 4: Complete Session**
1. Review all scanned products in the list
2. Click "Complete Session"
3. Confirm submission
4. You're done! Warehouse team will be notified

### For Warehouse Managers (Dashboard Access)

**Step 1: Access Dashboard**
- Navigate to: `https://nexgen.e-meril.in/product_scanning/dashboard/`
- Login with your credentials

**Step 2: Review Sessions**
1. View "Pending Sessions" (status: Completed)
2. Click on a session to view details
3. Review all scanned products
4. Check for expired products (highlighted in red)

**Step 3: Close Sessions**
1. Upload confirmation attachment (PDF/image)
2. Add notes if needed
3. Click "Upload & Close Session"
4. Session status changes to "Closed"

**Step 4: Manage Expired Products**
1. Navigate to "Expired Products Report"
2. Review list of expired items
3. Click "Acknowledge" on each item
4. Add action notes (recalled, disposed, etc.)

**Step 5: Track H2H Transfers**
1. Navigate to "Transfer Dashboard"
2. View all hospital-to-hospital transfers
3. Review transfer details and products
4. Upload delivery confirmation
5. Mark transfer as closed

### For Administrators (Full Access)

**Additional Capabilities**:
- Manage Hospital Master: `/product_scanning/dashboard/hospitals/`
- Upload GTIN Mappings: `/product_scanning/dashboard/gtin-material-master/`
- Force-complete sessions
- Terminate sessions
- View complete audit logs
- Export data for analysis

---

## FAQs

### General Questions

**Q: What types of barcodes does the system support?**  
A: The system supports GS1 DataMatrix, GS1-128, QR codes, and standard barcodes. It can parse GS1 Application Identifiers (AIs) automatically.

**Q: Can I scan offline?**  
A: The app requires internet connection for full functionality. However, the GS1 parser has regex fallback that works without OpenAI.

**Q: What if the QR code doesn't scan properly?**  
A: You can manually enter the data. The app also shows the raw QR data for verification.

**Q: How many products can I scan in one session?**  
A: There's no hard limit. Sessions with hundreds of products have been tested successfully.

---

### For Salespersons

**Q: Can I edit a scanned product after adding it?**  
A: Yes, you can delete and re-scan, or add remarks. Once submitted, warehouse team can also add remarks.

**Q: What happens if I accidentally close the browser during scanning?**  
A: The session remains active. Use the session key to resume or start a new session.

**Q: Can I scan the same serial number twice?**  
A: The system prevents duplicate scans within 30 seconds. Legitimate duplicates (same SN on different dates) are allowed.

**Q: Do I need to upload attachments?**  
A: No, salespersons submit sessions. Warehouse team uploads confirmation attachments to close them.

---

### For Warehouse Managers

**Q: How do I know when a session is submitted?**  
A: You receive both an email notification and an in-app notification.

**Q: Can I reject a session?**  
A: Yes, you can add remarks or terminate a session if needed. The salesperson will be notified.

**Q: What file types can I upload as attachments?**  
A: PDF, images (JPG, PNG), and common document formats. Files are stored securely in the database.

**Q: How do I export data for reporting?**  
A: Each session detail page has an "Export to Excel" button. You can also export expired product reports.

---

### Technical Questions

**Q: Is the data backed up?**  
A: Yes, the PostgreSQL database has regular automated backups.

**Q: What about data privacy and security?**  
A: The system follows industry standards: encrypted connections (HTTPS), role-based access, audit trails, and secure authentication.

**Q: Can the system integrate with our ERP/SAP?**  
A: The data model includes SAP codes for hospitals and materials. API integration can be developed.

**Q: What happens to data of closed sessions?**  
A: Data is retained indefinitely for compliance and historical reporting. Closed sessions cannot be modified.

---

## Support & Contact

For technical support, feature requests, or questions:

**Development Team**: NexGen E-Meril Platform  
**Email**: support@e-meril.in  
**Documentation**: `/docs/` folder in the project repository

---

## Appendix A: GS1 Application Identifiers Reference

The system automatically parses these GS1 AIs from barcodes:

| AI  | Description                | Example            |
|-----|----------------------------|--------------------|
| 01  | GTIN (Product Code)        | 00012345678905     |
| 10  | Batch/Lot Number           | BATCH2026A         |
| 11  | Production Date (YYMMDD)   | 260507 (7 May 26)  |
| 13  | Packaging Date (YYMMDD)    | 260430             |
| 15  | Best Before Date (YYMMDD)  | 280507             |
| 17  | Expiry Date (YYMMDD)       | 270507             |
| 21  | Serial Number              | SN123456789        |
| 22  | Secondary Data             | REF-12345          |
| 240 | Additional Product Info    | Additional ID      |

---

## Appendix B: Session Status Lifecycle

```
ACTIVE
  ↓
  User clicks "Complete Session"
  ↓
COMPLETED (Pending warehouse review)
  ↓
  Warehouse uploads attachment
  ↓
CLOSED (Workflow complete)

Alternative paths:
ACTIVE → CANCELLED (User cancels before completion)
ACTIVE/COMPLETED → TERMINATED (Warehouse terminates session)
```

---

## Appendix C: User Permission Matrix

| Function                    | PS Salesperson | PS Warehouse | Product Scanner | Superuser |
|-----------------------------|----------------|--------------|-----------------|-----------|
| Scan QR Codes               | ✅             | ❌           | ✅              | ✅        |
| Create Sessions             | ✅             | ❌           | ✅              | ✅        |
| Complete Sessions           | ✅             | ❌           | ✅              | ✅        |
| View Dashboard              | ❌             | ✅           | ✅              | ✅        |
| Upload Attachments          | ❌             | ✅           | ✅              | ✅        |
| Terminate Sessions          | ❌             | ✅           | ✅              | ✅        |
| Manage Hospitals            | ❌             | ✅           | ✅              | ✅        |
| Upload GTIN Mappings        | ❌             | ✅           | ✅              | ✅        |
| View Expired Reports        | ❌             | ✅           | ✅              | ✅        |
| Force-Complete Sessions     | ❌             | ✅           | ✅              | ✅        |
| View Audit Logs             | ❌             | ✅           | ✅              | ✅        |
| Export Data                 | ❌             | ✅           | ✅              | ✅        |

---

## Document Version History

| Version | Date       | Changes                          | Author  |
|---------|------------|----------------------------------|---------|
| 1.0     | May 2026   | Initial stakeholder guide        | NexGen  |

---

**End of Document**

*This guide is maintained as part of the NexGen E-Meril platform documentation.*
