"""Savings withdrawal initiation helpers."""

from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from .fee_calculator import SaccoInvoiceFeeCalculator
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


def initiate_savings_withdrawal(
    *,
    saving,
    phone_number,
    requested_amount,
    remarks='Savings Withdrawal',
):
    """
    Create a local B2C withdrawal attempt, then initiate the outbound Daraja request.

    The member requests a gross amount (e.g., KES 5,000).
    The SACCO retains the platform fee (e.g., KES 25).
    The member receives the net amount (e.g., KES 4,975).

    Returns (success: bool, payload: dict, http_status: int).
    """
    from services.models import Saving

    reference = f'SS-WD-{uuid4().hex[:18].upper()}'

    # Get SACCO-specific payment configuration
    sacco = saving.membership.sacco
    member = saving.membership.user

    # Validate saving status
    if saving.status != Saving.Status.ACTIVE:
        return False, {
            'error': 'Only active savings accounts can be withdrawn from.'
        }, 400

    # Check if SACCO is payment-ready
    if not sacco.payment_ready:
        return False, {
            'error': 'This SACCO has not completed M-Pesa Daraja onboarding. '
                    'B2C withdrawal is not yet available.'
        }, 400

    try:
        payment_config = sacco.payment_config
        if not payment_config.is_active:
            return False, {
                'error': 'Payment configuration for this SACCO is not active.'
            }, 400
        if not payment_config.has_b2c_config():
            return False, {
                'error': 'B2C withdrawal not configured for this SACCO.'
            }, 400
    except AttributeError:
        return False, {
            'error': 'Payment configuration not found for this SACCO. '
                    'Please contact platform administration.'
        }, 400

    # Calculate fee breakdown - OUTFLOW: input is gross (requested amount)
    calc = SaccoInvoiceFeeCalculator()
    breakdown = calc.calculate('withdrawal', requested_amount)

    # Validate sufficient balance (member is debited gross amount)
    if saving.amount < breakdown['gross_amount']:
        return False, {
            'error': f'Insufficient savings balance. Requested: KES {breakdown["gross_amount"]:,.2f}, '
                    f'Available: KES {saving.amount:,.2f}'
        }, 400

    with db_transaction.atomic():
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
            transaction_type=Transaction.TransactionType.WITHDRAWAL,
            amount=breakdown['net_amount'],  # What member receives
            gross_amount=breakdown['gross_amount'],  # Full requested amount
            platform_fee=breakdown['platform_fee'],  # What SACCO retains
            fee_rate=None,  # Tiered flat fee
            status=Transaction.Status.PENDING,
            description=f'Savings withdrawal - {saving.id}',
            metadata={
                'saving_id': str(saving.id),
                'requested_amount': str(requested_amount),
            },
        )

        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=payment,
            phone_number=phone_number,
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_saving=saving,
        )

    # Initiate Daraja B2C - send NET amount to member
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
            amount=breakdown['net_amount'],  # Send NET to member
            occasion='Savings Withdrawal',
            remarks=remarks,
            result_url=callback_url,
            timeout_url=callback_url,
            initiator_name=payment_config.b2c_initiator_name,
            security_credential=payment_config.b2c_security_credential,
        )
    except DarajaError as exc:
        _mark_withdrawal_attempt_failed(payment, mpesa_transaction, saving, exc)
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

        # Update saving with withdrawal details
        saving.amount -= breakdown['gross_amount']
        saving.total_withdrawals += breakdown['gross_amount']
        saving.last_transaction_date = timezone.localdate()
        saving.save(
            update_fields=[
                'amount',
                'total_withdrawals',
                'last_transaction_date',
                'updated_at',
            ],
        )

    return True, {
        'status': 'initiated',
        'conversation_id': conversation_id,
        'requested_amount': str(breakdown['gross_amount']),
        'net_amount_sent': str(breakdown['net_amount']),
        'platform_fee': str(breakdown['platform_fee']),
        'message': 'Withdrawal initiated. Awaiting M-Pesa confirmation.',
    }, 201


def _mark_withdrawal_attempt_failed(payment, mpesa_transaction, saving, exc):
    """Mark a withdrawal attempt as failed and revert the balance."""
    with db_transaction.atomic():
        payment.status = Transaction.Status.FAILED
        payment.metadata = {
            **payment.metadata,
            'withdrawal_error': exc.message,
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

        # Revert the balance deduction if it was applied
        # (it shouldn't have been since we deduct after successful initiation)
        # This is a safety measure in case the logic changes
        if saving.metadata.get('pending_withdrawal'):
            saving.amount += Decimal(saving.metadata['pending_withdrawal'])
            saving.metadata = {
                **saving.metadata,
                'pending_withdrawal': None,
            }
            saving.save(update_fields=['amount', 'metadata', 'updated_at'])
