"""Per-SACCO late-repayment penalty config.

Additive: three new fields on the SaccoSettings config table, all with
defaults, no data touched. Online-safe, no maintenance window.
"""

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0026_encrypt_pii_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='saccosettings',
            name='penalty_type',
            field=models.CharField(
                choices=[
                    ('NONE', 'No penalty'),
                    ('FLAT', 'Flat fee per overdue instalment'),
                    ('PERCENT_ONCE', 'Percent of instalment, one-time'),
                    (
                        'PERCENT_PER_DAY',
                        'Percent of instalment, per day overdue',
                    ),
                ],
                default='NONE',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='saccosettings',
            name='penalty_rate',
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal('0.0000'),
                help_text=(
                    'Flat KES amount when penalty_type=FLAT; a fraction '
                    'of the instalment (0.05 = 5%) when penalty_type is a '
                    'PERCENT rule.'
                ),
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name='saccosettings',
            name='penalty_grace_days',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text=(
                    'Days after the due date before a penalty is charged.'
                ),
            ),
        ),
    ]
