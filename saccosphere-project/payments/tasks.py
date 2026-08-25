import logging
from decimal import Decimal

from celery import shared_task
from django.db import transaction as db_transaction
from django.utils import timezone

from notifications.tasks import notify_user_task

from ledger.utils import create_ledger_entry

from django.conf import settings

from .integrations.mpesa.daraja import DarajaClient
from .models import Callback, MpesaIdempotencyRecord, MpesaTransaction, Transaction
from .providers.registry import get_provider_class


logger = logging.getLogger('saccosphere.payments')


@shared_task(
    bind=True,
    name='payments.tasks.process_stk_callback',
    max_retries=3,
    default_retry_delay=60,
)
def process_stk_callback_task(self, callback_id):
    """Process M-Pesa STK callback from durable Callback storage."""
    try:
        callback = Callback.objects.select_related(
            'transaction',
            'transaction__mpesa',
        ).get(id=callback_id)
    except Callback.DoesNotExist:
        logger.error('Callback not found: %s', callback_id)
        return False

    if callback.processed:
        logger.info('Callback already processed: %s', callback_id)
        return True

    callback_body = callback.raw_payload
    stk_callback = _get_stk_callback(callback_body)
    checkout_request_id = stk_callback.get('CheckoutRequestID')
    result_code = stk_callback.get('ResultCode')

    if not checkout_request_id:
        logger.error('Callback missing CheckoutRequestID: %s', callback_id)
        callback.processing_error = 'Missing CheckoutRequestID'
        callback.save(update_fields=['processing_error'])
        return False

    try:
        with db_transaction.atomic():
            try:
                mpesa_transaction = MpesaTransaction.objects.select_for_update(
                    of=('self',),
                ).select_related(
                    'transaction',
                    'transaction__user',
                    'related_saving',
                    'related_saving__membership',
                    'related_loan',
                    'related_loan__membership',
                ).get(checkout_request_id=checkout_request_id)
            except MpesaTransaction.DoesNotExist:
                logger.warning(
                    'M-Pesa STK callback ignored. Transaction not found: %s.',
                    checkout_request_id,
                )
                callback.processing_error = 'Transaction not found'
                callback.save(update_fields=['processing_error'])
                return False

            transaction = mpesa_transaction.transaction
            if _callback_already_processed(mpesa_transaction, transaction):
                logger.info(
                    'M-Pesa STK callback already processed: '
                    'checkout_request_id=%s transaction_reference=%s.',
                    checkout_request_id,
                    transaction.reference,
                )
                callback.processed = True
                callback.processed_at = timezone.now()
                callback.save(update_fields=['processed', 'processed_at'])
                return True

            # Check MpesaIdempotencyRecord before crediting
            idempotency_record, created = MpesaIdempotencyRecord.objects.get_or_create(
                checkout_request_id=checkout_request_id,
            )
            if not created:
                logger.warning(
                    'M-Pesa STK callback already processed via idempotency record: %s',
                    checkout_request_id,
                )
                callback.processed = True
                callback.processed_at = timezone.now()
                callback.save(update_fields=['processed', 'processed_at'])
                return True

            normalized_result_code = _normalize_result_code(result_code)

            if normalized_result_code == 0:
                _process_successful_callback(
                    mpesa_transaction,
                    transaction,
                    stk_callback,
                )
            else:
                _process_failed_callback(
                    mpesa_transaction,
                    transaction,
                    stk_callback,
                    normalized_result_code,
                )

            # Mark callback as processed
            callback.processed = True
            callback.processed_at = timezone.now()
            callback.save(update_fields=['processed', 'processed_at'])
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'M-Pesa STK callback processing failed for '
            'callback_id=%s. Retrying in %s seconds.',
            callback_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)

    logger.info(
        'M-Pesa STK callback processed for callback_id=%s, checkout_request_id=%s.',
        callback_id,
        checkout_request_id,
    )
    return True


@shared_task(
    bind=True,
    name='payments.tasks.process_payment_callback',
    max_retries=3,
    default_retry_delay=60,
)
def process_payment_callback(self, callback_id):
    """Process a PSP callback through the provider-specific parser."""
    try:
        callback = Callback.objects.select_related('provider').get(
            id=callback_id,
        )
    except Callback.DoesNotExist:
        logger.error('Payment callback not found: %s', callback_id)
        return False

    if callback.processed:
        logger.info('Payment callback already processed: %s', callback_id)
        return True

    provider = get_provider_class(callback.provider.name)()
    result = provider.parse_callback(callback.raw_payload)

    transaction_id = callback.raw_payload.get('merchantTransactionID')
    if not transaction_id:
        transaction_id = callback.raw_payload.get('transaction_id')

    with db_transaction.atomic():
        try:
            transaction = Transaction.objects.select_for_update().get(
                id=transaction_id,
            )
        except Transaction.DoesNotExist:
            logger.error('Transaction not found for callback %s', callback_id)
            return False

        if transaction.status == Transaction.Status.COMPLETED:
            callback.processed = True
            callback.save(update_fields=['processed'])
            return True

        if result.is_successful:
            transaction.status = Transaction.Status.COMPLETED
            transaction.save(update_fields=['status', 'updated_at'])
            _create_callback_ledger_entry(transaction)
            _record_platform_fee_for_sacco(
                transaction,
                _get_transaction_sacco(transaction),
            )
            notify_user_task.delay(
                str(transaction.user_id),
                'Deposit Confirmed',
                'Your deposit has been confirmed.',
                'payment',
            )
            logger.info(
                'Transaction %s completed via callback',
                transaction.id,
            )
        elif result.is_failed:
            transaction.status = Transaction.Status.FAILED
            transaction.save(update_fields=['status', 'updated_at'])
            notify_user_task.delay(
                str(transaction.user_id),
                'Payment Failed',
                'Your payment could not be completed.',
                'payment',
            )
            logger.info('Transaction %s failed via callback', transaction.id)
        else:
            transaction.status = Transaction.Status.PENDING
            transaction.save(update_fields=['status', 'updated_at'])
            logger.info('Transaction %s remains pending', transaction.id)

        callback.processed = True
        callback.save(update_fields=['processed'])

    return True


@shared_task(name='payments.tasks.reconcile_pending_transactions')
def reconcile_pending_transactions():
    """Query pending transactions and reconcile them with provider status."""
    cutoff = timezone.now() - timezone.timedelta(minutes=10)
    pending_transactions = Transaction.objects.filter(
        status=Transaction.Status.PENDING,
        created_at__lt=cutoff,
        provider__isnull=False,
    ).select_related('provider')

    for transaction in pending_transactions:
        try:
            provider = get_provider_class(transaction.provider.name)()
            result = provider.query_status(str(transaction.id))
        except Exception as exc:
            logger.warning(
                'Reconciliation failed for transaction %s: %s',
                transaction.id,
                exc,
            )
            continue

        if (
            not result.is_successful
            and not result.is_failed
            and not result.is_pending
        ):
            continue

        callback_payload = {
            'merchantTransactionID': str(transaction.id),
            'status': result.provider_status,
            'amount_confirmed': str(result.amount_confirmed or '0.00'),
        }
        callback = Callback.objects.create(
            transaction=transaction,
            provider=transaction.provider,
            raw_payload=callback_payload,
            processed=False,
        )
        process_payment_callback.delay(str(callback.id))

    return True


@shared_task(
    bind=True,
    name='payments.tasks.process_b2c_callback',
    max_retries=3,
    default_retry_delay=60,
)
def process_b2c_callback_task(self, callback_id):
    """Process M-Pesa B2C callback from durable Callback storage."""
    try:
        callback = Callback.objects.select_related(
            'transaction',
            'transaction__mpesa',
        ).get(id=callback_id)
    except Callback.DoesNotExist:
        logger.error('Callback not found: %s', callback_id)
        return False

    if callback.processed:
        logger.info('Callback already processed: %s', callback_id)
        return True

    callback_body = callback.raw_payload
    result = (
        callback_body.get('Result')
        or callback_body.get('result')
        or {}
    )
    conversation_id = result.get('ConversationID')
    result_code = result.get('ResultCode')

    if not conversation_id:
        logger.error('Callback missing ConversationID: %s', callback_id)
        callback.processing_error = 'Missing ConversationID'
        callback.save(update_fields=['processing_error'])
        return False

    try:
        with db_transaction.atomic():
            try:
                mpesa_transaction = MpesaTransaction.objects.select_for_update(
                    of=('self',),
                ).select_related(
                    'transaction',
                    'transaction__user',
                    'related_loan',
                    'related_loan__membership',
                ).get(
                    conversation_id=conversation_id,
                    transaction_type=MpesaTransaction.TransactionType.B2C,
                )
            except MpesaTransaction.DoesNotExist:
                logger.warning(
                    'M-Pesa B2C callback ignored. Transaction not found: %s.',
                    conversation_id,
                )
                callback.processing_error = 'Transaction not found'
                callback.save(update_fields=['processing_error'])
                return False

            transaction = mpesa_transaction.transaction
            if _callback_already_processed(mpesa_transaction, transaction):
                logger.info(
                    'M-Pesa B2C callback already processed: '
                    'conversation_id=%s transaction_reference=%s.',
                    conversation_id,
                    transaction.reference,
                )
                callback.processed = True
                callback.processed_at = timezone.now()
                callback.save(update_fields=['processed', 'processed_at'])
                return True

            # Check MpesaIdempotencyRecord before crediting
            idempotency_record, created = MpesaIdempotencyRecord.objects.get_or_create(
                checkout_request_id=conversation_id,
            )
            if not created:
                logger.warning(
                    'M-Pesa B2C callback already processed via idempotency record: %s',
                    conversation_id,
                )
                callback.processed = True
                callback.processed_at = timezone.now()
                callback.save(update_fields=['processed', 'processed_at'])
                return True

            normalized_result_code = _normalize_result_code(result_code)

            if normalized_result_code == 0:
                _process_successful_b2c_callback(
                    mpesa_transaction,
                    transaction,
                    result,
                )
            else:
                _process_failed_b2c_callback(
                    mpesa_transaction,
                    transaction,
                    result,
                    normalized_result_code,
                )

            # Mark callback as processed
            callback.processed = True
            callback.processed_at = timezone.now()
            callback.save(update_fields=['processed', 'processed_at'])
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'M-Pesa B2C callback processing failed for '
            'callback_id=%s. Retrying in %s seconds.',
            callback_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)

    logger.info(
        'M-Pesa B2C callback processed for callback_id=%s, conversation_id=%s.',
        callback_id,
        conversation_id,
    )
    return True


@shared_task(name='payments.tasks.reconcile_stale_mpesa_transactions')
def reconcile_stale_mpesa_transactions():
    """Reconcile stale M-Pesa STK transactions by querying Daraja status.
    
    Finds M-Pesa transactions stuck in PENDING/PROCESSING status
    older than the configured threshold and queries Daraja for their
    actual status. Uses the same idempotency checks as callback processing
    to prevent duplicate crediting.
    """
    threshold_minutes = getattr(
        settings,
        'MPESA_RECONCILIATION_THRESHOLD_MINUTES',
        5,
    )
    max_attempts = getattr(
        settings,
        'MPESA_MAX_RECONCILIATION_ATTEMPTS',
        3,
    )
    
    cutoff = timezone.now() - timezone.timedelta(minutes=threshold_minutes)
    
    # Find STK transactions in non-terminal states older than threshold
    stale_stk_transactions = MpesaTransaction.objects.filter(
        transaction_type=MpesaTransaction.TransactionType.STK_PUSH,
        transaction__status__in={
            Transaction.Status.PENDING,
            Transaction.Status.PROCESSING,
        },
        created_at__lt=cutoff,
        checkout_request_id__isnull=False,
    ).select_related(
        'transaction',
        'transaction__user',
        'related_saving',
        'related_saving__membership',
        'related_loan',
        'related_loan__membership',
    )
    
    reconciled_count = 0
    failed_count = 0
    
    for mpesa_transaction in stale_stk_transactions:
        transaction = mpesa_transaction.transaction
        checkout_request_id = mpesa_transaction.checkout_request_id
        
        # Check reconciliation attempt counter
        reconciliation_attempts = transaction.metadata.get(
            'reconciliation_attempts',
            0,
        )
        if reconciliation_attempts >= max_attempts:
            logger.error(
                'M-Pesa transaction %s (checkout_request_id=%s) has exceeded '
                'max reconciliation attempts (%d). Manual review required.',
                transaction.id,
                checkout_request_id,
                max_attempts,
            )
            failed_count += 1
            continue
        
        # Skip if already processed via callback
        if _callback_already_processed(mpesa_transaction, transaction):
            logger.info(
                'M-Pesa transaction %s already processed via callback, '
                'skipping reconciliation.',
                transaction.id,
            )
            continue
        
        try:
            # Query Daraja for status
            daraja_response = DarajaClient().query_stk_status(
                checkout_request_id,
            )
            
            # Process response using shared function
            with db_transaction.atomic():
                mpesa_transaction = MpesaTransaction.objects.select_for_update(
                    of=('self',),
                ).select_related('transaction').get(
                    id=mpesa_transaction.id,
                )
                transaction = mpesa_transaction.transaction
                
                # Double-check status inside transaction
                if _callback_already_processed(mpesa_transaction, transaction):
                    logger.info(
                        'M-Pesa transaction %s processed during reconciliation, '
                        'skipping.',
                        transaction.id,
                    )
                    continue
                
                # Process the response
                processed = _process_daraja_status_response(
                    mpesa_transaction,
                    transaction,
                    daraja_response,
                )
                
                if processed:
                    # Increment reconciliation attempt counter
                    transaction.metadata = {
                        **transaction.metadata,
                        'reconciliation_attempts': reconciliation_attempts + 1,
                        'last_reconciled_at': timezone.now().isoformat(),
                    }
                    transaction.save(update_fields=['metadata', 'updated_at'])
                    reconciled_count += 1
                    logger.info(
                        'M-Pesa transaction %s reconciled successfully via '
                        'Daraja query (checkout_request_id=%s).',
                        transaction.id,
                        checkout_request_id,
                    )
                else:
                    # Increment attempt counter even on failure
                    transaction.metadata = {
                        **transaction.metadata,
                        'reconciliation_attempts': reconciliation_attempts + 1,
                        'last_reconciled_at': timezone.now().isoformat(),
                        'last_reconciliation_error': (
                            'Failed to process Daraja response'
                        ),
                    }
                    transaction.save(update_fields=['metadata', 'updated_at'])
                    logger.warning(
                        'M-Pesa transaction %s reconciliation failed to '
                        'process Daraja response (checkout_request_id=%s).',
                        transaction.id,
                        checkout_request_id,
                    )
                    
        except Exception as exc:
            # Increment attempt counter on error
            with db_transaction.atomic():
                mpesa_transaction = MpesaTransaction.objects.select_for_update(
                    of=('self',),
                ).select_related('transaction').get(
                    id=mpesa_transaction.id,
                )
                transaction = mpesa_transaction.transaction
                reconciliation_attempts = transaction.metadata.get(
                    'reconciliation_attempts',
                    0,
                )
                transaction.metadata = {
                    **transaction.metadata,
                    'reconciliation_attempts': reconciliation_attempts + 1,
                    'last_reconciled_at': timezone.now().isoformat(),
                    'last_reconciliation_error': str(exc),
                }
                transaction.save(update_fields=['metadata', 'updated_at'])
            
            logger.error(
                'M-Pesa transaction %s reconciliation error: %s',
                transaction.id,
                exc,
                exc_info=True,
            )
            failed_count += 1
    
    logger.info(
        'M-Pesa reconciliation completed: %d reconciled, %d failed.',
        reconciled_count,
        failed_count,
    )
    
    return {'reconciled': reconciled_count, 'failed': failed_count}


def _callback_already_processed(mpesa_transaction, transaction):
    return (
        mpesa_transaction.callback_received
        and transaction.status in {
            Transaction.Status.COMPLETED,
            Transaction.Status.FAILED,
            Transaction.Status.AMOUNT_MISMATCH,
        }
    )


def _process_daraja_status_response(
    mpesa_transaction,
    transaction,
    daraja_response,
):
    """Process a Daraja query status response using the same logic as callbacks.
    
    This shared function is used by both:
    - Callback processing (when Safaricom sends a callback)
    - Reconciliation (when we query Daraja for stale transactions)
    
    It uses the same locking and idempotency checks to ensure
    that a late callback and a reconciliation result can't both
    credit the same payment.
    """
    result_code = daraja_response.get('ResponseCode')
    if result_code is None:
        logger.warning(
            'Daraja response missing ResponseCode for transaction_id=%s',
            transaction.id,
        )
        return False

    # Normalize result code to integer
    try:
        normalized_result_code = int(result_code)
    except (TypeError, ValueError):
        logger.warning(
            'Invalid M-Pesa result code in Daraja response: %s',
            result_code,
        )
        return False

    # Check idempotency before processing
    checkout_request_id = mpesa_transaction.checkout_request_id
    if checkout_request_id:
        idempotency_record, created = MpesaIdempotencyRecord.objects.get_or_create(
            checkout_request_id=checkout_request_id,
        )
        if not created:
            logger.info(
                'Transaction already processed via idempotency record: %s',
                checkout_request_id,
            )
            return True

    # Construct a callback-like structure for the existing processing functions
    # Daraja query response structure differs from callback, but we can adapt
    if normalized_result_code == 0:
        # Success - construct callback-like structure
        stk_callback = {
            'ResultCode': result_code,
            'ResultDesc': daraja_response.get('ResponseDescription', ''),
            'CallbackMetadata': {
                'Item': [
                    {'Name': 'MpesaReceiptNumber', 'Value': daraja_response.get('MpesaReceiptNumber')},
                    {'Name': 'Amount', 'Value': daraja_response.get('Amount')},
                ]
            },
        }
        _process_successful_callback(
            mpesa_transaction,
            transaction,
            stk_callback,
        )
    else:
        # Failure
        stk_callback = {
            'ResultCode': result_code,
            'ResultDesc': daraja_response.get('ResponseDescription', 'M-Pesa payment failed.'),
        }
        _process_failed_callback(
            mpesa_transaction,
            transaction,
            stk_callback,
            normalized_result_code,
        )

    return True


def _process_successful_callback(
    mpesa_transaction,
    transaction,
    stk_callback,
):
    receipt_number = _get_metadata_value(
        stk_callback,
        'MpesaReceiptNumber',
    )
    callback_amount = _to_decimal(_get_metadata_value(stk_callback, 'Amount'))

    mpesa_transaction.callback_received = True
    mpesa_transaction.mpesa_receipt_number = receipt_number
    mpesa_transaction.result_code = str(stk_callback.get('ResultCode'))
    mpesa_transaction.result_description = stk_callback.get(
        'ResultDesc',
        '',
    )
    mpesa_transaction.save(
        update_fields=[
            'callback_received',
            'mpesa_receipt_number',
            'result_code',
            'result_description',
            'updated_at',
        ]
    )

    expected_amount = _get_expected_gross_amount(transaction)
    amount_difference = abs(callback_amount - expected_amount)
    if amount_difference > Decimal('0.01'):
        _handle_amount_mismatch(
            mpesa_transaction,
            transaction,
            receipt_number,
            callback_amount,
            expected_amount,
            amount_difference,
        )
        return

    transaction.status = Transaction.Status.COMPLETED
    transaction.external_reference = (
        receipt_number or transaction.external_reference
    )
    transaction.save(
        update_fields=['status', 'external_reference', 'updated_at']
    )

    logger.info(
        'M-Pesa callback amount reconciled for transaction_id=%s: '
        'callback_amount=%s matches expected_amount=%s',
        transaction.id,
        callback_amount,
        expected_amount,
    )

    net_amount = transaction.amount

    if mpesa_transaction.related_saving_id:
        _apply_saving_deposit(mpesa_transaction, transaction, net_amount)
        _record_platform_fee_for_sacco(
            transaction,
            mpesa_transaction.related_saving.membership.sacco,
        )

    if mpesa_transaction.related_loan_id:
        _apply_loan_repayment(mpesa_transaction, transaction, net_amount)
        _record_platform_fee_for_sacco(
            transaction,
            mpesa_transaction.related_loan.membership.sacco,
        )

    _notify_payment_success(mpesa_transaction, transaction, net_amount)


def _get_expected_gross_amount(transaction):
    if transaction.gross_amount is not None:
        return transaction.gross_amount

    gross_amount = transaction.metadata.get('gross_amount')
    if gross_amount is None:
        return transaction.amount

    return _to_decimal(gross_amount)


def _handle_amount_mismatch(
    mpesa_transaction,
    transaction,
    receipt_number,
    callback_amount,
    expected_amount,
    amount_difference,
):
    logger.warning(
        'M-Pesa callback amount mismatch for transaction_id=%s: '
        'callback_amount=%s, expected_amount=%s, difference=%s',
        transaction.id,
        callback_amount,
        expected_amount,
        amount_difference,
    )

    transaction.status = Transaction.Status.AMOUNT_MISMATCH
    transaction.external_reference = (
        receipt_number or transaction.external_reference
    )
    transaction.metadata = {
        **transaction.metadata,
        'amount_mismatch': {
            'callback_amount': str(callback_amount),
            'expected_gross_amount': str(expected_amount),
            'difference': str(amount_difference),
        },
    }
    transaction.save(
        update_fields=[
            'status',
            'external_reference',
            'metadata',
            'updated_at',
        ]
    )

    _notify_sacco_admin_amount_mismatch(
        mpesa_transaction,
        transaction,
        callback_amount,
        expected_amount,
    )


def _notify_sacco_admin_amount_mismatch(
    mpesa_transaction,
    transaction,
    callback_amount,
    expected_amount,
):
    from notifications.models import Notification
    from notifications.utils import create_notification
    from saccomanagement.models import Role

    sacco = _get_related_sacco(mpesa_transaction)
    if sacco is None:
        logger.warning(
            'Amount mismatch has no SACCO context for transaction_id=%s.',
            transaction.id,
        )
        return

    admin_roles = Role.objects.select_related('user').filter(
        sacco=sacco,
        name=Role.SACCO_ADMIN,
    )
    for role in admin_roles:
        create_notification(
            user=role.user,
            title='Payment amount mismatch',
            message=(
                f'M-Pesa callback amount KES {callback_amount} did not '
                f'match expected amount KES {expected_amount}.'
            ),
            category=Notification.Category.PAYMENT,
            related_object_type='Transaction',
            related_object_id=str(transaction.id),
            dispatch_async=False,
        )


def _get_related_sacco(mpesa_transaction):
    if mpesa_transaction.related_saving_id:
        return mpesa_transaction.related_saving.membership.sacco

    if mpesa_transaction.related_loan_id:
        return mpesa_transaction.related_loan.membership.sacco

    return None


def _process_failed_callback(
    mpesa_transaction,
    transaction,
    stk_callback,
    result_code,
):
    result_description = stk_callback.get(
        'ResultDesc',
        'M-Pesa payment failed.',
    )
    mpesa_transaction.callback_received = True
    mpesa_transaction.result_code = str(result_code)
    mpesa_transaction.result_description = result_description
    mpesa_transaction.save(
        update_fields=[
            'callback_received',
            'result_code',
            'result_description',
            'updated_at',
        ]
    )

    transaction.status = Transaction.Status.FAILED
    transaction.save(update_fields=['status', 'updated_at'])

    _notify_payment_failure(
        mpesa_transaction,
        transaction,
        result_description,
    )
    logger.info(
        'M-Pesa STK callback failed for checkout_request_id=%s: %s.',
        mpesa_transaction.checkout_request_id,
        result_description,
    )


def _process_successful_b2c_callback(
    mpesa_transaction,
    transaction,
    result,
):
    receipt_number = _get_result_parameter_value(
        result,
        'TransactionReceipt',
    )
    loan = mpesa_transaction.related_loan
    amount = transaction.amount

    mpesa_transaction.callback_received = True
    mpesa_transaction.mpesa_receipt_number = receipt_number
    mpesa_transaction.result_code = str(result.get('ResultCode'))
    mpesa_transaction.result_description = result.get('ResultDesc', '')
    mpesa_transaction.save(
        update_fields=[
            'callback_received',
            'mpesa_receipt_number',
            'result_code',
            'result_description',
            'updated_at',
        ]
    )

    transaction.status = Transaction.Status.COMPLETED
    transaction.external_reference = (
        receipt_number or mpesa_transaction.conversation_id
    )
    transaction.save(
        update_fields=['status', 'external_reference', 'updated_at']
    )

    loan.status = loan.Status.ACTIVE
    loan.disbursed_amount = amount
    loan.disbursement_date = timezone.localdate()
    loan.outstanding_balance = amount
    loan.save(
        update_fields=[
            'status',
            'disbursed_amount',
            'disbursement_date',
            'outstanding_balance',
            'updated_at',
        ]
    )

    _create_loan_disbursement_ledger(mpesa_transaction, transaction, amount)
    _notify_disbursement_success(mpesa_transaction, transaction, amount)


def _process_failed_b2c_callback(
    mpesa_transaction,
    transaction,
    result,
    result_code,
):
    loan = mpesa_transaction.related_loan
    result_description = result.get(
        'ResultDesc',
        'M-Pesa loan disbursement failed.',
    )
    mpesa_transaction.callback_received = True
    mpesa_transaction.result_code = str(result_code)
    mpesa_transaction.result_description = result_description
    mpesa_transaction.save(
        update_fields=[
            'callback_received',
            'result_code',
            'result_description',
            'updated_at',
        ]
    )

    transaction.status = Transaction.Status.FAILED
    transaction.save(update_fields=['status', 'updated_at'])

    loan.status = loan.Status.APPROVED
    loan.save(update_fields=['status', 'updated_at'])

    _notify_disbursement_failure(
        mpesa_transaction,
        transaction,
        result_description,
    )
    logger.info(
        'M-Pesa B2C callback failed for conversation_id=%s: %s.',
        mpesa_transaction.conversation_id,
        result_description,
    )


def _apply_saving_deposit(mpesa_transaction, transaction, amount):
    from ledger.models import LedgerEntry
    from ledger.utils import create_ledger_entry
    from services.models import Saving

    with db_transaction.atomic():
        saving = Saving.objects.select_for_update().select_related(
            'membership',
        ).get(id=mpesa_transaction.related_saving_id)
        saving.amount += amount
        saving.total_contributions += amount
        saving.last_transaction_date = timezone.localdate()
        saving.save(
            update_fields=[
                'amount',
                'total_contributions',
                'last_transaction_date',
                'updated_at',
            ]
        )

        create_ledger_entry(
            membership=saving.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=amount,
            description=(
                f'Deposit -- member paid KES '
                f'{_get_authoritative_gross_amount(transaction):,.2f}. '
                f'KES {_get_authoritative_platform_fee(transaction):,.2f} '
                f'platform fee included.'
            ),
            reference=str(transaction.id),
            transaction=transaction,
        )


def _apply_loan_repayment(mpesa_transaction, transaction, amount):
    from ledger.models import LedgerEntry
    from services.models import Loan, RepaymentSchedule

    with db_transaction.atomic():
        loan = Loan.objects.select_for_update().get(
            id=mpesa_transaction.related_loan_id,
        )
        applied_amount, unapplied_amount = _apply_amount_to_instalments(
            loan,
            mpesa_transaction.related_instalment_number,
            amount,
            RepaymentSchedule,
        )

        if unapplied_amount > Decimal('0.00'):
            logger.warning(
                'Loan repayment has unapplied excess for transaction_id=%s: '
                'amount=%s, applied=%s, unapplied=%s.',
                transaction.id,
                amount,
                applied_amount,
                unapplied_amount,
            )

        if applied_amount <= Decimal('0.00'):
            return

        loan.outstanding_balance = max(
            Decimal('0.00'),
            loan.outstanding_balance - applied_amount,
        )
        loan.save(update_fields=['outstanding_balance', 'updated_at'])

        LedgerEntry.objects.create(
            membership=loan.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.LOAN_REPAYMENT,
            amount=applied_amount,
            reference=str(transaction.id),
            description=(
                f'Loan repayment -- member paid KES '
                f'{_get_authoritative_gross_amount(transaction):,.2f}. '
                f'Instalment: KES {transaction.amount:,.2f}.'
            ),
            balance_after=loan.outstanding_balance,
            transaction=transaction,
        )


def _apply_amount_to_instalments(
    loan,
    starting_instalment_number,
    amount,
    repayment_schedule_model,
):
    remaining_amount = amount
    applied_amount = Decimal('0.00')
    unpaid_statuses = [
        repayment_schedule_model.Status.PENDING,
        repayment_schedule_model.Status.OVERDUE,
        repayment_schedule_model.Status.PARTIAL,
    ]
    instalments = repayment_schedule_model.objects.select_for_update().filter(
        loan=loan,
        status__in=unpaid_statuses,
    )

    if starting_instalment_number:
        instalments = instalments.filter(
            instalment_number__gte=starting_instalment_number,
        )

    for instalment in instalments.order_by('instalment_number'):
        if remaining_amount <= Decimal('0.00'):
            break

        already_paid = instalment.paid_amount or Decimal('0.00')
        amount_due = max(
            Decimal('0.00'),
            instalment.amount - already_paid,
        )
        if amount_due <= Decimal('0.00'):
            continue

        instalment_payment = min(remaining_amount, amount_due)
        new_paid_amount = already_paid + instalment_payment
        remaining_amount -= instalment_payment
        applied_amount += instalment_payment

        if new_paid_amount < instalment.amount:
            instalment.status = repayment_schedule_model.Status.PARTIAL
        else:
            instalment.status = repayment_schedule_model.Status.PAID

        instalment.paid_amount = new_paid_amount
        instalment.paid_date = timezone.localdate()
        instalment.save(
            update_fields=[
                'status',
                'paid_amount',
                'paid_date',
            ]
        )

    return applied_amount, remaining_amount


def _create_loan_disbursement_ledger(
    mpesa_transaction,
    transaction,
    amount,
):
    from ledger.models import LedgerEntry

    loan = mpesa_transaction.related_loan
    LedgerEntry.objects.create(
        membership=loan.membership,
        entry_type=LedgerEntry.EntryType.DEBIT,
        category=LedgerEntry.Category.LOAN_DISBURSEMENT,
        amount=amount,
        reference=f'{transaction.reference}-LEDGER',
        description='M-Pesa loan disbursement',
        balance_after=loan.outstanding_balance,
        transaction=transaction,
    )


def _notify_payment_success(mpesa_transaction, transaction, amount):
    from notifications.models import Notification
    from notifications.utils import create_notification

    create_notification(
        user=transaction.user,
        title='Payment confirmed',
        message=(
            f'Your M-Pesa payment of KES {amount} has been confirmed.'
        ),
        category=Notification.Category.PAYMENT,
        related_object_type='MpesaTransaction',
        related_object_id=str(mpesa_transaction.id),
    )


def _notify_payment_failure(
    mpesa_transaction,
    transaction,
    result_description,
):
    from notifications.models import Notification
    from notifications.utils import create_notification

    create_notification(
        user=transaction.user,
        title='Payment failed',
        message=f'Your M-Pesa payment failed: {result_description}',
        category=Notification.Category.PAYMENT,
        related_object_type='MpesaTransaction',
        related_object_id=str(mpesa_transaction.id),
    )


def _notify_disbursement_success(mpesa_transaction, transaction, amount):
    from notifications.models import Notification
    from notifications.utils import create_notification

    create_notification(
        user=transaction.user,
        title='Loan disbursed',
        message=(
            f'Your loan of KES {amount} has been disbursed to your M-Pesa.'
        ),
        category=Notification.Category.LOAN,
        related_object_type='MpesaTransaction',
        related_object_id=str(mpesa_transaction.id),
    )


def _notify_disbursement_failure(
    mpesa_transaction,
    transaction,
    result_description,
):
    from notifications.models import Notification
    from notifications.utils import create_notification

    create_notification(
        user=transaction.user,
        title='Loan disbursement failed',
        message=f'Your loan disbursement failed: {result_description}',
        category=Notification.Category.LOAN,
        related_object_type='MpesaTransaction',
        related_object_id=str(mpesa_transaction.id),
    )


def _get_stk_callback(callback_body):
    body = callback_body.get('Body') or callback_body.get('body') or {}
    return body.get('stkCallback') or body.get('StkCallback') or {}


def _get_metadata_value(stk_callback, name):
    metadata = stk_callback.get('CallbackMetadata') or {}
    items = metadata.get('Item') or []

    for item in items:
        if item.get('Name') == name:
            return item.get('Value')

    return None


def _get_result_parameter_value(result, key):
    parameters = result.get('ResultParameters') or {}
    items = parameters.get('ResultParameter') or []

    for item in items:
        if item.get('Key') == key:
            return item.get('Value')

    return None


def _to_decimal(value):
    if value is None:
        return Decimal('0.00')

    return Decimal(str(value)).quantize(Decimal('0.01'))


def _normalize_result_code(result_code):
    try:
        return int(result_code)
    except (TypeError, ValueError):
        logger.warning(
            'Invalid M-Pesa result code received: %s.',
            result_code,
        )
        return -1


def _create_callback_ledger_entry(transaction):
    """Create generic provider ledger entries for supported transactions."""
    from ledger.models import LedgerEntry

    tx_type = _normalize_transaction_type(transaction.transaction_type)
    if tx_type not in {'deposit', 'repayment', 'withdrawal'}:
        return

    sacco = _get_transaction_sacco(transaction)
    membership = _get_transaction_membership(transaction, sacco)
    if membership is None:
        logger.warning(
            'No membership found for transaction %s',
            transaction.id,
        )
        return

    if tx_type == 'deposit':
        create_ledger_entry(
            membership=membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=transaction.amount,
            reference=str(transaction.id),
            description=(
                f'Deposit -- member paid KES '
                f'{_get_authoritative_gross_amount(transaction):,.2f}. '
                f'KES {_get_authoritative_platform_fee(transaction):,.2f} '
                f'platform fee included.'
            ),
            transaction=transaction,
        )
        return

    if tx_type == 'repayment':
        create_ledger_entry(
            membership=membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.LOAN_REPAYMENT,
            amount=transaction.amount,
            reference=str(transaction.id),
            description=(
                f'Loan repayment -- member paid KES '
                f'{_get_authoritative_gross_amount(transaction):,.2f}. '
                f'Instalment: KES {transaction.amount:,.2f}.'
            ),
            transaction=transaction,
        )
        return

    create_ledger_entry(
        membership=membership,
        entry_type=LedgerEntry.EntryType.DEBIT,
        category=LedgerEntry.Category.SAVING_WITHDRAWAL,
        amount=_get_authoritative_gross_amount(transaction),
        reference=str(transaction.id),
        description=(
            f'Withdrawal. Received: KES {transaction.amount:,.2f}. '
            f'Processing fee: KES '
            f'{_get_authoritative_platform_fee(transaction):,.2f}.'
        ),
        transaction=transaction,
    )


def _record_platform_fee_for_sacco(transaction, sacco):
    """Record the SACCO invoice line item for a completed transaction once."""
    if sacco is None:
        return

    tx_type = _normalize_transaction_type(transaction.transaction_type)
    if tx_type not in {'deposit', 'repayment', 'withdrawal'}:
        return

    from billing.models import InvoiceLineItem
    from payments.fee_calculator import SaccoInvoiceFeeCalculator

    if InvoiceLineItem.objects.filter(transaction=transaction).exists():
        return

    calc = SaccoInvoiceFeeCalculator()
    calculator_input = (
        transaction.amount
        if tx_type in calc.INFLOW_TYPES
        else _get_authoritative_gross_amount(transaction)
    )
    fee_breakdown = calc.calculate(tx_type, calculator_input)
    today = timezone.now().date()
    billing_month = today.replace(day=1)

    InvoiceLineItem.objects.create(
        sacco=sacco,
        transaction=transaction,
        transaction_type=tx_type,
        gross_amount=_get_authoritative_gross_amount(transaction),
        net_amount=transaction.amount,
        platform_fee=_get_authoritative_platform_fee(transaction),
        fee_model=fee_breakdown['fee_model'],
        rate_applied=fee_breakdown['rate_applied'],
        tier_applied=fee_breakdown['tier_applied'],
        billing_month=billing_month,
        invoiced=False,
    )


def _get_transaction_sacco(transaction):
    if transaction.sacco_id:
        return transaction.sacco

    sacco_id = transaction.metadata.get('sacco_id')
    if not sacco_id:
        return None

    from accounts.models import Sacco

    return Sacco.objects.filter(id=sacco_id).first()


def _get_transaction_membership(transaction, sacco):
    if sacco is None:
        return None

    membership = getattr(transaction, 'membership', None)
    if membership is not None:
        return membership

    from saccomembership.models import Membership

    return Membership.objects.filter(
        user=transaction.user,
        sacco=sacco,
        status=Membership.Status.APPROVED,
    ).first()


def _get_authoritative_gross_amount(transaction):
    if transaction.gross_amount is not None:
        return transaction.gross_amount

    gross_amount = transaction.metadata.get('gross_amount')
    if gross_amount is not None:
        return _to_decimal(gross_amount)

    return transaction.amount


def _get_authoritative_platform_fee(transaction):
    if transaction.platform_fee is not None:
        return transaction.platform_fee

    platform_fee = transaction.metadata.get('platform_fee')
    if platform_fee is not None:
        return _to_decimal(platform_fee)

    return transaction.fee_amount or Decimal('0.00')


def _normalize_transaction_type(transaction_type):
    aliases = {
        'DEPOSIT': 'deposit',
        'LOAN_REPAYMENT': 'repayment',
        'LOAN_DISBURSEMENT': 'disbursement',
        'WITHDRAWAL': 'withdrawal',
    }
    return aliases.get(transaction_type, transaction_type)


