"""Shared M-Pesa B2C disbursement initiation helpers."""

from rest_framework import serializers
from uuid import uuid4

from django.conf import settings
from django.db import transaction as db_transaction

from .integrations.mpesa.daraja import (
    DarajaClient,
    DarajaError,
    format_phone_for_daraja,
)
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

    # Get SACCO-specific payment configuration
    sacco = loan.membership.sacco
    
    # Check if SACCO is payment-ready
    if not sacco.payment_ready:
        return False, {
            'error': 'This SACCO has not completed M-Pesa Daraja onboarding. '
                    'B2C disbursement is not yet available.'
        }, 400
    
    try:
        payment_config = sacco.payment_config
        if not payment_config.is_active:
            return False, {
                'error': 'Payment configuration for this SACCO is not active.'
            }, 400
        if not payment_config.has_b2c_config():
            return False, {
                'error': 'B2C disbursement not configured for this SACCO.'
            }, 400
    except AttributeError:
        return False, {
            'error': 'Payment configuration not found for this SACCO. '
                    'Please contact platform administration.'
        }, 400

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

    daraja_client = DarajaClient(
        consumer_key=payment_config.daraja_consumer_key,
        consumer_secret=payment_config.daraja_consumer_secret,
        shortcode=payment_config.shortcode,
        environment=payment_config.environment,
    )
    callback_url = daraja_client._build_callback_url(_get_b2c_callback_path())

    try:
        daraja_response = daraja_client.initiate_b2c(
            phone_number=format_phone_for_daraja(phone_number),
            amount=amount,
            occasion='Loan Disbursement',
            remarks=remarks,
            result_url=callback_url,
            timeout_url=callback_url,
            initiator_name=payment_config.b2c_initiator_name,
            security_credential=payment_config.b2c_security_credential,
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
