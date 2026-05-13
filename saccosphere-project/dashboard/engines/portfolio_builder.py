from decimal import Decimal

from django.db.models import Prefetch


ZERO = Decimal('0.00')


def get_unified_portfolio(user):
    """
    Aggregate all SACCO data for a member across all their active SACCOs.
    Returns a portfolio dict. Uses a single prefetched queryset for SACCO data.
    """
    from payments.models import Transaction
    from saccomembership.models import Membership
    from services.models import Loan, RepaymentSchedule, Saving

    pending_schedule_queryset = RepaymentSchedule.objects.filter(
        status=RepaymentSchedule.Status.PENDING,
    ).order_by('due_date')
    active_loans_queryset = Loan.objects.filter(
        status=Loan.Status.ACTIVE,
    ).prefetch_related(
        Prefetch(
            'schedule',
            queryset=pending_schedule_queryset,
            to_attr='pending_schedule',
        )
    )
    memberships = list(
        Membership.objects.filter(
            user=user,
            status=Membership.Status.APPROVED,
        ).select_related('sacco').prefetch_related(
            Prefetch(
                'saving_set',
                queryset=Saving.objects.filter(
                    status=Saving.Status.ACTIVE,
                ).select_related('savings_type'),
                to_attr='active_savings',
            ),
            Prefetch(
                'loan_set',
                queryset=active_loans_queryset,
                to_attr='active_loans',
            ),
        )
    )

    saccos = [get_per_sacco_summary(membership) for membership in memberships]
    recent_transactions = [
        _serialize_transaction(transaction)
        for transaction in Transaction.objects.filter(
            user=user,
        ).select_related('provider').order_by('-created_at')[:10]
    ]

    return {
        'total_saccos': len(memberships),
        'total_savings': _sum_decimal(
            sacco['savings_total'] for sacco in saccos
        ),
        'total_active_loans': sum(
            sacco['active_loans_count'] for sacco in saccos
        ),
        'total_share_capital': _sum_decimal(
            sacco['share_capital_total'] for sacco in saccos
        ),
        'saccos': saccos,
        'recent_transactions': recent_transactions,
    }


def get_per_sacco_summary(membership):
    savings = getattr(membership, 'active_savings', [])
    loans = getattr(membership, 'active_loans', [])
    bosa_total = _sum_savings_by_type(savings, 'BOSA')
    fosa_total = _sum_savings_by_type(savings, 'FOSA')
    share_capital_total = _sum_savings_by_type(savings, 'SHARE_CAPITAL')
    outstanding_loan_balance = _sum_decimal(
        loan.outstanding_balance for loan in loans
    )

    return {
        'sacco_id': str(membership.sacco_id),
        'sacco_name': membership.sacco.name,
        'sacco_logo_url': _get_sacco_logo_url(membership),
        'member_number': membership.member_number,
        'membership_status': membership.status,
        'savings_total': _sum_decimal(saving.amount for saving in savings),
        'bosa_total': bosa_total,
        'fosa_total': fosa_total,
        'share_capital_total': share_capital_total,
        'active_loans_count': len(loans),
        'outstanding_loan_balance': outstanding_loan_balance,
        'next_due_date': _get_next_due_date(loans),
    }


def get_dashboard_state(user):
    from saccomembership.models import Membership

    statuses = list(
        Membership.objects.filter(user=user).values_list('status', flat=True)
    )
    active_count = statuses.count(Membership.Status.APPROVED)
    pending_count = sum(
        1
        for status in statuses
        if status in {
            Membership.Status.PENDING,
            Membership.Status.UNDER_REVIEW,
        }
    )
    suspended_count = statuses.count(Membership.Status.SUSPENDED)

    if not statuses:
        state = 'NO_SACCOS'
        message = 'You have not joined any SACCOs yet.'
    elif active_count and pending_count:
        state = 'PARTIAL_ACTIVE'
        message = 'Some SACCO memberships are active and some are pending.'
    elif active_count:
        state = 'FULLY_ACTIVE'
        message = 'Your SACCO portfolio is active.'
    elif suspended_count and not active_count:
        state = 'SUSPENDED'
        message = 'Your SACCO memberships are currently suspended.'
    elif pending_count == len(statuses):
        state = 'ALL_PENDING'
        message = 'All your SACCO membership applications are under review.'
    else:
        state = 'NO_SACCOS'
        message = 'You do not have an active SACCO membership yet.'

    return {
        'state': state,
        'message': message,
        'active_count': active_count,
        'pending_count': pending_count,
    }


def _sum_savings_by_type(savings, savings_type_name):
    return _sum_decimal(
        saving.amount
        for saving in savings
        if (
            saving.savings_type
            and saving.savings_type.name == savings_type_name
        )
    )


def _get_next_due_date(loans):
    due_dates = [
        schedule.due_date
        for loan in loans
        for schedule in getattr(loan, 'pending_schedule', [])
    ]
    if not due_dates:
        return None

    return min(due_dates).isoformat()


def _get_sacco_logo_url(membership):
    logo = membership.sacco.logo
    if not logo:
        return None

    try:
        return logo.url
    except ValueError:
        return None


def _serialize_transaction(transaction):
    provider_name = None
    if transaction.provider_id:
        provider_name = transaction.provider.name

    return {
        'id': str(transaction.id),
        'reference': transaction.reference,
        'external_reference': transaction.external_reference,
        'transaction_type': transaction.transaction_type,
        'amount': transaction.amount,
        'currency': transaction.currency,
        'status': transaction.status,
        'description': transaction.description,
        'provider_name': provider_name,
        'created_at': transaction.created_at.isoformat(),
    }


def _sum_decimal(values):
    return sum(values, ZERO)
