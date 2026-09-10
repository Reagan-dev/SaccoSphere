"""Report dividend declarations whose ``financial_year`` is not canonical.

Canonical = ``YYYY/YYYY`` with two consecutive years (see
``services.validators.validate_financial_year``). Read-only: never
rewrites a value - "2025" vs "2025/2026" for the same period must be
resolved by a human. Exits non-zero when any offender is found so it can
gate a deploy pipeline before ``0017_config_field_validation`` is relied
on.
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Sacco
from services.models import DividendDeclaration
from services.validators import is_canonical_financial_year


class Command(BaseCommand):
    help = (
        'List DividendDeclaration rows whose financial_year is not the '
        'canonical YYYY/YYYY (consecutive) format. Read-only.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sacco',
            dest='sacco_id',
            default=None,
            help='Restrict the audit to a single SACCO id.',
        )

    def handle(self, *args, **options):
        sacco_qs = Sacco.objects.all().order_by('created_at')
        if options['sacco_id']:
            sacco_qs = sacco_qs.filter(id=options['sacco_id'])

        total_offenders = 0
        for sacco in sacco_qs.iterator():
            rows = (
                DividendDeclaration.objects.filter(sacco=sacco)
                .select_related('savings_type')
                .order_by('financial_year', 'created_at')
            )
            offenders = [
                row
                for row in rows
                if not is_canonical_financial_year(row.financial_year)
            ]
            if not offenders:
                continue

            total_offenders += len(offenders)
            self.stdout.write('')
            self.stdout.write(
                self.style.ERROR(
                    f'{sacco.name} ({sacco.id}): {len(offenders)} '
                    'non-canonical financial_year value(s)'
                )
            )
            for row in offenders:
                savings_type = (
                    row.savings_type.name if row.savings_type_id else '-'
                )
                self.stdout.write(
                    f'  - {row.id} | {savings_type} | '
                    f'financial_year={row.financial_year!r} | '
                    f'status={row.status} | '
                    f'created={row.created_at:%Y-%m-%d}'
                )

        if total_offenders:
            raise CommandError(
                f'{total_offenders} dividend declaration(s) have a '
                'non-canonical financial_year. Correct each to YYYY/YYYY '
                '(consecutive years) - do not guess which period a value '
                'like "FY25" meant.'
            )

        self.stdout.write(
            self.style.SUCCESS(
                'All dividend declaration financial_year values are '
                'canonical.'
            )
        )
