"""Dividend calculation engine for SACCO dividend declarations."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from ledger.engines.balance_calculator import get_balance_at_date
from services.models import DividendDeclaration, DividendPayout, Saving


MONEY_QUANTIZER = Decimal('0.01')
ONE_HUNDRED = Decimal('100')
TWELVE = Decimal('12')


def calculate_average_balance(saving, period_start, period_end):
    """
    Calculate the average monthly balance for a saving account.

    Samples the saving's balance at the end of each calendar month in the
    period using get_balance_at_date(), and returns the simple average of
    those month-end balances.

    NOTE: This uses the average-monthly-balance method. This must be reviewed
    against the SACCO's bylaws, as some SACCOs use a stricter minimum-balance
    or day-weighted method. The calculation method should match what is
    specified in the SACCO's dividend policy.

    Args:
        saving: Saving instance
        period_start: datetime.date - start of calculation period
        period_end: datetime.date - end of calculation period

    Returns:
        Decimal: Average monthly balance rounded to 2 decimal places
    """
    if period_end < period_start:
        raise ValueError('period_end cannot be before period_start.')

    # Generate month-end dates within the period.
    month_end_dates = []
    current = (
        period_start.replace(day=1)
        + relativedelta(months=1)
        - timedelta(days=1)
    )

    while current <= period_end:
        month_end_dates.append(current)
        current += relativedelta(months=1)

    # If no full months are covered, sample the balance at period_end.
    if not month_end_dates:
        month_end_dates = [period_end]

    # Average-monthly-balance method: sample each month-end balance and take
    # the simple average. This must be reviewed against the SACCO's bylaws
    # because some SACCOs require a stricter minimum-balance or day-weighted
    # dividend method.
    balances = []
    for month_end_date in month_end_dates:
        balance = get_balance_at_date(saving.membership, month_end_date)
        balances.append(Decimal(balance))

    if not balances:
        return Decimal('0.00')

    total = sum(balances)
    average = total / Decimal(len(balances))

    return average.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def calculate_dividends_for_declaration(declaration):
    """
    Calculate dividends for a dividend declaration.

    This function is idempotent - if the declaration already has payouts and
    status is DRAFT or CALCULATED, existing payouts are deleted before
    recalculating. Recalculation is not allowed once status is APPROVED or
    DISBURSED.

    Args:
        declaration: DividendDeclaration instance

    Returns:
        dict: {
            'total_dividend_amount': Decimal,
            'payout_count': int,
        }

    Raises:
        ValueError: If declaration status is APPROVED or DISBURSED
    """
    with transaction.atomic():
        declaration = DividendDeclaration.objects.select_for_update().get(
            pk=declaration.pk,
        )

        if declaration.status in [
            DividendDeclaration.Status.APPROVED,
            DividendDeclaration.Status.DISBURSED,
        ]:
            raise ValueError(
                'Cannot recalculate dividends for declaration with status '
                f'{declaration.status}.'
            )

        if declaration.payouts.exists():
            declaration.payouts.all().delete()

        eligible_savings = Saving.objects.filter(
            membership__sacco=declaration.sacco,
            savings_type=declaration.savings_type,
            dividend_eligible=True,
        ).select_related('membership')

        months_in_period = Decimal(
            (
                (
                    declaration.period_end.year
                    - declaration.period_start.year
                )
                * 12
            )
            + (
                declaration.period_end.month
                - declaration.period_start.month
            )
            + 1
        )

        payout_records = []
        total_dividend_amount = Decimal('0.00')

        for saving in eligible_savings:
            average_balance = calculate_average_balance(
                saving,
                declaration.period_start,
                declaration.period_end,
            )

            dividend_amount = (
                average_balance
                * declaration.declared_rate
                / ONE_HUNDRED
                * (months_in_period / TWELVE)
            )
            dividend_amount = dividend_amount.quantize(
                MONEY_QUANTIZER,
                rounding=ROUND_HALF_UP,
            )

            payout_records.append(
                DividendPayout(
                    declaration=declaration,
                    membership=saving.membership,
                    saving=saving,
                    average_balance=average_balance,
                    dividend_amount=dividend_amount,
                    status=DividendPayout.Status.PENDING,
                )
            )

            total_dividend_amount += dividend_amount

        DividendPayout.objects.bulk_create(payout_records, batch_size=500)

        declaration.total_dividend_amount = total_dividend_amount.quantize(
            MONEY_QUANTIZER,
            rounding=ROUND_HALF_UP,
        )
        declaration.status = DividendDeclaration.Status.CALCULATED
        declaration.calculated_at = timezone.now()
        declaration.save(
            update_fields=[
                'total_dividend_amount',
                'status',
                'calculated_at',
            ]
        )

        return {
            'total_dividend_amount': declaration.total_dividend_amount,
            'payout_count': len(payout_records),
        }
