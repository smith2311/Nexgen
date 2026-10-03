"""
Data migration: create the 'AI Sales Dashboard' Django auth Group.

Users added to this group gain access to the AI Sales Agent Dashboard
without needing is_superuser=True.
"""
from django.db import migrations


def create_ai_dashboard_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='AI Sales Dashboard')


def remove_ai_dashboard_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='AI Sales Dashboard').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ai_sales_agent_dashboard', '0003_alter_hospitalaccount_hospital_code_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_ai_dashboard_group, remove_ai_dashboard_group),
    ]
