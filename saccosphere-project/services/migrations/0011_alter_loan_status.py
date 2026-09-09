"""Drop the unreachable BOARD_REVIEW choice from Loan.status.

Choices-only change: ``BOARD_REVIEW`` was never written by any code path
(Phase 7 removes it from the enum and every gate). No column type or
constraint change on PostgreSQL - Django stores choices in model state
only - so there is no table rewrite and no lock beyond the catalog touch.
Online-safe, no maintenance window. No data migration is needed: a
production row count query for ``services_loan`` where
``status = 'BOARD_REVIEW'`` returns 0 because nothing could ever set it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0010_encrypt_pii_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='loan',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending'),
                    ('GUARANTORS_PENDING', 'Guarantors pending'),
                    ('PENDING_APPROVAL', 'Pending approval'),
                    ('UNDER_REVIEW', 'Under review'),
                    ('APPROVED', 'Approved'),
                    ('DISBURSED', 'Disbursed'),
                    ('DISBURSEMENT_PENDING', 'Disbursement pending'),
                    ('ACTIVE', 'Active'),
                    ('COMPLETED', 'Completed'),
                    ('REJECTED', 'Rejected'),
                    ('DEFAULTED', 'Defaulted'),
                ],
                default='PENDING',
                help_text='Current loan workflow status.',
                max_length=30,
            ),
        ),
    ]
