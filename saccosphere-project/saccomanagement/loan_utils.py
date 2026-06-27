"""Shared helpers for SACCO admin loan approval workflows."""

from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from guarantor.models import ExternalGuarantor
from saccomembership.models import SaccoApplication
from services.engines.amortization import generate_repayment_schedule
from services.models import Guarantor, Loan, RepaymentSchedule


LOAN_FINAL_STATUSES = {
    Loan.Status.REJECTED,
    Loan.Status.COMPLETED,
    Loan.Status.DEFAULTED,
    Loan.Status.ACTIVE,
}


def build_guarantors_summary(loan):
    """Return internal/external guarantor counts and total coverage."""
    internal_approved = loan.guarantors.filter(
        status=Guarantor.Status.APPROVED,
    ).count()
    external_approved = loan.external_guarantors.filter(
        status=ExternalGuarantor.Status.APPROVED_BY_ADMIN,
    ).count()
    internal_amount = loan.guarantors.filter(
        status=Guarantor.Status.APPROVED,
    ).aggregate(total=Sum('guarantee_amount'))['total'] or Decimal('0.00')
    external_amount = loan.external_guarantors.filter(
        status=ExternalGuarantor.Status.APPROVED_BY_ADMIN,
    ).aggregate(total=Sum('guarantee_amount'))['total'] or Decimal('0.00')
    return {
        'internal_approved': internal_approved,
        'external_approved': external_approved,
        'total_coverage': str(internal_amount + external_amount),
    }


def get_member_application_documents(loan, request=None):
    """Return membership documents linked to the borrower's SACCO application."""
    application = (
        SaccoApplication.objects.filter(
            user=loan.membership.user,
            sacco=loan.membership.sacco,
        )
        .order_by('-created_at')
        .prefetch_related('membership_documents')
        .first()
    )
    if application is None:
        return []

    from saccomembership.membership_doc_serializers import (
        MembershipDocumentDetailSerializer,
    )

    documents = application.membership_documents.all()
    serializer = MembershipDocumentDetailSerializer(
        documents,
        many=True,
        context={'request': request},
    )
    return serializer.data


def persist_loan_repayment_schedule(loan):
    """Generate and store amortisation schedule for an approved loan."""
    if RepaymentSchedule.objects.filter(loan=loan).exists():
        return

    start_date = timezone.localdate()
    schedule_data = generate_repayment_schedule(
        loan_amount=loan.amount,
        annual_interest_rate=loan.interest_rate,
        term_months=loan.term_months,
        start_date=start_date,
    )

    with transaction.atomic():
        schedule_instances = [
            RepaymentSchedule(
                loan=loan,
                instalment_number=instalment['instalment_number'],
                due_date=instalment['due_date'],
                amount=instalment['amount'],
                principal=instalment['principal'],
                interest=instalment['interest'],
                balance_after=instalment['balance_after'],
            )
            for instalment in schedule_data
        ]
        RepaymentSchedule.objects.bulk_create(schedule_instances)
        loan.outstanding_balance = loan.amount
        loan.save(update_fields=['outstanding_balance', 'updated_at'])


def initiate_loan_disbursement(loan):
    """
    Validate and initiate M-Pesa B2C disbursement for an approved loan.

    Returns (success: bool, payload: dict, http_status: int).
    """
    from django.conf import settings

    from payments.integrations.mpesa.daraja import DarajaClient, DarajaError
    from payments.models import MpesaTransaction, PaymentProvider, Transaction

    member = loan.membership.user
    phone_number = member.phone_number
    if not phone_number:
        return False, {
            'detail': (
                'Member phone number is required before disbursement.'
            ),
        }, 400

    if not all([
        settings.MPESA_CONSUMER_KEY,
        settings.MPESA_CONSUMER_SECRET,
        settings.MPESA_SHORTCODE,
    ]):
        return False, {
            'detail': 'M-Pesa is not configured for disbursements.',
        }, 503

    amount = loan.amount
    reference = f'SS-B2C-{uuid4().hex[:18].upper()}'
    remarks = f'Loan disbursement — {loan.id}'
    callback_url = DarajaClient()._build_callback_url(
        '/api/v1/payments/callback/mpesa/b2c/',
    )

    try:
        daraja_response = DarajaClient().initiate_b2c(
            phone_number=phone_number,
            amount=amount,
            occasion='Loan Disbursement',
            remarks=remarks,
            result_url=callback_url,
            timeout_url=callback_url,
        )
    except DarajaError as exc:
        return False, {
            'detail': exc.message,
            'response_code': exc.response_code,
        }, 502

    conversation_id = daraja_response.get('ConversationID')
    originator_conversation_id = daraja_response.get(
        'OriginatorConversationID',
    )

    with transaction.atomic():
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        payment = Transaction.objects.create(
            provider=provider,
            user=member,
            reference=reference,
            external_reference=conversation_id,
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=amount,
            status=Transaction.Status.PENDING,
            description=remarks,
            metadata={'daraja_response': daraja_response},
        )
        MpesaTransaction.objects.create(
            transaction=payment,
            phone_number=phone_number,
            conversation_id=conversation_id,
            originator_conversation_id=originator_conversation_id,
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=loan,
        )
        loan.status = Loan.Status.DISBURSEMENT_PENDING
        loan.save(update_fields=['status', 'updated_at'])

    return True, {
        'conversation_id': conversation_id,
        'message': 'Disbursement initiated.',
        'status': loan.status,
    }, 200
