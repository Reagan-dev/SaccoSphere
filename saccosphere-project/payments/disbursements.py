"""Shared M-Pesa B2C disbursement initiation helpers."""

from rest_framework import serializers
from uuid import uuid4

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

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
    admin_user=None,
    request=None,
):
    """
    Create a local B2C attempt, then initiate the outbound Daraja request.

    Includes fraud-aware fee calculation and audit logging from DisbursementService.

    Returns (success: bool, payload: dict, http_status: int).
    """
    from payments.fee_calculator import SaccoInvoiceFeeCalculator
    from services.models import DisbursementAuditLog

    reference = f'SS-DSB-{uuid4().hex[:18].upper()}'

    # Get SACCO-specific payment configuration
    sacco = loan.membership.sacco
    member = loan.membership.user

    # Validate loan status
    if loan.status != loan.Status.APPROVED:
        return False, {
            'error': 'Only approved loans can be disbursed.'
        }, 400

    if loan.disbursement_status != loan.DisbursementStatus.PENDING:
        return False, {
            'error': f'Cannot disburse: status is {loan.disbursement_status}.'
        }, 400

    if not member.phone_number:
        return False, {
            'error': 'Member phone number is required before disbursement.'
        }, 400

    # Validate guarantor approval
    from services.models import Guarantor
    pending_guarantors = loan.guarantors.filter(
        status=Guarantor.Status.PENDING
    ).exists()
    if pending_guarantors:
        return False, {
            'error': (
                'Cannot disburse: loan has pending guarantor approvals. '
                'All required guarantors must approve before disbursement.'
            )
        }, 400

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
        # Calculate fee breakdown
        calc = SaccoInvoiceFeeCalculator()
        breakdown = calc.calculate('disbursement', loan.amount)

        # Create audit log for loan approval
        if admin_user:
            DisbursementAuditLog.objects.create(
                loan=loan,
                event='LOAN_APPROVED',
                actor=admin_user,
                actor_role='sacco_admin',
                ip_address=_get_ip(request),
                details={
                    'loan_amount': str(loan.amount),
                    'member_id': str(member.id),
                    'approved_by': str(admin_user.id),
                },
            )

        # Create payment provider record
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )

        # Create transaction with fee breakdown
        payment = Transaction.objects.create(
            provider=provider,
            sacco=sacco,
            user=member,
            reference=reference,
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=breakdown['net_amount'],
            gross_amount=breakdown['gross_amount'],
            platform_fee=breakdown['platform_fee'],
            fee_rate=None,
            status=Transaction.Status.PENDING,
            description=f'Loan disbursement - {loan.id}',
            metadata={'loan_id': str(loan.id)},
        )

        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=payment,
            phone_number=phone_number,
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=loan,
        )

    # Initiate Daraja B2C
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
            amount=breakdown['net_amount'],
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
        payment.metadata = {
            **payment.metadata,
            'daraja_response': daraja_response,
        }
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

        # Update loan with disbursement details
        loan.disbursement_transaction = payment
        loan.mpesa_transaction_record = mpesa_transaction
        loan.mpesa_conversation_id = conversation_id
        loan.disbursement_status = loan.DisbursementStatus.INITIATED
        loan.disbursement_initiated_at = timezone.now()
        loan.status = loan.Status.DISBURSEMENT_PENDING
        loan.save(
            update_fields=[
                'disbursement_transaction',
                'mpesa_transaction_record',
                'mpesa_conversation_id',
                'disbursement_status',
                'disbursement_initiated_at',
                'status',
                'updated_at',
            ],
        )

        # Create audit log for B2C initiation
        if admin_user:
            DisbursementAuditLog.objects.create(
                loan=loan,
                event='B2C_INITIATED',
                actor=admin_user,
                actor_role='sacco_admin',
                ip_address=_get_ip(request),
                mpesa_ref=conversation_id,
                details={
                    'conversation_id': conversation_id,
                    'gross_amount': str(breakdown['gross_amount']),
                    'platform_fee': str(breakdown['platform_fee']),
                    'net_amount_sent': str(breakdown['net_amount']),
                },
            )

    return True, {
        'status': loan.DisbursementStatus.INITIATED,
        'conversation_id': conversation_id,
        'message': 'Disbursement initiated. Awaiting M-Pesa confirmation.',
    }, 201


def _get_ip(request) -> str:
    """Extract client IP from request, accounting for proxies."""
    if not request:
        return ''
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _mark_b2c_attempt_failed(payment, mpesa_transaction, loan, exc):
    with db_transaction.atomic():
        payment.status = Transaction.Status.FAILED
        payment.metadata = {
            **payment.metadata,
            'disbursement_error': exc.message,
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

        loan.disbursement_status = loan.DisbursementStatus.FAILED
        loan.save(update_fields=['disbursement_status', 'updated_at'])
