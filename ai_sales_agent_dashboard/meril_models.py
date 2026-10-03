"""
Django models for the Meril n8n agent pipeline data tables.
These are read-only mirror models (managed=False).
Physical tables are created/populated by the load_meril_data management command.
"""

from django.db import models


class BusinessLead(models.Model):
    """WF01–WF07 discovery output: raw scraped business leads."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255, null=True, blank=True)
    business_name = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Business_Leads"
        app_label = "ai_sales_agent_dashboard"

    def __str__(self):
        return self.business_name or self.place_id or str(self.id)


class ICPAccountScore(models.Model):
    """WF08/WF09 ICP scoring output."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255)
    business_name = models.TextField(null=True, blank=True)
    firmographic_score = models.IntegerField(default=0)
    technographic_score = models.IntegerField(default=0)
    behavioral_score = models.IntegerField(default=0)
    timing_score = models.IntegerField(default=0)
    icp_fit_score = models.IntegerField(default=0)
    account_tier = models.CharField(max_length=20, default="T4_NURTURE")
    tier_label = models.CharField(max_length=50, default="Nurture")
    recommended_action = models.TextField(null=True, blank=True)
    industry_fit_tier = models.CharField(max_length=30, null=True, blank=True)
    geo_fit_tier = models.CharField(max_length=30, null=True, blank=True)
    size_fit = models.CharField(max_length=30, null=True, blank=True)
    has_website = models.SmallIntegerField(default=0)
    has_ecommerce = models.SmallIntegerField(default=0)
    has_booking_system = models.SmallIntegerField(default=0)
    social_channel_count = models.IntegerField(default=0)
    tech_sophistication = models.CharField(max_length=20, default="LOW")
    review_velocity = models.CharField(max_length=20, default="LOW")
    digital_engagement = models.CharField(max_length=20, default="LOW")
    active_buying_signals = models.IntegerField(default=0)
    trigger_events_detected = models.IntegerField(default=0)
    timing_urgency = models.CharField(max_length=20, default="LOW")
    fit_drivers = models.JSONField(null=True, blank=True)
    fit_gaps = models.JSONField(null=True, blank=True)
    entry_point = models.TextField(null=True, blank=True)
    score_version = models.IntegerField(default=1)
    last_scored = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "ICP_Account_Scores"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-icp_fit_score"]

    def __str__(self):
        return f"{self.business_name or self.place_id} — {self.tier_label} ({self.icp_fit_score})"

    @property
    def tier_color(self):
        return {
            "T1_HOT": "#ef4444",
            "T2_WARM": "#f97316",
            "T3_WARM": "#f97316",
            "T3_DEVELOP": "#eab308",
            "T3_OPPORTUNITY": "#eab308",
            "T4_NURTURE": "#6b7280",
        }.get(self.account_tier, "#6b7280")


class AIEnhancedBrief(models.Model):
    """WF11 AI enhanced brief output."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255)
    business_name = models.TextField(null=True, blank=True)
    ai_company_analysis = models.TextField(null=True, blank=True)
    ai_pain_point_analysis = models.TextField(null=True, blank=True)
    ai_contact_persona = models.TextField(null=True, blank=True)
    ai_competitive_intelligence = models.TextField(null=True, blank=True)
    ai_personalized_questions = models.TextField(null=True, blank=True)
    ai_talk_tracks = models.TextField(null=True, blank=True)
    ai_size_estimate = models.CharField(max_length=50, null=True, blank=True)
    ai_growth_stage = models.CharField(max_length=50, null=True, blank=True)
    ai_priority_score = models.IntegerField(null=True, blank=True)
    openai_model_used = models.CharField(max_length=50, null=True, blank=True)
    tokens_used = models.IntegerField(null=True, blank=True)
    processing_time_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "AI_Enhanced_Briefs"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-ai_priority_score"]

    def __str__(self):
        return f"Brief: {self.business_name or self.place_id}"


class DiscoveryQueue(models.Model):
    """WF05 discovery queue (leads ready for outreach)."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255)
    business_name = models.TextField(null=True, blank=True)
    contact_phone = models.CharField(max_length=50, null=True, blank=True)
    contact_email = models.CharField(max_length=255, null=True, blank=True)
    discovery_brief = models.JSONField(null=True, blank=True)
    discovery_status = models.CharField(max_length=50, default="READY_FOR_DISCOVERY")
    priority = models.CharField(max_length=20, default="MEDIUM")
    mql_stage = models.CharField(max_length=20, null=True, blank=True)
    buyer_persona = models.CharField(max_length=50, null=True, blank=True)
    icp_fit = models.CharField(max_length=20, null=True, blank=True)
    bant_score = models.IntegerField(default=0)
    wf2_score = models.IntegerField(default=0)
    wf2_tier = models.CharField(max_length=20, null=True, blank=True)
    prepared_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.CharField(max_length=100, null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Discovery_Queue"
        app_label = "ai_sales_agent_dashboard"

    def __str__(self):
        return f"DQ: {self.business_name or self.place_id} [{self.discovery_status}]"


class DiscoveryBrief(models.Model):
    """WF06 discovery brief (BANT-qualified briefs)."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255)
    business_name = models.TextField(null=True, blank=True)
    contact_phone = models.CharField(max_length=50, null=True, blank=True)
    contact_email = models.CharField(max_length=255, null=True, blank=True)
    tldr_summary = models.TextField(null=True, blank=True)
    bant_breakdown = models.TextField(null=True, blank=True)
    meddic_questions = models.TextField(null=True, blank=True)
    talking_points = models.TextField(null=True, blank=True)
    buyer_persona = models.CharField(max_length=50, null=True, blank=True)
    icp_fit = models.CharField(max_length=20, null=True, blank=True)
    wf2_score = models.IntegerField(default=0)
    bant_score = models.IntegerField(default=0)
    mql_stage = models.CharField(max_length=20, null=True, blank=True)
    lead_temperature = models.CharField(max_length=20, null=True, blank=True)
    lead_score = models.IntegerField(default=0)
    wf2_tier = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Discovery_Briefs"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-bant_score"]

    def __str__(self):
        return f"DB: {self.business_name or self.place_id} [{self.lead_temperature}]"


class ScrapedResult(models.Model):
    """WF01 raw Google Maps / scraper output."""

    place_id = models.CharField(max_length=255, primary_key=True)  # Google Maps Place ID — unique per business
    xid = models.CharField(max_length=100, null=True, blank=True)  # n8n batch/category ID (not unique)
    action = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=50, null=True, blank=True)
    title = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    name = models.TextField(null=True, blank=True)
    firstname = models.TextField(null=True, blank=True)
    lastname = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    clean_url = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    facebook = models.TextField(null=True, blank=True)
    instagram = models.TextField(null=True, blank=True)
    youtube = models.TextField(null=True, blank=True)
    linkedin = models.TextField(null=True, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    reviews = models.TextField(null=True, blank=True)
    type = models.TextField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    lead_score = models.IntegerField(default=0)
    lead_tier = models.CharField(max_length=20, null=True, blank=True)
    firmographic_score = models.IntegerField(default=0)
    behavioral_score = models.IntegerField(default=0)
    engagement_score = models.IntegerField(default=0)
    qualification_status = models.CharField(max_length=50, null=True, blank=True)
    mql_stage = models.CharField(max_length=20, null=True, blank=True)
    buyer_persona = models.CharField(max_length=50, null=True, blank=True)
    bant_score = models.IntegerField(default=0)
    # ICP scoring (folded into Results after ICP_Account_Scores table was retired)
    icp_score = models.IntegerField(default=0)
    icp_tier = models.CharField(max_length=10, null=True, blank=True)
    icp_fit_reason = models.TextField(null=True, blank=True)
    enrichment_status = models.CharField(max_length=20, default="pending")
    intent_score = models.IntegerField(default=0)
    intent_tier = models.CharField(max_length=20, null=True, blank=True)
    prospect_research_status = models.CharField(max_length=50, default="not_researched")
    call_status = models.CharField(max_length=50, default="not_called")
    call_count = models.IntegerField(default=0)
    last_call_outcome = models.CharField(max_length=100, null=True, blank=True)
    insight_status = models.CharField(max_length=50, default="pending")
    research_status = models.CharField(max_length=50, default="PENDING")

    class Meta:
        managed = False
        db_table = "Results"
        app_label = "ai_sales_agent_dashboard"

    def __str__(self):
        return self.title or self.name or self.place_id


class CSORunLog(models.Model):
    """WF00 Master Orchestrator / Monitor run log (Monitor_Run_Log table)."""

    id = models.AutoField(primary_key=True)
    run_id = models.CharField(max_length=64)
    run_type = models.CharField(max_length=32, null=True, blank=True)
    checks_run = models.IntegerField(default=0)
    p1_count = models.IntegerField(default=0)
    p2_count = models.IntegerField(default=0)
    p3_count = models.IntegerField(default=0)
    overall_status = models.CharField(max_length=32, null=True, blank=True)
    health_score = models.IntegerField(default=50)
    duration_ms = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Monitor_Run_Log"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Monitor Run {self.run_id} [{self.run_type}]"

    @property
    def pipeline_health_score(self):
        return self.health_score

    @property
    def health_color(self):
        if self.health_score >= 80:
            return "#10b981"
        elif self.health_score >= 60:
            return "#f59e0b"
        return "#ef4444"


class MeetingBooking(models.Model):
    """WF24/WF25 meeting booking records."""

    id = models.AutoField(primary_key=True)
    booking_id = models.CharField(max_length=64)
    run_id = models.CharField(max_length=64)
    meeting_id = models.CharField(max_length=64, null=True, blank=True)
    deal_id = models.CharField(max_length=100, null=True, blank=True)
    place_id = models.CharField(max_length=255, null=True, blank=True)
    prospect_first_name = models.CharField(max_length=100, null=True, blank=True)
    prospect_email = models.CharField(max_length=255, null=True, blank=True)
    rep_name = models.CharField(max_length=255, null=True, blank=True)
    rep_email = models.CharField(max_length=255, null=True, blank=True)
    meeting_type = models.CharField(max_length=50, null=True, blank=True)
    booking_status = models.CharField(max_length=50, default="pending")
    selected_slot = models.DateTimeField(null=True, blank=True)
    prospect_timezone = models.CharField(max_length=100, null=True, blank=True)
    video_link = models.CharField(max_length=500, null=True, blank=True)
    agenda = models.TextField(null=True, blank=True)
    confirmation_email_sent = models.SmallIntegerField(default=0)
    reminder_24h_sent = models.SmallIntegerField(default=0)
    reminder_1h_sent = models.SmallIntegerField(default=0)
    reschedule_count = models.IntegerField(default=0)
    no_show_triggered = models.SmallIntegerField(default=0)
    crm_logged = models.SmallIntegerField(default=0)
    tokens_used = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Meeting_Bookings"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Meeting {self.booking_id} — {self.prospect_first_name} [{self.booking_status}]"


class LeadScore(models.Model):
    """Lead score records."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255, null=True, blank=True)
    lead_score = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Lead_Scores"
        app_label = "ai_sales_agent_dashboard"

    def __str__(self):
        return f"{self.place_id}: {self.lead_score}"


class CRMContact(models.Model):
    """CRM contact records synced from HubSpot (WF18–WF21)."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255, null=True, blank=True)
    firstname = models.CharField(max_length=100, null=True, blank=True)
    lastname = models.CharField(max_length=100, null=True, blank=True)
    email = models.CharField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=100, null=True, blank=True)
    company = models.CharField(max_length=255, null=True, blank=True)
    lead_status = models.CharField(max_length=50, default="New")
    lifecycle_stage = models.CharField(max_length=50, default="lead")
    lead_tier = models.CharField(max_length=20, null=True, blank=True)
    lead_score = models.IntegerField(default=0)
    assigned_language = models.CharField(max_length=50, null=True, blank=True)
    assigned_region = models.CharField(max_length=100, null=True, blank=True)
    timezone_region = models.CharField(max_length=100, null=True, blank=True)
    outreach_channel = models.CharField(max_length=50, null=True, blank=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "CRM_Contacts"
        app_label = "ai_sales_agent_dashboard"

    def __str__(self):
        return f"{self.firstname} {self.lastname} — {self.company}"


class ProspectResearchProfile(models.Model):
    """WF12 prospect research profiles."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255)
    business_name = models.TextField(null=True, blank=True)
    prospect_name = models.TextField(null=True, blank=True)
    prospect_title = models.TextField(null=True, blank=True)
    company_summary = models.TextField(null=True, blank=True)
    pain_points = models.TextField(null=True, blank=True)
    personalisation_hooks = models.TextField(null=True, blank=True)
    conversation_starters = models.TextField(null=True, blank=True)
    outreach_angle = models.TextField(null=True, blank=True)
    buying_triggers = models.TextField(null=True, blank=True)
    competitor_mentions = models.TextField(null=True, blank=True)
    tech_stack = models.TextField(null=True, blank=True)
    recommended_channel = models.CharField(max_length=50, null=True, blank=True)
    data_quality_score = models.IntegerField(default=0)
    enrichment_source = models.CharField(max_length=100, null=True, blank=True)
    research_status = models.CharField(max_length=50, default="pending")
    personalisation_ready = models.SmallIntegerField(default=0)
    last_enriched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Prospect_Research_Profiles"
        app_label = "ai_sales_agent_dashboard"

    def __str__(self):
        return f"PRP: {self.business_name or self.place_id} — {self.prospect_name}"


class HubSpotDealMap(models.Model):
    """HubSpot deal mapping (WF18–WF21)."""

    id = models.AutoField(primary_key=True)
    place_id = models.CharField(max_length=255, null=True, blank=True)
    business_name = models.CharField(max_length=255, null=True, blank=True)
    contact_name = models.CharField(max_length=255, null=True, blank=True)
    contact_email = models.CharField(max_length=255, null=True, blank=True)
    contact_phone = models.CharField(max_length=100, null=True, blank=True)
    hs_contact_id = models.CharField(max_length=64, null=True, blank=True)
    hs_deal_id = models.CharField(max_length=64, null=True, blank=True)
    deal_name = models.CharField(max_length=255, null=True, blank=True)
    deal_stage = models.CharField(max_length=100, default="Discovery")
    deal_owner = models.CharField(max_length=100, null=True, blank=True)
    lead_tier = models.CharField(max_length=20, null=True, blank=True)
    lead_score = models.IntegerField(default=0)
    outreach_priority = models.CharField(max_length=20, null=True, blank=True)
    routing_recommendation = models.CharField(max_length=50, null=True, blank=True)
    assigned_language = models.CharField(max_length=50, null=True, blank=True)
    assigned_region = models.CharField(max_length=100, null=True, blank=True)
    engagement_score = models.IntegerField(default=0)
    quality_score = models.IntegerField(default=0)
    lifecycle_stage = models.CharField(max_length=50, default="lead")
    lead_status = models.CharField(max_length=50, default="New")
    last_activity_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "HubSpot_Deal_Map"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-lead_score"]

    def __str__(self):
        return f"{self.deal_name or self.business_name} [{self.deal_stage}]"


class EmailSequence(models.Model):
    """WF14a/WF14b/WF15 email sequence records."""

    id = models.AutoField(primary_key=True)
    sequence_id = models.CharField(max_length=64)
    run_id = models.CharField(max_length=64)
    sequence_type = models.CharField(max_length=30)
    lead_id = models.CharField(max_length=100, null=True, blank=True)
    company = models.CharField(max_length=200, null=True, blank=True)
    product_name = models.CharField(max_length=200, null=True, blank=True)
    icp_description = models.TextField(null=True, blank=True)
    key_pain_point = models.TextField(null=True, blank=True)
    proof_metric = models.CharField(max_length=500, null=True, blank=True)
    competitor_context = models.CharField(max_length=500, null=True, blank=True)
    rep_name = models.CharField(max_length=100, null=True, blank=True)
    rep_title = models.CharField(max_length=100, null=True, blank=True)
    email_count = models.SmallIntegerField(default=0)
    status = models.CharField(max_length=20, default="draft")
    notion_url = models.CharField(max_length=500, null=True, blank=True)
    tokens_used = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Email_Sequences"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Seq {self.sequence_id} — {self.company} [{self.sequence_type}]"


class WinLossAnalysis(models.Model):
    """WF34 win/loss analysis."""

    id = models.AutoField(primary_key=True)
    run_id = models.CharField(max_length=64, null=True, blank=True)
    period = models.CharField(max_length=20, null=True, blank=True)
    total_deals = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    win_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    top_loss_reasons = models.TextField(null=True, blank=True)
    competitor_mentions = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Win_Loss_Analysis"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-created_at"]

    def __str__(self):
        return f"W/L {self.period}: {self.wins}/{self.total_deals} ({self.win_rate}%)"


class Proposal(models.Model):
    """WF31 proposal records."""

    id = models.AutoField(primary_key=True)
    proposal_id = models.CharField(max_length=64)
    run_id = models.CharField(max_length=64)
    prospect_company = models.CharField(max_length=200, null=True, blank=True)
    prospect_name = models.CharField(max_length=200, null=True, blank=True)
    lead_id = models.CharField(max_length=100, null=True, blank=True)
    deal_id = models.CharField(max_length=100, null=True, blank=True)
    rep_name = models.CharField(max_length=100, null=True, blank=True)
    proposed_solution = models.TextField(null=True, blank=True)
    pricing = models.CharField(max_length=500, null=True, blank=True)
    timeline_urgency = models.CharField(max_length=500, null=True, blank=True)
    proposal_expiry = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, default="draft")
    review_required = models.SmallIntegerField(default=1)
    review_reason = models.TextField(null=True, blank=True)
    word_count = models.IntegerField(default=0)
    notion_url = models.CharField(max_length=500, null=True, blank=True)
    docusign_url = models.CharField(max_length=500, null=True, blank=True)
    tokens_used = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Proposals"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Proposal {self.proposal_id} — {self.prospect_company} [{self.status}]"


class CallInsight(models.Model):
    """WF26 call intelligence analysis."""

    id = models.AutoField(primary_key=True)
    call_id = models.CharField(max_length=255)
    place_id = models.CharField(max_length=255, null=True, blank=True)
    business_name = models.TextField(null=True, blank=True)
    overall_sentiment = models.CharField(max_length=50, null=True, blank=True)
    sentiment_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    engagement_level = models.CharField(max_length=50, null=True, blank=True)
    talk_ratio_rep = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    talk_ratio_prospect = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    question_count = models.IntegerField(default=0)
    key_pain_points = models.TextField(null=True, blank=True)
    decision_criteria = models.TextField(null=True, blank=True)
    objections_raised = models.TextField(null=True, blank=True)
    objections_handled = models.TextField(null=True, blank=True)
    competitor_mentions = models.TextField(null=True, blank=True)
    buying_signals = models.TextField(null=True, blank=True)
    red_flags = models.TextField(null=True, blank=True)
    next_steps_agreed = models.TextField(null=True, blank=True)
    action_items = models.TextField(null=True, blank=True)
    call_outcome = models.CharField(max_length=100, null=True, blank=True)
    outcome_probability = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    ai_summary = models.TextField(null=True, blank=True)
    ai_coaching_notes = models.TextField(null=True, blank=True)
    ai_call_score = models.IntegerField(default=0)
    tokens_used = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Call_Insights"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Call {self.call_id} — {self.business_name} [{self.overall_sentiment}]"


class ProductCatalog(models.Model):
    """Meril product catalog — read directly from the n8n-managed Meril_Products table."""

    id = models.AutoField(primary_key=True)
    productname = models.CharField(max_length=700, null=True, blank=True)
    category = models.CharField(max_length=300, null=True, blank=True)
    subcategory = models.CharField(max_length=300, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    specifications = models.TextField(null=True, blank=True)
    price = models.CharField(max_length=100, null=True, blank=True)
    availabilitystatus = models.CharField(max_length=100, null=True, blank=True)
    producturl = models.CharField(max_length=700, null=True, blank=True)
    manufacturer = models.CharField(max_length=300, null=True, blank=True)
    clinicalapplications = models.TextField(null=True, blank=True)
    features = models.TextField(null=True, blank=True)
    regulatoryinfo = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "Meril_Products"
        app_label = "ai_sales_agent_dashboard"
        ordering = ["productname"]
        verbose_name = "Product Catalog"
        verbose_name_plural = "Product Catalogs"

    def __str__(self):
        return self.productname or str(self.id)


class AZZip(models.Model):
    """AZ zip-code lookup table used by the n8n lead-generation pipeline."""

    zip = models.CharField(max_length=10, primary_key=True)
    status = models.CharField(max_length=50, null=True, blank=True)
    subcat = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "AZ_Zips"
        app_label = "ai_sales_agent_dashboard"

    def __str__(self):
        return self.zip
