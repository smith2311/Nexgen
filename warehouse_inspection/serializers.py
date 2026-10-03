"""
Warehouse Inspection Serializers
────────────────────────────────────────────────────────────────
REST API serializers for warehouse inspection models.
"""
import uuid
from datetime import datetime, time, timedelta

from rest_framework import serializers
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import (
    WarehouseInspectionBuilding,
    ConditionCategory,
    WarehouseInspectionCheckpoint,
    WarehouseInspectionFloor,
    WarehouseInspectionFloorAssignment,
    WarehouseInspectionResponse,
    WarehouseInspectionResponsePhotoUpload,
    WarehouseInspectionSecondResponse,
    WarehouseInspectionSecondSubmission,
    WarehouseInspectionSubmission,
    WarehouseInspectionSubmissionEvent,
)


def get_warehouse_sender_email():
    return settings.DEFAULT_FROM_EMAIL


def get_warehouse_user_identity(user):
    """Return a stable display email/name for saved inspection audit fields."""
    if not user or not getattr(user, 'is_authenticated', False):
        return get_warehouse_sender_email(), ''

    email = str(getattr(user, 'email', '') or '').strip()
    username = str(user.get_username() if hasattr(user, 'get_username') else '').strip()
    full_name = ''
    if hasattr(user, 'get_full_name'):
        full_name = str(user.get_full_name() or '').strip()

    if email:
        return email, full_name
    return get_warehouse_sender_email(), full_name or username


def get_warehouse_inspection_day():
    """Return the app-local inspection date and midnight bounds for the daily limit."""
    inspection_tz = timezone.get_default_timezone()
    inspection_date = timezone.localdate(timezone=inspection_tz)
    day_start = timezone.make_aware(datetime.combine(inspection_date, time.min), inspection_tz)
    day_end = timezone.make_aware(
        datetime.combine(inspection_date + timedelta(days=1), time.min),
        inspection_tz,
    )
    return inspection_date, day_start, day_end


class WarehouseInspectionBuildingSerializer(serializers.ModelSerializer):
    """Serializer for warehouse/building master data."""

    class Meta:
        model = WarehouseInspectionBuilding
        fields = [
            'building_id', 'building_name', 'building_order', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['building_id', 'created_at', 'updated_at']


class WarehouseInspectionFloorSerializer(serializers.ModelSerializer):
    """Serializer for warehouse floor master data."""
    building_name = serializers.CharField(source='building.building_name', read_only=True)

    class Meta:
        model = WarehouseInspectionFloor
        fields = [
            'floor_id', 'building', 'building_name', 'floor_name', 'floor_order', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['floor_id', 'created_at', 'updated_at']


class WarehouseInspectionFloorAssignmentSerializer(serializers.ModelSerializer):
    """Serializer for checkpoint/floor responsible-person mappings."""
    floor_name = serializers.CharField(source='floor.floor_name', read_only=True)
    checkpoint_name = serializers.CharField(source='checkpoint.checkpoint_name', read_only=True)
    building_name = serializers.CharField(source='floor.building.building_name', read_only=True)

    class Meta:
        model = WarehouseInspectionFloorAssignment
        fields = [
            'assignment_id', 'checkpoint', 'checkpoint_name', 'floor', 'floor_name',
            'building_name',
            'is_applicable', 'responsible_email', 'created_at', 'updated_at'
        ]
        read_only_fields = ['assignment_id', 'created_at', 'updated_at']

    def validate(self, attrs):
        checkpoint = attrs.get('checkpoint') or getattr(self.instance, 'checkpoint', None)
        floor = attrs.get('floor') or getattr(self.instance, 'floor', None)
        if checkpoint and floor and checkpoint.condition_category.building_id != floor.building_id:
            raise serializers.ValidationError(
                'Checkpoint and floor must belong to the same building.'
            )
        return attrs


class WarehouseInspectionCheckpointSerializer(serializers.ModelSerializer):
    """Serializer for inspection checkpoints."""
    floor_assignments = WarehouseInspectionFloorAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = WarehouseInspectionCheckpoint
        fields = [
            'checkpoint_id', 'condition_category', 'checkpoint_name',
            'checkpoint_description', 'internal_department_email',
            'checkpoint_order', 'is_active', 'floor_assignments',
            'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['checkpoint_id', 'created_by', 'created_at', 'updated_at']


class WarehouseInspectionResponseSerializer(serializers.ModelSerializer):
    """Serializer for inspection responses."""
    checkpoint_name = serializers.CharField(source='checkpoint.checkpoint_name', read_only=True)
    floor_name = serializers.CharField(source='floor.floor_name', read_only=True)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = WarehouseInspectionResponse
        fields = [
            'response_id', 'submission', 'checkpoint', 'checkpoint_name',
            'floor', 'floor_name', 'response', 'comments', 'photo_url',
            'responded_by_email', 'responded_at'
        ]
        read_only_fields = ['response_id', 'responded_at']

    def get_photo_url(self, obj):
        if not obj.photo:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.photo.url) if request else obj.photo.url


class WarehouseInspectionSecondResponseSerializer(serializers.ModelSerializer):
    """Serializer for second-inspection checkpoint responses."""

    checkpoint_name = serializers.CharField(source='checkpoint.checkpoint_name', read_only=True)
    floor_name = serializers.CharField(source='floor.floor_name', read_only=True)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = WarehouseInspectionSecondResponse
        fields = [
            'second_response_id', 'submission', 'checkpoint', 'checkpoint_name',
            'floor', 'floor_name', 'response', 'comments', 'photo_url',
            'responded_by_email', 'responded_at'
        ]
        read_only_fields = ['second_response_id', 'responded_at']

    def get_photo_url(self, obj):
        if not obj.photo:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.photo.url) if request else obj.photo.url


class WarehouseInspectionResponsePhotoUploadSerializer(serializers.ModelSerializer):
    """Accepts one checkpoint proof-photo upload from the tablet form before final submit."""

    class Meta:
        model = WarehouseInspectionResponsePhotoUpload
        fields = ['checkpoint', 'photo']

    def validate_checkpoint(self, checkpoint):
        if not checkpoint.is_active:
            raise serializers.ValidationError('This checklist item is not active.')
        return checkpoint

    def create(self, validated_data):
        request = self.context.get('request')
        user_email, _ = get_warehouse_user_identity(getattr(request, 'user', None))
        return WarehouseInspectionResponsePhotoUpload.objects.create(
            upload_token=uuid.uuid4().hex,
            uploaded_by_email=user_email,
            **validated_data,
        )


class ConditionCategoryDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for condition categories with checkpoints."""
    building_name = serializers.CharField(source='building.building_name', read_only=True)
    checkpoints = serializers.SerializerMethodField()

    class Meta:
        model = ConditionCategory
        fields = [
            'condition_category_id', 'building', 'building_name',
            'condition_category_name', 'description',
            'created_by', 'created_at', 'checkpoints'
        ]
        read_only_fields = [
            'condition_category_id', 'created_at'
        ]

    def get_checkpoints(self, obj):
        checkpoints = obj.checkpoints.filter(is_active=True).order_by(
            'checkpoint_order',
            'checkpoint_name',
        )
        return WarehouseInspectionCheckpointSerializer(
            checkpoints,
            many=True,
            context=self.context,
        ).data


class ConditionCategorySerializer(serializers.ModelSerializer):
    """List serializer for condition categories."""
    building_name = serializers.CharField(source='building.building_name', read_only=True)
    checkpoint_count = serializers.SerializerMethodField()
    checkpoints = serializers.SerializerMethodField()

    class Meta:
        model = ConditionCategory
        fields = [
            'condition_category_id', 'building', 'building_name',
            'condition_category_name', 'description',
            'created_by', 'created_at', 'checkpoint_count', 'checkpoints'
        ]
        read_only_fields = [
            'condition_category_id', 'created_at'
        ]

    def get_checkpoint_count(self, obj):
        return obj.checkpoints.filter(is_active=True).count()

    def get_checkpoints(self, obj):
        checkpoints = obj.checkpoints.filter(is_active=True).order_by(
            'checkpoint_order',
            'checkpoint_name',
        )
        return [
            {
                'checkpoint_id': checkpoint.checkpoint_id,
                'checkpoint_name': checkpoint.checkpoint_name,
                'checkpoint_order': checkpoint.checkpoint_order,
            }
            for checkpoint in checkpoints
        ]


class ConditionCategoryCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating condition categories."""

    class Meta:
        model = ConditionCategory
        fields = [
            'condition_category_id', 'building', 'condition_category_name', 'description'
        ]
        read_only_fields = ['condition_category_id']

    def create(self, validated_data):
        # Get the email from the request context
        request = self.context.get('request')
        email, _ = get_warehouse_user_identity(getattr(request, 'user', None))

        validated_data['created_by'] = email
        # Some databases enforce NOT NULL on entity_type without a default.
        # Ensure inserts always include a value.
        validated_data.setdefault('entity_type', 'admin')
        return super().create(validated_data)


class WarehouseInspectionSubmissionResponseInputSerializer(serializers.Serializer):
    """One checklist response inside a submitted inspection."""

    checkpoint = serializers.IntegerField()
    response = serializers.ChoiceField(choices=['yes', 'no'])
    comments = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    photo_token = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate(self, attrs):
        # Proof photos only make sense against a failed checkpoint - the
        # capture UI disables the camera button once "Yes" is selected, but
        # this is the server-side backstop since client-side-only would be
        # trivially bypassable in a compliance app.
        if attrs.get('response') == 'yes' and attrs.get('photo_token'):
            raise serializers.ValidationError(
                'A photo can only be attached to a checklist item answered No.'
            )
        return attrs


class WarehouseInspectionSubmissionCreateSerializer(serializers.ModelSerializer):
    """Creates a completed condition/floor inspection in one request."""

    responses = WarehouseInspectionSubmissionResponseInputSerializer(many=True, write_only=True)

    class Meta:
        model = WarehouseInspectionSubmission
        fields = ['submission_id', 'building', 'condition_category', 'floor', 'notes', 'responses']
        read_only_fields = ['submission_id']

    def validate(self, attrs):
        building = attrs['building']
        condition = attrs['condition_category']
        floor = attrs['floor']
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        user_email, _ = get_warehouse_user_identity(user)
        today = get_warehouse_inspection_day()[0]

        if not building.is_active:
            raise serializers.ValidationError('Selected building is not active.')
        if condition.building_id != building.building_id:
            raise serializers.ValidationError('Selected condition does not belong to the selected building.')
        if floor.building_id != building.building_id:
            raise serializers.ValidationError('Selected floor does not belong to the selected building.')
        if condition.is_deleted:
            raise serializers.ValidationError('Selected condition is not active.')
        if not floor.is_active:
            raise serializers.ValidationError('Selected floor is not active.')

        assignments = (
            WarehouseInspectionFloorAssignment.objects
            .select_related('checkpoint')
            .filter(
                checkpoint__condition_category=condition,
                checkpoint__is_active=True,
                floor=floor,
                is_applicable=True,
            )
        )
        assignment_by_checkpoint = {
            assignment.checkpoint_id: assignment for assignment in assignments
        }
        required_checkpoint_ids = set(assignment_by_checkpoint)
        submitted_checkpoint_ids = [item['checkpoint'] for item in attrs['responses']]
        submitted_checkpoint_id_set = set(submitted_checkpoint_ids)

        if not required_checkpoint_ids:
            raise serializers.ValidationError(
                'No active checklist items are configured for this condition and floor.'
            )
        if len(submitted_checkpoint_ids) != len(submitted_checkpoint_id_set):
            raise serializers.ValidationError('Duplicate checklist responses are not allowed.')

        missing_ids = required_checkpoint_ids - submitted_checkpoint_id_set
        extra_ids = submitted_checkpoint_id_set - required_checkpoint_ids
        if missing_ids:
            raise serializers.ValidationError(
                'Every checklist item for the selected condition and floor must have Yes or No selected.'
            )
        if extra_ids:
            raise serializers.ValidationError(
                'One or more submitted checklist items do not belong to the selected condition and floor.'
            )

        photo_tokens_by_checkpoint = {
            item['checkpoint']: item['photo_token']
            for item in attrs['responses']
            if item.get('photo_token')
        }
        photo_uploads_by_checkpoint = {}
        if photo_tokens_by_checkpoint:
            uploads = WarehouseInspectionResponsePhotoUpload.objects.filter(
                upload_token__in=set(photo_tokens_by_checkpoint.values()),
                consumed=False,
            )
            uploads_by_token = {upload.upload_token: upload for upload in uploads}
            for checkpoint_id, token in photo_tokens_by_checkpoint.items():
                upload = uploads_by_token.get(token)
                if (
                    not upload
                    or upload.checkpoint_id != checkpoint_id
                    or upload.uploaded_by_email.lower() != user_email.lower()
                ):
                    raise serializers.ValidationError(
                        'One or more attached photos could not be found. Please re-attach the photo and submit again.'
                    )
                photo_uploads_by_checkpoint[checkpoint_id] = upload

        attrs['assignment_by_checkpoint'] = assignment_by_checkpoint
        attrs['photo_uploads_by_checkpoint'] = photo_uploads_by_checkpoint
        attrs['inspection_date'] = today
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        responses_data = validated_data.pop('responses')
        assignment_by_checkpoint = validated_data.pop('assignment_by_checkpoint')
        photo_uploads_by_checkpoint = validated_data.pop('photo_uploads_by_checkpoint', {})
        inspection_date = validated_data.pop('inspection_date', get_warehouse_inspection_day()[0])
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        user_email, submitted_by_name = get_warehouse_user_identity(user)
        notes = validated_data.get('notes') or ''

        # One row per (building, condition, floor, day) - unlimited resubmits
        # the same day upsert this same row instead of creating a new one.
        # select_for_update() serializes concurrent resubmits of the same key.
        submission, created = (
            WarehouseInspectionSubmission.objects
            .select_for_update()
            .get_or_create(
                building=validated_data['building'],
                condition_category=validated_data['condition_category'],
                floor=validated_data['floor'],
                inspection_date=inspection_date,
                defaults={
                    'submitted_by_email': user_email,
                    'submitted_by_name': submitted_by_name,
                    'notes': notes,
                },
            )
        )
        if not created:
            submission.submitted_by_email = user_email
            submission.submitted_by_name = submitted_by_name
            submission.notes = notes
            submission.email_to = []
            submission.email_cc = []
            submission.email_sent = False
            submission.email_sent_at = None
            submission.email_error = None
            submission.save()

        # Every individual (re)submission is preserved here, since the
        # submission row above now only ever reflects the latest state.
        event = WarehouseInspectionSubmissionEvent.objects.create(
            submission=submission,
            submitted_by_email=user_email,
            submitted_by_name=submitted_by_name,
            notes=notes,
        )

        responses = []
        for item in responses_data:
            assignment = assignment_by_checkpoint[item['checkpoint']]
            checkpoint = assignment.checkpoint
            photo_upload = photo_uploads_by_checkpoint.get(item['checkpoint'])
            responses.append(
                WarehouseInspectionResponse(
                    submission=submission,
                    event=event,
                    checkpoint=checkpoint,
                    floor=assignment.floor,
                    floor_assignment=assignment,
                    response=item['response'],
                    comments=item.get('comments') or '',
                    photo=photo_upload.photo if photo_upload else None,
                    responsible_email=assignment.responsible_email,
                    internal_department_email=checkpoint.internal_department_email,
                    responded_by_email=user_email,
                    warehouse_floor=assignment.floor.floor_name,
                )
            )
        created_responses = WarehouseInspectionResponse.objects.bulk_create(responses)

        from .tickets import sync_tickets_for_responses
        sync_tickets_for_responses(created_responses)

        if photo_uploads_by_checkpoint:
            WarehouseInspectionResponsePhotoUpload.objects.filter(
                pk__in=[upload.pk for upload in photo_uploads_by_checkpoint.values()]
            ).update(consumed=True)
        return submission


class WarehouseInspectionSubmissionSerializer(serializers.ModelSerializer):
    """Read serializer for completed submissions."""

    building_name = serializers.CharField(source='building.building_name', read_only=True)
    condition_name = serializers.CharField(
        source='condition_category.condition_category_name',
        read_only=True,
    )
    floor_name = serializers.CharField(source='floor.floor_name', read_only=True)
    responses = WarehouseInspectionResponseSerializer(many=True, read_only=True)
    attempt_count = serializers.SerializerMethodField()

    def get_attempt_count(self, obj):
        return obj.events.count()

    class Meta:
        model = WarehouseInspectionSubmission
        fields = [
            'submission_id', 'building', 'building_name',
            'condition_category', 'condition_name', 'floor',
            'floor_name', 'submitted_by_email', 'submitted_by_name', 'notes',
            'submitted_at', 'attempt_count', 'email_to', 'email_cc', 'email_sent',
            'email_sent_at', 'email_error', 'responses'
        ]
        read_only_fields = fields


class WarehouseInspectionSecondSubmissionSerializer(serializers.ModelSerializer):
    """Read serializer for rare second same-day inspections."""

    building_name = serializers.CharField(source='building.building_name', read_only=True)
    condition_name = serializers.CharField(
        source='condition_category.condition_category_name',
        read_only=True,
    )
    floor_name = serializers.CharField(source='floor.floor_name', read_only=True)
    responses = WarehouseInspectionSecondResponseSerializer(many=True, read_only=True)
    inspection_attempt = serializers.SerializerMethodField()

    def get_inspection_attempt(self, obj):
        return 2

    class Meta:
        model = WarehouseInspectionSecondSubmission
        fields = [
            'second_submission_id', 'first_submission', 'building', 'building_name',
            'condition_category', 'condition_name', 'floor',
            'floor_name', 'submitted_by_email', 'submitted_by_name', 'notes',
            'inspection_date', 'submitted_at', 'inspection_attempt',
            'email_to', 'email_cc', 'email_sent', 'email_sent_at',
            'email_error', 'responses'
        ]
        read_only_fields = fields
