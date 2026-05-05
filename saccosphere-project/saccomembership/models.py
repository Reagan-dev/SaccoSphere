from uuid import uuid4

from django.conf import settings
from django.db import models


class Membership(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under review'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        SUSPENDED = 'SUSPENDED', 'Suspended'
        LEFT = 'LEFT', 'Left'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
    )
    member_number = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    application_date = models.DateTimeField(auto_now_add=True)
    approved_date = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'sacco']

    def __str__(self):
        return f'{self.user.email} — {self.sacco.name} — {self.status}'


class SaccoFieldDefinition(models.Model):
    class FieldType(models.TextChoices):
        TEXT = 'TEXT', 'Text'
        NUMBER = 'NUMBER', 'Number'
        DATE = 'DATE', 'Date'
        SELECT = 'SELECT', 'Select'
        BOOLEAN = 'BOOLEAN', 'Boolean'
        FILE = 'FILE', 'File'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
    )
    label = models.CharField(max_length=100)
    field_type = models.CharField(
        max_length=20,
        choices=FieldType.choices,
    )
    is_required = models.BooleanField(default=True)
    options = models.JSONField(null=True, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['display_order']

    def __str__(self):
        return f'{self.sacco.name}: {self.label}'


class MemberFieldData(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    membership = models.ForeignKey(
        Membership,
        on_delete=models.CASCADE,
    )
    field = models.ForeignKey(
        SaccoFieldDefinition,
        on_delete=models.CASCADE,
    )
    value = models.TextField(null=True, blank=True)
    file_value = models.FileField(
        upload_to='member_docs/',
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = ['membership', 'field']

    def __str__(self):
        return f'{self.field.label}: {self.value}'


class SaccoApplication(models.Model):
    class ApplicationType(models.TextChoices):
        NEW_MEMBERSHIP = 'NEW_MEMBERSHIP', 'New membership'
        ADDITIONAL_SERVICE = 'ADDITIONAL_SERVICE', 'Additional service'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under review'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
    )
    application_type = models.CharField(
        max_length=30,
        choices=ApplicationType.choices,
        default=ApplicationType.NEW_MEMBERSHIP,
    )
    employment_status = models.CharField(
        max_length=50,
        null=True,
        blank=True,
    )
    employer_name = models.CharField(
        max_length=200,
        null=True,
        blank=True,
    )
    monthly_income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    additional_docs = models.JSONField(default=list)
    registration_fee_paid = models.BooleanField(default=False)
    fee_transaction = models.ForeignKey(
        'payments.Transaction',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_applications',
    )
    review_notes = models.TextField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.email} → {self.sacco.name} ({self.status})'


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# accounts/permissions.py
#
# IsKYCVerified is a DRF permission class. It allows a request only when the
# logged-in user has a related KYCVerification record and its status is
# APPROVED. This protects endpoints that should only be used after identity
# verification.
#
# IsPhoneVerified is also a DRF permission class. It allows a request only when
# the logged-in user has a phone_number saved. In this base version, having a
# phone number is treated as phone verification.
#
# accounts/urls.py
#
# app_name = 'accounts' gives these routes a namespace. That means other code
# can refer to routes like accounts:register or accounts:me.
#
# urlpatterns connects each URL path to its view. Register, login, logout,
# profile, password change, SACCO list, and SACCO detail use the completed
# accounts views. token/ and token/refresh/ use Simple JWT's built-in views.
#
# saccomembership/models.py
#
# Membership stores the relationship between one user and one SACCO. The
# unique_together rule prevents the same user from joining the same SACCO more
# than once.
#
# SaccoFieldDefinition lets each SACCO define extra application fields, such as
# employer name, payroll number, or an uploaded document. options is a JSON
# field so SELECT fields can store a list of allowed choices.
#
# MemberFieldData stores the answer for one custom field on one membership.
# The unique_together rule means a membership can answer each field only once.
#
# SaccoApplication stores a user's application before or during review. It
# tracks employment details, extra documents, fee payment status, review
# status, review notes, and timestamps.
#
# Django/Python concepts you might not know well
#
# A UUID primary key is a non-sequential unique ID. It is harder to guess than
# an auto-incrementing integer ID.
#
# TextChoices gives a model field a fixed set of valid values while still
# keeping the database value as normal text.
#
# ForeignKey connects one model to another. on_delete=models.CASCADE deletes
# child records when the parent is deleted. on_delete=models.SET_NULL keeps the
# child record but clears the link.
#
# JSONField stores structured data such as a list or dictionary. default=list
# is used instead of default=[] so every row gets its own separate list.
#
# Manual test to confirm it works
#
# Run makemigrations for saccomembership. Then create a user, a SACCO, and a
# Membership in Django shell. Try creating the same user/SACCO membership twice
# and confirm the unique_together rule blocks the duplicate.
#
# Important design decision
#
# User foreign keys use settings.AUTH_USER_MODEL instead of importing User
# directly. This is the recommended Django pattern because it keeps the app
# compatible with the custom accounts.User model.
#
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
