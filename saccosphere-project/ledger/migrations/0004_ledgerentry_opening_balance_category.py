"""Add the OPENING_BALANCE LedgerEntry category.

Choices-only ``AlterField`` - no column type change, no data touched.
Online-safe, no maintenance window. Reversible (reverting just drops the
choice from validation; any existing OPENING_BALANCE rows would then fail
model validation but remain readable - run the reverse only if no such
rows have been written).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0003_alter_ledgerentry_category'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ledgerentry',
            name='category',
            field=models.CharField(
                choices=[
                    ('SAVING_DEPOSIT', 'Saving deposit'),
                    ('SAVING_WITHDRAWAL', 'Saving withdrawal'),
                    ('LOAN_DISBURSEMENT', 'Loan disbursement'),
                    ('LOAN_REPAYMENT', 'Loan repayment'),
                    ('FEE', 'Fee'),
                    ('PENALTY', 'Penalty'),
                    ('DIVIDEND', 'Dividend'),
                    ('DIVIDEND_PAYOUT', 'Dividend payout'),
                    ('ADJUSTMENT', 'Adjustment'),
                    ('OPENING_BALANCE', 'Opening balance'),
                ],
                max_length=30,
            ),
        ),
    ]
