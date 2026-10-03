from django.urls import include, path

from rest_framework.routers import DefaultRouter

from . import views_frontend as vf
from .views import AgentConversationViewSet, ConversationMessageViewSet, SalesAgentViewSet

app_name = "ai_sales_agent_dashboard"

router = DefaultRouter()
router.register(r"api/agents", SalesAgentViewSet, basename="agent")
router.register(r"api/conversations", AgentConversationViewSet, basename="conversation")
router.register(r"api/messages", ConversationMessageViewSet, basename="message")

urlpatterns = [
    # ── REST API ──────────────────────────────────────────────────────
    path("", include(router.urls)),
    # ── Frontend Dashboard (superuser only) ───────────────────────────
    path("dashboard/", vf.dashboard_home, name="dashboard_home"),
    path("workflow/", vf.workflow_eagle_eye, name="workflow_eagle_eye"),
    path("agents/<int:pk>/", vf.agent_detail, name="agent_detail"),
    path("hospitals/", vf.hospitals_list, name="hospitals_list"),
    path("hospitals/create/", vf.hospital_create, name="hospital_create"),
    path("hospitals/lead/<path:place_id>/", vf.scraped_lead_detail, name="scraped_lead_detail"),
    path("hospitals/<int:pk>/", vf.hospital_detail, name="hospital_detail"),
    path("hospitals/<int:pk>/edit/", vf.hospital_edit, name="hospital_edit"),
    # ── JSON API for interactive UI ───────────────────────────────────
    path("api/agent-quick/<str:workflow_id>/", vf.api_agent_quick, name="api_agent_quick"),
]
