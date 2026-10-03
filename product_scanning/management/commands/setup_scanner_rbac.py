"""
Management command: setup_scanner_rbac

Creates / updates the product-scanning RBAC groups:

  PS Salesperson  — scanner-only        (start sessions, scan products)
  PS Warehouse    — dashboard full       (view sessions, upload attachments, manage data)
  PS Viewer       — dashboard read-only  (view only, no actions)
  Product Scanner — full access          (both scanner + dashboard)

Usage:
    python manage.py setup_scanner_rbac
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

SCANNER_CODENAMES = [
    "add_scanningsession",
    "change_scanningsession",
    "view_scanningsession",
    "add_scannedproduct",
    "change_scannedproduct",
    "view_scannedproduct",
    "add_batch",
    "change_batch",
    "view_batch",
    "view_hospital",
    "view_qrtemplate",
]

WAREHOUSE_CODENAMES = [
    "view_scanningsession",
    "change_scanningsession",
    "view_scannedproduct",
    "view_batch",
    "view_hospital",
    "add_sessionattachment",
    "change_sessionattachment",
    "view_sessionattachment",
    "delete_sessionattachment",
]


class Command(BaseCommand):
    help = "Create / update PS Salesperson, PS Warehouse, and Product Scanner RBAC groups"

    def handle(self, *args, **options):
        app_label = "product_scanning"

        all_ps_perms = Permission.objects.filter(content_type__in=ContentType.objects.filter(app_label=app_label))

        scanner_perms = all_ps_perms.filter(codename__in=SCANNER_CODENAMES)
        warehouse_perms = all_ps_perms.filter(codename__in=WAREHOUSE_CODENAMES)

        viewer_perms = all_ps_perms.filter(codename__in=[
            "view_scanningsession",
            "view_scannedproduct",
            "view_batch",
            "view_hospital",
            "view_sessionattachment",
        ])

        groups = {
            "PS Salesperson": scanner_perms,
            "PS Warehouse": warehouse_perms,
            "PS Viewer": viewer_perms,
            "Product Scanner": all_ps_perms,  # full access
        }

        for group_name, perms_qs in groups.items():
            group, created = Group.objects.get_or_create(name=group_name)
            group.permissions.set(perms_qs)
            action = "Created" if created else "Updated"
            perm_list = sorted(perms_qs.values_list("codename", flat=True))
            self.stdout.write(self.style.SUCCESS(f'{action} group "{group_name}" with {len(perm_list)} permissions'))
            for p in perm_list:
                self.stdout.write(f"    • {p}")

        self.stdout.write(self.style.SUCCESS("\n✅ RBAC groups setup complete."))
        self.stdout.write(
            "\nAssignment guide:\n"
            "  PS Salesperson  → QR scanner only          (no dashboard access)\n"
            "  PS Warehouse    → Dashboard full access    (no scanner access)\n"
            "  PS Viewer       → Dashboard read-only      (view only, no actions)\n"
            "  Product Scanner → Full access              (scanner + dashboard)\n"
        )
