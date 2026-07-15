import logging

from celery import chain, shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

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


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_bulk_sms_campaign_task(self, campaign_id):
    """Send a SACCO bulk SMS campaign through Africa's Talking."""
    from accounts.integrations.otp_service import ATSMSClient, ATSMSError
    from accounts.models import SaccoSettings
    from saccomanagement.models import (
        SMSCampaign,
        SMSCampaignRecipient,
    )

    try:
        campaign = SMSCampaign.objects.select_related('sacco').get(
            id=campaign_id,
        )
    except SMSCampaign.DoesNotExist:
        logger.warning('SMS campaign_id=%s does not exist.', campaign_id)
        return None

    try:
        settings_obj, _created = SaccoSettings.objects.get_or_create(
            sacco=campaign.sacco,
        )
        allowance = get_remaining_daily_sms_allowance(
            campaign,
            settings_obj.sms_daily_limit,
            SMSCampaignRecipient,
        )
        pending_recipients = campaign.recipients.filter(
            status=SMSCampaignRecipient.Status.PENDING,
        ).order_by('id')

        if allowance <= 0:
            mark_daily_limit_failures(pending_recipients)
            update_campaign_counts(campaign, SMSCampaignRecipient)
            campaign.status = SMSCampaign.Status.FAILED
            campaign.save(update_fields=[
                'status',
                'sent_count',
                'failed_count',
            ])
            return {
                'sent': campaign.sent_count,
                'failed': campaign.failed_count,
                'status': campaign.status,
            }

        allowed_recipients = list(pending_recipients[:allowance])
        allowed_ids = [recipient.id for recipient in allowed_recipients]
        limit_recipients = pending_recipients.exclude(id__in=allowed_ids)
        mark_daily_limit_failures(limit_recipients)

        client = ATSMSClient()
        for batch in chunked(allowed_recipients, 50):
            for recipient in batch:
                send_campaign_sms(
                    client,
                    campaign,
                    recipient,
                    ATSMSError,
                )
            update_campaign_counts(campaign, SMSCampaignRecipient)

        update_campaign_counts(campaign, SMSCampaignRecipient)
        campaign.status = (
            SMSCampaign.Status.COMPLETED
            if campaign.sent_count > 0
            else SMSCampaign.Status.FAILED
        )
        campaign.save(update_fields=[
            'status',
            'sent_count',
            'failed_count',
        ])
        return {
            'sent': campaign.sent_count,
            'failed': campaign.failed_count,
            'status': campaign.status,
        }
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Bulk SMS campaign_id=%s failed. Retrying in %s seconds.',
            campaign_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


def get_remaining_daily_sms_allowance(
    campaign,
    sms_daily_limit,
    recipient_model,
):
    sent_today = recipient_model.objects.filter(
        campaign__sacco=campaign.sacco,
        status=recipient_model.Status.SENT,
        sent_at__date=timezone.localdate(),
    ).count()
    return max(sms_daily_limit - sent_today, 0)


def mark_daily_limit_failures(recipients):
    recipients.update(
        status=recipients.model.Status.FAILED,
        error_message='daily SMS limit reached',
    )


def send_campaign_sms(client, campaign, recipient, sms_error_class):
    try:
        client.send_sms(recipient.phone_number, campaign.message)
    except sms_error_class as exc:
        recipient.status = recipient.Status.FAILED
        recipient.error_message = str(exc)[:255]
        recipient.save(update_fields=['status', 'error_message'])
        logger.warning(
            'Bulk SMS recipient_id=%s failed.',
            recipient.id,
            exc_info=True,
        )
        return False

    recipient.status = recipient.Status.SENT
    recipient.sent_at = timezone.now()
    recipient.error_message = ''
    recipient.save(update_fields=[
        'status',
        'sent_at',
        'error_message',
    ])
    return True


def update_campaign_counts(campaign, recipient_model):
    campaign.sent_count = campaign.recipients.filter(
        status=recipient_model.Status.SENT,
    ).count()
    campaign.failed_count = campaign.recipients.filter(
        status=recipient_model.Status.FAILED,
    ).count()
    campaign.save(update_fields=['sent_count', 'failed_count'])


def chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


