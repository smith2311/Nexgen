from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group


class Command(BaseCommand):
    help = 'Create Django groups for Logistics Performance RBAC'

    def handle(self, *args, **options):
        groups = [
            'Logistics Performance',   # super role
            'LP-Admin',                # admin role
            'LP-Members',              # member role
        ]
        for name in groups:
            _, created = Group.objects.get_or_create(name=name)
            status = 'Created' if created else 'Already exists'
            self.stdout.write(f'  {status}: {name}')
        self.stdout.write(self.style.SUCCESS('LP roles ready.'))
