"""Celery tasks for the external-guarantor workflow."""

import logging

from celery import shared_task
from django.utils import timezone

from notifications.models import Notification
from notifications.utils import create_notification

from .models import ExternalGuarantor
from .utils import build_guarantor_sms_message


logger = logging.getLogger('saccosphere.guarantor')


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='guarantor.tasks.send_external_guarantor_sms',
)
def send_external_guarantor_sms_task(self, external_guarantor_id):
    """Send the request SMS to an external guarantor, with retry.

    Mirrors services.tasks.notify_guarantor_task: on a transient SMS
    gateway failure it retries with an exponential back-off instead of
    failing the caller's request cycle.
    """
    from accounts.integrations.otp_service import ATSMSClient, ATSMSError

    try:
        external_guarantor = ExternalGuarantor.objects.select_related(
            'requested_by',
            'sacco',
        ).get(
            id=external_guarantor_id,
            status=ExternalGuarantor.Status.PENDING_SMS,
        )
    except ExternalGuarantor.DoesNotExist:
        logger.warning(
            'External guarantor SMS skipped; external_guarantor_id=%s '
            'does not exist or is no longer pending SMS.',
            external_guarantor_id,
        )
        return False

    message = build_guarantor_sms_message(external_guarantor)

    try:
        ATSMSClient().send_sms(external_guarantor.phone_number, message)
    except ATSMSError as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'External guarantor SMS failed for external_guarantor_id=%s. '
            'Retrying in %s seconds.',
            external_guarantor_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)

    external_guarantor.status = ExternalGuarantor.Status.SMS_SENT
    external_guarantor.save(update_fields=['status', 'updated_at'])
    create_notification(
        user=external_guarantor.requested_by,
        title='Guarantor SMS Sent',
        message=(
            f'{external_guarantor.full_name} has been sent an SMS to '
            'approve your guarantee request.'
        ),
        category=Notification.Category.GUARANTOR,
        related_object_type='ExternalGuarantor',
        related_object_id=str(external_guarantor.id),
        dispatch_async=False,
    )
    logger.info(
        'External guarantor SMS sent for external_guarantor_id=%s.',
        external_guarantor_id,
    )
    return True


@shared_task(name='guarantor.tasks.expire_stale_external_guarantors')
def expire_stale_external_guarantors_task():
    """Expire external-guarantor requests past their response window.

    An external guarantor who never responds otherwise blocks the loan
    forever, because check_loan_guarantors_complete treats PENDING_SMS /
    SMS_SENT as "pending admin review". Flipping them to EXPIRED drops
    them out of that gate (same effect as a DECLINE) and the applicant is
    notified to add another guarantor - mirroring
    GuarantorRespondView._notify_applicant_guarantor_declined for the
    internal path.

    Returns the number of requests expired.
    """
    now = timezone.now()
    stale = ExternalGuarantor.objects.select_related(
        'loan',
        'loan__membership',
        'loan__membership__user',
        'requested_by',
    ).filter(
        status__in=[
            ExternalGuarantor.Status.PENDING_SMS,
            ExternalGuarantor.Status.SMS_SENT,
        ],
        response_token_expires_at__lte=now,
    )

    expired_count = 0
    for external_guarantor in stale:
        external_guarantor.status = ExternalGuarantor.Status.EXPIRED
        external_guarantor.save(update_fields=['status', 'updated_at'])

        create_notification(
            user=external_guarantor.requested_by,
            title='Guarantor Request Expired',
            message=(
                f'{external_guarantor.full_name} did not respond to your '
                'guarantee request within 48 hours. Please request another '
                'guarantor for your loan.'
            ),
            category=Notification.Category.GUARANTOR,
            action_url=f'/loans/{external_guarantor.loan_id}/',
            related_object_type='ExternalGuarantor',
            related_object_id=str(external_guarantor.id),
            dispatch_async=False,
        )
        expired_count += 1
        logger.info(
            'External guarantor request expired: external_guarantor_id=%s '
            'loan_id=%s.',
            external_guarantor.id,
            external_guarantor.loan_id,
        )

    logger.info(
        'Expired %d stale external guarantor request(s).',
        expired_count,
    )
    return expired_count
