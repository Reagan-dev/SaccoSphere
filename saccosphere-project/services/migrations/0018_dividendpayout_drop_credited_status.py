"""Drop the unused ``CREDITED`` value from ``DividendPayout.status``.

Product decision: ``CREDITED`` was never distinct from ``PAID``. A
dividend on this platform is reinvested directly into the member's
savings account, so the single ``DIVIDEND_PAYOUT`` ledger credit *is*
both "credited to the account" and "paid". ``CREDITED`` was also never
set by any code path (``calculate_dividends_for_declaration`` writes
``PENDING``, ``disburse_dividends_for_declaration`` writes ``PAID``), so
the state machine is really ``PENDING -> PAID``.

Data step (``_flip_stray_credited``): defensively re-labels any row that
somehow holds ``CREDITED`` (only reachable via a manual admin edit) to
``PAID`` - a ``CREDITED`` row's dividend has effectively been handled and
disburse would otherwise skip it forever (it only processes
``PENDING``). Expected to touch zero rows; it prints the count if not.

Online-safe: ``AlterField`` here only changes the Python-level ``choices``
(no DB constraint on this ``CharField``) - no SQL, no lock, no rewrite.
Reversible: the reverse ``AlterField`` restores the three choices; the
data step's reverse is a no-op (there is nothing to turn back into
``CREDITED``, and nothing depends on the distinction).
"""

import sys

from django.db import migrations, models


def _flip_stray_credited(apps, schema_editor):
    payout_model = apps.get_model('services', 'DividendPayout')
    updated = payout_model.objects.filter(status='CREDITED').update(
        status='PAID',
    )
    if updated:
        sys.stdout.write(
            f'\n  0018: re-labelled {updated} DividendPayout row(s) from '
            'CREDITED to PAID.\n'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0017_config_field_validation'),
    ]

    operations = [
        migrations.RunPython(
            _flip_stray_credited,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='dividendpayout',
            name='status',
            field=models.CharField(
                choices=[('PENDING', 'Pending'), ('PAID', 'Paid')],
                default='PENDING',
                help_text='Payout status.',
                max_length=20,
            ),
        ),
    ]
