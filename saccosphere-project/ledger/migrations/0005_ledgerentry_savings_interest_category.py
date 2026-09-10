"""Add the ``SAVINGS_INTEREST`` category to ``LedgerEntry``.

Written only by ``services.engines.savings_interest`` (the
``accrue_savings_interest`` monthly beat task). It is a savings-balance
category (added to ``ledger.utils.SAVINGS_LEDGER_CATEGORIES``), so an
interest credit is included in ``savings_ledger_balance`` and stays in
step with the ``Saving.amount`` bump that ``apply_ledger_entry`` makes -
``reconcile_savings_ledger`` sees no drift.

Choices-only ``AlterField``: this ``CharField`` has no DB check
constraint, so it is a Python-metadata change only - no SQL, no lock, no
rewrite. Online-safe, no maintenance window. Reversible (reverse drops
the choice; existing data is untouched).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0004_ledgerentry_opening_balance_category'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ledgerentry',
            name='category',
            field=models.CharField(
                max_length=30,
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
                    ('SAVINGS_INTEREST', 'Savings interest'),
                ],
            ),
        ),
    ]
