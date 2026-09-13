"""Add a DB-level CheckConstraint enforcing LedgerEntry.amount > 0.

``create_ledger_entry()`` and ``apply_ledger_entry()`` already reject a
non-positive amount in Python, but that guard only protects callers that
go through those two functions. This constraint is the backstop: no code
path - present or future - can write a zero or negative ``amount`` row,
even a direct ``LedgerEntry.objects.create(...)`` or a raw SQL insert.

CAUTION - not automatically safe to run: unlike the choices-only
``AlterField`` migrations before it, this one adds a real constraint
that Postgres validates against every existing row. If any row already
violates it (amount <= 0), this migration fails outright. Audit
existing data with
``LedgerEntry.objects.filter(amount__lte=0).exists()`` before applying
this in an environment with real data.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0005_ledgerentry_savings_interest_category'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='ledgerentry',
            constraint=models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='ledger_entry_amount_positive',
            ),
        ),
    ]
