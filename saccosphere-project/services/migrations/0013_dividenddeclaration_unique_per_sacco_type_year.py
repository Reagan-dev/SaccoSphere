"""Enforce one dividend declaration per (sacco, savings_type, financial_year).

Without this a SACCO could hold two declarations for the same period and
disburse both, paying members twice. ``sacco`` is a direct FK on
``DividendDeclaration`` so the tenant is already part of the key.

Pre-check
---------
``_abort_if_duplicates`` runs first and refuses to proceed if any
duplicate group already exists, pointing the operator at
``manage.py audit_duplicate_dividend_declarations``. Real financial
declarations are never merged or deleted here - a human resolves them.

Online-safe assessment
----------------------
This platform is pre-production; ``services_dividenddeclaration`` holds on
the order of one row per SACCO per savings type per year (tens of rows).
``CREATE UNIQUE INDEX`` at that size completes in well under a second and
the brief ``SHARE`` lock (blocks writes to this one table, not reads) is
not perceptible. Online-safe, no maintenance window. On a large populated
table this should instead be ``AddConstraintNotValid`` + a concurrent
unique index, or ``AddIndexConcurrently`` with ``atomic = False``; not
warranted at current volume and it would break the SQLite dev/CI DB.

Reversible: the constraint is dropped on reverse; the pre-check is a
no-op on reverse.
"""

from django.db import migrations, models


DUPLICATE_QUERY_HINT = (
    'Run "python manage.py audit_duplicate_dividend_declarations" for the '
    'full list, resolve each group, then re-run this migration.'
)


def _abort_if_duplicates(apps, schema_editor):
    from django.db.models import Count

    declaration_model = apps.get_model('services', 'DividendDeclaration')
    duplicates = (
        declaration_model.objects.values(
            'sacco_id', 'savings_type_id', 'financial_year',
        )
        .annotate(n=Count('id'))
        .filter(n__gt=1)
        .order_by('sacco_id', 'financial_year')
    )
    groups = list(duplicates)
    if not groups:
        return

    summary = ', '.join(
        f'sacco={g["sacco_id"]} savings_type={g["savings_type_id"]} '
        f'fy={g["financial_year"]} ({g["n"]} declarations)'
        for g in groups[:10]
    )
    more = '' if len(groups) <= 10 else f' ... and {len(groups) - 10} more'
    raise RuntimeError(
        f'{len(groups)} duplicate dividend declaration group(s) exist and '
        f'must be resolved before the unique constraint can be added: '
        f'{summary}{more}. {DUPLICATE_QUERY_HINT}'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0012_index_loan_repaymentschedule_status'),
    ]

    operations = [
        migrations.RunPython(
            _abort_if_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='dividenddeclaration',
            constraint=models.UniqueConstraint(
                fields=['sacco', 'savings_type', 'financial_year'],
                name='unique_dividend_declaration_per_sacco_type_year',
            ),
        ),
    ]
