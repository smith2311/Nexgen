import csv
import io
import json
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from openpyxl import load_workbook

from ui.decorators import uses_components

from .models import (
    VatCountryMaster,
    VatRateMaster,
    VatChaEmailMaster,
    VatInvoice,
    VatInvoiceDocument,
)
from .services import currency, email_draft
from .services.pipeline import process_invoice


# ═════════════════════════════════════════════════════════════════════════════
# VAT ROLE-BASED ACCESS CONTROL
# ═════════════════════════════════════════════════════════════════════════════

def _vat_role(user):
    """Return VAT access role: 'super' | 'admin' | 'member' | None."""
    if not user.is_authenticated:
        return None
    if user.is_superuser or user.groups.filter(name='VAT Reconciliation').exists():
        return 'super'
    if user.groups.filter(name='VAT-Admin').exists():
        return 'admin'
    if user.groups.filter(name='VAT-Members').exists():
        return 'member'
    return None


def _vat_require(*roles):
    """Page-view decorator: redirect non-authorised users to VAT home (or site root)."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if _vat_role(request.user) not in roles:
                return redirect('vat:home')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _vat_api_require(*roles):
    """API-view decorator: return 403 JSON for unauthorised users."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if _vat_role(request.user) not in roles:
                return JsonResponse({'error': 'Permission denied'}, status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ═════════════════════════════════════════════════════════════════════════════
# MODULE HOME (submodule selector — Warehouse Inspection style)
# ═════════════════════════════════════════════════════════════════════════════
def vat_home(request):
    """Landing page: redirect users with no VAT role back to the site root."""
    role = _vat_role(request.user)
    if role is None:
        return redirect('/')
    return render(request, 'vat_reconciliation/home.html', {'vat_role': role})


# ═════════════════════════════════════════════════════════════════════════════
# SHARED UPLOAD HELPERS  (mirrors logistics_performance._read_uploaded_location_rows)
# ═════════════════════════════════════════════════════════════════════════════
def _read_uploaded_rows(file_obj, filename):
    filename = filename or ""
    ext = filename.split(".")[-1].lower()

    if ext == "csv":
        text = file_obj.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        return [
            {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            for row in reader
        ]

    if ext in ("xlsx", "xls"):
        wb = load_workbook(filename=io.BytesIO(file_obj.read()), read_only=True, data_only=True)
        ws = wb.active
        headers = [
            str(cell).strip().lower() if cell is not None else ""
            for cell in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        ]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(row):
                continue
            rows.append({
                (headers[i] if i < len(headers) else f"col_{i}"):
                    (str(value).strip() if value is not None else "")
                for i, value in enumerate(row)
            })
        return rows

    raise ValueError(f"Unsupported file type: {ext}")


def _to_decimal(value, default=None):
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return default


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y", "active")


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════
@_vat_require('super', 'admin', 'member')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def vat_dashboard(request):
    return render(request, "vat_reconciliation/dashboard.html", {})


# Pipeline stages shown on the left rail (label + FontAwesome icon).
PIPELINE_STAGES = [
    ("UPLOADED",   "Upload",       "fa-cloud-upload-alt"),
    ("OCR",        "OCR Extract",  "fa-eye"),
    ("TRANSLATED", "Translate",    "fa-language"),
    ("CONVERTED",  "Currency",     "fa-exchange-alt"),
    ("CALCULATED", "VAT Calc",     "fa-calculator"),
    ("RECONCILED", "Reconcile",    "fa-balance-scale"),
]


def dashboard_stats_api(request):
    """All numbers the command-center dashboard needs (no network calls)."""
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    qs = VatInvoice.objects.all()
    processed = qs.filter(stage="RECONCILED")
    month_processed = processed.filter(updated_at__gte=month_start)
    month_matched = month_processed.filter(recon_status="MATCHED").count()
    month_total = month_processed.count()
    match_pct = round((month_matched / month_total) * 100) if month_total else 0

    # Variance trend — last 12 months, summed absolute variance
    trend_raw = (
        processed.filter(variance__isnull=False)
        .annotate(m=TruncMonth("updated_at"))
        .values("m")
        .annotate(total=Sum("variance"))
        .order_by("m")
    )
    variance_trend = [
        {"month": r["m"].strftime("%b %Y") if r["m"] else "—",
         "value": float(r["total"] or 0)}
        for r in trend_raw
    ][-12:]

    # Pipeline rail — how many invoices have reached each stage
    stage_counts = dict(qs.values_list("stage").annotate(c=Count("id")))
    order = [s[0] for s in PIPELINE_STAGES]
    rail = []
    for code, label, icon in PIPELINE_STAGES:
        # an invoice "passed" a stage if its current stage index >= this one
        reached = sum(
            stage_counts.get(o, 0) for o in order[order.index(code):]
        )
        rail.append({"code": code, "label": label, "icon": icon, "count": reached})

    languages = list(
        VatCountryMaster.objects.filter(is_active=True)
        .exclude(language="").values_list("language", flat=True).distinct()
    )

    return JsonResponse({
        "success": True,
        "counts": {
            "invoices": qs.count(),
            "matched": qs.filter(recon_status="MATCHED").count(),
            "discrepancy": qs.filter(recon_status="DISCREPANCY").count(),
            "pending": qs.filter(recon_status="PENDING").count(),
            "countries": VatCountryMaster.objects.filter(is_active=True).count(),
            "rates": VatRateMaster.objects.filter(is_active=True).count(),
        },
        "month": {"processed": month_total, "matched": month_matched, "match_pct": match_pct},
        "variance_trend": variance_trend,
        "rail": rail,
        "languages": languages,
        "countries": [_country_dict(c) for c in VatCountryMaster.objects.filter(is_active=True).order_by("country")],
        "recent": [_invoice_dict(i) for i in VatInvoice.objects.order_by("-created_at")[:8]],
    })


def dashboard_fx_api(request):
    """Live exchange rates for the active country currency pairs (and an optional
    explicit pair via ?from=EUR&to=INR). Network-bound, so it's its own endpoint
    the dashboard calls asynchronously."""
    pairs = []
    seen = set()

    f = (request.GET.get("from") or "").upper().strip()
    t = (request.GET.get("to") or "").upper().strip()
    if f and t:
        pairs.append((f, t))
        seen.add((f, t))

    for c in VatCountryMaster.objects.filter(is_active=True):
        inv = (c.standard_invoice_currency or "").upper().strip()
        loc = (c.local_currency or "").upper().strip()
        if inv and loc and (inv, loc) not in seen:
            pairs.append((inv, loc))
            seen.add((inv, loc))

    rates = []
    for frm, to in pairs[:12]:
        try:
            rate, source = currency.get_rate(frm, to)
            rates.append({"from": frm, "to": to, "pair": f"{frm}→{to}",
                          "rate": float(rate), "source": source})
        except Exception as exc:  # noqa: BLE001 — show what we can
            rates.append({"from": frm, "to": to, "pair": f"{frm}→{to}",
                          "rate": None, "error": str(exc)})

    return JsonResponse({"success": True, "rates": rates})


# ═════════════════════════════════════════════════════════════════════════════
# COUNTRY MASTER
# ═════════════════════════════════════════════════════════════════════════════
COUNTRY_FIELDS = [
    "country", "language", "local_currency", "standard_invoice_currency",
    "cha_partner_name", "local_entity", "tolerance_limit", "is_active",
]


def _country_dict(c):
    return {
        "id": c.id,
        "country": c.country,
        "language": c.language,
        "local_currency": c.local_currency,
        "standard_invoice_currency": c.standard_invoice_currency,
        "cha_partner_name": c.cha_partner_name,
        "local_entity": c.local_entity,
        "tolerance_limit": str(c.tolerance_limit),
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@_vat_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def country_master_list(request):
    return render(request, "vat_reconciliation/country_master.html", {})


def country_list_api(request):
    search = request.GET.get("search", "").strip()
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 15))

    qs = VatCountryMaster.objects.all()
    if search:
        qs = qs.filter(
            Q(country__icontains=search) | Q(language__icontains=search) |
            Q(local_currency__icontains=search) | Q(cha_partner_name__icontains=search)
        )

    total = qs.count()
    start = (page - 1) * per_page
    rows = qs.order_by("country")[start:start + per_page]
    data = [_country_dict(c) for c in rows]
    stats = {
        "total": total,
        "active": qs.filter(is_active=True).count(),
        "currencies": qs.values("local_currency").distinct().count(),
    }
    return JsonResponse({"success": True, "count": len(data), "total": total,
                         "page": page, "per_page": per_page, "data": data, "stats": stats})


def _country_payload(data):
    return {
        "country": (data.get("country") or "").strip(),
        "language": (data.get("language") or "").strip(),
        "local_currency": (data.get("local_currency") or "").strip(),
        "standard_invoice_currency": (data.get("standard_invoice_currency") or "").strip(),
        "cha_partner_name": (data.get("cha_partner_name") or "").strip() or None,
        "local_entity": (data.get("local_entity") or "").strip() or None,
        "tolerance_limit": _to_decimal(data.get("tolerance_limit"), Decimal("0.05")),
        "is_active": _truthy(data.get("is_active", True)),
    }


def country_create_api(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Create requires POST"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    errors = {}
    for f in ("country", "language", "local_currency", "standard_invoice_currency"):
        if not (data.get(f) or "").strip():
            errors[f] = ["This field is required"]
    if errors:
        return JsonResponse({"success": False, "message": "Validation failed", "errors": errors}, status=400)

    payload = _country_payload(data)
    if VatCountryMaster.objects.filter(country__iexact=payload["country"]).exists():
        return JsonResponse({"success": False, "message": "This country already exists",
                             "errors": {"country": ["Already exists"]}}, status=400)

    revived = VatCountryMaster.all_objects.filter(
        country__iexact=payload["country"], is_deleted=True).first()
    if revived:
        for k, v in payload.items():
            setattr(revived, k, v)
        revived.is_deleted = False
        revived.deleted_by = None
        revived.deleted_at = None
        revived.save()
        obj = revived
    else:
        obj = VatCountryMaster.objects.create(**payload)
    return JsonResponse({"success": True, "message": "Country created", "id": obj.id,
                         "data": _country_dict(obj)}, status=201)


def country_update_api(request, id):
    if request.method != "PUT":
        return JsonResponse({"success": False, "message": "Update requires PUT"}, status=405)
    try:
        obj = VatCountryMaster.objects.get(id=id)
    except VatCountryMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    payload = _country_payload(data)
    if VatCountryMaster.objects.filter(country__iexact=payload["country"]).exclude(id=id).exists():
        return JsonResponse({"success": False, "message": "This country already exists",
                             "errors": {"country": ["Already exists"]}}, status=400)
    for k, v in payload.items():
        setattr(obj, k, v)
    obj.save()
    return JsonResponse({"success": True, "message": "Country updated", "id": obj.id,
                         "data": _country_dict(obj)})


def country_delete_api(request, id):
    if request.method != "DELETE":
        return JsonResponse({"success": False, "message": "Delete requires DELETE"}, status=405)
    try:
        obj = VatCountryMaster.objects.get(id=id)
    except VatCountryMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    obj.soft_delete(request.user.username if request.user.is_authenticated else None)
    return JsonResponse({"success": True, "message": f"Country '{obj.country}' deleted"})


def _build_country_preview(rows):
    seen = set()
    preview = []
    for idx, raw in enumerate(rows, start=2):
        data = {
            "country": (raw.get("country") or "").strip(),
            "language": (raw.get("language") or "").strip(),
            "local_currency": (raw.get("local_currency") or "").strip(),
            "standard_invoice_currency": (raw.get("standard_invoice_currency")
                                          or raw.get("invoice_currency") or "").strip(),
            "cha_partner_name": (raw.get("cha_partner_name") or raw.get("cha_partner") or "").strip(),
            "local_entity": (raw.get("local_entity") or "").strip(),
            "tolerance_limit": (raw.get("tolerance_limit") or raw.get("tolerance") or "0.05").strip(),
            "is_active": (raw.get("is_active") or "true").strip(),
        }
        errors = []
        for f in ("country", "language", "local_currency", "standard_invoice_currency"):
            if not data[f]:
                errors.append(f"Missing {f}")

        key = data["country"].lower()
        dup_in_file = bool(key) and key in seen
        if key:
            seen.add(key)
        exists = bool(key) and VatCountryMaster.objects.filter(country__iexact=data["country"]).exists()

        if errors:
            status, message = "error", "; ".join(errors)
        elif dup_in_file or exists:
            status, message = "duplicate", ("Duplicate in file" if dup_in_file else "Already exists")
        else:
            status, message = "ok", ""
        preview.append({"row": idx, "status": status, "message": message, "data": data})
    return preview


def country_upload_api(request):
    return _generic_upload(request, "country", _build_country_preview)


def country_preview_api(request):
    return _generic_preview(request, "country")


def country_confirm_api(request):
    def creator(data):
        payload = {
            "country": data["country"], "language": data["language"],
            "local_currency": data["local_currency"],
            "standard_invoice_currency": data["standard_invoice_currency"],
            "cha_partner_name": data["cha_partner_name"] or None,
            "local_entity": data["local_entity"] or None,
            "tolerance_limit": _to_decimal(data["tolerance_limit"], Decimal("0.05")),
            "is_active": _truthy(data["is_active"]),
        }
        existing = VatCountryMaster.all_objects.filter(country__iexact=data["country"]).first()
        if existing is None:
            VatCountryMaster.objects.create(**payload)
            return True
        if existing.is_deleted:
            for k, v in payload.items():
                setattr(existing, k, v)
            existing.is_deleted = False
            existing.deleted_by = None
            existing.deleted_at = None
            existing.save()
            return True
        return False
    return _generic_confirm(request, "country", creator)


# ═════════════════════════════════════════════════════════════════════════════
# VAT RATE MASTER
# ═════════════════════════════════════════════════════════════════════════════
def _rate_dict(r):
    return {
        "id": r.id,
        "country": r.country,
        "calc_type": r.calc_type,
        "hsn_sac_code": r.hsn_sac_code,
        "vat_percent": str(r.vat_percent),
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@_vat_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def rate_master_list(request):
    return render(request, "vat_reconciliation/vat_rate_master.html", {})


def rate_list_api(request):
    search = request.GET.get("search", "").strip()
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 15))

    qs = VatRateMaster.objects.all()
    if search:
        qs = qs.filter(
            Q(country__icontains=search) | Q(hsn_sac_code__icontains=search) |
            Q(calc_type__icontains=search)
        )
    total = qs.count()
    start = (page - 1) * per_page
    rows = qs.order_by("country", "hsn_sac_code")[start:start + per_page]
    data = [_rate_dict(r) for r in rows]
    stats = {
        "total": total,
        "countries": qs.values("country").distinct().count(),
        "hsn": qs.filter(calc_type="HSN").count(),
    }
    return JsonResponse({"success": True, "count": len(data), "total": total,
                         "page": page, "per_page": per_page, "data": data, "stats": stats})


def _rate_payload(data):
    calc_type = (data.get("calc_type") or "HSN").strip().upper()
    if calc_type not in ("HSN", "FLAT"):
        calc_type = "HSN"
    hsn = (data.get("hsn_sac_code") or "").strip()
    return {
        "country": (data.get("country") or "").strip(),
        "calc_type": calc_type,
        "hsn_sac_code": (hsn if calc_type == "HSN" else "") or None,
        "vat_percent": _to_decimal(data.get("vat_percent")),
        "is_active": _truthy(data.get("is_active", True)),
    }


def _country_in_master(name):
    """True if an active country with this name exists in Country Master."""
    name = (name or "").strip()
    return bool(name) and VatCountryMaster.objects.filter(country__iexact=name).exists()


def _rate_validate(payload):
    errors = {}
    if not payload["country"]:
        errors["country"] = ["This field is required"]
    elif not _country_in_master(payload["country"]):
        errors["country"] = ["Country not found in Country Master — add it there first"]
    if payload["vat_percent"] is None:
        errors["vat_percent"] = ["Valid VAT % required"]
    if payload["calc_type"] == "HSN" and not payload["hsn_sac_code"]:
        errors["hsn_sac_code"] = ["HSN/SAC required for HSN-based rate"]
    return errors


def rate_create_api(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Create requires POST"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    payload = _rate_payload(data)
    errors = _rate_validate(payload)
    if errors:
        return JsonResponse({"success": False, "message": "Validation failed", "errors": errors}, status=400)

    dup = VatRateMaster.objects.filter(
        country__iexact=payload["country"], calc_type=payload["calc_type"],
        hsn_sac_code=payload["hsn_sac_code"])
    if dup.exists():
        return JsonResponse({"success": False, "message": "This VAT rate already exists",
                             "errors": {"hsn_sac_code": ["Already exists"]}}, status=400)
    obj = VatRateMaster.objects.create(**payload)
    return JsonResponse({"success": True, "message": "VAT rate created", "id": obj.id,
                         "data": _rate_dict(obj)}, status=201)


def rate_update_api(request, id):
    if request.method != "PUT":
        return JsonResponse({"success": False, "message": "Update requires PUT"}, status=405)
    try:
        obj = VatRateMaster.objects.get(id=id)
    except VatRateMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    payload = _rate_payload(data)
    errors = _rate_validate(payload)
    if errors:
        return JsonResponse({"success": False, "message": "Validation failed", "errors": errors}, status=400)
    dup = VatRateMaster.objects.filter(
        country__iexact=payload["country"], calc_type=payload["calc_type"],
        hsn_sac_code=payload["hsn_sac_code"],
    ).exclude(id=id)
    if dup.exists():
        return JsonResponse({"success": False, "message": "This VAT rate already exists",
                             "errors": {"hsn_sac_code": ["Already exists"]}}, status=400)
    for k, v in payload.items():
        setattr(obj, k, v)
    obj.save()
    return JsonResponse({"success": True, "message": "VAT rate updated", "id": obj.id,
                         "data": _rate_dict(obj)})


def rate_delete_api(request, id):
    if request.method != "DELETE":
        return JsonResponse({"success": False, "message": "Delete requires DELETE"}, status=405)
    try:
        obj = VatRateMaster.objects.get(id=id)
    except VatRateMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    obj.soft_delete(request.user.username if request.user.is_authenticated else None)
    return JsonResponse({"success": True, "message": "VAT rate deleted"})


def _build_rate_preview(rows):
    seen = set()
    preview = []
    # Countries available in Country Master (active), lowercased for matching.
    master_countries = {
        c.lower() for c in VatCountryMaster.objects.values_list("country", flat=True)
    }
    for idx, raw in enumerate(rows, start=2):
        calc_type = (raw.get("calc_type") or raw.get("vat_calculation_type") or "HSN").strip().upper()
        if calc_type not in ("HSN", "FLAT"):
            calc_type = "HSN"
        hsn = (raw.get("hsn_sac_code") or raw.get("hsn") or raw.get("sac") or "").strip()
        data = {
            "country": (raw.get("country") or "").strip(),
            "calc_type": calc_type,
            "hsn_sac_code": hsn if calc_type == "HSN" else "",
            "vat_percent": (raw.get("vat_percent") or raw.get("vat") or raw.get("standard_vat") or "").strip(),
            "is_active": (raw.get("is_active") or "true").strip(),
        }
        errors = []
        if not data["country"]:
            errors.append("Missing country")
        elif data["country"].lower() not in master_countries:
            errors.append("Country not in Country Master")
        if _to_decimal(data["vat_percent"]) is None:
            errors.append("Invalid vat_percent")
        if calc_type == "HSN" and not data["hsn_sac_code"]:
            errors.append("Missing hsn_sac_code")

        key = (data["country"].lower(), calc_type, data["hsn_sac_code"].lower())
        dup_in_file = key in seen
        seen.add(key)
        exists = VatRateMaster.objects.filter(
            country__iexact=data["country"], calc_type=calc_type,
            hsn_sac_code=(data["hsn_sac_code"] or None)).exists()

        if errors:
            status, message = "error", "; ".join(errors)
        elif dup_in_file or exists:
            status, message = "duplicate", ("Duplicate in file" if dup_in_file else "Already exists")
        else:
            status, message = "ok", ""
        preview.append({"row": idx, "status": status, "message": message, "data": data})
    return preview


def rate_upload_api(request):
    return _generic_upload(request, "rate", _build_rate_preview)


def rate_preview_api(request):
    return _generic_preview(request, "rate")


def rate_confirm_api(request):
    def creator(data):
        payload = {
            "country": data["country"],
            "calc_type": data["calc_type"],
            "hsn_sac_code": (data["hsn_sac_code"] or None) if data["calc_type"] == "HSN" else None,
            "vat_percent": _to_decimal(data["vat_percent"]),
            "is_active": _truthy(data["is_active"]),
        }
        existing = VatRateMaster.all_objects.filter(
            country__iexact=data["country"], calc_type=data["calc_type"],
            hsn_sac_code=payload["hsn_sac_code"]).first()
        if existing is None:
            VatRateMaster.objects.create(**payload)
            return True
        if existing.is_deleted:
            for k, v in payload.items():
                setattr(existing, k, v)
            existing.is_deleted = False
            existing.deleted_by = None
            existing.deleted_at = None
            existing.save()
            return True
        return False
    return _generic_confirm(request, "rate", creator)


# ═════════════════════════════════════════════════════════════════════════════
# CHA EMAIL MASTER
# ═════════════════════════════════════════════════════════════════════════════
def _split_emails(value):
    """Split a free-text email cell (comma / semicolon / newline separated)
    into a clean list of individual addresses."""
    import re
    parts = re.split(r"[,;\n\r]+", str(value or ""))
    return [p.strip() for p in parts if p.strip() and "@" in p]


def _cha_dict(c):
    return {
        "id": c.id,
        "country": c.country,
        "company_name": c.company_name,
        "vat_approval": c.vat_approval,
        "cha_name": c.cha_name,
        "poc_name": c.poc_name,
        "poc_email": c.poc_email,
        "emails": _split_emails(c.poc_email),
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@_vat_require('super', 'admin')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def cha_master_list(request):
    return render(request, "vat_reconciliation/cha_email_master.html", {})


def cha_list_api(request):
    search = request.GET.get("search", "").strip()
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 15))
    qs = VatChaEmailMaster.objects.all()
    if search:
        qs = qs.filter(
            Q(country__icontains=search) | Q(company_name__icontains=search) |
            Q(cha_name__icontains=search) | Q(poc_name__icontains=search) |
            Q(poc_email__icontains=search)
        )
    total = qs.count()
    start = (page - 1) * per_page
    rows = qs.order_by("country", "cha_name")[start:start + per_page]
    data = [_cha_dict(c) for c in rows]
    stats = {
        "total": total,
        "active": qs.filter(is_active=True).count(),
        "countries": qs.values("country").distinct().count(),
    }
    return JsonResponse({"success": True, "count": len(data), "total": total,
                         "page": page, "per_page": per_page, "data": data, "stats": stats})


def _cha_payload(data):
    return {
        "country": (data.get("country") or "").strip(),
        "company_name": (data.get("company_name") or "").strip() or None,
        "vat_approval": _truthy(data.get("vat_approval", False)),
        "cha_name": (data.get("cha_name") or "").strip() or None,
        "poc_name": (data.get("poc_name") or "").strip() or None,
        "poc_email": (data.get("poc_email") or "").strip() or None,
        "is_active": _truthy(data.get("is_active", True)),
    }


def cha_create_api(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Create requires POST"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    payload = _cha_payload(data)
    if not payload["country"]:
        return JsonResponse({"success": False, "message": "Validation failed",
                             "errors": {"country": ["Country is required"]}}, status=400)
    obj = VatChaEmailMaster.objects.create(**payload)
    return JsonResponse({"success": True, "message": "CHA contact created", "id": obj.id,
                         "data": _cha_dict(obj)}, status=201)


def cha_update_api(request, id):
    if request.method != "PUT":
        return JsonResponse({"success": False, "message": "Update requires PUT"}, status=405)
    try:
        obj = VatChaEmailMaster.objects.get(id=id)
    except VatChaEmailMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    payload = _cha_payload(data)
    if not payload["country"]:
        return JsonResponse({"success": False, "message": "Validation failed",
                             "errors": {"country": ["Country is required"]}}, status=400)
    for k, v in payload.items():
        setattr(obj, k, v)
    obj.save()
    return JsonResponse({"success": True, "message": "CHA contact updated", "id": obj.id,
                         "data": _cha_dict(obj)})


def cha_delete_api(request, id):
    if request.method != "DELETE":
        return JsonResponse({"success": False, "message": "Delete requires DELETE"}, status=405)
    try:
        obj = VatChaEmailMaster.objects.get(id=id)
    except VatChaEmailMaster.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    obj.soft_delete(request.user.username if request.user.is_authenticated else None)
    return JsonResponse({"success": True, "message": "CHA contact deleted"})


def _build_cha_preview(rows):
    seen = set()
    preview = []
    for idx, raw in enumerate(rows, start=2):
        data = {
            "country": (raw.get("country") or "").strip(),
            "company_name": (raw.get("company_name") or raw.get("name_of_the_company")
                             or raw.get("company") or "").strip(),
            "vat_approval": (raw.get("vat_approval") or raw.get("vat approval") or "").strip(),
            "cha_name": (raw.get("cha_name") or raw.get("cha") or "").strip(),
            "poc_name": (raw.get("poc_name") or raw.get("poc_of_cha") or raw.get("poc") or "").strip(),
            "poc_email": (raw.get("poc_email") or raw.get("emails_of_poc") or raw.get("email")
                          or raw.get("emails") or "").strip(),
            "is_active": (raw.get("is_active") or "true").strip(),
        }
        errors = []
        if not data["country"]:
            errors.append("Missing country")
        key = (data["country"].lower(), data["cha_name"].lower(), data["poc_email"].lower())
        dup_in_file = bool(data["country"]) and key in seen
        if data["country"]:
            seen.add(key)
        exists = bool(data["country"]) and VatChaEmailMaster.objects.filter(
            country__iexact=data["country"], cha_name__iexact=data["cha_name"],
            poc_email__iexact=data["poc_email"]).exists()
        if errors:
            status, message = "error", "; ".join(errors)
        elif dup_in_file or exists:
            status, message = "duplicate", ("Duplicate in file" if dup_in_file else "Already exists")
        else:
            status, message = "ok", ""
        preview.append({"row": idx, "status": status, "message": message, "data": data})
    return preview


def cha_upload_api(request):
    return _generic_upload(request, "cha", _build_cha_preview)


def cha_preview_api(request):
    return _generic_preview(request, "cha")


def cha_confirm_api(request):
    def creator(data):
        VatChaEmailMaster.objects.create(
            country=data["country"],
            company_name=data["company_name"] or None,
            vat_approval=_truthy(data["vat_approval"]),
            cha_name=data["cha_name"] or None,
            poc_name=data["poc_name"] or None,
            poc_email=data["poc_email"] or None,
            is_active=_truthy(data["is_active"]),
        )
        return True
    return _generic_confirm(request, "cha", creator)


# ═════════════════════════════════════════════════════════════════════════════
# GENERIC UPLOAD / PREVIEW / CONFIRM  (session-token pattern, like logistics)
# ═════════════════════════════════════════════════════════════════════════════
def _session_key(kind):
    return f"vat_{kind}_upload_preview"


def _generic_upload(request, kind, builder):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Upload must use POST"}, status=405)
    file_obj = request.FILES.get("file")
    if not file_obj:
        return JsonResponse({"success": False, "message": "No file uploaded"}, status=400)
    try:
        rows = _read_uploaded_rows(file_obj, file_obj.name)
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=400)
    except Exception:
        return JsonResponse({"success": False, "message": "Could not parse uploaded file"}, status=400)

    preview_rows = builder(rows)
    token = secrets.token_urlsafe(16)
    request.session[_session_key(kind)] = {
        "token": token, "rows": preview_rows, "created_at": timezone.now().isoformat(),
    }
    request.session.modified = True
    return JsonResponse({"success": True, "message": "File parsed", "token": token,
                         "rows": preview_rows, "count": len(preview_rows)})


def _generic_preview(request, kind):
    preview = request.session.get(_session_key(kind))
    if not preview:
        return JsonResponse({"success": False, "message": "No upload preview available"}, status=404)
    return JsonResponse({"success": True, "token": preview["token"],
                         "rows": preview["rows"], "count": len(preview["rows"])})


def _generic_confirm(request, kind, creator):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Confirm must use POST"}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON payload"}, status=400)

    token = payload.get("token")
    preview = request.session.get(_session_key(kind))
    if not preview or preview.get("token") != token:
        return JsonResponse({"success": False, "message": "Preview token not found or expired"}, status=400)

    created = 0
    for row in preview["rows"]:
        if row["status"] != "ok":
            continue
        if creator(row["data"]):
            created += 1

    request.session.pop(_session_key(kind), None)
    request.session.modified = True
    return JsonResponse({"success": True, "imported": created, "created": created,
                         "updated": 0, "skipped": len(preview["rows"]) - created,
                         "total": len(preview["rows"])})


# ═════════════════════════════════════════════════════════════════════════════
# INVOICE UPLOAD + PROCESS + OUTPUT
# ═════════════════════════════════════════════════════════════════════════════
def _invoice_dict(inv, detail=False):
    base = {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "country": inv.country,
        "invoice_currency": inv.invoice_currency,
        "local_currency": inv.local_currency,
        "taxable_amount": str(inv.taxable_amount) if inv.taxable_amount is not None else None,
        "charged_vat": str(inv.charged_vat) if inv.charged_vat is not None else None,
        "expected_vat": str(inv.expected_vat) if inv.expected_vat is not None else None,
        "variance": str(inv.variance) if inv.variance is not None else None,
        "stage": inv.stage,
        "recon_status": inv.recon_status,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
    }
    if detail:
        base.update({
            "language": inv.language,
            "local_entity": inv.local_entity,
            "cha_partner": inv.cha_partner,
            "exchange_rate": str(inv.exchange_rate),
            "converted_taxable": str(inv.converted_taxable) if inv.converted_taxable is not None else None,
            "rate_source": inv.rate_source,
            "rate_date": inv.rate_date.isoformat() if inv.rate_date else None,
            "ocr_json": inv.ocr_json or {},
            "total_amount": str(inv.total_amount) if inv.total_amount is not None else None,
            "tolerance_used": str(inv.tolerance_used),
            "raw_ocr_text": inv.raw_ocr_text,
            "translated_text": inv.translated_text,
            "ocr_provider": inv.ocr_provider,
            "process_error": inv.process_error,
            "documents": [
                {"id": d.id, "file_name": d.file_name, "url": d.file.url}
                for d in inv.documents.all()
            ],
            "lines": [
                {"hsn_sac_code": ln.hsn_sac_code, "taxable_amount": str(ln.taxable_amount),
                 "vat_percent": str(ln.vat_percent), "expected_vat": str(ln.expected_vat)}
                for ln in inv.calc_lines.all()
            ],
        })
    return base


@_vat_require('super', 'member')
@uses_components("data_table", "adv_filter", "modal", "master_page")
def invoice_list(request):
    return render(request, "vat_reconciliation/invoice_upload.html", {})


@_vat_require('super', 'member')
@uses_components("data_table", "modal", "master_page")
def invoice_detail_page(request, pk):
    get_object_or_404(VatInvoice, pk=pk)
    return render(request, "vat_reconciliation/invoice_detail.html", {"invoice_id": pk})


def invoice_list_api(request):
    search = request.GET.get("search", "").strip()
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 15))
    qs = VatInvoice.objects.all()
    if search:
        qs = qs.filter(
            Q(invoice_number__icontains=search) | Q(country__icontains=search) |
            Q(recon_status__icontains=search)
        )
    total = qs.count()
    start = (page - 1) * per_page
    rows = qs.order_by("-created_at")[start:start + per_page]
    data = [_invoice_dict(i) for i in rows]
    stats = {
        "total": total,
        "matched": qs.filter(recon_status="MATCHED").count(),
        "discrepancy": qs.filter(recon_status="DISCREPANCY").count(),
    }
    return JsonResponse({"success": True, "count": len(data), "total": total,
                         "page": page, "per_page": per_page, "data": data, "stats": stats})


def invoice_detail_api(request, id):
    try:
        inv = VatInvoice.objects.get(id=id)
    except VatInvoice.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    return JsonResponse({"success": True, "data": _invoice_dict(inv, detail=True)})


ALLOWED_EXT = (".pdf", ".jpg", ".jpeg", ".png")


def invoice_upload_api(request):
    """Create an invoice + attach one or more files. Does NOT run the pipeline."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Upload must use POST"}, status=405)

    invoice_number = (request.POST.get("invoice_number") or "").strip()
    invoice_date = (request.POST.get("invoice_date") or "").strip()
    # Language is auto-detected during processing — ignore any posted value.
    language = None
    files = request.FILES.getlist("files") or ([request.FILES["file"]] if "file" in request.FILES else [])

    errors = {}
    if not files:
        errors["files"] = ["At least one file is required"]
    for f in files:
        if not f.name.lower().endswith(ALLOWED_EXT):
            errors["files"] = [f"Unsupported file: {f.name}. Allowed: PDF, JPG, JPEG, PNG"]
            break
    if errors:
        return JsonResponse({"success": False, "message": "Validation failed", "errors": errors}, status=400)

    # Invoice number is detected from the document during processing; until then
    # use a unique placeholder so the record can be created from the file alone.
    if not invoice_number:
        invoice_number = "INV-" + timezone.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2).upper()

    inv = VatInvoice.objects.create(
        invoice_number=invoice_number,
        invoice_date=invoice_date or None,
        language=language,
    )
    for f in files:
        VatInvoiceDocument.objects.create(
            invoice=inv, file=f, file_name=f.name, mime_type=getattr(f, "content_type", None),
        )
    return JsonResponse({"success": True, "message": "Invoice uploaded", "id": inv.id,
                         "data": _invoice_dict(inv, detail=True)}, status=201)


def invoice_process_api(request, id):
    """Run the OCR -> translate -> convert -> calculate -> reconcile pipeline."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Process must use POST"}, status=405)
    try:
        inv = VatInvoice.objects.get(id=id)
    except VatInvoice.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    try:
        process_invoice(inv)
    except Exception as exc:  # noqa: BLE001 — surfaced from pipeline
        return JsonResponse({"success": False, "message": f"Processing failed: {exc}",
                             "data": _invoice_dict(inv, detail=True)}, status=500)
    return JsonResponse({"success": True, "message": "Invoice processed",
                         "data": _invoice_dict(inv, detail=True)})


def invoice_delete_api(request, id):
    if request.method != "DELETE":
        return JsonResponse({"success": False, "message": "Delete requires DELETE"}, status=405)
    try:
        inv = VatInvoice.objects.get(id=id)
    except VatInvoice.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    inv.soft_delete(request.user.username if request.user.is_authenticated else None)
    return JsonResponse({"success": True, "message": "Invoice deleted"})


def invoice_email_draft_api(request, id):
    try:
        inv = VatInvoice.objects.get(id=id)
    except VatInvoice.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    return JsonResponse({"success": True, "data": email_draft.build_draft(inv)})


def invoice_cha_contacts_api(request, id):
    """CHA contacts available for an invoice's country — feeds the recipient
    picker on the Send-to-CHA modal. One entry per individual email address."""
    try:
        inv = VatInvoice.objects.get(id=id)
    except VatInvoice.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    contacts = []
    if inv.country:
        for c in VatChaEmailMaster.objects.filter(
                country__iexact=inv.country.strip(), is_active=True).order_by("cha_name"):
            for em in _split_emails(c.poc_email):
                contacts.append({
                    "cha_name": c.cha_name or "",
                    "poc_name": c.poc_name or "",
                    "email": em,
                    "vat_approval": c.vat_approval,
                })
    return JsonResponse({"success": True, "country": inv.country, "contacts": contacts})


def invoice_send_cha_api(request, id):
    """E-mail the reviewed draft (with the invoice file attached) to the
    recipients the user selected on the Send-to-CHA modal."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Send requires POST"}, status=405)
    try:
        inv = VatInvoice.objects.get(id=id)
    except VatInvoice.DoesNotExist:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    recipients = [e.strip() for e in (data.get("recipients") or []) if e and "@" in e]
    subject = (data.get("subject") or "").strip() or f"VAT Reconciliation — Invoice {inv.invoice_number}"
    body = (data.get("body") or "").strip()
    if not recipients:
        return JsonResponse({"success": False, "message": "Select at least one recipient"}, status=400)
    if not body:
        return JsonResponse({"success": False, "message": "Email body is empty"}, status=400)

    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@merillife.com")
    try:
        msg = EmailMultiAlternatives(subject, body, from_email, recipients)
        for doc in inv.documents.all():
            try:
                doc.file.open("rb")
                msg.attach(doc.file_name, doc.file.read(), doc.mime_type or "application/octet-stream")
                doc.file.close()
            except Exception:  # noqa: BLE001 — a missing file shouldn't block the email
                pass
        msg.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 — surface SMTP errors to the UI
        return JsonResponse({"success": False, "message": f"Send failed: {exc}"}, status=500)

    return JsonResponse({"success": True,
                         "message": f"Email sent to {len(recipients)} recipient(s)",
                         "recipients": recipients})


# ─────────────────────────────────────────────────────────────────────────────
# APPLY VAT RBAC TO ALL API VIEWS
# (module-level reassignment so urls.py picks up the wrapped versions)
# ─────────────────────────────────────────────────────────────────────────────

_VAT_ADMIN_APIS = [
    # Dashboard APIs (both groups)
    # (listed in _VAT_SHARED_APIS below)
    # Country Master
    'country_list_api', 'country_create_api', 'country_update_api',
    'country_delete_api', 'country_upload_api', 'country_preview_api', 'country_confirm_api',
    # VAT Rate Master
    'rate_list_api', 'rate_create_api', 'rate_update_api',
    'rate_delete_api', 'rate_upload_api', 'rate_preview_api', 'rate_confirm_api',
    # CHA Email Master
    'cha_list_api', 'cha_create_api', 'cha_update_api',
    'cha_delete_api', 'cha_upload_api', 'cha_preview_api', 'cha_confirm_api',
]

_VAT_MEMBER_APIS = [
    'invoice_list_api', 'invoice_detail_api', 'invoice_upload_api',
    'invoice_process_api', 'invoice_delete_api',
    'invoice_email_draft_api', 'invoice_cha_contacts_api', 'invoice_send_cha_api',
]

_VAT_SHARED_APIS = [
    'dashboard_stats_api', 'dashboard_fx_api',
]

_g = globals()
for _name in _VAT_ADMIN_APIS:
    _g[_name] = _vat_api_require('super', 'admin')(_g[_name])
for _name in _VAT_MEMBER_APIS:
    _g[_name] = _vat_api_require('super', 'member')(_g[_name])
for _name in _VAT_SHARED_APIS:
    _g[_name] = _vat_api_require('super', 'admin', 'member')(_g[_name])
