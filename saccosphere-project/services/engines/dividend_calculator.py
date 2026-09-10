"""Dividend calculation engine for SACCO dividend declarations."""

import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Case, DecimalField, F, Q, Sum, When
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from accounts.models import SaccoSettings
from ledger.models import LedgerEntry
from saccomanagement.audit_logger import log_audit
from services.models import DividendDeclaration, DividendPayout, Saving


logger = logging.getLogger(__name__)

MONEY_QUANTIZER = Decimal('0.01')
ZERO = Decimal('0.00')
ONE_HUNDRED = Decimal('100')
TWELVE = Decimal('12')

# LedgerEntry.amount is DecimalField(max_digits=12, decimal_places=2);
# match it for the signed conditional sum below.
_LEDGER_AMOUNT_FIELD = DecimalField(max_digits=12, decimal_places=2)

_Method = SaccoSettings.DividendCalculationMethod


class DividendMethodNotSupported(NotImplementedError):
    """A SACCO selected a dividend method with no engine implementation.

    Subclasses ``NotImplementedError`` so the stub strategies and the
    dispatcher raise one consistent type. ``DividendCalculateView`` turns
    this into a synchronous 400 before any async run is queued, and
    ``calculate_dividends_for_declaration`` re-checks as a backstop so an
    unimplemented method can never silently fall back to the default.
    """

    def __init__(self, method):
        self.method = method
        super().__init__(
            f'Dividend calculation method {method!r} is not yet supported.'
        )


def resolve_dividend_calculation_method(sacco):
    """Return the ``DividendCalculationMethod`` configured for ``sacco``.

    Falls back to ``AVERAGE_MONTH_END`` (the historical behaviour) when
    the SACCO has no ``settings`` row yet.
    """
    sacco_settings = getattr(sacco, 'settings', None)
    if sacco_settings is None:
        return _Method.AVERAGE_MONTH_END
    return sacco_settings.dividend_calculation_method


def calculate_period_balance(saving, period_start, period_end, *, method):
    """Dispatch to the configured dividend balance-basis strategy.

    ``method`` is a ``SaccoSettings.DividendCalculationMethod`` value.
    Only ``AVERAGE_MONTH_END`` is implemented; every other value (or an
    unknown string) raises :class:`DividendMethodNotSupported` rather
    than falling back to a default.
    """
    strategy = _BALANCE_STRATEGIES.get(method)
    if strategy is None:
        raise DividendMethodNotSupported(method)
    return strategy(saving, period_start, period_end)


def _day_weighted_balance(saving, period_start, period_end):
    """Stub: day-weighted average balance over the period.

    TODO(dividends): implement once at least one real SACCO's bylaws
    give a confirmed reference for the exact rules - inclusive/exclusive
    period boundaries, the day-count basis, and how a same-day
    deposit-then-withdraw is weighted. Do not guess the formula.
    """
    raise DividendMethodNotSupported(_Method.DAY_WEIGHTED)


def _minimum_balance(saving, period_start, period_end):
    """Stub: pay dividends on the lowest balance held during the period.

    TODO(dividends): implement once at least one real SACCO's bylaws
    give a confirmed reference - in particular the sampling granularity
    (calendar-day minimum vs per-transaction running minimum) and
    whether the opening balance counts. Do not guess the formula.
    """
    raise DividendMethodNotSupported(_Method.MINIMUM_BALANCE)


def calculate_average_balance(saving, period_start, period_end):
    """
    Calculate the average monthly balance for a saving account.

    Samples the saving account's own balance at the end of each calendar
    month in the period, and returns the simple average of those month-end
    balances.

    This is the ``AVERAGE_MONTH_END`` strategy in ``_BALANCE_STRATEGIES``
    - the default and, for now, the only implemented dividend method.
    Reached via ``calculate_period_balance(..., method=...)``; still
    importable directly under its historical name for callers and tests
    that only ever use this method.

    NOTE: The average-monthly-balance basis must be reviewed against each
    SACCO's bylaws - some use a stricter minimum-balance or day-weighted
    method (see the ``DAY_WEIGHTED`` / ``MINIMUM_BALANCE`` stubs). A SACCO
    selects its basis via ``SaccoSettings.dividend_calculation_method``.

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
    dividend_refs = _saving_dividend_ledger_refs(saving)
    balances = []
    for month_end_date in month_end_dates:
        balance = _get_saving_balance_at_date(
            saving,
            month_end_date,
            dividend_refs=dividend_refs,
        )
        balances.append(balance)

    if not balances:
        return Decimal('0.00')

    total = sum(balances)
    average = total / Decimal(len(balances))

    return average.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def _average_month_end_strategy(saving, period_start, period_end):
    """``AVERAGE_MONTH_END`` strategy seam.

    Delegates to the module-level ``calculate_average_balance`` by name
    (not by a captured reference) so callers and tests can still patch
    that function and have the dispatcher pick the patch up.
    """
    return calculate_average_balance(saving, period_start, period_end)


# Strategy registry: dividend method -> period-balance callable
# ``(saving, period_start, period_end) -> Decimal``. Only
# ``AVERAGE_MONTH_END`` has a real implementation; the other two are
# stubs that raise ``DividendMethodNotSupported``. Anything not listed
# here is likewise unsupported.
_BALANCE_STRATEGIES = {
    _Method.AVERAGE_MONTH_END: _average_month_end_strategy,
    _Method.DAY_WEIGHTED: _day_weighted_balance,
    _Method.MINIMUM_BALANCE: _minimum_balance,
}

# Methods with a working engine. The view checks membership here for its
# synchronous 400; keep it in sync with the real implementations above.
SUPPORTED_DIVIDEND_METHODS = frozenset({_Method.AVERAGE_MONTH_END})


def is_supported_dividend_method(method):
    """True when ``method`` has a real balance-basis implementation."""
    return method in SUPPORTED_DIVIDEND_METHODS


def _saving_dividend_ledger_refs(saving):
    """Ledger references for dividend-payout rows credited to this saving.

    ``LedgerEntry`` is membership-scoped with no per-savings-account FK
    (deferred - see Prompt 7), and dividend credits are posted with
    ``transaction=None``. ``DividendDisburseView`` writes them with the
    deterministic reference ``DIV-<declaration>-<payout>``, so we rebuild
    that set from the ``DividendPayout`` rows that point at this saving.
    """
    return [
        f'DIV-{declaration_id}-{payout_id}'
        for declaration_id, payout_id in (
            DividendPayout.objects.filter(saving=saving)
            .values_list('declaration_id', 'id')
        )
    ]


def _get_saving_balance_at_date(saving, balance_date, dividend_refs=None):
    """Reconstruct one savings account's balance as of ``balance_date``.

    ``Saving.amount`` is the ledger-reconciled current balance: since
    Prompt 7, ``ledger.utils.apply_ledger_entry`` is the only writer, and
    ``backfill_savings_opening_balances`` + the daily
    ``reconcile_savings_ledger`` task keep it equal to the sum of the
    account's savings-category ledger rows (including the pre-ledger
    opening balance, which the old M-Pesa-only reconstruction dropped -
    the reported bug).

    We anchor on that figure and unwind only this account's own
    movements dated *after* ``balance_date``. That is arithmetically the
    same as summing every entry up to the date, but it self-corrects to
    the reconciled balance and needs no per-account opening-balance row.

    "This account's movements" are the ledger rows we can attribute to
    it: M-Pesa deposits / withdrawals via
    ``transaction.mpesa.related_saving`` and dividend-payout credits
    matched by reference. A membership-level ``ADJUSTMENT`` has no
    per-account link, so it stays in the baseline; that is the safe
    direction (it can never drag a reconstructed balance below the real
    one). The exact fix for that residue is a per-entry ``saving`` FK,
    deferred with the rest of the per-account ledger work.

    All querying is scoped to ``saving.membership`` (one membership
    belongs to exactly one SACCO), so a different tenant's ledger rows
    are never summed here.
    """
    if dividend_refs is None:
        dividend_refs = _saving_dividend_ledger_refs(saving)

    later_entries = LedgerEntry.objects.filter(
        membership=saving.membership,
        created_at__date__gt=balance_date,
    ).filter(
        Q(transaction__mpesa__related_saving=saving)
        | Q(reference__in=dividend_refs)
    )
    later_net = later_entries.aggregate(
        net=Sum(
            Case(
                When(
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    then=F('amount'),
                ),
                default=-F('amount'),
                output_field=_LEDGER_AMOUNT_FIELD,
            )
        )
    )['net'] or ZERO

    balance = Decimal(saving.amount) - later_net
    return balance.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


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
        DividendMethodNotSupported: If the SACCO is configured for a
            dividend method that has no engine implementation. The view
            guards this synchronously; this is the backstop so an async
            run can never silently fall back to AVERAGE_MONTH_END.
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

        method = resolve_dividend_calculation_method(declaration.sacco)
        if not is_supported_dividend_method(method):
            raise DividendMethodNotSupported(method)

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
            average_balance = calculate_period_balance(
                saving,
                declaration.period_start,
                declaration.period_end,
                method=method,
            )

            raw_dividend = (
                average_balance
                * declaration.declared_rate
                / ONE_HUNDRED
                * (months_in_period / TWELVE)
            ).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)

            # Floor the payout at zero. A negative figure means the
            # reconstructed balance went negative, which is a data
            # problem worth a human looking at - clamp, warn and audit
            # rather than post a negative "credit" that removes money
            # from the member on a dividend run.
            dividend_amount = max(ZERO, raw_dividend)
            if raw_dividend < ZERO:
                logger.warning(
                    'Dividend for saving %s (declaration %s) computed '
                    'negative (%s) from average balance %s; clamped to '
                    '0.00.',
                    saving.id,
                    declaration.id,
                    raw_dividend,
                    average_balance,
                )
                log_audit(
                    None,
                    'DIVIDEND_NEGATIVE_CLAMPED',
                    'Saving',
                    saving.id,
                    new_values={
                        'declaration_id': str(declaration.id),
                        'sacco_id': str(declaration.sacco_id),
                        'average_balance': str(average_balance),
                        'declared_rate': str(declaration.declared_rate),
                        'raw_dividend_amount': str(raw_dividend),
                        'clamped_to': '0.00',
                    },
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
