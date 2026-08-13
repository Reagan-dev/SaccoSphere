from decimal import Decimal
from uuid import uuid4

from django.db import models


class SaccoSubscription(models.Model):
    class Plan(models.TextChoices):
        FREE = 'FREE', 'Free'
        BASIC = 'BASIC', 'Basic'
        PRO = 'PRO', 'Pro'
        ENTERPRISE = 'ENTERPRISE', 'Enterprise'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        PAST_DUE = 'PAST_DUE', 'Past due'
        CANCELLED = 'CANCELLED', 'Cancelled'
        EXPIRED = 'EXPIRED', 'Expired'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    sacco = models.OneToOneField(
        'accounts.Sacco',
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.FREE,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    monthly_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    starts_at = models.DateField(null=True, blank=True)
    ends_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sacco__name']

    def __str__(self):
        return f'{self.sacco.name} — {self.plan} — {self.status}'


class PlatformRevenue(models.Model):
    class RevenueType(models.TextChoices):
        SUBSCRIPTION = 'SUBSCRIPTION', 'Subscription'
        TRANSACTION_FEE = 'TRANSACTION_FEE', 'Transaction fee'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    transaction = models.ForeignKey(
        'payments.Transaction',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    revenue_type = models.CharField(
        max_length=30,
        choices=RevenueType.choices,
        default=RevenueType.SUBSCRIPTION,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='KES')
    description = models.CharField(max_length=255, null=True, blank=True)
    is_collected = models.BooleanField(
        default=True,
        help_text='Whether this revenue was actually collected in cash.',
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']

    def __str__(self):
        return f'{self.revenue_type} — {self.amount} {self.currency}'

class MonthlySaccoInvoice(models.Model):
    class Status(models.TextChoices):
        GENERATED = 'GENERATED', 'Generated'
        SENT = 'SENT', 'Sent'
        PAID = 'PAID', 'Paid'
        OVERDUE = 'OVERDUE', 'Overdue'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
        related_name='monthly_invoices',
    )
    period_start = models.DateField()
    period_end = models.DateField()
    amount_due = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    currency = models.CharField(max_length=3, default='KES')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.GENERATED,
    )
    report_payload = models.JSONField(default=dict)
    sent_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-period_end', 'sacco__name']
        unique_together = ['sacco', 'period_start', 'period_end']

    def __str__(self):
        return (
            f'Invoice {self.sacco.name} '
            f'({self.period_start} to {self.period_end})'
        )


class InvoiceLineItem(models.Model):
    """
    One record per successful transaction.

    Records what one SACCO owes SaccoSphere for one transaction.
    Accumulated during the month, aggregated into a monthly Invoice.
    APPEND-ONLY -- never update or delete rows.
    """

    TRANSACTION_TYPES = [
        ('deposit', 'Member Deposit'),
        ('repayment', 'Loan Repayment'),
        ('disbursement', 'Loan Disbursement'),
        ('withdrawal', 'Savings Withdrawal'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.PROTECT,
    )
    transaction = models.OneToOneField(
        'payments.Transaction',
        on_delete=models.PROTECT,
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES,
    )
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2)
    fee_model = models.CharField(max_length=30)
    rate_applied = models.CharField(max_length=80)
    tier_applied = models.CharField(
        max_length=120,
        null=True,
        blank=True,
    )
    billing_month = models.DateField()
    invoiced = models.BooleanField(default=False)
    invoice = models.ForeignKey(
        'Invoice',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'billing_invoice_line_items'
        indexes = [
            models.Index(fields=['sacco', 'billing_month', 'invoiced']),
            models.Index(fields=['transaction_type', 'billing_month']),
        ]

    def __str__(self):
        return (
            f'{self.transaction_type} | {self.sacco.name} | '
            f'KES {self.platform_fee} | {self.billing_month}'
        )


class Invoice(models.Model):
    STATUS = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('suspended', 'SACCO Suspended'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.PROTECT,
    )
    invoice_number = models.CharField(max_length=30, unique=True)
    billing_month = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    line_items_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default='draft',
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    pdf_path = models.CharField(max_length=500, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_invoices'
        unique_together = [['sacco', 'billing_month']]
        ordering = ['-billing_month']

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone

        return self.status == 'sent' and self.due_date < timezone.now().date()


class InvoicePayment(models.Model):
    METHODS = [
        ('mpesa', 'M-Pesa'),
        ('bank', 'Bank Transfer'),
        ('internal', 'Internal'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=METHODS)
    payment_ref = models.CharField(max_length=100)
    recorded_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
