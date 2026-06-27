import logging

from django.db import IntegrityError
from django.utils import timezone

from notifications.models import Notification
from notifications.utils import create_notification

from .models import ReminderLog, RepaymentSchedule


logger = logging.getLogger('saccosphere.reminders')


def get_upcoming_instalments(days_ahead=3):
    """Return pending instalments due exactly the requested days ahead."""
    due_date = timezone.localdate() + timezone.timedelta(days=days_ahead)
    return RepaymentSchedule.objects.filter(
        status=RepaymentSchedule.Status.PENDING,
        due_date=due_date,
    ).select_related(
        'loan',
        'loan__membership',
        'loan__membership__user',
        'loan__membership__sacco',
    )


def send_sms_notification(user, message):
    """Queue an SMS notification for the user."""
    try:
        from notifications.tasks import send_sms_task

        send_sms_task.delay(user.phone_number, message)
    except Exception:
        logger.exception(
            'Failed to queue repayment reminder SMS for user_id=%s.',
            user.id,
        )
        return False

    return True


def send_repayment_reminder(schedule_item):
    """Send one upcoming repayment reminder if it has not been sent."""
    days = (schedule_item.due_date - timezone.localdate()).days
    reminder_type = _get_upcoming_reminder_type(days)

    if _reminder_exists(schedule_item, reminder_type):
        logger.info(
            'Skipping duplicate %s reminder for schedule_item_id=%s.',
            reminder_type,
            schedule_item.id,
        )
        return False

    user = _get_schedule_user(schedule_item)
    sacco = _get_schedule_sacco(schedule_item)
    amount = _get_schedule_amount(schedule_item)
    due_date = schedule_item.due_date
    loan_id = schedule_item.loan.id
    message = (
        f'Your loan repayment of KES {amount:,.0f} for '
        f'{sacco.name} is due on {due_date.strftime("%d %B %Y")}. '
        f'Your reference: LOAN-{loan_id}'
    )
    notification = create_notification(
        user=user,
        title=f'Loan Repayment Due in {days} Days',
        message=message,
        category=Notification.Category.LOAN,
        action_url=f'/loans/{loan_id}/schedule/',
        related_object_type='RepaymentSchedule',
        related_object_id=str(schedule_item.id),
        dispatch_async=False,
    )
    sms_sent = False

    if user.phone_number:
        sms_message = (
            f'SaccoSphere: Loan payment of KES {amount:,.0f} '
            f'due {due_date.strftime("%d %b %Y")}. '
            f'Log in to pay via M-Pesa. Ref: LOAN-{loan_id}'
        )
        sms_sent = send_sms_notification(user, sms_message)

    notification_created = notification is not None
    if not _create_reminder_log(
        schedule_item,
        reminder_type,
        notification_created,
        sms_sent,
    ):
        return False

    logger.info(
        'Sent %s repayment reminder for schedule_item_id=%s.',
        reminder_type,
        schedule_item.id,
    )
    return notification_created


def send_overdue_alert(schedule_item):
    """Send one overdue alert if it has not been sent."""
    reminder_type = ReminderLog.ReminderType.OVERDUE

    if _reminder_exists(schedule_item, reminder_type):
        logger.info(
            'Skipping duplicate overdue alert for schedule_item_id=%s.',
            schedule_item.id,
        )
        return False

    user = _get_schedule_user(schedule_item)
    amount = _get_schedule_amount(schedule_item)
    due_date = schedule_item.due_date
    loan_id = schedule_item.loan.id
    penalty_amount = schedule_item.penalty_amount
    message = (
        f'Your loan repayment of KES {amount:,.0f} was due on '
        f'{due_date.strftime("%d %B %Y")}. A penalty of KES '
        f'{penalty_amount:,.0f} has been applied. Please pay immediately '
        'to avoid further charges.'
    )
    notification = create_notification(
        user=user,
        title='Loan Repayment Overdue',
        message=message,
        category=Notification.Category.ALERT,
        action_url=f'/loans/{loan_id}/schedule/',
        related_object_type='RepaymentSchedule',
        related_object_id=str(schedule_item.id),
        dispatch_async=False,
    )
    sms_sent = False

    if user.phone_number:
        sms_message = (
            f'SaccoSphere: Loan payment of KES {amount:,.0f} was due '
            f'{due_date.strftime("%d %b %Y")}. Penalty KES '
            f'{penalty_amount:,.0f} applied. Ref: LOAN-{loan_id}'
        )
        sms_sent = send_sms_notification(user, sms_message)

    notification_created = notification is not None
    if not _create_reminder_log(
        schedule_item,
        reminder_type,
        notification_created,
        sms_sent,
    ):
        return False

    logger.info(
        'Sent overdue alert for schedule_item_id=%s.',
        schedule_item.id,
    )
    return notification_created


def _get_upcoming_reminder_type(days):
    if days == 1:
        return ReminderLog.ReminderType.ONE_DAY
    return ReminderLog.ReminderType.THREE_DAY


def _reminder_exists(schedule_item, reminder_type):
    return ReminderLog.objects.filter(
        schedule_item=schedule_item,
        reminder_type=reminder_type,
    ).exists()


def _create_reminder_log(
    schedule_item,
    reminder_type,
    notification_created,
    sms_sent,
):
    try:
        ReminderLog.objects.create(
            schedule_item=schedule_item,
            reminder_type=reminder_type,
            notification_created=notification_created,
            sms_sent=sms_sent,
        )
    except IntegrityError:
        logger.info(
            'Skipping duplicate %s reminder for schedule_item_id=%s.',
            reminder_type,
            schedule_item.id,
        )
        return False

    return True


def _get_schedule_user(schedule_item):
    if hasattr(schedule_item.loan, 'user'):
        return schedule_item.loan.user
    return schedule_item.loan.membership.user


def _get_schedule_sacco(schedule_item):
    if hasattr(schedule_item.loan, 'sacco'):
        return schedule_item.loan.sacco
    return schedule_item.loan.membership.sacco


def _get_schedule_amount(schedule_item):
    return getattr(schedule_item, 'total_amount', schedule_item.amount)
