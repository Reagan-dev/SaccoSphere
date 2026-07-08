"""Django management command to identify and flag uncollected platform fees.

This command identifies PlatformRevenue records that were created before the
fee collection fix (where fees were recorded but never actually collected
from members via STK push gross-up).
"""

from decimal import Decimal
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Sum, Q
from django.utils import timezone

from billing.models import PlatformRevenue


class Command(BaseCommand):
    help = (
        'Identify and optionally flag uncollected platform fee revenue '
        'records (fees recorded before gross-up fix).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--before',
            type=str,
            help=(
                'Cutoff datetime to identify historical records '
                '(format: YYYY-MM-DD HH:MM:SS). Defaults to now.'
            ),
        )
        parser.add_argument(
            '--mark-uncollected',
            action='store_true',
            help='Flag identified records as is_collected=False',
        )
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Actually write changes (default is dry-run)',
        )

    def handle(self, *args, **options):
        cutoff = self._parse_cutoff(options.get('before'))
        mark_uncollected = options.get('mark_uncollected')
        execute = options.get('execute', False)

        self.stdout.write(
            f'\n{"=" * 70}\n'
            f'Uncollected Fee Reconciliation\n'
            f'{"=" * 70}\n'
            f'Cutoff datetime: {cutoff}\n'
            f'Mark as uncollected: {mark_uncollected}\n'
            f'Execute changes: {execute}\n'
            f'{"=" * 70}\n'
        )

        # Find transaction fee revenue records before cutoff
        queryset = PlatformRevenue.objects.filter(
            revenue_type=PlatformRevenue.RevenueType.TRANSACTION_FEE,
            recorded_at__lte=cutoff,
        ).select_related('sacco', 'transaction')

        total_count = queryset.count()
        self.stdout.write(f'Total transaction fee records before cutoff: {total_count}\n')

        if total_count == 0:
            self.stdout.write(self.style.WARNING('No records found. Exiting.'))
            return

        # Identify uncollected records (pre-fix transactions)
        uncollected_records = []
        sacco_breakdown = {}

        for record in queryset:
            if self._is_uncollected_fee(record):
                uncollected_records.append(record)
                sacco_name = record.sacco.name if record.sacco else 'Unknown'
                sacco_breakdown[sacco_name] = sacco_breakdown.get(
                    sacacco_name,
                    {'count': 0, 'amount': Decimal('0.00')},
                )
                sacco_breakdown[sacco_name]['count'] += 1
                sacco_breakdown[sacco_name]['amount'] += record.amount

        uncollected_count = len(uncollected_records)
        uncollected_total = sum(r.amount for r in uncollected_records)

        self.stdout.write(
            f'\nUncollected fee records identified: {uncollected_count}\n'
            f'Total uncollected amount: KES {uncollected_total:,.2f}\n'
        )

        if uncollected_count == 0:
            self.stdout.write(
                self.style.SUCCESS('All historical fees appear to be collected.')
            )
            return

        # Print SACCO breakdown
        self.stdout.write('\nBreakdown by SACCO:')
        self.stdout.write('-' * 70)
        for sacco_name, data in sorted(sacco_breakdown.items()):
            self.stdout.write(
                f'  {sacco_name}: {data["count"]} records, '
                f'KES {data["amount"]:,.2f}'
            )
        self.stdout.write('-' * 70)

        # Mark records if requested
        if mark_uncollected:
            if not execute:
                self.stdout.write(
                    self.style.WARNING(
                        '\nDRY RUN: No changes made. '
                        'Use --execute to actually flag records.'
                    )
                )
            else:
                self.stdout.write('\nFlagging records as uncollected...')
                flagged_count = 0
                for record in uncollected_records:
                    if not record.is_collected:
                        #_already flagged
                        continue
                    record.is_collected = False
                    record.save(update_fields=['is_collected'])
                    flagged_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f'Flagged {flagged_count} records as uncollected.'
                    )
                )
        else:
            self.stdout.write(
                '\nRecords identified but not flagged. '
                'Use --mark-uncollected to flag them.'
            )

        self.stdout.write('\n' + '=' * 70 + '\n')

    def _parse_cutoff(self, cutoff_str):
        """Parse cutoff datetime string or return current time."""
        if cutoff_str:
            try:
                return datetime.strptime(cutoff_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                self.stderr.write(
                    self.style.ERROR(
                        f'Invalid datetime format: {cutoff_str}. '
                        'Use YYYY-MM-DD HH:MM:SS'
                    )
                )
                raise
        return timezone.now()

    def _is_uncollected_fee(self, record):
        """
        Determine if a fee record represents uncollected revenue.

        A fee is considered uncollected if:
        1. It has a linked transaction
        2. The transaction's metadata does NOT contain 'gross_amount'
           (indicating it was created before the gross-up fix)
        3. The record is not already flagged as uncollected
        """
        if not record.transaction:
            # No linked transaction - cannot determine, assume collected
            return False

        transaction_metadata = record.transaction.metadata or {}
        has_gross_amount = 'gross_amount' in transaction_metadata

        # If metadata has gross_amount, it's a post-fix transaction (collected)
        # If metadata lacks gross_amount, it's a pre-fix transaction (uncollected)
        return not has_gross_amount


# ============================================================
# REVIEW --- READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# Why this matters for accurate financial reporting to SACCOs:
# Before the fee collection fix, PlatformRevenue recorded 2% fees as revenue
# even though no actual cash was collected from members. This inflated reported
# revenue and made SACCO invoices inaccurate. Flagging these records allows
# you to filter them out of financial reports and understand the true cash
# collected vs. revenue recognized.
#
# Exact commands to run, in order, to safely reconcile:
# 1. python manage.py migrate billing  # Add is_collected field
# 2. python manage.py reconcile_uncollected_fees --before "2024-07-08 12:00:00"
#    (Use the datetime when you deployed the fee collection fix)
# 3. Review the output to confirm the identified records are correct
# 4. python manage.py reconcile_uncollected_fees --before "2024-07-08 12:00:00" \
#    --mark-uncollected --execute
#    (This actually flags the records)
#
# Risk of double-flagging if run twice:
# The command checks if record.is_collected is already False before flagging,
# so running it multiple times is safe. It will only flag records that are
# currently True (unflagged).
#
# Decision about already-invoiced historical fees:
# You have two options:
# 1. Absorb the loss: Write off the uncollected fees as a one-time accounting
#    adjustment. This is simpler but means the platform lost that revenue.
# 2. Invoice SACCOs separately: Create supplementary invoices for the
#    shortfall. This is more complex but recovers the revenue, though it may
#    strain relationships with SACCOs who were underbilled.
#
# Recommendation: If the total uncollected amount is small (< KES 100,000),
# absorb it as a one-time loss and improve controls going forward. If large,
# consider a diplomatic approach with SACCOs explaining the technical issue
# and offering a payment plan for the shortfall.
#
# ============================================================
# END OF REVIEW --- DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
