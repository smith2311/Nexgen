from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group


class Command(BaseCommand):
    help = 'Create Django groups for PR Tracker RBAC'

    def handle(self, *args, **options):
        groups = [
            'PR-Manager',   # assigns PRs, views dashboard
            'PR-Admin',     # manages users/tracking master
            'PR-Member',    # fills in assigned PR details
        ]
        for name in groups:
            _, created = Group.objects.get_or_create(name=name)
            status = 'Created' if created else 'Already exists'
            self.stdout.write(f'  {status}: {name}')
        self.stdout.write(self.style.SUCCESS('PR Tracker roles ready.'))
