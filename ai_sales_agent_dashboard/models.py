from django.db import models
from django.utils import timezone

# ─────────────────────────────────────────────────────────────────────────────
# Agent Catalog  (seeded from JSON files, superuser-managed)
# ─────────────────────────────────────────────────────────────────────────────


class AgentPhase(models.TextChoices):
    ORCHESTRATOR = "orchestrator", "Master Orchestrator"
    DISCOVERY = "discovery", "Phase 1 — Discovery"
    INTELLIGENCE = "intelligence", "Phase 2 — Research & Intelligence"
    OUTREACH = "outreach", "Phase 3 — Outreach"
    COMMS = "comms", "Phase 4 — Comms & Agents"
    CONTENT = "content", "Phase 5 — Content & Closing"
    INFRASTRUCTURE = "infrastructure", "Infrastructure Layer"


class AgentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    TESTING = "testing", "Testing"


class SalesAgent(models.Model):
    """Represents an AI sales agent (n8n workflow)."""

    workflow_id = models.CharField(max_length=20, unique=True)  # e.g. "WF01"
    workflow_number = models.FloatField(default=0)  # for ordering (0=orchestrator, 1-34 others)
    name = models.CharField(max_length=255)
    short_name = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    phase = models.CharField(max_length=30, choices=AgentPhase.choices, default=AgentPhase.DISCOVERY)
    status = models.CharField(max_length=20, choices=AgentStatus.choices, default=AgentStatus.ACTIVE)

    # Workflow details
    trigger_types = models.JSONField(default=list, blank=True)  # ['manual','schedule','webhook']
    schedule_info = models.CharField(max_length=200, blank=True)  # "Every 6 hours"
    node_count = models.PositiveIntegerField(default=0)
    reports_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="sub_agents")
    direct_reports = models.ManyToManyField("self", blank=True, symmetrical=False, related_name="manager_of")

    # Input/Output spec (for future DB integration)
    input_tables = models.JSONField(default=list, blank=True)  # DB table names read
    output_tables = models.JSONField(default=list, blank=True)  # DB table names written
    input_schema = models.JSONField(default=dict, blank=True)  # field definitions
    output_schema = models.JSONField(default=dict, blank=True)

    # Key responsibilities / key points
    responsibilities = models.JSONField(default=list, blank=True)  # list of strings
    key_outputs = models.JSONField(default=list, blank=True)  # list of strings
    tags = models.JSONField(default=list, blank=True)  # searchable tags

    # Scoring / display
    complexity_score = models.PositiveSmallIntegerField(default=1)  # 1-5
    icon = models.CharField(max_length=50, default="🤖")

    # Raw n8n JSON for reference
    raw_json = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sales Agent"
        verbose_name_plural = "Sales Agents"
        ordering = ["workflow_number"]

    def __str__(self):
        return f"{self.workflow_id} — {self.name}"

    @property
    def phase_display(self):
        return dict(AgentPhase.choices).get(self.phase, self.phase)

    @property
    def complexity_label(self):
        return {1: "Simple", 2: "Basic", 3: "Moderate", 4: "Complex", 5: "Advanced"}.get(self.complexity_score, "")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Execution Log  (future DB integration)
# ─────────────────────────────────────────────────────────────────────────────


class AgentRun(models.Model):
    """Records a single execution run of an agent (populated from n8n DB)."""

    class RunStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial"

    agent = models.ForeignKey(SalesAgent, on_delete=models.CASCADE, related_name="runs")
    run_id = models.CharField(max_length=100, blank=True)  # n8n execution ID
    status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.PENDING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    input_data = models.JSONField(default=dict, blank=True)  # snapshot of inputs
    output_data = models.JSONField(default=dict, blank=True)  # snapshot of outputs
    error_msg = models.TextField(blank=True)
    records_in = models.PositiveIntegerField(default=0)
    records_out = models.PositiveIntegerField(default=0)

    # Optional: link to a hospital account
    hospital = models.ForeignKey(
        "HospitalAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="agent_runs"
    )

    class Meta:
        verbose_name = "Agent Run"
        verbose_name_plural = "Agent Runs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.agent.workflow_id} run {self.run_id} [{self.status}]"

    @property
    def duration_seconds(self):
        if self.finished_at and self.started_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Hospital Accounts
# ─────────────────────────────────────────────────────────────────────────────


class HospitalAccount(models.Model):
    """A hospital / prospect account tracked through the AI sales pipeline."""

    class AccountStatus(models.TextChoices):
        RAW = "raw", "Raw Lead"
        MQL = "mql", "Marketing Qualified"
        SAL = "sal", "Sales Accepted"
        SQL = "sql", "Sales Qualified"
        DEMO = "demo", "Demo Scheduled"
        PROPOSAL = "proposal", "Proposal Sent"
        NEGOTIATION = "negotiation", "Negotiation"
        CLOSED_WON = "closed_won", "Closed Won"
        CLOSED_LOST = "closed_lost", "Closed Lost"
        NURTURE = "nurture", "Nurturing"

    class IcpTier(models.TextChoices):
        HOT = "hot", "HOT"
        WARM = "warm", "WARM"
        COLD = "cold", "COLD"

    # Identity
    name = models.CharField(max_length=255)
    hospital_code = models.CharField(max_length=50, unique=True, blank=True)
    website = models.URLField(blank=True)
    logo_url = models.URLField(blank=True)

    # Contact
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    contact_role = models.CharField(max_length=100, blank=True)

    # Firmographic
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="India")
    postal_code = models.CharField(max_length=20, blank=True)
    bed_count = models.PositiveIntegerField(null=True, blank=True)
    speciality = models.CharField(max_length=200, blank=True)
    hospital_type = models.CharField(max_length=100, blank=True)  # Private / Govt / Trust

    # CRM / Pipeline
    status = models.CharField(max_length=30, choices=AccountStatus.choices, default=AccountStatus.RAW)
    icp_tier = models.CharField(max_length=10, choices=IcpTier.choices, default=IcpTier.COLD)
    lead_score = models.PositiveSmallIntegerField(default=0)  # 0-100
    icp_score = models.PositiveSmallIntegerField(default=0)  # 0-100
    deal_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    assigned_rep = models.CharField(max_length=200, blank=True)
    assigned_channel = models.CharField(max_length=100, blank=True)  # email / voice / video

    # Notes
    notes = models.TextField(blank=True)
    next_action = models.CharField(max_length=300, blank=True)
    next_action_date = models.DateField(null=True, blank=True)

    # CRM sync
    hubspot_id = models.CharField(max_length=100, blank=True)
    crm_synced_at = models.DateTimeField(null=True, blank=True)

    # Meta
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Hospital Account"
        verbose_name_plural = "Hospital Accounts"
        ordering = ["-lead_score", "name"]

    def __str__(self):
        return f"{self.name} [{self.icp_tier}]"

    @property
    def pipeline_stage_color(self):
        colors = {
            "raw": "#94a3b8",
            "mql": "#60a5fa",
            "sal": "#818cf8",
            "sql": "#a78bfa",
            "demo": "#f472b6",
            "proposal": "#fb923c",
            "negotiation": "#fbbf24",
            "closed_won": "#4ade80",
            "closed_lost": "#f87171",
            "nurture": "#67e8f9",
        }
        return colors.get(self.status, "#94a3b8")


class HospitalAgentActivity(models.Model):
    """Records what each agent did for a specific hospital account."""

    class ActivityType(models.TextChoices):
        LEAD_GEN = "lead_gen", "Lead Generated"
        SCORED = "scored", "Lead Scored"
        QUALIFIED = "qualified", "Qualified"
        RESEARCHED = "researched", "Researched"
        EMAIL_SENT = "email_sent", "Email Sent"
        CALL_PREP = "call_prep", "Call Prepared"
        CALL_INTEL = "call_intel", "Call Intelligence"
        OUTREACH = "outreach", "Outreach Sent"
        MEETING_SCHED = "meeting_sched", "Meeting Scheduled"
        PROPOSAL_SENT = "proposal_sent", "Proposal Sent"
        CRM_HANDOFF = "crm_handoff", "CRM Handoff"
        INSIGHT = "insight", "Insight Generated"
        CONTENT_SENT = "content_sent", "Content Sent"
        FOLLOW_UP = "follow_up", "Follow-Up Sent"
        OTHER = "other", "Other"

    hospital = models.ForeignKey(HospitalAccount, on_delete=models.CASCADE, related_name="agent_activities")
    agent = models.ForeignKey(SalesAgent, on_delete=models.CASCADE, related_name="hospital_activities")
    agent_run = models.ForeignKey(AgentRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    activity_type = models.CharField(max_length=30, choices=ActivityType.choices, default=ActivityType.OTHER)

    summary = models.TextField(blank=True)  # human-readable summary
    input_data = models.JSONField(default=dict, blank=True)
    output_data = models.JSONField(default=dict, blank=True)
    outcome = models.CharField(max_length=200, blank=True)  # e.g. "HOT", "booked"

    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Hospital Agent Activity"
        verbose_name_plural = "Hospital Agent Activities"
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.agent.workflow_id} → {self.hospital.name} [{self.activity_type}]"


# ─────────────────────────────────────────────────────────────────────────────
# Legacy conversation models (kept for API compatibility)
# ─────────────────────────────────────────────────────────────────────────────


class AgentConversation(models.Model):
    """Stores conversations handled by an AI sales agent."""

    agent = models.ForeignKey(
        SalesAgent,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    user = models.BigIntegerField(null=True, blank=True)
    session_id = models.CharField(max_length=255, unique=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Agent Conversation"
        verbose_name_plural = "Agent Conversations"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.agent.name} — {self.session_id}"


class ConversationMessage(models.Model):
    """An individual message within an agent conversation."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    conversation = models.ForeignKey(
        AgentConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Conversation Message"
        verbose_name_plural = "Conversation Messages"
        ordering = ["timestamp"]

    def __str__(self):
        return f"[{self.role}] {self.content[:50]}"
