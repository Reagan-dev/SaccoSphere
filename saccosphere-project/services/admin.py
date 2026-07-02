from django.contrib import admin

from .models import (
    GuaranteeCapacity,
    Guarantor,
    Insurance,
    Loan,
    LoanType,
    RepaymentSchedule,
    Saving,
    SavingsType,
)


@admin.register(SavingsType)
class SavingsTypeAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'sacco',
        'interest_rate',
        'minimum_contribution',
        'is_active',
    )
    list_filter = ('sacco', 'name', 'is_active')
    search_fields = ('name', 'sacco__name')


@admin.register(Saving)
class SavingAdmin(admin.ModelAdmin):
    list_display = (
        'membership',
        'savings_type',
        'amount',
        'total_contributions',
        'total_withdrawals',
        'status',
        'dividend_eligible',
        'last_transaction_date',
    )
    list_filter = ('status', 'dividend_eligible', 'savings_type')
    search_fields = (
        'membership__user__email',
        'membership__member_number',
        'membership__sacco__name',
    )


@admin.register(LoanType)
class LoanTypeAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'sacco',
        'interest_rate',
        'max_term_months',
        'min_amount',
        'max_amount',
        'requires_guarantors',
        'is_active',
    )
    list_filter = ('sacco', 'requires_guarantors', 'is_active')
    search_fields = ('name', 'sacco__name')


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = (
        'membership',
        'loan_type',
        'amount',
        'outstanding_balance',
        'interest_rate',
        'term_months',
        'status',
        'created_at',
    )
    list_filter = ('status', 'loan_type', 'created_at')
    search_fields = (
        'membership__user__email',
        'membership__member_number',
        'membership__sacco__name',
    )


@admin.register(RepaymentSchedule)
class RepaymentScheduleAdmin(admin.ModelAdmin):
    list_display = (
        'loan',
        'instalment_number',
        'due_date',
        'amount',
        'principal',
        'interest',
        'status',
        'is_overdue',
        'days_overdue',
    )
    list_filter = ('status', 'due_date')
    search_fields = ('loan__membership__user__email',)


@admin.register(Guarantor)
class GuarantorAdmin(admin.ModelAdmin):
    list_display = (
        'loan_short_id',
        'guarantor_email',
        'status',
        'guarantee_amount',
        'requested_at',
    )
    list_filter = ('status',)
    search_fields = ('guarantor__email',)

    @admin.display(description='Loan')
    def loan_short_id(self, obj):
        """Return a short loan identifier for admin lists."""
        return str(obj.loan_id)[:8]

    @admin.display(description='Guarantor email')
    def guarantor_email(self, obj):
        """Return the guarantor email for admin lists."""
        return obj.guarantor.email


@admin.register(GuaranteeCapacity)
class GuaranteeCapacityAdmin(admin.ModelAdmin):
    list_display = (
        'user_email',
        'total_savings',
        'active_guarantees',
        'available_capacity',
        'updated_at',
    )
    search_fields = ('user__email', 'user__first_name', 'user__last_name')

    @admin.display(description='User email')
    def user_email(self, obj):
        """Return the capacity owner's email for admin lists."""
        return obj.user.email


@admin.register(Insurance)
class InsuranceAdmin(admin.ModelAdmin):
    list_display = (
        'membership',
        'policy_number',
        'type',
        'coverage_amount',
        'premium',
        'start_date',
        'end_date',
        'status',
    )
    list_filter = ('status', 'type', 'start_date', 'end_date')
    search_fields = (
        'policy_number',
        'membership__user__email',
        'membership__member_number',
    )


