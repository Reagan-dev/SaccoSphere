from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import models


class PaymentProvider(models.Model):
    class ProviderType(models.TextChoices):
        MPESA = 'MPESA', 'M-Pesa'
        AIRTEL = 'AIRTEL', 'Airtel'
        BANK = 'BANK', 'Bank'
        INTERNAL = 'INTERNAL', 'Internal'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique payment provider identifier.',
    )
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text='Payment provider display name.',
    )
    provider_type = models.CharField(
        max_length=20,
        choices=ProviderType.choices,
        help_text='Type of payment provider.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this payment provider is active.',
    )
    config = models.JSONField(
        default=dict,
        help_text='Provider configuration such as endpoints. Do not store secrets.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this provider was created.',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Transaction(models.Model):
    class TransactionType(models.TextChoices):
        DEPOSIT = 'DEPOSIT', 'Deposit'
        WITHDRAWAL = 'WITHDRAWAL', 'Withdrawal'
        TRANSFER = 'TRANSFER', 'Transfer'
        LOAN_REPAYMENT = 'LOAN_REPAYMENT', 'Loan repayment'
        LOAN_DISBURSEMENT = 'LOAN_DISBURSEMENT', 'Loan disbursement'
        FEE = 'FEE', 'Fee'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        REVERSED = 'REVERSED', 'Reversed'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique transaction identifier.',
    )
    provider = models.ForeignKey(
        PaymentProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='Payment provider used for this transaction.',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        help_text='User who owns this transaction.',
    )
    reference = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text='Internal SaccoSphere transaction reference.',
    )
    external_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text='External provider transaction reference.',
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=TransactionType.choices,
        default=TransactionType.DEPOSIT,
        help_text='Business purpose of this transaction.',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Transaction amount.',
    )
    fee_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Fee charged for this transaction.',
    )
    currency = models.CharField(
        max_length=3,
        default='KES',
        help_text='ISO currency code.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text='Current transaction status.',
    )
    description = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='Optional transaction description.',
    )
    metadata = models.JSONField(
        default=dict,
        help_text='Additional non-sensitive transaction metadata.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Date and time this transaction was created.',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text='Date and time this transaction was last updated.',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return (
            f'{self.transaction_type} {self.amount} — '
            f'{self.reference} — {self.status}'
        )


class Callback(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique callback identifier.',
    )
    transaction = models.ForeignKey(
        Transaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='Transaction linked to this callback, if known.',
    )
    provider = models.ForeignKey(
        PaymentProvider,
        on_delete=models.PROTECT,
        help_text='Provider that sent this callback.',
    )
    raw_payload = models.JSONField(
        help_text='Raw provider callback payload.',
    )
    processed = models.BooleanField(
        default=False,
        help_text='Whether this callback has been processed.',
    )
    processing_error = models.TextField(
        null=True,
        blank=True,
        help_text='Processing error message, if processing failed.',
    )
    received_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this callback was received.',
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Date and time this callback was processed.',
    )

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        status = 'processed' if self.processed else 'pending'
        return f'Callback {self.provider.name} — {status}'


class MpesaTransaction(models.Model):
    class TransactionType(models.TextChoices):
        STK_PUSH = 'STK_PUSH', 'STK Push'
        B2C = 'B2C', 'B2C'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique M-Pesa transaction identifier.',
    )
    transaction = models.OneToOneField(
        Transaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='mpesa',
        help_text='Main transaction linked to this M-Pesa record.',
    )
    phone_number = models.CharField(
        max_length=13,
        help_text='Phone number used for the M-Pesa transaction.',
    )
    merchant_request_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='M-Pesa merchant request identifier.',
    )
    checkout_request_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text='M-Pesa checkout request identifier.',
    )
    conversation_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text='M-Pesa conversation identifier.',
    )
    originator_conversation_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='M-Pesa originator conversation identifier.',
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        default=TransactionType.STK_PUSH,
        help_text='M-Pesa transaction flow type.',
    )
    result_code = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        help_text='Provider result code.',
    )
    result_description = models.TextField(
        null=True,
        blank=True,
        help_text='Provider result description.',
    )
    mpesa_receipt_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text='M-Pesa receipt number.',
    )
    callback_received = models.BooleanField(
        default=False,
        help_text='Whether an M-Pesa callback has been received.',
    )
    related_saving = models.ForeignKey(
        'services.Saving',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='Saving affected by this transaction.',
    )
    related_loan = models.ForeignKey(
        'services.Loan',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='Loan affected by this transaction.',
    )
    related_instalment_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Repayment instalment number affected by this transaction.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this M-Pesa record was created.',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text='Date and time this M-Pesa record was last updated.',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'M-Pesa {self.transaction_type} — {self.phone_number} — '
            f'{self.checkout_request_id}'
        )


class MpesaIdempotencyRecord(models.Model):
    checkout_request_id = models.CharField(
        max_length=100,
        unique=True,
        help_text='Checkout request identifier already processed.',
    )
    processed_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this checkout request was processed.',
    )

    class Meta:
        ordering = ['-processed_at']

    def __str__(self):
        return self.checkout_request_id


class PlatformFee(models.Model):
    class FeeType(models.TextChoices):
        TRANSACTION_PCT = 'TRANSACTION_PCT', 'Transaction percentage'
        MONTHLY_SAAS = 'MONTHLY_SAAS', 'Monthly SaaS'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique platform fee identifier.',
    )
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        help_text='Transaction this platform fee belongs to.',
    )
    fee_type = models.CharField(
        max_length=20,
        choices=FeeType.choices,
        help_text='Type of platform fee.',
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Platform fee amount.',
    )
    invoice_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text='Optional invoice number for this fee.',
    )
    processed = models.BooleanField(
        default=False,
        help_text='Whether this platform fee has been processed.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this fee was created.',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.fee_type} — {self.amount}'
