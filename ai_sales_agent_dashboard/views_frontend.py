"""
Frontend views for the AI Sales Agent Dashboard.
Views are accessible to superusers OR users in the 'AI Sales Dashboard' group.
"""

import hashlib
import json
import logging
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

logger = logging.getLogger(__name__)

from .meril_models import (
    AIEnhancedBrief,
    BusinessLead,
    CallInsight,
    CRMContact,
    CSORunLog,
    DiscoveryBrief,
    DiscoveryQueue,
    EmailSequence,
    HubSpotDealMap,
    ICPAccountScore,
    MeetingBooking,
    ProductCatalog,
    Proposal,
    ProspectResearchProfile,
    ScrapedResult,
    WinLossAnalysis,
)
from .models import AgentPhase, AgentRun, HospitalAccount, HospitalAgentActivity, SalesAgent

# Map output/input table names (from seed_agents) → Django model classes
TABLE_MODEL_MAP = {
    "Results": ScrapedResult,
    "Business_Leads": BusinessLead,
    "ICP_Account_Scores": ICPAccountScore,
    "AI_Enhanced_Briefs": AIEnhancedBrief,
    "Discovery_Queue": DiscoveryQueue,
    "Discovery_Briefs": DiscoveryBrief,
    "CSO_Run_Log": CSORunLog,
    "Meeting_Bookings": MeetingBooking,
    "Lead_Scores": None,  # LeadScore table has minimal data
    "CRM_Contacts": CRMContact,
    "Prospect_Research_Profiles": ProspectResearchProfile,
    "HubSpot_Deal_Map": HubSpotDealMap,
    "HubSpot_Contacts": CRMContact,
    "Email_Sequences": EmailSequence,
    "Win_Loss_Analysis": WinLossAnalysis,
    "Proposals": Proposal,
    "Call_Insights": CallInsight,
}

# ICP tier → simple label mapping
ICP_TIER_MAP = {
    "T1_HOT": ("hot", "#ef4444"),
    "T2_WARM": ("warm", "#f59e0b"),
    "T3_OPPORTUNITY": ("warm", "#f59e0b"),
    "T3_WARM": ("warm", "#f59e0b"),
    "T3_DEVELOP": ("warm", "#eab308"),
    "T4_NURTURE": ("cold", "#6b7280"),
}


def _score_to_tier(score):
    """Derive (simple_tier, color, label) purely from a numeric ICP/lead score."""
    score = score or 0
    if score >= 70:
        return "hot", "#ef4444", "HOT"
    elif score >= 40:
        return "warm", "#f59e0b", "WARM"
    return "cold", "#6b7280", "NURTURE"


def _safe_count(queryset):
    """Return the row count, or None when the table can't be counted.

    A real, existing-but-empty table returns 0; a missing or unmapped table
    (e.g. an n8n pipeline table that hasn't been created yet) returns None, so
    callers can show "—" instead of a misleading 0 that hides a dead mapping.
    Templates already treat a None count as "unavailable" (see the table-count
    badges in agent_detail.html, which render only `{% if count is not None %}`).
    """
    try:
        return queryset.count()
    except Exception as e:
        logger.warning("_safe_count failed for %s: %s", getattr(queryset, "model", queryset), e)
        return None


def _safe_query(queryset, default=None):
    """Return queryset, returning empty list if the table doesn't exist yet."""
    if default is None:
        default = []
    try:
        return list(queryset)
    except Exception as e:
        logger.warning("_safe_query failed for %s: %s", getattr(queryset, "model", queryset), e)
        return default


def _results_columns():
    """Return the set of column names currently on the n8n-managed Results table.

    The Results table is rebuilt by the n8n pipeline, so its columns come and
    go between runs (e.g. icp_score / icp_tier / icp_fit_reason only exist once
    ICP scoring has run). Callers use this to select/filter only columns that
    actually exist right now, instead of crashing on "Unknown column". Returns
    an empty set if the table or DB is unavailable.
    """
    from django.db import connections

    try:
        conn = connections["ai_sales_agent_dashboard"]
        with conn.cursor() as cur:
            return {col.name for col in conn.introspection.get_table_description(cur, "Results")}
    except Exception as e:
        logger.warning("_results_columns introspection failed: %s", e)
        return set()


def _results_score_field(cols=None):
    """Pick the best available score column on Results (icp_score → lead_score)."""
    cols = _results_columns() if cols is None else cols
    if "icp_score" in cols:
        return "icp_score"
    return "lead_score"


def _results_as_namespaces(filters=None, order_by=None, limit=None):
    """Fetch Results rows as attribute-accessible objects, tolerant of the
    volatile n8n schema. Only columns that currently exist are SELECTed; every
    model field is still exposed as an attribute (missing columns read as None).
    Fetching full ScrapedResult model instances is unsafe because the model now
    declares icp_score/icp_tier/icp_fit_reason, which the Results table may not
    have at a given moment — so always go through this helper for object access.
    """
    cols = _results_columns()
    model_fields = [f.name for f in ScrapedResult._meta.fields]
    safe = [f for f in model_fields if f in cols] or ["place_id"]
    qs = ScrapedResult.objects.all()
    if filters:
        qs = qs.filter(**filters)
    if order_by:
        qs = qs.order_by(order_by)
    qs = qs.values(*safe)
    if limit:
        qs = qs[:limit]
    try:
        rows = list(qs)
    except Exception as e:
        logger.warning("_results_as_namespaces failed (order_by=%s): %s", order_by, e)
        return []
    return [SimpleNamespace(**{f: r.get(f) for f in model_fields}) for r in rows]


def _fetch_result_row(place_id):
    """Fetch a single Results row as an attribute-accessible object (or None)."""
    rows = _results_as_namespaces(filters={"place_id": place_id}, limit=1)
    return rows[0] if rows else None


def _fetch_result_raw(place_id):
    """Fetch the full Results row as a {column: value} dict (all live columns).

    Unlike _fetch_result_row (limited to columns declared on the ScrapedResult
    model), this returns every column the n8n Results table currently has, so
    the lead-detail page can surface the intelligence fields that live inline on
    Results — MEDDIC, BANT, qualification reasons, discovery notes — which are
    not declared on the model. Returns {} if the row/table/DB is unavailable.
    """
    from django.db import connections

    try:
        conn = connections["ai_sales_agent_dashboard"]
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM `Results` WHERE place_id = %s LIMIT 1", [place_id])
            row = cur.fetchone()
            if not row:
                return {}
            names = [c[0] for c in cur.description]
        return dict(zip(names, row))
    except Exception as e:
        logger.warning("_fetch_result_raw failed for %s: %s", place_id, e)
        return {}


# Results columns grouped by pipeline stage, for the lead-detail "all fields" panel.
RESULT_FIELD_GROUPS = [
    ("Identity & Contact", ["xid", "action", "status", "title", "name", "firstname",
        "lastname", "email", "phone", "website", "clean_url", "address", "postal_code",
        "type", "types", "rating", "provider_id"]),
    ("Lead Scoring", ["lead_score", "lead_tier", "firmographic_score", "behavioral_score",
        "engagement_score", "negative_score", "bant_score", "intent_score", "intent_tier",
        "enrichment_status", "score_updated_at"]),
    ("ICP & Qualification", ["icp_tier", "icp_score", "icp_fit_reason", "qualification_status",
        "qual_stage", "qual_reason", "disqual_reason", "qual_version", "mql_stage",
        "buyer_persona", "qualified_at"]),
    ("BANT", ["bant_budget", "bant_authority", "bant_need", "bant_timeline"]),
    ("MEDDIC", ["meddic_metrics", "meddic_economic_buyer", "meddic_decision_criteria",
        "meddic_decision_process", "meddic_identified_pain", "meddic_champion"]),
    ("Discovery Prep", ["discovery_notes", "discovery_brief_full", "discovery_company_snapshot",
        "discovery_contact_intel", "discovery_pain_hypotheses", "discovery_talking_points",
        "discovery_questions", "discovery_objections", "discovery_red_flags"]),
    ("Research & Insight", ["prospect_research_status", "research_status", "insight_status",
        "personalisation_ready", "intent_flagged_at"]),
    ("Calls", ["call_status", "call_count", "last_call_outcome", "last_call_score",
        "call_prep_version"]),
    ("Email Outreach", ["email_outreach_status", "email_sequence_step", "email_subject",
        "email_open_count", "email_reply_received", "email_campaign_version"]),
    ("BDR Outreach", ["bdr_region", "bdr_language_code", "bdr_outreach_status",
        "bdr_sequence_step", "bdr_email_subject", "bdr_reply_received", "bdr_campaign_version"]),
    ("Products", ["recommended_products", "total_matched", "sales_angle", "product_fit_score"]),
]


def _is_populated(value):
    """True when a Results cell carries a real value (not None/empty/zero)."""
    if value is None:
        return False
    s = str(value).strip()
    return s not in ("", "0")


def _grouped_result_fields(raw):
    """Return [(group, [(label, value), …]), …] of every populated Results column.

    Columns are bucketed by RESULT_FIELD_GROUPS; any populated column not listed
    there falls into a trailing "Other" group so nothing is silently dropped.
    """
    if not raw:
        return []
    used, out = set(), []
    for group, cols in RESULT_FIELD_GROUPS:
        items = []
        for c in cols:
            used.add(c)
            v = raw.get(c)
            if _is_populated(v):
                items.append((c.replace("_", " ").title(), v))
        if items:
            out.append((group, items))
    other = [(c.replace("_", " ").title(), v) for c, v in raw.items()
             if c not in used and _is_populated(v)]
    if other:
        out.append(("Other", other))
    return out


superuser_required = user_passes_test(lambda u: u.is_superuser, login_url="/")


def _has_dashboard_access(user):
    """Allow superusers or members of the 'AI Sales Dashboard' group."""
    return user.is_superuser or user.groups.filter(name="AI Sales Dashboard").exists()


def dashboard_access_required(view_func):
    """Decorator: restrict to superusers or AI Sales Dashboard group members."""
    return login_required(
        user_passes_test(_has_dashboard_access, login_url="/")(view_func),
        login_url="/",
    )


# Keep old name as alias so nothing breaks if referenced elsewhere
superuser_only = dashboard_access_required


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Home
# ─────────────────────────────────────────────────────────────────────────────


@superuser_only
def dashboard_home(request):
    agents = SalesAgent.objects.all().order_by("workflow_number")

    # Phase groupings
    phase_groups = {}
    for phase_key, phase_label in AgentPhase.choices:
        phase_agents = agents.filter(phase=phase_key)
        if phase_agents.exists():
            phase_groups[phase_key] = {
                "label": phase_label,
                "agents": list(phase_agents),
                "count": phase_agents.count(),
            }

    # Agent stats
    total_agents = agents.count()
    active_agents = agents.filter(is_active=True).count()
    # ICP scoring now lives inline on Results (ICP_Account_Scores table retired).
    # The n8n pipeline adds/drops icp_score between runs, so pick whichever score
    # column currently exists and build score filters against it dynamically.
    result_cols = _results_columns()
    score_field = _results_score_field(result_cols)
    has_icp_tier = "icp_tier" in result_cols

    def _score_filter(**bounds):
        """Build a count query filtering on the live score column."""
        flt = {f"{score_field}__{op}": val for op, val in bounds.items()}
        return _safe_count(ScrapedResult.objects.filter(**flt))

    total_hospitals = _safe_count(ScrapedResult.objects)
    hot_leads = _score_filter(gte=70)

    # ── Live pipeline metrics from meril data ─────────────────────────────
    total_scraped = _safe_count(ScrapedResult.objects)
    total_leads = total_scraped
    total_icp_scored = _score_filter(gt=0)
    # "AI Briefs" = leads with AI-generated qualification intel (buyer persona,
    # MEDDIC, discovery notes), which the pipeline writes inline on the Results
    # table. Count Results rows that carry a generated buyer_persona.
    total_ai_briefs = _safe_count(
        ScrapedResult.objects.exclude(buyer_persona__isnull=True).exclude(buyer_persona="")
    )
    total_in_queue = _safe_count(DiscoveryQueue.objects)
    total_crm_contacts = _safe_count(CRMContact.objects)
    total_deals = _safe_count(HubSpotDealMap.objects)
    total_meetings = _safe_count(MeetingBooking.objects)
    total_proposals = _safe_count(Proposal.objects)
    total_email_seqs = _safe_count(EmailSequence.objects)
    total_cso_runs = _safe_count(CSORunLog.objects)

    # ICP distribution — score-based (consistent with hospitals_list), from Results
    icp_hot = _score_filter(gte=70)
    icp_warm = _score_filter(gte=40, lt=70)
    icp_cold = _score_filter(gt=0, lt=40)
    icp_dev = 0  # kept for template compat
    icp_nurture = icp_cold  # alias for backwards compatibility

    # Also build full dynamic distribution for the tier bar (from Results.icp_tier)
    icp_tier_dist = []
    if has_icp_tier:
        try:
            icp_tier_dist = [
                {"account_tier": row["icp_tier"], "tier_label": row["icp_tier"], "cnt": row["cnt"]}
                for row in (
                    ScrapedResult.objects.filter(icp_tier__isnull=False)
                    .exclude(icp_tier="")
                    .values("icp_tier")
                    .annotate(cnt=Count("place_id"))
                    .order_by("-cnt")
                )
            ]
        except Exception:
            icp_tier_dist = []

    # Top ICP scored accounts (from Results) — shimmed to the template's ICP shape
    _top_fields = ["place_id", "title", "name", score_field] + (["icp_tier"] if has_icp_tier else [])
    top_icp_accounts = [
        SimpleNamespace(
            place_id=r["place_id"],
            business_name=(r["title"] or r["name"] or r["place_id"]),
            icp_fit_score=r.get(score_field) or 0,
            account_tier=(r.get("icp_tier") or "").strip(),
            tier_label=_score_to_tier(r.get(score_field) or 0)[2],
        )
        for r in _safe_query(
            ScrapedResult.objects.filter(**{f"{score_field}__gt": 0})
            .order_by(f"-{score_field}")
            .values(*_top_fields)[:6]
        )
    ]

    # Recent AI enhanced briefs
    # Recent "briefed" leads, read safely from the volatile Results schema.
    recent_briefs = _results_as_namespaces(
        filters={"buyer_persona__isnull": False}, order_by="-icp_score", limit=4
    )

    # Recent CSO runs
    recent_cso_runs = _safe_query(CSORunLog.objects.order_by("-created_at")[:5])

    # Upcoming / recent meetings
    recent_meetings = _safe_query(MeetingBooking.objects.order_by("-created_at")[:5])

    # Discovery queue snapshot
    dq_snapshot = _safe_query(DiscoveryQueue.objects.order_by("-created_at")[:6])

    # Qualification pipeline (from Results table)
    qual_status = {}
    try:
        for r in ScrapedResult.objects.values("qualification_status").annotate(cnt=Count("place_id")):
            if r["qualification_status"]:
                qual_status[r["qualification_status"]] = r["cnt"]
    except Exception:
        pass

    # Recent runs (Django model)
    recent_runs = AgentRun.objects.select_related("agent").order_by("-started_at")[:10]

    # Phase summary for eagle-eye
    phase_summary = []
    phase_order = [
        AgentPhase.ORCHESTRATOR,
        AgentPhase.DISCOVERY,
        AgentPhase.INTELLIGENCE,
        AgentPhase.OUTREACH,
        AgentPhase.INFRASTRUCTURE,
        AgentPhase.COMMS,
        AgentPhase.CONTENT,
    ]
    phase_colors = {
        "orchestrator": "#7c3aed",
        "discovery": "#2563eb",
        "intelligence": "#0891b2",
        "outreach": "#059669",
        "infrastructure": "#d97706",
        "comms": "#db2777",
        "content": "#7c3aed",
    }
    phase_icons = {
        "orchestrator": "🎯",
        "discovery": "🔍",
        "intelligence": "🧠",
        "outreach": "📤",
        "infrastructure": "⚙️",
        "comms": "💬",
        "content": "📝",
    }
    for phase_key in phase_order:
        phase_agents = agents.filter(phase=phase_key)
        if phase_agents.exists():
            phase_summary.append(
                {
                    "key": phase_key,
                    "label": dict(AgentPhase.choices).get(phase_key, phase_key),
                    "color": phase_colors.get(phase_key, "#6b7280"),
                    "icon": phase_icons.get(phase_key, "🤖"),
                    "agents": list(phase_agents),
                    "count": phase_agents.count(),
                }
            )

    context = {
        "agents": agents,
        "phase_summary": phase_summary,
        "total_agents": total_agents,
        "active_agents": active_agents,
        "total_hospitals": total_hospitals,
        "hot_leads": hot_leads,
        "recent_runs": recent_runs,
        # Pipeline live data
        "total_scraped": total_scraped,
        "total_leads": total_leads,
        "total_icp_scored": total_icp_scored,
        "total_ai_briefs": total_ai_briefs,
        "total_in_queue": total_in_queue,
        "total_crm_contacts": total_crm_contacts,
        "total_deals": total_deals,
        "total_meetings": total_meetings,
        "total_proposals": total_proposals,
        "total_email_seqs": total_email_seqs,
        "total_cso_runs": total_cso_runs,
        # ICP breakdown
        "icp_hot": icp_hot,
        "icp_warm": icp_warm,
        "icp_cold": icp_cold,
        "icp_dev": icp_dev,
        "icp_nurture": icp_nurture,
        "icp_tier_dist": icp_tier_dist,
        # Rich data
        "top_icp_accounts": top_icp_accounts,
        "recent_briefs": recent_briefs,
        "recent_cso_runs": recent_cso_runs,
        "recent_meetings": recent_meetings,
        "dq_snapshot": dq_snapshot,
        "qual_status": qual_status,
        "page_title": "AI Sales Agent Intelligence Hub",
    }
    return render(request, "ai_sales_agent_dashboard/dashboard_home.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Eagle-Eye Workflow View
# ─────────────────────────────────────────────────────────────────────────────


@superuser_only
def workflow_eagle_eye(request):
    agents = SalesAgent.objects.all().order_by("workflow_number")

    phase_colors = {
        "orchestrator": "#7c3aed",
        "discovery": "#2563eb",
        "intelligence": "#0891b2",
        "outreach": "#059669",
        "infrastructure": "#d97706",
        "comms": "#db2777",
        "content": "#9333ea",
    }

    # Build node data for the interactive graph
    nodes_data = []
    for agent in agents:
        nodes_data.append(
            {
                "id": agent.workflow_id,
                "label": agent.short_name or agent.name,
                "fullName": agent.name,
                "phase": agent.phase,
                "phaseLabel": agent.phase_display,
                "color": phase_colors.get(agent.phase, "#6b7280"),
                "icon": agent.icon,
                "nodeCount": agent.node_count,
                "complexity": agent.complexity_score,
                "workflowNumber": agent.workflow_number,
                "reportsTo": agent.reports_to.workflow_id if agent.reports_to else None,
                "pk": agent.pk,
            }
        )

    # Build edges (reports_to relationships)
    edges_data = []
    for agent in agents:
        if agent.reports_to:
            edges_data.append(
                {
                    "from": agent.workflow_id,
                    "to": agent.reports_to.workflow_id,
                }
            )

    # Pipeline flow
    pipeline_phases = [
        {
            "phase": "orchestrator",
            "label": "🎯 Master",
            "wfs": ["WF00"],
            "color": "#7c3aed",
        },
        {
            "phase": "discovery",
            "label": "🔍 Phase 1 — Discovery",
            "wfs": ["WF01", "WF02", "WF03", "WF04", "WF05", "WF06", "WF07"],
            "color": "#2563eb",
        },
        {
            "phase": "intelligence",
            "label": "🧠 Phase 2 — Research & Intelligence",
            "wfs": ["WF08", "WF09", "WF10", "WF11", "WF12", "WF13"],
            "color": "#0891b2",
        },
        {
            "phase": "outreach",
            "label": "📤 Phase 3 — Outreach",
            "wfs": ["WF14a", "WF14b", "WF15", "WF16", "WF17"],
            "color": "#059669",
        },
        {
            "phase": "infrastructure",
            "label": "⚙️ Infrastructure",
            "wfs": ["WF18", "WF19", "WF20", "WF21", "WF25"],
            "color": "#d97706",
        },
        {
            "phase": "comms",
            "label": "💬 Phase 4 — Comms & Agents",
            "wfs": ["WF22", "WF23", "WF24", "WF26", "WF27", "WF28"],
            "color": "#db2777",
        },
        {
            "phase": "content",
            "label": "📝 Phase 5 — Content & Closing",
            "wfs": ["WF29", "WF30", "WF31", "WF32", "WF33", "WF34"],
            "color": "#9333ea",
        },
    ]

    wf_lookup = {a.workflow_id: a for a in agents}

    context = {
        "agents": agents,
        "nodes_data_json": json.dumps(nodes_data),
        "edges_data_json": json.dumps(edges_data),
        "pipeline_phases": pipeline_phases,
        "wf_lookup": wf_lookup,
        "page_title": "Eagle-Eye Workflow Map",
    }
    return render(request, "ai_sales_agent_dashboard/workflow_eagle_eye.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Detail
# ─────────────────────────────────────────────────────────────────────────────


@superuser_only
def agent_detail(request, pk):
    agent = get_object_or_404(SalesAgent, pk=pk)
    runs = agent.runs.order_by("-started_at")[:20]
    hospital_activities = agent.hospital_activities.select_related("hospital").order_by("-occurred_at")[:20]
    sub_agents = agent.sub_agents.all()

    # Run stats
    total_runs = agent.runs.count()
    successful_runs = agent.runs.filter(status="success").count()
    failed_runs = agent.runs.filter(status="failed").count()
    success_rate = round((successful_runs / total_runs * 100) if total_runs > 0 else 0)

    # Related agents (same phase)
    related_agents = SalesAgent.objects.filter(phase=agent.phase).exclude(pk=agent.pk)

    phase_colors = {
        "orchestrator": "#7c3aed",
        "discovery": "#2563eb",
        "intelligence": "#0891b2",
        "outreach": "#059669",
        "infrastructure": "#d97706",
        "comms": "#db2777",
        "content": "#9333ea",
    }

    # ── Table data with live record counts ────────────────────────
    def _table_info(tables):
        result = []
        for tbl in tables or []:
            model_class = TABLE_MODEL_MAP.get(tbl)
            count = _safe_count(model_class.objects) if model_class else None
            result.append({"name": tbl, "count": count})
        return result

    input_table_data = _table_info(agent.input_tables)
    output_table_data = _table_info(agent.output_tables)

    # ── Live pipeline sample data for this agent ───────────────────
    wf = agent.workflow_id or ""
    agent_pipeline_data = {}

    if wf == "WF00":
        agent_pipeline_data["cso_runs"] = _safe_query(CSORunLog.objects.order_by("-created_at")[:8])

    elif wf in ("WF01", "WF02", "WF03", "WF04", "WF05", "WF06", "WF07"):
        agent_pipeline_data["scraped_total"] = _safe_count(ScrapedResult.objects)
        # Use the schema-tolerant fetch (full-object fetch would crash when the
        # volatile icp_* columns are absent from the Results table).
        agent_pipeline_data["recent_results"] = _results_as_namespaces(order_by="-lead_score", limit=8)

    elif wf in ("WF08", "WF09"):
        # ICP scoring now lives inline on Results (ICP_Account_Scores table retired);
        # use whichever score column the volatile n8n schema currently exposes.
        _sf = _results_score_field()
        agent_pipeline_data["icp_total"] = _safe_count(ScrapedResult.objects.filter(**{f"{_sf}__gt": 0}))
        agent_pipeline_data["recent_icp"] = [
            SimpleNamespace(
                place_id=r["place_id"],
                business_name=(r["title"] or r["name"] or r["place_id"]),
                icp_fit_score=r.get(_sf) or 0,
                tier_label=_score_to_tier(r.get(_sf) or 0)[2],
            )
            for r in _safe_query(
                ScrapedResult.objects.filter(**{f"{_sf}__gt": 0})
                .order_by(f"-{_sf}")
                .values("place_id", "title", "name", _sf)[:8]
            )
        ]

    elif wf in ("WF10", "WF11", "WF12", "WF13"):
        agent_pipeline_data["briefs_total"] = _safe_count(AIEnhancedBrief.objects)
        agent_pipeline_data["recent_briefs"] = _safe_query(AIEnhancedBrief.objects.order_by("-ai_priority_score")[:8])

    elif wf == "WF19":
        agent_pipeline_data["contacts_total"] = _safe_count(CRMContact.objects)
        agent_pipeline_data["deals_total"] = _safe_count(HubSpotDealMap.objects)
        agent_pipeline_data["recent_deals"] = _safe_query(HubSpotDealMap.objects.order_by("-created_at")[:8])

    elif wf in ("WF26", "WF27", "WF28"):
        agent_pipeline_data["meetings_total"] = _safe_count(MeetingBooking.objects)
        agent_pipeline_data["recent_meetings"] = _safe_query(MeetingBooking.objects.order_by("-created_at")[:8])

    elif wf == "WF30":
        agent_pipeline_data["email_seqs_total"] = _safe_count(EmailSequence.objects)

    elif wf == "WF31":
        agent_pipeline_data["proposals_total"] = _safe_count(Proposal.objects)

    elif wf == "WF33":
        agent_pipeline_data["contacts_total"] = _safe_count(CRMContact.objects)
        agent_pipeline_data["recent_contacts"] = _safe_query(CRMContact.objects.order_by("-created_at")[:8])

    context = {
        "agent": agent,
        "runs": runs,
        "hospital_activities": hospital_activities,
        "sub_agents": sub_agents,
        "total_runs": total_runs,
        "successful_runs": successful_runs,
        "failed_runs": failed_runs,
        "success_rate": success_rate,
        "related_agents": related_agents,
        "phase_color": phase_colors.get(agent.phase, "#6b7280"),
        "input_table_data": input_table_data,
        "output_table_data": output_table_data,
        "agent_pipeline_data": agent_pipeline_data,
        "page_title": f"{agent.icon} {agent.short_name or agent.name}",
    }
    return render(request, "ai_sales_agent_dashboard/agent_detail.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Hospital Accounts
# ─────────────────────────────────────────────────────────────────────────────


@superuser_only
def hospitals_list(request):
    qs = HospitalAccount.objects.all()

    # Filters
    tier_filter = request.GET.get("tier", "")
    status_filter = request.GET.get("status", "")
    search = request.GET.get("q", "")
    sort_by = request.GET.get("sort", "score_desc")  # score_desc|score_asc|name|city
    type_filter = request.GET.get("type_f", "")  # hospital type from scraped data
    score_min = request.GET.get("score_min", "")  # minimum ICP fit score (from ICPAccountScore)
    lead_score_min = request.GET.get("lead_score_min", "")  # minimum CRM lead score (from HubSpotDealMap)

    if tier_filter:
        qs = qs.filter(icp_tier=tier_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if search:
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(contact_name__icontains=search)
            | Q(contact_email__icontains=search)
            | Q(city__icontains=search)
        )

    # Sort CRM accounts
    _crm_sort_map = {
        "score_asc": "lead_score",
        "name": "name",
        "city": "city",
        "score_desc": "-lead_score",
    }
    qs = qs.order_by(_crm_sort_map.get(sort_by, "-lead_score"), "name")

    # Stats
    total = HospitalAccount.objects.count()
    qualified = HospitalAccount.objects.filter(status="sql").count()

    # ── ICP Pipeline Leads (from meril data) ──────────────────────

    # Build scraped info map from Results table (always available, query separately).
    # Only SELECT columns that currently exist — the n8n pipeline adds/drops the
    # ICP scoring columns between runs, so they may be absent at any moment.
    result_cols = _results_columns()
    base_fields = ["place_id", "address", "type", "phone", "email", "title", "name", "lead_score"]
    # Optional columns (present only at certain pipeline stages)
    optional_fields = ["firmographic_score", "behavioral_score", "icp_score", "icp_tier", "icp_fit_reason", "bant_score"]
    values_fields = [f for f in base_fields if f in result_cols] or base_fields
    values_fields += [f for f in optional_fields if f in result_cols]

    scraped_map = {}
    all_types = []
    try:
        for sr in ScrapedResult.objects.filter(place_id__isnull=False).values(*values_fields):
            if sr["place_id"] and sr["place_id"] not in scraped_map:
                scraped_map[sr["place_id"]] = sr
        all_types = sorted({sr["type"].strip() for sr in scraped_map.values() if sr.get("type") and sr["type"].strip()})
    except Exception as e:
        logger.warning("hospitals_list: ScrapedResult query failed: %s", e)

    icp_hospital_leads = []

    # ── Build leads directly from the Results table ──────────────────────
    # The standalone ICP_Account_Scores and HubSpot_Deal_Map tables were retired
    # by the n8n pipeline; ICP scoring is now stored inline on Results
    # (icp_score / icp_tier / icp_fit_reason), so read those columns directly.
    for place_id_key, sr in scraped_map.items():
        sr_name = ((sr.get("title") or "") or (sr.get("name") or "")).strip() or place_id_key
        if search and search.lower() not in sr_name.lower():
            continue

        # ICP fit score is the primary signal; fall back to legacy lead_score.
        score = int(sr.get("icp_score") or sr.get("lead_score") or 0)
        crm_score = int(sr.get("bant_score") or 0)
        hosp_type = (sr.get("type") or "").strip()
        tier_simple, tier_col, tier_lbl = _score_to_tier(score)

        if type_filter and hosp_type.lower() != type_filter.lower():
            continue
        if tier_filter and tier_simple != tier_filter:
            continue
        if score_min:
            try:
                if score < int(score_min):
                    continue
            except ValueError:
                pass
        if lead_score_min:
            try:
                if crm_score < int(lead_score_min):
                    continue
            except ValueError:
                pass

        address = sr.get("address", "") or ""
        city = address.split(",")[-2].strip() if address.count(",") >= 2 else ""

        icp_hospital_leads.append(
            SimpleNamespace(
                pk=None,
                place_id=place_id_key,
                name=sr_name,
                icp_tier=tier_simple,
                account_tier=(sr.get("icp_tier") or "").strip(),
                tier_label=tier_lbl,
                tier_color=tier_col,
                lead_score=score,
                crm_score=crm_score,
                contact_name="",
                contact_email=sr.get("email", "") or "",
                contact_phone=sr.get("phone", "") or "",
                city=city,
                state="",
                speciality=hosp_type,
                bed_count=None,
                status="Discovery",
                assigned_rep="",
                recommended_action=(sr.get("icp_fit_reason") or "").strip(),
                pipeline_stage_color=tier_col,
                fit_score=score,
                firmographic=sr.get("firmographic_score") or 0,
                behavioral=sr.get("behavioral_score") or 0,
            )
        )

    # Sort
    _sort_key_map = {
        "score_asc": lambda x: x.fit_score,
        "name": lambda x: (x.name or "").lower(),
        "city": lambda x: (x.city or "").lower(),
        "score_desc": lambda x: x.fit_score,
    }
    reverse_sort = sort_by != "score_asc" and sort_by not in ("name", "city")
    icp_hospital_leads.sort(
        key=_sort_key_map.get(sort_by, lambda x: x.fit_score),
        reverse=reverse_sort,
    )

    icp_total = len(icp_hospital_leads)
    icp_hot_count = sum(1 for lead in icp_hospital_leads if lead.icp_tier == "hot")
    icp_warm_count = sum(1 for lead in icp_hospital_leads if lead.icp_tier == "warm")
    icp_cold_count = sum(1 for lead in icp_hospital_leads if lead.icp_tier == "cold")

    # CRM tier counts (also score-based)
    hot = sum(1 for h in qs if (h.lead_score or 0) >= 70)
    warm = sum(1 for h in qs if 40 <= (h.lead_score or 0) < 70)
    cold = sum(1 for h in qs if (h.lead_score or 0) < 40)

    active_filter_count = sum(
        bool(f) for f in [tier_filter, status_filter, type_filter, score_min, lead_score_min, search]
    )

    context = {
        "hospitals": qs,
        "total": total,
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "qualified": qualified,
        # ICP pipeline leads
        "icp_hospital_leads": icp_hospital_leads,
        "icp_total": icp_total,
        "icp_hot_count": icp_hot_count,
        "icp_warm_count": icp_warm_count,
        "icp_cold_count": icp_cold_count,
        # Filter state
        "tier_filter": tier_filter,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "sort_by": sort_by,
        "score_min": score_min,
        "lead_score_min": lead_score_min,
        "search": search,
        "active_filter_count": active_filter_count,
        # Choices for dropdowns
        "status_choices": HospitalAccount.AccountStatus.choices,
        "tier_choices": HospitalAccount.IcpTier.choices,
        "hospital_type_choices": all_types,
        "page_title": "Hospital Accounts",
    }
    return render(request, "ai_sales_agent_dashboard/hospitals_list.html", context)


@superuser_only
def hospital_create(request):
    if request.method == "POST":
        data = request.POST
        hospital = HospitalAccount.objects.create(
            name=data.get("name", ""),
            website=data.get("website", ""),
            contact_name=data.get("contact_name", ""),
            contact_email=data.get("contact_email", ""),
            contact_phone=data.get("contact_phone", ""),
            contact_role=data.get("contact_role", ""),
            city=data.get("city", ""),
            state=data.get("state", ""),
            country=data.get("country", "India"),
            postal_code=data.get("postal_code", ""),
            bed_count=int(data["bed_count"]) if data.get("bed_count") else None,
            speciality=data.get("speciality", ""),
            hospital_type=data.get("hospital_type", ""),
            icp_tier=data.get("icp_tier", "cold"),
            status=data.get("status", "raw"),
            lead_score=int(data.get("lead_score", 0)),
            notes=data.get("notes", ""),
            assigned_rep=data.get("assigned_rep", ""),
        )
        # Auto-generate hospital code
        if not hospital.hospital_code:
            hospital.hospital_code = f"HOSP{hospital.pk:05d}"
            hospital.save(update_fields=["hospital_code"])

        messages.success(request, f'Hospital account "{hospital.name}" created successfully.')
        return redirect("ai_sales_agent_dashboard:hospital_detail", pk=hospital.pk)

    HOSPITAL_TYPE_CHOICES = [
        ("private", "Private"),
        ("government", "Government"),
        ("trust", "Trust / NGO"),
        ("corporate", "Corporate Chain"),
        ("teaching", "Teaching Hospital"),
        ("specialty", "Specialty Clinic"),
    ]
    # Blank form placeholder so template can use form.field.value uniformly
    form = SimpleNamespace(
        name=SimpleNamespace(value="", errors=[]),
        hospital_code=SimpleNamespace(value="", errors=[]),
        website=SimpleNamespace(value="", errors=[]),
        hospital_type=SimpleNamespace(value="", errors=[]),
        speciality=SimpleNamespace(value="", errors=[]),
        bed_count=SimpleNamespace(value="", errors=[]),
        city=SimpleNamespace(value="", errors=[]),
        state=SimpleNamespace(value="", errors=[]),
        country=SimpleNamespace(value="India", errors=[]),
        postal_code=SimpleNamespace(value="", errors=[]),
        contact_name=SimpleNamespace(value="", errors=[]),
        contact_role=SimpleNamespace(value="", errors=[]),
        contact_email=SimpleNamespace(value="", errors=[]),
        contact_phone=SimpleNamespace(value="", errors=[]),
        status=SimpleNamespace(value="raw", errors=[]),
        icp_tier=SimpleNamespace(value="cold", errors=[]),
        lead_score=SimpleNamespace(value=0, errors=[]),
        deal_value=SimpleNamespace(value="", errors=[]),
        assigned_rep=SimpleNamespace(value="", errors=[]),
        assigned_channel=SimpleNamespace(value="", errors=[]),
        next_action=SimpleNamespace(value="", errors=[]),
        next_action_date=SimpleNamespace(value="", errors=[]),
        hubspot_id=SimpleNamespace(value="", errors=[]),
        notes=SimpleNamespace(value="", errors=[]),
        non_field_errors=lambda: [],
    )
    context = {
        "form": form,
        "status_choices": HospitalAccount.AccountStatus.choices,
        "tier_choices": HospitalAccount.IcpTier.choices,
        "hospital_type_choices": HOSPITAL_TYPE_CHOICES,
        "page_title": "Create Hospital Account",
    }
    return render(request, "ai_sales_agent_dashboard/hospital_form.html", context)


@superuser_only
def hospital_detail(request, pk):
    hospital = get_object_or_404(HospitalAccount, pk=pk)
    activities = hospital.agent_activities.select_related("agent").order_by("-occurred_at")
    runs = hospital.agent_runs.select_related("agent").order_by("-started_at")[:20]

    # Agents that have worked on this hospital
    involved_agents = SalesAgent.objects.filter(hospital_activities__hospital=hospital).distinct()

    # Activity timeline by agent
    agent_timeline = {}
    for act in activities:
        wf_id = act.agent.workflow_id
        if wf_id not in agent_timeline:
            agent_timeline[wf_id] = {
                "agent": act.agent,
                "activities": [],
            }
        agent_timeline[wf_id]["activities"].append(act)

    # Pipeline stage progression
    stage_order = ["raw", "mql", "sal", "sql", "demo", "proposal", "negotiation", "closed_won"]
    current_stage_idx = stage_order.index(hospital.status) if hospital.status in stage_order else 0

    context = {
        "hospital": hospital,
        "activities": activities[:50],
        "runs": runs,
        "involved_agents": involved_agents,
        "agent_timeline": agent_timeline,
        "stage_order": stage_order,
        "current_stage_idx": current_stage_idx,
        "activity_type_choices": HospitalAgentActivity.ActivityType.choices,
        "page_title": hospital.name,
    }
    return render(request, "ai_sales_agent_dashboard/hospital_detail.html", context)


@superuser_only
def hospital_edit(request, pk):
    hospital = get_object_or_404(HospitalAccount, pk=pk)
    if request.method == "POST":
        data = request.POST
        hospital.name = data.get("name", hospital.name)
        hospital.website = data.get("website", hospital.website)
        hospital.contact_name = data.get("contact_name", hospital.contact_name)
        hospital.contact_email = data.get("contact_email", hospital.contact_email)
        hospital.contact_phone = data.get("contact_phone", hospital.contact_phone)
        hospital.contact_role = data.get("contact_role", hospital.contact_role)
        hospital.city = data.get("city", hospital.city)
        hospital.state = data.get("state", hospital.state)
        hospital.country = data.get("country", hospital.country)
        hospital.postal_code = data.get("postal_code", hospital.postal_code)
        hospital.bed_count = int(data["bed_count"]) if data.get("bed_count") else hospital.bed_count
        hospital.speciality = data.get("speciality", hospital.speciality)
        hospital.hospital_type = data.get("hospital_type", hospital.hospital_type)
        hospital.icp_tier = data.get("icp_tier", hospital.icp_tier)
        hospital.status = data.get("status", hospital.status)
        hospital.lead_score = int(data.get("lead_score", hospital.lead_score))
        hospital.notes = data.get("notes", hospital.notes)
        hospital.assigned_rep = data.get("assigned_rep", hospital.assigned_rep)
        hospital.next_action = data.get("next_action", hospital.next_action)
        hospital.save()
        messages.success(request, "Hospital account updated.")
        return redirect("ai_sales_agent_dashboard:hospital_detail", pk=hospital.pk)

    HOSPITAL_TYPE_CHOICES = [
        ("private", "Private"),
        ("government", "Government"),
        ("trust", "Trust / NGO"),
        ("corporate", "Corporate Chain"),
        ("teaching", "Teaching Hospital"),
        ("specialty", "Specialty Clinic"),
    ]
    form = SimpleNamespace(
        name=SimpleNamespace(value=hospital.name, errors=[]),
        hospital_code=SimpleNamespace(value=hospital.hospital_code, errors=[]),
        website=SimpleNamespace(value=hospital.website, errors=[]),
        hospital_type=SimpleNamespace(value=hospital.hospital_type, errors=[]),
        speciality=SimpleNamespace(value=hospital.speciality, errors=[]),
        bed_count=SimpleNamespace(value=hospital.bed_count or "", errors=[]),
        city=SimpleNamespace(value=hospital.city, errors=[]),
        state=SimpleNamespace(value=hospital.state, errors=[]),
        country=SimpleNamespace(value=hospital.country, errors=[]),
        postal_code=SimpleNamespace(value=hospital.postal_code, errors=[]),
        contact_name=SimpleNamespace(value=hospital.contact_name, errors=[]),
        contact_role=SimpleNamespace(value=hospital.contact_role, errors=[]),
        contact_email=SimpleNamespace(value=hospital.contact_email, errors=[]),
        contact_phone=SimpleNamespace(value=hospital.contact_phone, errors=[]),
        status=SimpleNamespace(value=hospital.status, errors=[]),
        icp_tier=SimpleNamespace(value=hospital.icp_tier, errors=[]),
        lead_score=SimpleNamespace(value=hospital.lead_score, errors=[]),
        deal_value=SimpleNamespace(value=hospital.deal_value or "", errors=[]),
        assigned_rep=SimpleNamespace(value=hospital.assigned_rep, errors=[]),
        assigned_channel=SimpleNamespace(value=hospital.assigned_channel, errors=[]),
        next_action=SimpleNamespace(value=hospital.next_action, errors=[]),
        next_action_date=SimpleNamespace(value=hospital.next_action_date or "", errors=[]),
        hubspot_id=SimpleNamespace(value=hospital.hubspot_id, errors=[]),
        notes=SimpleNamespace(value=hospital.notes, errors=[]),
        non_field_errors=lambda: [],
    )
    context = {
        "hospital": hospital,
        "form": form,
        "status_choices": HospitalAccount.AccountStatus.choices,
        "tier_choices": HospitalAccount.IcpTier.choices,
        "hospital_type_choices": HOSPITAL_TYPE_CHOICES,
        "page_title": f"Edit — {hospital.name}",
    }
    return render(request, "ai_sales_agent_dashboard/hospital_form.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Scraped Hospital Lead Detail
# ─────────────────────────────────────────────────────────────────────────────


@superuser_only
def scraped_lead_detail(request, place_id):
    """
    Full detail page for a hospital that went through the n8n pipeline,
    identified by place_id. Aggregates data from all agent pipeline tables.
    """

    def _first(model_class, **kwargs):
        try:
            return model_class.objects.filter(**kwargs).first()
        except Exception:
            return None

    def _list(model_class, **kwargs):
        try:
            return list(model_class.objects.filter(**kwargs).order_by("-created_at"))
        except Exception:
            return []

    def _parse_jsonish(value):
        """Parse JSON or double-encoded JSON strings into Python objects.
        Also handles the n8n pattern where a JSON array is stored with
        surrounding literal quote characters: '"[{...}]"' (unescaped).
        """
        parsed = value
        for _ in range(3):
            if isinstance(parsed, str):
                parsed = parsed.strip()
                if not parsed:
                    return None
                try:
                    parsed = json.loads(parsed)
                    continue
                except (json.JSONDecodeError, TypeError):
                    # Handle literal surrounding quotes added by n8n:
                    # e.g. '"[{...}]"' → strip the wrapping " and retry
                    if len(parsed) >= 2 and parsed[0] == '"' and parsed[-1] == '"':
                        inner = parsed[1:-1]
                        try:
                            parsed = json.loads(inner)
                            continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                    return None
            return parsed
        return parsed

    def _format_reviews(raw_reviews):
        parsed = _parse_jsonish(raw_reviews)
        if isinstance(parsed, dict):
            parsed = parsed.get("reviews") or parsed.get("data") or parsed
        if not isinstance(parsed, list):
            return []

        review_items = []
        for review in parsed:
            if not isinstance(review, dict):
                continue

            text_block = review.get("text")
            original_text_block = review.get("originalText")
            if isinstance(text_block, dict):
                text = text_block.get("text")
            else:
                text = text_block
            if not text:
                if isinstance(original_text_block, dict):
                    text = original_text_block.get("text")
                else:
                    text = original_text_block

            author = review.get("authorAttribution") or {}
            review_items.append(
                {
                    "author_name": author.get("displayName") or "Anonymous reviewer",
                    "author_photo": author.get("photoUri") or "",
                    "author_profile": author.get("uri") or "",
                    "rating": review.get("rating"),
                    "relative_time": review.get("relativePublishTimeDescription") or "",
                    "published_at": review.get("publishTime") or "",
                    "text": (text or "").strip(),
                    "maps_url": review.get("googleMapsUri") or "",
                }
            )

        return [item for item in review_items if item["text"] or item["rating"] or item["author_name"]]

    # ── Gather all pipeline data for this place_id ─────────────────────────
    # The n8n pipeline consolidated everything into the Results table; the
    # per-workflow tables (ICP_Account_Scores, AI_Enhanced_Briefs, HubSpot_Deal_Map,
    # CRM_Contacts, etc.) were retired. Read ICP scoring inline off `scraped`.
    scraped = _fetch_result_row(place_id)
    raw = _fetch_result_raw(place_id)  # full Results row (all live columns)
    brief = None
    dq = None
    deal = None
    contact = None
    research = None
    meetings = []

    # ── ICP scoring — folded inline on Results (ICP_Account_Scores retired) ──
    icp = None
    if raw and (raw.get("icp_score") or 0) > 0:
        icp = {
            "icp_fit_score": raw.get("icp_score") or 0,
            "account_tier": raw.get("icp_tier") or "",
            "tier_label": raw.get("icp_tier") or "",
            "recommended_action": (raw.get("qual_reason") or "").strip(),
            "entry_point": (raw.get("icp_fit_reason") or "").strip(),
            "fit_drivers": [],
            "fit_gaps": [],
        }

    # ── Discovery / qualification brief — BANT + MEDDIC inline on Results ───
    disc_brief = None
    if raw and (raw.get("bant_score") or raw.get("buyer_persona") or raw.get("meddic_identified_pain")):
        _bant_parts = [
            ("Budget", raw.get("bant_budget")),
            ("Authority", raw.get("bant_authority")),
            ("Need", raw.get("bant_need")),
            ("Timeline", raw.get("bant_timeline")),
        ]
        _bant_breakdown = "  ·  ".join(
            "%s: %s" % (k, v) for k, v in _bant_parts if v not in (None, "")
        )
        disc_brief = SimpleNamespace(
            mql_stage=(raw.get("qual_stage") or raw.get("mql_stage") or "").strip(),
            lead_temperature=(raw.get("intent_tier") or "").strip().upper(),
            bant_score=raw.get("bant_score") or 0,
            wf2_score=raw.get("product_fit_score") or 0,
            lead_score=raw.get("lead_score") or 0,
            buyer_persona=(raw.get("buyer_persona") or "").strip(),
            bant_breakdown=_bant_breakdown,
        )

    # If no data at all, 404
    if not scraped:
        from django.http import Http404

        raise Http404("No pipeline data found for this hospital.")

    # ── Canonical display name ─────────────────────────────────────────────
    name = (scraped and scraped.title) or (scraped and scraped.name) or place_id

    # ── ICP tier / color (derived from Results.icp_score / icp_tier) ────────
    _icp_score_val = (scraped and scraped.icp_score) or 0
    tier_simple, tier_color, tier_label = _score_to_tier(_icp_score_val)

    # ── Best contact info across all sources ───────────────────────────────
    def _pick(*vals):
        for v in vals:
            if v and str(v).strip():
                return str(v).strip()
        return ""

    contact_name = _pick(
        deal and deal.contact_name,
        contact and f"{contact.firstname or ''} {contact.lastname or ''}".strip(),
    )
    contact_email = _pick(
        deal and deal.contact_email,
        contact and contact.email,
        scraped and scraped.email,
    )
    contact_phone = _pick(
        deal and deal.contact_phone,
        contact and contact.phone,
        scraped and scraped.phone,
    )
    address = _pick(scraped and scraped.address)
    city = _pick(
        deal and deal.assigned_region,
        address.split(",")[-2].strip() if address and "," in address else "",
    )
    website = _pick(
        scraped and scraped.clean_url,
        scraped and scraped.website,
        deal and deal.business_name,  # fallback to name, not URL, but best effort
    )

    # ── Pipeline stages (which agents have data for this hospital) ─────────
    pipeline_stages = []
    if scraped:
        pipeline_stages.append(
            {
                "wf": "WF01–07",
                "label": "Discovery",
                "icon": "🔍",
                "color": "#2563eb",
                "agent_name": "Business Lead Generation",
            }
        )
    if icp:
        pipeline_stages.append(
            {
                "wf": "WF08–09",
                "label": "ICP Scoring",
                "icon": "📊",
                "color": "#0891b2",
                "agent_name": "ICP Intelligence Engine",
            }
        )
    if brief:
        pipeline_stages.append(
            {
                "wf": "WF10–13",
                "label": "AI Brief",
                "icon": "🧠",
                "color": "#7c3aed",
                "agent_name": "Insight Synthesiser",
            }
        )
    if research:
        pipeline_stages.append(
            {
                "wf": "WF12",
                "label": "Research",
                "icon": "🔭",
                "color": "#9333ea",
                "agent_name": "Prospect Research Agent",
            }
        )
    if disc_brief:
        pipeline_stages.append(
            {
                "wf": "WF06",
                "label": "Disc. Brief",
                "icon": "🗂️",
                "color": "#0891b2",
                "agent_name": "Discovery Brief Agent",
            }
        )
    if deal:
        pipeline_stages.append(
            {
                "wf": "WF18–21",
                "label": "CRM / HubSpot",
                "icon": "💼",
                "color": "#d97706",
                "agent_name": "HubSpot Sync Agent",
            }
        )
    if meetings:
        pipeline_stages.append(
            {
                "wf": "WF26–28",
                "label": "Meetings",
                "icon": "📅",
                "color": "#db2777",
                "agent_name": "Meeting Manager",
            }
        )
    if contact:
        pipeline_stages.append(
            {
                "wf": "WF33",
                "label": "CRM Contact",
                "icon": "👤",
                "color": "#059669",
                "agent_name": "CRM Sync Agent",
            }
        )

    # ── Social links ───────────────────────────────────────────────────────
    social_links = {}
    if scraped:
        if scraped.linkedin:
            social_links["LinkedIn"] = scraped.linkedin
        if scraped.facebook:
            social_links["Facebook"] = scraped.facebook
        if scraped.instagram:
            social_links["Instagram"] = scraped.instagram
        if scraped.youtube:
            social_links["YouTube"] = scraped.youtube

    # ── Sub scores (raw numbers for display) ──────────────────────────────
    # Sourced from Results (ICP_Account_Scores table retired). The legacy
    # technographic/timing sub-scores no longer exist; surface the engagement
    # and BANT signals that Results does carry instead.
    icp_scores = None
    if scraped and (scraped.icp_score or 0) > 0:
        icp_scores = {
            "firmographic": scraped.firmographic_score or 0,
            "behavioral": scraped.behavioral_score or 0,
            "technographic": scraped.engagement_score or 0,
            "timing": scraped.bant_score or 0,
        }

    # ── Parse AI Brief JSON fields into structured dicts ──────────────────
    import re as _re

    def _parse_json(text):
        parsed = _parse_jsonish(text)
        return parsed if isinstance(parsed, (dict, list)) else None

    def _text_to_list(text):
        """Normalize a TextField to a plain Python list of strings."""
        if not text:
            return []
        maybe = _parse_jsonish(text)
        if isinstance(maybe, list):
            return [str(i).strip() for i in maybe if str(i).strip()]
        if isinstance(maybe, dict):
            items = []
            for k, v in maybe.items():
                if isinstance(v, list):
                    items.extend([str(x).strip() for x in v if str(x).strip()])
                elif v:
                    items.append(str(v).strip())
            return items
        lines = _re.split(r"\n+", str(text).strip())
        return [
            _re.sub(r"^[\s\-\*\•\d\.\)]+", "", ln).strip()
            for ln in lines
            if _re.sub(r"^[\s\-\*\•\d\.\)]+", "", ln).strip()
        ]

    _MEDDIC_ALIASES = {
        "metrics": "Metrics",
        "metric": "Metrics",
        "m": "Metrics",
        "economic buyer": "Economic Buyer",
        "economic": "Economic Buyer",
        "e": "Economic Buyer",
        "decision criteria": "Decision Criteria",
        "decision_criteria": "Decision Criteria",
        "d": "Decision Criteria",
        "decision process": "Decision Process",
        "decision_process": "Decision Process",
        "dp": "Decision Process",
        "identify pain": "Identify Pain",
        "identify_pain": "Identify Pain",
        "pain": "Identify Pain",
        "i": "Identify Pain",
        "champion": "Champion",
        "c": "Champion",
    }
    _MEDDIC_ORDER = ["Metrics", "Economic Buyer", "Decision Criteria", "Decision Process", "Identify Pain", "Champion"]

    def _parse_meddic(text):
        """Parse MEDDIC questions text into an ordered list of (label, [questions])."""
        if not text:
            return []
        maybe = _parse_jsonish(text)
        result = {}

        def _normalize_label(raw_label):
            key = _re.sub(r"\s+", " ", str(raw_label).strip().lower().replace("_", " ").replace("-", " "))
            return _MEDDIC_ALIASES.get(key, str(raw_label).strip().title())

        def _append_question(label, question):
            q = str(question).strip()
            if not q:
                return
            result[label] = result.get(label, []) + [q]

        def _ingest_text(raw_text, default_label=None):
            """Parse MEDDIC text that may be newline or pipe-delimited with section markers."""
            if not raw_text:
                return
            chunks = [c.strip() for c in _re.split(r"\n+|\s*\|\s*", str(raw_text).strip()) if c.strip()]
            current_label = default_label
            for chunk in chunks:
                # Remove numbering prefixes like "1.", "2)", "3 -"
                cleaned = _re.sub(r"^\s*\d+\s*[\.)\-]\s*", "", chunk).strip()

                # Pattern: [METRICS] question text
                m = _re.match(r"^\[\s*([^\]]+?)\s*\]\s*(.*)$", cleaned)
                if m:
                    current_label = _normalize_label(m.group(1))
                    _append_question(current_label, m.group(2))
                    continue

                # Pattern: Metrics: question text OR Metrics - question text
                m2 = _re.match(r"^([A-Za-z][A-Za-z_\s\-]{0,40})\s*[:\-]\s*(.+)$", cleaned)
                if m2:
                    possible = _normalize_label(m2.group(1))
                    if possible in _MEDDIC_ORDER:
                        current_label = possible
                        _append_question(current_label, m2.group(2))
                        continue

                # No explicit section marker in this chunk.
                target = current_label or default_label or "Questions"
                _append_question(target, cleaned)

        if isinstance(maybe, dict):
            for k, v in maybe.items():
                default_label = _normalize_label(k)
                if isinstance(v, list):
                    for item in v:
                        _ingest_text(item, default_label=default_label)
                else:
                    _ingest_text(v, default_label=default_label)
        elif isinstance(maybe, list):
            for item in maybe:
                _ingest_text(item)
        elif isinstance(maybe, str):
            _ingest_text(maybe)
        else:
            _ingest_text(text)

        # Return as ordered list preserving MEDDIC order where possible
        ordered = [(lbl, result[lbl]) for lbl in _MEDDIC_ORDER if lbl in result]
        remaining = [(k, v) for k, v in result.items() if k not in _MEDDIC_ORDER]
        return ordered + remaining

    def _clean_text(value):
        if value is None:
            return ""
        return str(value).strip()

    def _listify(value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str):
            return _text_to_list(value)
        return [value]

    def _normalize_products_to_pitch(value):
        products = []
        for item in _listify(value):
            if isinstance(item, dict):
                product_name = _clean_text(item.get("product_name") or item.get("name"))
                why_it_fits = _clean_text(item.get("why_it_fits") or item.get("reason"))
                lead_with = _clean_text(item.get("lead_with") or item.get("lead"))
                replaces = _clean_text(item.get("replaces") or item.get("replacement"))
                if product_name or why_it_fits or lead_with or replaces:
                    products.append(
                        {
                            "product_name": product_name,
                            "why_it_fits": why_it_fits,
                            "lead_with": lead_with,
                            "replaces": replaces,
                        }
                    )
            else:
                text = _clean_text(item)
                if text:
                    products.append(
                        {
                            "product_name": text,
                            "why_it_fits": "",
                            "lead_with": "",
                            "replaces": "",
                        }
                    )
        return products

    def _normalize_objection_responses(value):
        responses = []
        for item in _listify(value):
            if isinstance(item, dict):
                they_say = _clean_text(item.get("they_say") or item.get("objection") or item.get("concern"))
                you_say = _clean_text(item.get("you_say") or item.get("response") or item.get("rebuttal"))
                if they_say or you_say:
                    responses.append(
                        {
                            "they_say": they_say,
                            "you_say": you_say,
                        }
                    )
            else:
                text = _clean_text(item)
                if text:
                    responses.append(
                        {
                            "they_say": text,
                            "you_say": "",
                        }
                    )
        return responses

    def _normalize_questions(payload):
        if not payload:
            return []

        if isinstance(payload, dict):
            if isinstance(payload.get("questions"), list):
                source_items = payload.get("questions") or []
            else:
                source_items = []
                for category, raw_items in payload.items():
                    items = raw_items if isinstance(raw_items, list) else [raw_items]
                    for item in items:
                        if isinstance(item, dict):
                            source_items.append(
                                {
                                    "category": category,
                                    **item,
                                }
                            )
                        else:
                            text = _clean_text(item)
                            if text:
                                source_items.append(
                                    {
                                        "category": category,
                                        "question": text,
                                    }
                                )
        else:
            source_items = _listify(payload)

        normalized = []
        for item in source_items:
            if isinstance(item, dict):
                question = _clean_text(item.get("question") or item.get("ask") or item.get("prompt"))
                category = _clean_text(item.get("category") or item.get("purpose") or item.get("type"))
                follow_up = _clean_text(item.get("follow_up") or item.get("followup") or item.get("next_question"))
                insight_goal = _clean_text(item.get("insight_goal") or item.get("listen_for") or item.get("goal"))
                if question:
                    normalized.append(
                        {
                            "category": category,
                            "question": question,
                            "follow_up": follow_up,
                            "insight_goal": insight_goal,
                        }
                    )
            else:
                question = _clean_text(item)
                if question:
                    normalized.append(
                        {
                            "category": "",
                            "question": question,
                            "follow_up": "",
                            "insight_goal": "",
                        }
                    )
        return normalized

    def _normalize_follow_up(value):
        if isinstance(value, dict):
            return {
                "send": _clean_text(value.get("send")),
                "subject_line": _clean_text(value.get("subject_line") or value.get("subject")),
                "within": _clean_text(value.get("within") or value.get("timing")),
            }
        if isinstance(value, str):
            text = _clean_text(value)
            if text:
                return {"send": text, "subject_line": "", "within": ""}
        return None

    def _normalize_company(payload):
        if not isinstance(payload, dict):
            return payload if payload else None
        normalized = dict(payload)
        normalized["size_estimate"] = _clean_text(
            normalized.get("size_estimate")
            or normalized.get("company_size")
            or (brief.ai_size_estimate if brief else "")
        )
        normalized["employee_range"] = _clean_text(normalized.get("employee_range") or normalized.get("employee_count"))
        normalized["growth_stage"] = _clean_text(
            normalized.get("growth_stage") or normalized.get("stage") or (brief.ai_growth_stage if brief else "")
        )
        normalized["digital_maturity"] = _clean_text(normalized.get("digital_maturity"))
        normalized["market_position"] = _clean_text(
            normalized.get("market_position") or normalized.get("positioning") or normalized.get("summary")
        )
        normalized["key_insights"] = _text_to_list(
            normalized.get("key_insights") or normalized.get("business_snapshot")
        )
        normalized["products_to_pitch"] = _normalize_products_to_pitch(normalized.get("products_to_pitch"))
        return normalized if any(bool(v) for v in normalized.values()) else None

    def _normalize_pain(payload):
        if not isinstance(payload, dict):
            return payload if payload else None
        normalized = dict(payload)
        normalized["primary_pain_points"] = normalized.get("primary_pain_points") or normalized.get("pain_points") or []
        normalized["opportunity_areas"] = _text_to_list(normalized.get("opportunity_areas"))
        normalized["urgency_triggers"] = _text_to_list(normalized.get("urgency_triggers"))
        normalized["discovery_questions"] = _normalize_questions(
            normalized.get("discovery_questions") or normalized.get("questions")
        )
        normalized["objection_responses"] = _normalize_objection_responses(normalized.get("objection_responses"))
        return normalized if any(bool(v) for v in normalized.values()) else None

    def _normalize_persona(payload):
        if not isinstance(payload, dict):
            return payload if payload else None
        normalized = dict(payload)
        normalized["likely_decision_maker"] = _clean_text(
            normalized.get("likely_decision_maker") or normalized.get("decision_maker")
        )
        normalized["communication_style"] = _clean_text(normalized.get("communication_style"))
        normalized["key_priorities"] = _text_to_list(normalized.get("key_priorities"))
        normalized["rapport_builders"] = _text_to_list(normalized.get("rapport_builders"))
        normalized["potential_objections"] = _text_to_list(normalized.get("potential_objections"))
        normalized["call_opening"] = _clean_text(normalized.get("call_opening"))
        normalized["how_to_close"] = _clean_text(normalized.get("how_to_close"))
        normalized["follow_up"] = _normalize_follow_up(normalized.get("follow_up"))
        return normalized if any(bool(v) for v in normalized.values()) else None

    def _normalize_competitive(payload):
        if not isinstance(payload, dict):
            return payload if payload else None
        normalized = dict(payload)
        normalized["strengths"] = _text_to_list(normalized.get("strengths"))
        normalized["weaknesses"] = _text_to_list(normalized.get("weaknesses"))
        normalized["differentiation_opportunities"] = _text_to_list(
            normalized.get("differentiation_opportunities") or normalized.get("opportunities")
        )
        normalized["competitive_threats"] = _text_to_list(
            normalized.get("competitive_threats") or normalized.get("threats")
        )
        normalized["products_to_pitch"] = _normalize_products_to_pitch(normalized.get("products_to_pitch"))
        normalized["objection_responses"] = _normalize_objection_responses(normalized.get("objection_responses"))
        return normalized if any(bool(v) for v in normalized.values()) else None

    def _normalize_talk(payload, persona_payload=None):
        if not isinstance(payload, dict):
            payload = {}
        normalized = dict(payload)
        raw_pitch_scripts = normalized.get("pitch_scripts")
        normalized["opening_statement"] = _clean_text(
            normalized.get("opening_statement")
            or normalized.get("call_opening")
            or (persona_payload or {}).get("call_opening")
        )
        normalized["value_proposition"] = _clean_text(normalized.get("value_proposition"))
        normalized["key_talking_points"] = _text_to_list(normalized.get("key_talking_points"))
        normalized["closing_approach"] = _clean_text(
            normalized.get("closing_approach")
            or normalized.get("how_to_close")
            or (persona_payload or {}).get("how_to_close")
        )
        normalized["follow_up_strategy"] = _clean_text(normalized.get("follow_up_strategy"))
        normalized["pitch_scripts"] = []
        for item in _listify(raw_pitch_scripts):
            if not isinstance(item, dict):
                text = _clean_text(item)
                if text:
                    normalized["pitch_scripts"].append(
                        {
                            "product_name": "",
                            "script": text,
                            "close_with": "",
                        }
                    )
                continue
            product_name = _clean_text(item.get("product_name") or item.get("name"))
            script = _clean_text(item.get("script") or item.get("talk_track"))
            close_with = _clean_text(item.get("close_with") or item.get("cta"))
            if product_name or script or close_with:
                normalized["pitch_scripts"].append(
                    {
                        "product_name": product_name,
                        "script": script,
                        "close_with": close_with,
                    }
                )

        if not normalized["key_talking_points"] and normalized["pitch_scripts"]:
            normalized["key_talking_points"] = [
                (
                    f"{item['product_name']}: {item['script']}"
                    if item["product_name"] and item["script"]
                    else item["script"] or item["product_name"]
                )
                for item in normalized["pitch_scripts"]
                if item["script"] or item["product_name"]
            ]

        if not normalized["follow_up_strategy"] and persona_payload:
            follow_up = persona_payload.get("follow_up")
            if isinstance(follow_up, dict):
                follow_up_bits = []
                if follow_up.get("send"):
                    follow_up_bits.append(f"Send {follow_up['send']}")
                if follow_up.get("subject_line"):
                    follow_up_bits.append(f"subject line: {follow_up['subject_line']}")
                if follow_up.get("within"):
                    follow_up_bits.append(f"within {follow_up['within']}")
                normalized["follow_up_strategy"] = "; ".join(follow_up_bits)

        return normalized if any(bool(v) for v in normalized.values()) else None

    def _remove_empty_values(obj):
        """
        Recursively remove empty/None values from dictionaries and lists.
        Returns None if the resulting object is empty.
        """
        if isinstance(obj, dict):
            cleaned = {}
            for k, v in obj.items():
                if isinstance(v, dict):
                    cleaned_v = _remove_empty_values(v)
                    if cleaned_v is not None:
                        cleaned[k] = cleaned_v
                elif isinstance(v, list):
                    cleaned_v = _remove_empty_values(v)
                    if cleaned_v:  # Only add non-empty lists
                        cleaned[k] = cleaned_v
                elif v is not None and str(v).strip():  # Only add non-empty values
                    cleaned[k] = v
            return cleaned if cleaned else None
        elif isinstance(obj, list):
            cleaned = []
            for item in obj:
                if isinstance(item, dict):
                    cleaned_item = _remove_empty_values(item)
                    if cleaned_item is not None:
                        cleaned.append(cleaned_item)
                elif isinstance(item, list):
                    cleaned_item = _remove_empty_values(item)
                    if cleaned_item:
                        cleaned.append(cleaned_item)
                elif item is not None and str(item).strip():  # Only add non-empty items
                    cleaned.append(item)
            return cleaned
        return obj

    brief_company = _normalize_company(_parse_json(brief.ai_company_analysis if brief else None))
    if not brief_company and brief:
        # Fallback if JSON parsing fails: use direct fields from brief
        fallback_company = {}
        if brief.ai_size_estimate:
            fallback_company["size_estimate"] = brief.ai_size_estimate
        if brief.ai_growth_stage:
            fallback_company["growth_stage"] = brief.ai_growth_stage
        brief_company = fallback_company if fallback_company else None
    brief_company = _remove_empty_values(brief_company)

    brief_pain = _normalize_pain(_parse_json(brief.ai_pain_point_analysis if brief else None))
    brief_pain = _remove_empty_values(brief_pain)

    brief_persona = _normalize_persona(_parse_json(brief.ai_contact_persona if brief else None))
    brief_persona = _remove_empty_values(brief_persona)

    brief_competitive = _normalize_competitive(_parse_json(brief.ai_competitive_intelligence if brief else None))
    brief_competitive = _remove_empty_values(brief_competitive)

    brief_questions = _normalize_questions(_parse_json(brief.ai_personalized_questions if brief else None))
    brief_questions = _remove_empty_values(brief_questions)

    brief_talk = _normalize_talk(_parse_json(brief.ai_talk_tracks if brief else None), brief_persona)
    brief_talk = _remove_empty_values(brief_talk)
    review_items = _format_reviews(scraped.reviews if scraped else None)

    def _get_review_total(raw_reviews):
        """Extract userRatingCount from root of the reviews JSON, if present."""
        parsed = _parse_jsonish(raw_reviews)
        if isinstance(parsed, dict):
            for key in ("userRatingCount", "user_ratings_total", "reviewCount", "totalRatings"):
                val = parsed.get(key)
                if val is not None:
                    try:
                        return int(val)
                    except (TypeError, ValueError):
                        pass
        return None

    total_review_count = _get_review_total(scraped.reviews if scraped else None)

    # Results has no dedicated talking-points list column — leave empty.
    disc_talking_pts = []
    # MEDDIC: build directly from the six inline Results columns (each a single
    # statement → wrapped as a one-item list to fit the template's per-category UI).
    disc_meddic = []
    for _label, _col in (
        ("Metrics", "meddic_metrics"),
        ("Economic Buyer", "meddic_economic_buyer"),
        ("Decision Criteria", "meddic_decision_criteria"),
        ("Decision Process", "meddic_decision_process"),
        ("Identified Pain", "meddic_identified_pain"),
        ("Champion", "meddic_champion"),
    ):
        _v = (raw.get(_col) or "").strip() if raw else ""
        if _v:
            disc_meddic.append((_label, [_v]))

    # Full discovery call-prep notes (free text) + every populated Results field.
    discovery_notes_text = (raw.get("discovery_notes") or "").strip() if raw else ""
    result_fields = _grouped_result_fields(raw)

    # Extract products explicitly mentioned in talking points/MEDDIC
    def _extract_products_from_source(talking_pts, meddic_qs):
        """Extract product mentions from discovery brief data."""
        products = set()

        # Check talking points for product mentions
        if talking_pts:
            for pt in talking_pts:
                pt_str = str(pt or "").lower()
                # Look for obvious product mentions
                if any(kw in pt_str for kw in ["product", "pitch", "meril", "procedure", "treatment"]):
                    products.add(pt)

        # Check MEDDIC questions for product context
        if meddic_qs:
            for label, questions in meddic_qs:
                if label:
                    products.add(str(label))
                if questions:
                    for q in questions:
                        q_str = str(q or "").lower()
                        if any(kw in q_str for kw in ["product", "meril", "treatment", "procedure"]):
                            products.add(q)

        return list(products)

    # ── Smart Meril product recommendations based on hospital profile ──────
    # Load product catalog from database (Meril_Products table)
    def _get_meril_catalog():
        """Fetch Meril product catalog from the Meril_Products table."""
        _SUBCAT_ICON = {
            "cardiologist": "❤️",
            "orthopedic surgeon": "🦴",
            "medical equipment supplier": "🏥",
            "hospital equipment supplier": "🏥",
        }
        try:
            products = ProductCatalog.objects.filter(availabilitystatus="Available").order_by(
                "category", "subcategory", "productname"
            )
            catalog = []
            for prod in products:
                subcat_lower = (prod.subcategory or "").lower()
                icon = _SUBCAT_ICON.get(subcat_lower, "📦")
                # Build keyword list from subcategory and clinical applications
                kws = []
                if prod.subcategory:
                    kws.append(prod.subcategory.lower())
                if prod.clinicalapplications:
                    kws.extend([w.strip().lower() for w in prod.clinicalapplications.split(",") if w.strip()])
                catalog.append(
                    {
                        "icon": icon,
                        "name": prod.productname or "",
                        "desc": prod.description or f"{prod.category} — {prod.subcategory}",
                        "tags": [prod.category, prod.subcategory] if prod.subcategory else [prod.category],
                        "keywords": kws,
                    }
                )
            return catalog if catalog else _get_default_catalog()
        except Exception:
            # Fallback if table doesn't exist yet or database error
            return _get_default_catalog()

    def _get_default_catalog():
        """Default hardcoded catalog as fallback."""
        return [
            {
                "icon": "❤️",
                "name": "Cardiovascular",
                "desc": "Coronary stents, heart valves, structural heart devices & TAVR",
                "tags": ["DES Stents", "TAVR", "Mitral Clip", "CTO Devices"],
                "keywords": [
                    "cardiac",
                    "cardio",
                    "cath lab",
                    "coronary",
                    "heart valve",
                    "cardiovascular",
                    "angioplasty",
                    "heart hospital",
                    "heart care",
                    "pci ",
                    "ccu ",
                    "structural heart",
                    "interventional cardiol",
                ],
            },
            {
                "icon": "🔬",
                "name": "Endosurgery",
                "desc": "Surgical staplers, energy devices, trocars & laparoscopic access",
                "tags": ["Staplers", "Vessel Sealer", "Trocars", "Laparoscopy"],
                "keywords": [
                    "laparoscop",
                    "oncolog",
                    "bariatric",
                    "operating theatre",
                    "endoscop",
                    "multi-speciality",
                    "multispeciality",
                    "multi speciality",
                    "super speciality",
                    "cancer hospital",
                    "cancer centre",
                    "robotic surgery",
                    "minimally invasive",
                ],
            },
            {
                "icon": "🦴",
                "name": "Orthopaedics",
                "desc": "Joint replacements, trauma implants & sports medicine devices",
                "tags": ["Knee Replacement", "Hip Replacement", "Trauma", "Spine"],
                "keywords": [
                    "orthop",
                    "orthopaedic",
                    "bone and joint",
                    "spine hospital",
                    "joint replacement",
                    "trauma centre",
                    "sports medicine",
                    "musculoskeletal",
                    "fracture clinic",
                ],
            },
            {
                "icon": "🧪",
                "name": "Diagnostics",
                "desc": "Rapid test kits, in-vitro diagnostics & point-of-care testing",
                "tags": ["Rapid Tests", "IVD", "POC Testing", "Glucometer"],
                "keywords": [
                    "diagnostic centre",
                    "diagnostic lab",
                    "pathology lab",
                    "pathology centre",
                    "clinical laboratory",
                    "ivd ",
                    "point of care",
                    "blood bank",
                    "laboratory services",
                ],
            },
            {
                "icon": "⚡",
                "name": "Electrophysiology",
                "desc": "EP catheters, cardiac ablation & mapping systems",
                "tags": ["EP Catheters", "RF Ablation", "Mapping"],
                "keywords": [
                    "electrophysiol",
                    "ep lab",
                    "arrhythmia",
                    "ablation clinic",
                    "pacemaker",
                    "defibrillat",
                    "heart rhythm",
                ],
            },
            {
                "icon": "💉",
                "name": "Vascular Access & Intervention",
                "desc": "Peripheral intervention, guide wires, balloons & stent grafts",
                "tags": ["PTA Balloons", "Guide Wires", "Stent Grafts", "Peripheral"],
                "keywords": [
                    "vascular",
                    "peripheral intervention",
                    "angiograph",
                    "dialysis centre",
                    "nephrology",
                    "kidney hospital",
                    "renal care",
                ],
            },
            {
                "icon": "🩹",
                "name": "Wound Care & Aesthetics",
                "desc": "Sutures, skin staplers & aesthetic fillers",
                "tags": ["Sutures", "Skin Staplers", "Fillers"],
                "keywords": [
                    "wound care",
                    "aesthetic",
                    "dermatolog",
                    "plastic surgery",
                    "cosmetic surgery",
                    "burn unit",
                    "skin clinic",
                    "hair clinic",
                    "skin care clinic",
                    "cosmetolog",
                ],
            },
        ]

    _MERIL_CATALOG = _get_meril_catalog()

    def _recommend_products(h_name, h_scraped, h_icp):
        """Return Meril products ONLY from structured AI data:
        1) brief_company.products_to_pitch  (from ai_company_analysis JSON)
        2) A 'Products to Pitch' bullet in disc_talking_pts
        No keyword/type/category fallback — if neither source has data, return [].
        """

        def _base_entry(prod, reason=""):
            entry = {k: v for k, v in prod.items() if k != "keywords"}
            entry["match_reason"] = reason
            return entry

        # ── Cache ──────────────────────────────────────────────────────────
        cache_payload = {
            "place_id": place_id,
            "brief_products": [
                (p.get("product_name") or "") for p in ((brief_company or {}).get("products_to_pitch") or [])
            ],
            "talking_points": disc_talking_pts or [],
        }
        cache_sig = hashlib.sha1(json.dumps(cache_payload, sort_keys=True, default=str).encode("utf-8"),
                                 usedforsecurity=False).hexdigest()[
            :16
        ]
        cache_key = f"ai_dash:meril_rec:{place_id}:{cache_sig}"
        cache_ttl_seconds = 6 * 60 * 60
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            return cached

        by_name = {p["name"]: p for p in _MERIL_CATALOG}

        # Catalog name + alias → product line name
        _PRODUCT_ALIASES = {
            "endosurgery": "Endosurgery",
            "laparoscop": "Endosurgery",
            "stapler": "Endosurgery",
            "trocar": "Endosurgery",
            "vessel sealer": "Endosurgery",
            "fess": "Endosurgery",
            "sinus balloon": "Endosurgery",
            "balloon sinuplasty": "Endosurgery",
            "myrac": "Endosurgery",
            "metic": "Endosurgery",
            "mesire": "Endosurgery",
            "cardiovascular": "Cardiovascular",
            "cardiac": "Cardiovascular",
            "coronary stent": "Cardiovascular",
            "des stent": "Cardiovascular",
            "tavr": "Cardiovascular",
            "mitral clip": "Cardiovascular",
            "pci": "Cardiovascular",
            "structural heart": "Cardiovascular",
            "orthopaedic": "Orthopaedics",
            "orthopedic": "Orthopaedics",
            "orthopaedics": "Orthopaedics",
            "knee replacement": "Orthopaedics",
            "hip replacement": "Orthopaedics",
            "joint replacement": "Orthopaedics",
            "trauma implant": "Orthopaedics",
            "diagnostics": "Diagnostics",
            "ivd": "Diagnostics",
            "rapid test": "Diagnostics",
            "glucometer": "Diagnostics",
            "point of care": "Diagnostics",
            "electrophysiology": "Electrophysiology",
            "ep lab": "Electrophysiology",
            "ablation": "Electrophysiology",
            "pacemaker": "Electrophysiology",
            "arrhythmia": "Electrophysiology",
            "vascular access": "Vascular Access & Intervention",
            "peripheral vascular": "Vascular Access & Intervention",
            "angiograph": "Vascular Access & Intervention",
            "dialysis": "Vascular Access & Intervention",
            "wound care": "Wound Care & Aesthetics",
            "suture": "Wound Care & Aesthetics",
            "aesthetic": "Wound Care & Aesthetics",
            "dermatolog": "Wound Care & Aesthetics",
        }

        def _resolve(text):
            tl = str(text or "").lower().strip()
            if not tl:
                return None
            for cat_name in by_name:
                if cat_name.lower() == tl or cat_name.lower() in tl:
                    return cat_name
            best, best_len = None, 0
            for alias, line_name in _PRODUCT_ALIASES.items():
                if alias in tl and len(alias) > best_len:
                    best, best_len = line_name, len(alias)
            return best

        results = []
        seen = set()

        def _add(prod, reason):
            if prod["name"] not in seen:
                seen.add(prod["name"])
                results.append(_base_entry(prod, reason))

        # ── SOURCE 1: brief_company.products_to_pitch ──────────────────────
        for item in (brief_company or {}).get("products_to_pitch") or []:
            pname = (item.get("product_name") or "") if isinstance(item, dict) else str(item or "")
            why = (item.get("why_it_fits") or "") if isinstance(item, dict) else ""
            if not pname:
                continue
            line = _resolve(pname)
            if line and line in by_name:
                reason = f"AI Brief — {pname}" + (f": {why}" if why else "")
                _add(by_name[line], reason)

        if results:
            cache.set(cache_key, results, cache_ttl_seconds)
            return results

        # ── SOURCE 2: disc_talking_pts "Products to Pitch" line ────────────
        for line in disc_talking_pts or []:
            ls = str(line or "").lower()
            if "product" in ls and ("pitch" in ls or "recommend" in ls or "suggest" in ls):
                rhs = _re.split(r"[:\-–]", str(line), maxsplit=1)
                items_text = rhs[1] if len(rhs) == 2 else str(line)
                for part in _re.split(r",|/|\||;|\band\b", items_text):
                    part = part.strip()
                    if not part:
                        continue
                    line_name = _resolve(part)
                    if line_name and line_name in by_name:
                        _add(by_name[line_name], f"Products to Pitch: {part}")

        cache.set(cache_key, results, cache_ttl_seconds)
        return results

    meril_products = _recommend_products(name, scraped, icp)

    context = {
        "place_id": place_id,
        "name": name,
        "tier_simple": tier_simple,
        "tier_color": tier_color,
        "tier_label": tier_label,
        # Raw pipeline objects
        "scraped": scraped,
        "icp": icp,
        "brief": brief,
        "dq": dq,
        "disc_brief": disc_brief,
        "deal": deal,
        "contact": contact,
        "research": research,
        "meetings": meetings,
        # Derived / aggregated
        "pipeline_stages": pipeline_stages,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "address": address,
        "city": city,
        "website": website,
        "social_links": social_links,
        "icp_scores": icp_scores,
        "review_items": review_items,
        "total_review_count": total_review_count,
        "page_title": name,
        # Parsed AI brief sub-objects
        "brief_company": brief_company,
        "brief_pain": brief_pain,
        "brief_persona": brief_persona,
        "brief_competitive": brief_competitive,
        "brief_questions": brief_questions,
        "brief_talk": brief_talk,
        # Parsed disc brief fields
        "disc_talking_pts": disc_talking_pts,
        "disc_meddic": disc_meddic,
        "discovery_notes_text": discovery_notes_text,
        # All populated Results columns, grouped by pipeline stage
        "result_fields": result_fields,
        # Meril product portfolio
        "meril_products": meril_products,
    }
    return render(request, "ai_sales_agent_dashboard/scraped_lead_detail.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# API: Agent Quick Info (for modal/tooltip)
# ─────────────────────────────────────────────────────────────────────────────


@superuser_only
def api_agent_quick(request, workflow_id):
    agent = get_object_or_404(SalesAgent, workflow_id=workflow_id)
    return JsonResponse(
        {
            "workflow_id": agent.workflow_id,
            "name": agent.name,
            "short_name": agent.short_name,
            "icon": agent.icon,
            "description": agent.description,
            "phase": agent.phase,
            "phase_display": agent.phase_display,
            "trigger_types": agent.trigger_types,
            "schedule_info": agent.schedule_info,
            "node_count": agent.node_count,
            "complexity_score": agent.complexity_score,
            "complexity_label": agent.complexity_label,
            "input_tables": agent.input_tables,
            "output_tables": agent.output_tables,
            "responsibilities": agent.responsibilities,
            "key_outputs": agent.key_outputs,
            "tags": agent.tags,
            "reports_to": agent.reports_to.name if agent.reports_to else None,
            "detail_url": f"/ai-sales-agent/agents/{agent.pk}/",
            "pk": agent.pk,
        }
    )
