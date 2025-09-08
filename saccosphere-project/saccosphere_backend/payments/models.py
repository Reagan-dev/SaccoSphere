from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class PaymentProvider(models.Model):
    
    #Stores different payment providers (e.g., M-Pesa, Airtel Money, Bank).
    
    name = models.CharField(max_length=100, unique=True)
    provider_code = models.CharField(max_length=50, unique=True)  # e.g. "MPESA", "AIRTEL"
    api_key = models.CharField(max_length=255, blank=True, null=True)
    api_secret = models.CharField(max_length=255, blank=True, null=True)
    callback_url = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Transaction(models.Model):
    
    #Represents payment transactions from members/users.
    
    TRANSACTION_STATUS = [
        ("PENDING", "Pending"),
        ("SUCCESS", "Success"),
        ("FAILED", "Failed"),
        ("CANCELLED", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name="transactions"
    )
    provider = models.ForeignKey(PaymentProvider, on_delete=models.PROTECT, related_name="transactions")

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="KES")  
    status = models.CharField(max_length=10, choices=TRANSACTION_STATUS, default="PENDING")

    reference = models.CharField(max_length=100, unique=True) 
    provider_reference = models.CharField(max_length=100, blank=True, null=True)  
    description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.reference} - {self.status}"


class Callback(models.Model):
    
    #Stores raw callback data from payment providers.

    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="callbacks")
    provider = models.ForeignKey(PaymentProvider, on_delete=models.CASCADE, related_name="callbacks")

    payload = models.JSONField()  # callback response
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)

    def __str__(self):
        return f"Callback for {self.transaction.reference} at {self.received_at}"


