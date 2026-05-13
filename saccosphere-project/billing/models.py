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
