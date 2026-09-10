"""Sanity bounds on rate fields + a canonical financial-year format.

* ``DividendDeclaration.declared_rate`` and ``SavingsType.interest_rate``
  gain 0-100% validators (the ``100`` ceiling is a PLACEHOLDER pending
  policy sign-off - see ``services/validators.py``).
* ``DividendDeclaration.financial_year`` gains
  ``validate_financial_year`` - canonical ``YYYY/YYYY`` with consecutive
  years. This is what the Prompt-4 uniqueness constraint
  (``0013_dividenddeclaration_unique_per_sacco_type_year``) silently
  assumed but never enforced.

Online-safe: all three operations are ``AlterField`` that only add
Python-level ``validators`` / change ``help_text`` - no SQL, no lock, no
rewrite. Reversible (reverse restores the fields without the
validators).

``_report_nonconforming_financial_years`` runs once on apply and just
*prints* any existing ``financial_year`` that is not canonical - it does
NOT block the migration and does NOT modify data, so a deploy never
fails here, but an operator is told which rows to fix (with
``manage.py audit_financial_year_format``) before those rows next hit a
validated save. Reverse is a no-op.
"""

import re
import sys
from decimal import Decimal

import django.core.validators
import services.validators
from django.db import migrations, models


_FINANCIAL_YEAR_RE = re.compile(r'^(\d{4})/(\d{4})$')


def _report_nonconforming_financial_years(apps, schema_editor):
    declaration_model = apps.get_model('services', 'DividendDeclaration')

    offenders = []
    for sacco_id, financial_year in (
        declaration_model.objects.values_list('sacco_id', 'financial_year')
        .order_by('sacco_id', 'financial_year')
    ):
        match = _FINANCIAL_YEAR_RE.match(financial_year or '')
        canonical = bool(match) and (
            int(match.group(2)) == int(match.group(1)) + 1
        )
        if not canonical:
            offenders.append((sacco_id, financial_year))

    if not offenders:
        return

    sys.stdout.write(
        '\n  WARNING: {} dividend declaration(s) have a non-canonical '
        'financial_year.\n'.format(len(offenders))
    )
    for sacco_id, financial_year in offenders[:50]:
        sys.stdout.write(
            f'    sacco={sacco_id} financial_year={financial_year!r}\n'
        )
    if len(offenders) > 50:
        sys.stdout.write(
            f'    ... and {len(offenders) - 50} more.\n'
        )
    sys.stdout.write(
        '  These are not blocking this migration, but they will fail '
        'their next validated save. Run '
        '"manage.py audit_financial_year_format" and correct them.\n\n'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0016_dividenddeclaration_created_by_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dividenddeclaration',
            name='declared_rate',
            field=models.DecimalField(
                decimal_places=2,
                max_digits=5,
                help_text=(
                    'Annual dividend rate percentage (0-100, ceiling '
                    'pending policy sign-off).'
                ),
                validators=[
                    django.core.validators.MinValueValidator(
                        Decimal('0.00'),
                        message='Annual rate cannot be negative.',
                    ),
                    django.core.validators.MaxValueValidator(
                        Decimal('100.00'),
                        message=(
                            'Annual rate cannot exceed %(limit_value)s '
                            'percent (placeholder ceiling, pending '
                            'policy sign-off).'
                        ),
                    ),
                ],
            ),
        ),
        migrations.AlterField(
            model_name='dividenddeclaration',
            name='financial_year',
            field=models.CharField(
                max_length=20,
                help_text=(
                    'Canonical financial year: YYYY/YYYY with two '
                    'consecutive years, e.g. 2025/2026.'
                ),
                validators=[services.validators.validate_financial_year],
            ),
        ),
        migrations.AlterField(
            model_name='savingstype',
            name='interest_rate',
            field=models.DecimalField(
                blank=True,
                null=True,
                decimal_places=2,
                max_digits=5,
                help_text=(
                    'Optional annual interest rate percentage (0-100, '
                    'ceiling pending policy sign-off).'
                ),
                validators=[
                    django.core.validators.MinValueValidator(
                        Decimal('0.00'),
                        message='Annual rate cannot be negative.',
                    ),
                    django.core.validators.MaxValueValidator(
                        Decimal('100.00'),
                        message=(
                            'Annual rate cannot exceed %(limit_value)s '
                            'percent (placeholder ceiling, pending '
                            'policy sign-off).'
                        ),
                    ),
                ],
            ),
        ),
        migrations.RunPython(
            _report_nonconforming_financial_years,
            migrations.RunPython.noop,
        ),
    ]
