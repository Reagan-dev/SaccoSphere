import logging
from decimal import Decimal

from celery import shared_task
from django.db import transaction as db_transaction
from django.utils import timezone

from .models import MpesaTransaction, Transaction


logger = logging.getLogger('saccosphere.payments')


@shared_task(name='payments.tasks.process_stk_callback')
def process_stk_callback_task(checkout_request_id, result_code, callback_body):
    with db_transaction.atomic():
        try:
            mpesa_transaction = MpesaTransaction.objects.select_for_update(
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


@shared_task(name='payments.tasks.process_b2c_callback')
def process_b2c_callback_task(conversation_id, result_code, callback_body):
    with db_transaction.atomic():
        try:
            mpesa_transaction = MpesaTransaction.objects.select_for_update(
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
    amount = _to_decimal(_get_metadata_value(stk_callback, 'Amount'))

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

    if mpesa_transaction.related_saving_id:
        _apply_saving_deposit(mpesa_transaction, transaction, amount)

    if mpesa_transaction.related_loan_id:
        _apply_loan_repayment(mpesa_transaction, transaction, amount)

    _notify_payment_success(mpesa_transaction, transaction, amount)


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

    LedgerEntry.objects.create(
        membership=saving.membership,
        entry_type=LedgerEntry.EntryType.CREDIT,
        category=LedgerEntry.Category.SAVING_DEPOSIT,
        amount=amount,
        reference=f'{transaction.reference}-LEDGER',
        description='M-Pesa saving deposit',
        balance_after=saving.amount,
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


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# What each class or function does and why:
# - verify_mpesa_signature checks that a callback password matches the password
#   SaccoSphere can generate from the shortcode, passkey, and timestamp.
# - is_safaricom_ip rejects callbacks that do not come from Safaricom's known
#   IP ranges. In DEBUG it allows all IPs because sandbox/local IPs vary.
# - is_replay_attack remembers each CheckoutRequestID for 24 hours and blocks
#   duplicate callbacks from being processed again.
# - MPesaSTKCallbackView validates the callback, acknowledges Safaricom with
#   HTTP 200, then hands heavy processing to Celery.
# - process_stk_callback_task updates the local payment records, applies money
#   to savings or loan instalments, writes a ledger entry, and notifies the
#   user.
# - process_b2c_callback_task updates loan disbursement records, activates the
#   loan on success, resets it for retry on failure, writes the ledger entry,
#   and notifies the member.
#
# Django/Python concepts that may be useful:
# - authentication_classes = [] disables normal JWT/session authentication for
#   the callback endpoint because Safaricom is not a logged-in app user.
# - hmac.compare_digest compares secrets in a timing-safe way.
# - select_for_update locks the payment row inside the transaction so two
#   worker processes cannot update the same callback at the same time.
# - db_transaction.atomic() makes all database updates succeed or roll back as
#   one unit.
# - Celery lets Django return 200 to Safaricom quickly while the worker
#   performs slower balance, ledger, and notification work in the background.
#
# One manual test:
# - Create a pending STK payment, then POST a sample successful Daraja callback
#   to /api/v1/payments/callback/mpesa/stk/. Confirm the response is 200, the
#   linked Transaction becomes COMPLETED, and the Saving balance increases.
# - For B2C, create a pending disbursement and POST a successful B2C callback.
#   Confirm the linked loan becomes ACTIVE and disbursed_amount is populated.
#
# Important design decision:
# - The callback view returns 200 for unknown or replayed CheckoutRequestIDs.
#   This prevents Safaricom from retrying forever while still avoiding unsafe
#   or duplicate money movement.
#
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
