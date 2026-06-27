from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from .utils import generate_response_token


def default_response_token_expires_at():
    return timezone.now() + timedelta(hours=48)


class ExternalGuarantor(models.Model):
    class EmploymentStatus(models.TextChoices):
        EMPLOYED = 'EMPLOYED', 'Employed'
        SELF_EMPLOYED = 'SELF_EMPLOYED', 'Self employed'
        BUSINESS_OWNER = 'BUSINESS_OWNER', 'Business owner'
        RETIRED = 'RETIRED', 'Retired'
        OTHER = 'OTHER', 'Other'

    class Status(models.TextChoices):
        PENDING_SMS = 'PENDING_SMS', 'Pending SMS'
        SMS_SENT = 'SMS_SENT', 'SMS sent'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        DECLINED = 'DECLINED', 'Declined'
        UNDER_ADMIN_REVIEW = 'UNDER_ADMIN_REVIEW', 'Under admin review'
        APPROVED_BY_ADMIN = 'APPROVED_BY_ADMIN', 'Approved by admin'
        REJECTED_BY_ADMIN = 'REJECTED_BY_ADMIN', 'Rejected by admin'

    class GuarantorResponse(models.TextChoices):
        ACCEPTED = 'ACCEPTED', 'Accepted'
        DECLINED = 'DECLINED', 'Declined'

    loan = models.ForeignKey(
        'services.Loan',
        on_delete=models.CASCADE,
        related_name='external_guarantors',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='external_guarantor_requests',
    )
    sacco = models.ForeignKey('accounts.Sacco', on_delete=models.CASCADE)
    full_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20)
    id_number = models.CharField(max_length=20)
    employment_status = models.CharField(
        max_length=20,
        choices=EmploymentStatus.choices,
    )
    monthly_income = models.DecimalField(max_digits=10, decimal_places=2)
    guarantee_amount = models.DecimalField(max_digits=10, decimal_places=2)
    id_front = models.ImageField(
        upload_to='external_guarantors/id_front/',
        null=True,
        blank=True,
    )
    id_back = models.ImageField(
        upload_to='external_guarantors/id_back/',
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING_SMS,
    )
    response_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_response_token,
    )
    response_token_expires_at = models.DateTimeField(
        default=default_response_token_expires_at,
    )
    guarantor_response = models.CharField(
        max_length=20,
        choices=GuarantorResponse.choices,
        null=True,
        blank=True,
    )
    guarantor_response_notes = models.TextField(null=True, blank=True)
    guarantor_responded_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='external_guarantor_reviews',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.full_name} for loan {self.loan.id} - {self.status}'
