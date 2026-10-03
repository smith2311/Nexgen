from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

User = get_user_model()

MD_ADMIN_GROUP = 'Material Rework Admin'
MD_HOD         = 'MR HOD'
MD_PRODUCTION  = 'MR Production'
MD_WAREHOUSE   = 'MR Warehouse'
ROOT_USER_GROUP = 'Root User'


class Command(BaseCommand):
    help = 'Create or update the "smith" user with full Material Rework + Root User access'

    def handle(self, *args, **options):
        admin_group, _ = Group.objects.get_or_create(name=MD_ADMIN_GROUP)
        hod_group, _   = Group.objects.get_or_create(name=MD_HOD)
        prod_group, _  = Group.objects.get_or_create(name=MD_PRODUCTION)
        wh_group, _    = Group.objects.get_or_create(name=MD_WAREHOUSE)
        root_group, _  = Group.objects.get_or_create(name=ROOT_USER_GROUP)

        user, created = User.objects.get_or_create(username='smith')
        user.set_password('Smith@2311')
        user.is_staff = True
        user.is_active = True
        user.first_name = 'Smith'
        user.last_name = 'Kansara'
        user.email = 'smith.kansara@merillife.com'
        user.save()

        user.groups.add(admin_group, hod_group, prod_group, wh_group)

        # Enforce single Root User — clear existing holder then assign
        with transaction.atomic():
            root_group.user_set.clear()
            user.groups.add(root_group)

        action = 'Created' if created else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'{action} user "smith" — groups: {MD_ADMIN_GROUP}, {MD_HOD}, {MD_PRODUCTION}, {MD_WAREHOUSE}, {ROOT_USER_GROUP}'
        ))
        self.stdout.write(self.style.SUCCESS(
            'Login at /admin/  username: smith  password: Smith@2311'
        ))
