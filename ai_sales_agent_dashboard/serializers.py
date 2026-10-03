from rest_framework import serializers

from .models import AgentConversation, ConversationMessage, SalesAgent


class ConversationMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationMessage
        fields = ("id", "role", "content", "timestamp")
        read_only_fields = ("id", "timestamp")


class AgentConversationSerializer(serializers.ModelSerializer):
    messages = ConversationMessageSerializer(many=True, read_only=True)

    class Meta:
        model = AgentConversation
        fields = ("id", "agent", "user", "session_id", "started_at", "ended_at", "messages")
        read_only_fields = ("id", "started_at")


class SalesAgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesAgent
        fields = ("id", "name", "description", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
