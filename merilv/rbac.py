"""Role-based access control for Meril-V.

Follows the platform convention: plain Django Groups plus predicate functions
that views wrap in ``user_passes_test``. Four tiers, because warehouse floor
staff, the supervisors who own a site, and the people who maintain master data
all have genuinely different needs:

``viewer``
    Read-only access to history and reports, for audit.
``picker``
    Scans against deliveries and runs validations. The day-to-day role.
    Sees only the plants of the manager they report to.
``manager``
    A picker's supervisor, mapped to one or more plants. Sees and operates on
    those plants, and on the work of the pickers reporting to them.
``admin``
    Everything, plus master data and entity configuration. Not plant-scoped.

Two axes, deliberately kept separate:

*What* a user may do is the tier (these group sets and the predicates below).
*Which rows* they may see is the plant scope (:func:`plant_scope`). Conflating
them is how a "read-only" flag ends up also deciding data visibility.
"""

ADMIN_GROUPS = {
    "Meril-V Admin",
    "Global SCM",
}

#: The supervisor tier. "Warehouse Manager" was historically an *admin* group;
#: it is a manager now, which is what the name says and what the client's
#: hierarchy asks for. Admin remains an explicit grant.
MANAGER_GROUPS = {
    "Meril-V Manager",
    "Warehouse Manager",
    "warehouse manager",
} | ADMIN_GROUPS

PICKER_GROUPS = {
    "Meril-V Picker",
    "Meril-V User",
    "Warehouse Picker",
    "PS Warehouse",
} | MANAGER_GROUPS

VIEWER_GROUPS = {
    "Meril-V Viewer",
    "Warehouse Supervisor",
} | PICKER_GROUPS

_MERIL_DOMAINS = ("@merillife.com", "@meril.com", "@meril.in", "@merillife.in")


def _is_meril_staff(user):
    """Meril employees flagged as staff get READ access without an explicit group.

    Read-only on purpose: this only feeds ``can_access_merilv``. It used to
    feed ``can_operate_merilv`` too, which meant any staff-flagged Meril
    account could scan and close deliveries — so a supervisor deliberately
    put in "Meril-V Viewer" still had full write access, and the viewer tier
    was effectively unreachable. Scanning now requires an explicit picker or
    admin group.
    """
    email = (user.email or "").lower()
    return user.is_staff and any(email.endswith(domain) for domain in _MERIL_DOMAINS)


def _in_groups(user, groups):
    return user.groups.filter(name__in=groups).exists()


def can_access_merilv(user):
    """Anyone who may open the module at all."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_meril_staff(user):
        return True
    return _in_groups(user, VIEWER_GROUPS)


def can_operate_merilv(user):
    """May scan, validate and record work — everything except configuration.

    Deliberately does NOT honour ``_is_meril_staff``: writing warehouse
    records is an explicit-grant action, so it needs a picker or admin group
    (see the note on ``_is_meril_staff``).
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _in_groups(user, PICKER_GROUPS)


def is_merilv_admin(user):
    """May edit master data, entity rules and box profiles."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _in_groups(user, ADMIN_GROUPS)


def is_merilv_manager(user):
    """A plant-owning supervisor. Admins qualify — a manager is a subset of them."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _in_groups(user, MANAGER_GROUPS)


def pair_scope(user):
    """The (plant, entity) pairs this user is bound to, or ``None``.

    ``UserScope`` is the joined mapping the 23 Sep MoM asks for: one row per
    real posting, so being allowed Ahmedabad-MHPL and Kolkata-MLSIPL does not
    quietly also allow Ahmedabad-MLSIPL. The two single-axis tables cannot
    express that — a list of plants times a list of entities is every
    combination — which is why this exists rather than a third column on one
    of them.

    ``None`` means "no pair mapping applies to this user", NOT "no access", and
    callers must not collapse the two. Returning ``None`` is what keeps every
    existing account working: the single-axis tables still decide for anybody
    who has no rows here, so this becomes a restriction one user at a time as
    the business team's sheet is loaded.
    """
    if not user.is_authenticated:
        return set()
    if user.is_superuser or _in_groups(user, ADMIN_GROUPS):
        return None
    pairs = set(user.merilv_scopes.values_list("plant_id", "entity__code"))
    return pairs or None


def may_work_in(user, plant, entity_code):
    """Whether this user may act on this plant AND this entity together.

    The question the pair mapping exists to answer. Falls back to the two
    independent bounds for a user with no pairs, so the answer is the same one
    the rest of the module would have given before ``UserScope`` existed.
    """
    pairs = pair_scope(user)
    if pairs is None:
        plants, entities = plant_scope(user), entity_scope(user)
        if plants is not None and plant not in plants:
            return False
        return entities is None or entity_code in entities
    return (plant, entity_code) in pairs


def plant_scope(user):
    """Plant codes this user may see, or ``None`` for unrestricted.

    ``None`` and ``set()`` mean different things and callers must not conflate
    them: ``None`` is "no restriction applies", an empty set is "restricted, and
    the restriction currently resolves to nothing". Use :func:`scope_queryset`
    rather than testing truthiness, which collapses the two.

    Resolution order:

    1. Admins and superusers are never scoped — they maintain the masters that
       define plants in the first place.
    2. A user with their own plant mapping is scoped to it. This is the manager
       case, and it also lets an admin scope an individual by hand.
    3. A picker with no mapping of their own inherits their manager's, which is
       the client's rule: "picker only see the data of mapped Manager".
    4. Anyone else is scoped to NOTHING.

    Step 4 used to return ``None`` — unrestricted — so that an installation
    where nobody was mapped behaved exactly as it had before, with scoping
    switching on per-user as mappings were created. That is the wrong way round
    for a data-isolation boundary and was the whole of the #22 leak: a Manager
    or Picker created through Roles & Access gets a *group* but no *mapping*,
    so every one of them resolved to "unrestricted" and saw every plant's data.
    The mapped case was always correct; only the default was wrong.

    Failing closed means an unmapped account now sees empty screens instead of
    everything, which is a support call rather than a breach — and
    :func:`is_unmapped` exists so the UI can explain it instead of just looking
    broken.
    """
    if not user.is_authenticated:
        return set()
    if user.is_superuser or _in_groups(user, ADMIN_GROUPS):
        return None

    # A pair mapping is the whole answer for this user — see pair_scope. Two
    # sources of truth for one boundary is how a gap opens.
    pairs = set(user.merilv_scopes.values_list("plant_id", flat=True))
    if pairs:
        return pairs

    own = set(user.merilv_plants.values_list("plant_id", flat=True))
    if own:
        return own

    link = getattr(user, "merilv_manager_link", None)
    if link is not None:
        return set(link.manager.merilv_plants.values_list("plant_id", flat=True))

    return set()


def entity_scope(user):
    """Entity codes this user may work in, or ``None`` for unrestricted.

    The other axis to :func:`plant_scope`. An entity is a business unit with
    its own masters and its own parsing rules; a plant is a physical site. A
    user can be scoped on either, both, or neither.

    Opt-in, unlike plants, and the asymmetry is decided here rather than at
    each call site:

    * admins and superusers are never scoped;
    * a user with entity mappings is scoped to them;
    * a user with none is unrestricted.

    The last line is the one worth arguing about. Plant scoping fails closed
    because its mappings already exist, so an unmapped account there is an
    oversight. No installation has a single ENTITY mapping yet, so failing
    closed would empty every picker's screen the moment this deploys — an
    outage, not a boundary. The client's rule is conditional: "if a picker or
    manager IS MAPPED to a particular plant or entity, he should only see that
    one." Opt-in is that rule, and it becomes a real restriction for each user
    the moment somebody maps them on Roles & Access.

    Flipping it later is one `return set()` here.
    """
    if not user.is_authenticated:
        return set()
    if user.is_superuser or _in_groups(user, ADMIN_GROUPS):
        return None
    pairs = set(user.merilv_scopes.values_list("entity__code", flat=True))
    if pairs:
        return pairs

    own = set(user.merilv_entities.values_list("entity__code", flat=True))
    return own or None


def scope_entities(queryset, user, field="code"):
    """Filter an ``Entity`` queryset to the ones this user may work in.

    ``field`` is the path from the model to the entity CODE — ``code`` on
    Entity itself, ``entity__code`` on anything that points at one.
    """
    scope = entity_scope(user)
    if scope is None:
        return queryset
    return queryset.filter(**{f"{field}__in": scope})


def scope_entities_if_present(queryset, user):
    """Apply the entity bound to ``queryset`` when its model has an entity.

    Most of the module's models point at one; a few — the masters, the org
    tables — do not, and for those this is a no-op rather than an error. Asking
    the model is what makes the bound automatic: see :func:`scope_visible`.
    """
    try:
        queryset.model._meta.get_field("entity")
    except Exception:
        return queryset
    return scope_entities(queryset, user, field="entity__code")


def entities_for(user):
    """The active entities this user may work in, as a queryset.

    Every entity picker and every "which entity is selected" lookup in the
    module goes through this. They used to each build
    ``Entity.objects.filter(is_active=True)`` for themselves — seven copies,
    two of them in rival ``_selected_entity`` helpers — which is how a list and
    the screen behind it end up disagreeing about what a user may open.
    """
    from .models import Entity

    return scope_entities(Entity.objects.filter(is_active=True), user)


def is_unmapped(user):
    """True for a non-admin who has no plant mapping and no manager link.

    Such an account is now scoped to nothing (see :func:`plant_scope`), so its
    screens come up empty. That is the correct security answer and a terrible
    user-facing one on its own, so the module shows a banner explaining what to
    ask for. Without it, failing closed reads as "the module is broken" — which
    is how the previous pass at this got reported.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser or _in_groups(user, ADMIN_GROUPS):
        return False
    # A pair mapping is a mapping. Without this, somebody loaded from the
    # business team's sheet would be correctly scoped AND told they have no
    # plant assigned, which is the banner contradicting the screen behind it.
    if user.merilv_scopes.exists():
        return False
    if user.merilv_plants.exists():
        return False
    return getattr(user, "merilv_manager_link", None) is None


def scope_queryset(queryset, user, field="plant"):
    """Filter ``queryset`` to the user's plant scope.

    ``field`` names the plant column on the model, which differs across the
    module — ``DeliveryLine.plant`` and ``ValidationRow.system_plant`` are both
    plain codes, not FKs. Pass ``None`` scope through untouched so an
    unrestricted user pays no query cost.
    """
    scope = plant_scope(user)
    if scope is None:
        return queryset
    return queryset.filter(**{f"{field}__in": scope})


def team_scope(user):
    """User ids whose work this user may see, or ``None`` for unrestricted.

    The plant axis cannot cover everything: ``FinishedDelivery`` records who
    picked a delivery but not which plant it left, so "picker only see the data
    of mapped Manager" has to be answered by team membership there.

    A manager sees their own work and their pickers'. A picker sees their own
    and their manager's team — siblings included, because a delivery is
    routinely finished by whoever is on shift and hiding a teammate's row would
    make a picker's own history look incomplete.

    Mirrors :func:`plant_scope`: ``None`` means unrestricted, and only an admin
    or superuser gets it.

    A user with no team and no manager resolves to their OWN id, not to
    ``None`` (which was the #22 leak — see :func:`plant_scope`) and not to an
    empty set either. An empty set would lock a picker out of the sessions they
    created themselves, which is a worse failure than the one being fixed:
    secure but unusable. Own work only is both closed and workable.
    """
    if not user.is_authenticated:
        return set()
    if user.is_superuser or _in_groups(user, ADMIN_GROUPS):
        return None

    team = set(user.merilv_team.values_list("picker_id", flat=True))
    if team:
        # A manager: themselves plus their pickers.
        return team | {user.pk}

    link = getattr(user, "merilv_manager_link", None)
    if link is not None:
        siblings = set(link.manager.merilv_team.values_list("picker_id", flat=True))
        return siblings | {link.manager_id, user.pk}

    return {user.pk}


def site_users(user):
    """Everyone who works the plants this user is scoped to, or ``None``.

    The team axis answers "who reports to whom". This answers "who works
    here" — which is a different question and the one a warehouse actually
    runs on: a dump is picked by whoever is on shift, a supervisor covers for
    an absent one, and a delivery is finished by the person still at the bench
    when the van arrives.

    Built from the user graph rather than from any record, because most of the
    module's models carry no plant of their own. A user is at this user's sites
    if they are mapped to one of the plants, or if they are a picker inheriting
    from somebody who is (:func:`plant_scope`, steps 2 and 3, read backwards).

    Admins are in the set of anyone who works a site. They are not mapped to a
    plant — they maintain the masters that define plants — so nothing else
    would put them there, and a dump an admin uploads for the floor to pick
    has to reach the floor. Where the model carries a plant of its own,
    :func:`scope_visible` bounds that by the record's plant too; where it does
    not, an admin's upload genuinely does reach every site, because there is
    nothing on the record that says which site it is for.

    An account with NO plant scope is a member of no site and gets nothing:
    its own work and no more. :func:`plant_scope` fails closed for an unmapped
    user and this has to fail closed with it, or "an admin uploaded it" would
    be a way around the mapping — which is the #22 leak wearing a new hat.
    """
    if not user.is_authenticated:
        return set()
    plants = plant_scope(user)
    if plants is None:
        return None
    if not plants:
        return {user.pk}

    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from .models import PickerManager, UserPlantMapping

    mapped = set(
        UserPlantMapping.objects.filter(plant_id__in=plants).values_list("user_id", flat=True)
    )
    inherited = set(
        PickerManager.objects.filter(manager_id__in=mapped).values_list("picker_id", flat=True)
    )
    supervisors = set(
        get_user_model()
        ._default_manager.filter(Q(is_superuser=True) | Q(groups__name__in=ADMIN_GROUPS))
        .values_list("pk", flat=True)
    )
    return mapped | inherited | supervisors | {user.pk}


def supervisor_ids(candidates):
    """Which of ``candidates`` are managers or admins.

    Kept as its own query rather than folded into a join: the answer is needed
    as a *set* by :func:`work_scope`, and a subquery there would put a groups
    join on every scoped queryset in the module.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    return set(
        get_user_model()
        ._default_manager.filter(pk__in=candidates)
        .filter(Q(is_superuser=True) | Q(groups__name__in=MANAGER_GROUPS))
        .values_list("pk", flat=True)
    )


def work_scope(user):
    """User ids whose records this user may open, or ``None`` for unrestricted.

    Three grants, added together. Each is a different reason to be allowed in,
    and writing them as one set is what lets every module ask the same question
    of a screen that has nothing else in common.

    1. **My team** — :func:`team_scope`, unchanged. My own work, my
       shift-mates', my manager's.
    2. **The supervisors of my sites** — a manager or an admin who works a
       plant I work. What they upload is a work queue, not personal work: an
       admin who uploads the day's YVL06 and a picker who cannot open it is a
       shift that cannot start.
    3. **Everyone at my sites, if I am a supervisor.** A manager owns a site
       rather than a team within it. Shift cover, an absent picker, a delivery
       to be finished by whoever is still at the bench — all of it is work at
       their plant, and a plant they are mapped to is exactly what the mapping
       says they are responsible for.

    Grants 2 and 3 are bounded by the plants, so neither reaches another site.
    Grant 1 is not, deliberately: a record with no plant on it — a YVL06 with
    no plant column leaves every line blank — must not cost somebody their own
    work.
    """
    if not user.is_authenticated:
        return set()
    team = team_scope(user)
    if team is None:
        return None  # admin or superuser

    here = site_users(user)
    if here is None:  # unreachable: plant_scope and team_scope agree on admins
        return None
    if is_merilv_manager(user):
        return team | here
    return team | supervisor_ids(here)


def scope_visible(queryset, user, field="picker", plant_path=None, entity_scoped=True):
    """Filter ``queryset`` to the records this user may see.

    ``field`` names the user column, which differs per model — ``operator`` on
    an outbound session, ``created_by`` on most uploads, ``picker`` on a
    finished delivery.

    ``plant_path`` is the query path from this model to a plant code, where it
    has one — ``plant`` on a finished delivery, ``dump__lines__plant`` on an
    outbound session, ``rows__system_plant`` on a validation run. Passing it
    bounds the *shared* grants (:func:`work_scope`, 2 and 3) by where the work
    physically is, so "an admin uploaded it" and "a manager is asking" both
    stop at the site. Most of the module's models carry no plant at all, and
    for those the user graph is the whole of the boundary.

    ``NULL`` rows are kept: an unassigned record is not another team's data,
    and dropping it would silently hide history that predates user tracking.

    The ENTITY bound is applied first and is not optional. Any model carrying
    an ``entity`` column is a business unit's record, and a user mapped to one
    unit has no business seeing another's whatever the user graph says. It is
    detected from the model rather than passed in, because ``plant_path`` is
    passed in and the docstring above already explains, twice, how a list and
    a detail view disagreeing about it produced a row that could be seen and
    not opened. A bound nobody can forget to pass cannot drift that way.

    ``entity_scoped=False`` opts a queryset out, for a caller deliberately
    reporting across units.
    """
    if entity_scoped:
        queryset = scope_entities_if_present(queryset, user)
    scope = work_scope(user)
    if scope is None:
        return queryset
    from django.db.models import Q

    visible = Q(**{f"{field}__in": scope}) | Q(**{f"{field}__isnull": True})
    plants = plant_scope(user)
    if plant_path and plants is not None:
        # The shared half only. A record whose owner is in the user's TEAM
        # stays visible whatever plant it names, including none at all.
        team = team_scope(user) or set()
        # Matched through a subquery rather than a join. plant_path usually
        # crosses a to-many relation, and filtering across one here joined the
        # child table into the caller's own query: the row came back once per
        # matching child, which then had to be collapsed again with distinct().
        # That cost is invisible until the caller has to-many joins of its own.
        # The outbound landing annotates two (dump__lines and scans) and the
        # three multiplied out to 13M intermediate rows — a DISTINCT sort of
        # ~29GB, which exceeded the free disk and took the page down with
        # "No space left on device" for every manager.
        #
        # The subquery keeps that join inside itself and dedupes in the IN, so
        # the caller's query gains no join, needs no distinct(), and cannot
        # hand get_object_or_404 a duplicate to raise MultipleObjectsReturned
        # over.
        at_plant = Q(pk__in=(
            queryset.model._default_manager
            .filter(**{f"{plant_path}__in": plants})
            .values("pk")
        ))
        visible = (
            Q(**{f"{field}__in": team})
            | Q(**{f"{field}__isnull": True})
            | (visible & at_plant)
        )
    return queryset.filter(visible)


def scope_queryset_by_team(queryset, user, field="picker", plant_path=None, entity_scoped=True):
    """Historical name for :func:`scope_visible`.

    Kept because it is the name sixty-odd call sites use and the meaning is the
    same question — "whose work may this user see" — only answered by a wider
    rule now that a supervisor's upload is work for the floor.
    """
    return scope_visible(
        queryset, user, field=field, plant_path=plant_path, entity_scoped=entity_scoped,
    )


def get_scoped_or_404(model_or_queryset, user, field="created_by", plant_path=None, **lookups):
    """Fetch one row, but only from within the user's team scope.

    The counterpart to :func:`scope_queryset_by_team` for detail and mutation
    views. Scoping only the list screens is not a boundary — it just hides the
    link. Anything reachable by typing a different number into the URL has to
    re-check, or a picker on one team can open, scan into, finish and export
    another team's session by guessing a pk.

    Deliberately raises ``Http404`` rather than ``PermissionDenied``: a 403 on
    a row that exists and a 404 on one that doesn't lets an outsider map which
    ids are real. Out of scope and non-existent look identical from outside.

    ``field`` names the user column, which differs per model — ``operator`` on
    an outbound session, ``created_by`` on most uploads, ``received_by`` on an
    inbound receipt. Unrestricted users (admins, and anyone on an installation
    with no mappings yet) pass straight through, same as the list helpers.

    ``plant_path`` is passed through to :func:`scope_visible` and means the
    same there: where the model carries a plant, the shared grants stop at the
    site. It must match whatever the module's LIST screen passes, or the two
    disagree and a row is listed that answers 404 when it is clicked — which is
    exactly how the outbound console shipped a supervisor's dump that a picker
    could see and could not open.
    """
    from django.http import Http404
    from django.shortcuts import get_object_or_404

    queryset = model_or_queryset if hasattr(model_or_queryset, "filter") else model_or_queryset._default_manager.all()
    try:
        return get_object_or_404(
            scope_visible(queryset, user, field=field, plant_path=plant_path), **lookups
        )
    except (TypeError, ValueError) as exc:
        # A malformed id — ``pk="abc"`` from a hand-edited form post — makes
        # Django's IntegerField raise rather than simply not match, which would
        # surface as a 500. "Not a valid id" and "no such row" mean the same
        # thing to the caller, so both answer 404.
        raise Http404(f"Malformed lookup: {exc}") from exc


__all__ = [
    "ADMIN_GROUPS",
    "MANAGER_GROUPS",
    "PICKER_GROUPS",
    "VIEWER_GROUPS",
    "can_access_merilv",
    "can_operate_merilv",
    "entities_for",
    "entity_scope",
    "scope_entities_if_present",
    "get_scoped_or_404",
    "is_merilv_admin",
    "is_merilv_manager",
    "is_unmapped",
    "plant_scope",
    "scope_entities",
    "scope_queryset",
    "scope_queryset_by_team",
    "scope_visible",
    "site_users",
    "team_scope",
    "work_scope",
]
