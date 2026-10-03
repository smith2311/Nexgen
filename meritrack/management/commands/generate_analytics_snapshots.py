"""
Generate hospital analytics snapshots and product movement summaries.
Run daily/weekly via cron: python manage.py generate_analytics_snapshots
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.utils import timezone

from meritrack.models import (
    Hospital, ConsignedProduct, UsageRecord, StockOrder,
    ExpiryAlert, ScanningSession, HospitalAnalyticsSnapshot, ProductMovementSummary
)


class Command(BaseCommand):
    help = 'Generate hospital analytics snapshots and product movement summaries'

    def add_arguments(self, parser):
        parser.add_argument('--hospital-id', type=int, help='Generate for specific hospital')
        parser.add_argument('--date', type=str, help='Snapshot date (YYYY-MM-DD), default today')
        parser.add_argument('--force', action='store_true', help='Overwrite existing snapshots')

    def handle(self, *args, **options):
        today = timezone.now().date()
        if options.get('date'):
            from datetime import datetime
            today = datetime.strptime(options['date'], '%Y-%m-%d').date()

        hospitals = Hospital.objects.filter(is_active=True)
        if options.get('hospital_id'):
            hospitals = hospitals.filter(pk=options['hospital_id'])

        self.stdout.write(f"Generating analytics snapshots for {today}...")

        for hospital in hospitals:
            self._generate_hospital_snapshot(hospital, today, options.get('force'))
            self._generate_movement_summaries(hospital, today, options.get('force'))

        self.stdout.write(self.style.SUCCESS(
            f"Completed analytics generation for {hospitals.count()} hospitals"
        ))

    def _generate_hospital_snapshot(self, hospital, date, force=False):
        """Generate a single hospital snapshot."""
        if not force:
            if HospitalAnalyticsSnapshot.objects.filter(
                hospital=hospital, snapshot_date=date
            ).exists():
                return

        year_start = date.replace(month=1, day=1)
        last_snapshot = HospitalAnalyticsSnapshot.objects.filter(
            hospital=hospital, snapshot_date__lt=date
        ).order_by('-snapshot_date').first()
        last_date = last_snapshot.snapshot_date if last_snapshot else date - timedelta(days=30)

        on_shelf = ConsignedProduct.objects.filter(
            department__hospital=hospital, status='on_shelf'
        )
        total_on_shelf = on_shelf.count()
        expired_on_shelf = on_shelf.filter(expiry_date__lt=date).count()
        expiring_30d = on_shelf.filter(
            expiry_date__gte=date, expiry_date__lte=date + timedelta(days=30)
        ).count()
        expiring_90d = on_shelf.filter(
            expiry_date__gte=date, expiry_date__lte=date + timedelta(days=90)
        ).count()

        used_ytd = UsageRecord.objects.filter(
            hospital=hospital, used_date__gte=year_start, used_date__lte=date
        ).count()
        used_period = UsageRecord.objects.filter(
            hospital=hospital, used_date__gte=last_date, used_date__lte=date
        ).count()

        # Inventory value
        inventory_value = on_shelf.filter(
            catalog_product__unit_price__isnull=False
        ).aggregate(
            total=Sum('catalog_product__unit_price')
        )['total'] or Decimal('0')

        # Used value
        used_value = UsageRecord.objects.filter(
            hospital=hospital, used_date__gte=last_date, used_date__lte=date,
            product__catalog_product__unit_price__isnull=False
        ).aggregate(
            total=Sum('product__catalog_product__unit_price')
        )['total'] or Decimal('0')

        # Operational
        active_sessions = ScanningSession.objects.filter(
            hospital=hospital, status='active'
        ).count()
        pending_orders = StockOrder.objects.filter(
            hospital=hospital, status__in=['submitted', 'confirmed']
        ).count()
        pending_alerts = ExpiryAlert.objects.filter(
            product__department__hospital=hospital, status='pending'
        ).count()

        avg_expiry = None
        if total_on_shelf > 0:
            expiry_products = on_shelf.filter(expiry_date__isnull=False)
            total_days = sum(
                (p.expiry_date - date).days for p in expiry_products
            )
            avg_expiry = Decimal(str(round(total_days / max(expiry_products.count(), 1), 1)))

        # Turnover rate
        six_months_ago = date - timedelta(days=180)
        used_6m = UsageRecord.objects.filter(
            hospital=hospital, used_date__gte=six_months_ago, used_date__lte=date
        ).count()
        turnover = Decimal(str(round((used_6m * 2) / max(total_on_shelf, 1), 2)))

        # Product velocity
        usage_by_product = UsageRecord.objects.filter(
            hospital=hospital, used_date__gte=date - timedelta(days=180)
        ).values('product__material_code').annotate(count=Count('id'))

        usage_counts = [r['count'] for r in usage_by_product]
        avg_usage = sum(usage_counts) / len(usage_counts) if usage_counts else 0
        fast = sum(1 for c in usage_counts if c > avg_usage * 1.5)
        slow = sum(1 for c in usage_counts if c <= avg_usage * 0.3)

        HospitalAnalyticsSnapshot.objects.update_or_create(
            hospital=hospital,
            snapshot_date=date,
            defaults={
                'total_on_shelf': total_on_shelf,
                'total_expired_on_shelf': expired_on_shelf,
                'total_expiring_30d': expiring_30d,
                'total_expiring_90d': expiring_90d,
                'total_used_ytd': used_ytd,
                'total_used_period': used_period,
                'inventory_value': inventory_value,
                'used_value_period': used_value,
                'active_sessions': active_sessions,
                'pending_orders': pending_orders,
                'pending_alerts': pending_alerts,
                'avg_days_to_expiry': avg_expiry,
                'inventory_turnover_rate': turnover,
                'fast_moving_count': fast,
                'slow_moving_count': slow,
            }
        )
        self.stdout.write(f"  {hospital.name}: snapshot generated")

    def _generate_movement_summaries(self, hospital, date, force=False):
        """Generate product movement summaries for the latest period."""
        period_start = date.replace(day=1)
        period_end = date

        products_on_shelf = ConsignedProduct.objects.filter(
            department__hospital=hospital
        ).values('catalog_product_id').distinct()

        for entry in products_on_shelf:
            catalog_id = entry['catalog_product_id']
            if not catalog_id:
                continue

            if not force and ProductMovementSummary.objects.filter(
                hospital=hospital, catalog_product_id=catalog_id,
                period_start=period_start
            ).exists():
                continue

            qs = ConsignedProduct.objects.filter(
                department__hospital=hospital, catalog_product_id=catalog_id
            )

            on_shelf = qs.filter(status='on_shelf').count()
            used = qs.filter(
                status='used', removed_at__gte=period_start, removed_at__lte=date
            ).count()
            expired = qs.filter(
                status='expired_removed', removed_at__gte=period_start
            ).count()
            transferred_out = qs.filter(
                status='transferred', removed_at__gte=period_start
            ).count()

            # Usage rate over last 6 months
            six_months_usage = UsageRecord.objects.filter(
                hospital=hospital,
                product__catalog_product_id=catalog_id,
                used_date__gte=date - timedelta(days=180)
            ).count()
            monthly_rate = round(six_months_usage / 6, 2)

            # Velocity classification
            if monthly_rate >= 3:
                velocity = 'fast'
            elif monthly_rate >= 1:
                velocity = 'medium'
            elif monthly_rate > 0:
                velocity = 'slow'
            else:
                velocity = 'dead'

            ProductMovementSummary.objects.update_or_create(
                hospital=hospital,
                catalog_product_id=catalog_id,
                period_start=period_start,
                defaults={
                    'period_end': period_end,
                    'opening_stock': on_shelf + used + expired + transferred_out,
                    'received': 0,
                    'used': used,
                    'expired_removed': expired,
                    'transferred_out': transferred_out,
                    'transferred_in': 0,
                    'closing_stock': on_shelf,
                    'velocity': velocity,
                    'usage_rate_per_month': Decimal(str(monthly_rate)),
                }
            )
