"""Add ``SaccoSettings.dividend_calculation_method``.

Selects the dividend balance-basis strategy per SACCO. Only
``AVERAGE_MONTH_END`` is implemented today; the column defaults to it so
every existing SACCO keeps exactly the current behaviour.

Online-safe assessment
----------------------
Single ``AddField`` of a ``varchar(20) NOT NULL DEFAULT
'AVERAGE_MONTH_END'``. On PostgreSQL 11+ a column with a constant default
is a metadata-only change - no table rewrite, only a brief ``ACCESS
EXCLUSIVE`` lock to update the catalog. ``saccosettings`` holds one row
per SACCO (tens to hundreds). Online-safe, no maintenance window.
Reversible: reverse drops the column (the enum values are not persisted
anywhere else yet).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0029_saccosettings_savings_interest_accrual_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='saccosettings',
            name='dividend_calculation_method',
            field=models.CharField(choices=[('AVERAGE_MONTH_END', 'Average of month-end balances'), ('DAY_WEIGHTED', 'Day-weighted average balance (not yet supported)'), ('MINIMUM_BALANCE', 'Minimum balance over the period (not yet supported)')], default='AVERAGE_MONTH_END', help_text='Which balance basis the dividend engine uses for this SACCO. AVERAGE_MONTH_END is the only implemented method; DAY_WEIGHTED and MINIMUM_BALANCE are placeholders and a dividend run is refused with a 400 while one is selected.', max_length=20),
        ),
    ]
