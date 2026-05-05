from uuid import uuid4

from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Category(models.TextChoices):
        LOAN = 'LOAN', 'Loan'
        PAYMENT = 'PAYMENT', 'Payment'
        ALERT = 'ALERT', 'Alert'
        DIVIDEND = 'DIVIDEND', 'Dividend'
        SYSTEM = 'SYSTEM', 'System'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.SYSTEM,
    )
    is_read = models.BooleanField(default=False, db_index=True)
    push_sent = models.BooleanField(default=False)
    action_url = models.CharField(max_length=500, null=True, blank=True)
    related_object_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
    )
    related_object_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read']),
        ]

    def __str__(self):
        read_status = 'read' if self.is_read else 'unread'
        return f'{self.user.email} — {self.title} — {read_status}'


class DeviceToken(models.Model):
    class Platform(models.TextChoices):
        ANDROID = 'ANDROID', 'Android'
        IOS = 'IOS', 'iOS'
        WEB = 'WEB', 'Web'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    token = models.TextField(unique=True)
    platform = models.CharField(max_length=20, choices=Platform.choices)
    is_active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-registered_at']

    def __str__(self):
        return f'{self.user.email} — {self.platform}'
