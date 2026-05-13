from uuid import uuid4

from django.conf import settings
from django.db import models


class SystemAuditLog(models.Model):
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
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=50)
    resource_id = models.CharField(max_length=100)
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        actor = self.user.email if self.user else 'System'
        return f'{actor} {self.action} {self.resource_type}'


class DataConsentLog(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='data_consent_logs',
    )
    accessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='data_access_logs',
    )
    data_type = models.CharField(max_length=100)
    reason = models.CharField(max_length=200)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return (
            f'{self.accessed_by.email} accessed {self.user.email} '
            f'{self.data_type}'
        )


class Role(models.Model):
    """
    Role-based access control model.
    
    A role represents a set of permissions a user has within a sacco context.
    Platform-wide roles have sacco=None.
    """

    MEMBER = 'MEMBER'
    SACCO_ADMIN = 'SACCO_ADMIN'
    SUPER_ADMIN = 'SUPER_ADMIN'

    ROLE_CHOICES = [
        (MEMBER, 'Member'),
        (SACCO_ADMIN, 'Sacco Admin'),
        (SUPER_ADMIN, 'Super Admin'),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='roles',
        help_text='The user who holds this role.',
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='admin_roles',
        help_text='The SACCO this role is scoped to. Null for platform-wide roles.',
    )
    name = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        help_text='The role name (MEMBER, SACCO_ADMIN, SUPER_ADMIN).',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'sacco', 'name']
        ordering = ['-created_at']

    def __str__(self):
        sacco_context = self.sacco.name if self.sacco else 'Platform'
        return f'{self.user.email} — {self.name} — {sacco_context}'


class RolePermission(models.Model):
    """
    Granular permissions tied to a role.
    
    Controls CRUD operations on specific resources (e.g., 'loans', 'members').
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name='permissions',
        help_text='The role this permission belongs to.',
    )
    resource = models.CharField(
        max_length=50,
        help_text='Resource name (e.g., loans, members, reports).',
    )
    can_create = models.BooleanField(
        default=False,
        help_text='Allow creation of this resource.',
    )
    can_read = models.BooleanField(
        default=True,
        help_text='Allow reading this resource.',
    )
    can_update = models.BooleanField(
        default=False,
        help_text='Allow updating this resource.',
    )
    can_delete = models.BooleanField(
        default=False,
        help_text='Allow deletion of this resource.',
    )

    class Meta:
        unique_together = ['role', 'resource']

    def __str__(self):
        return f'{self.role} — {self.resource}'

class ImportJob(models.Model):
    """Track asynchronous SACCO member bulk-import processing."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        PARTIAL = 'PARTIAL', 'Partial'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    sacco = models.ForeignKey(
        'accounts.Sacco',
        on_delete=models.CASCADE,
        related_name='import_jobs',
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='member_import_jobs',
    )
    file = models.FileField(upload_to='imports/')
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.PENDING,
    )
    total_rows = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    fail_count = models.PositiveIntegerField(default=0)
    error_summary = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'Import {str(self.id)[:8]} - '
            f'{self.sacco.name} - {self.status}'
        )
