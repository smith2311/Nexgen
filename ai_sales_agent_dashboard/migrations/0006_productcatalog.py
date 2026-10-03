# Generated migration for ProductCatalog model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai_sales_agent_dashboard', '0005_aienhancedbrief_businesslead_callinsight_crmcontact_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductCatalog',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('icon', models.CharField(default='📦', max_length=10)),
                ('description', models.TextField()),
                ('tags', models.JSONField(default=list, help_text='JSON array of product tags')),
                ('keywords', models.JSONField(default=list, help_text='JSON array of keywords for matching')),
                ('is_active', models.BooleanField(default=True)),
                ('display_order', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Product Catalog',
                'verbose_name_plural': 'Product Catalogs',
                'db_table': 'Product_Catalog',
                'managed': True,
                'ordering': ['display_order', 'name'],
            },
        ),
    ]
