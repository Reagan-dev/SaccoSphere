"""Report PENDING/PROCESSING STK inflow transactions initiated before the
Daraja whole-shilling rounding fix (SaccoInvoiceFeeCalculator's
whole-shilling policy - see payments/fee_calculator.py).

Before that fix, gross_amount could be fractional (e.g. net=550.00 at a
1% fee -> gross=555.50), while Daraja only ever moves whole shillings. A
transaction still non-terminal with a fractional gross_amount was
initiated with that stale expected value: a genuine M-Pesa callback now
reports a whole shilling and will never exactly match it, so
reconciliation will flag it AMOUNT_MISMATCH under the *new* comparison
just as reliably as it did under the old one - it was never going to
resolve cleanly either way. These must be moved into manual
reconciliation by a human, not silently reprocessed or auto-corrected.

Read-only: never rewrites a value.
"""

from django.core.management.base import BaseCommand, CommandError

from payments.models import Transaction


class Command(BaseCommand):
    help = (
        'List PENDING/PROCESSING deposit/repayment transactions whose '
        'gross_amount is not a whole shilling - created before the '
        'Daraja whole-shilling rounding fix. Read-only.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sacco',
            dest='sacco_id',
            default=None,
            help='Restrict the audit to a single SACCO id.',
        )

    def handle(self, *args, **options):
        queryset = Transaction.objects.filter(
            transaction_type__in=(
                Transaction.TransactionType.DEPOSIT,
                Transaction.TransactionType.LOAN_REPAYMENT,
            ),
            status__in=(
                Transaction.Status.PENDING,
                Transaction.Status.PROCESSING,
            ),
            gross_amount__isnull=False,
        ).select_related('sacco').order_by('sacco_id', 'created_at')

        if options['sacco_id']:
            queryset = queryset.filter(sacco_id=options['sacco_id'])

        offenders = [
            row
            for row in queryset.iterator()
            if row.gross_amount != row.gross_amount.to_integral_value()
        ]

        if not offenders:
            self.stdout.write(
                self.style.SUCCESS(
                    'No pending/processing deposit or repayment '
                    'transaction has a fractional gross_amount.'
                )
            )
            return

        by_sacco = {}
        for row in offenders:
            by_sacco.setdefault(row.sacco_id, []).append(row)

        for sacco_id, rows in by_sacco.items():
            sacco_name = rows[0].sacco.name if rows[0].sacco_id else 'Unknown'
            self.stdout.write('')
            self.stdout.write(
                self.style.ERROR(
                    f'{sacco_name} ({sacco_id}): {len(rows)} pending '
                    'transaction(s) with a fractional gross_amount'
                )
            )
            for row in rows:
                self.stdout.write(
                    f'  - {row.id} | {row.reference} | '
                    f'{row.transaction_type} | status={row.status} | '
                    f'net={row.amount} gross={row.gross_amount} | '
                    f'user={row.user_id} | '
                    f'created={row.created_at:%Y-%m-%d %H:%M}'
                )

        raise CommandError(
            f'{len(offenders)} pending/processing transaction(s) were '
            'initiated with a fractional gross_amount, before the '
            'whole-shilling rounding fix. A genuine M-Pesa callback for '
            'one of these reports a whole shilling and will never '
            'exactly match the stored expected amount - move each into '
            'manual reconciliation rather than letting it resolve or '
            'time out under the new comparison.'
        )
