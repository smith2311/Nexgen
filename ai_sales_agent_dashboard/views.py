from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import AgentConversation, ConversationMessage, SalesAgent
from .serializers import (
    AgentConversationSerializer,
    ConversationMessageSerializer,
    SalesAgentSerializer,
)


class SalesAgentViewSet(viewsets.ModelViewSet):
    """CRUD endpoints for AI sales agents."""

    queryset = SalesAgent.objects.all()
    serializer_class = SalesAgentSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=["get"])
    def conversations(self, request, pk=None):
        agent = self.get_object()
        conversations = agent.conversations.all()
        serializer = AgentConversationSerializer(conversations, many=True)
        return Response(serializer.data)


class AgentConversationViewSet(viewsets.ModelViewSet):
    """CRUD endpoints for agent conversations."""

    queryset = AgentConversation.objects.select_related("agent").all()
    serializer_class = AgentConversationSerializer
    permission_classes = [permissions.IsAuthenticated]


class ConversationMessageViewSet(viewsets.ModelViewSet):
    """CRUD endpoints for conversation messages."""

    queryset = ConversationMessage.objects.select_related("conversation").all()
    serializer_class = ConversationMessageSerializer
    permission_classes = [permissions.IsAuthenticated]
