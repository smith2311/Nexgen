"""
feedback_views.py
─────────────────────────────────────────────────────────────────────────────
DRF ViewSet for the Feedback / Bug Report system.

Endpoints (mounted at /product_scanning/api/feedback/)
  GET    /                  – list feedback (own for regular users; all for admins)
  POST   /                  – submit new feedback
  GET    /<id>/             – retrieve single ticket + replies
  PATCH  /<id>/             – update (admin: any field; user: description only)
  POST   /<id>/reply/       – post a reply (admin or submitter)
  PATCH  /<id>/status/      – update ticket status (admin only)
  GET    /stats/            – aggregate counts (admin only)
  POST   /<id>/mark_read/   – mark ticket as read by admin
"""

import logging

from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .feedback_notifications import (
    notify_feedback_reply,
    notify_feedback_submitted,
    notify_status_changed,
)
from .models import Feedback, FeedbackReply

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _is_admin(user):
    return user.is_superuser or user.is_staff


# ── Serializers ─────────────────────────────────────────────────────────────

class FeedbackReplySerializer(serializers.ModelSerializer):
    replied_by_name = serializers.SerializerMethodField()
    replied_by_is_admin = serializers.SerializerMethodField()

    class Meta:
        model = FeedbackReply
        fields = [
            "id",
            "message",
            "replied_at",
            "is_internal",
            "replied_by_name",
            "replied_by_is_admin",
        ]

    def get_replied_by_name(self, obj):
        if obj.replied_by:
            return obj.replied_by.get_full_name() or obj.replied_by.username
        return "Support Team"

    def get_replied_by_is_admin(self, obj):
        if obj.replied_by:
            return obj.replied_by.is_superuser or obj.replied_by.is_staff
        return False


class FeedbackSerializer(serializers.ModelSerializer):
    replies = serializers.SerializerMethodField()
    submitter_name = serializers.ReadOnlyField()
    submitter_role_label = serializers.ReadOnlyField()
    submitter_email = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()
    category_display = serializers.SerializerMethodField()
    severity_display = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    module_display = serializers.SerializerMethodField()
    screenshot_url = serializers.SerializerMethodField()

    class Meta:
        model = Feedback
        fields = [
            "id",
            "feedback_id",
            "title",
            "description",
            "category",
            "category_display",
            "severity",
            "severity_display",
            "status",
            "status_display",
            "module",
            "module_display",
            "screenshot_url",
            "submitted_by",
            "submitter_name",
            "submitter_role_label",
            "submitter_email",
            "submitted_at",
            "updated_at",
            "resolved_at",
            "is_read_by_admin",
            "replies",
            "reply_count",
        ]
        read_only_fields = [
            "feedback_id",
            "submitted_by",
            "submitted_at",
            "updated_at",
            "resolved_at",
            "is_read_by_admin",
        ]

    def get_replies(self, obj):
        request = self.context.get("request")
        qs = obj.replies.all()
        if request and not _is_admin(request.user):
            # Regular users don't see internal notes
            qs = qs.filter(is_internal=False)
        return FeedbackReplySerializer(qs, many=True).data

    def get_reply_count(self, obj):
        return obj.replies.filter(is_internal=False).count()

    def get_submitter_email(self, obj):
        request = self.context.get("request")
        if request and _is_admin(request.user) and obj.submitted_by:
            return obj.submitted_by.email
        return None

    def get_category_display(self, obj):
        return obj.get_category_display()

    def get_severity_display(self, obj):
        return obj.get_severity_display()

    def get_status_display(self, obj):
        return obj.get_status_display()

    def get_module_display(self, obj):
        # choices no longer on the field — look up from registry
        from product_scanning.feedback_registry import get_label
        return get_label(obj.module)

    def get_screenshot_url(self, obj):
        if obj.screenshot:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.screenshot.url)
            return obj.screenshot.url
        return None


class FeedbackCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feedback
        fields = ["title", "description", "category", "severity", "module", "screenshot"]


# ── ViewSet ──────────────────────────────────────────────────────────────────

class FeedbackViewSet(viewsets.ModelViewSet):
    """
    CRUD + actions for the Feedback system.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = Feedback.objects.select_related("submitted_by", "assigned_to").prefetch_related("replies")
        if _is_admin(user):
            return qs
        # Regular users only see their own tickets
        return qs.filter(submitted_by=user)

    def get_serializer_class(self):
        if self.action == "create":
            return FeedbackCreateSerializer
        return FeedbackSerializer

    def perform_create(self, serializer):
        feedback = serializer.save(submitted_by=self.request.user)
        # Fire escalation email in background
        try:
            notify_feedback_submitted(feedback)
        except Exception as exc:
            logger.error("notify_feedback_submitted failed: %s", exc)

    def partial_update(self, request, *args, **kwargs):
        feedback = self.get_object()
        user = request.user

        # Non-admins can only update description if ticket is still open
        if not _is_admin(user):
            if feedback.submitted_by != user:
                return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)
            allowed = {"description"}
            disallowed = set(request.data.keys()) - allowed
            if disallowed:
                return Response(
                    {"detail": f"You can only update: {allowed}"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        old_status = feedback.status
        serializer = FeedbackSerializer(feedback, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        # Notify on status change
        if "status" in request.data and request.data["status"] != old_status:
            if updated.status in ("resolved", "closed"):
                updated.resolved_at = timezone.now()
                updated.save(update_fields=["resolved_at"])
            try:
                notify_status_changed(updated, user)
            except Exception as exc:
                logger.error("notify_status_changed failed: %s", exc)

        return Response(FeedbackSerializer(updated, context={"request": request}).data)

    # ── Custom actions ────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="reply")
    def reply(self, request, pk=None):
        """Post a reply to a ticket (admin or submitter)."""
        feedback = self.get_object()
        user = request.user

        # Submitters can reply to own tickets; admins can reply to any
        if not _is_admin(user) and feedback.submitted_by != user:
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        message = request.data.get("message", "").strip()
        if not message:
            return Response({"detail": "Message cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)

        is_internal = bool(request.data.get("is_internal", False)) and _is_admin(user)

        reply_obj = FeedbackReply.objects.create(
            feedback=feedback,
            replied_by=user,
            message=message,
            is_internal=is_internal,
        )

        # If admin replies, move ticket to in_progress automatically (if still open)
        if _is_admin(user) and feedback.status == "open":
            feedback.status = "in_progress"
            feedback.save(update_fields=["status", "updated_at"])

        # Send notification
        if not is_internal:
            try:
                notify_feedback_reply(reply_obj)
            except Exception as exc:
                logger.error("notify_feedback_reply failed: %s", exc)

        return Response(
            FeedbackReplySerializer(reply_obj).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        """Update ticket status (admin only)."""
        if not _is_admin(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        feedback = self.get_object()
        new_status = request.data.get("status")
        valid = [s[0] for s in Feedback.STATUS_CHOICES]
        if new_status not in valid:
            return Response(
                {"detail": f"Invalid status. Choose from: {valid}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = feedback.status
        feedback.status = new_status
        if new_status in ("resolved", "closed") and not feedback.resolved_at:
            feedback.resolved_at = timezone.now()
        feedback.save(update_fields=["status", "resolved_at", "updated_at"])

        if new_status != old_status:
            try:
                notify_status_changed(feedback, request.user)
            except Exception as exc:
                logger.error("notify_status_changed failed: %s", exc)

        return Response(FeedbackSerializer(feedback, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="mark_read")
    def mark_read(self, request, pk=None):
        """Mark ticket as read by admin."""
        if not _is_admin(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        feedback = self.get_object()
        feedback.is_read_by_admin = True
        feedback.save(update_fields=["is_read_by_admin"])
        return Response({"status": "ok"})

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """Aggregate stats for the admin inbox dashboard."""
        if not _is_admin(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        qs = Feedback.objects.all()
        total = qs.count()
        unread = qs.filter(is_read_by_admin=False).count()

        by_status = {}
        for s, _ in Feedback.STATUS_CHOICES:
            by_status[s] = qs.filter(status=s).count()

        by_category = {}
        for c, label in Feedback.CATEGORY_CHOICES:
            cnt = qs.filter(category=c).count()
            if cnt:
                by_category[c] = {"label": label, "count": cnt}

        by_severity = {}
        for sv, label in Feedback.SEVERITY_CHOICES:
            cnt = qs.filter(severity=sv).count()
            if cnt:
                by_severity[sv] = {"label": label, "count": cnt}

        critical_open = qs.filter(severity="critical", status__in=["open", "in_progress"]).count()

        return Response(
            {
                "total": total,
                "unread": unread,
                "by_status": by_status,
                "by_category": by_category,
                "by_severity": by_severity,
                "critical_open": critical_open,
            }
        )
