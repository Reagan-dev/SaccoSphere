from django.db import models
from django.conf import settings
import uuid

# model that defines sacco services (savings, loans, and insuarance)
class service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    describtion = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class saving(models.Model):
    # savings deposits and withdrawals by a member without forgetting a member can have many savings records linked to a sacco service.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="savings")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    service = models.ForeignKey(
        service,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="savings"
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=[("deposit", "Deposit"), ("withdrawal", "Withdrawal")],
        default="deposit"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_type.title()} - {self.amount} by {self.member.username}"
    

class loan(models.Model):
    LOAN_STATUS = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("paid", "Paid"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loans"
    )
    service = models.ForeignKey(
        service,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loans"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Annual interest rate in %"
    )
    duration_months = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=LOAN_STATUS, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Loan of {self.amount} for {self.member.username} ({self.status})"


class insurance(models.Model):
        id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
        member = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            related_name="insurances"
        )
        service = models.ForeignKey(
            service,
            on_delete=models.SET_NULL,
            null=True,
            blank=True,
            related_name="insurances"
        )
        policy_number = models.CharField(max_length=50, unique=True)
        coverage_amount = models.DecimalField(max_digits=12, decimal_places=2)
        premium = models.DecimalField(max_digits=10, decimal_places=2)
        start_date = models.DateField()
        end_date = models.DateField()
        created_at = models.DateTimeField(auto_now_add=True)

        def __str__(self):
            return f"Insurance {self.policy_number} for {self.member.username}"
        

