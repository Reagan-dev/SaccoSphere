"""Notification helper functions."""

from .models import Notification


def create_notification(
    user,
    title,
    message,
    category=Notification.Category.SYSTEM,
    **extra_fields,
):
    """Create an in-app notification for a user."""
    return Notification.objects.create(
        user=user,
        title=title,
        message=message,
        category=category,
        **extra_fields,
    )
