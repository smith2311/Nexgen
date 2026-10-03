"""
Management command: seed_agents
Reads the n8n JSON workflow files and populates the SalesAgent model.
Usage: python manage.py seed_agents
"""

import json
import os

from django.core.management.base import BaseCommand

from ai_sales_agent_dashboard.models import AgentPhase, SalesAgent

AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AI Sales Agents")

# Full agent catalog — sourced from README.md + sticky notes in each JSON
AGENT_CATALOG = [
    {
        "workflow_id": "WF00",
        "workflow_number": 0,
        "name": "00. Master Orchestrator",
        "short_name": "Master Orchestrator",
        "phase": AgentPhase.ORCHESTRATOR,
        "icon": "🎯",
        "complexity_score": 5,
        "description": (
            "Runs all 36 workflows in sequence once daily. "
            "Inserts 30s wait after every 7 WFs to prevent DB overload. "
            "Coordinates the entire AI sales pipeline from lead generation to CRM handoff."
        ),
        "trigger_types": ["manual", "schedule"],
        "schedule_info": "Daily (24h)",
        "responsibilities": [
            "Orchestrate all 36 sub-workflows in order",
            "Phase 1 — Discovery: WF1–7 (Lead Gen → Scoring → Qualification → Call Prep → ICP → Research → Market Segment)",
            "Phase 2 — Research & Intelligence: WF8–14",
            "Phase 3 — Outreach: WF15–21",
            "Phase 4 — Comms & Agents: WF22–28",
            "Phase 5 — Content & Closing: WF29–36",
            "Rate-limit protection between phases",
        ],
        "key_outputs": ["Sequenced execution of all pipeline agents", "Full pipeline completion report"],
        "input_tables": [],
        "output_tables": [],
        "tags": ["orchestrator", "pipeline", "master"],
        "file": "00. Master Orchestrator.json",
    },
    {
        "workflow_id": "WF01",
        "workflow_number": 1,
        "name": "1. Business Lead Generation",
        "short_name": "Lead Generation",
        "phase": AgentPhase.DISCOVERY,
        "icon": "🔍",
        "complexity_score": 3,
        "description": (
            "Scrapes and generates new business leads from multiple sources. "
            "Populates the Results table with raw prospect data including company info, "
            "contact details, and initial scoring signals."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Daily",
        "responsibilities": [
            "Scrape business leads from directories and web sources",
            "Extract company name, website, contact, location",
            "Initial deduplication against existing Results",
            "Populate Results table with raw_lead status",
        ],
        "key_outputs": ["New leads in Results table", "Company & contact data"],
        "input_tables": [],
        "output_tables": ["Results"],
        "tags": ["lead-gen", "scraping", "discovery"],
        "file": "1. Business Lead Generation.json",
    },
    {
        "workflow_id": "WF02",
        "workflow_number": 2,
        "name": "2. Lead-Scoring Specialist",
        "short_name": "Lead Scoring",
        "phase": AgentPhase.DISCOVERY,
        "icon": "📊",
        "complexity_score": 4,
        "description": (
            "Dynamic data-driven lead scoring (0–100). "
            "Firmographic (0–40): company size proxy, ICP match, geography. "
            "Behavioral (0–40): website engagement, email engagement, content consumption. "
            "Engagement (0–20): real contact data quality. "
            "Assigns HOT/WARM/COLD tier based on composite score."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Score leads on Firmographic dimensions (0-40)",
            "Score leads on Behavioral dimensions (0-40)",
            "Score leads on Engagement dimensions (0-20)",
            "Assign HOT / WARM / COLD tier",
            "Update score in Results table",
        ],
        "key_outputs": ["Lead score (0-100)", "ICP tier (HOT/WARM/COLD)", "Scoring breakdown"],
        "input_tables": ["Results"],
        "output_tables": ["Results", "Lead_Scores"],
        "tags": ["scoring", "icp", "prioritization"],
        "file": "2. Lead-Scoring Specialist.json",
    },
    {
        "workflow_id": "WF03",
        "workflow_number": 3,
        "name": "3. Qualification Manager",
        "short_name": "Qualification",
        "phase": AgentPhase.DISCOVERY,
        "icon": "✅",
        "complexity_score": 3,
        "description": (
            "BANT + MEDDIC qualification framework. "
            "Stages: Raw Lead → MQL → SAL → SQL. "
            "Evaluates Budget, Authority, Need, Timeline + ICP fit (industry, size, geo). "
            "Assigns qualification stage and fit level (STRONG/MODERATE/WEAK)."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Apply BANT + MEDDIC qualification framework",
            "Progress leads through Raw → MQL → SAL → SQL stages",
            "ICP fit scoring (STRONG / MODERATE / WEAK)",
            "Flag disqualified leads",
        ],
        "key_outputs": ["Qualification stage", "BANT score", "ICP fit level"],
        "input_tables": ["Results", "Lead_Scores"],
        "output_tables": ["Results", "Qualification_Log"],
        "tags": ["qualification", "bant", "meddic", "mql", "sql"],
        "file": "3. Qualification Manager.json",
    },
    {
        "workflow_id": "WF04",
        "workflow_number": 4,
        "name": "4. Discovery Call Prep Specialist - AI Enhanced",
        "short_name": "Call Prep",
        "phase": AgentPhase.DISCOVERY,
        "icon": "📞",
        "complexity_score": 4,
        "description": (
            "AI-powered (GPT-4o) discovery call preparation for high-value leads (rating ≥ 4.0). "
            "Generates persona development, pain point analysis, custom talk tracks, "
            "competitive intelligence extraction, and personalised discovery questions."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Daily",
        "responsibilities": [
            "Select high-value leads (rating ≥ 4.0)",
            "Build comprehensive prospect context packages",
            "AI-powered pain point identification",
            "Generate personalised discovery questions",
            "Create contact persona profiles",
            "Produce competitive intelligence summaries",
            "Generate custom talk tracks",
        ],
        "key_outputs": ["Call prep brief", "Talk track", "Discovery questions", "Persona profile"],
        "input_tables": ["Results", "ICP_Account_Scores"],
        "output_tables": ["Call_Prep_Briefs"],
        "tags": ["call-prep", "gpt4", "discovery", "persona"],
        "file": "4. Discovery Call Prep Specialist - AI Enhanced.json",
    },
    {
        "workflow_id": "WF05",
        "workflow_number": 5,
        "name": "5. Account-Fit Analyst - ICP Scoring & Territory Planning",
        "short_name": "ICP Analyst",
        "phase": AgentPhase.DISCOVERY,
        "icon": "🗺️",
        "complexity_score": 4,
        "description": (
            "Deep ICP (Ideal Customer Profile) scoring and territory planning. "
            "Analyses accounts against ideal profile dimensions and plans territory coverage. "
            "Identifies white-space opportunities and prioritises accounts by territory fit."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Daily",
        "responsibilities": [
            "Score accounts against full ICP profile",
            "Territory segmentation and planning",
            "White-space opportunity identification",
            "Account prioritisation by fit and territory",
        ],
        "key_outputs": ["ICP score", "Territory assignments", "Account prioritisation list"],
        "input_tables": ["Results"],
        "output_tables": ["ICP_Account_Scores", "Territory_Plans"],
        "tags": ["icp", "territory", "account-mapping"],
        "file": "5. Account-Fit Analyst - ICP Scoring & Territory Planning.json",
    },
    {
        "workflow_id": "WF06",
        "workflow_number": 6,
        "name": "6. Data Research Manager",
        "short_name": "Data Research",
        "phase": AgentPhase.DISCOVERY,
        "icon": "🎯",
        "complexity_score": 4,
        "description": (
            "Orchestrator for WF1–WF5. Coordinates Lead Gen, Scoring, Qualification, "
            "Call Prep, and ICP Analyst. Maintains Research Queue, Data Quality Log, "
            "Research Playbooks, and Manager Reports to ensure data pipeline health."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Daily",
        "responsibilities": [
            "Orchestrate WF1–WF5 specialist workflows",
            "Manage Research Queue",
            "Monitor Data Quality",
            "Generate Research Playbooks",
            "Produce Manager Reports",
        ],
        "key_outputs": ["Research Queue", "Data Quality Log", "Manager Reports"],
        "input_tables": ["Results", "Lead_Scores"],
        "output_tables": ["Research_Queue", "Data_Quality_Log", "Research_Playbooks", "Manager_Reports"],
        "tags": ["research", "orchestrator", "data-quality"],
        "file": "6. Data Research Manager.json",
    },
    {
        "workflow_id": "WF07",
        "workflow_number": 7,
        "name": "7. Market Segment Specialist",
        "short_name": "Market Segments",
        "phase": AgentPhase.DISCOVERY,
        "icon": "📈",
        "complexity_score": 3,
        "description": (
            "Researches industry segments from live Results data. "
            "Builds market segment profiles, counts leads per segment, identifies "
            "segment-specific pain points, triggers, personas, and optimal messaging strategies."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 12 hours",
        "responsibilities": [
            "Analyse Results table → build Market Segment profiles",
            "Count and score leads per segment",
            "Identify segment-specific pain points and triggers",
            "Create segment personas",
            "Track segment performance over time",
        ],
        "key_outputs": ["Market segment profiles", "Segment personas", "Targeting recommendations"],
        "input_tables": ["Results", "ICP_Account_Scores"],
        "output_tables": ["Market_Segments"],
        "tags": ["segments", "market-intelligence", "personas"],
        "file": "7. Market Segment Specialist.json",
    },
    {
        "workflow_id": "WF08",
        "workflow_number": 8,
        "name": "8. Intent Signal Analyst",
        "short_name": "Intent Signals",
        "phase": AgentPhase.INTELLIGENCE,
        "icon": "🔦",
        "complexity_score": 3,
        "description": (
            "Detects and scores buying intent signals from Results + ICP_Account_Scores. "
            "Monitors signal strength, alerts on high-intent accounts, analyses intent "
            "patterns by segment and continuously refines intent models."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Monitor intent signals from live data",
            "Score and prioritise by signal strength",
            "Alert on high-intent accounts",
            "Analyse intent patterns by segment",
            "Refine intent models",
        ],
        "key_outputs": ["Intent scores", "High-intent alerts", "Intent patterns"],
        "input_tables": ["Results", "ICP_Account_Scores"],
        "output_tables": ["Intent_Signals", "Intent_Alerts"],
        "tags": ["intent", "signals", "buying-signals"],
        "file": "8. Intent Signal Analyst.json",
    },
    {
        "workflow_id": "WF09",
        "workflow_number": 9,
        "name": "9. Prospect Research Analyst",
        "short_name": "Prospect Research",
        "phase": AgentPhase.INTELLIGENCE,
        "icon": "🔎",
        "complexity_score": 3,
        "description": (
            "Deep-dive prospect research for qualified accounts. "
            "Enriches prospect profiles with company news, funding events, "
            "hiring signals, technology stack, and executive changes to feed personalised outreach."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Deep-dive prospect profile enrichment",
            "Company news and funding signal monitoring",
            "Technology stack identification",
            "Executive change tracking",
            "Trigger event detection",
        ],
        "key_outputs": ["Enriched prospect profiles", "Trigger events", "Research briefs"],
        "input_tables": ["Results", "ICP_Account_Scores"],
        "output_tables": ["Prospect_Research"],
        "tags": ["research", "enrichment", "prospect"],
        "file": "9. Prospect Research Analyst.json",
    },
    {
        "workflow_id": "WF10",
        "workflow_number": 10,
        "name": "10. Call Intelligence Manager",
        "short_name": "Call Intelligence",
        "phase": AgentPhase.INTELLIGENCE,
        "icon": "🎤",
        "complexity_score": 4,
        "description": (
            "Analyses call recordings and transcripts to extract actionable intelligence. "
            "Identifies objections, competitor mentions, buying signals, and rep coaching moments. "
            "Stores insights in Call_Insights for downstream synthesis."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Process call recordings and transcripts",
            "Extract objections and competitor mentions",
            "Identify buying signals",
            "Generate rep coaching recommendations",
            "Store structured insights",
        ],
        "key_outputs": ["Call insights", "Objection list", "Competitor mentions", "Coaching notes"],
        "input_tables": ["Call_Recordings"],
        "output_tables": ["Call_Insights"],
        "tags": ["call-intelligence", "transcripts", "coaching"],
        "file": "10. Call Intelligence Manager.json",
    },
    {
        "workflow_id": "WF11",
        "workflow_number": 11,
        "name": "11. Insight Synthesiser",
        "short_name": "Insight Synthesis",
        "phase": AgentPhase.INTELLIGENCE,
        "icon": "💡",
        "complexity_score": 4,
        "description": (
            "Synthesises call intelligence, research data, and intent signals into "
            "actionable account-level insights. Feeds WF12 (Follow-Up Drafter) and "
            "WF13 (Channel Outreach Manager) with structured synthesis reports."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Join Call_Recordings + Call_Insights data",
            "Synthesise cross-signal account intelligence",
            "Produce structured synthesis reports",
            "Trigger WF12 for follow-up drafting",
        ],
        "key_outputs": ["Synthesis reports", "Account intelligence summaries"],
        "input_tables": ["Call_Recordings", "Call_Insights"],
        "output_tables": ["Insight_Syntheses"],
        "tags": ["synthesis", "intelligence", "insights"],
        "file": "11. Insight Synthesiser.json",
    },
    {
        "workflow_id": "WF12",
        "workflow_number": 12,
        "name": "12. Follow-Up Drafter Agent",
        "short_name": "Follow-Up Drafter",
        "phase": AgentPhase.INTELLIGENCE,
        "icon": "✉️",
        "complexity_score": 3,
        "description": (
            "Converts synthesised call intelligence into ready-to-send follow-up emails, "
            "Slack messages, and WhatsApp variants. Triggered after WF11. "
            "Drafts are queued for human review before sending."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Draft follow-up emails from call insights",
            "Create Slack message variants",
            "Generate WhatsApp message options",
            "Queue drafts for rep review",
        ],
        "key_outputs": ["Follow-up email drafts", "Slack message drafts", "WhatsApp templates"],
        "input_tables": ["Call_Recordings", "Call_Insights", "Insight_Syntheses"],
        "output_tables": ["Follow_Up_Drafts"],
        "tags": ["follow-up", "email", "drafting"],
        "file": "12. Follow-Up Drafter Agent.json",
    },
    {
        "workflow_id": "WF13",
        "workflow_number": 13,
        "name": "13. Channel Outreach Manager",
        "short_name": "Channel Manager",
        "phase": AgentPhase.INTELLIGENCE,
        "icon": "📡",
        "complexity_score": 4,
        "description": (
            "Routes qualified prospects to the correct outreach channel based on "
            "ICP tier, engagement history, and intent signals. "
            "Assigns: email / voice+WhatsApp / video / event / global BDR channels. "
            "Populates Channel_Outreach_Schedule for downstream specialists."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Evaluate prospect data from Results + Insights",
            "Assign outreach channel per prospect",
            "Route to WF14 (Email), WF14b (Voice/SMS), WF15 (Video), WF16 (Event), WF17 (BDR)",
            "Populate Channel_Outreach_Schedule",
        ],
        "key_outputs": ["Channel assignments", "Channel_Outreach_Schedule records"],
        "input_tables": ["Results", "Insight_Syntheses", "ICP_Account_Scores"],
        "output_tables": ["Channel_Outreach_Schedule"],
        "tags": ["channel", "routing", "outreach"],
        "file": "13. Channel Outreach Manager.json",
    },
    {
        "workflow_id": "WF14a",
        "workflow_number": 14,
        "name": "14. Email Outreach Specialist",
        "short_name": "Email Outreach",
        "phase": AgentPhase.OUTREACH,
        "icon": "📧",
        "complexity_score": 4,
        "description": (
            "Generates and sends personalised outbound email sequences for prospects "
            "assigned to the email channel by WF13. Creates multi-touch sequences "
            "with A/B subject line variants and tracks open/click engagement."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Fetch prospects assigned to email channel",
            "Generate personalised email sequences",
            "A/B subject line testing",
            "Track sends, opens, clicks",
            "Update outreach status",
        ],
        "key_outputs": ["Email sequences", "Send logs", "Engagement metrics"],
        "input_tables": ["Channel_Outreach_Schedule", "Results"],
        "output_tables": ["Email_Outreach", "Email_Outreach_Log"],
        "tags": ["email", "outreach", "sequences"],
        "file": "14. Email Outreach Specialist.json",
    },
    {
        "workflow_id": "WF14b",
        "workflow_number": 14.5,
        "name": "14. Voice & SMS Outreach Specialist",
        "short_name": "Voice & SMS",
        "phase": AgentPhase.OUTREACH,
        "icon": "📱",
        "complexity_score": 4,
        "description": (
            "Generates personalised call scripts and SMS/WhatsApp messages for prospects "
            "assigned to the voice or WhatsApp channel by WF13. "
            "Output: full JSON call script, SMS ≤160 chars, WhatsApp ≤300 chars. "
            "Routing: direct_close | nurture | callback | qualify_further."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Generate full call scripts per prospect",
            "Create SMS messages (≤160 chars)",
            "Create WhatsApp messages (≤300 chars)",
            "Assign routing: direct_close / nurture / callback / qualify_further",
            "Track call status: queued → in_progress → completed/failed",
        ],
        "key_outputs": ["Call scripts", "SMS content", "WhatsApp content", "Routing decisions"],
        "input_tables": ["Channel_Outreach_Schedule", "Results"],
        "output_tables": ["Voice_SMS_Outreach", "Voice_SMS_Log"],
        "tags": ["voice", "sms", "whatsapp", "call-scripts"],
        "file": "14. Voice & SMS Outreach Specialist.json",
    },
    {
        "workflow_id": "WF15",
        "workflow_number": 15,
        "name": "15. Video Prospecting Specialist",
        "short_name": "Video Prospecting",
        "phase": AgentPhase.OUTREACH,
        "icon": "🎬",
        "complexity_score": 4,
        "description": (
            "Creates personalised video scripts (A/B/C variants) for high-value accounts "
            "on Loom, Vidyard, Sendspark, or Hippo Video. "
            "Tracks engagement (views, rewatches, CTA clicks) and routes high-intent viewers. "
            "Video lengths: 60s / 90s / 120s with thumbnail hook and CTA script."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Generate personalised video scripts (A/B/C variants)",
            "Support Loom / Vidyard / Sendspark / Hippo Video",
            "Track view_count, rewatch_count, watch_duration, CTA clicks",
            "Calculate engagement_score (0-100)",
            "Flag high-intent viewers for follow-up",
        ],
        "key_outputs": ["Video scripts", "Thumbnail hooks", "CTA scripts", "Engagement scores"],
        "input_tables": ["Channel_Outreach_Schedule", "Results"],
        "output_tables": ["Video_Prospecting", "Video_Engagement_Log"],
        "tags": ["video", "loom", "vidyard", "prospecting"],
        "file": "15. Video Prospecting Specialist.json",
    },
    {
        "workflow_id": "WF16",
        "workflow_number": 16,
        "name": "16. Event/Webinar Outreach Specialist",
        "short_name": "Event Outreach",
        "phase": AgentPhase.OUTREACH,
        "icon": "🎪",
        "complexity_score": 4,
        "description": (
            "Manages full 4-phase event/webinar outreach lifecycle. "
            "Phase: invitation → reminder_1 (3 days before) → reminder_2 (day before) → post_event. "
            "A/B/C content variants: problem-first / outcome-first / social-proof. "
            "Routing: nurture | sdr | closer | executive."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Phase 1: Invitation outreach (Day 0)",
            "Phase 2: Reminder 1 (3 days before)",
            "Phase 3: Reminder 2 (day before)",
            "Phase 4: Post-event follow-up and routing",
            "A/B/C content variant testing",
        ],
        "key_outputs": ["Invitation sequences", "Reminder sequences", "Post-event follow-ups", "Routing decisions"],
        "input_tables": ["Channel_Outreach_Schedule", "Results"],
        "output_tables": ["Event_Webinar_Outreach", "Event_Outreach_Log"],
        "tags": ["event", "webinar", "outreach", "lifecycle"],
        "file": "16. Event_Webinar Outreach Specialist.json",
    },
    {
        "workflow_id": "WF17",
        "workflow_number": 17,
        "name": "17. Global BDR Manager",
        "short_name": "Global BDR",
        "phase": AgentPhase.OUTREACH,
        "icon": "🌍",
        "complexity_score": 5,
        "description": (
            "Multilingual, culturally-adapted B2B outreach for global markets. "
            "Supports 10 languages with regional compliance (GDPR/LGPD/APPI/PIPL/PDPB). "
            "Dual-fetch: primary from Channel_Outreach_Schedule, fallback from Results (HOT/WARM + international TLDs). "
            "Languages: EN-GB, FR, DE, ES, PT-BR, JA, AR, ZH, EN-APAC."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Generate outreach in 10 languages with cultural adaptation",
            "Regional compliance checks (GDPR, LGPD, APPI, PIPL, PDPB)",
            "Dual-fetch lead sourcing with fallback logic",
            "Localisation checklist and cultural notes",
            "CTA text adaptation per region",
        ],
        "key_outputs": ["Localised email sequences", "Cultural notes", "Compliance flags", "Routing recommendations"],
        "input_tables": ["Channel_Outreach_Schedule", "Results"],
        "output_tables": ["Global_BDR_Outreach", "Global_BDR_Log"],
        "tags": ["global", "multilingual", "bdr", "compliance"],
        "file": "17. Global BDR Manager.json",
    },
    {
        "workflow_id": "WF18",
        "workflow_number": 18,
        "name": "18. Systems Data Manager",
        "short_name": "Systems Manager",
        "phase": AgentPhase.INFRASTRUCTURE,
        "icon": "🛠️",
        "complexity_score": 5,
        "description": (
            "Oversees all sales tech systems, data integrity, integrations, and operational "
            "efficiency across WF1–WF17. "
            "Phases: Setup → Audit (scan all pipeline tables) → AI Analysis (GPT-4o recommendations) "
            "→ Actions (fix stale records, dedup, field gaps) → Report (health dashboard to CSO via email)."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Setup health and log tables",
            "Audit all pipeline tables for data issues",
            "GPT-4o AI analysis and recommendations",
            "Fix stale records and duplicates",
            "Field gap remediation",
            "Generate health dashboard report for CSO",
        ],
        "key_outputs": ["System health report", "Data quality fixes", "CSO email report"],
        "input_tables": ["All pipeline tables"],
        "output_tables": ["System_Health_Log", "Integration_Alerts"],
        "tags": ["infrastructure", "data-quality", "monitoring", "systems"],
        "file": "18. Systems Data Manager.json",
    },
    {
        "workflow_id": "WF19",
        "workflow_number": 19,
        "name": "19. HubSpot Lead",
        "short_name": "HubSpot Sync",
        "phase": AgentPhase.INFRASTRUCTURE,
        "icon": "🔶",
        "complexity_score": 5,
        "description": (
            "Dynamic HubSpot CRM integration (v5.0). "
            "Phases: Setup → Sync (BDR to HubSpot contacts + deals) → Remediate (HOT/stale/compliance) → Report. "
            "Dynamic pipeline and deal stage fetch at runtime. No hardcoded IDs. "
            "Auto-selects pipeline, maps HOT→first stage, WARM→second. Deal retry on existing contacts."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Sync leads from BDR pipeline to HubSpot",
            "Create and update Contacts and Deals dynamically",
            "Remediate stale and non-compliant records",
            "Generate AI health dashboard",
        ],
        "key_outputs": ["HubSpot contacts", "HubSpot deals", "Sync report", "Remediation log"],
        "input_tables": ["Results", "Channel_Outreach_Schedule"],
        "output_tables": ["HubSpot_Contacts", "HubSpot_Deal_Map", "HubSpot_Sync_Log"],
        "tags": ["hubspot", "crm", "integration", "sync"],
        "file": "19. HubSpot Lead.json",
    },
    {
        "workflow_id": "WF20",
        "workflow_number": 20,
        "name": "20. Smartlead Lead",
        "short_name": "Smartlead",
        "phase": AgentPhase.INFRASTRUCTURE,
        "icon": "📧",
        "complexity_score": 5,
        "description": (
            "Manages email sending infrastructure, deliverability, warmup, and campaign performance "
            "for all outbound email activity. "
            "Monitors bounce rates, spam complaints, open rates, blacklist status, and warmup progress. "
            "GPT-4o generates deliverability recommendations."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Monitor email account health metrics",
            "Track bounce rates and spam complaints",
            "Manage email warmup schedules",
            "Monitor blacklist status",
            "Generate AI-powered deliverability recommendations",
        ],
        "key_outputs": ["Deliverability report", "Warmup status", "Campaign performance metrics"],
        "input_tables": ["Email_Outreach_Log"],
        "output_tables": ["Email_Account_Health", "Deliverability_Log", "Campaign_Performance"],
        "tags": ["email", "deliverability", "smartlead", "warmup"],
        "file": "20. Smartlead Lead.json",
    },
    {
        "workflow_id": "WF21",
        "workflow_number": 21,
        "name": "21. Data Warehouse Lead",
        "short_name": "Data Warehouse",
        "phase": AgentPhase.INFRASTRUCTURE,
        "icon": "🗄️",
        "complexity_score": 5,
        "description": (
            "Centralised ETL monitoring, freshness SLA checks, data quality scoring, "
            "decay flagging, archival, and AI-powered root cause analysis across all 20 pipeline tables. "
            "Runs against WF1–WF20 coverage. Reports to Systems & Data Manager (WF18)."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Pipeline freshness SLA checks",
            "Data quality scoring (all 20 pipeline tables)",
            "Data decay flagging and archival",
            "Root cause analysis via AI",
            "ETL pipeline health monitoring",
        ],
        "key_outputs": ["Freshness SLA report", "Data quality scores", "Root cause analysis"],
        "input_tables": ["All pipeline tables (WF1–WF20)"],
        "output_tables": ["DW_Freshness_Log", "DW_Quality_Report", "Archive_Log"],
        "tags": ["data-warehouse", "etl", "quality", "archival"],
        "file": "21. Data Warehouse Lead.json",
    },
    {
        "workflow_id": "WF22",
        "workflow_number": 22,
        "name": "22. Slack Comms Lead",
        "short_name": "Slack Comms",
        "phase": AgentPhase.COMMS,
        "icon": "💬",
        "complexity_score": 3,
        "description": (
            "Manages all Slack-based communications, notifications, and integrations for the sales org. "
            "Posts to #ai-sales-agent channel with reliable text fallback on all nodes. "
            "Covers deal alerts, hot-lead pings, system health summaries, and rep coaching nudges."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 4 hours",
        "responsibilities": [
            "Send deal and hot-lead Slack alerts",
            "Post system health summaries",
            "Manage rep coaching nudges",
            "Integration notifications for pipeline events",
        ],
        "key_outputs": ["Slack notifications", "Alert summaries"],
        "input_tables": ["Results", "System_Health_Log", "Call_Insights"],
        "output_tables": ["Slack_Comms_Log"],
        "tags": ["slack", "notifications", "comms"],
        "file": "22. Slack Comms Lead.json",
    },
    {
        "workflow_id": "WF23",
        "workflow_number": 23,
        "name": "23. Competitive Intel Manager",
        "short_name": "Competitive Intel",
        "phase": AgentPhase.COMMS,
        "icon": "🕵️",
        "complexity_score": 4,
        "description": (
            "Monitors competitor activity, generates battle cards, and provides competitive intelligence. "
            "Fetches live articles with date filtering, injects known competitors into AI prompts, "
            "and applies P1 gate (AI OR funding signal). Maintains Battle_Cards table."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Monitor competitor news and activity",
            "Generate and update battle cards",
            "Funding signal detection",
            "Competitive intelligence injection into sales process",
        ],
        "key_outputs": ["Battle cards", "Competitor alerts", "Competitive intelligence briefs"],
        "input_tables": ["Results"],
        "output_tables": ["Battle_Cards", "Competitive_Intel_Log"],
        "tags": ["competitive", "battle-cards", "intelligence"],
        "file": "23. Competitive Intel Manager.json",
    },
    {
        "workflow_id": "WF24",
        "workflow_number": 24,
        "name": "24. Product Specialist",
        "short_name": "Product Specialist",
        "phase": AgentPhase.COMMS,
        "icon": "🏷️",
        "complexity_score": 3,
        "description": (
            "Maintains deep product knowledge and provides technical sales support for complex deal situations. "
            "Handles technical questions, demo requests, security questionnaires, and product fit analyses. "
            "Reports to Competitive Intel Manager (WF23)."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Maintain product knowledge base",
            "Answer prospect technical questions",
            "Prepare demo request packages",
            "Handle security questionnaires",
            "Product-fit analysis per prospect",
        ],
        "key_outputs": ["Technical Q&A", "Demo packages", "Security questionnaire responses"],
        "input_tables": ["Results", "Call_Insights"],
        "output_tables": ["Product_Knowledge", "Technical_QA_Log"],
        "tags": ["product", "technical", "demo", "security"],
        "file": "24. Product Specialist.json",
    },
    {
        "workflow_id": "WF25",
        "workflow_number": 25,
        "name": "25. Monitoring Agent",
        "short_name": "Monitor",
        "phase": AgentPhase.INFRASTRUCTURE,
        "icon": "📡",
        "complexity_score": 4,
        "description": (
            "Automated continuous monitoring of all systems, data flows, and performance metrics "
            "across WF1–WF24. Monitors: System Health, Data Quality, Performance KPIs, and Integration Status. "
            "Reports to Systems & Data Manager (WF18)."
        ),
        "trigger_types": ["manual", "schedule", "execute_workflow"],
        "schedule_info": "Every 6 hours",
        "responsibilities": [
            "Monitor API sync and integration freshness",
            "Track error rates across all agents",
            "Data completeness and duplicate checks",
            "Performance KPI tracking",
            "Generate monitoring dashboards",
        ],
        "key_outputs": ["System health dashboard", "Alert log", "KPI report"],
        "input_tables": ["All pipeline tables"],
        "output_tables": ["Monitoring_Log", "System_Alerts"],
        "tags": ["monitoring", "health", "kpi", "alerts"],
        "file": "25. Monitoring Agent.json",
    },
    {
        "workflow_id": "WF26",
        "workflow_number": 26,
        "name": "26. Workspace Manager",
        "short_name": "Workspace Manager",
        "phase": AgentPhase.COMMS,
        "icon": "🏢",
        "complexity_score": 5,
        "description": (
            "Administrative layer overseer (AGENT01). Coordinates Meeting Logistics (WF27) "
            "and Content Enablement (WF29) sub-agents. Receives via Webhook/Manual, "
            "creates run IDs, normalises input, and pulls context from 5 live data sources. "
            "Reports to CSO."
        ),
        "trigger_types": ["webhook", "manual", "execute_workflow"],
        "schedule_info": "On-demand",
        "responsibilities": [
            "Administrative coordination across agents",
            "Context aggregation from 5 data sources",
            "Run ID generation and input normalisation",
            "Route to Meeting Logistics and Content Enablement",
            "Report to CSO (WF34)",
        ],
        "key_outputs": ["Coordination decisions", "Run manifests", "Context packages"],
        "input_tables": ["Results", "Call_Prep_Briefs", "Meeting_Schedule"],
        "output_tables": ["AGENT01_Run_Log"],
        "tags": ["workspace", "coordination", "admin", "agent01"],
        "file": "26. Workspace Manager.json",
    },
    {
        "workflow_id": "WF27",
        "workflow_number": 27,
        "name": "27. Meeting Logistics Manager",
        "short_name": "Meetings",
        "phase": AgentPhase.COMMS,
        "icon": "📅",
        "complexity_score": 4,
        "description": (
            "AGENT02. Handles meeting logistics including Google Calendar availability checks, "
            "no-show rate tracking, calendar load analysis, and prep brief status from WF4 + WF24. "
            "Reports to Workspace Manager (WF26). Coordinates with Meeting Scheduler (WF28)."
        ),
        "trigger_types": ["webhook", "manual", "execute_workflow"],
        "schedule_info": "On-demand",
        "responsibilities": [
            "Check real Google Calendar availability",
            "Track no-show rates per contact",
            "Analyse calendar load",
            "Fetch and assess prep brief status",
            "Coordinate scheduling with WF28",
        ],
        "key_outputs": ["Availability windows", "Meeting history", "No-show analysis"],
        "input_tables": ["Call_Prep_Briefs", "Meeting_Schedule"],
        "output_tables": ["AGENT02_Run_Log", "Meeting_Logistics"],
        "tags": ["meetings", "calendar", "logistics", "no-show"],
        "file": "27. Meeting Logistics Manager.json",
    },
    {
        "workflow_id": "WF28",
        "workflow_number": 28,
        "name": "28. Meeting Scheduler Specialist",
        "short_name": "Scheduler",
        "phase": AgentPhase.COMMS,
        "icon": "🗓️",
        "complexity_score": 4,
        "description": (
            "AGENT03. Handles full meeting booking flow. AI Agent makes booking decisions + email content. "
            "Books via Calendly HTTP integration. Sends confirmation via Gmail. "
            "Cron-based reminder checks every hour. Handles reschedule webhooks."
        ),
        "trigger_types": ["execute_workflow", "webhook", "schedule"],
        "schedule_info": "Hourly (reminder checks)",
        "responsibilities": [
            "AI booking decision and email content generation",
            "Calendly slot booking via HTTP",
            "Gmail confirmation sending",
            "Hourly reminder checks",
            "Reschedule request handling",
        ],
        "key_outputs": ["Booked meetings", "Confirmation emails", "Reminder sequences"],
        "input_tables": ["Meeting_Logistics"],
        "output_tables": ["Meeting_Schedule", "AGENT03_Run_Log"],
        "tags": ["scheduling", "calendly", "gmail", "reminders"],
        "file": "28. Meeting Scheduler Specialist.json",
    },
    {
        "workflow_id": "WF29",
        "workflow_number": 29,
        "name": "29. Content Enablement Manager",
        "short_name": "Content Manager",
        "phase": AgentPhase.CONTENT,
        "icon": "📚",
        "complexity_score": 4,
        "description": (
            "Manages the full sales content library and governs content creation workflows. "
            "Weekly audit on Mondays at 7AM. Classifies and routes content requests, "
            "triggers specialised WF30 (Copywriter), WF31 (Proposal), WF32 (Asset Librarian). "
            "Reports to Workspace Manager (WF26)."
        ),
        "trigger_types": ["webhook", "schedule", "execute_workflow"],
        "schedule_info": "Weekly audit + on-demand",
        "responsibilities": [
            "Parse and classify content requests",
            "Route to WF30 (Sequence Copywriter) or WF31 (Proposal) or WF32 (Asset Librarian)",
            "Weekly content library audit",
            "Content governance and version control",
        ],
        "key_outputs": ["Content library status", "Routed content requests", "Audit report"],
        "input_tables": ["Sales_Asset_Repository"],
        "output_tables": ["Content_Requests", "Content_Audit_Log"],
        "tags": ["content", "enablement", "library", "governance"],
        "file": "29. Content Enablement Manager.json",
    },
    {
        "workflow_id": "WF30",
        "workflow_number": 30,
        "name": "30. Sequence Copywriter",
        "short_name": "Copywriter",
        "phase": AgentPhase.CONTENT,
        "icon": "✍️",
        "complexity_score": 3,
        "description": (
            "Writes high-converting cold outreach, warm follow-up, post-meeting, and re-engagement "
            "email sequences on demand. Sequence types: Cold (5-7 emails), Warm Follow-Up, "
            "Post-Meeting, Re-Engagement. Routed from WF29."
        ),
        "trigger_types": ["webhook", "execute_workflow"],
        "schedule_info": "On-demand",
        "responsibilities": [
            "Write cold outreach sequences (5-7 emails)",
            "Create warm follow-up sequences",
            "Post-meeting sequence drafting",
            "Re-engagement sequence creation",
        ],
        "key_outputs": ["Email sequences", "Subject line variants", "CTA recommendations"],
        "input_tables": ["Content_Requests", "Results"],
        "output_tables": ["Email_Sequences"],
        "tags": ["copywriting", "sequences", "email", "content"],
        "file": "30. Sequence Copywriter.json",
    },
    {
        "workflow_id": "WF31",
        "workflow_number": 31,
        "name": "31. Proposal Copy Specialist",
        "short_name": "Proposals",
        "phase": AgentPhase.CONTENT,
        "icon": "📋",
        "complexity_score": 4,
        "description": (
            "Creates winning, highly personalised proposals using a strict 6-section framework. "
            "Every proposal is written for executives to read in under 2 minutes. "
            'Triggered when CRM opportunity stage = "Proposal Sent". Routed from WF29.'
        ),
        "trigger_types": ["webhook", "execute_workflow"],
        "schedule_info": "On-demand",
        "responsibilities": [
            "Write 6-section proposal framework",
            "Personalise for executive audience (< 2-minute read)",
            "Integrate competitive intelligence",
            "Generate ROI and value statements",
        ],
        "key_outputs": ["Personalised proposals", "6-section proposal documents"],
        "input_tables": ["Content_Requests", "Results", "Battle_Cards"],
        "output_tables": ["Proposals"],
        "tags": ["proposals", "copywriting", "executives", "content"],
        "file": "31. Proposal Copy Specialist.json",
    },
    {
        "workflow_id": "WF32",
        "workflow_number": 32,
        "name": "32. Sales Asset Librarian",
        "short_name": "Asset Librarian",
        "phase": AgentPhase.CONTENT,
        "icon": "🗂️",
        "complexity_score": 3,
        "description": (
            "Manages Sales_Asset_Repository — indexes, retrieves, tracks usage, and flags stale assets. "
            "Routes ASSET_REQUEST from WF26 (Workspace Manager). "
            "Daily 9AM audit + on-demand webhook trigger. "
            "Tables: Sales_Asset_Repository, Asset_Request_Log, Asset_Usage_Log."
        ),
        "trigger_types": ["webhook", "schedule", "execute_workflow"],
        "schedule_info": "Daily 9AM + on-demand",
        "responsibilities": [
            "Index and retrieve sales assets",
            "Track asset usage across reps",
            "Flag stale or outdated assets",
            "Handle asset requests from workspace",
        ],
        "key_outputs": ["Asset retrieval", "Usage reports", "Stale asset flags"],
        "input_tables": ["Content_Requests"],
        "output_tables": ["Sales_Asset_Repository", "Asset_Request_Log", "Asset_Usage_Log"],
        "tags": ["assets", "library", "content", "sales-collateral"],
        "file": "32. Sales Asset Librarian.json",
    },
    {
        "workflow_id": "WF33",
        "workflow_number": 33,
        "name": "33. CRM Handoff Agent",
        "short_name": "CRM Handoff",
        "phase": AgentPhase.CONTENT,
        "icon": "🔄",
        "complexity_score": 4,
        "description": (
            "Ensures clean handoff of QUALIFIED leads into CRM. "
            "Validates data completeness, enriches handoff package, updates Results table, "
            "writes to CRM_Contacts, and notifies the assigned rep. "
            "Schedule: 6AM daily + webhook + execute workflow."
        ),
        "trigger_types": ["webhook", "schedule", "execute_workflow"],
        "schedule_info": "Daily 6AM + on-demand",
        "responsibilities": [
            "Validate qualified lead data completeness",
            "Enrich CRM handoff package",
            "Update Results table status",
            "Write to CRM_Contacts",
            "Notify assigned rep",
        ],
        "key_outputs": ["CRM records", "Handoff packages", "Rep notifications"],
        "input_tables": ["Results (QUALIFIED)", "CRM_Contacts", "HubSpot_Deal_Map"],
        "output_tables": ["CRM_Handoff_Log", "CRM_Contacts"],
        "tags": ["crm", "handoff", "qualified", "closing"],
        "file": "33. CRM Handoff Agent.json",
    },
    {
        "workflow_id": "WF34",
        "workflow_number": 34,
        "name": "34. Chief Sales Officer (CSO) Agent",
        "short_name": "CSO Agent",
        "phase": AgentPhase.CONTENT,
        "icon": "👔",
        "complexity_score": 5,
        "description": (
            "Top-level AI orchestrator acting as Chief Sales Officer. "
            "9 direct reports: WF13, WF17, WF18, WF23, WF3, WF6, WF10, WF26, WF29. "
            "Escalation triggers: deal_value > 50K, P1 issues, legal flags, human requests. "
            "Tables: CSO_Run_Log, CSO_Escalations, CSO_Delegation_Queue, CSO_Weekly_Reports."
        ),
        "trigger_types": ["webhook", "schedule", "manual"],
        "schedule_info": "Every 6h + Weekly Mon 6AM",
        "responsibilities": [
            "9 direct report management and delegation",
            "Escalation handling (> $50K, P1, legal, human)",
            "Weekly performance reports",
            "Strategic pipeline oversight",
            "Cross-agent coordination",
        ],
        "key_outputs": ["CSO reports", "Escalation decisions", "Delegation queue", "Weekly summaries"],
        "input_tables": ["CSO_Run_Log", "Results", "System_Health_Log"],
        "output_tables": ["CSO_Run_Log", "CSO_Escalations", "CSO_Delegation_Queue", "CSO_Weekly_Reports"],
        "tags": ["cso", "orchestrator", "executive", "escalation"],
        "file": "34. Chief Sales Officer (CSO) Agent.json",
    },
]

# Phase display info
PHASE_META = {
    AgentPhase.ORCHESTRATOR: {"label": "Master Orchestrator", "color": "#7c3aed", "icon": "🎯"},
    AgentPhase.DISCOVERY: {"label": "Phase 1 — Discovery", "color": "#2563eb", "icon": "🔍"},
    AgentPhase.INTELLIGENCE: {"label": "Phase 2 — Research & Intelligence", "color": "#0891b2", "icon": "🧠"},
    AgentPhase.OUTREACH: {"label": "Phase 3 — Outreach", "color": "#059669", "icon": "📤"},
    AgentPhase.INFRASTRUCTURE: {"label": "Infrastructure Layer", "color": "#d97706", "icon": "⚙️"},
    AgentPhase.COMMS: {"label": "Phase 4 — Comms & Agents", "color": "#db2777", "icon": "💬"},
    AgentPhase.CONTENT: {"label": "Phase 5 — Content & Closing", "color": "#7c3aed", "icon": "📝"},
}


class Command(BaseCommand):
    help = "Seed SalesAgent records from the n8n JSON workflow catalog"

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Clear all agents before seeding")

    def handle(self, *args, **options):
        if options["clear"]:
            SalesAgent.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared all agents."))

        created = updated = 0
        for data in AGENT_CATALOG:
            file_path = os.path.join(AGENTS_DIR, data.get("file", ""))
            raw_json = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path) as f:
                        raw_json = json.load(f)
                except Exception:
                    pass

            defaults = {
                "workflow_number": data["workflow_number"],
                "name": data["name"],
                "short_name": data.get("short_name", ""),
                "description": data.get("description", ""),
                "phase": data["phase"],
                "icon": data.get("icon", "🤖"),
                "complexity_score": data.get("complexity_score", 3),
                "trigger_types": data.get("trigger_types", []),
                "schedule_info": data.get("schedule_info", ""),
                "node_count": len(raw_json.get("nodes", [])),
                "input_tables": data.get("input_tables", []),
                "output_tables": data.get("output_tables", []),
                "responsibilities": data.get("responsibilities", []),
                "key_outputs": data.get("key_outputs", []),
                "tags": data.get("tags", []),
                "raw_json": {"name": raw_json.get("name", ""), "node_count": len(raw_json.get("nodes", []))},
                "is_active": True,
            }

            obj, was_created = SalesAgent.objects.update_or_create(
                workflow_id=data["workflow_id"],
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        # Wire up reports_to relationships
        wf_map = {a.workflow_id: a for a in SalesAgent.objects.all()}

        relationships = {
            "WF01": "WF06",
            "WF02": "WF06",
            "WF03": "WF06",
            "WF04": "WF06",
            "WF05": "WF06",
            "WF08": "WF06",
            "WF09": "WF06",
            "WF10": "WF34",
            "WF11": "WF34",
            "WF12": "WF34",
            "WF13": "WF34",
            "WF17": "WF34",
            "WF18": "WF34",
            "WF23": "WF34",
            "WF06": "WF34",
            "WF26": "WF34",
            "WF29": "WF34",
            "WF14a": "WF13",
            "WF14b": "WF13",
            "WF15": "WF13",
            "WF16": "WF13",
            "WF19": "WF18",
            "WF20": "WF18",
            "WF21": "WF18",
            "WF25": "WF18",
            "WF22": "WF18",
            "WF24": "WF23",
            "WF27": "WF26",
            "WF32": "WF26",
            "WF28": "WF27",
            "WF30": "WF29",
            "WF31": "WF29",
            "WF33": "WF29",
        }
        for child_id, parent_id in relationships.items():
            if child_id in wf_map and parent_id in wf_map:
                wf_map[child_id].reports_to = wf_map[parent_id]
                wf_map[child_id].save(update_fields=["reports_to"])

        self.stdout.write(
            self.style.SUCCESS(f"✅ Done — {created} created, {updated} updated ({created + updated} total agents).")
        )
