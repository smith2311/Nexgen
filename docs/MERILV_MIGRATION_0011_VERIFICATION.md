# Verifying `merilv.0011` against a restored database copy

`merilv/migrations/0011_focus_belongs_to_the_operator.py` **drops five columns**.
Dropping a column is not reversible by rolling the migration back — the data is
gone with it — so this one wants a rehearsal on a restored copy before it
reaches production.

This document is the rehearsal. **Do not run any of it against production.**

---

## 1. What the five columns are

| Table | Column | Type |
|---|---|---|
| `merilv_outboundsession` | `focus_delivery_no` | `varchar(50) NOT NULL` |
| `merilv_outboundsession` | `focus_customer_code` | `varchar(50) NOT NULL` |
| `merilv_zva05dump` | `focus_key` | `varchar(200) NOT NULL` |
| `merilv_btscansession` | `focus_key` | `varchar(80) NOT NULL` |
| `merilv_branchtransferreceipt` | `focus_key` | `varchar(80) NOT NULL` |

They held the scan target — "which delivery is being picked in this record". That
is a per-operator cursor, and holding it on the shared record is the bug 0011
fixes: two pickers on one session shared one focus, so one of them scanned into
the delivery the other had chosen. It now lives on `OperatorFocus`, keyed on
(record, user). See `merilv/focus.py`.

**What is lost:** the last focus each *record* carried. It is transient UI state,
not business data — no dispatch, scan, delivery or audit row references it.

**Do not confuse these with `Zva05Line.focus_key`**, which is a derived property
on the *line* (`"{customer} - {sales_document}"`) and is untouched. The same
name also appears as a dataclass field in `merilv/services/transfers.py`, as a
key in JSON state payloads, and as a `request.POST` parameter name. None of those
are the dropped columns.

---

## 2. What has already been verified, and what has not

Verified on real PostgreSQL, in transactions that were rolled back:

* the full chain `0001 → 0012` applies cleanly and creates 54 `merilv_*` tables;
* all five columns are gone afterwards and `merilv_operatorfocus` exists;
* `mv_serial_edit` is created and granted to Picker, Manager and Admin;
* **with the columns populated**, the drop preserves every row, leaves unrelated
  columns in place and leaves row payloads intact.

Not verified, and the reason this document exists:

* **production volume.** `ALTER TABLE … DROP COLUMN` takes an `ACCESS EXCLUSIVE`
  lock. On PostgreSQL the drop itself is a catalog update and does not rewrite
  the table, so it is fast — but it still has to *acquire* that lock, and it will
  queue behind any open transaction touching the table.
* **production schema drift.** The rehearsal ran against the schema these
  migrations build, not against whatever production has actually accumulated.
* **the partially-migrated state.** Production has historically sat mid-squash;
  `manage.py heal_squashed_migrations` exists for exactly that and runs ahead of
  `migrate` in `docker-compose.yml`.

---

## 3. Procedure

### Step 1 — restore a copy, never production

```bash
createdb -h "$DB_HOST" -U postgres nexgen_staging
pg_restore -h "$DB_HOST" -U postgres -d nexgen_staging --no-owner --role=nexgen_user \
    /path/to/production_snapshot.dump
```

Point the app at the copy for the whole of this procedure:

```bash
export POSTGRES_DB=nexgen_staging
```

Confirm you are not on production before continuing:

```bash
python manage.py shell -c "from django.conf import settings; print(settings.DATABASES['default']['NAME'])"
```

### Step 2 — confirm the merilv tables are there

```bash
psql -h "$DB_HOST" -U nexgen_user -d nexgen_staging -c "
  SELECT table_name FROM information_schema.tables
  WHERE table_schema='public' AND table_name LIKE 'merilv_%' ORDER BY 1;"
```

If this returns nothing, the snapshot predates Meril-V and proves nothing about
the drop. Get a snapshot that has the tables.

### Step 3 & 4 — record what the columns hold, before touching anything

```bash
psql -h "$DB_HOST" -U nexgen_user -d nexgen_staging -c "
  SELECT 'outboundsession'      AS tbl, count(*) AS rows,
         count(*) FILTER (WHERE focus_delivery_no   <> '') AS focus_delivery,
         count(*) FILTER (WHERE focus_customer_code <> '') AS focus_customer
    FROM merilv_outboundsession
  UNION ALL SELECT 'zva05dump', count(*),
         count(*) FILTER (WHERE focus_key <> ''), NULL FROM merilv_zva05dump
  UNION ALL SELECT 'btscansession', count(*),
         count(*) FILTER (WHERE focus_key <> ''), NULL FROM merilv_btscansession
  UNION ALL SELECT 'branchtransferreceipt', count(*),
         count(*) FILTER (WHERE focus_key <> ''), NULL FROM merilv_branchtransferreceipt;"
```

Keep this output. Step 6 compares against it.

Two of these counts are the ones that matter. The row counts must not change.
The focus counts are what you are consciously discarding — if they are high, and
pickers are mid-shift, they will each have to re-select their delivery once after
the deploy. That is the whole user-visible cost.

### Step 5 — migrate

```bash
python manage.py heal_squashed_migrations    # no-op unless mid-squash
python manage.py showmigrations merilv       # record the before state
python manage.py migrate merilv
```

### Step 6 — verify

Row counts unchanged, and the columns gone:

```bash
psql -h "$DB_HOST" -U nexgen_user -d nexgen_staging -c "
  SELECT 'outboundsession' AS tbl, count(*) FROM merilv_outboundsession
  UNION ALL SELECT 'zva05dump', count(*) FROM merilv_zva05dump
  UNION ALL SELECT 'btscansession', count(*) FROM merilv_btscansession
  UNION ALL SELECT 'branchtransferreceipt', count(*) FROM merilv_branchtransferreceipt;"

psql -h "$DB_HOST" -U nexgen_user -d nexgen_staging -c "
  SELECT table_name, column_name FROM information_schema.columns
  WHERE table_schema='public'
    AND (column_name IN ('focus_delivery_no','focus_customer_code')
         OR (column_name='focus_key' AND table_name IN
             ('merilv_zva05dump','merilv_btscansession','merilv_branchtransferreceipt')));"
```

The second query must return **zero rows**. The first must match step 4 exactly.

Unrelated columns survived, the new table and grant exist:

```bash
psql -h "$DB_HOST" -U nexgen_user -d nexgen_staging -c "
  SELECT column_name FROM information_schema.columns
  WHERE table_name='merilv_outboundsession' ORDER BY 1;"

psql -h "$DB_HOST" -U nexgen_user -d nexgen_staging -c "
  SELECT count(*) AS operatorfocus_table FROM information_schema.tables
  WHERE table_name='merilv_operatorfocus';
  SELECT g.name FROM auth_group g
    JOIN auth_group_permissions gp ON gp.group_id=g.id
    JOIN auth_permission p ON p.id=gp.permission_id
   WHERE p.codename='mv_serial_edit' ORDER BY 1;"
```

`mv_serial_edit` must be granted to `Meril-V Picker`, `Meril-V Manager` and
`Meril-V Admin`. If a picker is missing it they cannot correct a mistyped serial,
which is a mid-shift outage.

The models still load, and focus reads clean rather than raising:

```bash
python manage.py shell -c "
from merilv.models import OutboundSession, Zva05Dump, BtScanSession, BranchTransferReceipt
from merilv import focus
for m in (OutboundSession, Zva05Dump, BtScanSession, BranchTransferReceipt):
    print(m.__name__, m.objects.count())
s = OutboundSession.objects.first()
print('focus of first session:', focus.by_user(s) if s else 'no sessions')
"
```

### Step 7 — run the suite against the migrated copy

```bash
POSTGRES_DB=nexgen_staging pytest merilv/ -q
```

Requires `CREATEDB` on the role — see §4.

### Step 8 — migration state

```bash
python manage.py showmigrations merilv
python manage.py migrate --check      # exits non-zero if anything is unapplied
```

`0011_focus_belongs_to_the_operator` and `0012_grant_serial_correction` should
both show `[X]`.

### Step 9 — tear the copy down

```bash
dropdb -h "$DB_HOST" -U postgres nexgen_staging
```

---

## 4. The `CREATEDB` prerequisite

`pytest` cannot run at all without it. `DATABASES['default']` has no `TEST`
block, so Django creates and drops `test_nexgen` on every run
(`TEST.MIGRATE=True`, `TEST.NAME=None`), and `pytest.ini` sets no `--reuse-db`.
The role currently cannot:

```
role: nexgen_user  CREATEDB=False  SUPERUSER=False
```

An authorised PostgreSQL administrator runs, once:

```sql
ALTER ROLE nexgen_user CREATEDB;
```

This grants only the right to create databases. It is not superuser, and it does
not widen access to any existing database. To reverse it:

```sql
ALTER ROLE nexgen_user NOCREATEDB;
```

---

## 5. If it goes wrong

`0011` is **not** reversible in any useful sense. `migrate merilv 0010` will
re-add the five columns, but empty — the values are gone. Recovery is to restore
the snapshot again. That is the reason for rehearsing on a copy.

`0012` is safely reversible: its `RunPython` reverse is deliberately a no-op, so
rolling back drops the model option and leaves the grants in the database.
