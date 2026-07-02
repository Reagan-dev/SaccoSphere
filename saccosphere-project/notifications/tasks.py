import logging

from celery import chain, shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail

from .models import DeviceToken


logger = logging.getLogger('saccosphere.notifications')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_sms_task(self, phone_number, message):
    from accounts.integrations.otp_service import ATSMSClient, ATSMSError

    try:
        ATSMSClient().send_sms(phone_number, message)
        logger.info('SMS notification sent to %s.', phone_number)
        return True
    except ATSMSError as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'SMS notification failed for %s. Retrying in %s seconds.',
            phone_number,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_task(self, to_email, subject, body, html_body=None):
    try:
        sent_count = send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            fail_silently=False,
            html_message=html_body,
        )
        logger.info('Email notification sent to %s.', to_email)
        return sent_count
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Email notification failed for %s. Retrying in %s seconds.',
            to_email,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_push_notification_task(self, user_id, title, body, data=None):
    from notifications.integrations.fcm_push import FCMPushClient, FCMError

    client = FCMPushClient()
    sent_count = 0
    tokens = DeviceToken.objects.filter(user_id=user_id, is_active=True)

    for device_token in tokens:
        try:
            client.send(device_token.token, title, body, data)
            sent_count += 1
            logger.info(
                'Push notification sent to device_token_id=%s.',
                device_token.id,
            )
        except FCMError as exc:
            if exc.invalid_registration:
                device_token.is_active = False
                device_token.save(update_fields=['is_active'])
                logger.info(
                    'Deactivated invalid device_token_id=%s.',
                    device_token.id,
                )
                continue

            countdown = 60 * 2 ** self.request.retries
            logger.warning(
                'Push notification failed for user_id=%s. '
                'Retrying in %s seconds.',
                user_id,
                countdown,
                exc_info=True,
            )
            raise self.retry(exc=exc, countdown=countdown)

    return sent_count


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def notify_user_task(
    self,
    user_id,
    title,
    message,
    category,
    action_url=None,
    send_sms=False,
    send_push=True,
    create_in_app=True,
):
    from .utils import create_notification

    User = get_user_model()

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning('Notification user_id=%s does not exist.', user_id)
        return None

    notification = None
    if create_in_app:
        notification = create_notification(
            user=user,
            title=title,
            message=message,
            category=category,
            action_url=action_url,
            dispatch_async=False,
        )

    task_signatures = []
    if send_push:
        task_signatures.append(
            send_push_notification_task.s(
                str(user_id),
                title,
                message,
                {'category': category, 'action_url': action_url or ''},
            )
        )

    if send_sms and user.phone_number:
        task_signatures.append(send_sms_task.s(user.phone_number, message))

    for task_signature in task_signatures:
        chain(task_signature).delay()

    return str(notification.id) if notification else None


