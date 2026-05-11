from decimal import Decimal

from rest_framework import serializers

from saccomembership.models import Membership

from .models import (
    Guarantor,
    Insurance,
    Loan,
    LoanType,
    RepaymentSchedule,
    Saving,
    SavingsType,
)


class MembershipSummarySerializer(serializers.Serializer):
    member_number = serializers.CharField(read_only=True)
    sacco_name = serializers.CharField(source='sacco.name', read_only=True)


class SavingsTypeNameSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)


class LoanTypeNameSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)


class GuarantorUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    phone_number = serializers.CharField(read_only=True)


class SavingsTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavingsType
        fields = '__all__'


class SavingSerializer(serializers.ModelSerializer):
    membership = MembershipSummarySerializer(read_only=True)
    savings_type = SavingsTypeSerializer(read_only=True)
    savings_type_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Saving
        fields = (
            'id',
            'membership',
            'savings_type',
            'savings_type_id',
            'amount',
            'total_contributions',
            'total_withdrawals',
            'status',
            'dividend_eligible',
            'last_transaction_date',
        )


class LoanTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanType
        fields = '__all__'


class LoanListSerializer(serializers.ModelSerializer):
    membership = MembershipSummarySerializer(read_only=True)
    loan_type = LoanTypeNameSerializer(read_only=True)

    class Meta:
        model = Loan
        fields = (
            'id',
            'membership',
            'loan_type',
            'amount',
            'outstanding_balance',
            'interest_rate',
            'term_months',
            'status',
            'created_at',
        )


class LoanDetailSerializer(LoanListSerializer):
    class Meta(LoanListSerializer.Meta):
        fields = LoanListSerializer.Meta.fields + (
            'disbursement_date',
            'application_notes',
            'rejection_reason',
        )


class LoanApplySerializer(serializers.ModelSerializer):
    loan_type = serializers.PrimaryKeyRelatedField(
        queryset=LoanType.objects.filter(is_active=True),
    )

    class Meta:
        model = Loan
        fields = (
            'loan_type',
            'amount',
            'term_months',
            'application_notes',
        )

    def validate_amount(self, value):
        if value <= Decimal('0.00'):
            raise serializers.ValidationError(
                'Loan amount must be greater than zero.'
            )
        return value

    def validate(self, attrs):
        loan_type = attrs['loan_type']
        amount = attrs['amount']
        term_months = attrs['term_months']
        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if loan_type.max_amount and amount > loan_type.max_amount:
            raise serializers.ValidationError(
                {'amount': 'Loan amount cannot exceed the maximum amount.'}
            )

        if term_months > loan_type.max_term_months:
            raise serializers.ValidationError(
                {
                    'term_months': (
                        'Loan term cannot exceed the maximum term months.'
                    ),
                }
            )

        membership = Membership.objects.filter(
            user=user,
            sacco=loan_type.sacco,
            status=Membership.Status.APPROVED,
        ).first()

        if membership is None:
            raise serializers.ValidationError(
                'You must have an approved membership in this SACCO.'
            )

        attrs['membership'] = membership
        return attrs

    def create(self, validated_data):
        loan_type = validated_data['loan_type']
        amount = validated_data['amount']

        return Loan.objects.create(
            interest_rate=loan_type.interest_rate,
            outstanding_balance=amount,
            status=Loan.Status.PENDING,
            **validated_data,
        )


class RepaymentScheduleSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)

    class Meta:
        model = RepaymentSchedule
        fields = '__all__'


class GuarantorSerializer(serializers.ModelSerializer):
    guarantor = GuarantorUserSerializer(read_only=True)

    class Meta:
        model = Guarantor
        fields = (
            'id',
            'guarantor',
            'status',
            'guarantee_amount',
            'requested_at',
            'responded_at',
        )


class GuarantorSearchResultSerializer(serializers.Serializer):
    """Serialize guarantor search result details."""

    user = GuarantorUserSerializer(read_only=True)
    member_number = serializers.CharField(read_only=True)
    savings_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    available_capacity = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    can_guarantee = serializers.BooleanField(read_only=True)


class SavingsBreakdownSerializer(serializers.Serializer):
    """
    Serializer for savings breakdown by type.
    
    Returns aggregated totals for BOSA, FOSA, and SHARE_CAPITAL.
    """
    sacco_id = serializers.UUIDField(read_only=True)
    sacco_name = serializers.CharField(read_only=True)
    bosa_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    fosa_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    share_capital_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    dividend_eligible_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)


class InsuranceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Insurance
        fields = '__all__'
