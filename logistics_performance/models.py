from django.db import models
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# REUSABLE BASE — Audit trail + Soft delete
# Inherited by every model in this module (and any future submodule).
# Each child gets these columns in its OWN table:
#   created_at, updated_at, created_by, updated_by,
#   is_deleted (0=active, 1=deleted), deleted_by, deleted_at
# Managers:  objects -> active only (default) ; all_objects -> includes deleted
# Never call obj.delete(); use obj.soft_delete(request.user.username).
# ─────────────────────────────────────────────────────────────────────────────
class ActiveManager(models.Manager):
    """Default manager — hides soft-deleted rows everywhere."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AuditSoftDeleteModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.CharField(max_length=150, blank=True, null=True)
    updated_by = models.CharField(max_length=150, blank=True, null=True)

    is_deleted = models.BooleanField(default=False)
    deleted_by = models.CharField(max_length=150, blank=True, null=True)
    deleted_at = models.DateTimeField(blank=True, null=True)

    objects = ActiveManager()        # active-only (default)
    all_objects = models.Manager()   # includes soft-deleted

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """Auto-stamp created_by (on create) and updated_by (on every change)
        with the currently logged-in user."""
        from .middleware import get_current_username
        username = get_current_username()
        # Never blank out created_by on an update — keep the original value.
        if self.pk and not self._state.adding and not self.created_by:
            original = type(self).all_objects.filter(pk=self.pk).values_list(
                'created_by', flat=True).first()
            if original:
                self.created_by = original
        if username:
            if self._state.adding and not self.created_by:
                self.created_by = username
            self.updated_by = username
            uf = kwargs.get('update_fields')
            if uf is not None:
                uf = set(uf) | {'updated_by'}
                if self._state.adding:
                    uf |= {'created_by'}
                kwargs['update_fields'] = uf
        super().save(*args, **kwargs)

    def soft_delete(self, username=None):
        """Mark the row deleted instead of removing it from the database."""
        self.is_deleted = True
        self.deleted_by = username
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_by', 'deleted_at', 'updated_at'])


class PincodeMaster(AuditSoftDeleteModel):
    # Core Location Fields
    name = models.CharField(max_length=100, blank=True, null=True)
    location = models.CharField(max_length=100)
    city = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=50, blank=True, null=True)
    state_name = models.CharField(max_length=50)
    zone = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=10)

    # Courier Details
    courier_code = models.CharField(max_length=50, blank=True, null=True)
    courier_name = models.CharField(max_length=100)
    mode = models.CharField(max_length=50, blank=True, null=True)
    service_type = models.CharField(max_length=100, blank=True, null=True)

    # Delivery Details
    tat = models.PositiveIntegerField(help_text="Turnaround time in days")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    min_weight = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    oda_nonoda = models.CharField(max_length=50, blank=True, null=True)

    # Meta Fields
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Pincode Master"
        verbose_name_plural = "Pincode Masters"
        ordering = ['-updated_at']
        constraints = [
            # Uniqueness applies only to ACTIVE rows, so a soft-deleted row
            # does not block re-adding the same combination as a new record.
            models.UniqueConstraint(
                fields=['name', 'pincode', 'courier_name', 'mode'],
                condition=models.Q(is_deleted=False),
                name='uniq_active_pincode',
            )
        ]

    def __str__(self):
        return f"{self.pincode} - {self.location}"


class CourierMaster(AuditSoftDeleteModel):
    """Authoritative list of couriers (code + name). Used as the validation
    source for Location Master uploads: a location row's courier_code +
    courier_name must match an entry here."""
    courier_code = models.CharField(max_length=50)
    courier_name = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Courier Master"
        verbose_name_plural = "Courier Masters"
        ordering = ['-updated_at']
        unique_together = [['courier_code', 'courier_name']]

    def __str__(self):
        return f"{self.courier_code} - {self.courier_name}"


class ZoneMaster(AuditSoftDeleteModel):
    """Authoritative list of zones. Used as the validation source for Location
    Master uploads: a location row's zone must match an entry here."""
    zone = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Zone Master"
        verbose_name_plural = "Zone Masters"
        ordering = ['zone']

    def __str__(self):
        return self.zone


class ModeMaster(AuditSoftDeleteModel):
    """Authoritative list of transport modes. Validation source for Location
    Master and Logistics Performance uploads: a row's mode must match an entry here."""
    mode = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Mode Master"
        verbose_name_plural = "Mode Masters"
        ordering = ['mode']

    def __str__(self):
        return self.mode


class StateMaster(AuditSoftDeleteModel):
    """Authoritative list of states. Validation source for Location Master
    uploads: a location row's state must match an entry here."""
    state = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "State Master"
        verbose_name_plural = "State Masters"
        ordering = ['state']

    def __str__(self):
        return self.state


class PlantMaster(AuditSoftDeleteModel):
    """Authoritative list of plants. Validation source for Logistics Performance
    uploads: a row's plant name must match an entry here (else the row is rejected).

    A plant is considered a duplicate only when BOTH plant_name AND plant_address
    match — the same plant name at a different address is allowed.

    Soft-deleted via the shared base (is_deleted / deleted_by / deleted_at)."""
    plant_name    = models.CharField(max_length=150)
    plant_email   = models.EmailField(max_length=254, blank=True, null=True)
    plant_company = models.CharField(max_length=150, blank=True, null=True)
    plant_address = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Plant Master"
        verbose_name_plural = "Plant Masters"
        ordering = ['plant_name']
        constraints = [
            # Uniqueness applies only to ACTIVE rows (mirrors uniq_active_pincode),
            # so a soft-deleted row does not block re-adding the same plant.
            models.UniqueConstraint(
                fields=['plant_name', 'plant_address'],
                condition=models.Q(is_deleted=False),
                name='uniq_active_plant',
            )
        ]

    def __str__(self):
        return self.plant_name


class DomesticSalesCard(AuditSoftDeleteModel):
    # Division is validated against the Division master table (see below),
    # so no hard-coded choices list is kept here.
    division = models.CharField(max_length=20)
    sales_type = models.CharField(max_length=100)
    sales_type_description = models.TextField(blank=True, null=True)
    pending_show = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Domestic Sales Card"
        verbose_name_plural = "Domestic Sales Cards"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.division} - {self.sales_type}"


class LogisticsPerformance(AuditSoftDeleteModel):
    # Core fields from Excel
    division        = models.CharField(max_length=20, blank=True, null=True)
    location        = models.CharField(max_length=100, blank=True, null=True)
    plant_name      = models.CharField(max_length=100, blank=True, null=True)
    billing_doc     = models.CharField(max_length=50, blank=True, null=True)
    billing_date    = models.DateField(blank=True, null=True)
    inv_value       = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    doc_type        = models.CharField(max_length=20, blank=True, null=True)
    billed_qty      = models.IntegerField(blank=True, null=True)
    shipto_party    = models.CharField(max_length=50, blank=True, null=True)
    shipto_name     = models.CharField(max_length=200, blank=True, null=True)
    shipto_city     = models.CharField(max_length=100, blank=True, null=True)
    pincode         = models.CharField(max_length=10, blank=True, null=True)
    courier_no      = models.CharField(max_length=50, blank=True, null=True)
    courier_code    = models.CharField(max_length=50, blank=True, null=True)
    courier_name    = models.CharField(max_length=100, blank=True, null=True)
    docket_no       = models.CharField(max_length=100, blank=True, null=True)
    mode            = models.CharField(max_length=50, blank=True, null=True)
    boxes           = models.IntegerField(blank=True, null=True)
    ch_weight       = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    dispatch_date   = models.DateField(blank=True, null=True)
    delivery_date   = models.DateField(blank=True, null=True)
    basic_logistics_cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    other_charges   = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    remarks         = models.TextField(blank=True, null=True)
    cost_center     = models.CharField(max_length=50, blank=True, null=True)
    shipto_region   = models.CharField(max_length=100, blank=True, null=True)
    zone            = models.CharField(max_length=100, blank=True, null=True)
    creator         = models.CharField(max_length=100, blank=True, null=True)

    # Auto-calculated fields (from Location Master TAT lookup)
    tat_from_master             = models.IntegerField(blank=True, null=True)
    expected_delivery_date      = models.DateField(blank=True, null=True)
    dispatch_vs_edd_tat         = models.IntegerField(blank=True, null=True)
    dispatch_vs_delivery_tat    = models.IntegerField(blank=True, null=True)
    # Auto-calculated: basic_logistics_cost + other_charges
    actual_logistics_cost       = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)

    class Meta:
        verbose_name = "Logistics Performance"
        verbose_name_plural = "Logistics Performance"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.division} - {self.docket_no or self.billing_doc}"


# ─────────────────────────────────────────────────────────────────────────────
# DIVISION MASTER  (business-editable — replaces hard-coded division values)
# ─────────────────────────────────────────────────────────────────────────────
class DivisionMaster(AuditSoftDeleteModel):
    """Master list of company divisions.

    Soft-deleted via the shared base (is_deleted / deleted_by / deleted_at).
    Used to validate the division on Sales Type Master uploads.
    """
    companyname   = models.CharField(max_length=150, blank=True, null=True)
    division      = models.CharField(max_length=100)
    code          = models.IntegerField()

    class Meta:
        verbose_name = "Division Master"
        verbose_name_plural = "Division Masters"
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.companyname or ''} - {self.division}".strip(' -')
