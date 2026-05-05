from uuid import uuid4

from django.db import models


class LedgerEntry(models.Model):
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
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'

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

    def __str__(self):
        return (
            f'{self.entry_type} {self.amount} — '
            f'{self.membership} — {self.reference}'
        )
