from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group


class Command(BaseCommand):
    help = 'Create Django groups for VAT Reconciliation RBAC'

    def handle(self, *args, **options):
        groups = [
            'VAT Reconciliation',   # super role
            'VAT-Admin',            # admin role
            'VAT-Members',          # member role
        ]
        for name in groups:
            _, created = Group.objects.get_or_create(name=name)
            status = 'Created' if created else 'Already exists'
            self.stdout.write(f'  {status}: {name}')
        self.stdout.write(self.style.SUCCESS('VAT roles ready.'))
