"""Per-SACCO toggle for the dividend four-eyes rule.

``enforce_dividend_dual_control`` defaults to ``True`` - every SACCO
starts with separation of duties on (creator != approver != disburser).
A SACCO can opt out via its settings.

Online-safe: a boolean column with a constant default is a metadata-only
add on PostgreSQL 11+ (no row rewrite). Reversible - the column is
dropped on reverse and the view helper falls back to "enforced" when a
SACCO has no settings row anyway.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0027_sacco_penalty_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='saccosettings',
            name='enforce_dividend_dual_control',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Require a dividend declaration to be approved by a '
                    'different admin than the one who created it, and '
                    'disbursed by a different admin than the one who '
                    'approved it.'
                ),
            ),
        ),
    ]
