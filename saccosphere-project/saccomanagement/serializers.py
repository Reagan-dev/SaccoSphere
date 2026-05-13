from rest_framework import serializers

from saccomembership.models import SaccoApplication, Membership


class AdminMemberUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.SerializerMethodField()
    phone_number = serializers.CharField(allow_blank=True, allow_null=True)
    kyc_status = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        return obj.get_full_name()

    def get_kyc_status(self, obj):
        return getattr(getattr(obj, 'kyc', None), 'status', None)


class AdminMemberSaccoSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class AdminMemberDetailSerializer(serializers.ModelSerializer):
    user = AdminMemberUserSerializer(read_only=True)
    sacco = AdminMemberSaccoSerializer(read_only=True)
    savings_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    outstanding_loans = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    savings_breakdown = serializers.SerializerMethodField()
    active_loans = serializers.SerializerMethodField()
    recent_transactions = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = (
            'id',
            'user',
            'sacco',
            'member_number',
            'status',
            'application_date',
            'approved_date',
            'savings_total',
            'outstanding_loans',
            'savings_breakdown',
            'active_loans',
            'recent_transactions',
        )

    def get_savings_breakdown(self, obj):
        return [
            {
                'savings_type': (
                    saving.savings_type.name
                    if saving.savings_type
                    else 'General'
                ),
                'amount': saving.amount,
                'total_contributions': saving.total_contributions,
                'total_withdrawals': saving.total_withdrawals,
                'status': saving.status,
            }
            for saving in getattr(obj, 'admin_savings', [])
        ]

    def get_active_loans(self, obj):
        return [
            {
                'id': str(loan.id),
                'loan_type': loan.loan_type.name if loan.loan_type else None,
                'amount': loan.amount,
                'interest_rate': loan.interest_rate,
                'term_months': loan.term_months,
                'outstanding_balance': loan.outstanding_balance,
                'status': loan.status,
                'created_at': loan.created_at,
            }
            for loan in getattr(obj, 'admin_active_loans', [])
        ]

    def get_recent_transactions(self, obj):
        transactions = self.context.get('recent_transactions', [])
        return [
            {
                'id': str(transaction.id),
                'reference': transaction.reference,
                'transaction_type': transaction.transaction_type,
                'amount': transaction.amount,
                'status': transaction.status,
                'description': transaction.description,
                'created_at': transaction.created_at,
            }
            for transaction in transactions
        ]


class AdminSaccoStatsSerializer(serializers.Serializer):
    total_members = serializers.IntegerField()
    pending_applications = serializers.IntegerField()
    total_savings_portfolio = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    total_loans_portfolio = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    active_loans_count = serializers.IntegerField()
    pending_loan_approvals = serializers.IntegerField()
    default_count = serializers.IntegerField()
    default_rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    monthly_contributions = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    recent_transactions = serializers.ListField()


class ApplicationReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=(
            (SaccoApplication.Status.APPROVED, 'Approved'),
            (SaccoApplication.Status.REJECTED, 'Rejected'),
        ),
    )
    review_notes = serializers.CharField(
        allow_blank=True,
        required=False,
    )
