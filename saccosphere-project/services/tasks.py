"""Celery tasks for SACCO services (loans, guarantors, savings)."""

import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db import DatabaseError, InterfaceError, OperationalError, transaction
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.models import Sacco
from notifications.models import Notification
from notifications.tasks import notify_user_task
from notifications.utils import create_notification
from saccomanagement.models import Role

from .engines.liquidity_monitor import check_liquidity_risk
from .engines.npl_monitor import arrears_buckets_for_monitored_loans
from .engines.penalties import compute_penalty
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
        is_active=True,
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


# Chunk size for iterating the delinquency working set. The sweep only
# ever loads loans that are in arrears now or were flagged/defaulted
# before, so this caps peak memory rather than total work.
NPL_SWEEP_CHUNK_SIZE = 500

# The 90-day bucket is the SASRA non-performing line; only that level is
# escalated to a platform-visible ComplianceFlag.
NPL_COMPLIANCE_BUCKET = 90


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='services.tasks.flag_npl_arrears',
)
def flag_npl_arrears(self):
    """Stage NPL flags, drive ACTIVE<->DEFAULTED, surface severe arrears.

    Scales with delinquency, not portfolio size: arrears for the whole
    loan book are computed in a single aggregate query, and only loans
    that are delinquent now OR were previously flagged/defaulted are
    iterated - a healthy loan is never loaded. Writes a JobHeartbeat on
    every run so ``/health/jobs/`` can alert if the sweep stops running.
    """
    from health.models import JobHeartbeat

    try:
        result = _run_npl_arrears_sweep()
        logger.info(
            'NPL arrears check complete. checked=%s flags=%s resolved=%s '
            'defaulted=%s recovered=%s compliance_flags=%s.',
            result['checked'],
            result['flags_created'],
            result['flags_resolved'],
            result['loans_defaulted'],
            result['loans_recovered'],
            result['compliance_flags'],
        )
        JobHeartbeat.record('flag_npl_arrears', detail=result)
        return result
    except Exception as exc:
        logger.exception('NPL arrears check failed.')
        try:
            JobHeartbeat.record(
                'flag_npl_arrears',
                status=JobHeartbeat.Status.ERROR,
                detail={'error': str(exc)},
            )
        except Exception:
            logger.exception('Could not record NPL sweep failure heartbeat.')
        raise self.retry(exc=exc)


def _run_npl_arrears_sweep():
    """Do the actual NPL sweep. Query count is fixed + O(working set)."""
    now = timezone.now()

    # 1 aggregate query: loan_id -> arrears bucket, delinquent loans only.
    buckets = arrears_buckets_for_monitored_loans()

    # Loans already carrying an unresolved flag, and loans sitting in
    # DEFAULTED - both must be revisited so they can clear / recover even
    # if they have no past-due instalment this run. 2 id-only queries.
    unresolved_flag_loan_ids = set(
        NPLFlag.objects.filter(resolved=False).values_list(
            'loan_id', flat=True,
        )
    )
    defaulted_loan_ids = set(
        Loan.objects.filter(
            status=Loan.Status.DEFAULTED,
        ).values_list('id', flat=True)
    )

    working_ids = set(buckets) | unresolved_flag_loan_ids | defaulted_loan_ids

    # 1 query: every (loan, threshold) pair already on record for the
    # working set - resolved or not - so we never violate NPLFlag's
    # unique_together by re-creating one (mirrors the old get_or_create).
    existing_thresholds = {}
    for loan_id, threshold in NPLFlag.objects.filter(
        loan_id__in=working_ids,
    ).values_list('loan_id', 'threshold_days'):
        existing_thresholds.setdefault(loan_id, set()).add(threshold)

    checked = 0
    flags_created = 0
    loans_defaulted = 0
    loans_recovered = 0
    cleared_flag_loan_ids = []

    loans = Loan.objects.filter(id__in=working_ids).select_related(
        'membership__user',
        'membership__sacco',
    )
    for loan in loans.iterator(chunk_size=NPL_SWEEP_CHUNK_SIZE):
        checked += 1
        bucket = buckets.get(loan.id)

        transition = _apply_default_status_transition(loan, bucket)
        if transition == 'defaulted':
            loans_defaulted += 1
        elif transition == 'recovered':
            loans_recovered += 1

        if bucket is None:
            if loan.id in unresolved_flag_loan_ids:
                cleared_flag_loan_ids.append(loan.id)
            continue

        if bucket in existing_thresholds.get(loan.id, set()):
            continue

        flag = NPLFlag.objects.create(loan=loan, threshold_days=bucket)
        existing_thresholds.setdefault(loan.id, set()).add(bucket)
        flags_created += 1
        _notify_npl_flag(loan, flag)

    flags_resolved = 0
    if cleared_flag_loan_ids:
        flags_resolved = NPLFlag.objects.filter(
            loan_id__in=cleared_flag_loan_ids,
            resolved=False,
        ).update(resolved=True, resolved_at=now)

    compliance_flags = _sync_severe_arrears_compliance_flags(buckets)

    return {
        'checked': checked,
        'flags_created': flags_created,
        'flags_resolved': flags_resolved,
        'loans_defaulted': loans_defaulted,
        'loans_recovered': loans_recovered,
        'compliance_flags': compliance_flags,
    }


def _sync_severe_arrears_compliance_flags(buckets):
    """Emit/refresh one platform ComplianceFlag per SACCO with 90+ arrears.

    The per-loan staged NPLFlag + notifications remain the operational
    early-warning layer at 30/60/90. This is the platform-compliance
    layer: a single aggregate flag per SACCO once it carries a
    non-performing (90-day) loan, visible on the superadmin dashboards.
    One aggregate query; then one detector call per affected SACCO.
    """
    from saccomanagement.compliance_detectors import SevereArrearsDetector

    severe_loan_ids = [
        loan_id
        for loan_id, bucket in buckets.items()
        if bucket >= NPL_COMPLIANCE_BUCKET
    ]
    if not severe_loan_ids:
        return 0

    rows = (
        Loan.objects.filter(id__in=severe_loan_ids)
        .values('membership__sacco_id')
        .annotate(
            loan_count=Count('id'),
            outstanding=Coalesce(Sum('outstanding_balance'), Decimal('0.00')),
        )
    )

    detector = SevereArrearsDetector()
    flagged = 0
    saccos = Sacco.objects.in_bulk(
        [row['membership__sacco_id'] for row in rows],
    )
    for row in rows:
        sacco = saccos.get(row['membership__sacco_id'])
        if sacco is None:
            continue
        try:
            detector.check(
                sacco,
                severe_loan_count=row['loan_count'],
                outstanding_balance=row['outstanding'],
            )
            flagged += 1
        except Exception:
            logger.exception(
                'Could not sync severe-arrears ComplianceFlag for '
                'sacco_id=%s.',
                row['membership__sacco_id'],
            )
    return flagged


SAVINGS_RECON_CHUNK_SIZE = 500


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='services.tasks.reconcile_savings_ledger',
)
def reconcile_savings_ledger(self):
    """Per-SACCO: flag memberships whose cached savings balance has
    drifted from the ledger. Alerts only - it never auto-corrects.

    ``ledger.utils.apply_ledger_entry`` is the sole writer of
    ``Saving.amount``; this is the control that catches anything that
    bypassed it. Writes a JobHeartbeat every run for ``/health/jobs/``.
    """
    from health.models import JobHeartbeat

    try:
        result = _run_savings_ledger_reconciliation()
        logger.info(
            'Savings-ledger reconciliation complete. checked=%s '
            'memberships_mismatched=%s saccos_flagged=%s.',
            result['checked'],
            result['memberships_mismatched'],
            result['saccos_flagged'],
        )
        JobHeartbeat.record('reconcile_savings_ledger', detail=result)
        return result
    except Exception as exc:
        logger.exception('Savings-ledger reconciliation failed.')
        try:
            JobHeartbeat.record(
                'reconcile_savings_ledger',
                status=JobHeartbeat.Status.ERROR,
                detail={'error': str(exc)},
            )
        except Exception:
            logger.exception(
                'Could not record reconciliation failure heartbeat.',
            )
        raise self.retry(exc=exc)


def _run_savings_ledger_reconciliation():
    from ledger.utils import (
        expected_savings_balance,
        savings_ledger_balance,
    )
    from saccomanagement.audit_logger import log_audit
    from saccomanagement.compliance_detectors import (
        SavingsLedgerMismatchDetector,
    )
    from saccomembership.models import Membership
    from services.models import Saving

    zero = Decimal('0.00')
    detector = SavingsLedgerMismatchDetector()
    checked = 0
    saccos_flagged = 0
    memberships_mismatched = 0

    for sacco in Sacco.objects.filter(is_active=True).iterator(
        chunk_size=SAVINGS_RECON_CHUNK_SIZE,
    ):
        member_ids = list(
            Saving.objects.filter(membership__sacco=sacco)
            .values_list('membership_id', flat=True)
            .distinct()
        )
        if not member_ids:
            continue

        mismatches = []
        total_drift = zero
        memberships = (
            Membership.objects.filter(id__in=member_ids)
            .only('id', 'member_number')
            .iterator(chunk_size=SAVINGS_RECON_CHUNK_SIZE)
        )
        for membership in memberships:
            checked += 1
            expected = expected_savings_balance(membership)
            ledger = savings_ledger_balance(membership)
            drift = expected - ledger
            if drift != zero:
                memberships_mismatched += 1
                total_drift += drift
                mismatches.append({
                    'membership_id': str(membership.id),
                    'member_number': membership.member_number,
                    'saving_amount_total': str(expected),
                    'ledger_savings_balance': str(ledger),
                    'drift': str(drift),
                })

        if mismatches:
            saccos_flagged += 1
            try:
                detector.check(sacco, mismatches, total_drift)
            except Exception:
                logger.exception(
                    'Could not raise savings-ledger mismatch flag for '
                    'sacco_id=%s.',
                    sacco.id,
                )
            log_audit(
                None,
                'SAVINGS_LEDGER_MISMATCH',
                'Sacco',
                sacco.id,
                new_values={
                    'mismatched_memberships': len(mismatches),
                    'total_drift': str(total_drift),
                    'sample': mismatches[:10],
                },
            )

    return {
        'checked': checked,
        'memberships_mismatched': memberships_mismatched,
        'saccos_flagged': saccos_flagged,
    }


def _apply_default_status_transition(loan, bucket):
    """Flip a loan between ACTIVE and DEFAULTED off the 90-day bucket.

    90 days past due on the earliest unpaid instalment is the SASRA
    non-performing line and is exactly ``NPLFlag.ThresholdDays.NINETY`` /
    the top bucket of ``get_arrears_bucket`` - no separate knob. Recovery
    is automatic: once the worst arrears fall back below the 30-day
    early-warning line (``bucket is None``) a DEFAULTED loan returns to
    ACTIVE. DEFAULTED stays inside OUTSTANDING_LOAN_STATUSES, so guarantor
    capacity is NOT released while a loan is in default.

    Returns ``'defaulted'``, ``'recovered'`` or ``None``.
    """
    if loan.status == Loan.Status.ACTIVE and bucket == 90:
        loan.status = Loan.Status.DEFAULTED
        loan.save(update_fields=['status', 'updated_at'])
        logger.info(
            'Loan %s reached 90-day arrears: ACTIVE -> DEFAULTED.',
            loan.id,
        )
        return 'defaulted'

    if loan.status == Loan.Status.DEFAULTED and bucket is None:
        loan.status = Loan.Status.ACTIVE
        loan.save(update_fields=['status', 'updated_at'])
        logger.info(
            'Loan %s arrears cleared: DEFAULTED -> ACTIVE.',
            loan.id,
        )
        return 'recovered'

    return None


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
        is_active=True,
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
    # These point at the frontend confirm/dispute pages, which read the
    # token from the query string and then POST it to
    # ConfirmDisbursementView / DisputeDisbursementView. The API endpoints
    # are POST-only, so simply fetching one of these links (a link-preview
    # bot, for example) cannot change disbursement state - the member has
    # to open the page and act on it.
    confirm_url = (
        f'{frontend_base_url}/confirm-disbursement/?token={token}'
    )
    dispute_url = (
        f'{frontend_base_url}/dispute-disbursement/?token={token}'
    )

    member = loan.membership.user
    sacco = loan.membership.sacco
    sms_message = (
        f'SaccoSphere: KES {tx.amount:,.0f} was sent to your M-Pesa from '
        f'{sacco.name}. Open this link to confirm you received it: '
        f'{confirm_url} - or to report a problem: {dispute_url} '
        f'(Links expire in 24 hours)'
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

    # Send alert to Sentry for human visibility
    try:
        import sentry_sdk
        sentry_sdk.set_context('disbursement_escalation', {
            'sacco_id': str(sacco.id),
            'sacco_name': sacco.name,
            'loan_id': str(loan.id),
            'conversation_id': loan.mpesa_conversation_id or 'unknown',
            'disbursement_status': loan.disbursement_status,
            'reason': '24hr timeout, M-Pesa B2C status query not supported',
        })
        sentry_sdk.capture_message(
            f'B2C Disbursement Escalated to Review: SACCO {sacco.name} '
            f'(ID: {sacco.id}), Loan {loan.id} - No callback received '
            f'within 24hr window. Daraja API does not support B2C status queries.',
            level='warning',
        )
    except ImportError:
        # Sentry not configured, log only
        pass

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
        is_active=True,
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
        is_active=True,
    )
    for role in admin_roles:
        notify_user_task.delay(
            str(role.user.id),
            title,
            message,
            Notification.Category.LOAN,
            action_url='/management/disbursements/',
        )




@shared_task(name='services.tasks.purge_expired_crb_raw_response')
def purge_expired_crb_raw_response():
    """Clear CRB raw responses past their retention period.

    Thin wrapper around the purge_expired_crb_raw_response management
    command, mirroring accounts.tasks.cleanup_expired_kyc.
    """
    from io import StringIO

    from django.core.management import call_command

    output = StringIO()
    try:
        call_command('purge_expired_crb_raw_response', stdout=output)
        result = output.getvalue()
        logger.info('CRB raw-response purge completed: %s', result)
        return result
    except Exception as exc:
        logger.error('CRB raw-response purge failed: %s', exc)
        raise


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='services.tasks.mark_overdue_instalments',
)
def mark_overdue_instalments(self):
    """Flip past-due unpaid instalments to OVERDUE and (re)accrue penalties.

    Nothing else in the codebase sets RepaymentSchedule.status = OVERDUE,
    so send_repayment_reminders' overdue-alert branch (which filters on
    status=OVERDUE, due_date=yesterday) never fires without this. Run it
    daily, before send_repayment_reminders.

    Only PENDING instalments are flipped - a PARTIAL instalment has a
    payment in progress and RepaymentSchedule.is_overdue already excludes
    it. penalty_amount is written here from the SACCO's own rule
    (services.engines.penalties.compute_penalty) so the overdue reminder
    can read a correct figure. PERCENT_PER_DAY rows already in OVERDUE are
    re-accrued on each run.
    """
    from accounts.models import SaccoSettings

    today = timezone.localdate()
    flipped = 0
    reaccrued = 0

    newly_overdue = RepaymentSchedule.objects.filter(
        status=RepaymentSchedule.Status.PENDING,
        due_date__lt=today,
    ).select_related('loan__membership__sacco')

    for item in newly_overdue.iterator():
        settings_obj = getattr(
            item.loan.membership.sacco, 'settings', None,
        )
        penalty = compute_penalty(item, settings_obj, as_of=today)
        RepaymentSchedule.objects.filter(pk=item.pk).update(
            status=RepaymentSchedule.Status.OVERDUE,
            penalty_amount=penalty,
        )
        flipped += 1

    already_overdue = RepaymentSchedule.objects.filter(
        status=RepaymentSchedule.Status.OVERDUE,
        due_date__lt=today,
    ).select_related('loan__membership__sacco')

    for item in already_overdue.iterator():
        settings_obj = getattr(
            item.loan.membership.sacco, 'settings', None,
        )
        per_day = SaccoSettings.PenaltyType.PERCENT_PER_DAY
        if settings_obj is None or settings_obj.penalty_type != per_day:
            continue
        penalty = compute_penalty(item, settings_obj, as_of=today)
        if penalty != item.penalty_amount:
            RepaymentSchedule.objects.filter(pk=item.pk).update(
                penalty_amount=penalty,
            )
            reaccrued += 1

    logger.info(
        'Overdue sweep complete: flipped=%s, penalties re-accrued=%s.',
        flipped,
        reaccrued,
    )
    return {'flipped': flipped, 'penalties_reaccrued': reaccrued}


@shared_task(name='services.tasks.send_repayment_reminders')
def send_repayment_reminders(days=3):
    """Run the repayment-reminder + overdue-alert workflow.

    Thin wrapper around the send_repayment_reminders management command,
    mirroring accounts.tasks.cleanup_expired_kyc. Wired into Celery beat
    (config/celery.py) because the command was never scheduled anywhere.
    """
    from io import StringIO

    from django.core.management import call_command

    output = StringIO()
    try:
        call_command(
            'send_repayment_reminders', days=days, stdout=output,
        )
        result = output.getvalue()
        logger.info('Repayment reminders run completed: %s', result)
        return result
    except Exception as exc:
        logger.error('Repayment reminders run failed: %s', exc)
        raise


# --- Dividend calculation / disbursement (moved off the request thread) ---

def _resolve_actor(actor_id):
    """Load the acting user for an audit call, or None."""
    if not actor_id:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


def _emit_dividend_run_metric(
    event, declaration, started_at, *, outcome, members_processed,
):
    """Emit a run-duration metric and return the observability summary."""
    from config.utils import emit_metric

    ended_at = timezone.now()
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    emit_metric(
        event,
        declaration_id=str(declaration.pk),
        sacco_id=str(declaration.sacco_id),
        outcome=outcome,
        members_processed=members_processed,
        duration_ms=duration_ms,
    )
    return {
        'started_at': started_at.isoformat(),
        'ended_at': ended_at.isoformat(),
        'duration_ms': duration_ms,
        'members_processed': members_processed,
    }


def _settle_failed_dividend_run(
    declaration_id, *, from_status, revert_to, action, error,
):
    """Move a failed run out of its transient status and audit it.

    Only touches the row if it is still in ``from_status`` (the transient
    the view set) - never stomps a status something else has changed.
    """
    from saccomanagement.audit_logger import log_audit
    from services.models import DividendDeclaration

    with transaction.atomic():
        declaration = DividendDeclaration.objects.select_for_update().get(
            pk=declaration_id,
        )
        if declaration.status != from_status:
            logger.warning(
                'Dividend declaration %s no longer in %s (is %s); leaving '
                'status untouched after a failed run.',
                declaration_id, from_status, declaration.status,
            )
            return
        declaration.status = revert_to
        declaration.save(update_fields=['status'])

    log_audit(
        None,
        action,
        'DividendDeclaration',
        declaration_id,
        old_values={'status': from_status},
        new_values={'status': revert_to, 'error': str(error)[:500]},
    )


@shared_task(
    bind=True,
    name='services.tasks.calculate_dividends_for_declaration',
)
def calculate_dividends_for_declaration_task(
    self, declaration_id, actor_id=None,
):
    """Run the per-member dividend calculation off the request thread.

    The view has already done the cheap status guard and moved the
    declaration to ``CALCULATING``. This rebuilds the payout set and, on
    success, ``calculate_dividends_for_declaration`` moves it to
    ``CALCULATED``; on any error the payout writes are rolled back (that
    function is fully atomic) and the declaration is set to ``FAILED``.
    The ``DIVIDEND_CALCULATED`` audit entry fires here now, with the same
    action / resource / values as before (no ``request``, so IP and
    user-agent are null - the work is no longer tied to a request).
    """
    from saccomanagement.audit_logger import log_audit
    from services.engines.dividend_calculator import (
        calculate_dividends_for_declaration,
    )
    from services.models import DividendDeclaration

    started_at = timezone.now()
    declaration = DividendDeclaration.objects.select_related('sacco').get(
        pk=declaration_id,
    )
    actor = _resolve_actor(actor_id)

    try:
        result = calculate_dividends_for_declaration(declaration)
    except Exception as exc:
        _settle_failed_dividend_run(
            declaration_id,
            from_status=DividendDeclaration.Status.CALCULATING,
            revert_to=DividendDeclaration.Status.FAILED,
            action='DIVIDEND_CALCULATION_FAILED',
            error=exc,
        )
        _emit_dividend_run_metric(
            'dividend_calculate_run', declaration, started_at,
            outcome='failed', members_processed=0,
        )
        logger.exception(
            'Dividend calculation failed for declaration %s.', declaration_id,
        )
        raise

    log_audit(
        actor,
        'DIVIDEND_CALCULATED',
        'DividendDeclaration',
        declaration.id,
        new_values={
            'sacco_id': str(declaration.sacco_id),
            'total_dividend_amount': str(result['total_dividend_amount']),
            'payout_count': result['payout_count'],
        },
    )
    summary = _emit_dividend_run_metric(
        'dividend_calculate_run', declaration, started_at,
        outcome='succeeded', members_processed=result['payout_count'],
    )
    logger.info(
        'Dividend calculation for declaration %s done: payouts=%s total=%s '
        'duration_ms=%s.',
        declaration_id, result['payout_count'],
        result['total_dividend_amount'], summary['duration_ms'],
    )
    return {
        'declaration_id': str(declaration_id),
        'status': DividendDeclaration.Status.CALCULATED,
        'payout_count': result['payout_count'],
        'total_dividend_amount': str(result['total_dividend_amount']),
        **summary,
    }


@shared_task(
    bind=True,
    name='services.tasks.disburse_dividends_for_declaration',
)
def disburse_dividends_for_declaration_task(
    self, declaration_id, actor_id=None,
):
    """Post an approved declaration's payouts to the ledger off-thread.

    The view has moved the declaration to ``DISBURSING``. On success
    ``disburse_dividends_for_declaration`` moves it to ``DISBURSED`` and
    the ``DIVIDEND_DISBURSED`` audit entry fires here; on failure the
    whole batched run has already rolled back (all-or-nothing) and the
    declaration is returned to ``APPROVED`` so it can be retried.
    """
    from saccomanagement.audit_logger import log_audit
    from services.engines.dividend_disbursement import (
        disburse_dividends_for_declaration,
    )
    from services.models import DividendDeclaration

    started_at = timezone.now()
    declaration = DividendDeclaration.objects.select_related('sacco').get(
        pk=declaration_id,
    )
    actor = _resolve_actor(actor_id)

    try:
        result = disburse_dividends_for_declaration(declaration)
    except Exception as exc:
        _settle_failed_dividend_run(
            declaration_id,
            from_status=DividendDeclaration.Status.DISBURSING,
            revert_to=DividendDeclaration.Status.APPROVED,
            action='DIVIDEND_DISBURSEMENT_FAILED',
            error=exc,
        )
        _emit_dividend_run_metric(
            'dividend_disburse_run', declaration, started_at,
            outcome='failed', members_processed=0,
        )
        logger.exception(
            'Dividend disbursement failed for declaration %s.', declaration_id,
        )
        raise

    log_audit(
        actor,
        'DIVIDEND_DISBURSED',
        'DividendDeclaration',
        declaration.id,
        old_values={'status': DividendDeclaration.Status.APPROVED},
        new_values={
            'status': DividendDeclaration.Status.DISBURSED,
            'sacco_id': str(declaration.sacco_id),
            'paid_count': result['paid_count'],
        },
    )
    summary = _emit_dividend_run_metric(
        'dividend_disburse_run', declaration, started_at,
        outcome='succeeded', members_processed=result['paid_count'],
    )
    logger.info(
        'Dividend disbursement for declaration %s done: paid=%s '
        'duration_ms=%s.',
        declaration_id, result['paid_count'], summary['duration_ms'],
    )
    return {
        'declaration_id': str(declaration_id),
        'status': DividendDeclaration.Status.DISBURSED,
        'paid_count': result['paid_count'],
        **summary,
    }
