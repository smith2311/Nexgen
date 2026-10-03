"""
MeriTrack Analytics Engine
──────────────────────────────────────────────────────────────────
Comprehensive data insights, forecasting, and reporting service.

Provides:
 • Hospital-level KPI computation
 • Product velocity classification (fast/slow/dead moving)
 • Demand forecasting with trend analysis
 • Expiry risk scoring
 • Inventory health scoring
 • Supply chain visibility
 • Reconciliation analytics
"""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncMonth, Coalesce, Extract
from django.utils import timezone

logger = logging.getLogger(__name__)


class AnalyticsEngine:
    """Core analytics engine for MeriTrack data insights."""

    # ── Hospital-Level KPIs ──────────────────────────────────────────────
    @staticmethod
    def get_hospital_kpis(hospital_id=None):
        """Compute comprehensive KPIs for a hospital or all hospitals."""
        from .models import (
            Hospital, ConsignedProduct, UsageRecord, StockOrder,
            ExpiryAlert, ScanningSession
        )

        today = timezone.now().date()
        year_start = today.replace(month=1, day=1)
        hospitals = Hospital.objects.filter(is_active=True)
        if hospital_id:
            hospitals = hospitals.filter(pk=hospital_id)

        results = []
        for hospital in hospitals:
            on_shelf_qs = ConsignedProduct.objects.filter(
                department__hospital=hospital, status='on_shelf'
            )
            total_on_shelf = on_shelf_qs.count()
            expired_on_shelf = on_shelf_qs.filter(expiry_date__lt=today).count()
            expiring_30d = on_shelf_qs.filter(
                expiry_date__gte=today, expiry_date__lte=today + timedelta(days=30)
            ).count()
            expiring_90d = on_shelf_qs.filter(
                expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90)
            ).count()

            # Usage YTD
            used_ytd = UsageRecord.objects.filter(
                hospital=hospital, used_date__gte=year_start
            ).count()

            # Average days to expiry for on-shelf
            avg_expiry = on_shelf_qs.filter(expiry_date__isnull=False).aggregate(
                avg_days=Avg(F('expiry_date') - today)
            )

            # Inventory value
            inventory_value = on_shelf_qs.filter(
                catalog_product__unit_price__isnull=False
            ).aggregate(
                total=Sum('catalog_product__unit_price')
            )['total'] or Decimal('0')

            # Pending alerts and orders
            pending_alerts = ExpiryAlert.objects.filter(
                product__department__hospital=hospital, status='pending'
            ).count()
            pending_orders = StockOrder.objects.filter(
                hospital=hospital, status__in=['submitted', 'confirmed']
            ).count()

            # Recent activity
            last_session = ScanningSession.objects.filter(
                hospital=hospital
            ).order_by('-started_at').first()

            # Inventory turnover (last 6 months)
            six_months_ago = today - timedelta(days=180)
            used_6m = UsageRecord.objects.filter(
                hospital=hospital, used_date__gte=six_months_ago
            ).count()
            avg_inventory = max(total_on_shelf, 1)
            turnover_rate = round((used_6m * 2) / avg_inventory, 2)  # annualized

            results.append({
                'hospital_id': hospital.id,
                'hospital_name': hospital.name,
                'sap_code': hospital.sap_code,
                'country': hospital.country,
                'total_on_shelf': total_on_shelf,
                'expired_on_shelf': expired_on_shelf,
                'expiring_30d': expiring_30d,
                'expiring_90d': expiring_90d,
                'used_ytd': used_ytd,
                'inventory_value': float(inventory_value),
                'avg_days_to_expiry': avg_expiry.get('avg_days'),
                'pending_alerts': pending_alerts,
                'pending_orders': pending_orders,
                'turnover_rate': turnover_rate,
                'last_session_date': last_session.started_at.isoformat() if last_session else None,
                'health_score': AnalyticsEngine._compute_health_score(
                    total_on_shelf, expired_on_shelf, expiring_30d, turnover_rate
                ),
            })

        return results

    @staticmethod
    def _compute_health_score(total, expired, expiring_30d, turnover):
        """Compute inventory health score (0-100)."""
        if total == 0:
            return 100
        # Deduct for expired
        expired_penalty = min(40, (expired / total) * 100)
        # Deduct for soon-expiring
        expiring_penalty = min(20, (expiring_30d / total) * 50)
        # Reward good turnover (3-6 ideal)
        turnover_score = 20
        if turnover < 1:
            turnover_score = 5
        elif turnover < 2:
            turnover_score = 10
        elif turnover > 8:
            turnover_score = 10
        # Base from expiry health
        base = 60
        score = base - expired_penalty - expiring_penalty + turnover_score
        return max(0, min(100, round(score)))

    # ── Product Velocity Analysis ────────────────────────────────────────
    @staticmethod
    def get_product_velocity(hospital_id=None, months=6):
        """Classify products as fast/medium/slow/dead moving."""
        from .models import UsageRecord, ConsignedProduct

        cutoff = timezone.now().date() - timedelta(days=months * 30)
        usage_qs = UsageRecord.objects.filter(used_date__gte=cutoff)
        if hospital_id:
            usage_qs = usage_qs.filter(hospital_id=hospital_id)

        # Usage counts per product
        usage_by_product = usage_qs.values(
            'product__material_code', 'product__product_name'
        ).annotate(
            total_used=Count('id'),
            unique_months=Count('used_date__month', distinct=True),
        ).order_by('-total_used')

        # Compute velocity thresholds
        usage_counts = [r['total_used'] for r in usage_by_product]
        if usage_counts:
            avg_usage = sum(usage_counts) / len(usage_counts)
            high_threshold = avg_usage * 1.5
            low_threshold = avg_usage * 0.3
        else:
            high_threshold = 10
            low_threshold = 2

        results = []
        for item in usage_by_product:
            used = item['total_used']
            if used >= high_threshold:
                velocity = 'fast'
            elif used >= low_threshold:
                velocity = 'medium'
            elif used > 0:
                velocity = 'slow'
            else:
                velocity = 'dead'

            monthly_rate = round(used / max(months, 1), 2)
            results.append({
                'material_code': item['product__material_code'],
                'product_name': item['product__product_name'],
                'total_used': used,
                'monthly_rate': monthly_rate,
                'velocity': velocity,
                'unique_months': item['unique_months'],
            })

        # Add dead stock (on shelf but zero usage)
        on_shelf_materials = set(
            ConsignedProduct.objects.filter(status='on_shelf')
            .values_list('material_code', flat=True).distinct()
        )
        used_materials = set(r['material_code'] for r in results)
        dead_materials = on_shelf_materials - used_materials

        for mat_code in dead_materials:
            product = ConsignedProduct.objects.filter(
                material_code=mat_code, status='on_shelf'
            ).first()
            results.append({
                'material_code': mat_code,
                'product_name': product.product_name if product else mat_code,
                'total_used': 0,
                'monthly_rate': 0,
                'velocity': 'dead',
                'unique_months': 0,
            })

        return {
            'products': results,
            'summary': {
                'fast': sum(1 for r in results if r['velocity'] == 'fast'),
                'medium': sum(1 for r in results if r['velocity'] == 'medium'),
                'slow': sum(1 for r in results if r['velocity'] == 'slow'),
                'dead': sum(1 for r in results if r['velocity'] == 'dead'),
                'total_products': len(results),
            },
            'thresholds': {
                'fast_min': high_threshold,
                'slow_max': low_threshold,
                'period_months': months,
            },
        }

    # ── Demand Forecasting ───────────────────────────────────────────────
    @staticmethod
    def generate_demand_forecast(hospital_id=None, months_history=12, forecast_months=3):
        """Generate demand forecasts based on historical usage patterns."""
        from .models import UsageRecord, ConsignedProduct, DemandForecast

        cutoff = timezone.now().date() - timedelta(days=months_history * 30)
        today = timezone.now().date()

        usage_qs = UsageRecord.objects.filter(used_date__gte=cutoff)
        if hospital_id:
            usage_qs = usage_qs.filter(hospital_id=hospital_id)

        # Group by hospital + product + month
        monthly_usage = usage_qs.annotate(
            month=TruncMonth('used_date')
        ).values(
            'hospital_id', 'hospital__name',
            'product__catalog_product_id', 'product__catalog_product__name',
            'month'
        ).annotate(
            count=Count('id')
        ).order_by('hospital_id', 'product__catalog_product_id', 'month')

        # Build time series per hospital/product
        series = defaultdict(lambda: defaultdict(int))
        meta = {}
        for row in monthly_usage:
            key = (row['hospital_id'], row['product__catalog_product_id'])
            month_str = row['month'].strftime('%Y-%m') if row['month'] else None
            if month_str:
                series[key][month_str] = row['count']
            meta[key] = {
                'hospital_name': row['hospital__name'],
                'product_name': row['product__catalog_product__name'],
            }

        forecasts = []
        for (hosp_id, prod_id), monthly_data in series.items():
            if not hosp_id or not prod_id:
                continue

            values = list(monthly_data.values())
            if not values:
                continue

            avg_usage = sum(values) / len(values)
            recent_3 = values[-3:] if len(values) >= 3 else values
            recent_avg = sum(recent_3) / len(recent_3)

            # Trend detection
            if len(values) >= 3:
                first_half = values[:len(values)//2]
                second_half = values[len(values)//2:]
                first_avg = sum(first_half) / len(first_half) if first_half else 0
                second_avg = sum(second_half) / len(second_half) if second_half else 0

                if second_avg > first_avg * 1.2:
                    trend = 'increasing'
                elif second_avg < first_avg * 0.8:
                    trend = 'decreasing'
                else:
                    trend = 'stable'
            else:
                trend = 'insufficient_data'

            # Weighted prediction (recent months weighted higher)
            predicted = round(recent_avg * 0.6 + avg_usage * 0.4)

            # Get current stock
            current_stock = ConsignedProduct.objects.filter(
                department__hospital_id=hosp_id,
                catalog_product_id=prod_id,
                status='on_shelf'
            ).count()

            # Safety stock = 1 month of average usage
            safety_stock = max(1, round(avg_usage))

            # Reorder quantity
            total_predicted = predicted * forecast_months
            reorder = max(0, total_predicted + safety_stock - current_stock)

            # Months of stock
            months_of_stock = round(current_stock / max(avg_usage, 0.1), 1)

            # Confidence
            if len(values) >= 6 and trend != 'insufficient_data':
                confidence = 'high'
            elif len(values) >= 3:
                confidence = 'medium'
            else:
                confidence = 'low'

            forecast_entry = {
                'hospital_id': hosp_id,
                'hospital_name': meta.get((hosp_id, prod_id), {}).get('hospital_name', ''),
                'catalog_product_id': prod_id,
                'product_name': meta.get((hosp_id, prod_id), {}).get('product_name', ''),
                'avg_monthly_usage': round(avg_usage, 2),
                'recent_avg': round(recent_avg, 2),
                'predicted_usage': predicted,
                'current_stock': current_stock,
                'recommended_reorder': reorder,
                'safety_stock': safety_stock,
                'months_of_stock': months_of_stock,
                'usage_trend': trend,
                'confidence': confidence,
                'data_points': len(values),
            }
            forecasts.append(forecast_entry)

            # Persist forecast
            period_start = today.replace(day=1)
            period_end = (period_start + timedelta(days=forecast_months * 31)).replace(day=1) - timedelta(days=1)

            try:
                DemandForecast.objects.update_or_create(
                    hospital_id=hosp_id,
                    catalog_product_id=prod_id,
                    period_start=period_start,
                    period_type='monthly',
                    defaults={
                        'period_end': period_end,
                        'actual_usage': sum(values),
                        'current_stock': current_stock,
                        'predicted_usage': predicted,
                        'recommended_reorder': reorder,
                        'safety_stock': safety_stock,
                        'confidence': confidence,
                        'usage_trend': trend,
                        'avg_monthly_usage': Decimal(str(round(avg_usage, 2))),
                        'months_of_stock': Decimal(str(months_of_stock)),
                    }
                )
            except Exception as exc:
                logger.warning(f"Failed to persist forecast: {exc}")

        # Sort by reorder urgency
        forecasts.sort(key=lambda x: (-x['recommended_reorder'], x['months_of_stock']))

        return {
            'forecasts': forecasts,
            'summary': {
                'total_products': len(forecasts),
                'need_reorder': sum(1 for f in forecasts if f['recommended_reorder'] > 0),
                'low_stock': sum(1 for f in forecasts if f['months_of_stock'] < 2),
                'critical_stock': sum(1 for f in forecasts if f['months_of_stock'] < 1),
                'increasing_trend': sum(1 for f in forecasts if f['usage_trend'] == 'increasing'),
                'decreasing_trend': sum(1 for f in forecasts if f['usage_trend'] == 'decreasing'),
            },
        }

    # ── Expiry Risk Analysis ─────────────────────────────────────────────
    @staticmethod
    def get_expiry_risk_analysis(hospital_id=None):
        """Comprehensive expiry risk analysis with financial impact."""
        from .models import ConsignedProduct

        today = timezone.now().date()
        qs = ConsignedProduct.objects.filter(status='on_shelf')
        if hospital_id:
            qs = qs.filter(department__hospital_id=hospital_id)

        # Tier breakdown with value
        tiers = {
            'expired': qs.filter(expiry_date__lt=today),
            '1_week': qs.filter(expiry_date__gte=today, expiry_date__lte=today + timedelta(days=7)),
            '1_month': qs.filter(expiry_date__gte=today + timedelta(days=8), expiry_date__lte=today + timedelta(days=30)),
            '3_months': qs.filter(expiry_date__gte=today + timedelta(days=31), expiry_date__lte=today + timedelta(days=90)),
            '6_months': qs.filter(expiry_date__gte=today + timedelta(days=91), expiry_date__lte=today + timedelta(days=180)),
            'safe': qs.filter(expiry_date__gt=today + timedelta(days=180)),
            'no_expiry': qs.filter(expiry_date__isnull=True),
        }

        results = {}
        total_at_risk_value = Decimal('0')
        for tier_name, tier_qs in tiers.items():
            count = tier_qs.count()
            value = tier_qs.filter(
                catalog_product__unit_price__isnull=False
            ).aggregate(
                total=Coalesce(Sum('catalog_product__unit_price'), Decimal('0'))
            )['total']

            if tier_name in ('expired', '1_week', '1_month'):
                total_at_risk_value += value

            results[tier_name] = {
                'count': count,
                'value': float(value),
            }

        # By hospital breakdown
        by_hospital = qs.filter(
            expiry_date__lt=today + timedelta(days=90)
        ).values(
            'department__hospital__name', 'department__hospital__id'
        ).annotate(
            expired=Count('id', filter=Q(expiry_date__lt=today)),
            expiring_soon=Count('id', filter=Q(expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90))),
            total_at_risk=Count('id'),
        ).order_by('-total_at_risk')

        # By product category
        by_category = qs.filter(
            expiry_date__lt=today + timedelta(days=90)
        ).values(
            'catalog_product__category'
        ).annotate(
            count=Count('id')
        ).order_by('-count')

        return {
            'tiers': results,
            'total_at_risk_value': float(total_at_risk_value),
            'by_hospital': list(by_hospital),
            'by_category': list(by_category),
            'total_on_shelf': qs.count(),
        }

    # ── Supply Chain Visibility ──────────────────────────────────────────
    @staticmethod
    def get_product_journey(serial_number):
        """Get complete supply chain journey for a single product."""
        from .models import (
            ConsignedProduct, ConsignmentPosting, ScanLog,
            SupplyChainEvent, TransferredProduct
        )

        events = []

        # Supply chain events (if any)
        for sce in SupplyChainEvent.objects.filter(serial_number=serial_number).order_by('event_date'):
            events.append({
                'type': 'supply_chain',
                'event': sce.get_event_type_display(),
                'date': sce.event_date.isoformat(),
                'location': sce.location_name,
                'country': sce.location_country,
                'notes': sce.notes,
            })

        # SAP postings (by batch/material)
        product = ConsignedProduct.objects.filter(serial_number=serial_number).first()
        if product and product.batch_number:
            for posting in ConsignmentPosting.objects.filter(
                batch=product.batch_number
            ).order_by('posting_date'):
                events.append({
                    'type': 'sap_posting',
                    'event': posting.movement_type_display,
                    'date': posting.posting_date.isoformat(),
                    'location': posting.customer_name or posting.plant,
                    'country': None,
                    'quantity': float(posting.quantity),
                    'document': posting.material_document,
                })

        # Scan history
        for scan in ScanLog.objects.filter(serial_number=serial_number).order_by('scanned_at'):
            events.append({
                'type': 'scan',
                'event': scan.get_action_display(),
                'date': scan.scanned_at.isoformat(),
                'location': scan.session.hospital.name if scan.session else 'Unknown',
                'session_key': scan.session.session_key if scan.session else None,
            })

        # Transfers
        for tp in TransferredProduct.objects.filter(serial_number=serial_number).order_by('scanned_at'):
            events.append({
                'type': 'transfer',
                'event': f"Transfer: {tp.transfer_session.from_hospital.name} → {tp.transfer_session.to_hospital.name}",
                'date': tp.scanned_at.isoformat(),
                'from': tp.transfer_session.from_hospital.name,
                'to': tp.transfer_session.to_hospital.name,
                'transfer_id': tp.transfer_session.transfer_id,
            })

        # Sort all events by date
        events.sort(key=lambda x: x.get('date', ''))

        # Product summary
        summary = None
        if product:
            summary = {
                'serial_number': product.serial_number,
                'product_name': product.product_name,
                'material_code': product.material_code,
                'batch_number': product.batch_number,
                'status': product.get_status_display(),
                'current_hospital': product.department.hospital.name if product.department else None,
                'current_department': product.department.name if product.department else None,
                'expiry_date': str(product.expiry_date) if product.expiry_date else None,
                'days_until_expiry': product.days_until_expiry,
            }

        return {
            'product': summary,
            'events': events,
            'total_events': len(events),
        }

    # ── Reconciliation Analytics ─────────────────────────────────────────
    @staticmethod
    def get_reconciliation_summary(hospital_id=None):
        """Get reconciliation history and accuracy trends."""
        from .models import InventoryReconciliation

        qs = InventoryReconciliation.objects.all()
        if hospital_id:
            qs = qs.filter(hospital_id=hospital_id)

        # Recent reconciliations
        recent = qs.order_by('-scheduled_date')[:20]
        recon_list = []
        for r in recent:
            recon_list.append({
                'recon_id': r.recon_id,
                'hospital_name': r.hospital.name,
                'recon_type': r.get_recon_type_display(),
                'status': r.get_status_display(),
                'scheduled_date': str(r.scheduled_date) if r.scheduled_date else None,
                'system_count': r.system_count,
                'scanned_count': r.scanned_count,
                'matched_count': r.matched_count,
                'missing_count': r.missing_count,
                'extra_count': r.extra_count,
                'expired_found': r.expired_found,
                'accuracy_rate': r.accuracy_rate,
            })

        # Accuracy trend
        completed = qs.filter(status__in=['completed', 'reviewed', 'resolved']).order_by('scheduled_date')
        accuracy_trend = []
        for r in completed:
            accuracy_trend.append({
                'date': str(r.scheduled_date or r.created_at.date()),
                'accuracy': r.accuracy_rate,
                'hospital': r.hospital.name,
            })

        # Summary stats
        total = qs.count()
        avg_accuracy = 0
        completed_qs = qs.filter(status__in=['completed', 'reviewed', 'resolved'])
        if completed_qs.exists():
            accuracies = [r.accuracy_rate for r in completed_qs]
            avg_accuracy = round(sum(accuracies) / len(accuracies), 2)

        return {
            'reconciliations': recon_list,
            'accuracy_trend': accuracy_trend,
            'summary': {
                'total': total,
                'completed': completed_qs.count(),
                'pending': qs.filter(status__in=['planned', 'in_progress']).count(),
                'avg_accuracy': avg_accuracy,
                'with_discrepancies': qs.filter(status='discrepancy_found').count(),
            },
        }

    # ── Comprehensive Dashboard Data ─────────────────────────────────────
    @staticmethod
    def get_executive_dashboard():
        """Get all data needed for executive-level dashboard."""
        from .models import (
            Hospital, ConsignedProduct, UsageRecord, StockOrder,
            ExpiryAlert, ScanningSession, ConsignmentPosting,
            TransferSession
        )

        today = timezone.now().date()
        year_start = today.replace(month=1, day=1)
        last_30d = today - timedelta(days=30)

        # Key metrics
        metrics = {
            'total_hospitals': Hospital.objects.filter(is_active=True).count(),
            'total_on_shelf': ConsignedProduct.objects.filter(status='on_shelf').count(),
            'total_expired': ConsignedProduct.objects.filter(status='on_shelf', expiry_date__lt=today).count(),
            'expiring_30d': ConsignedProduct.objects.filter(
                status='on_shelf', expiry_date__gte=today, expiry_date__lte=today + timedelta(days=30)
            ).count(),
            'expiring_90d': ConsignedProduct.objects.filter(
                status='on_shelf', expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90)
            ).count(),
            'used_ytd': UsageRecord.objects.filter(used_date__gte=year_start).count(),
            'used_last_30d': UsageRecord.objects.filter(used_date__gte=last_30d).count(),
            'active_sessions': ScanningSession.objects.filter(status='active').count(),
            'pending_orders': StockOrder.objects.filter(status__in=['submitted', 'confirmed']).count(),
            'pending_alerts': ExpiryAlert.objects.filter(status='pending').count(),
            'total_transfers': TransferSession.objects.filter(status='completed').count(),
            'total_sap_postings': ConsignmentPosting.objects.count(),
        }

        # Monthly usage trend (last 12 months)
        usage_trend = []
        for i in range(11, -1, -1):
            month_start = (today - timedelta(days=i * 30)).replace(day=1)
            if i > 0:
                month_end = (today - timedelta(days=(i-1) * 30)).replace(day=1)
            else:
                month_end = today + timedelta(days=1)
            count = UsageRecord.objects.filter(
                used_date__gte=month_start, used_date__lt=month_end
            ).count()
            usage_trend.append({
                'month': month_start.strftime('%Y-%m'),
                'count': count,
            })

        # Top hospitals by activity
        top_hospitals = list(
            ConsignedProduct.objects.filter(status='on_shelf')
            .values(name=F('department__hospital__name'))
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )

        # Alert distribution
        alert_dist = {}
        for tier, label in ExpiryAlert.TIER_CHOICES:
            alert_dist[tier] = ExpiryAlert.objects.filter(tier=tier, status='pending').count()

        # Inventory value at risk
        at_risk_value = ConsignedProduct.objects.filter(
            status='on_shelf',
            expiry_date__lt=today + timedelta(days=90),
            catalog_product__unit_price__isnull=False
        ).aggregate(
            total=Coalesce(Sum('catalog_product__unit_price'), Decimal('0'))
        )['total']

        return {
            'metrics': metrics,
            'usage_trend': usage_trend,
            'top_hospitals': top_hospitals,
            'alert_distribution': alert_dist,
            'inventory_value_at_risk': float(at_risk_value),
        }

    # ── Usage Pattern Analysis ───────────────────────────────────────────
    @staticmethod
    def get_usage_patterns(hospital_id=None, months=12):
        """Detailed usage pattern analysis for forecasting."""
        from .models import UsageRecord

        cutoff = timezone.now().date() - timedelta(days=months * 30)
        qs = UsageRecord.objects.filter(used_date__gte=cutoff)
        if hospital_id:
            qs = qs.filter(hospital_id=hospital_id)

        # Monthly distribution
        monthly = list(
            qs.annotate(month=TruncMonth('used_date'))
            .values('month')
            .annotate(count=Count('id'))
            .order_by('month')
        )

        # Day of week distribution
        dow = list(
            qs.annotate(dow=Extract('used_date', 'dow'))
            .values('dow')
            .annotate(count=Count('id'))
            .order_by('dow')
        )

        # By surgeon (top 15)
        by_surgeon = list(
            qs.exclude(surgeon_name='').exclude(surgeon_name__isnull=True)
            .values('surgeon_name')
            .annotate(
                count=Count('id'),
                hospitals=Count('hospital', distinct=True),
            )
            .order_by('-count')[:15]
        )

        # By procedure type
        by_procedure = list(
            qs.exclude(procedure_type='').exclude(procedure_type__isnull=True)
            .values('procedure_type')
            .annotate(count=Count('id'))
            .order_by('-count')[:15]
        )

        # By hospital
        by_hospital = list(
            qs.values('hospital__name')
            .annotate(
                count=Count('id'),
                unique_products=Count('product__material_code', distinct=True),
            )
            .order_by('-count')[:15]
        )

        # By product + size
        by_product = list(
            qs.values('product__product_name', 'product__size', 'product__material_code')
            .annotate(count=Count('id'))
            .order_by('-count')[:20]
        )

        return {
            'monthly': [{'month': m['month'].strftime('%Y-%m') if m['month'] else '', 'count': m['count']} for m in monthly],
            'day_of_week': dow,
            'by_surgeon': by_surgeon,
            'by_procedure': by_procedure,
            'by_hospital': by_hospital,
            'by_product': by_product,
            'total_usage': qs.count(),
            'avg_monthly': round(qs.count() / max(months, 1), 1),
        }

    # ── SAP Data Insights ────────────────────────────────────────────────
    @staticmethod
    def get_sap_movement_insights(months=12, plant=None):
        """Analyze SAP consignment posting patterns."""
        from .models import ConsignmentPosting

        cutoff = timezone.now().date() - timedelta(days=months * 30)
        qs = ConsignmentPosting.objects.filter(posting_date__gte=cutoff)
        if plant:
            qs = qs.filter(plant=plant)

        # Movement type breakdown
        mt_map = dict(ConsignmentPosting.MOVEMENT_TYPE_CHOICES)
        movement_types = list(
            qs.values('movement_type')
            .annotate(count=Count('id'), total_qty=Sum('quantity'))
            .order_by('-count')
        )
        for m in movement_types:
            m['description'] = mt_map.get(m['movement_type'], m['movement_type'])

        # Monthly trends by movement type
        monthly_trends = list(
            qs.annotate(month=TruncMonth('posting_date'))
            .values('month', 'movement_type')
            .annotate(count=Count('id'), total_qty=Sum('quantity'))
            .order_by('month', 'movement_type')
        )
        for m in monthly_trends:
            m['month'] = m['month'].strftime('%Y-%m') if m['month'] else ''
            m['description'] = mt_map.get(m['movement_type'], m['movement_type'])

        # Top materials by posting volume
        top_materials = list(
            qs.values('material', 'material_description')
            .annotate(
                count=Count('id'),
                fillup=Count('id', filter=Q(movement_type='631')),
                used=Count('id', filter=Q(movement_type='633')),
                returned=Count('id', filter=Q(movement_type='632')),
            )
            .order_by('-count')[:20]
        )

        # Top customers
        top_customers = list(
            qs.filter(customer_name__isnull=False).exclude(customer_name='')
            .values('customer', 'customer_name')
            .annotate(
                count=Count('id'),
                total_qty=Sum('quantity'),
            )
            .order_by('-count')[:15]
        )

        # Plant summary
        plants = list(
            qs.values('plant')
            .annotate(count=Count('id'), total_qty=Sum('quantity'))
            .order_by('-count')
        )

        # Fill-up vs usage ratio
        fillups = qs.filter(movement_type='631').count()
        usages = qs.filter(movement_type='633').count()
        returns = qs.filter(movement_type='632').count()

        return {
            'movement_types': movement_types,
            'monthly_trends': monthly_trends,
            'top_materials': top_materials,
            'top_customers': top_customers,
            'plants': plants,
            'ratios': {
                'fillup_count': fillups,
                'usage_count': usages,
                'return_count': returns,
                'usage_to_fillup_ratio': round(usages / max(fillups, 1), 2),
                'return_rate': round(returns / max(fillups, 1) * 100, 1),
            },
            'total_postings': qs.count(),
        }
