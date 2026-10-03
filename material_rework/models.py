import uuid
from django.db import models


class RequestedByOption(models.Model):
    """Master table for 'Requested By' dropdown options, managed by admins."""
    name       = models.CharField(max_length=100, unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_rework_requestedbyoption'
        ordering = ['name']
        verbose_name        = 'Requested By Option'
        verbose_name_plural = 'Requested By Options'

    def __str__(self):
        return self.name


class Country(models.Model):
    """Master list of valid country names, managed by admins.

    Exists so an uploaded Excel row can be checked against a known spelling
    rather than accepting whatever was typed. Matching is case-insensitive and
    the master's own spelling is what gets stored, so "india"/"INDIA" both
    normalise to the master row's "India" instead of being rejected — only a
    genuinely unknown name is an error.

    Deliberately safe when empty: an installation that has not populated this
    master yet skips country validation entirely rather than blocking every
    upload (see ``_validate_country`` in views.py).
    """
    name       = models.CharField(max_length=100, unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_rework_country'
        ordering = ['name']
        verbose_name        = 'Country'
        verbose_name_plural = 'Countries'

    def __str__(self):
        return self.name


class ReworkType(models.Model):
    """Master table for the 'Type of Rework' dropdown on the upload form."""
    name       = models.CharField(max_length=100, unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_rework_reworktype'
        ordering = ['name']
        verbose_name        = 'Type of Rework'
        verbose_name_plural = 'Types of Rework'

    def __str__(self):
        return self.name


class ReworkLocation(models.Model):
    """Master table for the 'Location' dropdown on the upload form.

    Named ``ReworkLocation`` rather than ``Location`` because the item rows
    already carry ``from_storage_location``/``to_storage_location``, which are
    free-text SAP storage codes and a different thing entirely — this is the
    physical site the rework happens at.
    """
    name       = models.CharField(max_length=150, unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_rework_reworklocation'
        ordering = ['name']
        verbose_name        = 'Rework Location'
        verbose_name_plural = 'Rework Locations'

    def __str__(self):
        return self.name


class Company(models.Model):
    cid          = models.IntegerField(unique=True, verbose_name='CId')
    company_name = models.CharField(max_length=300, verbose_name='Company Name')
    acronym      = models.CharField(max_length=50, verbose_name='Acronym')
    country      = models.IntegerField(null=True, blank=True, verbose_name='Country Code')
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'material_rework_company'
        ordering = ['company_name']
        verbose_name = 'Company'
        verbose_name_plural = 'Companies'

    def __str__(self):
        return f"{self.company_name} ({self.acronym})"


class Vertical(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_rework_vertical'
        ordering = ['name']

    def __str__(self):
        return self.name


class Department(models.Model):
    name            = models.CharField(max_length=100)
    vertical        = models.ForeignKey(Vertical, on_delete=models.CASCADE, related_name='departments')
    hod_email               = models.TextField(help_text='HOD receives approval emails. Separate multiple with commas.')
    warehouse_email         = models.TextField(blank=True, help_text='Warehouse team receives pending-qty alerts. Separate multiple with commas.')
    production_review_email = models.TextField(blank=True, help_text='Notified when production review completes. Separate multiple with commas.')
    ppic_email              = models.TextField(blank=True, help_text='PPIC team receives OM batch review requests. Separate multiple with commas.')
    om_email                = models.TextField(blank=True, help_text='Order Management receives batch review requests when a flow routes through OM. Separate multiple with commas.')
    is_active       = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_rework_department'
        unique_together = ('name', 'vertical')
        ordering = ['vertical__name', 'name']

    def __str__(self):
        return f"{self.name} — {self.vertical.name}"


class DispositionBatch(models.Model):
    STATUS_CHOICES = [
        # ── Stages a configurable flow can put a batch in ────────────────
        # One "pending_<stage>" per routable stage, so the status says what
        # the batch is waiting for. The older ppic_approved / hod_approved
        # said who had just approved instead, which stops being answerable
        # once the chain is data: "approved" by whom, out of four possible
        # predecessors? Those two are kept below for display only.
        ('pending_om',       'Pending Order Management'),
        ('om_approved',      'Order Management Approved'),
        ('om_rejected',      'Order Management Rejected'),
        ('pending_ppic',    'Pending PPIC'),
        ('ppic_approved',   'PPIC Approved'),
        ('ppic_rejected',   'PPIC Rejected'),
        ('pending_production',        'Pending Production Review'),
        ('pending_warehouse_release', 'Pending Warehouse Release'),
        ('production_done', 'Production Review Done'),
        ('completed',       'Completed'),
        # ── Retired ──────────────────────────────────────────────────────
        # HOD is no longer part of any flow and nothing routes here. Kept as
        # choices so any historical row still renders a label rather than a
        # raw slug, and so the dormant review screen keeps working.
        ('pending_hod',     'Pending HOD Approval'),
        ('hod_approved',    'HOD Approved'),
        ('hod_rejected',    'HOD Rejected'),
    ]

    SOURCE_CHOICES = [
        ('warehouse',         'Warehouse'),
        ('order_management',  'Order Management'),
        ('production',        'Production'),
    ]

    #: Stored as an integer, not a slug, so ``order_by('priority')`` alone puts
    #: the most urgent rework first — no CASE expression and no risk of a list
    #: sorting alphabetically ("High, Low, Medium") and quietly lying about
    #: which batch production should pick up next.
    PRIORITY_HIGH   = 1
    PRIORITY_MEDIUM = 2
    PRIORITY_LOW    = 3
    PRIORITY_CHOICES = [
        (PRIORITY_HIGH,   'High'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_LOW,    'Low'),
    ]

    company           = models.ForeignKey(Company, on_delete=models.PROTECT, null=True, blank=True)
    vertical          = models.ForeignKey(Vertical, on_delete=models.PROTECT)
    department        = models.ForeignKey(Department, on_delete=models.PROTECT)
    source            = models.CharField(max_length=30, choices=SOURCE_CHOICES, blank=True, verbose_name='Source')
    requested_by      = models.ForeignKey(RequestedByOption, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Requested By')
    rework_type       = models.ForeignKey(ReworkType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Type of Rework')
    location          = models.ForeignKey(ReworkLocation, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Location')
    priority          = models.PositiveSmallIntegerField(
        choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM, db_index=True, verbose_name='Priority',
    )
    #: The rework window production commits to, set on the Production Review
    #: screen. Nullable because every batch uploaded before slot booking
    #: existed has no slot, and backfilling a made-up date range would be
    #: worse than an honest blank.
    slot_from         = models.DateField(null=True, blank=True, verbose_name='Slot From')
    slot_to           = models.DateField(null=True, blank=True, verbose_name='Slot To')
    reference_number  = models.CharField(max_length=40, unique=True, blank=True, null=True, verbose_name='Reference Number')
    uploaded_by_name  = models.CharField(max_length=150)
    uploaded_by_email = models.EmailField(db_index=True)
    uploaded_at       = models.DateTimeField(auto_now_add=True)
    status            = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending_hod', db_index=True)
    approval_token    = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    approval_token_claimed_at = models.DateTimeField(null=True, blank=True)
    approval_token_session_key = models.CharField(max_length=40, blank=True)
    subject                      = models.CharField(max_length=500, blank=True, verbose_name='Email Subject')
    hod_reviewed_at              = models.DateTimeField(null=True, blank=True)
    hod_comments                 = models.TextField(blank=True)
    production_reviewed_by_name  = models.CharField(max_length=150, blank=True, verbose_name='Production Reviewed By')
    production_reviewed_by_email = models.EmailField(blank=True, verbose_name='Production Reviewer Email')
    production_reviewed_at       = models.DateTimeField(null=True, blank=True, verbose_name='Production Review Date')
    ppic_reviewed_by_name        = models.CharField(max_length=150, blank=True, verbose_name='PPIC Reviewed By')
    ppic_reviewed_by_email       = models.EmailField(blank=True, verbose_name='PPIC Reviewer Email')
    ppic_reviewed_at             = models.DateTimeField(null=True, blank=True, verbose_name='PPIC Review Date')
    ppic_comments                = models.TextField(blank=True, verbose_name='PPIC Comments')
    om_reviewed_by_name          = models.CharField(max_length=150, blank=True, verbose_name='OM Reviewed By')
    om_reviewed_by_email         = models.EmailField(blank=True, verbose_name='OM Reviewer Email')
    om_reviewed_at               = models.DateTimeField(null=True, blank=True, verbose_name='OM Review Date')
    om_comments                  = models.TextField(blank=True, verbose_name='OM Comments')

    # ── Routing ──────────────────────────────────────────────────────────
    #: The stage chain this batch follows, resolved once at upload and never
    #: read from RoutingRule again. Frozen deliberately: rules are editable
    #: master data, and re-resolving on each hand-off would let an edit
    #: re-route, strand or skip a stage on a batch already in flight — the
    #: reviewer who approved stage 1 would have approved a different journey
    #: from the one the batch then took.
    routing_chain     = models.JSONField(default=list, blank=True, verbose_name='Approval Flow')
    #: Where the batch is on that chain. Derivable from ``status``, but stored
    #: because the mapping is not one-to-one — ``production_done`` and
    #: ``completed`` both sit past the last stage — and because the inbox
    #: queries read it directly.
    current_stage     = models.CharField(max_length=20, blank=True, db_index=True, verbose_name='Current Stage')
    #: Which rule decided the chain: the rework type, the requester, or the
    #: fallback. Recorded so a reviewer can see why a batch skipped a stage
    #: they expected, months after the rule itself may have been edited.
    routing_origin    = models.CharField(max_length=20, blank=True, verbose_name='Routed By')
    routing_rule_note = models.CharField(max_length=200, blank=True, verbose_name='Routing Rule')

    class Meta:
        db_table = 'material_rework_dispositionbatch'
        ordering = ['-uploaded_at']
        verbose_name = 'Disposition Batch'
        verbose_name_plural = 'Disposition Batches'

    def __str__(self):
        ref = self.reference_number or f"#{self.pk}"
        return f"{ref} | {self.department} | {self.get_status_display()}"


class DispositionItem(models.Model):
    STATUS_CHOICES = [
        ('pending_om',         'Pending Order Management'),
        ('om_rejected',        'Order Management Rejected'),
        ('pending_ppic',       'Pending PPIC'),
        ('ppic_rejected',      'PPIC Rejected'),
        ('pending_hod',        'Pending HOD'),
        ('hod_rejected',       'HOD Rejected'),
        ('pending_production', 'Pending Production'),
        ('accepted',           'Accepted'),
        ('rejected',           'Rejected'),
        ('partially_accepted', 'Partially Accepted'),
        # ── The rework round trip ────────────────────────────────────────
        # Production says how much it will rework, then the material has to
        # physically move: warehouse releases it, production works it, and it
        # comes back to be counted again. Each leg is its own status so the
        # screens can show exactly whose move it is, and so a line stuck
        # mid-journey is visible rather than looking simply "pending".
        ('pending_rework_handover', 'Pending Handover to Production'),
        ('in_rework',              'In Rework at Production'),
        ('pending_return_receipt', 'Pending Warehouse Receipt'),
        ('received_discrepancy',   'Received — Count Mismatch'),
        # ``pending_warehouse`` predates the round trip. Kept so rows created
        # before it existed keep rendering; nothing routes into it now.
        ('pending_warehouse',  'Pending Warehouse'),
        ('received',           'Received'),
    ]

    batch                  = models.ForeignKey(DispositionBatch, on_delete=models.CASCADE, related_name='items')
    serial_number          = models.CharField(max_length=300, blank=True, verbose_name='Serial Number')
    country                = models.CharField(max_length=100, blank=True, verbose_name='Country')
    #: Where the material is moving from and to. Added alongside the existing
    #: single ``country`` rather than replacing it — that column is already
    #: populated on live batches and is emitted by the report export, so
    #: dropping it would silently blank a column of real history.
    from_country           = models.CharField(max_length=100, blank=True, verbose_name='From Country')
    to_country             = models.CharField(max_length=100, blank=True, verbose_name='To Country')
    material               = models.CharField(max_length=200, verbose_name='Code')
    batch_no               = models.CharField(max_length=100, blank=True, verbose_name='Batch')
    mfg_date               = models.CharField(max_length=50, blank=True, verbose_name='Mfg Date')
    exp_date               = models.CharField(max_length=50, blank=True, verbose_name='Exp Date')
    from_storage_location  = models.CharField(max_length=150, blank=True, verbose_name='From Storage Location')
    to_storage_location    = models.CharField(max_length=150, blank=True, verbose_name='To Storage Location')
    qty                    = models.PositiveIntegerField()
    box_no                 = models.CharField(max_length=100, blank=True, verbose_name='Box No')
    remark                 = models.TextField(blank=True, verbose_name='Remarks')
    item_date              = models.CharField(max_length=50, blank=True, verbose_name='Uploaded At')
    accepted_qty           = models.PositiveIntegerField(default=0)
    pending_qty            = models.PositiveIntegerField(default=0)
    qty_received           = models.PositiveIntegerField(default=0, verbose_name='Qty Received')
    production_reviewed_at = models.DateTimeField(null=True, blank=True)
    production_remarks     = models.TextField(blank=True)
    status                 = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending_hod', db_index=True)
    warehouse_received_at  = models.DateTimeField(null=True, blank=True)
    warehouse_received_by_name  = models.CharField(max_length=150, blank=True, verbose_name='Received By')
    warehouse_received_by_email = models.EmailField(blank=True, db_index=True, verbose_name='Received By Email')
    warehouse_remarks      = models.TextField(blank=True, verbose_name='WH Remarks')
    inward_number          = models.CharField(max_length=100, blank=True, verbose_name='Inward No')
    outward_number         = models.CharField(max_length=100, blank=True, verbose_name='Outward No')

    # ── The rework round trip ────────────────────────────────────────────
    # Each leg records the quantity *that leg* agreed, rather than one running
    # figure being overwritten. That is deliberate: the whole point of the
    # round trip is being able to see where units went missing, which is
    # impossible if each hand-off overwrites the last one's number.
    # Who performed each leg, alongside when.
    #
    # Every leg recorded its timestamp but not its actor, so the people doing
    # the physical work -- warehouse releasing and counting back, production
    # reworking -- had no attributable activity anywhere in the system. A
    # per-user progress report built on that showed them as zero while the
    # reviewers who only clicked Approve showed real numbers.
    #
    # Name and email are denormalised the way the batch's reviewer columns
    # already are, rather than an FK: the report has to keep reading correctly
    # after an account is deactivated or renamed, and an FK with on_delete
    # would either block the deletion or blank the history.
    rework_released_qty     = models.PositiveIntegerField(default=0, verbose_name='Qty Released to Production')
    rework_released_at      = models.DateTimeField(null=True, blank=True, verbose_name='Released At')
    rework_released_by_name  = models.CharField(max_length=150, blank=True, verbose_name='Released By')
    rework_released_by_email = models.EmailField(blank=True, db_index=True, verbose_name='Released By Email')
    rework_released_remarks = models.TextField(blank=True, verbose_name='Handover Remarks')
    reworked_qty            = models.PositiveIntegerField(default=0, verbose_name='Qty Reworked')
    rework_loss_qty         = models.PositiveIntegerField(default=0, verbose_name='Lost in Rework')
    rework_loss_reason      = models.TextField(blank=True, verbose_name='Rework Loss Reason')
    rework_returned_at      = models.DateTimeField(null=True, blank=True, verbose_name='Returned At')
    rework_returned_by_name  = models.CharField(max_length=150, blank=True, verbose_name='Returned By')
    rework_returned_by_email = models.EmailField(blank=True, db_index=True, verbose_name='Returned By Email')
    #: Set when warehouse's final count differs from what production said it
    #: sent back. Stored rather than derived so the report can filter on it and
    #: a later correction to either number does not silently clear the flag.
    has_receipt_discrepancy = models.BooleanField(default=False, verbose_name='Receipt Mismatch')

    class Meta:
        db_table = 'material_rework_dispositionitem'

    def __str__(self):
        return f"{self.material} | Qty:{self.qty} | {self.get_status_display()}"

    @property
    def receipt_variance(self) -> int:
        """Received minus what production said it sent. Negative means short."""
        return int(self.qty_received) - int(self.reworked_qty)


class RoutingRule(models.Model):
    """One configurable flow: who reviews a batch, in what order.

    Replaces a hardcoded ``if requested_by is Order Management`` in the upload
    view. A rule is keyed on **either** a Requested By option or a Type of
    Rework, never both, and holds the ordered stage chain that key should take.

    The two keys are not equal partners. A rework type wins outright over the
    requester, because assigning a type such as Damage is precisely how a
    batch is sent straight to whoever should handle it — it has to be able to
    override the requester's longer flow rather than being merged with it. The
    engine in ``routing.py`` applies that precedence; this model only stores
    the rules.

    Editing a rule affects **new** batches only. Every batch keeps its own copy
    of the chain it was created with (``DispositionBatch.routing_chain``), so
    reordering a flow can never re-route work that is already moving.
    """

    #: Exactly one of these is set; the check constraint below enforces it.
    requested_by = models.OneToOneField(
        RequestedByOption, on_delete=models.CASCADE,
        null=True, blank=True, related_name='routing_rule',
        verbose_name='Requested By',
    )
    rework_type = models.OneToOneField(
        ReworkType, on_delete=models.CASCADE,
        null=True, blank=True, related_name='routing_rule',
        verbose_name='Type of Rework',
    )
    #: Ordered stage codes, e.g. ["om", "ppic", "production"]. Validated by
    #: ``routing.validate_chain`` in :meth:`clean` — a chain must end at
    #: exactly one terminal stage and cannot repeat a stage.
    stages = models.JSONField(default=list, verbose_name='Flow')
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'material_rework_routingrule'
        verbose_name = 'Routing Rule'
        verbose_name_plural = 'Routing Rules'
        ordering = ['rework_type__name', 'requested_by__name']
        constraints = [
            # A rule keyed on nothing would match everything; a rule keyed on
            # both would make precedence meaningless. Enforced in the database
            # because a rule can be written by a migration or a shell, not
            # only by the form that also calls clean().
            models.CheckConstraint(
                condition=(
                    models.Q(requested_by__isnull=False, rework_type__isnull=True)
                    | models.Q(requested_by__isnull=True, rework_type__isnull=False)
                ),
                name='mr_routingrule_exactly_one_key',
            ),
        ]

    def __str__(self):
        return f"{self.key_label} → {self.chain_label}"

    # ── Reading ───────────────────────────────────────────────────────────

    @property
    def key_label(self):
        if self.rework_type_id:
            return f"Type: {self.rework_type.name}"
        if self.requested_by_id:
            return f"Requested by: {self.requested_by.name}"
        return "—"

    @property
    def chain_label(self):
        from . import routing
        return routing.describe(self.stages)

    @property
    def is_type_rule(self):
        return bool(self.rework_type_id)

    # ── Writing ───────────────────────────────────────────────────────────

    def clean(self):
        from django.core.exceptions import ValidationError

        from . import routing

        if bool(self.requested_by_id) == bool(self.rework_type_id):
            raise ValidationError(
                'Choose either a Requested By option or a Type of Rework — one, not both.'
            )
        try:
            self.stages = routing.validate_chain(self.stages)
        except routing.RoutingError as exc:
            raise ValidationError({'stages': str(exc)}) from exc

    # ── Lookups used by the engine ────────────────────────────────────────

    @classmethod
    def for_rework_type(cls, rework_type):
        if not rework_type:
            return None
        return cls.objects.filter(rework_type=rework_type, is_active=True).first()

    @classmethod
    def for_requested_by(cls, requested_by):
        if not requested_by:
            return None
        return cls.objects.filter(requested_by=requested_by, is_active=True).first()
