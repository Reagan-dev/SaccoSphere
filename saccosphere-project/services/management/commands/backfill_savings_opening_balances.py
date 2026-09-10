"""One-time backfill so the ledger is complete for every savings account.

For each membership whose total ``Saving.amount`` does not equal its
savings-category ledger balance, insert a single ``OPENING_BALANCE``
ledger entry for the difference - the pre-ledger opening balance that was
never itself a ledger row. It never edits an existing entry and never
touches ``Saving.amount`` (the cache is already right; this brings the
ledger up to it).

Per-SACCO and resumable: pass ``--sacco <id>`` to do one tenant; a
membership that already has an ``OPENING_BALANCE`` entry is skipped, so
re-running after an interruption is safe. Run this on every SACCO before
enabling the daily reconciliation task.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from accounts.models import Sacco
from ledger.models import LedgerEntry
from ledger.utils import (
    create_ledger_entry,
    expected_savings_balance,
    savings_ledger_balance,
)
from saccomembership.models import Membership
from services.models import Saving


ZERO = Decimal('0.00')


class Command(BaseCommand):
    help = (
        'Insert one OPENING_BALANCE ledger entry per membership whose '
        'Saving.amount total differs from its savings-category ledger '
        'balance. Read-safe: never edits entries or Saving.amount.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sacco',
            dest='sacco_id',
            default=None,
            help='Restrict the backfill to a single SACCO id.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be written without writing anything.',
        )

    def handle(self, *args, **options):
        sacco_id = options['sacco_id']
        dry_run = options['dry_run']

        sacco_qs = Sacco.objects.all().order_by('created_at')
        if sacco_id:
            sacco_qs = sacco_qs.filter(id=sacco_id)

        total_backfilled = Decimal('0')
        memberships_backfilled = 0

        for sacco in sacco_qs.iterator():
            fixed_here = self._process_sacco(sacco, dry_run)
            if fixed_here:
                memberships_backfilled += fixed_here['count']
                total_backfilled += fixed_here['amount']
                self.stdout.write(
                    self.style.SUCCESS(
                        f'{sacco.name}: {fixed_here["count"]} membership(s), '
                        f'net KES {fixed_here["amount"]:,.2f}.'
                    )
                )

        verb = 'would backfill' if dry_run else 'backfilled'
        self.stdout.write(
            self.style.SUCCESS(
                f'Done. {verb} {memberships_backfilled} membership(s), '
                f'net KES {total_backfilled:,.2f}.'
            )
        )

    def _process_sacco(self, sacco, dry_run):
        member_ids = list(
            Saving.objects.filter(membership__sacco=sacco)
            .values_list('membership_id', flat=True)
            .distinct()
        )
        if not member_ids:
            return None

        count = 0
        net = Decimal('0')
        memberships = Membership.objects.filter(id__in=member_ids).iterator()
        for membership in memberships:
            if LedgerEntry.objects.filter(
                membership=membership,
                category=LedgerEntry.Category.OPENING_BALANCE,
            ).exists():
                continue

            expected = expected_savings_balance(membership)
            ledger = savings_ledger_balance(membership)
            drift = expected - ledger
            if drift == ZERO:
                continue

            if drift > ZERO:
                entry_type = LedgerEntry.EntryType.CREDIT
            else:
                entry_type = LedgerEntry.EntryType.DEBIT
            magnitude = abs(drift)

            earliest = (
                Saving.objects.filter(membership=membership)
                .order_by('created_at')
                .values_list('created_at', flat=True)
                .first()
            )
            self.stdout.write(
                f'  {membership.member_number}: Saving.amount total '
                f'{expected} vs ledger {ledger} -> {entry_type} '
                f'{magnitude} OPENING_BALANCE'
            )
            count += 1
            net += drift

            if dry_run:
                continue

            create_ledger_entry(
                membership=membership,
                entry_type=entry_type,
                category=LedgerEntry.Category.OPENING_BALANCE,
                amount=magnitude,
                description=(
                    'Opening balance backfill: brings the ledger up to the '
                    'savings balance held before the ledger existed '
                    f'(account opened {earliest:%Y-%m-%d}).'
                ),
                reference=f'OPENING-{membership.id}',
            )

        if count == 0:
            return None
        return {'count': count, 'amount': net}
