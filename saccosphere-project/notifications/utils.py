"""Notification helper functions."""

import logging

from .models import Notification


logger = logging.getLogger('saccosphere.notifications')


def create_notification(
    user,
    title,
    message,
    category=Notification.Category.SYSTEM,
    action_url=None,
    related_object_type=None,
    related_object_id=None,
    dispatch_async=True,
):
    """Create an in-app notification without crashing calling code."""
    try:
        notification = Notification.objects.create(
            user=user,
            title=title,
            message=message,
            category=category,
            action_url=action_url,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
        )
    except Exception:
        logger.exception(
            'Failed to create notification for user_id=%s.',
            getattr(user, 'id', None),
        )
        return None

    if dispatch_async and category in {
        Notification.Category.PAYMENT,
        Notification.Category.LOAN,
    }:
        try:
            from celery import chain

            from .tasks import notify_user_task

            chain(
                notify_user_task.s(
                    str(user.id),
                    title,
                    message,
                    category,
                    action_url=action_url,
                    create_in_app=False,
                )
            ).delay()
        except Exception:
            logger.exception(
                'Failed to dispatch notification delivery for user_id=%s.',
                getattr(user, 'id', None),
            )

    return notification
