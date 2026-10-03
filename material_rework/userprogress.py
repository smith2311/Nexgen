"""Per-user progress across the Material Rework workflow.

The report answered "what happened to the material" but never "who moved it".
Every figure on it was batch- or line-shaped, so there was no way to see what a
given person had actually done — which is what this builds: one row per user,
one column per kind of work, over the report's own date window.

**Where the numbers come from.** Every stage already stamps its actor onto the
row it touches, so this needs no new event table — it reads the workflow's own
records:

===========================  ==================================================
Uploads raised               ``DispositionBatch.uploaded_by_email``
Order Management reviews     ``DispositionBatch.om_reviewed_by_email``
PPIC reviews                 ``DispositionBatch.ppic_reviewed_by_email``
Production reviews           ``DispositionBatch.production_reviewed_by_email``
Lines released to production ``DispositionItem.rework_released_by_email``
Lines reworked and returned  ``DispositionItem.rework_returned_by_email``
Lines counted back in        ``DispositionItem.warehouse_received_by_email``
===========================  ==================================================

The three line-level columns are new. Those legs recorded *when* they happened
but not *who* did them, so the people doing the physical work had no
attributable activity anywhere — a progress report built before that was added
would have shown warehouse and production operators as idle while the
reviewers who only click Approve showed real numbers.

**Users with nothing are still listed.** "Holds a Material Rework role and has
done nothing this window" is the finding an admin is usually looking for, and a
row that is simply absent cannot be read as zero.

**Matching by email, lower-cased.** The workflow stores a denormalised address
rather than a foreign key so history survives an account being renamed or
deactivated; the cost is that joining back to users is a string match, and
addresses reach these columns from ``request.user.email`` and from imported
master data with inconsistent case.

Eight aggregate queries regardless of how much history exists — seven grouped
counts plus the roster — so this can sit on a page that already runs its own.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db.models import Count, Max, Q, Sum

from .models import DispositionBatch, DispositionItem

#: Groups whose members are expected to appear on the report even at zero.
ROLE_GROUPS = (
    'Material Rework Admin',
    'MR HOD',
    'MR Order Management',
    'MR PPIC',
    'MR Production',
    'MR Warehouse',
    'Material Rework User',
)

#: Short labels for the role chips, keyed by group name.
ROLE_LABELS = {
    'Material Rework Admin': 'Admin',
    'MR HOD': 'HoD',
    'MR Order Management': 'Order Mgmt',
    'MR PPIC': 'PPIC',
    'MR Production': 'Production',
    'MR Warehouse': 'Warehouse',
    'Material Rework User': 'User',
}

#: The activity columns, in the order the workflow runs them. Each is
#: ``(key, label, model, email field, timestamp field, quantity field)``.
#: Driving the queries from one table rather than seven hand-written blocks is
#: what keeps the screen, the totals and the Excel export from disagreeing.
ACTIVITY_COLUMNS = (
    ('uploads',        'Uploads',        DispositionBatch, 'uploaded_by_email',             'uploaded_at',            None),
    ('om_reviews',     'OM reviews',     DispositionBatch, 'om_reviewed_by_email',          'om_reviewed_at',         None),
    ('ppic_reviews',   'PPIC reviews',   DispositionBatch, 'ppic_reviewed_by_email',        'ppic_reviewed_at',       None),
    ('prod_reviews',   'Prod. reviews',  DispositionBatch, 'production_reviewed_by_email',  'production_reviewed_at', None),
    ('lines_released', 'Lines released', DispositionItem,  'rework_released_by_email',      'rework_released_at',     'rework_released_qty'),
    ('lines_returned', 'Lines returned', DispositionItem,  'rework_returned_by_email',      'rework_returned_at',     'reworked_qty'),
    ('lines_received', 'Lines received', DispositionItem,  'warehouse_received_by_email',    'warehouse_received_at',  'qty_received'),
)

#: Which quantity each column contributes to the "units moved" figure. Only
#: the physical legs count: a review decides, it does not move anything, and
#: adding an upload's quantity would triple-count the same units as they went
#: out and came back.
QTY_COLUMNS = ('lines_released', 'lines_returned', 'lines_received')


def _blank_row(email, name='', username='', roles=None, is_active=True):
    row = {
        'email': email,
        'name': name or email,
        'username': username,
        'roles': roles or [],
        'is_active': is_active,
        'total': 0,
        'qty_released': 0,
        'qty_reworked': 0,
        'qty_received': 0,
        'last_action': None,
        'has_role': bool(roles),
    }
    for key, _label, _model, _email_field, _ts, _qty in ACTIVITY_COLUMNS:
        row[key] = 0
    return row


def _window_filter(model, timestamp_field, date_from, date_to):
    """The report's own date window, applied to this column's timestamp.

    Each column is filtered on the timestamp of the act being counted, not on
    the batch's upload date: "PPIC reviews in September" has to mean reviews
    done in September, or a batch uploaded in August and reviewed in September
    would land in the wrong month and the columns would not add up to what an
    admin saw on the workflow screens.
    """
    conditions = Q()
    if date_from:
        conditions &= Q(**{f'{timestamp_field}__date__gte': date_from})
    if date_to:
        conditions &= Q(**{f'{timestamp_field}__date__lte': date_to})
    return conditions


def leaderboard(date_from=None, date_to=None, vertical_id=None, department_id=None):
    """One row per user, newest activity first, plus the column totals.

    Returns ``{'rows': [...], 'totals': {...}, 'columns': [(key, label), ...]}``.
    The filters mirror the report screen's, so the section agrees with the
    tables above it rather than quietly reporting a different period.
    """
    rows: dict[str, dict] = {}

    # The roster first, so a role holder with no activity still gets a row.
    for user in (
        User.objects.filter(groups__name__in=ROLE_GROUPS)
        .prefetch_related('groups')
        .distinct()
    ):
        email = (user.email or '').strip().lower()
        # A user with no email cannot be matched to any workflow record, so
        # they are keyed on username to stay visible rather than colliding
        # with every other blank-email account under the key "".
        key = email or f'username:{user.username}'
        rows[key] = _blank_row(
            email=user.email or '',
            name=user.get_full_name() or user.username,
            username=user.username,
            roles=[
                ROLE_LABELS.get(g.name, g.name)
                for g in user.groups.all() if g.name in ROLE_GROUPS
            ],
            is_active=user.is_active,
        )

    for key, _label, model, email_field, timestamp_field, qty_field in ACTIVITY_COLUMNS:
        queryset = model.objects.exclude(**{f'{email_field}': ''}).exclude(
            **{f'{email_field}__isnull': True},
        )
        # Only rows where the act actually happened. Without this an unreviewed
        # batch whose uploader is also a PPIC member would be counted as a
        # review that never took place.
        queryset = queryset.filter(**{f'{timestamp_field}__isnull': False})
        queryset = queryset.filter(_window_filter(model, timestamp_field, date_from, date_to))

        if vertical_id or department_id:
            prefix = '' if model is DispositionBatch else 'batch__'
            if vertical_id:
                queryset = queryset.filter(**{f'{prefix}vertical_id': vertical_id})
            if department_id:
                queryset = queryset.filter(**{f'{prefix}department_id': department_id})

        aggregates = {'n': Count('pk'), 'last': Max(timestamp_field)}
        if qty_field:
            aggregates['qty'] = Sum(qty_field)

        for record in queryset.values(email_field).annotate(**aggregates):
            email = (record[email_field] or '').strip().lower()
            if not email:
                continue
            row = rows.get(email)
            if row is None:
                # Activity from somebody who no longer holds a role, or never
                # did. Kept rather than dropped: the work happened, and a
                # report that hides it cannot be reconciled with the workflow
                # screens.
                row = rows[email] = _blank_row(email=record[email_field])
            # Accumulated, not assigned. GROUP BY is case-sensitive, so one
            # person whose address was stored as both "A@x.com" and "a@x.com"
            # arrives as two records for the same column — assigning would
            # keep only the last one and silently under-count them.
            row[key] += record['n']
            row['total'] += record['n']
            if record['last'] and (row['last_action'] is None or record['last'] > row['last_action']):
                row['last_action'] = record['last']
            if qty_field and record.get('qty'):
                if key == 'lines_released':
                    row['qty_released'] += record['qty']
                elif key == 'lines_returned':
                    row['qty_reworked'] += record['qty']
                elif key == 'lines_received':
                    row['qty_received'] += record['qty']

    # Counts in column order, so the template can render the row without
    # indexing a dict by a loop variable -- which Django's template language
    # cannot do, and which would otherwise need a custom filter whose only
    # job is to work around that.
    for row in rows.values():
        row['cells'] = [row[key] for key, *_rest in ACTIVITY_COLUMNS]

    ordered = sorted(
        rows.values(),
        # Busiest first, then anyone idle alphabetically — an admin scanning
        # for "who has done nothing" wants those grouped, not scattered.
        key=lambda r: (-r['total'], r['name'].lower()),
    )

    totals = {'users': len(ordered), 'active_users': sum(1 for r in ordered if r['total']), 'total': 0}
    for key, _label, _model, _email, _ts, _qty in ACTIVITY_COLUMNS:
        totals[key] = sum(r[key] for r in ordered)
        totals['total'] += totals[key]
    for field in ('qty_released', 'qty_reworked', 'qty_received'):
        totals[field] = sum(r[field] for r in ordered)
    totals['idle_users'] = totals['users'] - totals['active_users']
    totals['cells'] = [totals[key] for key, *_rest in ACTIVITY_COLUMNS]

    return {
        'rows': ordered,
        'totals': totals,
        'columns': [(key, label) for key, label, *_rest in ACTIVITY_COLUMNS],
    }


# ── Dashboard scopes ────────────────────────────────────────────────────────
# The report dashboard answers "what is happening" for the whole module. These
# two add "and who is doing it" without replacing that: the default view is
# untouched, and the scope selector switches between it, every user at once,
# and one person on their own.

SCOPE_OVERVIEW = "overview"
SCOPE_ALL_USERS = "all"
SCOPE_ONE_USER = "user"

SCOPE_CHOICES = (
    (SCOPE_OVERVIEW, "Overview (default)"),
    (SCOPE_ALL_USERS, "All users"),
    (SCOPE_ONE_USER, "Specific user"),
)


def resolve_scope(raw):
    """The requested scope, or the default for anything unrecognised.

    Falling back rather than erroring: this comes from a query string, and a
    stale bookmark or a hand-edited URL should show the dashboard, not a 400.
    """
    valid = {code for code, _label in SCOPE_CHOICES}
    return raw if raw in valid else SCOPE_OVERVIEW


def user_options():
    """Everyone selectable in the "specific user" picker.

    Role holders and anyone with recorded activity, because those are not the
    same set: work is attributed by the email stamped on the row, so somebody
    whose role was removed still has history worth looking at, and a role
    holder who has done nothing is exactly who an admin wants to find.
    """
    people = {}

    for user in (
        User.objects.filter(groups__name__in=ROLE_GROUPS)
        .prefetch_related("groups").distinct()
    ):
        email = (user.email or "").strip()
        key = email.lower() or f"username:{user.username}"
        people[key] = {
            "key": email or user.username,
            "email": email,
            "label": user.get_full_name() or user.username,
            "roles": [
                ROLE_LABELS.get(g.name, g.name)
                for g in user.groups.all() if g.name in ROLE_GROUPS
            ],
            "is_active": user.is_active,
        }

    for _key, _label, model, email_field, timestamp_field, _qty in ACTIVITY_COLUMNS:
        values = (
            model.objects.exclude(**{email_field: ""})
            .filter(**{f"{timestamp_field}__isnull": False})
            .values_list(email_field, flat=True).distinct()
        )
        for address in values:
            email = (address or "").strip()
            if not email:
                continue
            key = email.lower()
            if key not in people:
                people[key] = {
                    "key": email, "email": email, "label": email,
                    "roles": [], "is_active": True,
                }

    return sorted(people.values(), key=lambda p: p["label"].lower())


def _matching_email(queryset, email_field, email):
    """Case-insensitive match on a denormalised address.

    The workflow stores whatever ``request.user.email`` held, and imported
    master data disagrees on case — an exact match silently returns nothing
    for a user whose address was stored capitalised.
    """
    return queryset.filter(**{f"{email_field}__iexact": email})


def user_dashboard(email, date_from=None, date_to=None, vertical_id=None,
                   department_id=None, recent=12):
    """One person's own dashboard: their totals, their split, their last actions.

    Returns ``None`` for an email with no match at all, so the caller can say
    "nothing recorded for them" rather than rendering a dashboard of zeros
    that looks like a data fault.
    """
    email = (email or "").strip()
    if not email:
        return None

    board = leaderboard(
        date_from=date_from, date_to=date_to,
        vertical_id=vertical_id, department_id=department_id,
    )
    row = next(
        (r for r in board["rows"] if (r["email"] or "").lower() == email.lower()
         or r["username"] == email),
        None,
    )
    if row is None:
        return None

    # Their own actions, newest first, across every source. One query per
    # source rather than a union: the seven models share no table, and the
    # lists are then merged in Python the way the feed is elsewhere.
    events = []
    for key, label, model, email_field, timestamp_field, qty_field in ACTIVITY_COLUMNS:
        queryset = _matching_email(model.objects.all(), email_field, email)
        queryset = queryset.filter(**{f"{timestamp_field}__isnull": False})
        queryset = queryset.filter(
            _window_filter(model, timestamp_field, date_from, date_to),
        )
        prefix = "" if model is DispositionBatch else "batch__"
        if vertical_id:
            queryset = queryset.filter(**{f"{prefix}vertical_id": vertical_id})
        if department_id:
            queryset = queryset.filter(**{f"{prefix}department_id": department_id})

        fields = [timestamp_field, f"{prefix}reference_number", f"{prefix}department__name"]
        if qty_field:
            fields.append(qty_field)
        if model is not DispositionBatch:
            fields.append("material")
        else:
            fields.append("subject")

        for record in queryset.order_by(f"-{timestamp_field}")[:recent].values(*fields):
            events.append({
                "at": record[timestamp_field],
                "action": label,
                "kind": key,
                "reference": record[f"{prefix}reference_number"] or "—",
                "department": record[f"{prefix}department__name"] or "—",
                "detail": record.get("material") or record.get("subject") or "",
                "qty": record.get(qty_field) if qty_field else None,
            })

    events.sort(key=lambda e: e["at"], reverse=True)

    return {
        "row": row,
        "events": events[:recent],
        # Labels and values in column order, for the per-stage bar chart.
        "chart": {
            "labels": [label for _key, label in board["columns"]],
            "data": list(row["cells"]),
        },
        "totals": board["totals"],
    }
