from datetime import datetime, timezone


def get_activity_feed(user, limit=20):
    """
    Merge completed payments and paid loan instalments into one activity feed.
    """
    from payments.models import Transaction
    from services.models import RepaymentSchedule

    transactions = Transaction.objects.filter(
        user=user,
        status=Transaction.Status.COMPLETED,
    ).select_related(
        'mpesa__related_saving__membership__sacco',
        'mpesa__related_loan__membership__sacco',
    ).order_by('-created_at')[:limit]

    repayments = RepaymentSchedule.objects.filter(
        loan__membership__user=user,
        status=RepaymentSchedule.Status.PAID,
    ).select_related(
        'loan__loan_type',
        'loan__membership__sacco',
    ).order_by('-paid_date', '-due_date')[:limit]

    items = [_serialize_transaction(transaction) for transaction in transactions]
    items.extend(_serialize_repayment(repayment) for repayment in repayments)
    items.sort(key=_activity_sort_key, reverse=True)

    return items[:limit]


def _serialize_transaction(transaction):
    return {
        'type': 'PAYMENT',
        'amount': transaction.amount,
        'description': transaction.description,
        'date': transaction.created_at.isoformat(),
        'sacco_name': _get_transaction_sacco_name(transaction),
        'reference': transaction.reference,
    }


def _serialize_repayment(repayment):
    paid_date = repayment.paid_date or repayment.due_date
    loan_type = repayment.loan.loan_type
    loan_type_name = loan_type.name if loan_type else 'Loan'

    return {
        'type': 'REPAYMENT',
        'amount': repayment.paid_amount or repayment.amount,
        'description': (
            f'{loan_type_name} instalment {repayment.instalment_number}'
        ),
        'date': paid_date.isoformat(),
        'sacco_name': repayment.loan.membership.sacco.name,
        'reference': str(repayment.id),
    }


def _get_transaction_sacco_name(transaction):
    if not hasattr(transaction, 'mpesa'):
        return None

    if transaction.mpesa.related_saving_id:
        return transaction.mpesa.related_saving.membership.sacco.name

    if transaction.mpesa.related_loan_id:
        return transaction.mpesa.related_loan.membership.sacco.name

    return None


def _activity_sort_key(item):
    date_value = datetime.fromisoformat(item['date'])
    if date_value.tzinfo is None:
        return date_value

    return date_value.astimezone(timezone.utc).replace(tzinfo=None)
