"""Celery tasks for SACCO services (loans, guarantors, savings)."""

import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db import DatabaseError, InterfaceError, OperationalError, transaction
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
from .models import (
    DisbursementAuditLog,
    Guarantor,
    LiquidityAlert,
    Loan,
    NPLFlag,
    RepaymentSchedule,
)
from .reminder_utils import send_sms_notification


logger = logging.getLogger('saccosphere.services')


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='services.tasks.notify_guarantor',
)
def notify_guarantor_task(self, guarantor_id):
    """Queue notification delivery for one pending guarantor."""
    try:
        guarantor = Guarantor.objects.select_related(
            'guarantor',
            'loan',
            'loan__membership__user',
        ).get(id=guarantor_id, status=Guarantor.Status.PENDING)
    except Guarantor.DoesNotExist:
        logger.warning(
            'Pending guarantor notification skipped; guarantor_id=%s '
            'does not exist or is no longer pending.',
            guarantor_id,
        )
        return False

    loan = guarantor.loan
    applicant_name = (
        f'{loan.membership.user.first_name} '
        f'{loan.membership.user.last_name}'
    )
    action_url = f'/loans/{loan.id}/guarantors/{guarantor.id}/respond'

    title = f'Guarantor Request - {applicant_name}'
    message = (
        f'You have been requested to guarantee a loan of '
        f'KES {loan.amount:.2f} for {applicant_name}. '
        f'Please respond in the SaccoSphere app.'
    )

    try:
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
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Guarantor notification failed for guarantor_id=%s. '
            'Retrying in %s seconds.',
            guarantor.id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)

    logger.info(
        'Guarantor notification queued for guarantor_id=%s, loan_id=%s.',
        guarantor.id,
        loan.id,
    )
    return True


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='services.tasks.notify_guarantors',
)
def notify_guarantors_task(self, loan_id):
    """
    Notify all pending guarantors about a loan guarantee request.

    Retrieves the loan with related guarantors and sends notification
    to each guarantor with PENDING status via the notification system.
    Includes SMS notification for high-priority guarantor requests.

    Individual guarantor notification failures are isolated and logged
    without blocking other guarantors. The task itself retries only on
    transient errors (e.g., database connection issues).

    Args:
        loan_id (str): UUID of the Loan object.

    Returns:
        int: Number of guarantors notified.

    Raises:
        Loan.DoesNotExist: If the loan is not found.
    """
    TRANSIENT_ERRORS = (DatabaseError, InterfaceError, OperationalError)

    try:
        loan = get_object_or_404(Loan, id=loan_id)
        loan = loan.refresh_from_db() or loan

        pending_guarantors = Guarantor.objects.filter(
            loan=loan,
            status=Guarantor.Status.PENDING,
        ).select_related('guarantor')

        count = 0
        for guarantor in pending_guarantors:
            try:
                notify_guarantor_task.delay(str(guarantor.id))

                count += 1
                logger.info(
                    'Guarantor notification queued for guarantor_id=%s, '
                    'loan_id=%s.',
                    guarantor.id,
                    loan.id,
                )

            except Exception as exc:
                logger.error(
                    'Failed to queue guarantor notification for '
                    'guarantor_id=%s: %s',
                    guarantor.id,
                    exc,
                    exc_info=True,
                )
                continue

        return count
    except TRANSIENT_ERRORS as exc:
        if self.request.retries >= self.max_retries:
            logger.exception(
                'Guarantor notification task exhausted transient retries '
                'for loan_id=%s.',
                loan_id,
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Guarantor notification task hit transient DB/connection error '
            'for loan_id=%s. Retrying in %s seconds.',
            loan_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


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


def _record_disbursement_invoice_item(loan) -> None:
    """
    Create the SaccoSphere invoice line item after receipt is confirmed.

    This is deliberately not called at B2C initiation or at DISBURSED callback.
    """
    from billing.models import InvoiceLineItem
    from django.db import IntegrityError
    from payments.fee_calculator import SaccoInvoiceFeeCalculator

    tx = loan.disbursement_transaction
    if tx is None:
        raise ValueError('Loan has no disbursement transaction to invoice.')

    calc = SaccoInvoiceFeeCalculator()
    fee_breakdown = calc.calculate('disbursement', tx.gross_amount)
    today = timezone.now().date()
    billing_month = today.replace(day=1)

    try:
        InvoiceLineItem.objects.create(
            sacco=loan.membership.sacco,
            transaction=tx,
            transaction_type='disbursement',
            gross_amount=tx.gross_amount,
            net_amount=tx.amount,
            platform_fee=tx.platform_fee,
            fee_model=fee_breakdown['fee_model'],
            rate_applied=fee_breakdown['rate_applied'],
            tier_applied=fee_breakdown['tier_applied'],
            billing_month=billing_month,
            invoiced=False,
        )
    except IntegrityError:
        logger.info(
            'Disbursement invoice line already exists for transaction_id=%s.',
            tx.id,
        )


@shared_task(name='services.on_disbursement_b2c_callback')
def on_disbursement_b2c_callback(loan_id: str, mpesa_payload: dict):
    """Process a loan-disbursement B2C callback from M-Pesa."""
    from ledger.models import LedgerEntry
    from ledger.utils import create_ledger_entry
    from payments.models import Transaction

    result = mpesa_payload.get('Result') or mpesa_payload
    result_code = _normalize_mpesa_result_code(result.get('ResultCode'))
    transaction_id = (
        result.get('TransactionID')
        or _get_result_parameter_value(result, 'TransactionReceipt')
        or ''
    )

    if result_code == 0:
        with transaction.atomic():
            loan = (
                Loan.objects.select_for_update()
                .select_related('membership', 'membership__sacco', 'membership__user')
                .get(id=loan_id)
            )
            tx = loan.disbursement_transaction
            if tx is None:
                raise ValueError('Loan has no disbursement transaction.')

            if loan.disbursement_status in [
                Loan.DisbursementStatus.DISBURSED,
                Loan.DisbursementStatus.MEMBER_CONFIRMED,
                Loan.DisbursementStatus.AUTO_CONFIRMED,
            ]:
                return True

            loan.mpesa_transaction_id = transaction_id
            loan.disbursement_status = Loan.DisbursementStatus.DISBURSED
            loan.disbursement_confirmed_at = timezone.now()
            loan.status = Loan.Status.ACTIVE
            loan.disbursed_amount = tx.amount
            loan.disbursement_date = timezone.localdate()
            loan.outstanding_balance = tx.gross_amount
            loan.save(
                update_fields=[
                    'mpesa_transaction_id',
                    'disbursement_status',
                    'disbursement_confirmed_at',
                    'status',
                    'disbursed_amount',
                    'disbursement_date',
                    'outstanding_balance',
                    'updated_at',
                ],
            )

            tx.status = Transaction.Status.COMPLETED
            tx.external_reference = (
                transaction_id or loan.mpesa_conversation_id
            )
            tx.save(
                update_fields=[
                    'status',
                    'external_reference',
                    'updated_at',
                ],
            )

            DisbursementAuditLog.objects.create(
                loan=loan,
                event='B2C_CALLBACK_RECEIVED',
                actor=None,
                actor_role='system',
                mpesa_ref=transaction_id,
                details={'mpesa_payload': mpesa_payload},
            )

            create_ledger_entry(
                membership=loan.membership,
                entry_type=LedgerEntry.EntryType.DEBIT,
                category=LedgerEntry.Category.LOAN_DISBURSEMENT,
                amount=tx.gross_amount,
                reference=f'{tx.reference}-LEDGER',
                description=(
                    f'Loan disbursed. Received: KES {tx.amount:,.2f}. '
                    f'Disbursement fee: KES {tx.platform_fee:,.2f}.'
                ),
                transaction=tx,
            )

        send_disbursement_confirmation_request.delay(str(loan_id))
        return True

    with transaction.atomic():
        loan = Loan.objects.select_for_update().get(id=loan_id)
        loan.disbursement_status = Loan.DisbursementStatus.FAILED
        loan.status = Loan.Status.APPROVED
        loan.save(
            update_fields=['disbursement_status', 'status', 'updated_at'],
        )

        if loan.disbursement_transaction_id:
            tx = loan.disbursement_transaction
            tx.status = Transaction.Status.FAILED
            tx.save(update_fields=['status', 'updated_at'])

        DisbursementAuditLog.objects.create(
            loan=loan,
            event='DISBURSEMENT_FAILED',
            actor=None,
            actor_role='system',
            details={
                'result_code': result_code,
                'result_desc': result.get('ResultDesc', ''),
            },
        )

    notify_user_task.delay(
        str(loan.membership.user_id),
        'Loan Disbursement Failed',
        'Your loan disbursement failed. Please contact your SACCO.',
        Notification.Category.LOAN,
    )
    return False


@shared_task(name='services.send_disbursement_confirmation_request')
def send_disbursement_confirmation_request(loan_id: str):
    """Ask the member to confirm the net amount received via M-Pesa."""
    from django.conf import settings
    from django.core.signing import TimestampSigner
    from notifications.tasks import send_sms_task

    loan = (
        Loan.objects.select_related('membership', 'membership__sacco', 'membership__user')
        .get(id=loan_id)
    )
    tx = loan.disbursement_transaction
    if tx is None:
        raise ValueError('Loan has no disbursement transaction.')

    signer = TimestampSigner()
    token = signer.sign(str(loan.id))
    frontend_base_url = getattr(
        settings,
        'FRONTEND_BASE_URL',
        'http://localhost:3000',
    ).rstrip('/')
    confirm_url = (
        f'{frontend_base_url}/confirm-disbursement/?token={token}'
    )
    dispute_url = (
        f'{frontend_base_url}/dispute-disbursement/?token={token}'
    )

    member = loan.membership.user
    sacco = loan.membership.sacco
    sms_message = (
        f'SaccoSphere: KES {tx.amount:,.0f} has been disbursed to your '
        f'M-Pesa from {sacco.name}. Did you receive it? Reply YES: '
        f'{confirm_url} or NO: {dispute_url} '
        f'(Link expires in 24 hours)'
    )
    send_sms_task.delay(member.phone_number, sms_message)

    notify_user_task.delay(
        str(member.id),
        'Confirm Loan Receipt',
        f'KES {tx.amount:,.0f} sent to your M-Pesa. Did you receive it?',
        Notification.Category.LOAN,
        action_url=confirm_url,
        action_label='Yes, I received it',
        secondary_url=dispute_url,
        secondary_label='No, I did not receive it',
    )

    loan.member_confirmation_sent_at = timezone.now()
    loan.save(update_fields=['member_confirmation_sent_at', 'updated_at'])

    DisbursementAuditLog.objects.create(
        loan=loan,
        event='MEMBER_NOTIFIED',
        actor=None,
        actor_role='system',
        details={
            'phone': member.phone_number,
            'net_amount_notified': str(tx.amount),
            'token_expiry': '24 hours',
        },
    )

    auto_resolve_disbursement.apply_async(
        args=[str(loan.id)],
        countdown=86400,
    )


@shared_task(name='services.auto_resolve_disbursement')
def auto_resolve_disbursement(loan_id: str):
    """Auto-check M-Pesa after 24 hours if the member has not responded."""
    from payments.integrations.mpesa.daraja import DarajaClient

    loan = (
        Loan.objects.select_related('membership', 'membership__sacco')
        .get(id=loan_id)
    )

    if loan.disbursement_status in [
        Loan.DisbursementStatus.MEMBER_CONFIRMED,
        Loan.DisbursementStatus.AUTO_CONFIRMED,
        Loan.DisbursementStatus.DISPUTED,
    ]:
        return

    # Use SACCO-specific Daraja credentials directly (no generic PSP)
    sacco = loan.membership.sacco
    if not sacco.payment_ready:
        logger.warning(
            'SACCO %s is not payment-ready, cannot auto-resolve disbursement %s',
            sacco.id,
            loan.id,
        )
        return

    try:
        payment_config = sacco.payment_config
        if not payment_config.is_active or not payment_config.has_b2c_config():
            logger.warning(
                'SACCO %s has no active B2C config, cannot auto-resolve disbursement %s',
                sacco.id,
                loan.id,
            )
            return
    except AttributeError:
        logger.warning(
            'SACCO %s has no payment config, cannot auto-resolve disbursement %s',
            sacco.id,
            loan.id,
        )
        return

    # Note: Daraja API does not support B2C status queries
    # We cannot verify delivery via API - escalate to review
    logger.info(
        'M-Pesa B2C status query not supported by Daraja API. '
        'Escalating disbursement %s to review.',
        loan.id,
    )

    with transaction.atomic():
        loan = Loan.objects.select_for_update().get(id=loan_id)
        if loan.disbursement_status in [
            Loan.DisbursementStatus.MEMBER_CONFIRMED,
            Loan.DisbursementStatus.AUTO_CONFIRMED,
            Loan.DisbursementStatus.DISPUTED,
        ]:
            return

        loan.disbursement_status = Loan.DisbursementStatus.UNDER_REVIEW
        loan.save(update_fields=['disbursement_status', 'updated_at'])

        DisbursementAuditLog.objects.create(
            loan=loan,
            event='ESCALATED_TO_SUPERADMIN',
            actor=None,
            actor_role='system',
            details={
                'reason': '24hr timeout, M-Pesa B2C status query not supported',
            },
        )

        _notify_superadmins(
            'Disbursement Auto-Escalation',
            f'Loan {loan.id} for {loan.membership.sacco.name} could not be auto-confirmed via M-Pesa status query (not supported by Daraja API).',
            related_loan_id=str(loan.id),
        )


def _normalize_mpesa_result_code(result_code):
    try:
        return int(result_code)
    except (TypeError, ValueError):
        return result_code


def _get_result_parameter_value(result, key):
    parameters = (
        result.get('ResultParameters', {})
        .get('ResultParameter', [])
    )
    for parameter in parameters:
        if parameter.get('Key') == key:
            return parameter.get('Value')
    return None


def _notify_superadmins(title, message, related_loan_id=None):
    from notifications.tasks import send_email_task

    superadmin_roles = Role.objects.select_related('user').filter(
        name=Role.SUPER_ADMIN,
        sacco__isnull=True,
    )
    for role in superadmin_roles:
        notify_user_task.delay(
            str(role.user.id),
            title,
            message,
            Notification.Category.LOAN,
            action_url='/management/disbursement-disputes/',
        )
        if role.user.email:
            send_email_task.delay(role.user.email, title, message)


def _notify_sacco_admins(sacco, title, message):
    admin_roles = Role.objects.select_related('user').filter(
        name=Role.SACCO_ADMIN,
        sacco=sacco,
    )
    for role in admin_roles:
        notify_user_task.delay(
            str(role.user.id),
            title,
            message,
            Notification.Category.LOAN,
            action_url='/management/disbursements/',
        )


