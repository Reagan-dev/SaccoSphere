from uuid import uuid4

from django.conf import settings
from django.db import models


class Transaction(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUCCESSFUL = 'SUCCESSFUL', 'Successful'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reference = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reference} - {self.status}'


class MpesaTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUCCESSFUL = 'SUCCESSFUL', 'Successful'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique M-Pesa transaction identifier.',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='User linked to this M-Pesa transaction.',
    )
    checkout_request_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text='M-Pesa checkout request identifier.',
    )
    mpesa_receipt_number = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text='M-Pesa receipt number.',
    )
    phone_number = models.CharField(
        max_length=13,
        null=True,
        blank=True,
        help_text='Phone number used for the M-Pesa transaction.',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='M-Pesa transaction amount.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text='Current M-Pesa transaction status.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this M-Pesa transaction was created.',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        reference = self.mpesa_receipt_number or self.checkout_request_id
        return f'{reference} - {self.status}'
