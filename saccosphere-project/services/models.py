from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone


class SavingsType(models.Model):
    class Name(models.TextChoices):
        BOSA = 'BOSA', 'BOSA'
        FOSA = 'FOSA', 'FOSA'
        SHARE_CAPITAL = 'SHARE_CAPITAL', 'Share capital'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique savings type identifier.',
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
        help_text='SACCO that owns this savings type.',
    )
    name = models.CharField(
        max_length=20,
        choices=Name.choices,
        help_text='Savings product category.',
    )
    description = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='Optional savings type description.',
    )
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Optional annual interest rate percentage.',
    )
    minimum_contribution = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Minimum expected contribution amount.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this savings type is available.',
    )

    class Meta:
        ordering = ['sacco__name', 'name']
        unique_together = ['name', 'sacco']

    def __str__(self):
        return f'{self.sacco.name} — {self.name}'


class Saving(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        FROZEN = 'FROZEN', 'Frozen'
        CLOSED = 'CLOSED', 'Closed'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique savings account identifier.',
    )
    membership = models.ForeignKey(
        'saccomembership.Membership',
        on_delete=models.PROTECT,
        help_text='Membership that owns this savings account.',
    )
    savings_type = models.ForeignKey(
        SavingsType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='Savings product used by this account.',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Current savings balance.',
    )
    total_contributions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Total contributions posted to this account.',
    )
    total_withdrawals = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Total withdrawals posted from this account.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text='Current savings account status.',
    )
    dividend_eligible = models.BooleanField(
        default=True,
        help_text='Whether this saving qualifies for dividends.',
    )
    last_transaction_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date of the latest savings transaction.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this saving was created.',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text='Date and time this saving was last updated.',
    )

    class Meta:
        ordering = ['-created_at']
        unique_together = ['membership', 'savings_type']

    def __str__(self):
        savings_type = self.savings_type or 'General'
        return f'{self.membership} — {savings_type}: {self.amount}'


class LoanType(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique loan type identifier.',
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
        help_text='SACCO that offers this loan type.',
    )
    name = models.CharField(
        max_length=100,
        help_text='Loan product name.',
    )
    description = models.TextField(
        null=True,
        blank=True,
        help_text='Optional loan product description.',
    )
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text='Annual loan interest rate percentage.',
    )
    max_term_months = models.PositiveIntegerField(
        help_text='Maximum repayment term in months.',
    )
    min_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Minimum loan principal amount.',
    )
    max_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Optional maximum loan principal amount.',
    )
    requires_guarantors = models.BooleanField(
        default=True,
        help_text='Whether this loan product requires guarantors.',
    )
    min_guarantors = models.PositiveSmallIntegerField(
        default=1,
        help_text='Minimum number of guarantors required.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this loan product is available.',
    )

    class Meta:
        ordering = ['sacco__name', 'name']

    def __str__(self):
        return f'{self.sacco.name} — {self.name}'


class Loan(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        GUARANTORS_PENDING = 'GUARANTORS_PENDING', 'Guarantors pending'
        BOARD_REVIEW = 'BOARD_REVIEW', 'Board review'
        APPROVED = 'APPROVED', 'Approved'
        DISBURSEMENT_PENDING = (
            'DISBURSEMENT_PENDING',
            'Disbursement pending',
        )
        ACTIVE = 'ACTIVE', 'Active'
        COMPLETED = 'COMPLETED', 'Completed'
        REJECTED = 'REJECTED', 'Rejected'
        DEFAULTED = 'DEFAULTED', 'Defaulted'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique loan identifier.',
    )
    membership = models.ForeignKey(
        'saccomembership.Membership',
        on_delete=models.PROTECT,
        help_text='Membership applying for or holding this loan.',
    )
    loan_type = models.ForeignKey(
        LoanType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='Loan product used for this loan.',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Requested loan principal amount.',
    )
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text='Interest rate applied to this loan.',
    )
    term_months = models.PositiveIntegerField(
        help_text='Loan repayment term in months.',
    )
    outstanding_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Remaining loan balance.',
    )
    disbursed_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Amount disbursed to the member.',
    )
    disbursement_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date the loan was disbursed.',
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        help_text='Current loan workflow status.',
    )
    application_notes = models.TextField(
        null=True,
        blank=True,
        help_text='Optional notes submitted with the loan application.',
    )
    rejection_reason = models.TextField(
        null=True,
        blank=True,
        help_text='Reason provided when the loan is rejected.',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='approved_loans',
        help_text='Staff user who approved this loan.',
    )
    mpesa_transaction = models.ForeignKey(
        'payments.MpesaTransaction',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='disbursed_loans',
        help_text='M-Pesa transaction used to disburse this loan.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Date and time this loan was created.',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text='Date and time this loan was last updated.',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        short_id = str(self.id)[:8]
        return (
            f'Loan {short_id} — {self.membership} — '
            f'{self.amount} — {self.status}'
        )


class RepaymentSchedule(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'
        OVERDUE = 'OVERDUE', 'Overdue'
        PARTIAL = 'PARTIAL', 'Partial'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique repayment schedule identifier.',
    )
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name='schedule',
        help_text='Loan this instalment belongs to.',
    )
    instalment_number = models.PositiveIntegerField(
        help_text='Sequential instalment number for this loan.',
    )
    due_date = models.DateField(
        db_index=True,
        help_text='Date this instalment is due.',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Total instalment amount due.',
    )
    principal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Principal portion of this instalment.',
    )
    interest = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Interest portion of this instalment.',
    )
    balance_after = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Loan balance after this instalment is paid.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text='Current instalment payment status.',
    )
    paid_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date this instalment was paid.',
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Amount already paid for this instalment.',
    )
    penalty_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Penalty charged for this instalment.',
    )

    class Meta:
        ordering = ['instalment_number']
        unique_together = ['loan', 'instalment_number']

    @property
    def is_overdue(self):
        return (
            self.status == self.Status.PENDING
            and self.due_date < timezone.localdate()
        )

    @property
    def days_overdue(self):
        if not self.is_overdue:
            return 0

        return (timezone.localdate() - self.due_date).days

    def __str__(self):
        return (
            f'Inst {self.instalment_number} — {self.loan} — '
            f'{self.due_date} — {self.status}'
        )


class Guarantor(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        DECLINED = 'DECLINED', 'Declined'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique guarantor request identifier.',
    )
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name='guarantors',
        help_text='Loan being guaranteed.',
    )
    guarantor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='guarantees',
        help_text='User asked to guarantee this loan.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text='Current guarantor response status.',
    )
    guarantee_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Amount guaranteed by this user.',
    )
    requested_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time the guarantee was requested.',
    )
    responded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Date and time the guarantor responded.',
    )
    notes = models.TextField(
        null=True,
        blank=True,
        help_text='Optional guarantor notes.',
    )

    class Meta:
        ordering = ['-requested_at']
        unique_together = ['loan', 'guarantor']

    def __str__(self):
        return f'{self.guarantor.email} guarantees {self.loan}'


class GuaranteeCapacity(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique guarantee capacity identifier.',
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='guarantee_capacity',
        help_text='User whose guarantee capacity is tracked.',
    )
    total_savings = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Total savings available for guarantee calculations.',
    )
    active_guarantees = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Total active guarantees already committed.',
    )
    available_capacity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Remaining guarantee capacity.',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text='Date and time this capacity was last updated.',
    )

    class Meta:
        ordering = ['user__email']

    def __str__(self):
        return f'{self.user.email} capacity: {self.available_capacity}'


class Insurance(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique insurance policy identifier.',
    )
    membership = models.ForeignKey(
        'saccomembership.Membership',
        on_delete=models.CASCADE,
        help_text='Membership covered by this insurance policy.',
    )
    policy_number = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text='Optional unique policy number.',
    )
    type = models.CharField(
        max_length=100,
        help_text='Insurance product type.',
    )
    coverage_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Total coverage amount.',
    )
    premium = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Insurance premium amount.',
    )
    start_date = models.DateField(
        help_text='Policy start date.',
    )
    end_date = models.DateField(
        help_text='Policy end date.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text='Current insurance policy status.',
    )

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f'{self.membership} — {self.type}'
