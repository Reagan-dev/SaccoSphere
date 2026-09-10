"""Index the two status columns the admin queues and daily sweeps filter on.

What this does
--------------
* ``services_loan.status`` -> single-column btree index (``db_index``).
* ``services_repaymentschedule.status`` -> single-column btree index.
* ``services_repaymentschedule`` -> composite ``(status, due_date)`` for
  the overdue sweep / reminder / NPL-arrears queries, and ``(loan,
  status)`` for per-loan schedule scans.

None of the ``AlterField`` operations change a column type, nullability
or default - they only attach an index - so PostgreSQL rewrites no rows.

Online-safe vs maintenance window
---------------------------------
This platform is still pre-production: both tables hold on the order of
1e2-1e3 rows in the live database today. ``CREATE INDEX`` on tables that
size completes in well under a second and the brief ``SHARE`` lock it
takes (blocks writes, not reads, to the one table) is not perceptible.
**Online-safe, no maintenance window.**

If this were ever applied to a populated production book (rule of thumb:
``services_repaymentschedule`` > ~1e6 rows, i.e. tens of thousands of
active loans x ~12 instalments), the four operations should instead be
run as ``django.contrib.postgres.operations.AddIndexConcurrently`` with
``atomic = False`` so the build never blocks writes. Not done here
because it is unwarranted at current volume and would break the SQLite
dev/CI database.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0011_alter_loan_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='loan',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending'),
                    ('GUARANTORS_PENDING', 'Guarantors pending'),
                    ('PENDING_APPROVAL', 'Pending approval'),
                    ('UNDER_REVIEW', 'Under review'),
                    ('APPROVED', 'Approved'),
                    ('DISBURSED', 'Disbursed'),
                    ('DISBURSEMENT_PENDING', 'Disbursement pending'),
                    ('ACTIVE', 'Active'),
                    ('COMPLETED', 'Completed'),
                    ('REJECTED', 'Rejected'),
                    ('DEFAULTED', 'Defaulted'),
                ],
                db_index=True,
                default='PENDING',
                help_text='Current loan workflow status.',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='repaymentschedule',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending'),
                    ('PAID', 'Paid'),
                    ('OVERDUE', 'Overdue'),
                    ('PARTIAL', 'Partial'),
                ],
                db_index=True,
                default='PENDING',
                help_text='Current instalment payment status.',
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name='repaymentschedule',
            index=models.Index(
                fields=['status', 'due_date'],
                name='services_re_status_adde82_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='repaymentschedule',
            index=models.Index(
                fields=['loan', 'status'],
                name='services_re_loan_id_d0a335_idx',
            ),
        ),
    ]
