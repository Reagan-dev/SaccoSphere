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
from .engines.npl_monitor import (
    get_arrears_bucket,
    resolve_cleared_npl_flags,
)
from .models import Guarantor, LiquidityAlert, Loan, NPLFlag, RepaymentSchedule
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


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='services.tasks.flag_npl_arrears',
)
def flag_npl_arrears(self):
    """Create staged NPL flags for active loans in arrears."""
    try:
        loans = Loan.objects.filter(
            status=Loan.Status.ACTIVE,
        ).select_related(
            'membership__user',
            'membership__sacco',
        )
        checked_count = 0
        flags_created = 0
        flags_resolved = 0

        for loan in loans:
            checked_count += 1
            flags_resolved += resolve_cleared_npl_flags(loan)
            bucket = get_arrears_bucket(loan)

            if bucket is None:
                continue

            flag, created = NPLFlag.objects.get_or_create(
                loan=loan,
                threshold_days=bucket,
            )
            if not created:
                continue

            flags_created += 1
            _notify_npl_flag(loan, flag)

        logger.info(
            'NPL arrears check complete. checked=%s flags=%s resolved=%s.',
            checked_count,
            flags_created,
            flags_resolved,
        )
        return {
            'checked': checked_count,
            'flags_created': flags_created,
            'flags_resolved': flags_resolved,
        }
    except Exception as exc:
        logger.exception('NPL arrears check failed.')
        raise self.retry(exc=exc)


def _notify_npl_flag(loan, flag):
    member = loan.membership.user
    sacco = loan.membership.sacco
    member_name = member.get_full_name() or member.email
    days_overdue = _get_current_days_overdue(loan) or flag.threshold_days

    _notify_npl_admins(
        sacco=sacco,
        member_name=member_name,
        loan=loan,
        flag=flag,
        days_overdue=days_overdue,
    )
    _notify_npl_member(
        member=member,
        sacco=sacco,
        loan=loan,
        flag=flag,
        days_overdue=days_overdue,
    )


def _get_current_days_overdue(loan):
    earliest_unpaid = RepaymentSchedule.objects.filter(
        loan=loan,
        status__in=[
            RepaymentSchedule.Status.PENDING,
            RepaymentSchedule.Status.OVERDUE,
        ],
    ).order_by('due_date', 'instalment_number').first()

    if earliest_unpaid is None:
        return None

    return earliest_unpaid.days_overdue


def _notify_npl_admins(sacco, member_name, loan, flag, days_overdue):
    admin_roles = Role.objects.filter(
        name=Role.SACCO_ADMIN,
        sacco=sacco,
    ).select_related('user')
    notified_user_ids = set()
    loan_id = str(loan.id)
    title = f'NPL warning - {days_overdue} days'
    message = (
        f'{member_name} has loan {loan_id} at least '
        f'{days_overdue} days overdue. Please review the account and '
        f'follow your SACCO arrears process.'
    )

    for role in admin_roles:
        user = role.user
        if user.id in notified_user_ids:
            continue

        create_notification(
            user=user,
            title=title,
            message=message,
            category=Notification.Category.NPL_WARNING,
            action_url='/management/npl/',
            related_object_type='NPLFlag',
            related_object_id=str(flag.id),
            dispatch_async=False,
        )
        notified_user_ids.add(user.id)


def _notify_npl_member(member, sacco, loan, flag, days_overdue):
    title, message = _get_member_npl_message(
        sacco=sacco,
        loan=loan,
        days_overdue=days_overdue,
    )
    create_notification(
        user=member,
        title=title,
        message=message,
        category=Notification.Category.LOAN,
        action_url=f'/loans/{loan.id}/schedule/',
        related_object_type='NPLFlag',
        related_object_id=str(flag.id),
        dispatch_async=False,
    )

    if member.phone_number:
        send_sms_notification(member, message)


def _get_member_npl_message(sacco, loan, days_overdue):
    short_loan_id = str(loan.id)[:8]

    if days_overdue >= 90:
        return (
            'Loan significantly overdue',
            (
                f'Your {sacco.name} loan {short_loan_id} is now '
                f'significantly overdue. Please contact the SACCO to '
                f'discuss your repayment plan. This may affect your member '
                f'standing if it remains unresolved.'
            ),
        )

    if days_overdue >= 60:
        return (
            'Formal loan arrears notice',
            (
                f'Your {sacco.name} loan {short_loan_id} remains overdue '
                f'under the loan agreement. Please contact the SACCO as soon '
                f'as possible to agree on the next repayment steps.'
            ),
        )

    return (
        'Loan repayment falling behind',
        (
            f'Your {sacco.name} loan {short_loan_id} is falling behind. '
            f'Please make a repayment or contact the SACCO if you need help '
            f'with your repayment plan.'
        ),
    )


