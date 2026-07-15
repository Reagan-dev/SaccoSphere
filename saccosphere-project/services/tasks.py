"""Celery tasks for SACCO services (loans, guarantors, savings)."""

import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.models import Sacco
from notifications.models import Notification
from notifications.tasks import notify_user_task
from notifications.utils import create_notification
from saccomanagement.models import Role

from .engines.liquidity_monitor import check_liquidity_risk
from .models import Guarantor, LiquidityAlert, Loan
from .reminder_utils import send_sms_notification


logger = logging.getLogger('saccosphere.services')


@shared_task(name='services.tasks.notify_guarantors')
def notify_guarantors_task(loan_id):
    """
    Notify all pending guarantors about a loan guarantee request.

    Retrieves the loan with related guarantors and sends notification
    to each guarantor with PENDING status via the notification system.
    Includes SMS notification for high-priority guarantor requests.

    Args:
        loan_id (str): UUID of the Loan object.

    Returns:
        int: Number of guarantors notified.

    Raises:
        Loan.DoesNotExist: If the loan is not found.
    """
    loan = get_object_or_404(Loan, id=loan_id)
    loan = loan.refresh_from_db() or loan

    pending_guarantors = Guarantor.objects.filter(
        loan=loan,
        status=Guarantor.Status.PENDING,
    ).select_related('guarantor')

    count = 0
    for guarantor in pending_guarantors:
        try:
            applicant_name = (
                f'{loan.membership.user.first_name} '
                f'{loan.membership.user.last_name}'
            )
            action_url = (
                f'/loans/{loan.id}/guarantors/{guarantor.id}/respond'
            )

            title = f'Guarantor Request — {applicant_name}'
            message = (
                f'You have been requested to guarantee a loan of '
                f'KES {loan.amount:.2f} for {applicant_name}. '
                f'Please respond in the SaccoSphere app.'
            )

            notify_user_task.delay(
                user_id=str(guarantor.guarantor.id),
                title=title,
                message=message,
                category='LOAN',
                action_url=action_url,
                send_sms=True,
                send_push=True,
                create_in_app=True,
            )

            count += 1
            logger.info(
                'Guarantor notification queued for guarantor_id=%s, loan_id=%s.',
                guarantor.id,
                loan.id,
            )

        except Exception as exc:
            logger.error(
                'Failed to queue guarantor notification for guarantor_id=%s: %s',
                guarantor.id,
                exc,
                exc_info=True,
            )
            continue

    return count


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='services.tasks.check_all_sacco_liquidity',
)
def check_all_sacco_liquidity(self):
    """Check every active SACCO for loan-disbursement liquidity risk."""
    try:
        saccos = Sacco.objects.filter(is_active=True).select_related(
            'settings',
        )
        checked_count = 0
        alert_count = 0
        resolved_count = 0

        for sacco in saccos:
            risk = check_liquidity_risk(sacco)
            checked_count += 1

            if risk['at_risk']:
                alert = _create_liquidity_alert_if_needed(sacco, risk)
                if alert:
                    alert_count += 1
                    _notify_sacco_admins(sacco, risk, alert)
                continue

            resolved_count += _resolve_open_liquidity_alerts(sacco)

        logger.info(
            'Liquidity check complete. checked=%s alerts=%s resolved=%s.',
            checked_count,
            alert_count,
            resolved_count,
        )
        return {
            'checked': checked_count,
            'alerts_created': alert_count,
            'alerts_resolved': resolved_count,
        }
    except Exception as exc:
        logger.exception('Liquidity check failed.')
        raise self.retry(exc=exc)


def _create_liquidity_alert_if_needed(sacco, risk):
    recent_window_start = timezone.now() - timedelta(hours=24)
    recent_alert_exists = LiquidityAlert.objects.filter(
        sacco=sacco,
        resolved=False,
        created_at__gte=recent_window_start,
    ).exists()

    if recent_alert_exists:
        return None

    return LiquidityAlert.objects.create(
        sacco=sacco,
        available_reserves=risk['available_reserves'],
        pending_disbursements=risk['pending_disbursements'],
        utilisation_pct=risk['utilisation_pct'],
    )


def _notify_sacco_admins(sacco, risk, alert):
    admin_roles = Role.objects.filter(
        name=Role.SACCO_ADMIN,
        sacco=sacco,
    ).select_related('user').order_by('created_at')
    notified_user_ids = set()

    title = 'Liquidity warning'
    message = (
        f'{sacco.name} has KES {risk["pending_disbursements"]:,.2f} '
        f'in approved loans awaiting disbursement against KES '
        f'{risk["available_reserves"]:,.2f} in liquid reserves. '
        f'Utilisation is {risk["utilisation_pct"]}%.'
    )

    for role in admin_roles:
        user = role.user
        if user.id in notified_user_ids:
            continue

        create_notification(
            user=user,
            title=title,
            message=message,
            category=Notification.Category.LIQUIDITY_WARNING,
            action_url='/management/liquidity/',
            related_object_type='LiquidityAlert',
            related_object_id=str(alert.id),
            dispatch_async=False,
        )
        notified_user_ids.add(user.id)

    if risk['utilisation_pct'] >= Decimal('100.00'):
        primary_role = admin_roles.first()
        if primary_role and primary_role.user.phone_number:
            sms_message = (
                f'SaccoSphere: {sacco.name} cannot currently honour all '
                f'approved loans. Pending KES '
                f'{risk["pending_disbursements"]:,.2f}; reserves KES '
                f'{risk["available_reserves"]:,.2f}.'
            )
            send_sms_notification(primary_role.user, sms_message)


def _resolve_open_liquidity_alerts(sacco):
    return LiquidityAlert.objects.filter(
        sacco=sacco,
        resolved=False,
    ).update(
        resolved=True,
        resolved_at=timezone.now(),
    )


