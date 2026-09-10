"""Per-SACCO opt-in for the monthly savings-interest accrual job.

``savings_interest_accrual_enabled`` defaults to ``False`` - a SACCO
credits no interest (even where a ``SavingsType`` advertises a rate)
until an admin turns this on. Paying interest is an outbound liability,
so the conservative default is off.

Online-safe: a boolean column with a constant default is a metadata-only
add on PostgreSQL 11+ (no row rewrite). Reversible - the column is
dropped on reverse and the accrual task treats a missing settings row as
"disabled".
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0028_saccosettings_enforce_dividend_dual_control'),
    ]

    operations = [
        migrations.AddField(
            model_name='saccosettings',
            name='savings_interest_accrual_enabled',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Opt in to the monthly savings-interest accrual '
                    'job. When off (the default) no interest is '
                    'credited even if a SavingsType advertises an '
                    "interest_rate. Simple interest, monthly, on each "
                    "ACTIVE account's balance at run time."
                ),
            ),
        ),
    ]
