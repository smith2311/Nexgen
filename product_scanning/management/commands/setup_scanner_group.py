"""
Management command to create (or update) the 'Product Scanner' group.

Users assigned to this group will only see product_scanning models in Django admin.

Usage:
    python manage.py setup_scanner_group
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create the "Product Scanner" group with product_scanning-only admin permissions'

    # All product_scanning models that should be visible in admin
    APP_LABEL = "product_scanning"

    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(name="Product Scanner")
        verb = "Created" if created else "Updated"

        # Grab every permission for the product_scanning app
        ct_ids = ContentType.objects.filter(app_label=self.APP_LABEL).values_list("id", flat=True)
        perms = Permission.objects.filter(content_type_id__in=ct_ids)

        group.permissions.set(perms)

        perm_list = list(perms.values_list("codename", flat=True))
        self.stdout.write(
            self.style.SUCCESS(
                f'{verb} group "Product Scanner" with {len(perm_list)} permissions:\n'
                + "\n".join(f"  • {p}" for p in sorted(perm_list))
            )
        )
        self.stdout.write(
            self.style.NOTICE(
                "\nAssign users to this group via Admin → Users → Groups.\n"
                "Those users also need is_staff=True to access the admin panel."
            )
        )
