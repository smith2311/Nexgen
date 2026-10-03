"""
Warehouse Inspection Models
────────────────────────────────────────────────────────────────
Models for managing warehouse inspection conditions and checklist categories.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

INSPECTION_PHOTO_MIN_BYTES = 400 * 1024  # 400 KB
INSPECTION_PHOTO_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def validate_inspection_photo_size(file):
    """Enforce the 400KB-5MB proof-photo range; the tablet UI compresses to fit before upload."""
    if file.size > INSPECTION_PHOTO_MAX_BYTES:
        raise ValidationError('Photo must not be larger than 5MB.')
    if file.size < INSPECTION_PHOTO_MIN_BYTES:
        raise ValidationError('Photo must be at least 400KB. Please retake with better quality.')


class WarehouseInspectionBuilding(models.Model):
    """Top-level warehouse/building master for inspection setup."""

    building_id = models.AutoField(primary_key=True)
    building_name = models.CharField(max_length=150, unique=True)
    building_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['building_order', 'building_name']
        indexes = [
            models.Index(fields=['is_active', 'building_order'], name='warehouse_i_buildi_idx'),
        ]

    def __str__(self):
        return self.building_name


class ConditionCategory(models.Model):
    """Master table for warehouse inspection condition categories."""

    ENTITY_TYPE_CHOICES = [
        ('admin', 'Admin'),
        ('user', 'User'),
    ]

    condition_category_id = models.AutoField(primary_key=True)
    building = models.ForeignKey(
        WarehouseInspectionBuilding,
        on_delete=models.PROTECT,
        related_name='conditions',
    )
    condition_category_name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    entity_type = models.CharField(
        max_length=20,
        choices=ENTITY_TYPE_CHOICES,
        default='admin',
    )
    created_by = models.EmailField()  # Store email of logged-in user
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_by = models.EmailField(blank=True, null=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['building__building_order', '-created_at']
        unique_together = [('building', 'condition_category_name')]
        indexes = [
            models.Index(fields=['building', 'is_deleted'], name='warehouse_i_build_c_idx'),
            models.Index(fields=['entity_type', 'is_deleted'], name='warehouse_i_entity__idx'),
            models.Index(fields=['created_by'], name='warehouse_i_created__idx'),
        ]

    def __str__(self):
        return self.condition_category_name


class WarehouseInspectionCheckpoint(models.Model):
    """Checkpoints/items within a condition category for inspection."""

    checkpoint_id = models.AutoField(primary_key=True)
    condition_category = models.ForeignKey(
        ConditionCategory,
        on_delete=models.CASCADE,
        related_name='checkpoints'
    )
    checkpoint_name = models.CharField(max_length=500)
    checkpoint_description = models.TextField(blank=True, null=True)
    internal_department_email = models.EmailField(
        blank=True,
        null=True,
        help_text='Department email to keep in CC when this checkpoint is submitted.',
    )
    checkpoint_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['condition_category', 'checkpoint_order']
        unique_together = [('condition_category', 'checkpoint_name')]
        indexes = [
            models.Index(fields=['condition_category', 'checkpoint_order'], name='warehouse_i_condition_idx'),
        ]

    def __str__(self):
        return f"{self.condition_category.condition_category_name} - {self.checkpoint_name}"


class WarehouseInspectionFloor(models.Model):
    """Master floor list used to decide which checklist items appear."""

    floor_id = models.AutoField(primary_key=True)
    building = models.ForeignKey(
        WarehouseInspectionBuilding,
        on_delete=models.PROTECT,
        related_name='floors',
    )
    floor_name = models.CharField(max_length=100)
    floor_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['building__building_order', 'floor_order', 'floor_name']
        unique_together = [('building', 'floor_name')]
        indexes = [
            models.Index(fields=['building', 'is_active'], name='warehouse_i_build_f_idx'),
            models.Index(fields=['is_active', 'floor_order'], name='warehouse_i_is_acti_idx'),
        ]

    def __str__(self):
        return self.floor_name


class WarehouseInspectionFloorAssignment(models.Model):
    """Floor-specific applicability and responsible email for each checkpoint."""

    assignment_id = models.AutoField(primary_key=True)
    checkpoint = models.ForeignKey(
        WarehouseInspectionCheckpoint,
        on_delete=models.CASCADE,
        related_name='floor_assignments',
    )
    floor = models.ForeignKey(
        WarehouseInspectionFloor,
        on_delete=models.CASCADE,
        related_name='checkpoint_assignments',
    )
    is_applicable = models.BooleanField(default=True)
    responsible_email = models.EmailField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['checkpoint__condition_category', 'checkpoint__checkpoint_order', 'floor__floor_order']
        unique_together = [('checkpoint', 'floor')]
        indexes = [
            models.Index(fields=['floor', 'is_applicable'], name='warehouse_i_floor_i_idx'),
            models.Index(fields=['responsible_email'], name='warehouse_i_respons_idx'),
        ]

    def __str__(self):
        return f"{self.checkpoint.checkpoint_name} - {self.floor.floor_name}"


class WarehouseInspectionSubmission(models.Model):
    """
    The current state of one (building, condition, floor)'s inspection for one
    calendar day.

    Unlimited resubmissions are allowed per day; each resubmit upserts this
    same row (see WarehouseInspectionSubmissionCreateSerializer.create) rather
    than creating a new one, so submitted_by_email/submitted_by_name/notes
    reflect the LATEST submitter, not necessarily the first. Every individual
    resubmission is preserved separately in WarehouseInspectionSubmissionEvent.
    """

    submission_id = models.AutoField(primary_key=True)
    building = models.ForeignKey(
        WarehouseInspectionBuilding,
        on_delete=models.PROTECT,
        related_name='inspection_submissions',
    )
    condition_category = models.ForeignKey(
        ConditionCategory,
        on_delete=models.PROTECT,
        related_name='inspection_submissions',
    )
    floor = models.ForeignKey(
        WarehouseInspectionFloor,
        on_delete=models.PROTECT,
        related_name='inspection_submissions',
    )
    inspection_date = models.DateField(default=timezone.localdate, db_index=True)
    submitted_by_email = models.EmailField()
    submitted_by_name = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    email_to = models.JSONField(default=list, blank=True)
    email_cc = models.JSONField(default=list, blank=True)
    email_sent = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(blank=True, null=True)
    email_error = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(
                fields=['building', 'condition_category', 'floor', 'inspection_date'],
                name='wh_submission_daily_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['submitted_by_email', 'submitted_at'], name='warehouse_i_submitt_idx'),
            models.Index(fields=['building', 'submitted_at'], name='warehouse_i_build_s_idx'),
            models.Index(fields=['condition_category', 'floor'], name='warehouse_i_conditi_idx'),
            models.Index(fields=['email_sent'], name='warehouse_i_email_s_idx'),
            models.Index(
                fields=['building', 'floor', 'condition_category', 'inspection_date'],
                name='wh_submission_scope_idx',
            ),
        ]

    def __str__(self):
        return (
            f"{self.building.building_name} - "
            f"{self.condition_category.condition_category_name} - "
            f"{self.floor.floor_name} - {self.submitted_by_email}"
        )


class WarehouseInspectionSubmissionEvent(models.Model):
    """
    One immutable record per individual (re)submission of a
    WarehouseInspectionSubmission, since that row itself is now overwritten on
    every same-day resubmit. This is what the daily activity report reads to
    show every inspection actually performed, not just each day's final state.
    """

    event_id = models.AutoField(primary_key=True)
    submission = models.ForeignKey(
        WarehouseInspectionSubmission,
        on_delete=models.CASCADE,
        related_name='events',
    )
    submitted_by_email = models.EmailField()
    submitted_by_name = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['submission', 'created_at'], name='wh_sub_event_idx'),
        ]

    def __str__(self):
        return f"Event for submission #{self.submission_id} by {self.submitted_by_email}"


class WarehouseInspectionSecondSubmission(models.Model):
    """Rare second inspection for the same user/building/condition/floor/day."""

    second_submission_id = models.AutoField(primary_key=True)
    first_submission = models.ForeignKey(
        WarehouseInspectionSubmission,
        on_delete=models.SET_NULL,
        related_name='second_inspections',
        blank=True,
        null=True,
    )
    building = models.ForeignKey(
        WarehouseInspectionBuilding,
        on_delete=models.PROTECT,
        related_name='second_inspection_submissions',
    )
    condition_category = models.ForeignKey(
        ConditionCategory,
        on_delete=models.PROTECT,
        related_name='second_inspection_submissions',
    )
    floor = models.ForeignKey(
        WarehouseInspectionFloor,
        on_delete=models.PROTECT,
        related_name='second_inspection_submissions',
    )
    submitted_by_email = models.EmailField()
    submitted_by_name = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True, null=True)
    inspection_date = models.DateField(default=timezone.localdate)
    submitted_at = models.DateTimeField(auto_now_add=True)
    email_to = models.JSONField(default=list, blank=True)
    email_cc = models.JSONField(default=list, blank=True)
    email_sent = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(blank=True, null=True)
    email_error = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'building',
                    'condition_category',
                    'floor',
                    'submitted_by_email',
                    'inspection_date',
                ],
                name='wh_second_daily_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['submitted_by_email', 'inspection_date'], name='wh_second_submit_idx'),
            models.Index(fields=['building', 'inspection_date'], name='wh_second_build_idx'),
            models.Index(fields=['condition_category', 'floor'], name='wh_second_scope_idx'),
            models.Index(fields=['email_sent'], name='wh_second_email_idx'),
        ]

    def __str__(self):
        return (
            f"Second - {self.building.building_name} - "
            f"{self.condition_category.condition_category_name} - "
            f"{self.floor.floor_name} - {self.submitted_by_email}"
        )


class WarehouseInspectionResponse(models.Model):
    """Stores user responses to inspection checkpoints."""

    RESPONSE_CHOICES = [
        ('yes', 'Yes'),
        ('no', 'No'),
    ]

    response_id = models.AutoField(primary_key=True)
    submission = models.ForeignKey(
        WarehouseInspectionSubmission,
        on_delete=models.CASCADE,
        related_name='responses',
        blank=True,
        null=True,
    )
    event = models.ForeignKey(
        WarehouseInspectionSubmissionEvent,
        on_delete=models.SET_NULL,
        related_name='responses',
        blank=True,
        null=True,
    )
    checkpoint = models.ForeignKey(WarehouseInspectionCheckpoint, on_delete=models.CASCADE)
    floor = models.ForeignKey(
        WarehouseInspectionFloor,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    floor_assignment = models.ForeignKey(
        WarehouseInspectionFloorAssignment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    ticket = models.ForeignKey(
        'WarehouseInspectionTicket',
        on_delete=models.SET_NULL,
        related_name='response_events',
        blank=True,
        null=True,
    )
    response = models.CharField(max_length=20, choices=RESPONSE_CHOICES)
    comments = models.TextField(blank=True, null=True)
    photo = models.ImageField(
        upload_to='warehouse_inspection/response_photos/%Y/%m/',
        validators=[validate_inspection_photo_size],
        blank=True,
        null=True,
    )
    responsible_email = models.EmailField(blank=True, null=True)
    internal_department_email = models.EmailField(blank=True, null=True)
    responded_by_email = models.EmailField()
    responded_at = models.DateTimeField(auto_now_add=True)
    warehouse_floor = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        ordering = ['-responded_at']
        indexes = [
            models.Index(fields=['responded_by_email', 'responded_at'], name='warehouse_i_responded__idx'),
            models.Index(fields=['submission', 'checkpoint'], name='warehouse_i_submiss_idx'),
            models.Index(fields=['floor', 'response'], name='warehouse_i_floor_r_idx'),
        ]

    def __str__(self):
        return f"{self.checkpoint.checkpoint_name} - {self.response}"

    @property
    def photo_absolute_url(self):
        if not self.photo:
            return ''
        return f"{settings.SITE_URL.rstrip('/')}{self.photo.url}"


class WarehouseInspectionSecondResponse(models.Model):
    """Stores checkpoint responses for a second same-day inspection."""

    RESPONSE_CHOICES = [
        ('yes', 'Yes'),
        ('no', 'No'),
    ]

    second_response_id = models.AutoField(primary_key=True)
    submission = models.ForeignKey(
        WarehouseInspectionSecondSubmission,
        on_delete=models.CASCADE,
        related_name='responses',
    )
    checkpoint = models.ForeignKey(WarehouseInspectionCheckpoint, on_delete=models.CASCADE)
    floor = models.ForeignKey(
        WarehouseInspectionFloor,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    floor_assignment = models.ForeignKey(
        WarehouseInspectionFloorAssignment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    response = models.CharField(max_length=20, choices=RESPONSE_CHOICES)
    comments = models.TextField(blank=True, null=True)
    photo = models.ImageField(
        upload_to='warehouse_inspection/response_photos/%Y/%m/',
        validators=[validate_inspection_photo_size],
        blank=True,
        null=True,
    )
    responsible_email = models.EmailField(blank=True, null=True)
    internal_department_email = models.EmailField(blank=True, null=True)
    responded_by_email = models.EmailField()
    responded_at = models.DateTimeField(auto_now_add=True)
    warehouse_floor = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        ordering = ['-responded_at']
        constraints = [
            models.UniqueConstraint(
                fields=['submission', 'checkpoint'],
                name='wh_second_resp_once',
            ),
        ]
        indexes = [
            models.Index(fields=['responded_by_email', 'responded_at'], name='wh_second_resp_user_idx'),
            models.Index(fields=['submission', 'checkpoint'], name='wh_second_resp_sub_idx'),
            models.Index(fields=['floor', 'response'], name='wh_second_resp_floor_idx'),
        ]

    def __str__(self):
        return f"Second - {self.checkpoint.checkpoint_name} - {self.response}"

    @property
    def photo_absolute_url(self):
        if not self.photo:
            return ''
        return f"{settings.SITE_URL.rstrip('/')}{self.photo.url}"


class WarehouseInspectionTicket(models.Model):
    """
    A persistent open issue for one (floor, checkpoint), spanning across days.

    Opened the first time a checkpoint is answered 'No' for that floor;
    reopened if it regresses from Yes back to No later; closed the next time
    it's answered 'Yes'. Drives the daily reminder emails
    (warehouse_inspection/tickets.py) and the "is this warehouse open" status
    in the daily report - it has no dedicated admin screen by design.

    No row is created for a (floor, checkpoint) pair that has never been
    answered 'No' - "no ticket" means "never an issue", not "resolved".
    """

    STATUS_CHOICES = [('open', 'Open'), ('closed', 'Closed')]

    ticket_id = models.AutoField(primary_key=True)
    building = models.ForeignKey(
        WarehouseInspectionBuilding,
        on_delete=models.PROTECT,
        related_name='inspection_tickets',
    )
    floor = models.ForeignKey(
        WarehouseInspectionFloor,
        on_delete=models.PROTECT,
        related_name='inspection_tickets',
    )
    condition_category = models.ForeignKey(
        ConditionCategory,
        on_delete=models.PROTECT,
        related_name='inspection_tickets',
    )
    checkpoint = models.ForeignKey(
        WarehouseInspectionCheckpoint,
        on_delete=models.CASCADE,
        related_name='inspection_tickets',
    )
    floor_assignment = models.ForeignKey(
        WarehouseInspectionFloorAssignment,
        on_delete=models.SET_NULL,
        related_name='inspection_tickets',
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    responsible_email = models.EmailField(blank=True, null=True)
    internal_department_email = models.EmailField(blank=True, null=True)

    opened_at = models.DateTimeField(blank=True, null=True)
    opened_response = models.ForeignKey(
        'WarehouseInspectionResponse', on_delete=models.SET_NULL, related_name='+', blank=True, null=True,
    )
    closed_at = models.DateTimeField(blank=True, null=True)
    closed_response = models.ForeignKey(
        'WarehouseInspectionResponse', on_delete=models.SET_NULL, related_name='+', blank=True, null=True,
    )
    last_response = models.ForeignKey(
        'WarehouseInspectionResponse', on_delete=models.SET_NULL, related_name='+', blank=True, null=True,
    )
    last_reminder_sent_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['checkpoint', 'floor'], name='wh_ticket_checkpoint_floor_unique'),
        ]
        indexes = [
            models.Index(fields=['building', 'status'], name='wh_ticket_building_idx'),
            models.Index(fields=['status', 'opened_at'], name='wh_ticket_status_idx'),
        ]

    def __str__(self):
        return f"[{self.status}] {self.checkpoint.checkpoint_name} - {self.floor.floor_name}"


class WarehouseInspectionLoginLog(models.Model):
    """One row per day a given user first opens the Warehouse Inspection module (see views.record_warehouse_login)."""

    log_id = models.AutoField(primary_key=True)
    user_email = models.EmailField()
    user_name = models.CharField(max_length=255, blank=True)
    logged_in_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-logged_in_at']
        indexes = [
            models.Index(fields=['user_email', 'logged_in_at'], name='wh_login_user_idx'),
        ]

    def __str__(self):
        return f"{self.user_email} @ {self.logged_in_at}"


class WarehouseInspectionResponsePhotoUpload(models.Model):
    """Holds a checkpoint proof photo captured on a tablet before the checklist is submitted."""

    upload_token = models.CharField(max_length=64, unique=True, db_index=True)
    checkpoint = models.ForeignKey(
        WarehouseInspectionCheckpoint,
        on_delete=models.CASCADE,
        related_name='photo_uploads',
    )
    photo = models.ImageField(
        upload_to='warehouse_inspection/response_photos/%Y/%m/',
        validators=[validate_inspection_photo_size],
    )
    uploaded_by_email = models.EmailField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    consumed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['uploaded_by_email', 'consumed'], name='warehouse_i_upl_usr_idx'),
        ]

    def __str__(self):
        return f"Photo upload {self.upload_token} - {self.checkpoint.checkpoint_name}"


class WarehouseInspectionReportRecipient(models.Model):
    """Master list of emails that receive Warehouse Inspection report emails
    (daily activity report + inspection summary)."""

    recipient_id = models.AutoField(primary_key=True)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'email']

    def __str__(self):
        return self.email
