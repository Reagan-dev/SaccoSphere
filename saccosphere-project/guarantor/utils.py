import logging
import secrets

from django.conf import settings
from django.db.models import Sum


logger = logging.getLogger('saccosphere.guarantor')


def generate_response_token():
    return secrets.token_urlsafe(48)


def guarantor_response_page_url(token):
    """Frontend page the guarantor opens to accept/decline.

    The page reads the token and issues the JSON POST to
    guarantor:external-guarantor-respond on the guarantor's behalf. It is
    a real web page, not the API endpoint - the API needs a JSON body and
    there is no inbound-SMS "reply ACCEPT" handler.
    """
    base_url = (
        getattr(settings, 'GUARANTOR_RESPONSE_BASE_URL', '')
        or getattr(settings, 'FRONTEND_BASE_URL', '')
    ).rstrip('/')
    return f'{base_url}/guarantor-response/?token={token}'


def build_guarantor_sms_message(external_guarantor):
    """Compose the external-guarantor request SMS body."""
    url = guarantor_response_page_url(external_guarantor.response_token)
    return (
        f'Hello {external_guarantor.full_name}, '
        f'{external_guarantor.requested_by.first_name} has requested you to '
        f'guarantee a loan of KES '
        f'{external_guarantor.guarantee_amount:,.0f} at '
        f'{external_guarantor.sacco.name} SACCO. '
        f'Open this link to accept or decline: {url} '
        f'This link expires in 48 hours.'
    )


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
