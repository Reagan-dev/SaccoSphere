"""Shared M-Pesa B2C disbursement initiation helpers."""

from uuid import uuid4

from django.conf import settings
from django.db import transaction as db_transaction

from .integrations.mpesa.daraja import DarajaClient, DarajaError
from .models import MpesaTransaction, PaymentProvider, Transaction


def _get_b2c_callback_path():
    """Build B2C callback path with security token."""
    token = getattr(settings, 'MPESA_CALLBACK_TOKEN', '')
    if token:
        return f'/api/v1/payments/callback/mpesa/b2c/{token}/'
    return '/api/v1/payments/callback/mpesa/b2c/'


def initiate_b2c_loan_disbursement(
    *,
    loan,
    phone_number,
    amount,
    remarks,
):
    """
    Create a local B2C attempt, then initiate the outbound Daraja request.

    Returns (success: bool, payload: dict, http_status: int).
    """
    reference = f'SS-B2C-{uuid4().hex[:18].upper()}'

    with db_transaction.atomic():
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        payment = Transaction.objects.create(
            provider=provider,
            user=loan.membership.user,
            reference=reference,
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=amount,
            status=Transaction.Status.PENDING,
            description=remarks,
            metadata={},
        )
        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=payment,
            phone_number=phone_number,
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=loan,
        )
        loan.status = loan.Status.DISBURSEMENT_PENDING
        loan.save(update_fields=['status', 'updated_at'])

    daraja_client = DarajaClient()
    callback_url = daraja_client._build_callback_url(_get_b2c_callback_path())

    try:
        daraja_response = daraja_client.initiate_b2c(
            phone_number=phone_number,
            amount=amount,
            occasion='Loan Disbursement',
            remarks=remarks,
            result_url=callback_url,
            timeout_url=callback_url,
        )
    except DarajaError as exc:
        _mark_b2c_attempt_failed(payment, mpesa_transaction, loan, exc)
        return False, {
            'error': exc.message,
            'response_code': exc.response_code,
        }, 502

    conversation_id = daraja_response.get('ConversationID')
    originator_conversation_id = daraja_response.get(
        'OriginatorConversationID',
    )

    with db_transaction.atomic():
        payment.status = Transaction.Status.SENT
        payment.external_reference = conversation_id
        payment.metadata = {'daraja_response': daraja_response}
        payment.save(
            update_fields=[
                'status',
                'external_reference',
                'metadata',
                'updated_at',
            ]
        )
        mpesa_transaction.conversation_id = conversation_id
        mpesa_transaction.originator_conversation_id = (
            originator_conversation_id
        )
        mpesa_transaction.save(
            update_fields=[
                'conversation_id',
                'originator_conversation_id',
                'updated_at',
            ]
        )

    return True, {
        'conversation_id': conversation_id,
        'message': 'Disbursement initiated.',
        'status': payment.status,
    }, 201


def _mark_b2c_attempt_failed(payment, mpesa_transaction, loan, exc):
    with db_transaction.atomic():
        payment.status = Transaction.Status.FAILED
        payment.metadata = {
            **payment.metadata,
            'daraja_error': {
                'message': exc.message,
                'response_code': exc.response_code,
            },
        }
        payment.save(update_fields=['status', 'metadata', 'updated_at'])

        mpesa_transaction.result_code = exc.response_code
        mpesa_transaction.result_description = exc.message
        mpesa_transaction.save(
            update_fields=[
                'result_code',
                'result_description',
                'updated_at',
            ]
        )

        loan.status = loan.Status.APPROVED
        loan.save(update_fields=['status', 'updated_at'])
