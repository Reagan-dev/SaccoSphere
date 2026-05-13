from decimal import Decimal, ROUND_HALF_UP

from services.engines.amortization import calculate_monthly_payment


def compare_loan_options(user, requested_amount, term_months):
    """
    Compare active loan products across the user's approved SACCOs.
    """
    from saccomembership.models import Membership
    from services.models import LoanType

    requested_amount = Decimal(str(requested_amount))
    term_months = int(term_months)

    loan_types = LoanType.objects.filter(
        is_active=True,
        sacco__membership__user=user,
        sacco__membership__status=Membership.Status.APPROVED,
        max_term_months__gte=term_months,
    ).select_related('sacco').order_by('sacco__name', 'name')

    options = []
    for loan_type in loan_types:
        if requested_amount < loan_type.min_amount:
            continue
        if loan_type.max_amount and requested_amount > loan_type.max_amount:
            continue

        monthly_payment = calculate_monthly_payment(
            requested_amount,
            loan_type.interest_rate,
            term_months,
        )
        total_payable = (monthly_payment * term_months).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )
        total_interest = (total_payable - requested_amount).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )

        options.append(
            {
                'sacco_id': str(loan_type.sacco_id),
                'sacco_name': loan_type.sacco.name,
                'loan_type_name': loan_type.name,
                'monthly_payment': monthly_payment,
                'interest_rate': loan_type.interest_rate,
                'total_interest': total_interest,
                'total_payable': total_payable,
                'term_months': term_months,
            }
        )

    return sorted(options, key=lambda option: option['monthly_payment'])
