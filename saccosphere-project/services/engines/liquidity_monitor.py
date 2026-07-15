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
