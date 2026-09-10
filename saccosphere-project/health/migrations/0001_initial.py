"""JobHeartbeat: last-run marker for monitored scheduled jobs.

New table only. Online-safe, no maintenance window.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='JobHeartbeat',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('job_name', models.CharField(max_length=100, unique=True)),
                ('last_run_at', models.DateTimeField()),
                (
                    'last_status',
                    models.CharField(
                        choices=[('OK', 'OK'), ('ERROR', 'Error')],
                        default='OK',
                        max_length=20,
                    ),
                ),
                ('detail', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['job_name'],
            },
        ),
    ]
