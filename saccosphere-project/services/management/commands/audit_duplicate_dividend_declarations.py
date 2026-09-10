"""Report SACCOs that hold more than one dividend declaration for the
same savings type and financial year.

Read-only. This exists so a human reviews and resolves duplicates
*before* migration
``services/0013_dividenddeclaration_unique_per_sacco_type_year`` adds the
unique constraint - real financial declarations are never merged or
deleted automatically. Exits 1 when duplicates are found, 0 when clean,
so it can gate a deploy pipeline.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from services.models import DividendDeclaration


class Command(BaseCommand):
    help = (
        'List duplicate DividendDeclaration groups '
        '(same sacco + savings_type + financial_year). Read-only.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sacco',
            dest='sacco_id',
            default=None,
            help='Restrict the audit to a single SACCO id.',
        )

    def handle(self, *args, **options):
        queryset = DividendDeclaration.objects.all()

        sacco_id = options['sacco_id']
        if sacco_id:
            queryset = queryset.filter(sacco_id=sacco_id)

        duplicate_keys = (
            queryset.values('sacco_id', 'savings_type_id', 'financial_year')
            .annotate(n=Count('id'))
            .filter(n__gt=1)
            .order_by('sacco_id', 'financial_year')
        )

        groups = list(duplicate_keys)
        if not groups:
            self.stdout.write(
                self.style.SUCCESS(
                    'No duplicate dividend declarations found.'
                )
            )
            return

        self.stdout.write(
            self.style.ERROR(
                f'{len(groups)} duplicate dividend declaration group(s) '
                'found - resolve these before applying the unique '
                'constraint:'
            )
        )

        for group in groups:
            rows = (
                DividendDeclaration.objects.filter(
                    sacco_id=group['sacco_id'],
                    savings_type_id=group['savings_type_id'],
                    financial_year=group['financial_year'],
                )
                .select_related('sacco', 'savings_type')
                .order_by('created_at')
            )
            first = rows[0]
            self.stdout.write('')
            self.stdout.write(
                f'  SACCO {first.sacco.name} ({group["sacco_id"]}) | '
                f'{first.savings_type.name} | '
                f'FY {group["financial_year"]} | {group["n"]} declarations'
            )
            for row in rows:
                self.stdout.write(
                    f'    - {row.id} status={row.status} '
                    f'created={row.created_at:%Y-%m-%d %H:%M} '
                    f'rate={row.declared_rate} '
                    f'total={row.total_dividend_amount} '
                    f'payouts={row.payouts.count()}'
                )

        raise CommandError(
            f'{len(groups)} duplicate dividend declaration group(s) found. '
            'Resolve each (keep exactly one declaration per SACCO + savings '
            'type + financial year) before applying the unique constraint.'
        )
