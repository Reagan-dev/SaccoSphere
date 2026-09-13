"""Liquidity risk calculations for SACCO loan disbursements."""

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum

from accounts.models import SaccoSettings
from ledger.models import LedgerEntry
from services.models import Loan


MONEY_ZERO = Decimal('0.00')
PCT_ZERO = Decimal('0.00')
PCT_ONE_HUNDRED = Decimal('100.00')
PCT_QUANTIZER = Decimal('0.01')
# Mirrors SaccoSettings.liquidity_threshold_percentage's own field
# default, for a SACCO with no SaccoSettings row yet (see
# bulk_check_liquidity_risk below).
DEFAULT_LIQUIDITY_THRESHOLD = Decimal('80.00')

CASH_IN_CATEGORIES = (
    LedgerEntry.Category.SAVING_DEPOSIT,
    LedgerEntry.Category.LOAN_REPAYMENT,
    LedgerEntry.Category.FEE,
    LedgerEntry.Category.PENALTY,
)
CASH_OUT_CATEGORIES = (
    LedgerEntry.Category.SAVING_WITHDRAWAL,
    LedgerEntry.Category.LOAN_DISBURSEMENT,
    LedgerEntry.Category.DIVIDEND,
)
PENDING_DISBURSEMENT_STATUSES = (
    Loan.Status.APPROVED,
    Loan.Status.DISBURSEMENT_PENDING,
)


def get_available_liquid_reserves(sacco):
    """Return cash-like ledger credits less cash-like ledger debits."""
    cash_in = _sum_ledger_amounts(
        sacco=sacco,
        categories=CASH_IN_CATEGORIES,
        entry_type=LedgerEntry.EntryType.CREDIT,
    )
    cash_out = _sum_ledger_amounts(
        sacco=sacco,
        categories=CASH_OUT_CATEGORIES,
        entry_type=LedgerEntry.EntryType.DEBIT,
    )

    return cash_in - cash_out


def get_pending_disbursement_total(sacco):
    """Return approved loan principal that has not yet been disbursed."""
    total = Loan.objects.filter(
        membership__sacco=sacco,
        status__in=PENDING_DISBURSEMENT_STATUSES,
    ).aggregate(total=Sum('amount'))['total']

    return total or MONEY_ZERO


def check_liquidity_risk(sacco):
    """Return a point-in-time liquidity risk snapshot for a SACCO."""
    available_reserves = get_available_liquid_reserves(sacco)
    pending_disbursements = get_pending_disbursement_total(sacco)
    utilisation_pct = _calculate_utilisation_pct(
        available_reserves,
        pending_disbursements,
    )
    threshold = _get_liquidity_threshold(sacco)

    return {
        'available_reserves': available_reserves,
        'pending_disbursements': pending_disbursements,
        'utilisation_pct': utilisation_pct,
        'at_risk': utilisation_pct >= threshold,
    }


def bulk_check_liquidity_risk(saccos):
    """Return ``{sacco_id: risk_dict}`` for every sacco, in O(1) queries.

    Computes the same risk dict as check_liquidity_risk for every SACCO
    passed in, but with one aggregate query per figure (cash-in,
    cash-out, pending disbursements, thresholds) instead of one
    round-trip per SACCO - this task runs hourly across every active
    SACCO, so a per-SACCO query cost scales badly the way NPL
    monitoring's batched sweep (services.engines.npl_monitor) was
    already rewritten to avoid.

    Unlike check_liquidity_risk -> _get_liquidity_threshold, a SACCO
    with no SaccoSettings row yet is not lazily created here - a batch
    read job creating N rows as a side effect is worth avoiding, and any
    genuine settings lookup/write elsewhere (e.g. the settings API) goes
    through its own get_or_create regardless. DEFAULT_LIQUIDITY_THRESHOLD
    is used for such a SACCO instead, matching the field's own default.
    """
    sacco_ids = [sacco.id for sacco in saccos]
    if not sacco_ids:
        return {}

    cash_in_totals = _bulk_sum_ledger_amounts(
        sacco_ids, CASH_IN_CATEGORIES, LedgerEntry.EntryType.CREDIT,
    )
    cash_out_totals = _bulk_sum_ledger_amounts(
        sacco_ids, CASH_OUT_CATEGORIES, LedgerEntry.EntryType.DEBIT,
    )
    pending_totals = dict(
        Loan.objects.filter(
            membership__sacco_id__in=sacco_ids,
            status__in=PENDING_DISBURSEMENT_STATUSES,
        )
        .values('membership__sacco_id')
        .annotate(total=Sum('amount'))
        .values_list('membership__sacco_id', 'total')
    )
    thresholds = dict(
        SaccoSettings.objects.filter(
            sacco_id__in=sacco_ids,
        ).values_list('sacco_id', 'liquidity_threshold_percentage')
    )

    risks = {}
    for sacco_id in sacco_ids:
        available_reserves = cash_in_totals.get(
            sacco_id, MONEY_ZERO,
        ) - cash_out_totals.get(sacco_id, MONEY_ZERO)
        pending_disbursements = pending_totals.get(sacco_id) or MONEY_ZERO
        utilisation_pct = _calculate_utilisation_pct(
            available_reserves, pending_disbursements,
        )
        threshold = thresholds.get(sacco_id, DEFAULT_LIQUIDITY_THRESHOLD)
        risks[sacco_id] = {
            'available_reserves': available_reserves,
            'pending_disbursements': pending_disbursements,
            'utilisation_pct': utilisation_pct,
            'at_risk': utilisation_pct >= threshold,
        }
    return risks


def _bulk_sum_ledger_amounts(sacco_ids, categories, entry_type):
    rows = (
        LedgerEntry.objects.filter(
            membership__sacco_id__in=sacco_ids,
            category__in=categories,
            entry_type=entry_type,
        )
        .values('membership__sacco_id')
        .annotate(total=Sum('amount'))
        .values_list('membership__sacco_id', 'total')
    )
    return dict(rows)


def _sum_ledger_amounts(sacco, categories, entry_type):
    total = LedgerEntry.objects.filter(
        membership__sacco=sacco,
        category__in=categories,
        entry_type=entry_type,
    ).aggregate(total=Sum('amount'))['total']

    return total or MONEY_ZERO


def _get_liquidity_threshold(sacco):
    settings, _created = SaccoSettings.objects.get_or_create(sacco=sacco)
    return settings.liquidity_threshold_percentage


def _calculate_utilisation_pct(available_reserves, pending_disbursements):
    if available_reserves <= MONEY_ZERO:
        if pending_disbursements > MONEY_ZERO:
            return PCT_ONE_HUNDRED
        return PCT_ZERO

    utilisation_pct = (
        pending_disbursements / available_reserves * PCT_ONE_HUNDRED
    )
    return utilisation_pct.quantize(PCT_QUANTIZER, rounding=ROUND_HALF_UP)
