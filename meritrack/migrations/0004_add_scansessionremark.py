from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('meritrack', '0003_scanlog_quantity'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ScanSessionRemark is already included in 0001_initial on fresh installs.
        # Use SeparateDatabaseAndState so Django records this migration as applied
        # without re-running the DDL that 0001_initial already executed.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='ScanSessionRemark',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('remark', models.TextField()),
                        ('role', models.CharField(
                            choices=[('salesperson', 'Salesperson'), ('warehouse', 'Warehouse')],
                            max_length=20,
                        )),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('created_by', models.ForeignKey(
                            blank=True,
                            null=True,
                            on_delete=django.db.models.deletion.SET_NULL,
                            related_name='mt_session_remarks',
                            to=settings.AUTH_USER_MODEL,
                        )),
                        ('session', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='session_remarks',
                            to='meritrack.scanningsession',
                        )),
                    ],
                    options={
                        'verbose_name': 'Scan Session Remark',
                        'verbose_name_plural': 'Scan Session Remarks',
                        'ordering': ['created_at'],
                    },
                ),
                migrations.AddIndex(
                    model_name='scansessionremark',
                    index=models.Index(fields=['session'], name='meritrack_s_session_33f639_idx'),
                ),
                migrations.AddIndex(
                    model_name='scansessionremark',
                    index=models.Index(fields=['role'], name='meritrack_s_role_3ed5e6_idx'),
                ),
            ],
            database_operations=[],
        ),
    ]
