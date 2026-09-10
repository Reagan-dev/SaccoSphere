"""Add the transient/terminal statuses the async dividend jobs need.

``DividendDeclaration.status`` gains ``CALCULATING`` and ``DISBURSING``
(set by the view while the Celery task runs) and ``FAILED`` (a calculation
task that raised - the payout writes are rolled back, but the admin needs
to see the run broke rather than a declaration stuck in ``CALCULATING``).
A disbursement that fails reverts to ``APPROVED`` and does not use
``FAILED``.

Choices-only ``AlterField``: ``status`` is a plain ``CharField`` with no
DB-level check constraint, so this only updates Django's field metadata -
no table rewrite, no lock of consequence. Online-safe, no maintenance
window, and reversible (reverse restores the four original choices; any
row left in a new status would then fail validation but the column data
is untouched).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0014_savingstype_allows_multiple_accounts'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dividenddeclaration',
            name='status',
            field=models.CharField(
                choices=[
                    ('DRAFT', 'Draft'),
                    ('CALCULATING', 'Calculating'),
                    ('CALCULATED', 'Calculated'),
                    ('APPROVED', 'Approved'),
                    ('DISBURSING', 'Disbursing'),
                    ('DISBURSED', 'Disbursed'),
                    ('FAILED', 'Failed'),
                ],
                default='DRAFT',
                help_text='Declaration status.',
                max_length=20,
            ),
        ),
    ]
