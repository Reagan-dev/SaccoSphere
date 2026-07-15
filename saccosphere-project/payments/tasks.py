import logging
from decimal import Decimal

from celery import shared_task
from django.db import transaction as db_transaction
from django.utils import timezone

from notifications.tasks import notify_user_task

from ledger.utils import create_ledger_entry

from .models import Callback, MpesaTransaction, PlatformFee, Transaction
from .providers.registry import get_provider_class


logger = logging.getLogger('saccosphere.payments')


@shared_task(name='payments.tasks.process_stk_callback')
def process_stk_callback_task(checkout_request_id, result_code, callback_body):
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
            return False

        transaction = mpesa_transaction.transaction
        if _callback_already_processed(mpesa_transaction, transaction):
            logger.info(
                'M-Pesa STK callback already processed: %s.',
                checkout_request_id,
            )
            return True

        stk_callback = _get_stk_callback(callback_body)
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

    logger.info(
        'M-Pesa STK callback processed for checkout_request_id=%s.',
        checkout_request_id,
    )
    return True


@shared_task(bind=True, name='payments.tasks.process_payment_callback', max_retries=3, default_retry_delay=60)
def process_payment_callback(self, callback_id):
    """Process a PSP callback by dispatching to the provider-specific parser."""
    try:
        callback = Callback.objects.select_related('provider').get(id=callback_id)
    except Callback.DoesNotExist:
        logger.error('Payment callback not found: %s', callback_id)
        return False

    if callback.processed:
        logger.info('Payment callback already processed: %s', callback_id)
        return True

    provider = get_provider_class(callback.provider.name)()
    result = provider.parse_callback(callback.payload)

    transaction_id = callback.payload.get('merchantTransactionID')
    if not transaction_id:
        transaction_id = callback.payload.get('transaction_id')

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
            callback.save(update_fields=['processed', 'updated_at'])
            return True

        if result.is_successful:
            transaction.status = Transaction.Status.COMPLETED
            transaction.save(update_fields=['status', 'updated_at'])
            PlatformFee.objects.create(
                transaction=transaction,
                fee_type=PlatformFee.FeeType.TRANSACTION_PCT,
                amount=transaction.fee_amount,
                invoice_number=str(transaction.id),
                processed=True,
            )
            if getattr(transaction, 'membership', None) is not None:
                create_ledger_entry(
                    membership=transaction.membership,
                    entry_type='CREDIT',
                    category='SAVING_DEPOSIT',
                    amount=transaction.amount,
                    description='Deposit confirmed',
                    transaction=transaction,
                )
            else:
                logger.warning('No membership found for transaction %s', transaction.id)
            notify_user_task.delay(
                str(transaction.user_id),
                'Deposit Confirmed',
                'Your deposit has been confirmed.',
                'payment',
            )
            logger.info('Transaction %s completed via callback', transaction.id)
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
        callback.save(update_fields=['processed', 'updated_at'])

    return True


@shared_task(name='payments.tasks.reconcile_pending_transactions')
def reconcile_pending_transactions():
    """Query pending transactions and reconcile them with the provider status API."""
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
            logger.warning('Reconciliation failed for transaction %s: %s', transaction.id, exc)
            continue

        if not result.is_successful and not result.is_failed and not result.is_pending:
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


@shared_task(name='payments.tasks.process_b2c_callback')
def process_b2c_callback_task(conversation_id, result_code, callback_body):
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
            return False

        transaction = mpesa_transaction.transaction
        if _callback_already_processed(mpesa_transaction, transaction):
            logger.info(
                'M-Pesa B2C callback already processed: %s.',
                conversation_id,
            )
            return True

        result = (
            callback_body.get('Result')
            or callback_body.get('result')
            or {}
        )
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

    logger.info(
        'M-Pesa B2C callback processed for conversation_id=%s.',
        conversation_id,
    )
    return True


def _callback_already_processed(mpesa_transaction, transaction):
    return (
        mpesa_transaction.callback_received
        and transaction.status in {
            Transaction.Status.COMPLETED,
            Transaction.Status.FAILED,
        }
    )


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

    transaction.status = Transaction.Status.COMPLETED
    transaction.external_reference = (
        receipt_number or transaction.external_reference
    )
    transaction.save(
        update_fields=['status', 'external_reference', 'updated_at']
    )

    # Reconcile callback amount with expected gross amount
    expected_gross = transaction.amount + (transaction.fee_amount or Decimal('0.00'))
    amount_difference = abs(callback_amount - expected_gross)
    if amount_difference > Decimal('0.01'):
        logger.warning(
            'M-Pesa callback amount mismatch for transaction_id=%s: '
            'callback_amount=%s, expected_gross=%s, difference=%s',
            transaction.id,
            callback_amount,
            expected_gross,
            amount_difference,
        )
    else:
        logger.info(
            'M-Pesa callback amount reconciled for transaction_id=%s: '
            'callback_amount=%s matches expected_gross=%s',
            transaction.id,
            callback_amount,
            expected_gross,
        )

    # Use net amount (transaction.amount) for crediting, not gross callback amount
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

    _notify_payment_failure(mpesa_transaction, transaction, result_description)
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
    _record_platform_fee_for_sacco(transaction, loan.membership.sacco)
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

    saving = mpesa_transaction.related_saving
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
        description='M-Pesa saving deposit',
        reference=(
            mpesa_transaction.mpesa_receipt_number
            or transaction.external_reference
        ),
        transaction=transaction,
    )


def _apply_loan_repayment(mpesa_transaction, transaction, amount):
    from ledger.models import LedgerEntry
    from services.models import RepaymentSchedule

    loan = mpesa_transaction.related_loan
    loan.outstanding_balance = max(
        Decimal('0.00'),
        loan.outstanding_balance - amount,
    )
    loan.save(update_fields=['outstanding_balance', 'updated_at'])

    if mpesa_transaction.related_instalment_number:
        RepaymentSchedule.objects.filter(
            loan=loan,
            instalment_number=mpesa_transaction.related_instalment_number,
        ).update(
            status=RepaymentSchedule.Status.PAID,
            paid_date=timezone.localdate(),
            paid_amount=amount,
        )

    LedgerEntry.objects.create(
        membership=loan.membership,
        entry_type=LedgerEntry.EntryType.CREDIT,
        category=LedgerEntry.Category.LOAN_REPAYMENT,
        amount=amount,
        reference=f'{transaction.reference}-LEDGER',
        description='M-Pesa loan repayment',
        balance_after=loan.outstanding_balance,
        transaction=transaction,
    )


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
        logger.warning('Invalid M-Pesa result code received: %s.', result_code)
        return -1


def _record_platform_fee_for_sacco(transaction, sacco):
    """Record the platform fee for completed transaction once."""
    if sacco is None:
        return
    try:
        from billing.services import record_collected_fee

        record_collected_fee(transaction, sacco)
    except Exception:
        logger.exception(
            'Failed to record platform fee for transaction_id=%s.',
            transaction.id,
        )


