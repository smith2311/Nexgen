"""
Migrate legacy SQL Server dumps into the Django (Postgres) tables.

Two source tables are supported:

  [dbo].[LogisticsUpload_Master]  ->  PincodeMaster        (Location Master)
  [dbo].[LogisticsPerformance]    ->  LogisticsPerformance

The dumps are SQL Server scripts (UTF-16), one row per INSERT statement, using
N'...' string literals, CAST(N'...' AS DateTime) for dates, NULL, and bare
numbers.  This command streams the file, parses each INSERT, maps the columns to
the Django model fields, and bulk-inserts the rows.

Original audit values are preserved: created_at / updated_at / created_by /
is_deleted / deleted_at are taken from the source (auto_now is disabled during
the load so the source timestamps survive).

Usage (inside the web container):

    # Location Master (from PINscript.sql)
    python manage.py migrate_legacy_sql --location-file /app/PINscript.sql

    # Logistics Performance (from LCscript (1).sql)
    python manage.py migrate_legacy_sql --lp-file "/app/LCscript (1).sql"

    # both at once
    python manage.py migrate_legacy_sql \
        --location-file /app/PINscript.sql \
        --lp-file "/app/LCscript (1).sql"

Helpful flags:
    --flush      delete existing rows in the target table(s) first
    --limit N    only import the first N rows (for a quick test)
    --dry-run    parse + map but do NOT write to the database
"""

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from logistics_performance.models import PincodeMaster, LogisticsPerformance


# ─────────────────────────────────────────────────────────────────────────────
# Low-level T-SQL INSERT parsing
# ─────────────────────────────────────────────────────────────────────────────
def _statement_complete(text):
    """A buffered INSERT is complete when every string literal is closed
    (even number of single quotes — escaped '' counts as two, so parity holds)
    and the statement ends with the closing paren of the VALUES tuple."""
    stripped = text.rstrip()
    return stripped.endswith(')') and stripped.count("'") % 2 == 0


def _split_columns(insert_text):
    """Return the lower-cased column names from the `(...)` before VALUES."""
    head = insert_text.split(' VALUES ', 1)[0]
    inner = head[head.index('(') + 1: head.rindex(')')]
    return [c.strip().strip('[]').strip().lower() for c in inner.split(',')]


def _tokenize_values(values_inner):
    """Tokenize the content inside the VALUES(...) parens into Python values.
    Strings -> str, NULL -> None, numbers -> str (converted later by mappers),
    CAST(N'...' AS DateTime) -> the inner string."""
    tokens = []
    s = values_inner
    n = len(s)
    i = 0
    while i < n:
        c = s[i]
        if c in ' \t\r\n,':
            i += 1
            continue
        # String literal:  N'...'  or  '...'
        if c == "'" or (c in 'Nn' and i + 1 < n and s[i + 1] == "'"):
            if c in 'Nn':
                i += 1                      # skip the N
            i += 1                          # skip opening quote
            buf = []
            while i < n:
                if s[i] == "'":
                    if i + 1 < n and s[i + 1] == "'":   # escaped ''
                        buf.append("'")
                        i += 2
                        continue
                    i += 1                  # closing quote
                    break
                buf.append(s[i])
                i += 1
            tokens.append(''.join(buf))
            continue
        # CAST( N'...' AS DateTime )  ->  take the inner string
        if s[i:i + 4].upper() == 'CAST':
            q = s.index("'", i)             # first quote belongs to N'...'
            j = q + 1
            buf = []
            while j < n:
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    j += 1
                    break
                buf.append(s[j])
                j += 1
            close = s.index(')', j)         # closing paren of CAST(...)
            i = close + 1
            tokens.append(''.join(buf))
            continue
        # NULL
        if s[i:i + 4].upper() == 'NULL':
            tokens.append(None)
            i += 4
            continue
        # Bare number / identifier — read until the next top-level comma
        buf = []
        while i < n and s[i] not in ',)':
            buf.append(s[i])
            i += 1
        tokens.append(''.join(buf).strip())
    return tokens


def parse_insert(insert_text):
    """Return {column_name: value, ...} for one INSERT statement, or None."""
    columns = _split_columns(insert_text)
    right = insert_text.split(' VALUES ', 1)[1]
    i0 = right.index('(')
    i1 = right.rindex(')')
    values = _tokenize_values(right[i0 + 1:i1])
    if len(values) != len(columns):
        return None
    return dict(zip(columns, values))


def iter_inserts(path, table):
    """Yield {col: value} dicts for every INSERT into the given table.
    Handles multi-line statements (e.g. remarks containing newlines)."""
    prefix = 'INSERT [dbo].[%s]' % table
    buf = ''
    with open(path, encoding='utf-16') as fh:
        for raw in fh:
            if buf:
                buf += raw
            elif raw.lstrip().startswith(prefix):
                buf = raw
            else:
                continue
            if _statement_complete(buf):
                row = parse_insert(buf.strip())
                buf = ''
                if row is not None:
                    yield row


# ─────────────────────────────────────────────────────────────────────────────
# Value converters
# ─────────────────────────────────────────────────────────────────────────────
_DATE_FORMATS = (
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y',
    '%d/%m/%Y', '%Y/%m/%d', '%d-%b-%Y', '%d-%b-%y', '%d.%m.%Y',
)


def s_(v, maxlen=None):
    """Non-null string (required CharFields)."""
    if v is None:
        return ''
    out = str(v).strip()
    return out[:maxlen] if maxlen else out


def sn_(v, maxlen=None):
    """Nullable string."""
    if v is None:
        return None
    out = str(v).strip()
    if not out:
        return None
    return out[:maxlen] if maxlen else out


def dec_(v):
    if v is None:
        return None
    out = str(v).strip().replace(',', '')
    if not out:
        return None
    try:
        return Decimal(out)
    except (InvalidOperation, ValueError):
        return None


def int_(v):
    if v is None:
        return None
    out = str(v).strip().replace(',', '')
    if not out:
        return None
    try:
        return int(float(out))
    except (ValueError, TypeError):
        return None


def date_(v):
    if v is None:
        return None
    out = str(v).strip()
    if not out:
        return None
    out = out.split('.')[0]            # drop fractional seconds
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(out, fmt).date()
        except ValueError:
            continue
    return None


def dttm_(v):
    """Timezone-aware datetime, or None."""
    if v is None:
        return None
    out = str(v).strip()
    if not out:
        return None
    base = out.split('.')[0]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            naive = dt.datetime.strptime(base, fmt)
            return timezone.make_aware(naive, timezone.get_current_timezone())
        except ValueError:
            continue
    d = date_(out)
    if d:
        return timezone.make_aware(
            dt.datetime(d.year, d.month, d.day), timezone.get_current_timezone()
        )
    return None


def bool_(v):
    if v is None:
        return False
    return str(v).strip() in ('1', 'True', 'true', 'TRUE')


def pincode_(v):
    """Pincode may arrive as int (282001) or float text (282001.0)."""
    if v is None:
        return ''
    out = str(v).strip()
    if out.endswith('.0'):
        out = out[:-2]
    return out[:10]


# ─────────────────────────────────────────────────────────────────────────────
# Disable auto_now so source timestamps survive bulk_create
# ─────────────────────────────────────────────────────────────────────────────
def _freeze_audit_dates(model):
    for fname in ('created_at', 'updated_at'):
        f = model._meta.get_field(fname)
        f.auto_now = False
        f.auto_now_add = False


# ─────────────────────────────────────────────────────────────────────────────
# Row -> model instance builders
# ─────────────────────────────────────────────────────────────────────────────
def build_pincode(r):
    """LogisticsUpload_Master row -> PincodeMaster instance."""
    now = timezone.now()
    created = dttm_(r.get('createddate')) or now
    updated = dttm_(r.get('modifieddate')) or created
    return PincodeMaster(
        name=s_(r.get('location'), 100),
        location=s_(r.get('location'), 100),
        city=sn_(r.get('city'), 100),
        district=sn_(r.get('district'), 100),
        state=sn_(r.get('statename'), 50),
        state_name=s_(r.get('statename'), 50),
        zone=sn_(r.get('zone'), 100),
        pincode=pincode_(r.get('pincode')),
        courier_code=sn_(r.get('couriercode'), 50),
        courier_name=s_(r.get('couriername'), 100),
        mode=sn_(r.get('mode'), 50),
        service_type=sn_(r.get('servicetype'), 100),
        tat=int_(r.get('tat')) or 0,
        rate=dec_(r.get('rate')) or Decimal('0'),
        min_weight=dec_(r.get('minweight')),
        oda_nonoda=sn_(r.get('oda_nonoda'), 50),
        is_active=True,
        created_by=sn_(r.get('creator'), 150),
        updated_by=sn_(r.get('creator'), 150),
        created_at=created,
        updated_at=updated,
        is_deleted=bool_(r.get('isdeleted')),
        deleted_by=sn_(r.get('deletedby'), 150),
        deleted_at=dttm_(r.get('deleteddate')),
    )


def build_lp(r):
    """LogisticsPerformance (SQL Server) row -> LogisticsPerformance instance."""
    now = timezone.now()
    created = dttm_(r.get('createddate')) or now
    updated = dttm_(r.get('modifieddate')) or created
    return LogisticsPerformance(
        division=sn_(r.get('division'), 20),
        location=sn_(r.get('location'), 100),
        plant_name=sn_(r.get('plantname'), 100),
        billing_doc=sn_(r.get('billingdoc'), 50),
        billing_date=date_(r.get('billingdate')),
        inv_value=dec_(r.get('invvalue')),
        doc_type=sn_(r.get('doctype'), 20),
        billed_qty=int_(r.get('billedqty')),
        shipto_party=sn_(r.get('shiptoparty'), 50),
        shipto_name=sn_(r.get('shiptoname'), 200),
        shipto_city=sn_(r.get('shiptocity'), 100),
        pincode=pincode_(r.get('pincode')),
        courier_no=sn_(r.get('currierno'), 50),
        courier_code=sn_(r.get('curriercode'), 50),
        courier_name=sn_(r.get('curriername'), 100),
        docket_no=sn_(r.get('docketno'), 100),
        mode=sn_(r.get('mode'), 50),
        boxes=int_(r.get('boxes')),
        ch_weight=dec_(r.get('chweight')),
        dispatch_date=date_(r.get('dispatchdate')),
        delivery_date=date_(r.get('deliverydate')),
        basic_logistics_cost=dec_(r.get('basiclc')),
        other_charges=dec_(r.get('othercharges')),
        remarks=sn_(r.get('remarks')),
        cost_center=sn_(r.get('costcenter'), 50),
        shipto_region=sn_(r.get('shiptoregion'), 100),
        zone=sn_(r.get('zone'), 100),
        creator=sn_(r.get('creator'), 100),
        tat_from_master=None,
        expected_delivery_date=date_(r.get('estimateddeliverydate')),
        dispatch_vs_edd_tat=int_(r.get('dispatchvseddtat')),
        dispatch_vs_delivery_tat=int_(r.get('dispatchvdeliverytat')),
        actual_logistics_cost=dec_(r.get('actuallctotal')),
        created_by=sn_(r.get('creator'), 150),
        updated_by=sn_(r.get('creator'), 150),
        created_at=created,
        updated_at=updated,
        is_deleted=bool_(r.get('isdeleted')),
        deleted_by=sn_(r.get('deletedby'), 150),
        deleted_at=dttm_(r.get('deleteddate')),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Command
# ─────────────────────────────────────────────────────────────────────────────
class Command(BaseCommand):
    help = "Import legacy SQL Server dumps into PincodeMaster / LogisticsPerformance."

    def add_arguments(self, parser):
        parser.add_argument('--location-file', dest='location_file',
                            help='Path to PINscript.sql (LogisticsUpload_Master)')
        parser.add_argument('--lp-file', dest='lp_file',
                            help='Path to LCscript (1).sql (LogisticsPerformance)')
        parser.add_argument('--flush', action='store_true',
                            help='Delete existing rows in target table(s) first')
        parser.add_argument('--limit', type=int, default=0,
                            help='Import only the first N rows (0 = all)')
        parser.add_argument('--batch', type=int, default=2000,
                            help='bulk_create batch size (default 2000)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse and map but do not write to the database')

    def handle(self, *args, **opts):
        if not opts['location_file'] and not opts['lp_file']:
            raise CommandError('Pass --location-file and/or --lp-file.')

        _freeze_audit_dates(PincodeMaster)
        _freeze_audit_dates(LogisticsPerformance)

        if opts['location_file']:
            self._load(
                label='Location Master (PincodeMaster)',
                path=opts['location_file'],
                table='LogisticsUpload_Master',
                model=PincodeMaster,
                builder=build_pincode,
                ignore_conflicts=True,   # skip active-duplicate unique violations
                opts=opts,
            )

        if opts['lp_file']:
            self._load(
                label='Logistics Performance',
                path=opts['lp_file'],
                table='LogisticsPerformance',
                model=LogisticsPerformance,
                builder=build_lp,
                ignore_conflicts=False,
                opts=opts,
            )

    def _load(self, label, path, table, model, builder, ignore_conflicts, opts):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== %s  <-  %s ===' % (label, table)))

        if opts['flush'] and not opts['dry_run']:
            deleted = model.all_objects.all().delete()[0]
            self.stdout.write('  flushed %s existing rows' % deleted)

        limit = opts['limit']
        batch_size = opts['batch']
        batch, total, parsed = [], 0, 0

        def flush_batch():
            nonlocal batch
            if not batch:
                return 0
            if not opts['dry_run']:
                model.objects.bulk_create(
                    batch, batch_size=batch_size,
                    ignore_conflicts=ignore_conflicts,
                )
            count = len(batch)
            batch = []
            return count

        for row in iter_inserts(path, table):
            parsed += 1
            try:
                batch.append(builder(row))
            except Exception as exc:   # noqa: BLE001 — never let one bad row abort
                self.stderr.write('  skip row %s: %s' % (parsed, exc))
                continue
            if len(batch) >= batch_size:
                total += flush_batch()
                self.stdout.write('  ...%s rows' % total, ending='\r')
            if limit and parsed >= limit:
                break

        total += flush_batch()

        verb = 'would import' if opts['dry_run'] else 'imported'
        self.stdout.write(self.style.SUCCESS(
            '\n  parsed %s, %s %s rows into %s' % (parsed, verb, total, model.__name__)))
