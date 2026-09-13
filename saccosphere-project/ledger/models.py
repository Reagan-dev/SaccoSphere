from uuid import uuid4

from django.db import models


class LedgerEntry(models.Model):
    """One signed movement on a membership's ledger.

    Terminology note: ``entry_type`` (DEBIT/CREDIT) and the balanced-book
    conventions below make this look like double-entry bookkeeping, but
    it is not a full general ledger - each economic event posts exactly
    one row against the membership's own running balance, with no
    offsetting contra-account entry. There is no global invariant that
    total DEBITs equal total CREDITs across the table.

    The convention each category follows:

    * SAVING_DEPOSIT, LOAN_REPAYMENT, DIVIDEND_PAYOUT, SAVINGS_INTEREST,
      OPENING_BALANCE: CREDIT - money moving toward the member.
    * SAVING_WITHDRAWAL, LOAN_DISBURSEMENT, FEE, PENALTY, DIVIDEND:
      DEBIT - money moving away from the member's ledger balance (a loan
      disbursement is a DEBIT here because it is the SACCO paying the
      member, the mirror image of a deposit, not because the member owes
      anything on this row).
    * ADJUSTMENT: either direction, case by case - a manual correction
      modelled as a new offsetting entry (see ``save()`` below).

    ``ledger.utils.SAVINGS_LEDGER_CATEGORIES`` further narrows this to
    the categories that make up a member's *savings* balance specifically
    (excluding loan/fee/dividend movements) - see that constant's
    docstring and ``ledger.engines.balance_calculator`` before assuming
    "the balance" means the same thing in every context that reads this
    model. ``saccomanagement.sasra_reports`` separately re-derives a
    SACCO-wide cash position from these same rows, grouped by category,
    for regulatory reporting - a second, independent read of this table,
    not a second ledger row.
    """

    class EntryType(models.TextChoices):
        DEBIT = 'DEBIT', 'Debit'
        CREDIT = 'CREDIT', 'Credit'

    class Category(models.TextChoices):
        SAVING_DEPOSIT = 'SAVING_DEPOSIT', 'Saving deposit'
        SAVING_WITHDRAWAL = 'SAVING_WITHDRAWAL', 'Saving withdrawal'
        LOAN_DISBURSEMENT = 'LOAN_DISBURSEMENT', 'Loan disbursement'
        LOAN_REPAYMENT = 'LOAN_REPAYMENT', 'Loan repayment'
        FEE = 'FEE', 'Fee'
        PENALTY = 'PENALTY', 'Penalty'
        DIVIDEND = 'DIVIDEND', 'Dividend'
        DIVIDEND_PAYOUT = 'DIVIDEND_PAYOUT', 'Dividend payout'
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'
        # One-time reconciliation entry: brings the ledger up to the
        # savings balance that predated the ledger for an account. Only
        # written by the backfill_savings_opening_balances command.
        OPENING_BALANCE = 'OPENING_BALANCE', 'Opening balance'
        # Monthly savings-interest credit. Written only by
        # services.engines.savings_interest.accrue_savings_interest_for_sacco
        # (driven by the accrue_savings_interest beat task).
        SAVINGS_INTEREST = 'SAVINGS_INTEREST', 'Savings interest'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    membership = models.ForeignKey(
        'saccomembership.Membership',
        on_delete=models.PROTECT,
    )
    entry_type = models.CharField(max_length=10, choices=EntryType.choices)
    category = models.CharField(max_length=30, choices=Category.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    transaction = models.ForeignKey(
        'payments.Transaction',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['membership', 'created_at']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='ledger_entry_amount_positive',
            ),
        ]

    def save(self, *args, **kwargs):
        """Append-only: a posted entry is immutable.

        Mirrors services.models.DisbursementAuditLog. A correction is
        modelled as a new offsetting entry (category ADJUSTMENT), never an
        edit of the original. Bulk paths (QuerySet.update) still bypass
        this, so those must not be used on LedgerEntry either.
        """
        if not self._state.adding:
            raise PermissionError(
                'LedgerEntry is append-only: a posted entry is immutable. '
                'Model a correction as a new offsetting entry, never an '
                'edit.'
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(
            'LedgerEntry is append-only: a posted entry cannot be '
            'deleted. Reverse it with a new offsetting entry instead.'
        )

    def __str__(self):
        return (
            f'{self.entry_type} {self.amount} — '
            f'{self.membership} — {self.reference}'
        )
