"""Savings withdrawal initiation helpers.

Money-movement contract
-----------------------
The balance check, the ``select_for_update()`` lock on the ``Saving``
row, the balance deduction, the ``LedgerEntry`` and the ``Transaction``
record are all created inside ONE ``transaction.atomic()`` block, and the
locked row's balance is re-read there - an instance handed in from the
view is never trusted for the check. The outbound Daraja B2C call runs
*after* that block commits (holding a DB row lock across a slow network
call is its own hazard); if it fails, ``_reverse_withdrawal`` re-credits
the balance and writes an offsetting ledger entry.

Idempotency mirrors ``STKPushView``: an optional client-supplied
``idempotency_key`` forms a unique ``member:saving:gross:key`` row that is
checked-and-inserted atomically, plus a short in-flight window guard that
rejects a duplicate even when no key is supplied.
"""

import logging
from decimal import Decimal
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone

from .fee_calculator import SaccoInvoiceFeeCalculator
from .integrations.mpesa.daraja import (
    DarajaClient,
    DarajaError,
    format_phone_for_daraja,
)
from .models import (
    MpesaTransaction,
    PaymentProvider,
    SavingsWithdrawalIdempotencyKey,
    Transaction,
)


logger = logging.getLogger('saccosphere.payments')

# Safaricom's standard per-transaction B2C ceiling. Enforced here as
# defence in depth; WithdrawalRequestSerializer also rejects it earlier.
MAX_B2C_WITHDRAWAL_AMOUNT = Decimal('250000.00')

# A non-terminal withdrawal for the same saving + net amount + phone
# inside this window is treated as the same request even without a
# client-supplied idempotency key (mirrors STKPushView.IDEMPOTENCY_WINDOW,
# widened because B2C settles slower than STK).
WITHDRAWAL_IDEMPOTENCY_WINDOW = timedelta(minutes=10)

_NON_TERMINAL_STATUSES = (
    Transaction.Status.PENDING,
    Transaction.Status.SENT,
)


def _get_b2c_callback_path():
    """Build B2C callback path with security token."""
    token = getattr(settings, 'MPESA_CALLBACK_TOKEN', '')
    if token:
        return f'/api/v1/payments/callback/mpesa/b2c/{token}/'
    return '/api/v1/payments/callback/mpesa/b2c/'


def _duplicate_payload(existing_transaction):
    """Response body for a withdrawal that was already initiated."""
    conversation_id = None
    txn_status = None
    if existing_transaction is not None:
        conversation_id = existing_transaction.external_reference or None
        txn_status = existing_transaction.status
    return {
        'status': 'initiated',
        'duplicate': True,
        'conversation_id': conversation_id,
        'transaction_id': (
            str(existing_transaction.id)
            if existing_transaction is not None
            else None
        ),
        'transaction_status': txn_status,
        'message': (
            'A withdrawal for this request is already being processed. '
            'No second M-Pesa payout was initiated.'
        ),
    }


def initiate_savings_withdrawal(
    *,
    saving,
    phone_number,
    requested_amount,
    remarks='Savings Withdrawal',
    request=None,
    idempotency_key=None,
):
    """
    Reserve the balance locally, then initiate the outbound Daraja B2C.

    The member requests a gross amount (e.g. KES 5,000). The SACCO retains
    the platform fee (e.g. KES 25). The member receives the net amount
    (e.g. KES 4,975), and the gross amount leaves the savings balance.

    Returns (success: bool, payload: dict, http_status: int).
    """
    from services.models import Saving

    actor = getattr(request, 'user', None)
    log_ctx = {
        'member_id': str(saving.membership.user_id),
        'saving_id': str(saving.id),
        'sacco_id': str(saving.membership.sacco_id),
        'requested_amount': str(requested_amount),
    }

    requested_amount = Decimal(str(requested_amount))
    sacco = saving.membership.sacco
    member = saving.membership.user

    if requested_amount <= Decimal('0.00'):
        logger.warning(
            'Savings withdrawal rejected: non-positive amount. %s',
            log_ctx,
        )
        return False, {'error': 'Amount must be greater than zero.'}, 400

    if requested_amount > MAX_B2C_WITHDRAWAL_AMOUNT:
        logger.warning(
            'Savings withdrawal rejected: over the B2C ceiling of KES %s. %s',
            MAX_B2C_WITHDRAWAL_AMOUNT,
            log_ctx,
        )
        return False, {
            'error': (
                'Requested amount exceeds the maximum M-Pesa withdrawal of '
                f'KES {MAX_B2C_WITHDRAWAL_AMOUNT:,.2f} per transaction.'
            ),
        }, 400

    # SACCO onboarding / config gates (read config, not balance).
    if not sacco.payment_ready:
        logger.warning(
            'Savings withdrawal rejected: SACCO not payment-ready. %s',
            log_ctx,
        )
        return False, {
            'error': (
                'This SACCO has not completed M-Pesa Daraja onboarding. '
                'B2C withdrawal is not yet available.'
            ),
        }, 400

    try:
        payment_config = sacco.payment_config
        if not payment_config.is_active:
            logger.warning(
                'Savings withdrawal rejected: SACCO payment config '
                'inactive. %s',
                log_ctx,
            )
            return False, {
                'error': 'Payment configuration for this SACCO is not active.',
            }, 400
        if not payment_config.has_b2c_config():
            logger.warning(
                'Savings withdrawal rejected: SACCO has no B2C config. %s',
                log_ctx,
            )
            return False, {
                'error': 'B2C withdrawal not configured for this SACCO.',
            }, 400
    except AttributeError:
        logger.warning(
            'Savings withdrawal rejected: SACCO has no payment config. %s',
            log_ctx,
        )
        return False, {
            'error': (
                'Payment configuration not found for this SACCO. '
                'Please contact platform administration.'
            ),
        }, 400

    breakdown = SaccoInvoiceFeeCalculator().calculate(
        'withdrawal',
        requested_amount,
    )
    gross_amount = breakdown['gross_amount']
    net_amount = breakdown['net_amount']
    platform_fee = breakdown['platform_fee']

    if net_amount <= Decimal('0.00'):
        logger.warning(
            'Savings withdrawal rejected: amount does not exceed the '
            'processing fee (gross KES %s, fee KES %s). %s',
            gross_amount,
            platform_fee,
            log_ctx,
        )
        return False, {
            'error': 'Amount must exceed the withdrawal fee.',
        }, 400

    dedup_key = _build_idempotency_key(
        membership_id=saving.membership_id,
        saving_id=saving.id,
        gross_amount=gross_amount,
        client_key=idempotency_key,
    )
    reference = f'SS-WD-{uuid4().hex[:18].upper()}'

    try:
        with db_transaction.atomic():
            # Re-read the row under lock. The instance from the view is
            # never trusted for the balance or the status check.
            locked_saving = (
                Saving.objects.select_for_update()
                .select_related(
                    'membership',
                    'membership__sacco',
                    'membership__user',
                )
                .get(id=saving.id)
            )

            if locked_saving.status != Saving.Status.ACTIVE:
                logger.warning(
                    'Savings withdrawal rejected: account not ACTIVE '
                    '(status=%s). %s',
                    locked_saving.status,
                    log_ctx,
                )
                return False, {
                    'error': (
                        'Only active savings accounts can be withdrawn from.'
                    ),
                    'account_status': locked_saving.status,
                }, 400

            # Idempotency guard 1: an in-flight withdrawal for the same
            # saving + net amount + phone. Catches a double-submit even
            # when the client sent no key.
            window_start = timezone.now() - WITHDRAWAL_IDEMPOTENCY_WINDOW
            in_flight = (
                MpesaTransaction.objects.select_related('transaction')
                .filter(
                    related_saving=locked_saving,
                    transaction_type=MpesaTransaction.TransactionType.B2C,
                    phone_number=phone_number,
                    transaction__transaction_type=(
                        Transaction.TransactionType.WITHDRAWAL
                    ),
                    transaction__amount=net_amount,
                    transaction__status__in=_NON_TERMINAL_STATUSES,
                    transaction__created_at__gte=window_start,
                )
                .order_by('-created_at')
                .first()
            )
            if in_flight is not None:
                logger.info(
                    'Savings withdrawal de-duplicated by in-flight window. %s',
                    log_ctx,
                )
                return True, _duplicate_payload(in_flight.transaction), 200

            # Balance check against the freshly locked value.
            if locked_saving.amount < gross_amount:
                logger.warning(
                    'Savings withdrawal rejected: insufficient balance '
                    '(available KES %s, needed KES %s). %s',
                    locked_saving.amount,
                    gross_amount,
                    log_ctx,
                )
                return False, {
                    'error': (
                        'Insufficient savings balance. Requested: KES '
                        f'{gross_amount:,.2f}, Available: KES '
                        f'{locked_saving.amount:,.2f}'
                    ),
                }, 400

            # Idempotency guard 2: the explicit key. get_or_create is
            # atomic on the unique constraint. Placed after the balance
            # check so a failed-for-insufficient-funds attempt does not
            # burn the key - a genuine retry after a top-up still works.
            idem_record, created = (
                SavingsWithdrawalIdempotencyKey.objects.get_or_create(
                    key=dedup_key,
                    defaults={
                        'membership': locked_saving.membership,
                        'saving': locked_saving,
                        'amount': gross_amount,
                    },
                )
            )
            if not created:
                logger.info(
                    'Savings withdrawal de-duplicated by idempotency key. %s',
                    log_ctx,
                )
                return True, _duplicate_payload(idem_record.transaction), 200

            provider, _ = PaymentProvider.objects.get_or_create(
                name='M-Pesa',
                defaults={
                    'provider_type': PaymentProvider.ProviderType.MPESA,
                    'is_active': True,
                },
            )

            payment = Transaction.objects.create(
                provider=provider,
                sacco=sacco,
                user=member,
                reference=reference,
                transaction_type=Transaction.TransactionType.WITHDRAWAL,
                amount=net_amount,
                gross_amount=gross_amount,
                platform_fee=platform_fee,
                fee_rate=None,
                status=Transaction.Status.PENDING,
                description=f'Savings withdrawal - {locked_saving.id}',
                metadata={
                    'saving_id': str(locked_saving.id),
                    'requested_amount': str(requested_amount),
                    'idempotency_key': dedup_key,
                },
            )

            mpesa_transaction = MpesaTransaction.objects.create(
                transaction=payment,
                phone_number=phone_number,
                transaction_type=MpesaTransaction.TransactionType.B2C,
                related_saving=locked_saving,
            )

            locked_saving.amount -= gross_amount
            locked_saving.total_withdrawals += gross_amount
            locked_saving.last_transaction_date = timezone.localdate()
            locked_saving.save(
                update_fields=[
                    'amount',
                    'total_withdrawals',
                    'last_transaction_date',
                    'updated_at',
                ],
            )

            _write_withdrawal_debit_ledger(
                membership=locked_saving.membership,
                transaction=payment,
                gross_amount=gross_amount,
                net_amount=net_amount,
                platform_fee=platform_fee,
            )

            idem_record.transaction = payment
            idem_record.save(update_fields=['transaction'])

            _audit_withdrawal_initiated(
                actor=actor,
                saving=locked_saving,
                payment=payment,
                gross_amount=gross_amount,
                net_amount=net_amount,
                request=request,
            )
    except IntegrityError:
        # Lost the get_or_create race on the unique key between the SELECT
        # and INSERT; treat as a duplicate.
        logger.info(
            'Savings withdrawal de-duplicated by unique-key IntegrityError. '
            '%s',
            log_ctx,
        )
        existing = SavingsWithdrawalIdempotencyKey.objects.filter(
            key=dedup_key,
        ).select_related('transaction').first()
        existing_txn = existing.transaction if existing else None
        return True, _duplicate_payload(existing_txn), 200

    # --- balance is now reserved and committed; call Daraja outside the
    #     lock ---
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
            amount=net_amount,
            occasion='Savings Withdrawal',
            remarks=remarks,
            result_url=callback_url,
            timeout_url=callback_url,
            initiator_name=payment_config.b2c_initiator_name,
            security_credential=payment_config.b2c_security_credential,
        )
    except DarajaError as exc:
        logger.error(
            'Savings withdrawal B2C initiation failed: %s (code=%s). %s',
            exc.message,
            exc.response_code,
            {**log_ctx, 'transaction_id': str(payment.id)},
            exc_info=True,
        )
        _reverse_withdrawal(
            payment,
            reason=exc.message,
            response_code=exc.response_code,
        )
        return False, {
            'error': exc.message,
            'response_code': exc.response_code,
            'transaction_id': str(payment.id),
        }, 502

    conversation_id = daraja_response.get('ConversationID')
    originator_conversation_id = daraja_response.get(
        'OriginatorConversationID',
    )

    with db_transaction.atomic():
        payment = Transaction.objects.select_for_update().get(id=payment.id)
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
            ],
        )

        mpesa_transaction = MpesaTransaction.objects.select_for_update().get(
            transaction=payment,
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
            ],
        )

    logger.info(
        'Savings withdrawal B2C accepted: conversation_id=%s. %s',
        conversation_id,
        {**log_ctx, 'transaction_id': str(payment.id)},
    )

    return True, {
        'status': 'initiated',
        'conversation_id': conversation_id,
        'requested_amount': str(gross_amount),
        'net_amount_sent': str(net_amount),
        'platform_fee': str(platform_fee),
        'message': 'Withdrawal initiated. Awaiting M-Pesa confirmation.',
    }, 201


def _build_idempotency_key(
    *,
    membership_id,
    saving_id,
    gross_amount,
    client_key,
):
    """Compose member:saving:gross:request_id.

    A server-generated request id is used when the client omits one, so
    the row is still recorded for trace/audit even though it cannot then
    de-duplicate a later retry.
    """
    request_id = (client_key or '').strip() or f'srv-{uuid4().hex}'
    return f'{membership_id}:{saving_id}:{gross_amount}:{request_id}'


def _write_withdrawal_debit_ledger(
    *,
    membership,
    transaction,
    gross_amount,
    net_amount,
    platform_fee,
):
    """Write the SAVING_WITHDRAWAL debit in the same atomic block.

    Idempotent on the (unique) reference so a retried callback or a
    reconcile pass never double-posts it.
    """
    from ledger.models import LedgerEntry
    from ledger.utils import create_ledger_entry

    reference = str(transaction.id)
    if LedgerEntry.objects.filter(reference=reference).exists():
        return

    create_ledger_entry(
        membership=membership,
        entry_type=LedgerEntry.EntryType.DEBIT,
        category=LedgerEntry.Category.SAVING_WITHDRAWAL,
        amount=gross_amount,
        description=(
            f'Withdrawal. Net to member: KES {net_amount:,.2f}. '
            f'Processing fee: KES {platform_fee:,.2f}.'
        ),
        reference=reference,
        transaction=transaction,
    )


def _reverse_withdrawal(payment, *, reason, response_code=None):
    """Re-credit the balance and post an offsetting ledger entry.

    Used when the outbound B2C call is rejected at initiation, and by the
    async failure callback. Idempotent on the reversal reference.
    """
    from ledger.models import LedgerEntry
    from ledger.utils import create_ledger_entry
    from services.models import Saving

    reversal_reference = f'{payment.id}-REV'

    with db_transaction.atomic():
        # Fully idempotent: the reversal reference is unique, so a second
        # call (sync DarajaError path + async failure callback both firing)
        # returns here without re-crediting or double-posting.
        if LedgerEntry.objects.filter(reference=reversal_reference).exists():
            return

        try:
            mpesa_transaction = (
                MpesaTransaction.objects.select_for_update()
                .select_related('related_saving__membership')
                .get(transaction=payment)
            )
        except MpesaTransaction.DoesNotExist:
            logger.error(
                'Cannot reverse withdrawal %s: no MpesaTransaction.',
                payment.id,
            )
            return

        if mpesa_transaction.related_saving_id is None:
            logger.error(
                'Cannot reverse withdrawal %s: no related saving.',
                payment.id,
            )
            return

        gross_amount = payment.gross_amount or payment.amount
        locked_saving = Saving.objects.select_for_update().get(
            id=mpesa_transaction.related_saving_id,
        )
        locked_saving.amount += gross_amount
        locked_saving.total_withdrawals -= gross_amount
        locked_saving.last_transaction_date = timezone.localdate()
        locked_saving.save(
            update_fields=[
                'amount',
                'total_withdrawals',
                'last_transaction_date',
                'updated_at',
            ],
        )

        create_ledger_entry(
            membership=locked_saving.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.ADJUSTMENT,
            amount=gross_amount,
            description=(
                f'Savings withdrawal reversed: {reason}'
            )[:255],
            reference=reversal_reference,
            transaction=payment,
        )

        if payment.status != Transaction.Status.FAILED:
            payment.status = Transaction.Status.FAILED
            payment.metadata = {
                **payment.metadata,
                'withdrawal_reversal_reason': reason,
                'daraja_error': {
                    'message': reason,
                    'response_code': response_code,
                },
            }
            payment.save(update_fields=['status', 'metadata', 'updated_at'])

        if mpesa_transaction.result_code is None:
            mpesa_transaction.result_code = (
                str(response_code) if response_code is not None else None
            )
            mpesa_transaction.result_description = reason
            mpesa_transaction.save(
                update_fields=[
                    'result_code',
                    'result_description',
                    'updated_at',
                ],
            )

    logger.info(
        'Savings withdrawal %s reversed: %s',
        payment.id,
        reason,
    )


def _audit_withdrawal_initiated(
    *,
    actor,
    saving,
    payment,
    gross_amount,
    net_amount,
    request,
):
    from saccomanagement.audit_logger import log_audit

    log_audit(
        actor,
        'SAVINGS_WITHDRAWAL_INITIATED',
        'Saving',
        saving.id,
        new_values={
            'sacco_id': str(saving.membership.sacco_id),
            'membership_id': str(saving.membership_id),
            'transaction_id': str(payment.id),
            'gross_amount': str(gross_amount),
            'net_amount': str(net_amount),
        },
        request=request,
    )
