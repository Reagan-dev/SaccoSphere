import logging

from celery import chain, shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from config.utils import emit_metric

from .models import DeviceToken


logger = logging.getLogger('saccosphere.notifications')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_sms_task(self, phone_number, message):
    from accounts.integrations.otp_service import ATSMSClient, ATSMSError

    try:
        ATSMSClient().send_sms(phone_number, message)
        logger.info('SMS notification sent to %s.', phone_number)
        emit_metric('sms_notification_sent')
        return True
    except ATSMSError as exc:
        if not exc.retryable:
            logger.error(
                'SMS notification for %s rejected by Africa\'s Talking '
                'and will not be retried: %s',
                phone_number,
                exc,
            )
            emit_metric(
                'sms_notification_failed',
                reason=type(exc).__name__,
                retryable=False,
            )
            return False

        if self.request.retries >= self.max_retries:
            logger.error(
                'SMS notification for %s exhausted retries.',
                phone_number,
                exc_info=True,
            )
            emit_metric(
                'sms_notification_failed',
                reason=type(exc).__name__,
                retryable=True,
                outcome='exhausted',
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'SMS notification failed for %s. Retrying in %s seconds.',
            phone_number,
            countdown,
            exc_info=True,
        )
        emit_metric(
            'sms_notification_failed',
            reason=type(exc).__name__,
            retryable=True,
            outcome='retrying',
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
        emit_metric('email_notification_sent')
        return sent_count
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                'Email notification for %s exhausted retries.',
                to_email,
                exc_info=True,
            )
            emit_metric(
                'email_notification_failed',
                reason=type(exc).__name__,
                outcome='exhausted',
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Email notification failed for %s. Retrying in %s seconds.',
            to_email,
            countdown,
            exc_info=True,
        )
        emit_metric(
            'email_notification_failed',
            reason=type(exc).__name__,
            outcome='retrying',
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
                emit_metric('push_notification_token_deactivated')
                continue

            if exc.is_permanent:
                logger.error(
                    'Push notification misconfigured (error_status=%s); '
                    'not retrying user_id=%s.',
                    exc.error_status,
                    user_id,
                    exc_info=True,
                )
                emit_metric(
                    'push_notification_failed',
                    reason=exc.error_status or 'unknown',
                    retryable=False,
                )
                raise

            if self.request.retries >= self.max_retries:
                logger.error(
                    'Push notification for user_id=%s exhausted retries.',
                    user_id,
                    exc_info=True,
                )
                emit_metric(
                    'push_notification_failed',
                    reason=exc.error_status or 'unknown',
                    retryable=True,
                    outcome='exhausted',
                )
                raise

            countdown = 60 * 2 ** self.request.retries
            logger.warning(
                'Push notification failed for user_id=%s. '
                'Retrying in %s seconds.',
                user_id,
                countdown,
                exc_info=True,
            )
            emit_metric(
                'push_notification_failed',
                reason=exc.error_status or 'unknown',
                retryable=True,
                outcome='retrying',
            )
            raise self.retry(exc=exc, countdown=countdown)

    emit_metric('push_notification_batch_sent', sent_count=sent_count)
    return sent_count


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def notify_user_task(
    self,
    user_id,
    title,
    message,
    category,
    action_url=None,
    action_label=None,
    secondary_url=None,
    secondary_label=None,
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
        push_data = {
            'category': category,
            'action_url': action_url or '',
        }
        if action_label:
            push_data['action_label'] = action_label
        if secondary_url:
            push_data['secondary_url'] = secondary_url
        if secondary_label:
            push_data['secondary_label'] = secondary_label

        task_signatures.append(
            send_push_notification_task.s(
                str(user_id),
                title,
                message,
                push_data,
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

    # The view sets QUEUED when the send request is accepted; flip to
    # SENDING now that a worker has actually picked this up. Idempotent on
    # a Celery retry, which re-enters here with the campaign already in
    # SENDING.
    campaign.status = SMSCampaign.Status.SENDING
    campaign.save(update_fields=['status', 'updated_at'])

    try:
        SaccoSettings.objects.get_or_create(sacco=campaign.sacco)
        allowed_recipients = claim_recipients_within_daily_limit(
            campaign,
            SMSCampaignRecipient,
        )

        if not allowed_recipients:
            update_campaign_counts(campaign, SMSCampaignRecipient)
            campaign.status = determine_campaign_status(campaign)
            campaign.save(update_fields=['status', 'updated_at'])
            return {
                'sent': campaign.sent_count,
                'failed': campaign.failed_count,
                'status': campaign.status,
            }

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
        campaign.status = determine_campaign_status(campaign)
        campaign.save(update_fields=['status', 'updated_at'])
        return {
            'sent': campaign.sent_count,
            'failed': campaign.failed_count,
            'status': campaign.status,
        }
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            update_campaign_counts(campaign, SMSCampaignRecipient)
            campaign.status = determine_campaign_status(campaign)
            campaign.save(update_fields=['status', 'updated_at'])
            logger.error(
                'Bulk SMS campaign_id=%s exhausted retries; marked %s.',
                campaign_id,
                campaign.status,
                exc_info=True,
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Bulk SMS campaign_id=%s failed. Retrying in %s seconds.',
            campaign_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


def claim_recipients_within_daily_limit(campaign, recipient_model):
    """
    Lock the SACCO's SaccoSettings row for a brief, DB-only accounting
    step so two campaigns for the same SACCO processed concurrently by
    different Celery workers can't both see the same "already claimed
    today" count and each believe they have the full daily allowance
    left - a check-then-act race that would let them jointly exceed
    SaccoSettings.sms_daily_limit.

    The allowance is tracked as a claim counter
    (SaccoSettings.sms_sent_today_count/_date) rather than by counting
    already-SENT recipient rows, because counting rows can't reflect an
    in-flight send another worker has claimed but not finished yet -
    only a claim made *inside this same lock* closes that window. Every
    claimed recipient is refunded (send_campaign_sms) if its actual send
    later fails, so the counter still only reflects real usage.

    The lock is released before any recipient is actually sent to
    Africa's Talking; only this fast count-and-claim step runs inside
    it. Recipients beyond the remaining allowance are immediately
    marked FAILED (daily limit reached). Returns the PENDING recipients
    within today's remaining allowance, ready to send.
    """
    from accounts.models import SaccoSettings

    with transaction.atomic():
        sacco_settings = SaccoSettings.objects.select_for_update().get(
            sacco=campaign.sacco,
        )

        today = timezone.localdate()
        if sacco_settings.sms_sent_today_date != today:
            sacco_settings.sms_sent_today_date = today
            sacco_settings.sms_sent_today_count = 0

        allowance = max(
            sacco_settings.sms_daily_limit
            - sacco_settings.sms_sent_today_count,
            0,
        )

        pending_recipients = campaign.recipients.filter(
            status=recipient_model.Status.PENDING,
        ).order_by('id')
        allowed_recipients = list(pending_recipients[:allowance])
        allowed_ids = [recipient.id for recipient in allowed_recipients]
        limit_recipients = pending_recipients.exclude(id__in=allowed_ids)
        mark_daily_limit_failures(limit_recipients)

        sacco_settings.sms_sent_today_count += len(allowed_recipients)
        sacco_settings.save(
            update_fields=['sms_sent_today_count', 'sms_sent_today_date'],
        )

    return allowed_recipients


def _refund_daily_sms_claim(sacco_id):
    """Give back one claimed slot after a send actually failed.

    A plain F()-expression UPDATE is enough here (no select_for_update
    needed) - it's a single-row arithmetic update, which Postgres already
    applies atomically against concurrent writers.
    """
    from accounts.models import SaccoSettings

    SaccoSettings.objects.filter(
        sacco_id=sacco_id,
        sms_sent_today_date=timezone.localdate(),
        sms_sent_today_count__gt=0,
    ).update(sms_sent_today_count=F('sms_sent_today_count') - 1)


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
        _refund_daily_sms_claim(campaign.sacco_id)
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
    campaign.save(
        update_fields=['sent_count', 'failed_count', 'updated_at'],
    )


def determine_campaign_status(campaign):
    """
    COMPLETED only if every recipient succeeded, FAILED only if none did,
    PARTIAL otherwise - a campaign is not COMPLETED just because at least
    one message went out.
    """
    from saccomanagement.models import SMSCampaign

    if campaign.sent_count == 0:
        return SMSCampaign.Status.FAILED
    if campaign.failed_count == 0:
        return SMSCampaign.Status.COMPLETED
    return SMSCampaign.Status.PARTIAL


def chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


@shared_task(name='notifications.tasks.purge_expired_notification_content')
def purge_expired_notification_content():
    """Clear Notification content past its retention period.

    Thin wrapper around the purge_expired_notification_content
    management command, mirroring
    services.tasks.purge_expired_crb_raw_response.
    """
    from io import StringIO

    from django.core.management import call_command

    output = StringIO()
    try:
        call_command('purge_expired_notification_content', stdout=output)
        result = output.getvalue()
        logger.info('Notification content purge completed: %s', result)
        return result
    except Exception as exc:
        logger.error('Notification content purge failed: %s', exc)
        raise
