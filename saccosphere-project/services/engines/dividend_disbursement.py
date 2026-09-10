"""Batched, all-or-nothing disbursement of an approved dividend run.

Lifted verbatim from ``DividendDisburseView`` so the Celery task and the
view share one implementation. Every correctness guard is preserved: one
outer ``transaction.atomic()`` (a failure part-way through rolls back
every ledger entry and payout-status change), a ``select_for_update`` on
the declaration and on each 500-row payout batch, the zero-amount skip,
and ``apply_ledger_entry`` as the only writer of ``Saving.amount``.
"""

from decimal import Decimal

from django.db import transaction

from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry
from services.models import DividendDeclaration, DividendPayout


ZERO = Decimal('0.00')
DISBURSE_BATCH_SIZE = 500


def disburse_dividends_for_declaration(declaration):
    """Post every PENDING payout of a declaration to the ledger.

    Accepts a declaration in ``APPROVED`` (direct call) or ``DISBURSING``
    (the status the view sets before enqueuing the task). Returns
    ``{'paid_count': int}``. Raises ``ValueError`` for any other status.
    """
    with transaction.atomic():
        declaration = DividendDeclaration.objects.select_for_update().get(
            pk=declaration.pk,
        )

        if declaration.status not in (
            DividendDeclaration.Status.APPROVED,
            DividendDeclaration.Status.DISBURSING,
        ):
            raise ValueError(
                'Can only disburse declarations in APPROVED status, not '
                f'{declaration.status}.'
            )

        payout_ids = list(
            declaration.payouts.filter(
                status=DividendPayout.Status.PENDING,
            ).values_list('id', flat=True)
        )
        paid_count = 0

        for start in range(0, len(payout_ids), DISBURSE_BATCH_SIZE):
            batch_ids = payout_ids[start:start + DISBURSE_BATCH_SIZE]
            batch_payouts = DividendPayout.objects.select_related(
                'membership',
                'saving',
            ).select_for_update().filter(id__in=batch_ids)

            for payout in batch_payouts:
                if payout.dividend_amount <= ZERO:
                    # Clamped to zero at calculation time (negative
                    # reconstructed balance). Nothing to post -
                    # apply_ledger_entry rejects non-positive amounts -
                    # so mark it paid and move on.
                    payout.status = DividendPayout.Status.PAID
                    payout.save(update_fields=['status'])
                    paid_count += 1
                    continue

                # apply_ledger_entry is the only path allowed to move
                # Saving.amount: it locks the saving, posts the
                # DIVIDEND_PAYOUT credit and raises the balance in one
                # atomic block.
                ledger_entry = apply_ledger_entry(
                    saving=payout.saving,
                    amount=payout.dividend_amount,
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    category=LedgerEntry.Category.DIVIDEND_PAYOUT,
                    description=(
                        f'Dividend payout for {declaration.financial_year}'
                    ),
                    reference=f'DIV-{declaration.id}-{payout.id}',
                )

                if ledger_entry is None:
                    raise RuntimeError(
                        'Failed to create dividend ledger entry.'
                    )

                payout.status = DividendPayout.Status.PAID
                payout.save(update_fields=['status'])
                paid_count += 1

        declaration.status = DividendDeclaration.Status.DISBURSED
        declaration.save(update_fields=['status'])

        return {'paid_count': paid_count}
