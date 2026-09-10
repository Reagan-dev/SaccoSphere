"""Add the NPL (non-performing loans) ComplianceFlag type.

Choices-only ``AlterField`` - no column change, no data touched.
Online-safe, no maintenance window. New value is written by
``saccomanagement.compliance_detectors.SevereArrearsDetector``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('saccomanagement', '0009_smscampaign_partial_status_and_updated_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='complianceflag',
            name='flag_type',
            field=models.CharField(
                choices=[
                    ('API_ISSUE', 'API Issue'),
                    ('PAYMENT_FAILURE', 'Payment Failure'),
                    ('DATA_DISCREPANCY', 'Data Discrepancy'),
                    ('REGULATORY', 'Regulatory'),
                    ('SECURITY', 'Security'),
                    ('PERFORMANCE', 'Performance'),
                    ('NPL', 'Non-Performing Loans'),
                ],
                max_length=30,
            ),
        ),
    ]
