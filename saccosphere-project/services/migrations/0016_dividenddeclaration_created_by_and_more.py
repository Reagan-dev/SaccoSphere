"""Record the admin who created a dividend declaration (four-eyes rule).

``DividendApproveView`` now refuses to let the creating admin approve
their own declaration (and ``DividendDisburseView`` refuses to let the
approver disburse it). ``approved_by`` already existed; this adds the
matching ``created_by``.

Online-safe: a nullable FK column is a metadata-only add on
PostgreSQL - no table rewrite, no default backfill. The
``AlterField`` on ``approved_by`` is a ``help_text``-only change (no
SQL). The new FK index (``CREATE INDEX``) is instant at this table's
size (tens of rows per SACCO); on a large table this would want
``CREATE INDEX CONCURRENTLY``, not warranted here and it would break the
SQLite dev/CI DB. No maintenance window.

Reversible: the column is dropped on reverse; existing rows keep
``created_by = NULL`` and the approval check fails open for them (it can
only assert *sameness*, and with no recorded creator it cannot).
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0015_dividenddeclaration_async_statuses'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='dividenddeclaration',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_dividend_declarations',
                to=settings.AUTH_USER_MODEL,
                help_text=(
                    'Admin who created this declaration. Segregation of '
                    'duties: a different admin must approve it (see '
                    'SaccoSettings.enforce_dividend_dual_control).'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='dividenddeclaration',
            name='approved_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='approved_dividend_declarations',
                to=settings.AUTH_USER_MODEL,
                help_text=(
                    'Admin who approved this declaration. A different '
                    'admin must disburse it.'
                ),
            ),
        ),
    ]
