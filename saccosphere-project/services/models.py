from decimal import Decimal

from uuid import uuid4



from django.conf import settings

from django.core.exceptions import ValidationError

from django.db import models

from django.utils import timezone

from accounts.models import EncryptedJSONField

from .validators import (
    ANNUAL_RATE_VALIDATORS,
    FINANCIAL_YEAR_HELP_TEXT,
    validate_financial_year,
)





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

        validators=ANNUAL_RATE_VALIDATORS,

        help_text=(
            'Optional annual interest rate percentage (0-100, ceiling '
            'pending policy sign-off).'
        ),

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

    allows_multiple_accounts = models.BooleanField(

        default=False,

        help_text=(

            'When true, a member may hold more than one savings account '

            'of this type (e.g. several fixed-deposit pots). When false '

            '(the default) a member is limited to one account of this '

            'type.'

        ),

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



    def __str__(self):

        savings_type = self.savings_type or 'General'

        return f'{self.membership} — {savings_type}: {self.amount}'

    def clean(self):

        super().clean()

        self._enforce_same_sacco()

        self._enforce_one_account_per_type()

    def _enforce_same_sacco(self):

        """Membership and savings type must belong to the same SACCO.

        Keeps a cross-tenant account from being formed at the admin or

        any ``full_clean()`` caller; ``open_savings_account`` checks the

        same thing up front for a clearer message.

        """

        if self.savings_type_id is None or self.membership_id is None:

            return

        if self.membership.sacco_id != self.savings_type.sacco_id:

            raise ValidationError(

                {

                    'savings_type': (

                        'The savings type belongs to a different SACCO '

                        'than this member.'

                    ),

                }

            )

    def _enforce_one_account_per_type(self):

        """One savings account per member per type by default.

        A ``SavingsType`` with ``allows_multiple_accounts=True`` opts out

        (e.g. several fixed-deposit pots). This rule is enforced here

        rather than by a DB constraint because it crosses the

        ``savings_type`` relation and a Postgres partial unique index

        cannot reference a joined column. ``full_clean()`` runs it from

        the admin and from any serializer/form that calls it, and

        ``services.engines.savings_provisioning.open_savings_account``

        (the shared creation path) enforces it under a row lock. A raw

        ``.save()`` bypasses it, matching Django's usual ``clean()``

        contract.

        """

        if self.savings_type_id is None:

            return

        if self.savings_type.allows_multiple_accounts:

            return

        siblings = Saving.objects.filter(

            membership_id=self.membership_id,

            savings_type_id=self.savings_type_id,

        )

        if self.pk is not None:

            siblings = siblings.exclude(pk=self.pk)

        if siblings.exists():

            raise ValidationError(

                {

                    'savings_type': (

                        'This member already has a '

                        f'{self.savings_type.name} savings account and '

                        'this type does not allow multiple accounts.'

                    ),

                }

            )





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

        PENDING_APPROVAL = 'PENDING_APPROVAL', 'Pending approval'

        UNDER_REVIEW = 'UNDER_REVIEW', 'Under review'

        APPROVED = 'APPROVED', 'Approved'

        DISBURSED = 'DISBURSED', 'Disbursed'

        DISBURSEMENT_PENDING = (

            'DISBURSEMENT_PENDING',

            'Disbursement pending',

        )

        ACTIVE = 'ACTIVE', 'Active'

        COMPLETED = 'COMPLETED', 'Completed'

        REJECTED = 'REJECTED', 'Rejected'

        DEFAULTED = 'DEFAULTED', 'Defaulted'

    class DisbursementStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        INITIATING = 'INITIATING', 'Claimed, Calling M-Pesa'
        INITIATED = 'INITIATED', 'B2C Initiated'
        PENDING_CONFIRMATION = (
            'PENDING_CONFIRMATION',
            'M-Pesa Response Unknown (Timed Out)',
        )
        DISBURSED = 'DISBURSED', 'M-Pesa Confirmed'
        MEMBER_CONFIRMED = 'MEMBER_CONFIRMED', 'Member Confirmed'
        AUTO_CONFIRMED = 'AUTO_CONFIRMED', 'Auto-Confirmed by M-Pesa'
        DISPUTED = 'DISPUTED', 'Member Disputed'
        FAILED = 'FAILED', 'Disbursement Failed'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Admin Review'



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

    disbursement_status = models.CharField(
        max_length=30,
        choices=DisbursementStatus.choices,
        default=DisbursementStatus.PENDING,
        help_text='Current fraud-aware disbursement status.',
    )

    disbursement_idempotency_key = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        editable=False,
        help_text=(
            'Server-generated key stamped when a B2C disbursement attempt '
            'is claimed under lock. A unique DB constraint backstops the '
            'row lock so a second concurrent attempt for this loan can '
            'never be recorded, even if application code fails to lock '
            'correctly.'
        ),
    )

    mpesa_conversation_id = models.CharField(
        max_length=100,
        blank=True,
        help_text='M-Pesa B2C ConversationID returned at initiation.',
    )

    mpesa_transaction_id = models.CharField(
        max_length=100,
        blank=True,
        help_text='M-Pesa transaction receipt returned on successful callback.',
    )

    disbursement_transaction = models.ForeignKey(
        'payments.Transaction',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='loan_disbursements',
        help_text='Transaction carrying gross, net, and platform fee amounts.',
    )

    disbursement_initiated_at = models.DateTimeField(null=True, blank=True)

    disbursement_confirmed_at = models.DateTimeField(null=True, blank=True)

    member_confirmation_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    member_confirmed_at = models.DateTimeField(null=True, blank=True)

    member_disputed_at = models.DateTimeField(null=True, blank=True)

    dispute_reason = models.TextField(blank=True)

    status = models.CharField(

        max_length=30,

        choices=Status.choices,

        default=Status.PENDING,

        db_index=True,

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

    admin_notes = models.TextField(

        null=True,

        blank=True,

        help_text='Optional notes recorded by SACCO admin during review.',

    )

    approved_by = models.ForeignKey(

        settings.AUTH_USER_MODEL,

        null=True,

        blank=True,

        on_delete=models.SET_NULL,

        related_name='approved_loans',

        help_text='Staff user who approved this loan.',

    )

    mpesa_transaction_record = models.ForeignKey(

        'payments.MpesaTransaction',

        null=True,

        blank=True,

        on_delete=models.SET_NULL,

        related_name='disbursement_records',

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


class DisbursementAuditLog(models.Model):
    """
    Append-only evidence trail for loan disbursement state changes.

    Rows in this table must never be updated or deleted.
    """

    EVENTS = [
        ('LOAN_APPROVED', 'Loan Approved by Admin'),
        ('B2C_INITIATED', 'M-Pesa B2C API Called'),
        ('B2C_CALLBACK_RECEIVED', 'M-Pesa B2C Callback Received'),
        ('MEMBER_NOTIFIED', 'Member Confirmation Sent'),
        ('MEMBER_CONFIRMED', 'Member Confirmed Receipt'),
        ('MEMBER_DISPUTED', 'Member Reported Non-Receipt'),
        ('AUTO_CONFIRMED', 'Auto-Confirmed via M-Pesa API'),
        ('ESCALATED_TO_SUPERADMIN', 'Escalated to Super Admin'),
        ('RESOLVED_BY_ADMIN', 'Dispute Resolved by Super Admin'),
        ('DISBURSEMENT_FAILED', 'B2C Payment Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    loan = models.ForeignKey(
        Loan,
        on_delete=models.PROTECT,
        related_name='disbursement_audit_logs',
    )
    event = models.CharField(max_length=40, choices=EVENTS)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='disbursement_audit_logs',
    )
    actor_role = models.CharField(max_length=30)
    details = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    mpesa_ref = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'disbursement_audit_logs'
        ordering = ['created_at']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(
                'DisbursementAuditLog is append-only. Never update.'
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(
            'DisbursementAuditLog is append-only. Never delete.'
        )

    def __str__(self):
        return f'{self.event} - {self.loan_id} - {self.created_at}'


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

        db_index=True,

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

        indexes = [

            # Overdue sweep, reminder lookups and the NPL arrears query
            # all filter status + a due_date window.
            models.Index(fields=['status', 'due_date']),

            # Per-loan schedule scans (NPL resolution, repayment
            # waterfall) filter loan + status.
            models.Index(fields=['loan', 'status']),

        ]



    @property

    def is_overdue(self):

        return (

            self.status in [self.Status.PENDING, self.Status.OVERDUE]

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





class ReminderLog(models.Model):

    class ReminderType(models.TextChoices):

        THREE_DAY = 'THREE_DAY', 'Three day'

        ONE_DAY = 'ONE_DAY', 'One day'

        OVERDUE = 'OVERDUE', 'Overdue'



    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

    )

    schedule_item = models.ForeignKey(

        RepaymentSchedule,

        on_delete=models.CASCADE,

        related_name='reminder_logs',

    )

    reminder_type = models.CharField(

        max_length=20,

        choices=ReminderType.choices,

    )

    sent_at = models.DateTimeField(auto_now_add=True)

    notification_created = models.BooleanField()

    sms_sent = models.BooleanField()



    class Meta:

        unique_together = ['schedule_item', 'reminder_type']



    def __str__(self):

        return f'{self.schedule_item_id} - {self.reminder_type}'





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





class LiquidityAlert(models.Model):
    """Snapshot of a SACCO liquidity warning."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique liquidity alert identifier.',
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
        related_name='liquidity_alerts',
        help_text='SACCO this alert is for.',
    )
    available_reserves = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text='Available liquid reserves at time of alert.',
    )
    pending_disbursements = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text='Total pending loan disbursements at time of alert.',
    )
    utilisation_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text='Liquidity utilisation percentage at time of alert.',
    )
    resolved = models.BooleanField(
        default=False,
        help_text='Whether this alert has been resolved.',
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Date and time this alert was resolved.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Date and time this alert was created.',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Liquidity Alert'
        verbose_name_plural = 'Liquidity Alerts'

    def __str__(self):
        return (
            f'{self.sacco.name} liquidity alert '
            f'{self.utilisation_pct}%'
        )


class NPLFlag(models.Model):
    """Staged non-performing-loan early warning for arrears."""

    class ThresholdDays(models.IntegerChoices):
        THIRTY = 30, '30 days'
        SIXTY = 60, '60 days'
        NINETY = 90, '90 days'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique NPL flag identifier.',
    )
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name='npl_flags',
        help_text='Loan that crossed this arrears threshold.',
    )
    threshold_days = models.PositiveSmallIntegerField(
        choices=ThresholdDays.choices,
        help_text='Arrears threshold that triggered this flag.',
    )
    flagged_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Date and time this NPL flag was created.',
    )
    resolved = models.BooleanField(
        default=False,
        help_text='Whether this NPL flag has been cleared.',
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Date and time this NPL flag was resolved.',
    )

    class Meta:
        ordering = ['-flagged_at']
        unique_together = ['loan', 'threshold_days']

    def __str__(self):
        return f'{self.loan} - {self.threshold_days} days'


class CRBCheck(models.Model):

    class CreditBand(models.TextChoices):

        POOR = 'POOR', 'Poor'

        FAIR = 'FAIR', 'Fair'

        GOOD = 'GOOD', 'Good'

        VERY_GOOD = 'VERY_GOOD', 'Very Good'

        EXCELLENT = 'EXCELLENT', 'Excellent'



    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

        help_text='Unique CRB check identifier.',

    )

    loan = models.ForeignKey(

        Loan,

        on_delete=models.CASCADE,

        related_name='crb_checks',

        help_text='Loan this CRB check was performed for.',

    )

    score = models.PositiveSmallIntegerField(

        null=True,

        blank=True,

        help_text='Credit score from CRB check (300-850 range).',

    )

    band = models.CharField(

        max_length=20,

        choices=CreditBand.choices,

        null=True,

        blank=True,

        help_text='Credit band classification.',

    )

    listed_negative = models.BooleanField(

        default=False,

        help_text='Whether the applicant is listed negatively with CRB.',

    )

    provider = models.CharField(

        max_length=50,

        default='metropol',

        help_text='CRB provider used for the check.',

    )

    reference = models.CharField(

        max_length=100,

        help_text='Reference number from CRB provider.',

    )

    raw_response = EncryptedJSONField(

        null=True,

        blank=True,

        help_text=(
            'Raw response from the CRB provider, encrypted at rest. Kept '
            'for audit only and purged by the retention sweep once '
            'raw_response_purge_at passes.'
        ),

    )

    raw_response_purge_at = models.DateTimeField(

        null=True,

        blank=True,

        editable=False,

        help_text=(
            'When raw_response becomes eligible for the retention purge. '
            'Set on save from CRB_RAW_RESPONSE_RETENTION_DAYS.'
        ),

    )

    checked_by = models.ForeignKey(

        settings.AUTH_USER_MODEL,

        on_delete=models.SET_NULL,

        null=True,

        blank=True,

        related_name='crb_checks',

        help_text='User who initiated this CRB check.',

    )

    checked_at = models.DateTimeField(

        auto_now_add=True,

        help_text='Date and time this CRB check was performed.',

    )



    class Meta:

        ordering = ['-checked_at']

        verbose_name = 'CRB Check'

        verbose_name_plural = 'CRB Checks'



    def save(self, *args, **kwargs):
        """Stamp raw_response_purge_at from the retention setting.

        Mirrors KYCVerification.save()/KYC_RETENTION_DAYS. If retention is
        unset the raw response is kept indefinitely; if raw_response is
        cleared, so is the purge date.
        """
        retention_days = getattr(
            settings,
            'CRB_RAW_RESPONSE_RETENTION_DAYS',
            None,
        )
        if not self.raw_response:
            self.raw_response_purge_at = None
        elif retention_days and not self.raw_response_purge_at:
            from datetime import timedelta

            self.raw_response_purge_at = timezone.now() + timedelta(
                days=retention_days,
            )
        super().save(*args, **kwargs)

    def __str__(self):

        return f'CRB Check for {self.loan} — {self.band or "Pending"}'

class DividendDeclaration(models.Model):
    """
    Dividend declaration for a SACCO savings type and financial year.
    """

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        CALCULATING = 'CALCULATING', 'Calculating'
        CALCULATED = 'CALCULATED', 'Calculated'
        APPROVED = 'APPROVED', 'Approved'
        DISBURSING = 'DISBURSING', 'Disbursing'
        DISBURSED = 'DISBURSED', 'Disbursed'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique dividend declaration identifier.',
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
        related_name='dividend_declarations',
        help_text='SACCO this declaration belongs to.',
    )
    savings_type = models.ForeignKey(
        'SavingsType',
        on_delete=models.PROTECT,
        related_name='dividend_declarations',
        help_text='Savings type this declaration applies to.',
    )
    financial_year = models.CharField(
        max_length=20,
        validators=[validate_financial_year],
        help_text=FINANCIAL_YEAR_HELP_TEXT,
    )
    declared_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=ANNUAL_RATE_VALIDATORS,
        help_text=(
            'Annual dividend rate percentage (0-100, ceiling pending '
            'policy sign-off).'
        ),
    )
    period_start = models.DateField(
        help_text='Start date of dividend calculation period.',
    )
    period_end = models.DateField(
        help_text='End date of dividend calculation period.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text='Declaration status.',
    )
    calculated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Date and time dividends were calculated.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_dividend_declarations',
        help_text=(
            'Admin who created this declaration. Segregation of duties: '
            'a different admin must approve it (see SaccoSettings.'
            'enforce_dividend_dual_control).'
        ),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_dividend_declarations',
        help_text=(
            'Admin who approved this declaration. A different admin must '
            'disburse it.'
        ),
    )
    total_dividend_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Total dividend amount calculated.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Date and time this declaration was created.',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Dividend Declaration'
        verbose_name_plural = 'Dividend Declarations'
        constraints = [
            # A SACCO may declare a dividend for a given savings type and
            # financial year exactly once - otherwise two declarations for
            # the same period can both be disbursed and members are paid
            # twice. ``sacco`` is a direct FK on this model, so the tenant
            # is already part of the key.
            models.UniqueConstraint(
                fields=['sacco', 'savings_type', 'financial_year'],
                name='unique_dividend_declaration_per_sacco_type_year',
            ),
        ]

    def __str__(self):
        return (
            f'{self.sacco.name} - {self.financial_year} - '
            f'{self.savings_type.name}'
        )


class DividendPayout(models.Model):
    """Individual dividend payout for a member's saving.

    State machine (two states, no intermediate):

    * ``PENDING`` - set when the payout row is created by
      ``calculate_dividends_for_declaration``.
    * ``PAID`` - set by ``disburse_dividends_for_declaration`` once the
      dividend has been posted to the member's savings ledger via
      ``apply_ledger_entry`` (a ``DIVIDEND_PAYOUT`` credit that raises
      ``Saving.amount``). A zero-amount payout also lands here directly.

    A dividend on this platform is reinvested straight into the member's
    savings account, so "credited to the account" and "paid" are the same
    event - there is no separate step and therefore no third state. The
    old ``CREDITED`` value was never assigned by any code path and was
    removed in migration 0018.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique dividend payout identifier.',
    )
    declaration = models.ForeignKey(
        DividendDeclaration,
        on_delete=models.CASCADE,
        related_name='payouts',
        help_text='Dividend declaration this payout belongs to.',
    )
    membership = models.ForeignKey(
        'saccomembership.Membership',
        on_delete=models.CASCADE,
        related_name='dividend_payouts',
        help_text='Membership receiving this dividend.',
    )
    saving = models.ForeignKey(
        'Saving',
        on_delete=models.CASCADE,
        related_name='dividend_payouts',
        help_text='Saving account this dividend is based on.',
    )
    average_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Average monthly balance during calculation period.',
    )
    dividend_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Dividend amount calculated.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text='Payout status.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this payout was created.',
    )

    class Meta:
        ordering = ['declaration', 'membership']
        unique_together = ['declaration', 'saving']
        verbose_name = 'Dividend Payout'
        verbose_name_plural = 'Dividend Payouts'
        indexes = [
            # Serves the disburse scan
            # (``payouts.filter(status=PENDING)`` -> declaration_id = X
            # AND status = 'PENDING') and, via the leading column, the
            # ``?declaration=<id>`` list filter.
            models.Index(
                fields=['declaration', 'status'],
                name='divpayout_decl_status_idx',
            ),
        ]

    def __str__(self):
        return f'{self.membership} - {self.dividend_amount}'
