# Adds the inline ICP scoring fields that the n8n pipeline folded into the
# Results table after the standalone ICP_Account_Scores table was retired.
# ScrapedResult is managed=False, so these AddField operations only update
# Django's migration state — no ALTER TABLE is emitted (the columns already
# exist in the n8n-managed MySQL schema). They keep the migration graph in
# sync with meril_models.ScrapedResult.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_sales_agent_dashboard", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="scrapedresult",
            name="icp_score",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="scrapedresult",
            name="icp_tier",
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
        migrations.AddField(
            model_name="scrapedresult",
            name="icp_fit_reason",
            field=models.TextField(blank=True, null=True),
        ),
    ]
