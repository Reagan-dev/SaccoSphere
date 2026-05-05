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
        'loan',
        'guarantor',
        'status',
        'guarantee_amount',
        'requested_at',
        'responded_at',
    )
    list_filter = ('status', 'requested_at')
    search_fields = ('loan__membership__user__email', 'guarantor__email')


@admin.register(GuaranteeCapacity)
class GuaranteeCapacityAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'total_savings',
        'active_guarantees',
        'available_capacity',
        'updated_at',
    )
    search_fields = ('user__email', 'user__first_name', 'user__last_name')


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


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# services/serializers.py
#
# SavingsTypeSerializer and LoanTypeSerializer expose all fields for SACCO
# product setup. These are ModelSerializers, so DRF builds fields from the
# Django model automatically.
#
# SavingSerializer returns a member's savings account summary. The membership
# field is nested so the API shows member_number and sacco_name instead of only
# a database ID. savings_type is also nested so the API shows the product name.
#
# LoanListSerializer returns compact loan data for list pages.
# LoanDetailSerializer extends it with extra detail fields like disbursement
# date, application notes, and rejection reason.
#
# LoanApplySerializer accepts loan applications. It validates that the amount
# is above zero, does not exceed the loan type maximum, and that the term does
# not exceed the loan type maximum term. It also checks that the logged-in user
# has an APPROVED membership in the SACCO that offers the selected loan type.
# When saved, it creates a PENDING loan and copies the interest rate from the
# selected loan type.
#
# RepaymentScheduleSerializer exposes all repayment schedule fields plus the
# model properties is_overdue and days_overdue.
#
# GuarantorSerializer shows the guarantor's email and full name, plus the
# guarantee status, amount, and response timestamps.
#
# InsuranceSerializer exposes all insurance policy fields.
#
# services/views.py
#
# SavingsTypeViewSet provides basic CRUD for savings types. Anyone can list or
# retrieve savings types, but create/update/delete currently require Django
# staff permission until SACCO-admin permissions are added later.
#
# SavingListView returns the authenticated user's savings across all SACCOs,
# with an optional ?sacco= filter.
#
# LoanTypeListView returns active loan products and can filter by ?sacco_id=.
#
# LoanApplyView creates a new PENDING loan application for the logged-in user.
#
# LoanListView returns only the logged-in user's loans, with optional ?status=
# and ?sacco= filters.
#
# LoanDetailView returns one loan only if it belongs to the logged-in user.
#
# RepaymentScheduleView returns the repayment schedule for one of the logged-in
# user's loans.
#
# services/urls.py
#
# app_name = 'services' namespaces the service URLs. The router connects the
# SavingsTypeViewSet routes, and normal path() routes connect savings, loan
# types, loans, loan detail, and loan schedule endpoints.
#
# services/admin.py
#
# Each service model is registered in Django admin with useful columns,
# filters, and search fields so staff can inspect products, savings, loans,
# repayment schedules, guarantors, guarantee capacity, and insurance policies.
#
# Django/Python concepts you might not know well
#
# A ViewSet groups list, retrieve, create, update, and delete actions in one
# class. A router turns that ViewSet into URL patterns automatically.
#
# select_related tells Django to fetch related foreign key objects in the same
# database query. This avoids many repeated queries when serializers read the
# related membership, SACCO, loan type, or savings type data.
#
# serializer context is extra information passed into a serializer. DRF passes
# the request into serializers automatically. LoanApplySerializer uses it to
# find the logged-in user.
#
# Manual test to confirm it works
#
# Create an approved Membership and an active LoanType for the same SACCO. Log
# in as that user, then POST to /api/v1/services/loans/ with loan_type, amount,
# term_months, and application_notes. Confirm a PENDING loan is created.
#
# Important design decision
#
# Loan applications do not accept membership from the request body. The
# serializer finds the membership from the logged-in user and selected loan
# type. This prevents a user from applying for a loan under someone else's
# membership.
#
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
