from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from pr_tracker.models import TrackingMaster


TRACKING_NUMBERS = [
    ('TRK-001', 'General procurement tracking'),
    ('TRK-002', 'Cardiology supplies'),
    ('TRK-003', 'Orthopaedics supplies'),
    ('TRK-004', 'Endoscopy equipment'),
    ('TRK-005', 'Diagnostics reagents'),
    ('TRK-006', 'IT & software procurement'),
    ('TRK-007', 'Office supplies'),
    ('TRK-008', 'Lab consumables'),
    ('TRK-009', 'Capital equipment'),
    ('TRK-010', 'Marketing materials'),
]


class Command(BaseCommand):
    help = 'Seed PR Tracker: auth groups and sample tracking numbers'

    def handle(self, *args, **options):
        self._seed_groups()
        self._seed_tracking()
        self.stdout.write(self.style.SUCCESS('\nPR Tracker seed data loaded.'))

    def _seed_groups(self):
        self.stdout.write('\n--- Auth Groups ---')
        for name in ('PR-Manager', 'PR-Member'):
            _, created = Group.objects.get_or_create(name=name)
            self.stdout.write(f'  {name} — {"created" if created else "exists"}')

    def _seed_tracking(self):
        self.stdout.write('\n--- Tracking Numbers ---')
        for tn, desc in TRACKING_NUMBERS:
            obj, created = TrackingMaster.objects.update_or_create(
                tracking_number=tn,
                defaults={'description': desc, 'is_active': True},
            )
            self.stdout.write(f'  {tn:12s} — {"created" if created else "exists"}')
