"""Composite ``(declaration, status)`` index on ``DividendPayout``.

Query shapes it serves:

* Disburse - ``declaration.payouts.filter(status=PENDING)`` in
  ``services.engines.dividend_disbursement`` -> ``WHERE declaration_id =
  X AND status = 'PENDING'``. Fully covered.
* List by declaration - ``DividendPayoutListView`` with
  ``?declaration=<id>`` -> ``WHERE declaration_id = X``. Covered by the
  leading column (the ``unique_together (declaration, saving)`` index
  also covers this; the composite is added per the task's explicit
  request and helps the disburse filter that ``unique_together`` does
  not).

Online-safe assessment
----------------------
Pre-production; ``services_dividendpayout`` holds on the order of one row
per member per disbursed declaration (hundreds, not millions).
``CREATE INDEX`` at that size completes well under a second and the
brief ``SHARE`` lock (blocks writes to this one table, not reads) is not
perceptible. Online-safe, no maintenance window. On a large populated
table this should instead be ``AddIndexConcurrently`` with
``atomic = False``; not warranted at current volume and it would break
the SQLite dev/CI DB. Reversible: the index is dropped on reverse.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('saccomembership', '0002_membershipdocument'),
        ('services', '0018_dividendpayout_drop_credited_status'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='dividendpayout',
            index=models.Index(
                fields=['declaration', 'status'],
                name='divpayout_decl_status_idx',
            ),
        ),
    ]
