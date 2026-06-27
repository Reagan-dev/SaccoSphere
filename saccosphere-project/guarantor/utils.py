import logging
import secrets

from django.conf import settings
from django.db.models import Sum

from accounts.integrations.otp_service import ATSMSClient, ATSMSError


logger = logging.getLogger('saccosphere.guarantor')


def generate_response_token():
    return secrets.token_urlsafe(48)


def send_guarantor_sms(external_guarantor):
    base_url = (
        getattr(settings, 'GUARANTOR_RESPONSE_BASE_URL', '')
        or getattr(settings, 'MPESA_CALLBACK_BASE_URL', '')
    ).rstrip('/')
    token = external_guarantor.response_token
    message = (
        f'Hello {external_guarantor.full_name}, '
        f'{external_guarantor.requested_by.first_name} has requested you to '
        f'guarantee a loan of KES '
        f'{external_guarantor.guarantee_amount:,.0f} at '
        f'{external_guarantor.sacco.name} SACCO. '
        f'To ACCEPT reply ACCEPT to this message or visit: '
        f'{base_url}/guarantor/respond/{token}/accept '
        f'To DECLINE reply DECLINE or visit: '
        f'{base_url}/guarantor/respond/{token}/decline '
        f'This link expires in 48 hours.'
    )

    try:
        sent = ATSMSClient().send_sms(
            external_guarantor.phone_number,
            message,
        )
    except ATSMSError:
        logger.exception(
            'External guarantor SMS failed for external_guarantor_id=%s.',
            external_guarantor.id,
        )
        return False

    if not sent:
        logger.warning(
            (
                'External guarantor SMS was not sent for '
                'external_guarantor_id=%s.'
            ),
            external_guarantor.id,
        )
        return False

    external_guarantor.status = external_guarantor.Status.SMS_SENT
    external_guarantor.save(update_fields=['status', 'updated_at'])
    logger.info(
        'External guarantor SMS sent for external_guarantor_id=%s.',
        external_guarantor.id,
    )
    return True


def check_loan_guarantors_complete(loan):
    pending_external_statuses = [
        loan.external_guarantors.model.Status.PENDING_SMS,
        loan.external_guarantors.model.Status.SMS_SENT,
        loan.external_guarantors.model.Status.ACCEPTED,
    ]
    has_pending_external = loan.external_guarantors.filter(
        status__in=pending_external_statuses,
    ).exists()

    if has_pending_external:
        return False, 'Loan has external guarantors pending admin review.'

    requires_guarantors = getattr(
        loan.loan_type,
        'requires_guarantors',
        getattr(loan.loan_type, 'requires_guarantor', False),
    )

    if not requires_guarantors:
        return True, 'Guarantors complete.'

    from services.models import Guarantor

    internal_guaranteed = loan.guarantors.filter(
        status=Guarantor.Status.APPROVED,
    ).aggregate(total=Sum('guarantee_amount'))['total'] or 0
    external_guaranteed = loan.external_guarantors.filter(
        status=loan.external_guarantors.model.Status.APPROVED_BY_ADMIN,
    ).aggregate(total=Sum('guarantee_amount'))['total'] or 0
    total_guaranteed = internal_guaranteed + external_guaranteed

    if total_guaranteed < loan.amount:
        deficit = loan.amount - total_guaranteed
        return (
            False,
            (
                'Insufficient guarantee coverage. '
                f'Need KES {deficit:,.0f} more in guarantees.'
            ),
        )

    return True, 'Guarantors complete.'
