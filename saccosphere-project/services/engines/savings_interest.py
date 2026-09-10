"""Monthly savings-interest accrual for one SACCO.

Conservative model, pending product/bylaws sign-off (flagged in the task
summary):

* **Simple** interest, accrued and credited **monthly**, on each ACTIVE
  account's **balance at run time** (a snapshot - no daily accrual, no
  mid-period proration). Because the credit joins the balance, the next
  month's accrual is on the higher figure, i.e. it compounds monthly in
  effect.
* Rate comes from ``SavingsType.interest_rate`` (annual %); the monthly
  factor is ``rate / 100 / 12``.
* No accrual on FROZEN / CLOSED accounts or inactive savings products
  (mirrors the deposit/withdrawal guardrails).
* Idempotent per ``(saving, YYYY-MM)`` via the deterministic ledger
  reference ``INT-<saving id>-<YYYY-MM>`` (``LedgerEntry.reference`` is
  unique) - a retried or re-run job credits each account at most once
  per month.

Performance: one id query + one ``select_related`` batch fetch + one
``LedgerEntry`` existence query per 500-row batch, then the unavoidable
per-account ``apply_ledger_entry`` write. No per-account balance
reconstruction (the mistake the dividend engine made) - the interest
base is simply the current ``Saving.amount``. The rate multiply is a
trivial ``Decimal`` op done in Python (not SQL) so it is
backend-deterministic.
"""

from decimal import Decimal, ROUND_HALF_UP

from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry
from services.models import Saving


MONEY_QUANTIZER = Decimal('0.01')
ZERO = Decimal('0.00')
MONTHS_PER_YEAR = Decimal('12')
PERCENT = Decimal('100')
BATCH_SIZE = 500


def accrue_savings_interest_for_sacco(sacco, *, accrual_date):
    """Credit one month of simple interest to this SACCO's ACTIVE savings.

    ``accrual_date`` fixes the period label (``YYYY-MM``) and hence the
    idempotency key. Returns a summary dict::

        {'sacco_id', 'period', 'savings_credited',
         'skipped_already_accrued', 'total_interest' (Decimal)}
    """
    period = accrual_date.strftime('%Y-%m')

    base = (
        Saving.objects.filter(
            membership__sacco=sacco,
            status=Saving.Status.ACTIVE,
            savings_type__is_active=True,
            savings_type__interest_rate__gt=ZERO,
            amount__gt=ZERO,
        )
        .select_related('savings_type')
        .order_by('id')
    )
    saving_ids = list(base.values_list('id', flat=True))

    credited = 0
    skipped = 0
    total_interest = ZERO

    for start in range(0, len(saving_ids), BATCH_SIZE):
        batch_ids = saving_ids[start:start + BATCH_SIZE]
        batch = list(base.filter(id__in=batch_ids))
        references = {f'INT-{saving.id}-{period}' for saving in batch}
        already_accrued = set(
            LedgerEntry.objects.filter(reference__in=references)
            .values_list('reference', flat=True)
        )

        for saving in batch:
            reference = f'INT-{saving.id}-{period}'
            if reference in already_accrued:
                skipped += 1
                continue

            interest = (
                saving.amount
                * saving.savings_type.interest_rate
                / PERCENT
                / MONTHS_PER_YEAR
            ).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
            if interest <= ZERO:
                continue

            apply_ledger_entry(
                saving=saving,
                amount=interest,
                entry_type=LedgerEntry.EntryType.CREDIT,
                category=LedgerEntry.Category.SAVINGS_INTEREST,
                description=(
                    f'Savings interest for {period} at '
                    f'{saving.savings_type.interest_rate}% p.a.'
                ),
                reference=reference,
            )
            credited += 1
            total_interest += interest

    return {
        'sacco_id': str(sacco.id),
        'period': period,
        'savings_credited': credited,
        'skipped_already_accrued': skipped,
        'total_interest': total_interest,
    }
