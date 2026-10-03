import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai_sales_agent_dashboard", "0001_initial"),
    ]

    operations = [
        # ── Extend SalesAgent with all new fields ──
        # All AddField ops are wrapped in SeparateDatabaseAndState so the migration
        # is idempotent when columns were already added outside Django's migration history.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="salesagent",
                    name="workflow_id",
                    field=models.CharField(max_length=20, default='', unique=False),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="workflow_number",
                    field=models.FloatField(default=0),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="short_name",
                    field=models.CharField(max_length=100, blank=True, default=''),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="phase",
                    field=models.CharField(
                        max_length=30,
                        choices=[
                            ('orchestrator', 'Master Orchestrator'),
                            ('discovery', 'Phase 1 — Discovery'),
                            ('intelligence', 'Phase 2 — Research & Intelligence'),
                            ('outreach', 'Phase 3 — Outreach'),
                            ('comms', 'Phase 4 — Comms & Agents'),
                            ('content', 'Phase 5 — Content & Closing'),
                            ('infrastructure', 'Infrastructure Layer'),
                        ],
                        default='discovery',
                    ),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="status",
                    field=models.CharField(
                        max_length=20,
                        choices=[('active', 'Active'), ('inactive', 'Inactive'), ('testing', 'Testing')],
                        default='active',
                    ),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="trigger_types",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="schedule_info",
                    field=models.CharField(max_length=200, blank=True, default=''),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="node_count",
                    field=models.PositiveIntegerField(default=0),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="reports_to",
                    field=models.ForeignKey(
                        null=True, blank=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='sub_agents',
                        to='ai_sales_agent_dashboard.salesagent',
                    ),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="input_tables",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="output_tables",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="input_schema",
                    field=models.JSONField(default=dict, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="output_schema",
                    field=models.JSONField(default=dict, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="responsibilities",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="key_outputs",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="tags",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="complexity_score",
                    field=models.PositiveSmallIntegerField(default=1),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="icon",
                    field=models.CharField(max_length=50, default='🤖'),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="raw_json",
                    field=models.JSONField(default=dict, blank=True),
                ),
                migrations.AddField(
                    model_name="salesagent",
                    name="direct_reports",
                    field=models.ManyToManyField(
                        blank=True, related_name='manager_of',
                        symmetrical=False,
                        to='ai_sales_agent_dashboard.salesagent',
                    ),
                ),
                migrations.AlterModelOptions(
                    name="salesagent",
                    options={
                        "verbose_name": "Sales Agent",
                        "verbose_name_plural": "Sales Agents",
                        "ordering": ["workflow_number"],
                    },
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        DO $$
                        DECLARE
                            tbl TEXT := 'ai_sales_agent_dashboard_salesagent';
                        BEGIN
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='workflow_id') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "workflow_id" varchar(20) DEFAULT '' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='workflow_number') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "workflow_number" double precision DEFAULT 0.0 NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='short_name') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "short_name" varchar(100) DEFAULT '' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='phase') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "phase" varchar(30) DEFAULT 'discovery' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='status') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "status" varchar(20) DEFAULT 'active' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='trigger_types') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "trigger_types" jsonb DEFAULT '[]' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='schedule_info') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "schedule_info" varchar(200) DEFAULT '' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='node_count') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "node_count" integer DEFAULT 0 NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='reports_to_id') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "reports_to_id" integer NULL REFERENCES "ai_sales_agent_dashboard_salesagent"("id") DEFERRABLE INITIALLY DEFERRED;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='input_tables') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "input_tables" jsonb DEFAULT '[]' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='output_tables') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "output_tables" jsonb DEFAULT '[]' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='input_schema') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "input_schema" jsonb DEFAULT '{}' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='output_schema') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "output_schema" jsonb DEFAULT '{}' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='responsibilities') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "responsibilities" jsonb DEFAULT '[]' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='key_outputs') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "key_outputs" jsonb DEFAULT '[]' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='tags') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "tags" jsonb DEFAULT '[]' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='complexity_score') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "complexity_score" smallint DEFAULT 1 NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='icon') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "icon" varchar(50) DEFAULT '🤖' NOT NULL;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=tbl AND column_name='raw_json') THEN
                                ALTER TABLE "ai_sales_agent_dashboard_salesagent" ADD COLUMN "raw_json" jsonb DEFAULT '{}' NOT NULL;
                            END IF;
                            -- ManyToMany junction table for direct_reports
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.tables
                                WHERE table_name='ai_sales_agent_dashboard_salesagent_direct_reports'
                            ) THEN
                                CREATE TABLE "ai_sales_agent_dashboard_salesagent_direct_reports" (
                                    "id" serial NOT NULL PRIMARY KEY,
                                    "from_salesagent_id" integer NOT NULL REFERENCES "ai_sales_agent_dashboard_salesagent"("id") DEFERRABLE INITIALLY DEFERRED,
                                    "to_salesagent_id" integer NOT NULL REFERENCES "ai_sales_agent_dashboard_salesagent"("id") DEFERRABLE INITIALLY DEFERRED,
                                    UNIQUE ("from_salesagent_id", "to_salesagent_id")
                                );
                            END IF;
                        END $$;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),

        # ── AgentRun ──
        # 0001_initial.py already creates this table; use SeparateDatabaseAndState
        # so Django updates its model state without re-running the DDL.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="AgentRun",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("run_id", models.CharField(max_length=100, blank=True)),
                        ("status", models.CharField(
                            max_length=20,
                            choices=[
                                ('pending', 'Pending'), ('running', 'Running'), ('success', 'Success'),
                                ('failed', 'Failed'), ('partial', 'Partial'),
                            ],
                            default='pending',
                        )),
                        ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                        ("finished_at", models.DateTimeField(null=True, blank=True)),
                        ("input_data", models.JSONField(default=dict, blank=True)),
                        ("output_data", models.JSONField(default=dict, blank=True)),
                        ("error_msg", models.TextField(blank=True)),
                        ("records_in", models.PositiveIntegerField(default=0)),
                        ("records_out", models.PositiveIntegerField(default=0)),
                        ("agent", models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='runs',
                            to='ai_sales_agent_dashboard.salesagent',
                        )),
                    ],
                    options={
                        "verbose_name": "Agent Run",
                        "verbose_name_plural": "Agent Runs",
                        "ordering": ["-started_at"],
                    },
                ),
            ],
            database_operations=[],
        ),

        # ── HospitalAccount ──
        # 0001_initial.py already creates this table; skip DDL, update state only.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="HospitalAccount",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("name", models.CharField(max_length=255)),
                        ("hospital_code", models.CharField(max_length=50, unique=True, blank=True, default='')),
                        ("website", models.URLField(blank=True)),
                        ("logo_url", models.URLField(blank=True)),
                        ("contact_name", models.CharField(max_length=200, blank=True)),
                        ("contact_email", models.EmailField(blank=True)),
                        ("contact_phone", models.CharField(max_length=50, blank=True)),
                        ("contact_role", models.CharField(max_length=100, blank=True)),
                        ("city", models.CharField(max_length=100, blank=True)),
                        ("state", models.CharField(max_length=100, blank=True)),
                        ("country", models.CharField(max_length=100, default='India')),
                        ("postal_code", models.CharField(max_length=20, blank=True)),
                        ("bed_count", models.PositiveIntegerField(null=True, blank=True)),
                        ("speciality", models.CharField(max_length=200, blank=True)),
                        ("hospital_type", models.CharField(max_length=100, blank=True)),
                        ("status", models.CharField(
                            max_length=30,
                            choices=[
                                ('raw', 'Raw Lead'), ('mql', 'Marketing Qualified'),
                                ('sal', 'Sales Accepted'), ('sql', 'Sales Qualified'),
                                ('demo', 'Demo Scheduled'), ('proposal', 'Proposal Sent'),
                                ('negotiation', 'Negotiation'), ('closed_won', 'Closed Won'),
                                ('closed_lost', 'Closed Lost'), ('nurture', 'Nurturing'),
                            ],
                            default='raw',
                        )),
                        ("icp_tier", models.CharField(
                            max_length=10,
                            choices=[('hot', 'HOT'), ('warm', 'WARM'), ('cold', 'COLD')],
                            default='cold',
                        )),
                        ("lead_score", models.PositiveSmallIntegerField(default=0)),
                        ("icp_score", models.PositiveSmallIntegerField(default=0)),
                        ("deal_value", models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)),
                        ("assigned_rep", models.CharField(max_length=200, blank=True)),
                        ("assigned_channel", models.CharField(max_length=100, blank=True)),
                        ("notes", models.TextField(blank=True)),
                        ("next_action", models.CharField(max_length=300, blank=True)),
                        ("next_action_date", models.DateField(null=True, blank=True)),
                        ("hubspot_id", models.CharField(max_length=100, blank=True)),
                        ("crm_synced_at", models.DateTimeField(null=True, blank=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "verbose_name": "Hospital Account",
                        "verbose_name_plural": "Hospital Accounts",
                        "ordering": ["-lead_score", "name"],
                    },
                ),
            ],
            database_operations=[],
        ),

        # ── Add hospital FK to AgentRun ──
        # Column already exists in the table created by 0001_initial.py.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="agentrun",
                    name="hospital",
                    field=models.ForeignKey(
                        null=True, blank=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='agent_runs',
                        to='ai_sales_agent_dashboard.hospitalaccount',
                    ),
                ),
            ],
            database_operations=[],
        ),

        # ── HospitalAgentActivity ──
        # 0001_initial.py already creates this table; skip DDL, update state only.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="HospitalAgentActivity",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("activity_type", models.CharField(
                            max_length=30,
                            choices=[
                                ('lead_gen', 'Lead Generated'), ('scored', 'Lead Scored'),
                                ('qualified', 'Qualified'), ('researched', 'Researched'),
                                ('email_sent', 'Email Sent'), ('call_prep', 'Call Prepared'),
                                ('call_intel', 'Call Intelligence'), ('outreach', 'Outreach Sent'),
                                ('meeting_sched', 'Meeting Scheduled'), ('proposal_sent', 'Proposal Sent'),
                                ('crm_handoff', 'CRM Handoff'), ('insight', 'Insight Generated'),
                                ('content_sent', 'Content Sent'), ('follow_up', 'Follow-Up Sent'),
                                ('other', 'Other'),
                            ],
                            default='other',
                        )),
                        ("summary", models.TextField(blank=True)),
                        ("input_data", models.JSONField(default=dict, blank=True)),
                        ("output_data", models.JSONField(default=dict, blank=True)),
                        ("outcome", models.CharField(max_length=200, blank=True)),
                        ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("hospital", models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='agent_activities',
                            to='ai_sales_agent_dashboard.hospitalaccount',
                        )),
                        ("agent", models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='hospital_activities',
                            to='ai_sales_agent_dashboard.salesagent',
                        )),
                        ("agent_run", models.ForeignKey(
                            null=True, blank=True,
                            on_delete=django.db.models.deletion.SET_NULL,
                            related_name='activities',
                            to='ai_sales_agent_dashboard.agentrun',
                        )),
                    ],
                    options={
                        "verbose_name": "Hospital Agent Activity",
                        "verbose_name_plural": "Hospital Agent Activities",
                        "ordering": ["-occurred_at"],
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
