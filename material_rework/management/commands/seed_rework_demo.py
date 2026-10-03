"""Create realistic, repeatable demo data for the Material Rework UI."""

from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import CommandError
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from material_rework.models import (
    Company,
    Department,
    DispositionBatch,
    DispositionItem,
    ReworkLocation,
    ReworkType,
    RequestedByOption,
    Vertical,
)


User = get_user_model()


class Command(BaseCommand):
    help = "Seed realistic, idempotent demo data for the Material Rework screens"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the demo batches before recreating them.",
        )
        parser.add_argument(
            "--company-file",
            default="dummy_tempelate/material rework/Company.xlsx",
            help="Path to the supplied Company.xlsx master workbook.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            DispositionBatch.objects.filter(reference_number__startswith="DEMO-MR-").delete()

        users = self._users()
        masters = self._masters(options["company_file"])
        batches = self._batches(users, masters)

        self.stdout.write(self.style.SUCCESS(
            f"Material Rework demo ready: {len(batches)} batches, "
            f"{DispositionItem.objects.filter(batch__in=batches).count()} items."
        ))
        self.stdout.write("Login users: smith / Smith@2311, demo.hod / Demo@2311, "
                          "demo.production / Demo@2311, demo.warehouse / Demo@2311")

    def _users(self):
        roles = {
            "Material Rework Admin": "Material Rework Admin",
            "MR HOD": "MR HOD",
            "MR Production": "MR Production",
            "MR Warehouse": "MR Warehouse",
        }
        groups = {name: Group.objects.get_or_create(name=name)[0] for name in roles}
        specs = [
            ("smith", "Smith", "Kansara", "smith.kansara@merillife.com", roles),
            ("demo.hod", "Priya", "Shah", "priya.shah@merillife.com", {"MR HOD"}),
            ("demo.production", "Rahul", "Patel", "rahul.patel@merillife.com", {"MR Production"}),
            ("demo.warehouse", "Neha", "Mehta", "neha.mehta@merillife.com", {"MR Warehouse"}),
        ]
        result = {}
        for username, first, last, email, group_names in specs:
            user, _ = User.objects.get_or_create(username=username)
            user.first_name = first
            user.last_name = last
            user.email = email
            user.is_active = True
            user.is_staff = username == "smith"
            user.set_password("Smith@2311" if username == "smith" else "Demo@2311")
            user.save()
            user.groups.add(*(groups[name] for name in group_names))
            result[username] = user
        return result

    def _masters(self, company_file):
        vertical, _ = Vertical.objects.get_or_create(name="Pharmaceuticals")
        department, _ = Department.objects.get_or_create(
            name="Quality & Compliance",
            vertical=vertical,
            defaults={
                "hod_email": "priya.shah@merillife.com",
                "warehouse_email": "neha.mehta@merillife.com",
                "production_review_email": "rahul.patel@merillife.com",
                "ppic_email": "ppic.demo@merillife.com",
            },
        )
        department.hod_email = "priya.shah@merillife.com"
        department.warehouse_email = "neha.mehta@merillife.com"
        department.production_review_email = "rahul.patel@merillife.com"
        department.ppic_email = "ppic.demo@merillife.com"
        department.save()

        companies = self._load_companies(company_file)
        company_verticals = self._company_verticals(companies)
        requested_by = {
            name: RequestedByOption.objects.get_or_create(name=name)[0]
            for name in ("Quality Assurance", "Production Planning", "Customer Complaint")
        }
        rework_types = {
            name: ReworkType.objects.get_or_create(name=name)[0]
            for name in ("Re-labelling", "Repacking", "Visual Inspection", "Batch Documentation")
        }
        locations = {
            name: ReworkLocation.objects.get_or_create(name=name)[0]
            for name in ("Vapi Plant", "Surat Contract Facility", "Ahmedabad Warehouse")
        }
        return {
            # Kept as the fallback for a batch whose company has no vertical
            # (and for anything created before this existed).
            "vertical": vertical,
            "department": department,
            "companies": companies,
            # cid -> (vertical, department), so a batch can take its vertical
            # from the company it belongs to.
            "company_verticals": company_verticals,
            "requested_by": requested_by,
            "rework_types": rework_types,
            "locations": locations,
        }

    #: Placeholder recipients for generated departments. `example.com` is
    #: reserved by RFC 2606 and can never be delivered — deliberate, because
    #: these fields are what approval and alert mail is sent to, this
    #: environment sends live mail, and 29 generated departments carrying a
    #: real address would be 29 ways to mail a colleague by accident. An admin
    #: replaces them per department on the Masters screen.
    PLACEHOLDER_EMAILS = {
        "hod_email": "hod@example.com",
        "warehouse_email": "warehouse@example.com",
        "production_review_email": "production@example.com",
        "ppic_email": "ppic@example.com",
    }

    def _company_verticals(self, companies):
        """A Vertical per company, each with one Department to hang batches on.

        The Vertical master held a single row, so every chart that groups by
        vertical drew one bar. One vertical per company gives those charts real
        spread and matches how the business is actually divided.

        Named after the company, since `Vertical` has no company FK — the name
        IS the association. A Department is created alongside because
        `DispositionBatch.department` is required: a vertical with no
        department cannot receive an upload, so creating one without the other
        would produce 29 verticals nobody can use.
        """
        pairs = {}
        for cid, company in sorted(companies.items()):
            vertical, _ = Vertical.objects.get_or_create(name=company.company_name)
            department, _ = Department.objects.get_or_create(
                name="Rework & Disposition",
                vertical=vertical,
                defaults=dict(self.PLACEHOLDER_EMAILS),
            )
            pairs[cid] = (vertical, department)
        return pairs

    def _load_companies(self, company_file):
        """Synchronize Company rows from the supplied workbook by CId."""
        from openpyxl import load_workbook

        path = Path(company_file)
        if not path.exists():
            raise CommandError(f"Company master not found: {path}")
        sheet = load_workbook(path, read_only=True, data_only=True).active
        headers = [str(value or "").strip().lower() for value in next(sheet.iter_rows(values_only=True))]
        columns = {name: index for index, name in enumerate(headers)}
        required = {"cid", "companyname", "acronym", "country"}
        missing = required - columns.keys()
        if missing:
            raise CommandError(f"Company master is missing columns: {', '.join(sorted(missing))}")

        companies = {}
        for values in sheet.iter_rows(min_row=2, values_only=True):
            if not values[columns["cid"]] or not values[columns["companyname"]]:
                continue
            cid = int(values[columns["cid"]])
            raw_country = values[columns["country"]]
            country = None if str(raw_country).strip().upper() in {"", "NULL", "NONE"} else int(raw_country)
            company, _ = Company.objects.update_or_create(
                cid=cid,
                defaults={
                    "company_name": str(values[columns["companyname"]]).strip(),
                    "acronym": str(values[columns["acronym"]]).strip(),
                    "country": country,
                    "is_active": True,
                },
            )
            companies[cid] = company
        if not companies:
            raise CommandError(f"Company master contains no usable rows: {path}")
        return companies

    def _batches(self, users, masters):
        now = timezone.now()
        # Vertical and department are no longer common to every batch: each one
        # now takes them from its own company (see the per-definition lookup
        # below), so the dashboards show a real spread instead of one bar.
        common = {
            "uploaded_by_name": "Smith Kansara",
            "uploaded_by_email": "smith.kansara@merillife.com",
        }
        definitions = [
            {
                "ref": "DEMO-MR-001", "status": "pending_ppic", "source": "order_management",
                "company": 2,
                "requested": "Production Planning", "type": "Repacking", "location": "Vapi Plant",
                "priority": 1, "subject": "Urgent repacking request for CardioSafe 5 mL",
                "age": 1, "items": [
                    ("CS5-AMP", "CS5A2407", 2400, "India", "IN", "IN", "Repack into 10-unit cartons"),
                    ("CS5-AMP", "CS5A2408", 1200, "India", "IN", "IN", "Outer carton artwork update"),
                ],
            },
            {
                "ref": "DEMO-MR-002", "status": "pending_hod", "source": "warehouse",
                "company": 3,
                "requested": "Quality Assurance", "type": "Visual Inspection", "location": "Ahmedabad Warehouse",
                "priority": 1, "subject": "Visual inspection required after transit damage",
                "age": 3, "items": [
                    ("NEUROKIT-20", "NK2406B", 860, "India", "IN", "IN", "Inspect 37 cartons with crushed corners"),
                    ("NEUROKIT-20", "NK2406C", 420, "India", "IN", "IN", "Hold until QA disposition"),
                ],
            },
            {
                "ref": "DEMO-MR-003", "status": "hod_approved", "source": "production",
                "company": 2,
                "requested": "Quality Assurance", "type": "Batch Documentation", "location": "Vapi Plant",
                "priority": 2, "subject": "Update batch documents before market release",
                "age": 7, "items": [
                    ("INSULIN-PEN", "IP2405A", 1500, "India", "IN", "IN", "Attach corrected CoA and release note"),
                    ("INSULIN-PEN", "IP2405B", 720, "India", "IN", "IN", "Verify serialization report"),
                ],
            },
            {
                "ref": "DEMO-MR-004", "status": "production_done", "source": "warehouse",
                "company": 4,
                "requested": "Customer Complaint", "type": "Re-labelling", "location": "Surat Contract Facility",
                "priority": 1, "subject": "Relabel export cartons for UAE shipment",
                "age": 12, "production_done": True, "items": [
                    ("CARDIOPATCH-10", "CP2403X", 980, "India", "IN", "AE", "Replace country-of-origin label"),
                    ("CARDIOPATCH-10", "CP2403Y", 640, "India", "IN", "AE", "Replace IFU language sticker"),
                ],
            },
            {
                "ref": "DEMO-MR-005", "status": "completed", "source": "production",
                "company": 2,
                "requested": "Quality Assurance", "type": "Visual Inspection", "location": "Vapi Plant",
                "priority": 3, "subject": "Completed inspection for returned lots",
                "age": 24, "production_done": True, "completed": True, "items": [
                    ("DIALYZER-4H", "DZ2402K", 320, "India", "IN", "IN", "All units passed inspection"),
                    ("DIALYZER-4H", "DZ2402L", 180, "India", "IN", "IN", "All units passed inspection"),
                ],
            },
            {
                "ref": "DEMO-MR-006", "status": "ppic_rejected", "source": "order_management",
                "company": 1,
                "requested": "Customer Complaint", "type": "Repacking", "location": "Ahmedabad Warehouse",
                "priority": 2, "subject": "Rejected: stock is beyond approved rework window",
                "age": 18, "items": [
                    ("SURGIGLOVE-M", "SG2312Q", 5000, "India", "IN", "IN", "Expired packaging approval"),
                ],
            },
            {
                "ref": "DEMO-MR-007", "status": "production_done", "source": "production",
                "company": 3,
                "requested": "Quality Assurance", "type": "Re-labelling", "location": "Vapi Plant",
                "priority": 1, "subject": "Rework in progress: final warehouse receipt pending",
                "age": 5, "production_done": True, "items": [
                    ("STENT-PLUS", "ST2404M", 600, "India", "IN", "IN", "Warehouse released 600; production returned 594"),
                    ("STENT-PLUS", "ST2404N", 400, "India", "IN", "IN", "Awaiting release to production"),
                ],
            },
        ]

        batches = []
        for definition in definitions:
            company = masters["companies"][definition["company"]]
            # Each batch sits under its own company's vertical. Falls back to
            # the original single vertical if a company somehow has none, so a
            # missing row degrades to the old behaviour rather than an error.
            vertical, department = masters["company_verticals"].get(
                definition["company"], (masters["vertical"], masters["department"]),
            )
            batch, _ = DispositionBatch.objects.update_or_create(
                reference_number=definition["ref"],
                defaults={
                    **common,
                    "vertical": vertical,
                    "department": department,
                    "company": company,
                    "status": definition["status"],
                    "source": definition["source"],
                    "requested_by": masters["requested_by"][definition["requested"]],
                    "rework_type": masters["rework_types"][definition["type"]],
                    "location": masters["locations"][definition["location"]],
                    "priority": definition["priority"],
                    "subject": definition["subject"],
                },
            )
            batch.uploaded_at = now - timedelta(days=definition["age"])
            if definition.get("production_done"):
                batch.production_reviewed_by_name = "Rahul Patel"
                batch.production_reviewed_by_email = "rahul.patel@merillife.com"
                batch.production_reviewed_at = now - timedelta(days=max(1, definition["age"] - 2))
            if definition["status"] in {"ppic_approved", "ppic_rejected"}:
                batch.ppic_reviewed_by_name = "Amit Desai"
                batch.ppic_reviewed_by_email = "ppic.demo@merillife.com"
                batch.ppic_reviewed_at = now - timedelta(days=max(1, definition["age"] - 1))
            if definition["status"] in {"hod_approved", "hod_rejected"} or definition.get("production_done"):
                batch.hod_reviewed_at = now - timedelta(days=max(1, definition["age"] - 3))
                batch.hod_comments = "Reviewed for demo workflow; quantities and documentation verified."
            batch.save()
            batch.items.all().delete()
            for index, item_data in enumerate(definition["items"], start=1):
                material, batch_no, qty, country, from_country, to_country, remark = item_data
                item_status = self._item_status(definition, index)
                item = DispositionItem.objects.create(
                    batch=batch,
                    material=material,
                    batch_no=batch_no,
                    serial_number="",
                    country=country,
                    from_country=from_country,
                    to_country=to_country,
                    mfg_date="2024-06-15",
                    exp_date="2027-06-14",
                    from_storage_location="FG01",
                    to_storage_location="REWK",
                    qty=qty,
                    accepted_qty=qty if item_status in {"accepted", "received", "received_discrepancy"} else 0,
                    pending_qty=0 if item_status in {"accepted", "received", "received_discrepancy"} else qty,
                    qty_received=594 if definition["ref"] == "DEMO-MR-007" and index == 1 else (qty if item_status in {"received", "received_discrepancy"} else 0),
                    box_no=f"BX-{definition['ref'][-3:]}-{index:02d}",
                    remark=remark,
                    item_date=(now - timedelta(days=definition["age"])).strftime("%Y-%m-%d"),
                    status=item_status,
                )
                self._apply_round_trip(item, definition, index, now)
                item.save()
            batches.append(batch)
        return batches

    @staticmethod
    def _item_status(definition, index):
        ref = definition["ref"]
        if ref == "DEMO-MR-003":
            return "pending_production"
        if ref == "DEMO-MR-004":
            return "accepted"
        if ref == "DEMO-MR-005":
            return "received"
        if ref == "DEMO-MR-006":
            return "ppic_rejected"
        if ref == "DEMO-MR-007":
            return "pending_rework_handover" if index == 2 else "pending_return_receipt"
        if definition["status"] == "pending_ppic":
            return "pending_ppic"
        return "pending_hod"

    @staticmethod
    def _apply_round_trip(item, definition, index, now):
        if definition["ref"] != "DEMO-MR-007" or index != 1:
            return
        item.rework_released_qty = 600
        item.rework_released_at = now - timedelta(days=1)
        item.rework_released_remarks = "Released against warehouse issue WH-240905-17"
        item.reworked_qty = 594
        item.rework_loss_qty = 6
        item.rework_loss_reason = "Two damaged units and four units failed final visual check"
        item.rework_returned_at = now - timedelta(hours=8)
        item.has_receipt_discrepancy = True
        item.outward_number = "OUT-240905-17"
        item.inward_number = "IN-240905-22"
