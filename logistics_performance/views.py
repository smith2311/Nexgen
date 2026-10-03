import csv
import io
import json
import secrets
from functools import wraps

from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse, JsonResponse
from django.db.models import (
    Count, Q, Sum, Case, When, F,
    IntegerField, ExpressionWrapper,
)
from django.db.models.functions import ExtractMonth, ExtractYear
from django.utils import timezone
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from datetime import date, timedelta
from .models import LogisticsPerformance, PincodeMaster, DomesticSalesCard, CourierMaster, DivisionMaster, ZoneMaster, ModeMaster, StateMaster, PlantMaster
from ui.decorators import uses_components


# ─────────────────────────────────────────────
# LP ROLE-BASED ACCESS CONTROL
# ─────────────────────────────────────────────

def _lp_role(user):
    """Return LP access role: 'super' | 'admin' | 'member' | None."""
    if not user.is_authenticated:
        return None
    if user.is_superuser or user.groups.filter(name='Logistics Performance').exists():
        return 'super'
    if user.groups.filter(name='LP-Admin').exists():
        return 'admin'
    if user.groups.filter(name='LP-Members').exists():
        return 'member'
    return None


def _lp_require(*roles):
    """Page-view decorator: redirect non-authorised users to LP home (or site root)."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if _lp_role(request.user) not in roles:
                return redirect('logistics:home')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _lp_api_require(*roles):
    """API-view decorator: return 403 JSON for unauthorised users."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if _lp_role(request.user) not in roles:
                return JsonResponse({'error': 'Permission denied'}, status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────
# MODULE HOME (submodule selector — Warehouse Inspection style)
# ─────────────────────────────────────────────
def logistics_home(request):
    """Landing page: redirect users with no LP role back to the site root."""
    role = _lp_role(request.user)
    if role is None:
        return redirect('/')
    return render(request, 'logistics_performance/home.html', {'lp_role': role})


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────
@_lp_require('super', 'member')
def master_dashboard(request):

    # Pincode statistics
    pincodes_count = PincodeMaster.objects.count()
    locations_count = PincodeMaster.objects.values('location').distinct().count()
    couriers_count = PincodeMaster.objects.values('courier_name').distinct().count()
    states_count = PincodeMaster.objects.values('state_name').distinct().count()
    high_tat_count = PincodeMaster.objects.filter(tat__gt=5).count()

    # Sales cards statistics
    sales_cards_count = DomesticSalesCard.objects.count()
    sales_divisions_count = DomesticSalesCard.objects.values('division').distinct().count()
    sales_pending_count = DomesticSalesCard.objects.filter(pending_show=True).count()

    # Courier master statistics
    courier_count = CourierMaster.objects.count()
    courier_codes_count = CourierMaster.objects.values('courier_code').distinct().count()

    # Division master statistics (soft-deleted excluded)
    division_count = DivisionMaster.objects.filter(is_deleted=False).filter(code=101).count()
    division_companies_count = (DivisionMaster.objects.filter(is_deleted=False).filter(code=101)
                                .exclude(companyname__isnull=True).exclude(companyname='')
                                .values('companyname').distinct().count())
    division_removed_count = DivisionMaster.all_objects.filter(is_deleted=True).count()

    # Logistics Performance statistics
    lp_total_count     = LogisticsPerformance.objects.count()
    lp_delivered_count = LogisticsPerformance.objects.filter(delivery_date__isnull=False).count()
    lp_with_edd_count  = LogisticsPerformance.objects.filter(expected_delivery_date__isnull=False).count()
    lp_pending_count   = LogisticsPerformance.objects.filter(
        Q(dispatch_date__isnull=True) | Q(delivery_date__isnull=True) |
        Q(basic_logistics_cost__isnull=True)
    ).count()

    # Recent records
    recent_pincodes    = PincodeMaster.objects.order_by('-updated_at')[:10]
    recent_sales_cards = DomesticSalesCard.objects.order_by('-id')[:10]
    recent_lp_records  = LogisticsPerformance.objects.order_by('-created_at')[:8]

    recent_lp_json = json.dumps([{
        'docket_no':    lp.docket_no or '—',
        'division':     lp.division or '—',
        'courier_name': lp.courier_name or '—',
        'dispatch_date': lp.dispatch_date.strftime('%d %b %Y') if lp.dispatch_date else '—',
        'delivery_date': lp.delivery_date.strftime('%d %b %Y') if lp.delivery_date else None,
    } for lp in recent_lp_records])

    context = {
        'pincodes_count': pincodes_count,
        'locations_count': locations_count,
        'couriers_count': couriers_count,
        'states_count': states_count,
        'high_tat_count': high_tat_count,

        'sales_cards_count': sales_cards_count,
        'sales_divisions_count': sales_divisions_count,
        'sales_pending_count': sales_pending_count,

        'courier_count': courier_count,
        'courier_codes_count': courier_codes_count,

        'division_count': division_count,
        'division_companies_count': division_companies_count,
        'division_removed_count': division_removed_count,

        'lp_total_count': lp_total_count,
        'lp_delivered_count': lp_delivered_count,
        'lp_with_edd_count': lp_with_edd_count,
        'lp_pending_count': lp_pending_count,

        'recent_pincodes': recent_pincodes,
        'recent_sales_cards': recent_sales_cards,
        'recent_lp_records': recent_lp_records,
        'recent_lp_json': recent_lp_json,
    }

    return render(
        request,
        'logistics_performance/master_dashboard.html',
        context
    )


# ─────────────────────────────────────────────
# LOCATION MASTER PAGE
# ─────────────────────────────────────────────
@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def pincode_master_list(request):

    queryset = PincodeMaster.objects.all()

    tat_gt = request.GET.get('tat_gt')

    if tat_gt:
        queryset = queryset.filter(tat__gt=tat_gt)

    context = {
        'pincodes': queryset,
        'total_count': queryset.count(),
        'active_count': queryset.filter(tat__gt=0).count(),
        'zones_count': queryset.values('state_name').distinct().count(),
        # Zones come from the Zone Master so the form only offers valid zones.
        'zones': list(ZoneMaster.objects.values_list('zone', flat=True)),
        # Modes come from the Mode Master so the form only offers valid modes.
        'modes': list(ModeMaster.objects.values_list('mode', flat=True)),
        # States come from the State Master so the form only offers valid states.
        'states': list(StateMaster.objects.values_list('state', flat=True)),
    }

    return render(
        request,
        'logistics_performance/location_master.html',
        context
    )


def pincode_master_create(request):
    return HttpResponse(
        'Pincode master create page (not implemented yet)'
    )


def pincode_master_upload(request):
    return HttpResponse(
        'Pincode master upload page (not implemented yet)'
    )


def pincode_master_detail(request, pk):

    pincode = get_object_or_404(PincodeMaster, pk=pk)

    return HttpResponse(
        f'Pincode master detail page for pincode {pincode.pincode}'
    )


def pincode_master_edit(request, pk):

    pincode = get_object_or_404(PincodeMaster, pk=pk)

    return HttpResponse(
        f'Edit pincode master {pincode.pk} page (not implemented yet)'
    )


# ─────────────────────────────────────────────
# DOMESTIC SALES MASTER
# ─────────────────────────────────────────────
@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def domestic_sales_list(request):
    return render(request, 'logistics_performance/domestic_sales_master.html', {})


def domestic_sales_create(request):
    return HttpResponse(
        'Domestic sales create page (not implemented yet)'
    )


def domestic_sales_upload(request):
    return HttpResponse(
        'Domestic sales upload page (not implemented yet)'
    )


def domestic_sales_edit(request, pk):

    sales_card = get_object_or_404(
        DomesticSalesCard,
        pk=pk
    )

    return HttpResponse(
        f'Edit domestic sales card {sales_card.pk} page (not implemented yet)'
    )


# ─────────────────────────────────────────────
# DOMESTIC SALES TYPE MASTER APIs
# ─────────────────────────────────────────────
def sales_list_api(request):
    """List all sales cards with pagination and filtering"""
    search = request.GET.get('search', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = DomesticSalesCard.objects.all()

    # Filter by search
    if search:
        queryset = queryset.filter(
            Q(division__icontains=search) |
            Q(sales_type__icontains=search) |
            Q(sales_type_description__icontains=search)
        )

    # Get total count
    total_count = queryset.count()

    # Pagination
    start = (page - 1) * per_page
    end = start + per_page
    sales_cards = queryset.order_by('-created_at')[start:end]

    # Serialize sales cards
    data = []
    for card in sales_cards:
        data.append({
            'id': card.id,
            'division': card.division,
            'sales_type': card.sales_type,
            'sales_type_description': card.sales_type_description,
            'pending_show': card.pending_show,
            'created_date': card.created_at.isoformat(),
            'updated_at': card.updated_at.isoformat(),
        })

    # Get stats
    stats = {
        'total': queryset.count(),
        'divisions': queryset.values('division').distinct().count(),
        'pending': queryset.filter(pending_show=True).count(),
    }

    return JsonResponse({
        "success": True,
        "count": len(data),
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "data": data,
        "stats": stats
    })


def sales_create_api(request):
    """Create a new sales card"""
    if request.method != 'POST':
        return JsonResponse({
            "success": False,
            "message": "Create requires POST method",
            "errors": {}
        }, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            "success": False,
            "message": "Invalid JSON",
            "errors": {}
        }, status=400)

    # Validation
    errors = {}
    required_fields = ['division', 'sales_type']
    for field in required_fields:
        if not data.get(field):
            errors[field] = ['This field is required']

    if errors:
        return JsonResponse({
            "success": False,
            "message": "Validation failed",
            "errors": errors
        }, status=400)

    try:
        sales_card = DomesticSalesCard.objects.create(
            division=data.get('division', ''),
            sales_type=data.get('sales_type', ''),
            sales_type_description=data.get('sales_type_description', ''),
            pending_show=data.get('pending_show', False),
            created_by=request.user.username,
            updated_by=request.user.username,
        )

        return JsonResponse({
            "success": True,
            "message": "Sales card created successfully",
            "id": sales_card.id,
            "data": {
                'id': sales_card.id,
                'division': sales_card.division,
                'sales_type': sales_card.sales_type,
                'pending_show': sales_card.pending_show,
            }
        }, status=201)
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": f"Error creating sales card: {str(e)}",
            "errors": {}
        }, status=500)


def sales_update_api(request, id):
    """Update an existing sales card"""
    if request.method != 'PUT':
        return JsonResponse({
            "success": False,
            "message": "Update requires PUT method",
            "errors": {}
        }, status=405)

    try:
        sales_card = DomesticSalesCard.objects.get(id=id)
    except DomesticSalesCard.DoesNotExist:
        return JsonResponse({
            "success": False,
            "message": f"Sales card with ID {id} not found",
            "errors": {}
        }, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            "success": False,
            "message": "Invalid JSON",
            "errors": {}
        }, status=400)

    try:
        sales_card.division = data.get('division', sales_card.division)
        sales_card.sales_type = data.get('sales_type', sales_card.sales_type)
        sales_card.sales_type_description = data.get('sales_type_description', sales_card.sales_type_description)
        sales_card.pending_show = data.get('pending_show', sales_card.pending_show)
        sales_card.save()

        return JsonResponse({
            "success": True,
            "message": "Sales card updated successfully",
            "id": sales_card.id,
            "data": {
                'id': sales_card.id,
                'division': sales_card.division,
                'sales_type': sales_card.sales_type,
                'pending_show': sales_card.pending_show,
            }
        })
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": f"Error updating sales card: {str(e)}",
            "errors": {}
        }, status=500)


def sales_detail_api(request, id):
    """Get a single sales card detail"""
    try:
        sales_card = DomesticSalesCard.objects.get(id=id)
        return JsonResponse({
            "success": True,
            "data": {
                'id': sales_card.id,
                'division': sales_card.division,
                'sales_type': sales_card.sales_type,
                'sales_type_description': sales_card.sales_type_description,
                'pending_show': sales_card.pending_show,
                'created_date': sales_card.created_at.isoformat(),
                'updated_at': sales_card.updated_at.isoformat(),
            }
        })
    except DomesticSalesCard.DoesNotExist:
        return JsonResponse({
            "success": False,
            "message": f"Sales card with ID {id} not found",
            "errors": {}
        }, status=404)


def sales_delete_api(request, id):
    """Delete a sales card"""
    if request.method != 'DELETE':
        return JsonResponse({
            "success": False,
            "message": "Delete requires DELETE method",
            "errors": {}
        }, status=405)

    try:
        sales_card = DomesticSalesCard.objects.get(id=id)
        sales_type = sales_card.sales_type
        sales_card.soft_delete(request.user.username)

        return JsonResponse({
            "success": True,
            "message": f"Sales card '{sales_type}' deleted successfully"
        })
    except DomesticSalesCard.DoesNotExist:
        return JsonResponse({
            "success": False,
            "message": f"Sales card with ID {id} not found",
            "errors": {}
        }, status=404)
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": f"Error deleting sales card: {str(e)}",
            "errors": {}
        }, status=500)


def sales_upload_api(request):
    """Handle bulk upload of sales cards (CSV/Excel)"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)

    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)

    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_sales_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['sales_upload_preview'] = {
        'token': token,
        'rows': preview_rows,
        'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'message': 'File parsed successfully',
        'token': token,
        'rows': preview_rows,
        'count': len(preview_rows),
    })


def sales_preview_api(request):
    """Get the preview of uploaded sales data"""
    preview = request.session.get('sales_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)

    return JsonResponse({
        'success': True,
        'token': preview['token'],
        'rows': preview['rows'],
        'count': len(preview['rows']),
    })


def sales_confirm_api(request):
    """Confirm and import previewed sales data"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('sales_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    imported = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        data = row['data']
        pending = str(data.get('pending_show', 'false')).strip().lower() in ('pending', 'true', 'yes', '1')
        # Revive a soft-deleted card (same division + sales_type) and overwrite; else create.
        existing = DomesticSalesCard.all_objects.filter(
            division__iexact=data.get('division', ''),
            sales_type__iexact=data.get('sales_type', ''),
            is_deleted=True,
        ).first()
        if existing:
            existing.division = data.get('division', '')
            existing.sales_type = data.get('sales_type', '')
            existing.sales_type_description = data.get('sales_type_description', '')
            existing.pending_show = pending
            existing.is_deleted = False
            existing.deleted_by = None
            existing.deleted_at = None
            existing.save()
        else:
            DomesticSalesCard.objects.create(
                division=data.get('division', ''),
                sales_type=data.get('sales_type', ''),
                sales_type_description=data.get('sales_type_description', ''),
                pending_show=pending,
            )
        imported += 1

    request.session.pop('sales_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'imported': imported,
        'skipped': len(preview['rows']) - imported,
        'total': len(preview['rows']),
    })


def _build_sales_preview(rows):
    """Build preview for sales upload"""
    preview_rows = []

    for idx, raw in enumerate(rows, start=2):
        data = {
            'division': (raw.get('division') or '').strip(),
            'sales_type': (raw.get('sales_type') or '').strip(),
            'sales_type_description': (raw.get('sales_type_description') or '').strip(),
            'pending_show': (raw.get('pending_show') or 'false').strip(),
        }
        errors = []

        if not data['division']:
            errors.append('Missing division')
        elif not DivisionMaster.objects.filter(
            division__iexact=data['division'], is_deleted=False
        ).filter(code=101).exists():
            errors.append('Division not in Division Master')
        if not data['sales_type']:
            errors.append('Missing sales_type')

        if errors:
            status = 'error'
            message = '; '.join(errors)
        else:
            status = 'ok'
            message = ''

        preview_rows.append({
            'row': idx,
            'status': status,
            'message': message,
            'data': data,
        })

    return preview_rows


# ─────────────────────────────────────────────
# LOCATION MASTER APIs
# ─────────────────────────────────────────────
def location_list_api(request):
    """List all locations with pagination and filtering"""
    search = request.GET.get('search', '').strip()
    zone = request.GET.get('zone', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = PincodeMaster.objects.all()

    # Filter by search
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) |
            Q(location__icontains=search) |
            Q(city__icontains=search) |
            Q(pincode__icontains=search) |
            Q(courier_code__icontains=search) |
            Q(courier_name__icontains=search)

        )

    # Filter by zone
    if zone:
        queryset = queryset.filter(zone=zone)

    # Get total count before pagination
    total_count = queryset.count()

    # Pagination
    start = (page - 1) * per_page
    end = start + per_page
    locations = queryset.order_by('-updated_at')[start:end]

    # Serialize locations
    data = []
    for loc in locations:
        data.append({
            'id': loc.id,
            'name': loc.name,
            'location': loc.location,
            'city': loc.city,
            'district': loc.district,
            'state': loc.state,
            'state_name': loc.state_name,
            'zone': loc.zone,
            'pincode': loc.pincode,
            'courier_code': loc.courier_code,
            'courier_name': loc.courier_name,
            'mode': loc.mode,
            'service_type': loc.service_type,
            'tat': loc.tat,
            'rate': str(loc.rate),
            'min_weight': str(loc.min_weight) if loc.min_weight else None,
            'oda_nonoda': loc.oda_nonoda,
            'is_active': loc.is_active,
            'notes': loc.notes,
            'created_at': loc.created_at.isoformat(),
            'updated_at': loc.updated_at.isoformat(),
        })

    # Get stats
    stats = {
        'total': queryset.count(),
        'active': queryset.filter(is_active=True).count(),
        'zones': queryset.values('zone').distinct().count(),
        'states': queryset.values('state_name').distinct().count(),
    }

    return JsonResponse({
        "success": True,
        "count": len(data),
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "data": data,
        "stats": stats
    })


def location_create_api(request):
    """Create a new location record"""
    if request.method != 'POST':
        return JsonResponse({
            "success": False,
            "message": "Create requires POST method",
            "errors": {}
        }, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            "success": False,
            "message": "Invalid JSON",
            "errors": {}
        }, status=400)

    # Validation
    errors = {}
    required_fields = ['name', 'state', 'courier_name', 'pincode', 'tat', 'rate', 'zone', 'mode', 'oda_nonoda']
    for field in required_fields:
        if not data.get(field):
            errors[field] = ['This field is required']

    if errors:
        return JsonResponse({
            "success": False,
            "message": "Validation failed",
            "errors": errors
        }, status=400)

    try:
        fields = dict(
            name=data.get('name', ''),
            location=data.get('location') or data.get('name', ''),
            city=data.get('city', ''),
            district=data.get('district', ''),
            state=data.get('state', ''),
            state_name=data.get('state_name') or data.get('state', ''),
            zone=data.get('zone', ''),
            pincode=data.get('pincode', ''),
            courier_code=data.get('courier_code', ''),
            courier_name=data.get('courier_name', ''),
            mode=data.get('mode', ''),
            service_type=data.get('service_type', ''),
            tat=int(data.get('tat', 0)),
            rate=float(data.get('rate', 0)),
            min_weight=float(data.get('min_weight', 0)) if data.get('min_weight') else None,
            oda_nonoda=data.get('oda_nonoda', ''),
            is_active=data.get('is_active', True),
            notes=data.get('notes', ''),
        )
        # A soft-deleted match is left untouched (is_deleted stays True); a new
        # active row is inserted. Active duplicates are blocked by the partial
        # unique constraint.
        location = PincodeMaster.objects.create(**fields)

        return JsonResponse({
            "success": True,
            "message": "Location created successfully",
            "id": location.id,
            "data": {
                'id': location.id,
                'name': location.name,
                'location': location.location,
                'city': location.city,
                'state_name': location.state_name,
                'zone': location.zone,
                'pincode': location.pincode,
                'is_active': location.is_active,
            }
        }, status=201)
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": f"Error creating location: {str(e)}",
            "errors": {}
        }, status=500)


def location_update_api(request, id):
    """Update an existing location record"""
    if request.method != 'PUT':
        return JsonResponse({
            "success": False,
            "message": "Update requires PUT method",
            "errors": {}
        }, status=405)

    try:
        location = PincodeMaster.objects.get(id=id)
    except PincodeMaster.DoesNotExist:
        return JsonResponse({
            "success": False,
            "message": f"Location with ID {id} not found",
            "errors": {}
        }, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            "success": False,
            "message": "Invalid JSON",
            "errors": {}
        }, status=400)

    try:
        # Update the location record
        location.name = data.get('name', location.name)
        location.location = data.get('location') or data.get('name', location.location)
        location.city = data.get('city', location.city)
        location.district = data.get('district', location.district)
        location.state = data.get('state', location.state)
        location.state_name = data.get('state_name') or data.get('state', location.state_name)
        location.zone = data.get('zone', location.zone)
        location.pincode = data.get('pincode', location.pincode)
        location.courier_code = data.get('courier_code', location.courier_code)
        location.courier_name = data.get('courier_name', location.courier_name)
        location.mode = data.get('mode', location.mode)
        location.service_type = data.get('service_type', location.service_type)

        if data.get('tat') is not None:
            location.tat = int(data.get('tat'))
        if data.get('rate') is not None:
            location.rate = float(data.get('rate'))
        if data.get('min_weight') is not None:
            location.min_weight = float(data.get('min_weight')) if data.get('min_weight') else None

        location.oda_nonoda = data.get('oda_nonoda', location.oda_nonoda)
        location.is_active = data.get('is_active', location.is_active)
        location.notes = data.get('notes', location.notes)

        location.save()

        return JsonResponse({
            "success": True,
            "message": "Location updated successfully",
            "id": location.id,
            "data": {
                'id': location.id,
                'name': location.name,
                'location': location.location,
                'city': location.city,
                'state_name': location.state_name,
                'zone': location.zone,
                'pincode': location.pincode,
                'is_active': location.is_active,
            }
        })
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": f"Error updating location: {str(e)}",
            "errors": {}
        }, status=500)


def location_delete_api(request, id):
    """Delete a location record"""
    if request.method != 'DELETE':
        return JsonResponse({
            "success": False,
            "message": "Delete requires DELETE method",
            "errors": {}
        }, status=405)

    try:
        location = PincodeMaster.objects.get(id=id)
        location_name = location.location
        location.soft_delete(request.user.username)

        return JsonResponse({
            "success": True,
            "message": f"Location '{location_name}' deleted successfully"
        })
    except PincodeMaster.DoesNotExist:
        return JsonResponse({
            "success": False,
            "message": f"Location with ID {id} not found",
            "errors": {}
        }, status=404)
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": f"Error deleting location: {str(e)}",
            "errors": {}
        }, status=500)


def _read_uploaded_location_rows(file_obj, filename):
    filename = filename or ''
    ext = filename.split('.')[-1].lower()

    if ext == 'csv':
        text = file_obj.read().decode('utf-8-sig', errors='replace')
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for row in reader:
            rows.append({
                (k or '').strip().lower(): (v or '').strip()
                for k, v in row.items()
            })
        return rows

    if ext in ('xlsx', 'xls'):
        wb = load_workbook(filename=io.BytesIO(file_obj.read()), read_only=True, data_only=True)
        ws = wb.active
        rows = []
        headers = [str(cell).strip().lower() if cell is not None else '' for cell in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(row):
                continue
            rows.append({
                headers[i] if i < len(headers) else f'col_{i}': str(value).strip() if value is not None else ''
                for i, value in enumerate(row)
            })
        return rows

    raise ValueError(f'Unsupported file type: {ext}')


def _normalize_location_row(row):
    def norm(value):
        return (value or '').strip()

    return {
        'name': norm(row.get('name') or row.get('location')),
        'city': norm(row.get('city')),
        'district': norm(row.get('district')),
        'state': norm(row.get('state')),
        'zone': norm(row.get('zone')),
        'courier_code': norm(row.get('courier_code')),
        'courier_name': norm(row.get('courier_name')),
        'mode': norm(row.get('mode')),
        'rate': norm(row.get('rate')),
        'tat': norm(row.get('tat')),
        'oda_nonoda': norm(row.get('oda_nonoda')),
        'pincode': norm(row.get('pincode')),
        'service_type': norm(row.get('service_type')),
        'min_weight': norm(row.get('min_weight')),
        'is_active': norm(row.get('is_active')),
    }


def _location_incoming_values(data):
    """DB-writable values derived from one uploaded row (excludes the 4 key fields
    name/pincode/courier_name/mode, and excludes notes which the upload never carries)."""
    return {
        'location': data.get('name') or data.get('location', ''),
        'city': data.get('city', ''),
        'district': data.get('district', ''),
        'state': data.get('state', ''),
        'state_name': data.get('state_name') or data.get('state', ''),
        'zone': data.get('zone', ''),
        'courier_code': data.get('courier_code', ''),
        'service_type': data.get('service_type', ''),
        'tat': int(data['tat']) if (data.get('tat') or '').isdigit() else 0,
        'rate': float(data['rate']) if data.get('rate') else 0.0,
        'min_weight': float(data['min_weight']) if data.get('min_weight') else None,
        'oda_nonoda': data.get('oda_nonoda', ''),
        'is_active': str(data.get('is_active', 'true')).lower() == 'true',
    }


def _location_row_differs(incoming, obj):
    """True if any non-key field on the uploaded row differs from the existing record."""
    for field, val in incoming.items():
        cur = getattr(obj, field, None)
        if field in ('rate', 'tat'):
            if float(cur or 0) != float(val or 0):
                return True
        elif field == 'min_weight':
            a = None if cur is None else float(cur)
            b = None if val is None else float(val)
            if a != b:
                return True
        elif field == 'is_active':
            if bool(cur) != bool(val):
                return True
        else:
            if (cur or '') != (val or ''):
                return True
    return False


def _build_location_preview(rows):
    seen_keys = set()
    preview_rows = []

    for idx, raw in enumerate(rows, start=2):
        data = _normalize_location_row(raw)
        errors = []

        if not data['name']:
            errors.append('Missing name')

        # Pincode — required, exactly 6 digits, numbers only
        if not data['pincode']:
            errors.append('Missing pincode')
        else:
            pin = data['pincode']
            if pin.endswith('.0'):      # tolerate Excel numeric cells (380001.0)
                pin = pin[:-2]
            if not (pin.isdigit() and len(pin) == 6):
                errors.append("Pincode '%s' must be exactly 6 digits (numbers only)" % data['pincode'])

        # TAT — whole number of days
        if data['tat'] and not data['tat'].replace('.0', '').isdigit():
            errors.append("Invalid tat '%s' (must be a whole number)" % data['tat'])

        # Rate — a non-negative number
        if data['rate']:
            try:
                if float(data['rate']) < 0:
                    errors.append("Rate '%s' cannot be negative" % data['rate'])
            except ValueError:
                errors.append("Invalid rate '%s' (must be a number)" % data['rate'])

        # Min weight — a non-negative number (optional)
        if data['min_weight']:
            try:
                if float(data['min_weight']) < 0:
                    errors.append("Min weight '%s' cannot be negative" % data['min_weight'])
            except ValueError:
                errors.append("Invalid min weight '%s' (must be a number)" % data['min_weight'])

        # Courier validation — the courier_code + courier_name pair must exist
        # in the Courier Master, otherwise the row cannot be imported.
        if not data['courier_code'] or not data['courier_name']:
            errors.append('Missing courier code/name')
        elif not CourierMaster.objects.filter(
            courier_code__iexact=data['courier_code'],
            courier_name__iexact=data['courier_name'],
        ).exists():
            errors.append("Courier '%s / %s' not in Courier Master" % (data['courier_code'], data['courier_name']))

        # Zone validation — the zone must exist in the Zone Master.
        if not data['zone']:
            errors.append('Missing zone')
        elif not ZoneMaster.objects.filter(zone__iexact=data['zone']).exists():
            errors.append("Zone '%s' not in Zone Master" % data['zone'])

        # Mode validation — the mode must exist in the Mode Master.
        if not data['mode']:
            errors.append('Missing mode')
        elif not ModeMaster.objects.filter(mode__iexact=data['mode']).exists():
            errors.append("Mode '%s' not in Mode Master" % data['mode'])

        # State validation — the state must exist in the State Master.
        if not data['state']:
            errors.append('Missing state')
        elif not StateMaster.objects.filter(state__iexact=data['state']).exists():
            errors.append("State '%s' not in State Master" % data['state'])

        # Duplicate = name + pincode + courier_name + mode all match
        dup_key = (
            data['name'].lower(),
            data['pincode'].lower(),
            data['courier_name'].lower(),
            data['mode'].lower(),
        )
        duplicate_in_file = bool(data['pincode']) and dup_key in seen_keys
        if data['pincode']:
            seen_keys.add(dup_key)

        existing_obj = None
        if data['pincode']:
            existing_obj = PincodeMaster.objects.filter(
                name__iexact=data['name'],
                pincode=data['pincode'],
                courier_name__iexact=data['courier_name'],
                mode__iexact=data['mode'],
            ).first()

        if errors:
            status = 'error'
            message = '; '.join(errors)
        elif duplicate_in_file:
            # Same key (name + pincode + courier_name + mode) repeated within this file.
            status = 'duplicate'
            message = 'Duplicate row in file'
        elif existing_obj is not None:
            # Key matches an existing record — update only if other data changed.
            if _location_row_differs(_location_incoming_values(data), existing_obj):
                status = 'update'
                message = 'Will update existing record'
            else:
                status = 'duplicate'
                message = 'Already exists (no change)'
        else:
            status = 'ok'
            message = ''

        preview_rows.append({
            'row': idx,
            'status': status,
            'message': message,
            'data': data,
        })

    return preview_rows


def location_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)

    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)

    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_location_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['location_upload_preview'] = {
        'token': token,
        'rows': preview_rows,
        'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'message': 'File parsed successfully',
        'token': token,
        'rows': preview_rows,
        'count': len(preview_rows),
    })


def location_preview_api(request):
    preview = request.session.get('location_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)

    return JsonResponse({
        'success': True,
        'token': preview['token'],
        'rows': preview['rows'],
        'count': len(preview['rows']),
    })


def location_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('location_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    created = 0
    updated = 0
    for row in preview['rows']:
        if row['status'] not in ('ok', 'update'):
            continue
        data = row['data']
        values = _location_incoming_values(data)

        # Match ACTIVE rows only. A soft-deleted match is ignored, so a new
        # active row is inserted and the deleted twin keeps is_deleted=True.
        obj = PincodeMaster.objects.filter(
            name__iexact=data.get('name', ''),
            pincode=data['pincode'],
            courier_name__iexact=data.get('courier_name', ''),
            mode__iexact=data.get('mode', ''),
        ).first()

        if obj is not None:
            # Active match -> update only the non-key fields.
            for field, value in values.items():
                setattr(obj, field, value)
            obj.save()
            updated += 1
        else:
            PincodeMaster.objects.create(
                name=data.get('name', ''),
                pincode=data['pincode'],
                courier_name=data.get('courier_name', ''),
                mode=data.get('mode', ''),
                **values,
            )
            created += 1

    imported = created + updated
    request.session.pop('location_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'imported': imported,
        'created': created,
        'updated': updated,
        'skipped': len(preview['rows']) - imported,
        'total': len(preview['rows']),
    })


# ─────────────────────────────────────────────
# COURIER MASTER
# ─────────────────────────────────────────────
@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def courier_master_list(request):
    return render(request, 'logistics_performance/courier_master.html', {})


def courier_list_api(request):
    """List all couriers with pagination and search."""
    search = request.GET.get('search', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = CourierMaster.objects.all()
    if search:
        queryset = queryset.filter(
            Q(courier_code__icontains=search) | Q(courier_name__icontains=search)
        )

    total_count = queryset.count()
    start = (page - 1) * per_page
    end = start + per_page
    couriers = queryset.order_by('-updated_at')[start:end]

    data = []
    for c in couriers:
        data.append({
            'id': c.id,
            'courier_code': c.courier_code,
            'courier_name': c.courier_name,
            'created_at': c.created_at.isoformat(),
            'updated_at': c.updated_at.isoformat(),
        })

    stats = {
        'total': total_count,
        'codes': queryset.values('courier_code').distinct().count(),
        'names': queryset.values('courier_name').distinct().count(),
    }

    return JsonResponse({
        "success": True,
        "count": len(data),
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "data": data,
        "stats": stats,
    })


def courier_create_api(request):
    """Create a new courier record."""
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "Create requires POST method", "errors": {}}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    errors = {}
    for field in ['courier_code', 'courier_name']:
        if not data.get(field):
            errors[field] = ['This field is required']
    if errors:
        return JsonResponse({"success": False, "message": "Validation failed", "errors": errors}, status=400)

    code = (data.get('courier_code') or '').strip()
    name = (data.get('courier_name') or '').strip()
    if CourierMaster.objects.filter(courier_code__iexact=code, courier_name__iexact=name).exists():
        return JsonResponse({
            "success": False, "message": "This courier already exists",
            "errors": {"courier_code": ["Already exists"]},
        }, status=400)

    try:
        # Revive a soft-deleted match (flag is_deleted back to False) instead of a duplicate insert
        courier = CourierMaster.all_objects.filter(courier_code__iexact=code, courier_name__iexact=name).first()
        if courier:
            courier.courier_code = code
            courier.courier_name = name
            courier.is_deleted = False
            courier.deleted_by = None
            courier.deleted_at = None
            courier.save()
        else:
            courier = CourierMaster.objects.create(courier_code=code, courier_name=name)
        return JsonResponse({
            "success": True, "message": "Courier created successfully", "id": courier.id,
            "data": {'id': courier.id, 'courier_code': courier.courier_code, 'courier_name': courier.courier_name},
        }, status=201)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error creating courier: {str(e)}", "errors": {}}, status=500)


def courier_update_api(request, id):
    """Update an existing courier record."""
    if request.method != 'PUT':
        return JsonResponse({"success": False, "message": "Update requires PUT method", "errors": {}}, status=405)

    try:
        courier = CourierMaster.objects.get(id=id)
    except CourierMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Courier with ID {id} not found", "errors": {}}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    code = (data.get('courier_code') or courier.courier_code).strip()
    name = (data.get('courier_name') or courier.courier_name).strip()
    if CourierMaster.objects.filter(
        courier_code__iexact=code, courier_name__iexact=name
    ).exclude(id=id).exists():
        return JsonResponse({
            "success": False, "message": "This courier already exists",
            "errors": {"courier_code": ["Already exists"]},
        }, status=400)

    try:
        courier.courier_code = code
        courier.courier_name = name
        courier.save()
        return JsonResponse({
            "success": True, "message": "Courier updated successfully", "id": courier.id,
            "data": {'id': courier.id, 'courier_code': courier.courier_code, 'courier_name': courier.courier_name},
        })
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error updating courier: {str(e)}", "errors": {}}, status=500)


def courier_delete_api(request, id):
    """Delete a courier record."""
    if request.method != 'DELETE':
        return JsonResponse({"success": False, "message": "Delete requires DELETE method", "errors": {}}, status=405)

    try:
        courier = CourierMaster.objects.get(id=id)
        label = f"{courier.courier_code} - {courier.courier_name}"
        courier.soft_delete(request.user.username)
        return JsonResponse({"success": True, "message": f"Courier '{label}' deleted successfully"})
    except CourierMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Courier with ID {id} not found", "errors": {}}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error deleting courier: {str(e)}", "errors": {}}, status=500)


def _normalize_courier_row(row):
    def norm(value):
        return (value or '').strip()
    return {
        'courier_code': norm(row.get('courier_code') or row.get('code')),
        'courier_name': norm(row.get('courier_name') or row.get('name')),
    }


def _build_courier_preview(rows):
    seen_keys = set()
    preview_rows = []

    for idx, raw in enumerate(rows, start=2):
        data = _normalize_courier_row(raw)
        errors = []
        if not data['courier_code']:
            errors.append('Missing courier_code')
        if not data['courier_name']:
            errors.append('Missing courier_name')

        dup_key = (data['courier_code'].lower(), data['courier_name'].lower())
        has_key = bool(data['courier_code'] and data['courier_name'])
        duplicate_in_file = has_key and dup_key in seen_keys
        if has_key:
            seen_keys.add(dup_key)

        exists_in_db = False
        if has_key:
            exists_in_db = CourierMaster.objects.filter(
                courier_code__iexact=data['courier_code'],
                courier_name__iexact=data['courier_name'],
            ).exists()

        if errors:
            status = 'error'
            message = '; '.join(errors)
        elif duplicate_in_file:
            status = 'duplicate'
            message = 'Duplicate row in file'
        elif exists_in_db:
            status = 'duplicate'
            message = 'Already exists'
        else:
            status = 'ok'
            message = ''

        preview_rows.append({'row': idx, 'status': status, 'message': message, 'data': data})

    return preview_rows


def courier_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)

    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)

    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_courier_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['courier_upload_preview'] = {
        'token': token,
        'rows': preview_rows,
        'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'message': 'File parsed successfully',
        'token': token,
        'rows': preview_rows,
        'count': len(preview_rows),
    })


def courier_preview_api(request):
    preview = request.session.get('courier_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)

    return JsonResponse({
        'success': True,
        'token': preview['token'],
        'rows': preview['rows'],
        'count': len(preview['rows']),
    })


def courier_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('courier_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    created = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        data = row['data']
        existing = CourierMaster.all_objects.filter(
            courier_code__iexact=data['courier_code'],
            courier_name__iexact=data['courier_name'],
        ).first()
        if existing is None:
            CourierMaster.objects.create(
                courier_code=data['courier_code'],
                courier_name=data['courier_name'],
            )
            created += 1
        elif existing.is_deleted:
            existing.courier_code = data['courier_code']
            existing.courier_name = data['courier_name']
            existing.is_deleted = False
            existing.deleted_by = None
            existing.deleted_at = None
            existing.save()
            created += 1
        # active duplicate -> keep current behavior (skip)

    request.session.pop('courier_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'imported': created,
        'created': created,
        'updated': 0,
        'skipped': len(preview['rows']) - created,
        'total': len(preview['rows']),
    })


# ─────────────────────────────────────────────
# DIVISION MASTER  (soft-delete + audit)
# ─────────────────────────────────────────────
def _current_username(request):
    u = getattr(request, 'user', None)
    if u is not None and getattr(u, 'is_authenticated', False):
        return u.get_username() or getattr(u, 'email', '') or ''
    return ''


@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def division_master_list(request):
    return render(request, 'logistics_performance/division_master.html', {})


def division_list_api(request):
    """List non-deleted divisions with pagination and search."""
    search = request.GET.get('search', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = DivisionMaster.objects.filter(is_deleted=False).filter(code=101)
    if search:
        queryset = queryset.filter(
            Q(companyname__icontains=search) | Q(division__icontains=search)
        )

    total_count = queryset.count()
    start = (page - 1) * per_page
    end = start + per_page
    rows = queryset.order_by('-updated_at')[start:end]

    data = []
    for d in rows:
        data.append({
            'id': d.id,
            'companyname': d.companyname,
            'division': d.division,
            'code': d.code,
            'created_by': d.created_by,
            'created_date': d.created_at.isoformat() if d.created_at else None,
            'modified_date': d.updated_at.isoformat() if d.updated_at else None,
        })

    stats = {
        'total': total_count,
        'companies': queryset.exclude(companyname__isnull=True).exclude(companyname='')
                            .values('companyname').distinct().count(),
        'divisions': queryset.values('division').distinct().count(),
    }

    return JsonResponse({
        "success": True, "count": len(data), "total": total_count,
        "page": page, "per_page": per_page, "data": data, "stats": stats,
    })


def division_create_api(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "Create requires POST method", "errors": {}}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    errors = {}
    for field in ['companyname', 'division', 'code']:
        if not data.get(field):
            errors[field] = ['This field is required']
    if errors:
        return JsonResponse({"success": False, "message": "Validation failed", "errors": errors}, status=400)

    cn = (data.get('companyname') or '').strip()
    dv = (data.get('division') or '').strip()
    try:
        cd = int(str(data.get('code')).strip())
    except (TypeError, ValueError):
        return JsonResponse({
            "success": False, "message": "Validation failed",
            "errors": {"code": ["Code must be a number"]},
        }, status=400)
    if DivisionMaster.objects.filter(companyname__iexact=cn, division__iexact=dv, code=cd, is_deleted=False).filter(code=101).exists():
        return JsonResponse({
            "success": False, "message": "This division already exists",
            "errors": {"division": ["Already exists"]},
        }, status=400)

    try:
        obj = DivisionMaster.objects.create(
            companyname=cn, division=dv, code=cd, created_by=_current_username(request),
        )
        return JsonResponse({
            "success": True, "message": "Division created successfully", "id": obj.id,
            "data": {'id': obj.id, 'companyname': obj.companyname, 'division': obj.division, 'code': obj.code},
        }, status=201)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error creating division: {str(e)}", "errors": {}}, status=500)


def division_update_api(request, id):
    if request.method != 'PUT':
        return JsonResponse({"success": False, "message": "Update requires PUT method", "errors": {}}, status=405)
    try:
        obj = DivisionMaster.objects.get(id=id, is_deleted=False)
    except DivisionMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Division with ID {id} not found", "errors": {}}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    cn = (data.get('companyname') if data.get('companyname') is not None else obj.companyname or '').strip()
    dv = (data.get('division') or obj.division).strip()
    raw_code = data.get('code') if data.get('code') not in (None, '') else obj.code
    try:
        cd = int(str(raw_code).strip())
    except (TypeError, ValueError):
        return JsonResponse({
            "success": False, "message": "Validation failed",
            "errors": {"code": ["Code must be a number"]},
        }, status=400)
    if DivisionMaster.objects.filter(
        companyname__iexact=cn, division__iexact=dv, code=cd, is_deleted=False
    ).filter(code=101).exclude(id=id).exists():
        return JsonResponse({
            "success": False, "message": "This division already exists",
            "errors": {"division": ["Already exists"]},
        }, status=400)

    try:
        obj.companyname = cn
        obj.division = dv
        obj.code = cd
        obj.save()
        return JsonResponse({
            "success": True, "message": "Division updated successfully", "id": obj.id,
            "data": {'id': obj.id, 'companyname': obj.companyname, 'division': obj.division, 'code': obj.code},
        })
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error updating division: {str(e)}", "errors": {}}, status=500)


def division_delete_api(request, id):
    """Soft delete — never removes the row, just hides it from the UI."""
    if request.method != 'DELETE':
        return JsonResponse({"success": False, "message": "Delete requires DELETE method", "errors": {}}, status=405)
    try:
        obj = DivisionMaster.objects.get(id=id, is_deleted=False)
    except DivisionMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Division with ID {id} not found", "errors": {}}, status=404)
    try:
        label = f"{obj.companyname or ''} - {obj.division}".strip(' -')
        obj.soft_delete(_current_username(request))
        return JsonResponse({"success": True, "message": f"Division '{label}' removed successfully"})
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error removing division: {str(e)}", "errors": {}}, status=500)


def _normalize_division_row(row):
    def norm(value):
        return (value or '').strip()
    return {
        'companyname': norm(row.get('companyname') or row.get('company') or row.get('company_name')),
        'division': norm(row.get('division')),
        'code': norm(row.get('code'))
    }


def _build_division_preview(rows):
    seen_keys = set()
    preview_rows = []

    for idx, raw in enumerate(rows, start=2):
        data = _normalize_division_row(raw)
        errors = []
        if not data['companyname']:
            errors.append('Missing companyname')
        if not data['division']:
            errors.append('Missing division')
        if not data['code']:
            errors.append('Missing code')
        elif not str(data['code']).strip().isdigit():
            errors.append('Code must be a number')

        dup_key = (data['companyname'].lower(), data['division'].lower(), str(data['code']).strip())
        has_key = bool(data['companyname'] and data['division'] and str(data['code']).strip().isdigit())
        duplicate_in_file = has_key and dup_key in seen_keys
        if has_key:
            seen_keys.add(dup_key)

        exists_in_db = False
        if has_key:
            exists_in_db = DivisionMaster.objects.filter(
                companyname__iexact=data['companyname'],
                division__iexact=data['division'],
                code=int(str(data['code']).strip()),
                is_deleted=False,
            ).exists()

        if errors:
            status = 'error'
            message = '; '.join(errors)
        elif duplicate_in_file:
            status = 'duplicate'
            message = 'Duplicate row in file'
        elif exists_in_db:
            status = 'duplicate'
            message = 'Already exists'
        else:
            status = 'ok'
            message = ''

        preview_rows.append({'row': idx, 'status': status, 'message': message, 'data': data})

    return preview_rows


def division_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)
    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)
    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_division_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['division_upload_preview'] = {
        'token': token, 'rows': preview_rows, 'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True, 'message': 'File parsed successfully', 'token': token,
        'rows': preview_rows, 'count': len(preview_rows),
    })


def division_preview_api(request):
    preview = request.session.get('division_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)
    return JsonResponse({
        'success': True, 'token': preview['token'],
        'rows': preview['rows'], 'count': len(preview['rows']),
    })


def division_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('division_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    user = _current_username(request)
    created = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        data = row['data']
        code = int(str(data['code']).strip())
        # Revive a soft-deleted division (same companyname + division + code); else create.
        existing = DivisionMaster.all_objects.filter(
            companyname__iexact=data['companyname'], division__iexact=data['division'],
            code=code, is_deleted=True,
        ).first()
        if existing:
            existing.companyname = data['companyname']
            existing.division = data['division']
            existing.code = code
            existing.is_deleted = False
            existing.deleted_by = None
            existing.deleted_at = None
            existing.save()
        else:
            DivisionMaster.objects.create(
                companyname=data['companyname'], division=data['division'],
                code=code, created_by=user,
            )
        created += 1

    request.session.pop('division_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True, 'imported': created, 'created': created, 'updated': 0,
        'skipped': len(preview['rows']) - created, 'total': len(preview['rows']),
    })


# ─────────────────────────────────────────────
# ZONE MASTER
# ─────────────────────────────────────────────
@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def zone_master_list(request):
    return render(request, 'logistics_performance/zone_master.html', {})


def zone_list_api(request):
    """List all zones with pagination and search."""
    search = request.GET.get('search', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = ZoneMaster.objects.all()
    if search:
        queryset = queryset.filter(zone__icontains=search)

    total_count = queryset.count()
    start = (page - 1) * per_page
    end = start + per_page
    zones = queryset.order_by('zone')[start:end]

    data = []
    for z in zones:
        data.append({
            'id': z.id,
            'zone': z.zone,
            'created_at': z.created_at.isoformat(),
            'updated_at': z.updated_at.isoformat(),
        })

    stats = {'total': total_count}

    return JsonResponse({
        "success": True, "count": len(data), "total": total_count,
        "page": page, "per_page": per_page, "data": data, "stats": stats,
    })


def zone_create_api(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "Create requires POST method", "errors": {}}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    zone = (data.get('zone') or '').strip()
    if not zone:
        return JsonResponse({"success": False, "message": "Validation failed",
                             "errors": {"zone": ["This field is required"]}}, status=400)
    if ZoneMaster.objects.filter(zone__iexact=zone).exists():
        return JsonResponse({"success": False, "message": "This zone already exists",
                             "errors": {"zone": ["Already exists"]}}, status=400)

    try:
        # Revive a soft-deleted match (flag is_deleted back to False) instead of a duplicate insert
        obj = ZoneMaster.all_objects.filter(zone__iexact=zone).first()
        if obj:
            obj.zone = zone
            obj.is_deleted = False
            obj.deleted_by = None
            obj.deleted_at = None
            obj.save()
        else:
            obj = ZoneMaster.objects.create(zone=zone)
        return JsonResponse({"success": True, "message": "Zone created successfully", "id": obj.id,
                             "data": {'id': obj.id, 'zone': obj.zone}}, status=201)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error creating zone: {str(e)}", "errors": {}}, status=500)


def zone_update_api(request, id):
    if request.method != 'PUT':
        return JsonResponse({"success": False, "message": "Update requires PUT method", "errors": {}}, status=405)
    try:
        obj = ZoneMaster.objects.get(id=id)
    except ZoneMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Zone with ID {id} not found", "errors": {}}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    zone = (data.get('zone') or obj.zone).strip()
    if ZoneMaster.objects.filter(zone__iexact=zone).exclude(id=id).exists():
        return JsonResponse({"success": False, "message": "This zone already exists",
                             "errors": {"zone": ["Already exists"]}}, status=400)
    try:
        obj.zone = zone
        obj.save()
        return JsonResponse({"success": True, "message": "Zone updated successfully", "id": obj.id,
                             "data": {'id': obj.id, 'zone': obj.zone}})
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error updating zone: {str(e)}", "errors": {}}, status=500)


def zone_delete_api(request, id):
    if request.method != 'DELETE':
        return JsonResponse({"success": False, "message": "Delete requires DELETE method", "errors": {}}, status=405)
    try:
        obj = ZoneMaster.objects.get(id=id)
        label = obj.zone
        obj.soft_delete(request.user.username)
        return JsonResponse({"success": True, "message": f"Zone '{label}' deleted successfully"})
    except ZoneMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Zone with ID {id} not found", "errors": {}}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error deleting zone: {str(e)}", "errors": {}}, status=500)


def _normalize_zone_row(row):
    return {'zone': (row.get('zone') or '').strip()}


def _build_zone_preview(rows):
    seen_keys = set()
    preview_rows = []
    for idx, raw in enumerate(rows, start=2):
        data = _normalize_zone_row(raw)
        errors = []
        if not data['zone']:
            errors.append('Missing zone')

        key = data['zone'].lower()
        duplicate_in_file = bool(data['zone']) and key in seen_keys
        if data['zone']:
            seen_keys.add(key)

        exists_in_db = bool(data['zone']) and ZoneMaster.objects.filter(zone__iexact=data['zone']).exists()

        if errors:
            status = 'error'
            message = '; '.join(errors)
        elif duplicate_in_file:
            status = 'duplicate'
            message = 'Duplicate row in file'
        elif exists_in_db:
            status = 'duplicate'
            message = 'Already exists'
        else:
            status = 'ok'
            message = ''

        preview_rows.append({'row': idx, 'status': status, 'message': message, 'data': data})

    return preview_rows


def zone_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)
    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)
    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_zone_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['zone_upload_preview'] = {
        'token': token, 'rows': preview_rows, 'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True, 'message': 'File parsed successfully', 'token': token,
        'rows': preview_rows, 'count': len(preview_rows),
    })


def zone_preview_api(request):
    preview = request.session.get('zone_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)
    return JsonResponse({
        'success': True, 'token': preview['token'],
        'rows': preview['rows'], 'count': len(preview['rows']),
    })


def _revive_or_create_simple(model, field, value):
    """Single-unique-field masters (zone/mode/state): revive a soft-deleted
    match (overwrite the value) or create new. Returns True if a row was
    created or revived, False if an ACTIVE duplicate already existed (skip)."""
    existing = model.all_objects.filter(**{field + '__iexact': value}).first()
    if existing is None:
        model.objects.create(**{field: value})
        return True
    if existing.is_deleted:
        setattr(existing, field, value)
        existing.is_deleted = False
        existing.deleted_by = None
        existing.deleted_at = None
        existing.save()
        return True
    return False  # active duplicate -> keep current behavior (skip)


def zone_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('zone_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    created = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        if _revive_or_create_simple(ZoneMaster, 'zone', row['data']['zone']):
            created += 1

    request.session.pop('zone_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True, 'imported': created, 'created': created, 'updated': 0,
        'skipped': len(preview['rows']) - created, 'total': len(preview['rows']),
    })


# ─────────────────────────────────────────────
# MODE MASTER
# ─────────────────────────────────────────────
@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def mode_master_list(request):
    return render(request, 'logistics_performance/mode_master.html', {})


def mode_list_api(request):
    """List all modes with pagination and search."""
    search = request.GET.get('search', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = ModeMaster.objects.all()
    if search:
        queryset = queryset.filter(mode__icontains=search)

    total_count = queryset.count()
    start = (page - 1) * per_page
    end = start + per_page
    modes = queryset.order_by('mode')[start:end]

    data = [{
        'id': m.id,
        'mode': m.mode,
        'created_at': m.created_at.isoformat(),
        'updated_at': m.updated_at.isoformat(),
    } for m in modes]

    return JsonResponse({
        "success": True, "count": len(data), "total": total_count,
        "page": page, "per_page": per_page, "data": data, "stats": {'total': total_count},
    })


def mode_create_api(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "Create requires POST method", "errors": {}}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    mode = (data.get('mode') or '').strip()
    if not mode:
        return JsonResponse({"success": False, "message": "Validation failed",
                             "errors": {"mode": ["This field is required"]}}, status=400)
    if ModeMaster.objects.filter(mode__iexact=mode).exists():
        return JsonResponse({"success": False, "message": "This mode already exists",
                             "errors": {"mode": ["Already exists"]}}, status=400)

    try:
        # Revive a soft-deleted match (flag is_deleted back to False) instead of a duplicate insert
        obj = ModeMaster.all_objects.filter(mode__iexact=mode).first()
        if obj:
            obj.mode = mode
            obj.is_deleted = False
            obj.deleted_by = None
            obj.deleted_at = None
            obj.save()
        else:
            obj = ModeMaster.objects.create(mode=mode)
        return JsonResponse({"success": True, "message": "Mode created successfully", "id": obj.id,
                             "data": {'id': obj.id, 'mode': obj.mode}}, status=201)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error creating mode: {str(e)}", "errors": {}}, status=500)


def mode_update_api(request, id):
    if request.method != 'PUT':
        return JsonResponse({"success": False, "message": "Update requires PUT method", "errors": {}}, status=405)
    try:
        obj = ModeMaster.objects.get(id=id)
    except ModeMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Mode with ID {id} not found", "errors": {}}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    mode = (data.get('mode') or obj.mode).strip()
    if ModeMaster.objects.filter(mode__iexact=mode).exclude(id=id).exists():
        return JsonResponse({"success": False, "message": "This mode already exists",
                             "errors": {"mode": ["Already exists"]}}, status=400)
    try:
        obj.mode = mode
        obj.save()
        return JsonResponse({"success": True, "message": "Mode updated successfully", "id": obj.id,
                             "data": {'id': obj.id, 'mode': obj.mode}})
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error updating mode: {str(e)}", "errors": {}}, status=500)


def mode_delete_api(request, id):
    if request.method != 'DELETE':
        return JsonResponse({"success": False, "message": "Delete requires DELETE method", "errors": {}}, status=405)
    try:
        obj = ModeMaster.objects.get(id=id)
        label = obj.mode
        obj.soft_delete(request.user.username)
        return JsonResponse({"success": True, "message": f"Mode '{label}' deleted successfully"})
    except ModeMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Mode with ID {id} not found", "errors": {}}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error deleting mode: {str(e)}", "errors": {}}, status=500)


def _normalize_mode_row(row):
    return {'mode': (row.get('mode') or '').strip()}


def _build_mode_preview(rows):
    seen_keys = set()
    preview_rows = []
    for idx, raw in enumerate(rows, start=2):
        data = _normalize_mode_row(raw)
        errors = []
        if not data['mode']:
            errors.append('Missing mode')

        key = data['mode'].lower()
        duplicate_in_file = bool(data['mode']) and key in seen_keys
        if data['mode']:
            seen_keys.add(key)

        exists_in_db = bool(data['mode']) and ModeMaster.objects.filter(mode__iexact=data['mode']).exists()

        if errors:
            status = 'error'
            message = '; '.join(errors)
        elif duplicate_in_file:
            status = 'duplicate'
            message = 'Duplicate row in file'
        elif exists_in_db:
            status = 'duplicate'
            message = 'Already exists'
        else:
            status = 'ok'
            message = ''

        preview_rows.append({'row': idx, 'status': status, 'message': message, 'data': data})

    return preview_rows


def mode_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)
    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)
    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_mode_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['mode_upload_preview'] = {
        'token': token, 'rows': preview_rows, 'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True, 'message': 'File parsed successfully', 'token': token,
        'rows': preview_rows, 'count': len(preview_rows),
    })


def mode_preview_api(request):
    preview = request.session.get('mode_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)
    return JsonResponse({
        'success': True, 'token': preview['token'],
        'rows': preview['rows'], 'count': len(preview['rows']),
    })


def mode_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('mode_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    created = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        if _revive_or_create_simple(ModeMaster, 'mode', row['data']['mode']):
            created += 1

    request.session.pop('mode_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True, 'imported': created, 'created': created, 'updated': 0,
        'skipped': len(preview['rows']) - created, 'total': len(preview['rows']),
    })


# ─────────────────────────────────────────────
# STATE MASTER
# ─────────────────────────────────────────────
@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def state_master_list(request):
    return render(request, 'logistics_performance/state_master.html', {})


def state_list_api(request):
    """List all states with pagination and search."""
    search = request.GET.get('search', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = StateMaster.objects.all()
    if search:
        queryset = queryset.filter(state__icontains=search)

    total_count = queryset.count()
    start = (page - 1) * per_page
    end = start + per_page
    states = queryset.order_by('state')[start:end]

    data = [{
        'id': s.id,
        'state': s.state,
        'created_at': s.created_at.isoformat(),
        'updated_at': s.updated_at.isoformat(),
    } for s in states]

    return JsonResponse({
        "success": True, "count": len(data), "total": total_count,
        "page": page, "per_page": per_page, "data": data, "stats": {'total': total_count},
    })


def state_create_api(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "Create requires POST method", "errors": {}}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    state = (data.get('state') or '').strip()
    if not state:
        return JsonResponse({"success": False, "message": "Validation failed",
                             "errors": {"state": ["This field is required"]}}, status=400)
    if StateMaster.objects.filter(state__iexact=state).exists():
        return JsonResponse({"success": False, "message": "This state already exists",
                             "errors": {"state": ["Already exists"]}}, status=400)

    try:
        # Revive a soft-deleted match (flag is_deleted back to False) instead of a duplicate insert
        obj = StateMaster.all_objects.filter(state__iexact=state).first()
        if obj:
            obj.state = state
            obj.is_deleted = False
            obj.deleted_by = None
            obj.deleted_at = None
            obj.save()
        else:
            obj = StateMaster.objects.create(state=state)
        return JsonResponse({"success": True, "message": "State created successfully", "id": obj.id,
                             "data": {'id': obj.id, 'state': obj.state}}, status=201)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error creating state: {str(e)}", "errors": {}}, status=500)


def state_update_api(request, id):
    if request.method != 'PUT':
        return JsonResponse({"success": False, "message": "Update requires PUT method", "errors": {}}, status=405)
    try:
        obj = StateMaster.objects.get(id=id)
    except StateMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"State with ID {id} not found", "errors": {}}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    state = (data.get('state') or obj.state).strip()
    if StateMaster.objects.filter(state__iexact=state).exclude(id=id).exists():
        return JsonResponse({"success": False, "message": "This state already exists",
                             "errors": {"state": ["Already exists"]}}, status=400)
    try:
        obj.state = state
        obj.save()
        return JsonResponse({"success": True, "message": "State updated successfully", "id": obj.id,
                             "data": {'id': obj.id, 'state': obj.state}})
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error updating state: {str(e)}", "errors": {}}, status=500)


def state_delete_api(request, id):
    if request.method != 'DELETE':
        return JsonResponse({"success": False, "message": "Delete requires DELETE method", "errors": {}}, status=405)
    try:
        obj = StateMaster.objects.get(id=id)
        label = obj.state
        obj.soft_delete(request.user.username)
        return JsonResponse({"success": True, "message": f"State '{label}' deleted successfully"})
    except StateMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"State with ID {id} not found", "errors": {}}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error deleting state: {str(e)}", "errors": {}}, status=500)


def _normalize_state_row(row):
    return {'state': (row.get('state') or '').strip()}


def _build_state_preview(rows):
    seen_keys = set()
    preview_rows = []
    for idx, raw in enumerate(rows, start=2):
        data = _normalize_state_row(raw)
        errors = []
        if not data['state']:
            errors.append('Missing state')

        key = data['state'].lower()
        duplicate_in_file = bool(data['state']) and key in seen_keys
        if data['state']:
            seen_keys.add(key)

        exists_in_db = bool(data['state']) and StateMaster.objects.filter(state__iexact=data['state']).exists()

        if errors:
            status = 'error'
            message = '; '.join(errors)
        elif duplicate_in_file:
            status = 'duplicate'
            message = 'Duplicate row in file'
        elif exists_in_db:
            status = 'duplicate'
            message = 'Already exists'
        else:
            status = 'ok'
            message = ''

        preview_rows.append({'row': idx, 'status': status, 'message': message, 'data': data})

    return preview_rows


def state_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)
    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)
    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_state_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['state_upload_preview'] = {
        'token': token, 'rows': preview_rows, 'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True, 'message': 'File parsed successfully', 'token': token,
        'rows': preview_rows, 'count': len(preview_rows),
    })


def state_preview_api(request):
    preview = request.session.get('state_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)
    return JsonResponse({
        'success': True, 'token': preview['token'],
        'rows': preview['rows'], 'count': len(preview['rows']),
    })


def state_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('state_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    created = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        if _revive_or_create_simple(StateMaster, 'state', row['data']['state']):
            created += 1

    request.session.pop('state_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True, 'imported': created, 'created': created, 'updated': 0,
        'skipped': len(preview['rows']) - created, 'total': len(preview['rows']),
    })


# ─────────────────────────────────────────────
# PLANT MASTER
# ─────────────────────────────────────────────
@_lp_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def plant_master_list(request):
    return render(request, 'logistics_performance/plant_master.html', {})


def _plant_dup_qs(qs, name, address):
    """Filter a PlantMaster queryset to the duplicate-key match: a plant is a
    duplicate only when BOTH plant_name AND plant_address are the same
    (case-insensitive). Empty/blank address is treated as a single 'no address'
    bucket so NULL and '' don't count as different plants."""
    qs = qs.filter(plant_name__iexact=name)
    address = (address or '').strip()
    if address:
        qs = qs.filter(plant_address__iexact=address)
    else:
        qs = qs.filter(Q(plant_address__isnull=True) | Q(plant_address=''))
    return qs


def plant_list_api(request):
    """List all plants with pagination and search."""
    search = request.GET.get('search', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    queryset = PlantMaster.objects.all()
    if search:
        queryset = queryset.filter(
            Q(plant_name__icontains=search) |
            Q(plant_company__icontains=search) |
            Q(plant_email__icontains=search)
        )

    total_count = queryset.count()
    start = (page - 1) * per_page
    end = start + per_page
    plants = queryset.order_by('plant_name')[start:end]

    data = [{
        'id': p.id,
        'plant_name': p.plant_name,
        'plant_email': p.plant_email or '',
        'plant_company': p.plant_company or '',
        'plant_address': p.plant_address or '',
        'created_at': p.created_at.isoformat(),
        'updated_at': p.updated_at.isoformat(),
    } for p in plants]

    return JsonResponse({
        "success": True, "count": len(data), "total": total_count,
        "page": page, "per_page": per_page, "data": data, "stats": {'total': total_count},
    })


def plant_create_api(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "Create requires POST method", "errors": {}}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    name = (data.get('plant_name') or '').strip()
    address = (data.get('plant_address') or '').strip()
    if not name:
        return JsonResponse({"success": False, "message": "Validation failed",
                             "errors": {"plant_name": ["This field is required"]}}, status=400)
    if _plant_dup_qs(PlantMaster.objects, name, address).exists():
        return JsonResponse({"success": False, "message": "This plant (same name & address) already exists",
                             "errors": {"plant_name": ["Already exists for this address"]}}, status=400)

    fields = {
        'plant_email':   (data.get('plant_email') or '').strip() or None,
        'plant_company': (data.get('plant_company') or '').strip() or None,
        'plant_address': address or None,
    }
    try:
        # Revive a soft-deleted match (same name + address) instead of a duplicate insert.
        obj = _plant_dup_qs(PlantMaster.all_objects, name, address).first()
        if obj:
            obj.plant_name = name
            for k, v in fields.items():
                setattr(obj, k, v)
            obj.is_deleted = False
            obj.deleted_by = None
            obj.deleted_at = None
            obj.save()
        else:
            obj = PlantMaster.objects.create(plant_name=name, **fields)
        return JsonResponse({"success": True, "message": "Plant created successfully", "id": obj.id,
                             "data": {'id': obj.id, 'plant_name': obj.plant_name}}, status=201)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error creating plant: {str(e)}", "errors": {}}, status=500)


def plant_update_api(request, id):
    if request.method != 'PUT':
        return JsonResponse({"success": False, "message": "Update requires PUT method", "errors": {}}, status=405)
    try:
        obj = PlantMaster.objects.get(id=id)
    except PlantMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Plant with ID {id} not found", "errors": {}}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON", "errors": {}}, status=400)

    name = (data.get('plant_name') or obj.plant_name).strip()
    address = (data.get('plant_address') or '').strip()
    if _plant_dup_qs(PlantMaster.objects, name, address).exclude(id=id).exists():
        return JsonResponse({"success": False, "message": "This plant (same name & address) already exists",
                             "errors": {"plant_name": ["Already exists for this address"]}}, status=400)
    try:
        obj.plant_name    = name
        obj.plant_email   = (data.get('plant_email') or '').strip() or None
        obj.plant_company = (data.get('plant_company') or '').strip() or None
        obj.plant_address = address or None
        obj.save()
        return JsonResponse({"success": True, "message": "Plant updated successfully", "id": obj.id,
                             "data": {'id': obj.id, 'plant_name': obj.plant_name}})
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error updating plant: {str(e)}", "errors": {}}, status=500)


def plant_delete_api(request, id):
    if request.method != 'DELETE':
        return JsonResponse({"success": False, "message": "Delete requires DELETE method", "errors": {}}, status=405)
    try:
        obj = PlantMaster.objects.get(id=id)
        label = obj.plant_name
        obj.soft_delete(request.user.username)
        return JsonResponse({"success": True, "message": f"Plant '{label}' deleted successfully"})
    except PlantMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": f"Plant with ID {id} not found", "errors": {}}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Error deleting plant: {str(e)}", "errors": {}}, status=500)


def _normalize_plant_row(row):
    return {
        'plant_name':    (row.get('plant name') or row.get('plant_name') or row.get('plantname') or '').strip(),
        'plant_email':   (row.get('plant email') or row.get('plant_email') or row.get('plantemail') or '').strip(),
        'plant_company': (row.get('plant company') or row.get('plant_company') or row.get('plantcompany') or '').strip(),
        'plant_address': (row.get('plant address') or row.get('plant_address') or row.get('plantaddress') or '').strip(),
    }


def _build_plant_preview(rows):
    seen_keys = set()
    preview_rows = []
    for idx, raw in enumerate(rows, start=2):
        data = _normalize_plant_row(raw)
        errors = []
        if not data['plant_name']:
            errors.append('Missing plant name')

        # Duplicate key = plant_name + plant_address (both must match).
        key = (data['plant_name'].lower(), data['plant_address'].lower())
        duplicate_in_file = bool(data['plant_name']) and key in seen_keys
        if data['plant_name']:
            seen_keys.add(key)

        exists_in_db = bool(data['plant_name']) and _plant_dup_qs(
            PlantMaster.objects, data['plant_name'], data['plant_address']).exists()

        if errors:
            status = 'error'
            message = '; '.join(errors)
        elif duplicate_in_file:
            status = 'duplicate'
            message = 'Duplicate row in file'
        elif exists_in_db:
            status = 'duplicate'
            message = 'Already exists'
        else:
            status = 'ok'
            message = ''

        preview_rows.append({'row': idx, 'status': status, 'message': message, 'data': data})

    return preview_rows


def plant_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Upload must use POST'}, status=405)
    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)
    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse uploaded file'}, status=400)

    preview_rows = _build_plant_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['plant_upload_preview'] = {
        'token': token, 'rows': preview_rows, 'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True, 'message': 'File parsed successfully', 'token': token,
        'rows': preview_rows, 'count': len(preview_rows),
    })


def plant_preview_api(request):
    preview = request.session.get('plant_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No upload preview available'}, status=404)
    return JsonResponse({
        'success': True, 'token': preview['token'],
        'rows': preview['rows'], 'count': len(preview['rows']),
    })


def plant_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Confirm must use POST'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON payload'}, status=400)

    token = payload.get('token')
    preview = request.session.get('plant_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Preview token not found or expired'}, status=400)

    created = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        d = row['data']
        extra = {
            'plant_email':   d.get('plant_email') or None,
            'plant_company': d.get('plant_company') or None,
            'plant_address': d.get('plant_address') or None,
        }
        existing = _plant_dup_qs(PlantMaster.all_objects, d['plant_name'], d['plant_address']).first()
        if existing is None:
            PlantMaster.objects.create(plant_name=d['plant_name'], **extra)
            created += 1
        elif existing.is_deleted:
            existing.plant_name = d['plant_name']
            for k, v in extra.items():
                setattr(existing, k, v)
            existing.is_deleted = False
            existing.deleted_by = None
            existing.deleted_at = None
            existing.save()
            created += 1

    request.session.pop('plant_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True, 'imported': created, 'created': created, 'updated': 0,
        'skipped': len(preview['rows']) - created, 'total': len(preview['rows']),
    })


# ─────────────────────────────────────────────
# LOGISTICS PERFORMANCE — PAGE
# ─────────────────────────────────────────────
@_lp_require('super', 'member')
@uses_components("data_table", "adv_filter", "modal")
def logistics_performance_list(request):
    return render(request, 'logistics_performance/logistics_performance_upload.html', {})


def _lp_fmt_date(d):
    """Format a date as e.g. '30 Apr 2026', or None if empty."""
    return d.strftime('%d %b %Y') if d else None


def _lp_fmt_inr(val):
    """Format a number as Indian-grouped currency e.g. '₹1,70,000', or None if empty."""
    if val is None or val == '':
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return str(val)
    neg = num < 0
    whole = int(round(abs(num)))
    s = str(whole)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ','.join(groups) + ',' + last3
    return ('-' if neg else '') + '₹' + s


def _lp_fmt_days(val):
    """Format an integer day count as e.g. '3 days' / '1 day', or None if empty."""
    if val is None or val == '':
        return None
    suffix = 'day' if val in (1, -1) else 'days'
    return f'{val} {suffix}'


@_lp_require('super', 'member')
def lp_detail_page(request, pk):
    """Full detail view for a single logistics performance record (opens as its own page)."""
    r = get_object_or_404(LogisticsPerformance, pk=pk)

    sections = [
        {
            'title': 'Shipment Info', 'icon': 'fas fa-truck', 'calc': False,
            'fields': [
                ('Location', r.location),
                ('Plant Name', r.plant_name),
                ('Billing Doc', r.billing_doc),
                ('Billing Date', _lp_fmt_date(r.billing_date)),
                ('Invoice Value', _lp_fmt_inr(r.inv_value)),
                ('Doc Type', r.doc_type),
                ('Billed Qty', r.billed_qty),
                ('Zone', r.zone),
                ('Division', r.division)
            ],
        },
        {
            'title': 'Ship-to Details', 'icon': 'fas fa-map-marker-alt', 'calc': False,
            'fields': [
                ('Shipto Party', r.shipto_party),
                ('Shipto Name', r.shipto_name),
                ('Shipto City', r.shipto_city),
                ('Shipto Region', r.shipto_region),
                ('Pincode', r.pincode),
            ],
        },
        {
            'title': 'Courier & Delivery', 'icon': 'fas fa-shipping-fast', 'calc': False,
            'fields': [
                ('Courier No', r.courier_no),
                ('Courier Code', r.courier_code),
                ('Courier Name', r.courier_name),
                ('Docket No', r.docket_no),
                ('Mode', r.mode),
                ('Boxes', r.boxes),
                ('Chargeable Weight', str(r.ch_weight) if r.ch_weight is not None else None),
                ('Dispatch Date', _lp_fmt_date(r.dispatch_date)),
                ('Delivery Date', _lp_fmt_date(r.delivery_date)),
            ],
        },
        {
            'title': 'Cost & Remarks', 'icon': 'fas fa-rupee-sign', 'calc': False,
            'fields': [
                ('Basic Logistics Cost', _lp_fmt_inr(r.basic_logistics_cost)),
                ('Other Charges', _lp_fmt_inr(r.other_charges)),
                ('Cost Center', r.cost_center),
                ('Remarks / Delay Reason', r.remarks),
            ],
        },
        {
            'title': 'Auto-Calculated Metrics', 'icon': 'fas fa-magic', 'calc': True,
            'fields': [
                ('TAT (from Master)', _lp_fmt_days(r.tat_from_master)),
                ('Expected Delivery Date', _lp_fmt_date(r.expected_delivery_date)),
                ('Dispatch vs EDD TAT', _lp_fmt_days(r.dispatch_vs_edd_tat)),
                ('Dispatch vs Delivery TAT', _lp_fmt_days(r.dispatch_vs_delivery_tat)),
                ('Actual Logistics Cost', _lp_fmt_inr(r.actual_logistics_cost)),
            ],
        },
        {
            'title': 'Record Info', 'icon': 'fas fa-info-circle', 'calc': False,
            'fields': [
                ('Creator', r.creator),
                ('Created At', _lp_fmt_date(r.created_at) if r.created_at else None),
                ('Last Updated', _lp_fmt_date(r.updated_at) if r.updated_at else None),
            ],
        },
    ]

    division_badge = 'badge-' + r.division.lower() if r.division else 'badge-muted'

    return render(request, 'logistics_performance/lp_detail.html', {
        'r': r,
        'sections': sections,
        'division_badge': division_badge,
    })


# ─────────────────────────────────────────────
# LOGISTICS PERFORMANCE — HELPERS
# ─────────────────────────────────────────────
def _parse_lp_date(val):
    """Parse date strings in multiple formats, return date or None."""
    if not val:
        return None
    if isinstance(val, date):
        return val
    val = str(val).strip()
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d'):
        try:
            return date(*[int(x) for x in __import__('datetime').datetime.strptime(val, fmt).timetuple()[:3]])
        except ValueError:
            continue
    return None


def _count_working_days(start_date, end_date):
    """
    Count the number of non-Sunday days between start_date (exclusive) and end_date (inclusive).
    Returns int.
    """
    if not start_date or not end_date or start_date >= end_date:
        return 0

    count = 0
    current = start_date + timedelta(days=1)
    while current <= end_date:
        if current.weekday() != 6:   # 6 = Sunday
            count += 1
        current += timedelta(days=1)
    return count


def _calc_edd(dispatch_date, tat):
    """
    Calculate Expected Delivery Date from dispatch_date + tat days,
    skipping Sundays in between.
    Returns date or None.
    """
    if not dispatch_date or tat is None:
        return None
    try:
        tat = int(tat)
    except (ValueError, TypeError):
        return None

    current = dispatch_date
    days_added = 0
    while days_added < tat:
        current += timedelta(days=1)
        if current.weekday() != 6:   # 6 = Sunday
            days_added += 1
    return current


def _get_master_data_by_courier(courier_code, mode, courier_name=None):
    """
    Look up TAT and courier_name from PincodeMaster using courier_code or courier_name and optional mode.
    Returns tuple (tat, courier_name) or (None, None).
    """
    if not courier_code and not courier_name:
        return None, None
    try:
        if courier_code:
            qs = PincodeMaster.objects.filter(courier_code__iexact=courier_code.strip())
        else:
            qs = PincodeMaster.objects.filter(courier_name__iexact=courier_name.strip())

        if mode:
            mode = mode.strip()
            qs_mode = qs.filter(mode__iexact=mode)
            if qs_mode.exists():
                qs = qs_mode
            else:
                qs_blank = qs.filter(Q(mode__isnull=True) | Q(mode__exact=''))
                if qs_blank.exists():
                    qs = qs_blank

        obj = qs.first()
        if obj:
            return obj.tat, obj.courier_name
        return None, None
    except Exception:
        return None, None


def _get_tat_from_master(courier_code, mode, courier_name=None):
    """
    Look up TAT from PincodeMaster using courier_code or courier_name and optional mode.
    Returns int or None.
    """
    tat, _ = _get_master_data_by_courier(courier_code, mode, courier_name)
    return tat


def _compute_lp_fields(data):
    """
    Given a normalised row dict, fill in the 4 calculated fields in-place.
    Mutates and returns data.
    """
    dispatch_date   = _parse_lp_date(data.get('dispatch_date'))
    delivery_date   = _parse_lp_date(data.get('delivery_date'))
    courier_code    = data.get('courier_code') or data.get('courier_partner_code') or ''
    courier_name    = data.get('courier_name') or ''
    mode            = data.get('mode') or ''

    # Fetch TAT and courier_name from master
    tat, master_courier_name = _get_master_data_by_courier(courier_code, mode, courier_name)

    # Use courier_name from master if not provided in input
    if master_courier_name and not courier_name:
        courier_name = master_courier_name
        data['courier_name'] = courier_name

    edd = _calc_edd(dispatch_date, tat)

    # Calculate TAT differences excluding Sundays
    dispatch_vs_edd = None
    if edd and dispatch_date:
        dispatch_vs_edd = _count_working_days(dispatch_date, edd)

    dispatch_vs_delivery = None
    if delivery_date and dispatch_date:
        dispatch_vs_delivery = _count_working_days(dispatch_date, delivery_date)

    data['tat_from_master']          = tat
    data['expected_delivery_date']   = edd.isoformat() if edd else None
    data['dispatch_vs_edd_tat']      = dispatch_vs_edd
    data['dispatch_vs_delivery_tat'] = dispatch_vs_delivery

    # Actual logistics cost = basic logistics cost + other charges
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    basic = _num(data.get('basic_logistics_cost'))
    other = _num(data.get('other_charges'))
    if basic is None and other is None:
        data['actual_logistics_cost'] = None
    else:
        data['actual_logistics_cost'] = (basic or 0) + (other or 0)
    return data


def _normalize_lp_row(raw):
    """Map raw header keys (from Excel) to model field names."""
    def n(v): return str(v).strip() if v is not None else ''

    return {
        'division':         n(raw.get('division')),
        'location':         n(raw.get('location')),
        'plant_name':       n(raw.get('plant-name') or raw.get('plant_name')),
        'billing_doc':      n(raw.get('billing doc') or raw.get('billing_doc')),
        'billing_date':     n(raw.get('billing date') or raw.get('billing_date')),
        'inv_value':        n(raw.get('inv value') or raw.get('inv_value')),
        'doc_type':         n(raw.get('doc. type') or raw.get('doc_type')),
        'billed_qty':       n(raw.get('billed qty') or raw.get('billed_qty')),
        'shipto_party':     n(raw.get('shipto-party') or raw.get('shipto_party')),
        'shipto_name':      n(raw.get('shipto-name') or raw.get('shipto_name')),
        'shipto_city':      n(raw.get('shipto-city') or raw.get('shipto_city')),
        'pincode':          n(raw.get('pincode')),
        'courier_no':       n(raw.get('currier no') or raw.get('courier no') or raw.get('courier_no')),
        'courier_code':     n(raw.get('currier partner code') or raw.get('courier partner code') or raw.get('courier code') or raw.get('courier_code')),
        'courier_name':     n(raw.get('currier name') or raw.get('courier name') or raw.get('courier_name')),
        'docket_no':        n(raw.get('docket no') or raw.get('docket_no')),
        'mode':             n(raw.get('mode(surface/air/train)') or raw.get('mode') or raw.get('transport mode') or raw.get('transport_mode')),
        'boxes':            n(raw.get('boxes')),
        'ch_weight':        n(raw.get('ch weight') or raw.get('ch_weight')),
        'dispatch_date':    n(raw.get('dispatch date') or raw.get('dispatch_date')),
        'delivery_date':    n(raw.get('delivery date') or raw.get('delivery_date')),
        'basic_logistics_cost': n(raw.get('basic logistics cost') or raw.get('basic_logistics_cost')),
        'other_charges':    n(raw.get('other charges') or raw.get('other_charges')),
        'remarks':          n(raw.get('remarks/delay reason') or raw.get('remarks')),
        'cost_center':      n(raw.get('cost center') or raw.get('cost_center')),
        'shipto_region':    n(raw.get('shipto-region') or raw.get('shipto_region')),
        'zone':             n(raw.get('zone')),
        'creator':          n(raw.get('creator')),
    }


def _build_lp_preview(rows):
    preview_rows = []

    for idx, raw in enumerate(rows, start=2):
        data = _normalize_lp_row(raw)

        # Skip completely blank rows — don't show them as "Missing ..." errors
        if not any(str(v).strip() for v in data.values()):
            continue

        errors = []

        # ── Required text fields (just need a value) ──
        for key, label in [
            ('location', 'location'),
            ('billing_doc', 'billing doc'), ('billing_date', 'billing date'),
            ('doc_type', 'doc type'), ('shipto_party', 'shipto-party'),
            ('shipto_name', 'shipto-name'), ('shipto_city', 'shipto-city'),
            ('docket_no', 'docket no'),
        ]:
            if not data.get(key):
                errors.append('Missing %s' % label)

        # Plant — required + must exist in Plant Master (else the row is rejected)
        if not data['plant_name']:
            errors.append('Missing plant-name')
        elif not PlantMaster.objects.filter(plant_name__iexact=data['plant_name']).exists():
            errors.append("Plant '%s' not in Plant Master" % data['plant_name'])

        # Division — required + must exist in Division Master
        if not data['division']:
            errors.append('Missing division')
        elif not DivisionMaster.objects.filter(
                division__iexact=data['division'], is_deleted=False).exists():
            errors.append("Division '%s' not in Division Master" % data['division'])

        # Zone — required + must exist in Zone Master
        if not data['zone']:
            errors.append('Missing zone')
        elif not ZoneMaster.objects.filter(zone__iexact=data['zone']).exists():
            errors.append("Zone '%s' not in Zone Master" % data['zone'])

        # Mode — required + must exist in Mode Master
        if not data['mode']:
            errors.append('Missing mode')
        elif not ModeMaster.objects.filter(mode__iexact=data['mode']).exists():
            errors.append("Mode '%s' not in Mode Master" % data['mode'])

        # Doc type — must be a valid Sales Type for this division (Sales Type Master)
        if data['doc_type'] and data['division'] and not DomesticSalesCard.objects.filter(
                division__iexact=data['division'],
                sales_type__iexact=data['doc_type']).exists():
            errors.append("Doc type '%s' not a valid Sales Type for division '%s'"
                          % (data['doc_type'], data['division']))

        # Pincode — required, exactly 6 digits
        if not data['pincode']:
            errors.append('Missing pincode')
        else:
            pin = data['pincode']
            if pin.endswith('.0'):      # tolerate Excel numeric cells
                pin = pin[:-2]
            if not (pin.isdigit() and len(pin) == 6):
                errors.append("Pincode '%s' must be exactly 6 digits" % data['pincode'])

        # Invoice value — required, numeric (negative allowed)
        if not data['inv_value']:
            errors.append('Missing invoice value')
        else:
            try:
                float(data['inv_value'])
            except ValueError:
                errors.append("Invalid invoice value '%s' (must be a number)" % data['inv_value'])

        # Billed qty — optional; default to 1 if missing
        if not data['billed_qty']:
            data['billed_qty'] = '1'
        elif not data['billed_qty'].replace('.0', '').isdigit():
            errors.append("Invalid billed qty '%s' (must be a whole number)" % data['billed_qty'])

        # Compute calculated fields
        _compute_lp_fields(data)

        if errors:
            status  = 'error'
            message = '; '.join(errors)
        else:
            status  = 'ok'
            message = ''

        preview_rows.append({
            'row':     idx,
            'status':  status,
            'message': message,
            'data':    data,
        })

    return preview_rows


# ─────────────────────────────────────────────
# LOGISTICS PERFORMANCE — APIs
# ─────────────────────────────────────────────
def lp_list_api(request):
    search   = request.GET.get('search', '').strip()
    page     = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 15))

    qs = LogisticsPerformance.objects.all()

    if search:
        qs = qs.filter(
            Q(division__icontains=search)   |
            Q(location__icontains=search)   |
            Q(docket_no__icontains=search)  |
            Q(shipto_name__icontains=search) |
            Q(shipto_city__icontains=search) |
            Q(billing_doc__icontains=search)
        )

    total = qs.count()
    start = (page - 1) * per_page
    records = qs.order_by('-created_at')[start:start + per_page]

    data = []
    for r in records:
        data.append({
            'id':                       r.id,
            'division':                 r.division,
            'location':                 r.location,
            'plant_name':               r.plant_name,
            'billing_doc':              r.billing_doc,
            'billing_date':             r.billing_date.isoformat() if r.billing_date else None,
            'inv_value':                str(r.inv_value or ''),
            'doc_type':                 r.doc_type,
            'billed_qty':               r.billed_qty,
            'shipto_party':             r.shipto_party,
            'shipto_name':              r.shipto_name,
            'shipto_city':              r.shipto_city,
            'pincode':                  r.pincode,
            'courier_no':               r.courier_no,
            'courier_code':             r.courier_code,
            'courier_name':             r.courier_name,
            'docket_no':                r.docket_no,
            'mode':                     r.mode,
            'boxes':                    r.boxes,
            'ch_weight':                str(r.ch_weight or ''),
            'dispatch_date':            r.dispatch_date.isoformat() if r.dispatch_date else None,
            'delivery_date':            r.delivery_date.isoformat() if r.delivery_date else None,
            'basic_logistics_cost':     str(r.basic_logistics_cost or ''),
            'other_charges':            str(r.other_charges or ''),
            'remarks':                  r.remarks,
            'cost_center':              r.cost_center,
            'shipto_region':            r.shipto_region,
            'zone':                     r.zone,
            'creator':                  r.creator,
            # Calculated
            'tat_from_master':          r.tat_from_master,
            'expected_delivery_date':   r.expected_delivery_date.isoformat() if r.expected_delivery_date else None,
            'dispatch_vs_edd_tat':      r.dispatch_vs_edd_tat,
            'dispatch_vs_delivery_tat': r.dispatch_vs_delivery_tat,
            'actual_logistics_cost':    str(r.actual_logistics_cost or ''),
        })

    # Live, full-table stats (over the whole filtered queryset, not just this page)
    # so the header cards reflect real totals instead of the loaded slice.
    stats = {
        'total':     total,
        'divisions': qs.values('division').distinct().count(),
        'with_edd':  qs.filter(expected_delivery_date__isnull=False).count(),
        'delivered': qs.filter(delivery_date__isnull=False).count(),
    }

    return JsonResponse({'success': True, 'data': data, 'total': total,
                         'page': page, 'per_page': per_page, 'stats': stats})


def lp_create_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)

    _compute_lp_fields(data)

    try:
        obj = LogisticsPerformance.objects.create(
            division=data.get('division', ''),
            location=data.get('location', ''),
            plant_name=data.get('plant_name', ''),
            billing_doc=data.get('billing_doc', ''),
            billing_date=_parse_lp_date(data.get('billing_date')),
            inv_value=data.get('inv_value') or None,
            doc_type=data.get('doc_type', ''),
            billed_qty=int(data['billed_qty']) if data.get('billed_qty') else None,
            shipto_party=data.get('shipto_party', ''),
            shipto_name=data.get('shipto_name', ''),
            shipto_city=data.get('shipto_city', ''),
            pincode=data.get('pincode', ''),
            courier_no=data.get('courier_no', ''),
            courier_code=data.get('courier_code', ''),
            courier_name=data.get('courier_name', ''),
            docket_no=data.get('docket_no', ''),
            mode=data.get('mode', ''),
            boxes=int(data['boxes']) if data.get('boxes') else None,
            ch_weight=data.get('ch_weight') or None,
            dispatch_date=_parse_lp_date(data.get('dispatch_date')),
            delivery_date=_parse_lp_date(data.get('delivery_date')),
            basic_logistics_cost=data.get('basic_logistics_cost') or None,
            other_charges=data.get('other_charges') or None,
            remarks=data.get('remarks', ''),
            cost_center=data.get('cost_center', ''),
            shipto_region=data.get('shipto_region', ''),
            zone=data.get('zone', ''),
            creator=request.user.get_full_name() or request.user.username,
            tat_from_master=data.get('tat_from_master'),
            expected_delivery_date=_parse_lp_date(data.get('expected_delivery_date')),
            dispatch_vs_edd_tat=data.get('dispatch_vs_edd_tat'),
            dispatch_vs_delivery_tat=data.get('dispatch_vs_delivery_tat'),
            actual_logistics_cost=data.get('actual_logistics_cost'),
        )
        return JsonResponse({'success': True, 'id': obj.id}, status=201)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


def lp_update_api(request, id):
    if request.method != 'PUT':
        return JsonResponse({'success': False, 'message': 'PUT required'}, status=405)
    try:
        obj = LogisticsPerformance.objects.get(id=id)
    except LogisticsPerformance.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)

    # Carry forward the costs the edit form may not send, so actual cost
    # (basic + other) recomputes from the full picture, not basic-only.
    if data.get('basic_logistics_cost') in (None, ''):
        data['basic_logistics_cost'] = obj.basic_logistics_cost
    if data.get('other_charges') in (None, ''):
        data['other_charges'] = obj.other_charges

    # Recompute calculated fields whenever courier_code / mode / dates change
    _compute_lp_fields(data)

    for field in ['division', 'location', 'plant_name', 'billing_doc', 'doc_type', 'shipto_party',
                  'shipto_name', 'shipto_city', 'pincode', 'courier_no', 'courier_code', 'courier_name',
                  'docket_no', 'mode', 'remarks', 'cost_center', 'shipto_region', 'zone', 'creator']:
        if field in data:
            setattr(obj, field, data[field])

    for date_field in ['billing_date', 'dispatch_date', 'delivery_date', 'expected_delivery_date']:
        if date_field in data:
            setattr(obj, date_field, _parse_lp_date(data[date_field]))

    for dec_field in ['inv_value', 'ch_weight', 'basic_logistics_cost', 'other_charges', 'actual_logistics_cost']:
        if data.get(dec_field) not in (None, ''):
            setattr(obj, dec_field, data[dec_field])

    for int_field in ['billed_qty', 'boxes', 'tat_from_master', 'dispatch_vs_edd_tat', 'dispatch_vs_delivery_tat']:
        if data.get(int_field) is not None:
            setattr(obj, int_field, data[int_field])

    obj.save()
    return JsonResponse({'success': True, 'id': obj.id})


def lp_delete_api(request, id):
    if request.method != 'DELETE':
        return JsonResponse({'success': False, 'message': 'DELETE required'}, status=405)
    try:
        obj = LogisticsPerformance.objects.get(id=id)
        obj.soft_delete(request.user.username)
        return JsonResponse({'success': True, 'message': 'Record deleted'})
    except LogisticsPerformance.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


def lp_bulk_delete_api(request):
    """Soft-delete multiple records at once. Body: {"ids": [1, 2, 3]}."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)

    ids = data.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'success': False, 'message': 'No ids provided'}, status=400)
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'message': 'Invalid id list'}, status=400)

    try:
        username = request.user.username
        deleted = 0
        for obj in LogisticsPerformance.objects.filter(id__in=ids):
            obj.soft_delete(username)
            deleted += 1
        return JsonResponse({'success': True, 'deleted': deleted,
                             'message': f'{deleted} record(s) deleted'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


def lp_upload_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)
    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)
    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)   # reuse existing parser
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse file'}, status=400)

    preview_rows = _build_lp_preview(rows)
    token = secrets.token_urlsafe(16)
    request.session['lp_upload_preview'] = {
        'token': token,
        'rows':  preview_rows,
        'created_at': timezone.now().isoformat(),
    }
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'message': 'File parsed successfully',
        'token':   token,
        'rows':    preview_rows,
        'count':   len(preview_rows),
    })


def lp_preview_api(request):
    preview = request.session.get('lp_upload_preview')
    if not preview:
        return JsonResponse({'success': False, 'message': 'No preview available'}, status=404)
    return JsonResponse({'success': True, 'token': preview['token'], 'rows': preview['rows'], 'count': len(preview['rows'])})


def lp_confirm_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)

    token   = payload.get('token')
    preview = request.session.get('lp_upload_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Token not found or expired'}, status=400)

    imported = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        d = row['data']

        def safe_dec(v): return v if v not in ('', None) else None

        def safe_int(v):
            try: return int(v) if v not in ('', None) else None
            except (TypeError, ValueError): return None

        docket  = d.get('docket_no', '')
        billing = d.get('billing_doc', '')

        fields = dict(
            division=d.get('division', ''),
            location=d.get('location', ''),
            plant_name=d.get('plant_name', ''),
            billing_date=_parse_lp_date(d.get('billing_date')),
            inv_value=safe_dec(d.get('inv_value')),
            doc_type=d.get('doc_type', ''),
            billed_qty=safe_int(d.get('billed_qty')),
            shipto_party=d.get('shipto_party', ''),
            shipto_name=d.get('shipto_name', ''),
            shipto_city=d.get('shipto_city', ''),
            pincode=d.get('pincode', ''),
            courier_no=d.get('courier_no', ''),
            courier_code=d.get('courier_code', ''),
            courier_name=d.get('courier_name', ''),
            mode=d.get('mode', ''),
            boxes=safe_int(d.get('boxes')),
            ch_weight=safe_dec(d.get('ch_weight')),
            dispatch_date=_parse_lp_date(d.get('dispatch_date')),
            delivery_date=_parse_lp_date(d.get('delivery_date')),
            basic_logistics_cost=safe_dec(d.get('basic_logistics_cost')),
            other_charges=safe_dec(d.get('other_charges')),
            remarks=d.get('remarks', ''),
            cost_center=d.get('cost_center', ''),
            shipto_region=d.get('shipto_region', ''),
            zone=d.get('zone', ''),
            creator=request.user.get_full_name() or request.user.username,
            tat_from_master=safe_int(d.get('tat_from_master')),
            expected_delivery_date=_parse_lp_date(d.get('expected_delivery_date')),
            dispatch_vs_edd_tat=safe_int(d.get('dispatch_vs_edd_tat')),
            dispatch_vs_delivery_tat=safe_int(d.get('dispatch_vs_delivery_tat')),
            actual_logistics_cost=safe_dec(d.get('actual_logistics_cost')),
        )

        # Re-upload: if a record with the same Docket No + Billing Doc already
        # exists, update it instead of inserting a duplicate. If either key is
        # blank we can't match reliably, so insert as a new record.
        # Match ACTIVE rows only. A soft-deleted match is ignored, so a new
        # active row is inserted and the deleted twin keeps is_deleted=True.
        existing = None
        if docket and billing:
            existing = LogisticsPerformance.objects.filter(
                docket_no=docket, billing_doc=billing,
            ).first()
        if existing:
            for field_name, value in fields.items():
                setattr(existing, field_name, value)
            existing.save()
        else:
            LogisticsPerformance.objects.create(
                docket_no=docket, billing_doc=billing, **fields,
            )
        imported += 1

    request.session.pop('lp_upload_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success':  True,
        'imported': imported,
        'skipped':  len(preview['rows']) - imported,
        'total':    len(preview['rows']),
    })


# ─────────────────────────────────────────────────────────────
# PENDING RECORDS
# ─────────────────────────────────────────────────────────────

def lp_pending_api(request):
    """Return records whose doc_type maps to a pending_show=True sales type
    and have at least one of dispatch_date, delivery_date, basic_logistics_cost missing."""
    pending_doc_types = DomesticSalesCard.objects.filter(pending_show=True).values_list('sales_type', flat=True)

    qs = LogisticsPerformance.objects.filter(
        doc_type__in=pending_doc_types
    ).filter(
        Q(dispatch_date__isnull=True) |
        Q(delivery_date__isnull=True) |
        Q(basic_logistics_cost__isnull=True)
    ).order_by('-id')

    data = []
    for r in qs:
        pending = []
        if not r.dispatch_date:         pending.append('Dispatch Date')
        if not r.delivery_date:         pending.append('Delivery Date')
        if not r.basic_logistics_cost:  pending.append('Logistics Cost')

        data.append({
            'id':                   r.id,
            'division':             r.division,
            'location':             r.location,
            'plant_name':           r.plant_name,
            'billing_doc':          r.billing_doc,
            'billing_date':         r.billing_date.isoformat() if r.billing_date else None,
            'shipto_name':          r.shipto_name,
            'shipto_city':          r.shipto_city,
            'docket_no':            r.docket_no,
            'courier_name':         r.courier_name,
            'mode':                 r.mode,
            'dispatch_date':        r.dispatch_date.isoformat() if r.dispatch_date else None,
            'delivery_date':        r.delivery_date.isoformat() if r.delivery_date else None,
            'basic_logistics_cost': str(r.basic_logistics_cost or ''),
            'remarks':              r.remarks,
            'cost_center':          r.cost_center,
            'creator':              r.creator,
            'zone':                 r.zone,
            'pending_fields':       pending,
        })

    return JsonResponse({'success': True, 'data': data, 'total': len(data)})


def lp_pending_export_api(request):
    """Download all pending records as an Excel file ready to be filled in."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    pending_doc_types = DomesticSalesCard.objects.filter(pending_show=True).values_list('sales_type', flat=True)

    qs = LogisticsPerformance.objects.filter(
        doc_type__in=pending_doc_types
    ).filter(
        Q(dispatch_date__isnull=True) |
        Q(delivery_date__isnull=True) |
        Q(basic_logistics_cost__isnull=True)
    ).order_by('-id')

    wb = Workbook()
    ws = wb.active
    ws.title = 'Pending Records'

    headers = [
        'ID', 'Division', 'Location', 'Plant Name', 'Billing Doc',
        'Billing Date', 'Shipto Name', 'Shipto City', 'Docket No',
        'Courier Name', 'Mode', 'Dispatch Date', 'Delivery Date',
        'Basic Logistics Cost', 'Remarks', 'Cost Center',
        'Creator', 'Zone',
    ]

    header_fill   = PatternFill('solid', fgColor='1E3A5F')
    pending_fill  = PatternFill('solid', fgColor='FFF3CD')
    header_font   = Font(bold=True, color='FFFFFF', size=10)
    pending_font  = Font(bold=True, color='856404', size=10)
    pending_cols  = {'Dispatch Date', 'Delivery Date', 'Basic Logistics Cost'}

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        if h in pending_cols:
            cell.fill   = pending_fill
            cell.font   = pending_font
        else:
            cell.fill   = header_fill
            cell.font   = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for r in qs:
        ws.append([
            r.id,
            r.division or '',
            r.location or '',
            r.plant_name or '',
            r.billing_doc or '',
            r.billing_date.isoformat() if r.billing_date else '',
            r.shipto_name or '',
            r.shipto_city or '',
            r.docket_no or '',
            r.courier_name or '',
            r.mode or '',
            r.dispatch_date.isoformat() if r.dispatch_date else '',
            r.delivery_date.isoformat() if r.delivery_date else '',
            str(r.basic_logistics_cost) if r.basic_logistics_cost else '',
            r.remarks or '',
            r.cost_center or '',
            r.creator or '',
            r.zone or '',
        ])

    # Auto-fit columns
    for col in ws.columns:
        max_len = max((len(str(c.value or '')) for c in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 30)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    response = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="pending_logistics_records.xlsx"'
    return response


def lp_pending_update_upload_api(request):
    """Parse an uploaded Excel/CSV of pending records and store a preview in session."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    file_obj = request.FILES.get('file')
    if not file_obj:
        return JsonResponse({'success': False, 'message': 'No file uploaded'}, status=400)

    try:
        rows = _read_uploaded_location_rows(file_obj, file_obj.name)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Could not parse file'}, status=400)

    preview_rows = []
    for idx, raw in enumerate(rows, start=2):
        def n(v): return str(v).strip() if v not in (None, '') else ''

        row_id = n(raw.get('id') or raw.get('ID'))
        if not row_id:
            preview_rows.append({'row': idx, 'status': 'error', 'message': 'Missing ID column', 'data': {}})
            continue

        try:
            rec_id = int(row_id)
        except ValueError:
            preview_rows.append({'row': idx, 'status': 'error', 'message': f'Invalid ID: {row_id}', 'data': {}})
            continue

        try:
            obj = LogisticsPerformance.objects.get(id=rec_id)
        except LogisticsPerformance.DoesNotExist:
            preview_rows.append({'row': idx, 'status': 'error', 'message': f'ID {rec_id} not found', 'data': {'id': rec_id}})
            continue

        dispatch_date_raw    = n(raw.get('dispatch date') or raw.get('Dispatch Date') or raw.get('dispatch_date'))
        delivery_date_raw    = n(raw.get('delivery date') or raw.get('Delivery Date') or raw.get('delivery_date'))
        logistics_cost_raw   = n(raw.get('basic logistics cost') or raw.get('Basic Logistics Cost') or raw.get('basic_logistics_cost'))

        changes = {}
        if dispatch_date_raw:
            parsed = _parse_lp_date(dispatch_date_raw)
            if parsed:
                changes['dispatch_date'] = parsed.isoformat()
        if delivery_date_raw:
            parsed = _parse_lp_date(delivery_date_raw)
            if parsed:
                changes['delivery_date'] = parsed.isoformat()
        if logistics_cost_raw:
            changes['basic_logistics_cost'] = logistics_cost_raw

        if not changes:
            preview_rows.append({'row': idx, 'status': 'skip', 'message': 'No new values provided', 'data': {'id': rec_id, 'docket_no': obj.docket_no}})
            continue

        preview_rows.append({
            'row':    idx,
            'status': 'ok',
            'data': {
                'id':         rec_id,
                'division':   obj.division,
                'location':   obj.location,
                'docket_no':  obj.docket_no,
                'billing_doc': obj.billing_doc,
                'current': {
                    'dispatch_date':        obj.dispatch_date.isoformat() if obj.dispatch_date else None,
                    'delivery_date':        obj.delivery_date.isoformat() if obj.delivery_date else None,
                    'basic_logistics_cost': str(obj.basic_logistics_cost or ''),
                },
                'changes': changes,
            },
        })

    token = secrets.token_urlsafe(16)
    request.session['lp_pending_update_preview'] = {
        'token': token,
        'rows':  preview_rows,
    }
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'token':   token,
        'rows':    preview_rows,
        'count':   len(preview_rows),
    })


def lp_pending_update_confirm_api(request):
    """Apply the pending update (fill in missing fields for matched records)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)

    token   = payload.get('token')
    preview = request.session.get('lp_pending_update_preview')
    if not preview or preview.get('token') != token:
        return JsonResponse({'success': False, 'message': 'Token not found or expired'}, status=400)

    updated = 0
    for row in preview['rows']:
        if row['status'] != 'ok':
            continue
        d = row['data']
        changes = d.get('changes', {})
        try:
            obj = LogisticsPerformance.objects.get(id=d['id'])
        except LogisticsPerformance.DoesNotExist:
            continue

        if 'dispatch_date' in changes:
            obj.dispatch_date = _parse_lp_date(changes['dispatch_date'])
        if 'delivery_date' in changes:
            obj.delivery_date = _parse_lp_date(changes['delivery_date'])
        if 'basic_logistics_cost' in changes:
            try:
                obj.basic_logistics_cost = changes['basic_logistics_cost']
            except Exception:
                pass

        # Recompute calculated fields
        update_data = {
            'dispatch_date': obj.dispatch_date.isoformat() if obj.dispatch_date else None,
            'delivery_date': obj.delivery_date.isoformat() if obj.delivery_date else None,
            'courier_code':  obj.courier_code or '',
            'courier_name':  obj.courier_name or '',
            'mode':          obj.mode or '',
            'basic_logistics_cost': obj.basic_logistics_cost,
            'other_charges':        obj.other_charges,
        }
        _compute_lp_fields(update_data)
        obj.tat_from_master          = update_data.get('tat_from_master')
        obj.expected_delivery_date   = _parse_lp_date(update_data.get('expected_delivery_date'))
        obj.dispatch_vs_edd_tat      = update_data.get('dispatch_vs_edd_tat')
        obj.dispatch_vs_delivery_tat = update_data.get('dispatch_vs_delivery_tat')
        obj.actual_logistics_cost    = update_data.get('actual_logistics_cost')

        obj.save()
        updated += 1

    request.session.pop('lp_pending_update_preview', None)
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'updated': updated,
        'skipped': len(preview['rows']) - updated,
        'total':   len(preview['rows']),
    })


# ─────────────────────────────────────────────────────────────────────────────
# LOGISTICS PERFORMANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────

# Budget LC %, division display names and the division list all come from the
# Division master table now (nothing hard-coded). _DEFAULT_BUDGET_LC is only the
# fallback used when a division has somehow not been added to the master yet.
_DEFAULT_BUDGET_LC = 0.86


def _division_maps():
    """
    Read the Division master and return three dicts:
        budget  : {code -> budget_lc_percent (float)}
        display : {code -> label shown to users}
        reverse : {label -> code}   (to translate a display label back to its code)
    Fetched once and reused; the table is tiny.
    """
    budget, display, reverse = {}, {}, {}
    try:
        for name in (DivisionMaster.objects
                     .filter(is_deleted=False)
                     .values_list('division', flat=True)
                     .distinct()):
            if not name:
                continue
            # The new DivisionMaster has no budget %/display rename, so budget
            # falls back to the default and the label is the division itself.
            display[name]  = name
            reverse[name]  = name
    except Exception:
        pass
    return budget, display, reverse


def _budget_lc(division, budget_map=None):
    if budget_map is None:
        budget_map, _, _ = _division_maps()
    return budget_map.get(division, _DEFAULT_BUDGET_LC)


def _division_label(code, display_map=None):
    """Code stored in DB -> label shown to users (e.g. MILSPL -> MICRO)."""
    if display_map is None:
        _, display_map, _ = _division_maps()
    return display_map.get(code, code)


def _division_code(label, reverse_map=None):
    """Label shown to users -> code stored in DB (e.g. MICRO -> MILSPL)."""
    if reverse_map is None:
        _, _, reverse_map = _division_maps()
    return reverse_map.get(label, label)


def _safe_pct(num, den, dp=2):
    if not den:
        return 0.0
    v = round((num / den) * 100, dp)
    return 0.0 if (v != v) else v   # NaN guard


def _apply_lp_filters(qs, params):
    """Apply common filters from request GET params to a LogisticsPerformance queryset."""
    date_from   = params.get('date_from')
    date_to     = params.get('date_to')
    division    = params.get('division')
    mode        = params.get('mode')
    courier     = params.get('courier')
    plant       = params.get('plant')
    sales_type  = params.get('sales_type')

    if date_from:
        qs = qs.filter(billing_date__gte=date_from)
    if date_to:
        qs = qs.filter(billing_date__lte=date_to)
    if division:
        # Accept either the stored code (MILSPL) or the display label (MICRO).
        qs = qs.filter(division=_division_code(division))
    if mode:
        if mode == 'Other':
            # Everything not covered by the Mode master.
            master_modes = list(ModeMaster.objects.filter(is_deleted=False)
                                .values_list('mode', flat=True))
            qs = qs.exclude(mode__in=master_modes)
        else:
            qs = qs.filter(mode=mode)
    if courier:
        qs = qs.filter(courier_name=courier)
    if plant:
        qs = qs.filter(plant_name=plant)
    if sales_type:
        qs = qs.filter(doc_type=sales_type)
    return qs


@_lp_require('super', 'admin', 'member')
@uses_components("data_table", "modal", "master_page")
def logistics_performance_report_page(request):
    """Render the report page, passing dropdown options to the template."""

    lp = LogisticsPerformance.objects.all()

    # Divisions come from the Division master (non-deleted, distinct).
    try:
        divisions = sorted(set(filter(None,
            DivisionMaster.objects.filter(is_deleted=False).filter(code=101)
            .values_list('division', flat=True))))
    except Exception:
        divisions = []
    if not divisions:   # fallback if master is empty / table not migrated yet
        divisions = sorted(filter(None, lp.values_list('division', flat=True).distinct()))
    # Modes come from the Mode master (authoritative list). Any mode present in the
    # data but missing from the master is collapsed into a single "Other" option.
    try:
        modes = sorted(set(filter(None,
            ModeMaster.objects.filter(is_deleted=False).values_list('mode', flat=True))))
    except Exception:
        modes = []
    lp_modes = set(filter(None, lp.values_list('mode', flat=True).distinct()))
    if not modes:   # fallback if master is empty / table not migrated yet
        modes = sorted(lp_modes)
    else:
        master_set = {m.strip().lower() for m in modes}
        if any(m.strip().lower() not in master_set for m in lp_modes):
            modes.append('Other')
    couriers    = sorted(filter(None, lp.values_list('courier_name', flat=True).distinct()))
    plants      = sorted(filter(None, lp.values_list('plant_name',  flat=True).distinct()))
    locations   = sorted(filter(None,
                    PincodeMaster.objects.values_list('location', flat=True).distinct()))
    # Modes available for the Pincode Search tab come from the pincode rate master.
    pincode_modes = sorted(filter(None,
                    PincodeMaster.objects.filter(is_active=True)
                    .values_list('mode', flat=True).distinct()))
    sales_types = sorted(filter(None,
                    DomesticSalesCard.objects.values_list('sales_type_description', flat=True).distinct()))

    # Division -> plants map, so the Plant dropdown can be filtered by Division.
    plant_map = {}
    for div, plant in lp.values_list('division', 'plant_name').distinct():
        if not div or not plant:
            continue
        plant_map.setdefault(div, set()).add(plant)
    plant_map = {div: sorted(ps) for div, ps in plant_map.items()}

    return render(request, 'logistics_performance/logistics_performance_report.html', {
        'divisions_json':   json.dumps(divisions),
        'modes_json':       json.dumps(modes),
        'couriers_json':    json.dumps(couriers),
        'plants_json':      json.dumps(plants),
        'plant_map_json':   json.dumps(plant_map),
        'pincode_modes_json': json.dumps(pincode_modes),
        'locations_json':   json.dumps(locations),
        'sales_types_json': json.dumps(sales_types),
    })


# ── Report: header stats ──────────────────────────────────────────────────────

def report_stats_api(request):
    """Overall statistics shown in the page header."""
    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)

    total      = qs.count()
    # hit  = actual delivery TAT <= expected EDD TAT  (both non-null)
    tat_qs = qs.filter(
        dispatch_vs_delivery_tat__isnull=False,
        dispatch_vs_edd_tat__isnull=False,
        dispatch_vs_edd_tat__gt=0,
    )
    on_time    = tat_qs.filter(dispatch_vs_delivery_tat__lte=F('dispatch_vs_edd_tat')).count()
    delayed    = tat_qs.filter(dispatch_vs_delivery_tat__gt =F('dispatch_vs_edd_tat')).count()
    pending    = qs.filter(
        Q(dispatch_date__isnull=True) | Q(delivery_date__isnull=True)
    ).count()

    return JsonResponse({
        'success': True,
        'stats': {
            'total':   total,
            'on_time': on_time,
            'delayed': delayed,
            'pending': pending,
        }
    })


# ── Report: TAT (State / Zone / Division) ─────────────────────────────────────

def report_tat_api(request):
    """
    TAT performance summary.
    ?group_by=State|Zone|Division  (default Division)
    Plus standard filters.
    """
    group_by = request.GET.get('group_by', 'Division')
    field_map = {
        'State':    'shipto_region',
        'Zone':     'zone',
        'Division': 'division',
    }
    db_field = field_map.get(group_by, 'division')

    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(
        dispatch_vs_edd_tat__isnull=False,
        dispatch_vs_delivery_tat__isnull=False,
        dispatch_vs_edd_tat__gt=0,
    ).exclude(**{f'{db_field}__isnull': True}).exclude(**{f'{db_field}': ''})

    rows = qs.values(db_field).annotate(
        grand_total=Count('id'),
        hit=Sum(Case(
            When(dispatch_vs_delivery_tat__lte=F('dispatch_vs_edd_tat'), then=1),
            default=0, output_field=IntegerField()
        )),
        miss=Sum(Case(
            When(dispatch_vs_delivery_tat__gt=F('dispatch_vs_edd_tat'), then=1),
            default=0, output_field=IntegerField()
        )),
    ).order_by(db_field)

    _, display_map, _ = _division_maps()
    data = []
    for r in rows:
        grand = r['grand_total'] or 0
        hit   = r['hit']         or 0
        miss  = r['miss']        or 0
        label = r[db_field] or '(blank)'
        # Only the Division grouping needs the code->display translation.
        if group_by == 'Division':
            label = _division_label(label, display_map)
        data.append({
            'label':       label,
            'hit':         hit,
            'miss':        miss,
            'grand_total': grand,
            'hit_pct':     _safe_pct(hit,  grand),
            'miss_pct':    _safe_pct(miss, grand),
            'grand_pct':   round(_safe_pct(hit, grand) + _safe_pct(miss, grand), 2),
        })

    return JsonResponse({'success': True, 'group_by': group_by, 'data': data})


# ── Report: Cost (Division / Mode / State) ─────────────────────────────────────

def report_cost_api(request):
    """
    Logistics cost vs budget.
    ?view_by=Division|Mode|State  (default Division)
    """
    view_by  = request.GET.get('view_by', 'Division')
    field_map = {
        'Division': ['division'],
        'Mode':     ['division', 'mode'],
        'State':    ['division', 'shipto_region'],
    }
    group_fields = field_map.get(view_by, ['division'])

    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)

    # Sales type multi-filter: descriptions → actual SAP doc-type codes via DomesticSalesCard
    sales_types = request.GET.get('sales_types', '').strip()
    if sales_types:
        st_list = [s.strip() for s in sales_types.split(',') if s.strip()]
        if st_list:
            # Translate description labels to the SAP codes stored in doc_type
            type_codes = list(DomesticSalesCard.objects.filter(
                sales_type_description__in=st_list
            ).values_list('sales_type', flat=True).distinct())
            if type_codes:
                qs = qs.filter(doc_type__in=type_codes)

    rows = qs.values(*group_fields).annotate(
        total_sales    = Sum('inv_value'),
        total_lc_basic = Sum('basic_logistics_cost'),
        total_lc_other = Sum('other_charges'),
    ).order_by(*group_fields)

    budget_map, display_map, _ = _division_maps()
    data = []
    for r in rows:
        div    = r.get('division') or ''
        sales  = float(r['total_sales']    or 0)
        lc_b   = float(r['total_lc_basic'] or 0)
        lc_o   = float(r['total_lc_other'] or 0)
        actual = lc_b + lc_o
        budget = _budget_lc(div, budget_map)
        actual_pct = _safe_pct(actual, sales)
        abs_amount = round(((sales * budget) / 100) - actual, 2)

        entry = {
            'division':    _division_label(div, display_map),
            'total_sales': round(sales,  2),
            'actual_lc':   round(actual, 2),
            'actual_lc_pct':  actual_pct,
            'budget_lc_pct':  budget,
            'diff_pct':       round(budget - actual_pct, 2),
            'abs_amount':     abs_amount,
        }
        if view_by == 'Mode':
            entry['mode']  = r.get('mode') or '(blank)'
        if view_by == 'State':
            entry['state'] = r.get('shipto_region') or 'Other Region'

        data.append(entry)

    return JsonResponse({'success': True, 'view_by': view_by, 'data': data})


# ── Report: Courier-wise delivery performance ──────────────────────────────────

def report_courier_api(request):
    """
    Monthly courier-wise delivery bucket counts (on-time, 1-day delay … 5+ days).
    """
    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(
        dispatch_vs_edd_tat__isnull=False,
        dispatch_vs_delivery_tat__isnull=False,
        dispatch_vs_edd_tat__gt=0,
        courier_name__isnull=False,
    ).exclude(courier_name='')

    qs = qs.annotate(
        diff=ExpressionWrapper(
            F('dispatch_vs_delivery_tat') - F('dispatch_vs_edd_tat'),
            output_field=IntegerField()
        ),
        yr=ExtractYear('billing_date'),
        mo=ExtractMonth('billing_date'),
    )

    rows = qs.values('yr', 'mo', 'courier_name', 'mode').annotate(
        d0=Sum(Case(When(diff__lte=0, then=1), default=0, output_field=IntegerField())),
        d1=Sum(Case(When(diff=1,     then=1), default=0, output_field=IntegerField())),
        d2=Sum(Case(When(diff=2,     then=1), default=0, output_field=IntegerField())),
        d3=Sum(Case(When(diff=3,     then=1), default=0, output_field=IntegerField())),
        d4=Sum(Case(When(diff=4,     then=1), default=0, output_field=IntegerField())),
        d5=Sum(Case(When(diff__gte=5, then=1), default=0, output_field=IntegerField())),
    ).order_by('yr', 'mo', 'courier_name', 'mode')

    MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December']
    data = []
    for r in rows:
        d0 = r['d0'] or 0; d1 = r['d1'] or 0; d2 = r['d2'] or 0
        d3 = r['d3'] or 0; d4 = r['d4'] or 0; d5 = r['d5'] or 0
        grand = d0 + d1 + d2 + d3 + d4 + d5
        if not grand:
            continue
        mo = r['mo'] or 0
        data.append({
            'year':       r['yr'],
            'month_num':  mo,
            'month_name': MONTHS[mo] if mo else '—',
            'courier':    r['courier_name'],
            'mode':       r['mode'] or '—',
            'd0': d0, 'd1': d1, 'd2': d2, 'd3': d3, 'd4': d4, 'd5': d5,
            'grand':      grand,
            'ontime_pct': _safe_pct(d0, grand, 1),
            'delay_pct':  _safe_pct(d1+d2+d3+d4+d5, grand, 1),
        })

    # Service type lookup from PincodeMaster
    courier_names = list({r['courier'] for r in data})
    svc_map = {}
    for pm in (PincodeMaster.objects
               .filter(courier_name__in=courier_names)
               .exclude(service_type__isnull=True)
               .exclude(service_type='')
               .values('courier_name', 'service_type')
               .distinct()):
        if pm['courier_name'] not in svc_map:
            svc_map[pm['courier_name']] = pm['service_type']
    for r in data:
        r['service_type'] = svc_map.get(r['courier'], '')

    return JsonResponse({'success': True, 'data': data})


# ── Report: Courier LC contribution ────────────────────────────────────────────

def report_lc_api(request):
    """
    Per-courier logistics cost contribution & weight share.
    Requires date_from / date_to.
    """
    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(courier_name__isnull=False).exclude(courier_name='')

    # Totals
    totals = qs.aggregate(
        total_lc_basic=Sum('basic_logistics_cost'),
        total_lc_other=Sum('other_charges'),
        total_weight  =Sum('ch_weight'),
        total_sales   =Sum('inv_value'),
    )
    total_lc    = float(totals['total_lc_basic'] or 0) + float(totals['total_lc_other'] or 0)
    total_wt    = float(totals['total_weight']   or 0)
    total_sales = float(totals['total_sales']    or 0)

    rows = qs.values('courier_name').annotate(
        sale_val  = Sum('inv_value'),
        lc_basic  = Sum('basic_logistics_cost'),
        lc_other  = Sum('other_charges'),
        wt        = Sum('ch_weight'),
    ).order_by('-lc_basic')

    data = []
    for r in rows:
        sv   = float(r['sale_val']  or 0)
        lcfv = float(r['lc_basic']  or 0) + float(r['lc_other'] or 0)
        wt   = float(r['wt']        or 0)
        data.append({
            'courier':     r['courier_name'],
            'sale_value':  round(sv,   2),
            'lc_fv':       round(lcfv, 2),
            'biz_pct':     _safe_pct(lcfv, total_lc),
            'lc_pct':      _safe_pct(lcfv, sv) if sv else 0,
            'weight':      round(wt, 2),
            'weight_pct':  _safe_pct(wt, total_wt),
        })

    return JsonResponse({
        'success': True,
        'data': data,
        'totals': {
            'total_lc':    round(total_lc,    2),
            'total_wt':    round(total_wt,    2),
            'total_sales': round(total_sales, 2),
        }
    })


# ── Report: Pincode search ──────────────────────────────────────────────────────

def report_pincode_api(request):
    """
    Look up rate, TAT and business allocation for a given pincode.
    ?pincode=XXX  &mode=  &location=  &weight=  &date_from=  &date_to=
    """
    from django.db.models import Sum

    pincode  = (request.GET.get('pincode') or '').strip()
    mode     = (request.GET.get('mode')    or '').strip()
    location = (request.GET.get('location') or '').strip()
    weight   = float(request.GET.get('weight') or 0)
    date_from = request.GET.get('date_from')
    date_to   = request.GET.get('date_to')

    if not pincode:
        return JsonResponse({'success': False, 'message': 'Pincode is required'}, status=400)

    pm_qs = PincodeMaster.objects.filter(pincode=pincode, is_active=True)
    if mode:
        pm_qs = pm_qs.filter(mode__iexact=mode)
    if location:
        # Ahmedabad → Vapi (legacy mapping)
        loc = 'Vapi' if location == 'Ahmedabad' else location
        pm_qs = pm_qs.filter(location__iexact=loc)
    pm_qs = pm_qs.order_by('tat')

    rows = []
    for pm in pm_qs:
        rate    = float(pm.rate)
        min_wt  = float(pm.min_weight or 0)
        amount  = (weight * rate) if weight > min_wt else (min_wt * rate)

        # Business allocation
        lp_qs = LogisticsPerformance.objects.filter(
            pincode=pincode,
            mode__iexact=pm.mode or '',
            courier_name__iexact=pm.courier_name,
        )
        if date_from:
            lp_qs = lp_qs.filter(billing_date__gte=date_from)
        if date_to:
            lp_qs = lp_qs.filter(billing_date__lte=date_to)
        biz_alloc = lp_qs.aggregate(
            total=Sum('basic_logistics_cost')
        )['total'] or 0

        rows.append({
            'courier_name': pm.courier_name,
            'courier_code': pm.courier_code,
            'mode':         pm.mode,
            'rate':         rate,
            'min_weight':   min_wt,
            'tat':          pm.tat,
            'oda_nonoda':   pm.oda_nonoda,
            'zone':         pm.zone,
            'district':     pm.district,
            'amount':       round(amount, 2),
            'biz_alloc':    round(float(biz_alloc), 2),
            'service_type': pm.service_type,
        })

    # Min-rate and min-TAT highlighting
    if rows:
        all_amounts = [r['amount'] for r in rows]
        all_tats    = [r['tat']    for r in rows]
        sorted_amts = sorted(set(all_amounts))
        sorted_tats = sorted(set(all_tats))
        min_amt  = sorted_amts[0] if sorted_amts else None
        sec_amt  = sorted_amts[1] if len(sorted_amts) > 1 else None
        min_tat  = sorted_tats[0] if sorted_tats else None
        sec_tat  = sorted_tats[1] if len(sorted_tats) > 1 else None
        district = rows[0].get('district', '')
        for r in rows:
            r['amt_rank'] = (
                'best'   if r['amount'] == min_amt else
                'second' if r['amount'] == sec_amt else 'normal'
            )
            r['tat_rank'] = (
                'best'   if r['tat'] == min_tat else
                'second' if r['tat'] == sec_tat else 'normal'
            )
    else:
        district = ''

    return JsonResponse({
        'success':  True,
        'district': district,
        'data':     rows,
    })


# ─────────────────────────────────────────────────────────────────────────────
# TAT DRILL-DOWN DETAIL API
# ─────────────────────────────────────────────────────────────────────────────

def report_tat_detail_api(request):
    """
    Return individual LP records for a TAT drill-down.
    ?group_by=Division|Zone|State  &label=MEPL  &result=hit|miss  + standard filters
    """
    group_by = request.GET.get('group_by', 'Division')
    label    = request.GET.get('label', '')
    result   = request.GET.get('result', 'hit')   # 'hit' or 'miss'

    field_map = {
        'State':    'shipto_region',
        'Zone':     'zone',
        'Division': 'division',
    }
    db_field = field_map.get(group_by, 'division')

    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(
        dispatch_vs_edd_tat__isnull=False,
        dispatch_vs_delivery_tat__isnull=False,
        dispatch_vs_edd_tat__gt=0,
    )

    if label:
        # Translate the display label (e.g. MICRO) back to the stored code (MILSPL)
        db_label = _division_code(label) if db_field == 'division' else label
        qs = qs.filter(**{db_field: db_label})

    if result == 'hit':
        qs = qs.filter(dispatch_vs_delivery_tat__lte=F('dispatch_vs_edd_tat'))
    else:
        qs = qs.filter(dispatch_vs_delivery_tat__gt=F('dispatch_vs_edd_tat'))

    qs = qs.order_by('-billing_date')

    data = []
    for r in qs:
        data.append({
            'division':                 r.division or '',
            'location':                 r.location or '',
            'plant_name':               r.plant_name or '',
            'billing_doc':              r.billing_doc or '',
            'billing_date':             r.billing_date.isoformat() if r.billing_date else '',
            'inv_value':                str(r.inv_value or ''),
            'shipto_name':              r.shipto_name or '',
            'shipto_city':              r.shipto_city or '',
            'shipto_region':            r.shipto_region or '',
            'zone':                     r.zone or '',
            'courier_name':             r.courier_name or '',
            'docket_no':                r.docket_no or '',
            'mode':                     r.mode or '',
            'dispatch_date':            r.dispatch_date.isoformat() if r.dispatch_date else '',
            'expected_delivery_date':   r.expected_delivery_date.isoformat() if r.expected_delivery_date else '',
            'delivery_date':            r.delivery_date.isoformat() if r.delivery_date else '',
            'dispatch_vs_edd_tat':      r.dispatch_vs_edd_tat,
            'dispatch_vs_delivery_tat': r.dispatch_vs_delivery_tat,
            'remarks':                  r.remarks or '',
        })

    return JsonResponse({'success': True, 'data': data, 'count': len(data)})


# ─────────────────────────────────────────────────────────────────────────────
# COURIER DRILL-DOWN DETAIL API
# ─────────────────────────────────────────────────────────────────────────────

def report_courier_detail_api(request):
    """
    Return individual LP records for a courier delay-bucket drill-down.
    ?courier=  &month=  &year=  &mode=  &bucket=0..5  + standard filters
    bucket: 0=ontime(diff<=0), 1=diff=1, 2=diff=2, 3=diff=3, 4=diff=4, 5=diff>=5
    """
    courier = request.GET.get('courier', '')
    month   = request.GET.get('month')
    year    = request.GET.get('year')
    mode    = request.GET.get('mode', '')
    bucket  = request.GET.get('bucket')

    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(
        dispatch_vs_edd_tat__isnull=False,
        dispatch_vs_delivery_tat__isnull=False,
        dispatch_vs_edd_tat__gt=0,
        courier_name__isnull=False,
    ).exclude(courier_name='')

    if courier:
        qs = qs.filter(courier_name=courier)
    if month:
        try:
            qs = qs.filter(billing_date__month=int(month))
        except (ValueError, TypeError):
            pass
    if year:
        try:
            qs = qs.filter(billing_date__year=int(year))
        except (ValueError, TypeError):
            pass
    if mode and mode != '—':
        qs = qs.filter(mode=mode)

    qs = qs.annotate(
        diff=ExpressionWrapper(
            F('dispatch_vs_delivery_tat') - F('dispatch_vs_edd_tat'),
            output_field=IntegerField()
        )
    )

    if bucket is not None:
        try:
            b = int(bucket)
            if b == 0:
                qs = qs.filter(diff__lte=0)
            elif b == 5:
                qs = qs.filter(diff__gte=5)
            else:
                qs = qs.filter(diff=b)
        except (ValueError, TypeError):
            pass

    qs = qs.order_by('-billing_date')

    data = []
    for r in qs:
        data.append({
            'division':                 r.division or '',
            'location':                 r.location or '',
            'plant_name':               r.plant_name or '',
            'billing_doc':              r.billing_doc or '',
            'billing_date':             r.billing_date.isoformat() if r.billing_date else '',
            'inv_value':                str(r.inv_value or ''),
            'shipto_name':              r.shipto_name or '',
            'shipto_city':              r.shipto_city or '',
            'shipto_region':            r.shipto_region or '',
            'zone':                     r.zone or '',
            'courier_name':             r.courier_name or '',
            'docket_no':                r.docket_no or '',
            'mode':                     r.mode or '',
            'dispatch_date':            r.dispatch_date.isoformat() if r.dispatch_date else '',
            'expected_delivery_date':   r.expected_delivery_date.isoformat() if r.expected_delivery_date else '',
            'delivery_date':            r.delivery_date.isoformat() if r.delivery_date else '',
            'dispatch_vs_edd_tat':      r.dispatch_vs_edd_tat,
            'dispatch_vs_delivery_tat': r.dispatch_vs_delivery_tat,
            'remarks':                  r.remarks or '',
        })

    return JsonResponse({'success': True, 'data': data, 'count': len(data)})


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _excel_header_row(ws, headers, fg='1E3A8A'):
    """Write a styled header row to the worksheet."""
    ws.append(headers)
    hdr_fill = PatternFill(fill_type='solid', fgColor=fg)
    hdr_font = Font(bold=True, color='FFFFFF')
    hdr_align = Alignment(horizontal='center', vertical='center')
    for cell in ws[1]:
        cell.fill  = hdr_fill
        cell.font  = hdr_font
        cell.alignment = hdr_align


def _excel_autofit(ws):
    """Auto-fit column widths (capped at 40)."""
    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)


def _tat_status(edd, actual):
    """Delivery status label used in the raw export sheets (mirrors the legacy report)."""
    if edd is None or actual is None:
        return 'NA'
    diff = actual - edd
    if diff <= 0:
        return 'OK'
    if diff == 1:
        return '1 Day Delay'
    if diff <= 4:
        return f'{diff} Days Delay'
    return '5 and More Days Delay'


# Column layout shared by every raw detail sheet (header + per-record value extractor)
_DETAIL_HEADERS = [
    '#', 'Division', 'Location', 'Plant', 'Billing Doc', 'Billing Date', 'Inv Value',
    'Ship To', 'City', 'Region', 'Zone', 'Courier', 'Docket No', 'Mode', 'Boxes',
    'Ch. Weight', 'Dispatch Date', 'Expected Delivery', 'Delivery Date',
    'EDD TAT', 'Actual TAT', 'Delay Status', 'Remarks',
]


def _detail_row(r, idx):
    """Build one raw-detail row list for the Excel export (order matches _DETAIL_HEADERS)."""
    return [
        idx,
        r.division or '',
        r.location or '',
        r.plant_name or '',
        r.billing_doc or '',
        r.billing_date.isoformat() if r.billing_date else '',
        float(r.inv_value) if r.inv_value is not None else '',
        r.shipto_name or '',
        r.shipto_city or '',
        r.shipto_region or '',
        r.zone or '',
        r.courier_name or '',
        r.docket_no or '',
        r.mode or '',
        r.boxes if r.boxes is not None else '',
        float(r.ch_weight) if r.ch_weight is not None else '',
        r.dispatch_date.isoformat() if r.dispatch_date else '',
        r.expected_delivery_date.isoformat() if r.expected_delivery_date else '',
        r.delivery_date.isoformat() if r.delivery_date else '',
        r.dispatch_vs_edd_tat if r.dispatch_vs_edd_tat is not None else '',
        r.dispatch_vs_delivery_tat if r.dispatch_vs_delivery_tat is not None else '',
        _tat_status(r.dispatch_vs_edd_tat, r.dispatch_vs_delivery_tat),
        r.remarks or '',
    ]


def _add_detail_sheet(wb, qs, title):
    """Append a raw individual-records sheet (mirrors the legacy '…All' export sheet)."""
    ws = wb.create_sheet(title)
    _excel_header_row(ws, _DETAIL_HEADERS)
    for i, r in enumerate(qs, 1):
        ws.append(_detail_row(r, i))
    _excel_autofit(ws)
    return ws


def _excel_response(wb, filename):
    """Return an HttpResponse that downloads the workbook."""
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# TAT EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def report_tat_export(request):
    """Download TAT summary as Excel."""
    group_by = request.GET.get('group_by', 'Division')
    field_map = {'State': 'shipto_region', 'Zone': 'zone', 'Division': 'division'}
    db_field  = field_map.get(group_by, 'division')

    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(
        dispatch_vs_edd_tat__isnull=False,
        dispatch_vs_delivery_tat__isnull=False,
        dispatch_vs_edd_tat__gt=0,
    ).exclude(**{f'{db_field}__isnull': True}).exclude(**{f'{db_field}': ''})

    rows = qs.values(db_field).annotate(
        grand_total=Count('id'),
        hit=Sum(Case(When(dispatch_vs_delivery_tat__lte=F('dispatch_vs_edd_tat'), then=1), default=0, output_field=IntegerField())),
        miss=Sum(Case(When(dispatch_vs_delivery_tat__gt=F('dispatch_vs_edd_tat'), then=1), default=0, output_field=IntegerField())),
    ).order_by(db_field)

    wb = Workbook()
    ws = wb.active
    ws.title = f'TAT_{group_by}'

    _excel_header_row(ws, ['#', group_by, 'Met', 'Not Met', 'Grand Total', 'Met %', 'Not Met %', 'Total %'])

    _, display_map, _ = _division_maps()
    for i, r in enumerate(rows, 1):
        grand = r['grand_total'] or 0
        hit   = r['hit']         or 0
        miss  = r['miss']        or 0
        label = r[db_field] or '(blank)'
        if group_by == 'Division':
            label = _division_label(label, display_map)
        ws.append([
            i, label, hit, miss, grand,
            f"{_safe_pct(hit,  grand)}%",
            f"{_safe_pct(miss, grand)}%",
            f"{round(_safe_pct(hit, grand) + _safe_pct(miss, grand), 2)}%",
        ])

    _excel_autofit(ws)

    # Second sheet: the individual records behind the summary so the drill-down
    # list is exported alongside the totals (matches the legacy report).
    _add_detail_sheet(wb, qs.order_by('-billing_date'), 'Detail Records')

    return _excel_response(wb, f'TAT_Report_{group_by}.xlsx')


# ─────────────────────────────────────────────────────────────────────────────
# COST EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def report_cost_export(request):
    """Download Cost report (all 3 views in one workbook) as Excel."""
    def _cost_qs(group_fields):
        qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
        sales_types = request.GET.get('sales_types', '').strip()
        if sales_types:
            st_list = [s.strip() for s in sales_types.split(',') if s.strip()]
            if st_list:
                type_codes = list(DomesticSalesCard.objects.filter(
                    sales_type_description__in=st_list
                ).values_list('sales_type', flat=True).distinct())
                if type_codes:
                    qs = qs.filter(doc_type__in=type_codes)
        return qs.values(*group_fields).annotate(
            total_sales    = Sum('inv_value'),
            total_lc_basic = Sum('basic_logistics_cost'),
            total_lc_other = Sum('other_charges'),
        ).order_by(*group_fields)

    budget_map, display_map, _ = _division_maps()
    wb = Workbook()

    # Sheet 1 — Division
    ws1 = wb.active
    ws1.title = 'Division'
    _excel_header_row(ws1, ['#', 'Division', 'Total Sales (INR)', 'Actual LC (INR)',
                             'Actual LC %', 'Budget LC %', 'Difference %', 'Abs Amount (INR)'])
    for i, r in enumerate(_cost_qs(['division']), 1):
        div    = r.get('division') or ''
        sales  = float(r['total_sales']    or 0)
        actual = float(r['total_lc_basic'] or 0) + float(r['total_lc_other'] or 0)
        budget = _budget_lc(div, budget_map)
        actual_pct = _safe_pct(actual, sales)
        ws1.append([i, _division_label(div, display_map), round(sales, 2), round(actual, 2),
                    f"{actual_pct}%", f"{budget}%",
                    f"{round(budget - actual_pct, 2)}%",
                    round(((sales * budget) / 100) - actual, 2)])
    _excel_autofit(ws1)

    # Sheet 2 — Mode
    ws2 = wb.create_sheet('Mode')
    _excel_header_row(ws2, ['#', 'Division', 'Mode', 'Total Sales (INR)', 'Actual LC (INR)',
                             'Actual LC %', 'Budget LC %', 'Abs Amount (INR)'])
    for i, r in enumerate(_cost_qs(['division', 'mode']), 1):
        div    = r.get('division') or ''
        sales  = float(r['total_sales']    or 0)
        actual = float(r['total_lc_basic'] or 0) + float(r['total_lc_other'] or 0)
        budget = _budget_lc(div, budget_map)
        actual_pct = _safe_pct(actual, sales)
        ws2.append([i, _division_label(div, display_map), r.get('mode') or '(blank)',
                    round(sales, 2), round(actual, 2),
                    f"{actual_pct}%", f"{budget}%",
                    round(((sales * budget) / 100) - actual, 2)])
    _excel_autofit(ws2)

    # Sheet 3 — State
    ws3 = wb.create_sheet('State')
    _excel_header_row(ws3, ['#', 'State', 'Division', 'Total Sales (INR)', 'Actual LC (INR)',
                             'Actual LC %', 'Budget LC %', 'Abs Amount (INR)'])
    for i, r in enumerate(_cost_qs(['division', 'shipto_region']), 1):
        div    = r.get('division') or ''
        sales  = float(r['total_sales']    or 0)
        actual = float(r['total_lc_basic'] or 0) + float(r['total_lc_other'] or 0)
        budget = _budget_lc(div, budget_map)
        actual_pct = _safe_pct(actual, sales)
        ws3.append([i, r.get('shipto_region') or 'Other Region', _division_label(div, display_map),
                    round(sales, 2), round(actual, 2),
                    f"{actual_pct}%", f"{budget}%",
                    round(((sales * budget) / 100) - actual, 2)])
    _excel_autofit(ws3)

    return _excel_response(wb, 'Cost_Report.xlsx')


# ─────────────────────────────────────────────────────────────────────────────
# COURIER EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def report_courier_export(request):
    """Download Courier performance report as Excel."""
    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(
        dispatch_vs_edd_tat__isnull=False,
        dispatch_vs_delivery_tat__isnull=False,
        dispatch_vs_edd_tat__gt=0,
        courier_name__isnull=False,
    ).exclude(courier_name='').annotate(
        diff=ExpressionWrapper(F('dispatch_vs_delivery_tat') - F('dispatch_vs_edd_tat'), output_field=IntegerField()),
        yr=ExtractYear('billing_date'),
        mo=ExtractMonth('billing_date'),
    )

    rows = qs.values('yr', 'mo', 'courier_name', 'mode').annotate(
        d0=Sum(Case(When(diff__lte=0, then=1), default=0, output_field=IntegerField())),
        d1=Sum(Case(When(diff=1,     then=1), default=0, output_field=IntegerField())),
        d2=Sum(Case(When(diff=2,     then=1), default=0, output_field=IntegerField())),
        d3=Sum(Case(When(diff=3,     then=1), default=0, output_field=IntegerField())),
        d4=Sum(Case(When(diff=4,     then=1), default=0, output_field=IntegerField())),
        d5=Sum(Case(When(diff__gte=5, then=1), default=0, output_field=IntegerField())),
    ).order_by('yr', 'mo', 'courier_name', 'mode')

    MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December']

    wb = Workbook()
    ws = wb.active
    ws.title = 'Courier Performance'
    _excel_header_row(ws, ['#', 'Month', 'Year', 'Courier', 'Mode',
                            'On Time', '+1 Day', '+2 Days', '+3 Days', '+4 Days', '+5 Days',
                            'Grand Total', 'On Time %', 'Delay %'])

    i = 0
    for r in rows:
        d0 = r['d0'] or 0; d1 = r['d1'] or 0; d2 = r['d2'] or 0
        d3 = r['d3'] or 0; d4 = r['d4'] or 0; d5 = r['d5'] or 0
        grand = d0+d1+d2+d3+d4+d5
        if not grand:
            continue
        i += 1
        mo = r['mo'] or 0
        ws.append([
            i, MONTHS[mo] if mo else '—', r['yr'],
            r['courier_name'], r['mode'] or '—',
            d0, d1, d2, d3, d4, d5, grand,
            f"{_safe_pct(d0, grand, 1)}%",
            f"{_safe_pct(d1+d2+d3+d4+d5, grand, 1)}%",
        ])

    _excel_autofit(ws)

    # Second sheet: the individual records behind the summary (matches the legacy
    # "Courier Wise All" sheet) so the drill-down number list is exported too.
    _add_detail_sheet(wb, qs.order_by('-billing_date'), 'Detail Records')

    return _excel_response(wb, 'Courier_Performance_Report.xlsx')


# ─────────────────────────────────────────────────────────────────────────────
# LC CONTRIBUTION EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def report_lc_export(request):
    """Download LC Contribution report as Excel."""
    qs = _apply_lp_filters(LogisticsPerformance.objects.all(), request.GET)
    qs = qs.filter(courier_name__isnull=False).exclude(courier_name='')

    totals = qs.aggregate(
        total_lc_basic=Sum('basic_logistics_cost'),
        total_lc_other=Sum('other_charges'),
        total_weight  =Sum('ch_weight'),
        total_sales   =Sum('inv_value'),
    )
    total_lc = float(totals['total_lc_basic'] or 0) + float(totals['total_lc_other'] or 0)
    total_wt = float(totals['total_weight']   or 0)

    rows = qs.values('courier_name').annotate(
        sale_val = Sum('inv_value'),
        lc_basic = Sum('basic_logistics_cost'),
        lc_other = Sum('other_charges'),
        wt       = Sum('ch_weight'),
    ).order_by('-lc_basic')

    wb = Workbook()
    ws = wb.active
    ws.title = 'LC Contribution'
    _excel_header_row(ws, ['#', 'Courier Name', 'Sale Value (INR)', 'LC Freight Value (INR)',
                            'Biz Contribution %', 'LC %', 'Weight (kg)', 'Weight %'])

    for i, r in enumerate(rows, 1):
        sv   = float(r['sale_val']  or 0)
        lcfv = float(r['lc_basic'] or 0) + float(r['lc_other'] or 0)
        wt   = float(r['wt']       or 0)
        ws.append([
            i, r['courier_name'],
            round(sv, 2), round(lcfv, 2),
            f"{_safe_pct(lcfv, total_lc)}%",
            f"{_safe_pct(lcfv, sv) if sv else 0}%",
            round(wt, 2),
            f"{_safe_pct(wt, total_wt)}%",
        ])

    _excel_autofit(ws)
    return _excel_response(wb, 'LC_Contribution_Report.xlsx')


# ─────────────────────────────────────────────────────────────────────────────
# APPLY LP RBAC TO ALL API VIEWS
# (module-level reassignment so urls.py picks up the wrapped versions)
# ─────────────────────────────────────────────────────────────────────────────

_ADMIN_APIS = [
    # Sales Type Master
    'sales_list_api', 'sales_create_api', 'sales_update_api', 'sales_detail_api',
    'sales_delete_api', 'sales_upload_api', 'sales_preview_api', 'sales_confirm_api',
    # Location Master
    'location_list_api', 'location_create_api', 'location_update_api',
    'location_delete_api', 'location_upload_api', 'location_preview_api', 'location_confirm_api',
    # Courier Master
    'courier_list_api', 'courier_create_api', 'courier_update_api',
    'courier_delete_api', 'courier_upload_api', 'courier_preview_api', 'courier_confirm_api',
    # Division Master
    'division_list_api', 'division_create_api', 'division_update_api',
    'division_delete_api', 'division_upload_api', 'division_preview_api', 'division_confirm_api',
    # Zone Master
    'zone_list_api', 'zone_create_api', 'zone_update_api',
    'zone_delete_api', 'zone_upload_api', 'zone_preview_api', 'zone_confirm_api',
    # Mode Master
    'mode_list_api', 'mode_create_api', 'mode_update_api',
    'mode_delete_api', 'mode_upload_api', 'mode_preview_api', 'mode_confirm_api',
    # State Master
    'state_list_api', 'state_create_api', 'state_update_api',
    'state_delete_api', 'state_upload_api', 'state_preview_api', 'state_confirm_api',
    # Plant Master
    'plant_list_api', 'plant_create_api', 'plant_update_api',
    'plant_delete_api', 'plant_upload_api', 'plant_preview_api', 'plant_confirm_api',
]

_MEMBER_APIS = [
    'lp_list_api', 'lp_create_api', 'lp_update_api', 'lp_delete_api',
    'lp_bulk_delete_api', 'lp_upload_api', 'lp_preview_api', 'lp_confirm_api',
    'lp_pending_api', 'lp_pending_export_api',
    'lp_pending_update_upload_api', 'lp_pending_update_confirm_api',
]

_SHARED_APIS = [
    'report_stats_api', 'report_tat_api', 'report_cost_api',
    'report_courier_api', 'report_lc_api', 'report_pincode_api',
    'report_tat_detail_api', 'report_courier_detail_api',
    'report_tat_export', 'report_cost_export', 'report_courier_export', 'report_lc_export',
]

_g = globals()
for _name in _ADMIN_APIS:
    _g[_name] = _lp_api_require('super', 'admin')(_g[_name])
for _name in _MEMBER_APIS:
    _g[_name] = _lp_api_require('super', 'member')(_g[_name])
for _name in _SHARED_APIS:
    _g[_name] = _lp_api_require('super', 'admin', 'member')(_g[_name])
