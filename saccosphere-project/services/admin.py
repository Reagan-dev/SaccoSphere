from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import admin, messages
from django.db import transaction

from .models import (
    CRBCheck,
    DisbursementAuditLog,
    DividendDeclaration,
    DividendPayout,
    GuaranteeCapacity,
    Guarantor,
    Insurance,
    LiquidityAlert,
    Loan,
    LoanType,
    NPLFlag,
    RepaymentSchedule,
    ReminderLog,
    Saving,
    SavingsType,
)


class NoChangeAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SavingsType)
class SavingsTypeAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'sacco',
        'interest_rate',
        'minimum_contribution',
        'is_active',
        'allows_multiple_accounts',
    )
    list_filter = ('sacco', 'name', 'is_active', 'allows_multiple_accounts')
    search_fields = ('name', 'sacco__name')


class SavingAdminActionForm(admin.helpers.ActionForm):
    """Extra inputs for the audited SavingAdmin actions.

    Rendered in the action bar (matches DisbursementRetryActionForm); each
    action reads only the fields it needs.
    """

    reason = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Reason (required, min 10 chars)',
                'size': 45,
            },
        ),
    )
    adjustment_amount = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={'placeholder': 'Adjustment amount', 'step': '0.01'},
        ),
    )
    adjustment_direction = forms.ChoiceField(
        required=False,
        choices=(
            ('', 'Direction'),
            ('CREDIT', 'Credit (increase)'),
            ('DEBIT', 'Debit (decrease)'),
        ),
    )
    new_status = forms.ChoiceField(
        required=False,
        choices=(('', 'New status'),) + tuple(Saving.Status.choices),
    )


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
    action_form = SavingAdminActionForm
    actions = ['create_balance_adjustment', 'change_account_status']

    # Money and status are ledger- / audit-gated: never a bare field edit
    # in this form. amount / total_* move only through
    # ledger.utils.apply_ledger_entry; status only through
    # services.engines.savings_admin_ops.set_saving_status. Use the
    # actions below (super-admin only, reason required).
    readonly_fields = (
        'amount',
        'total_contributions',
        'total_withdrawals',
        'status',
    )

    def has_delete_permission(self, request, obj=None):
        # Deleting a Saving row would make a balance vanish with no
        # ledger entry and no reversal. Freeze / close via the action.
        return False

    def save_model(self, request, obj, form, change):
        """Route new accounts through the one shared creation path.

        Editing an existing row saves normally (amount / total_* / status
        are readonly, so the form cannot touch them); adding one goes
        through ``open_savings_account`` so the same-SACCO check runs and
        any amount entered is recorded as an opening ledger entry instead
        of a bare ``Saving.amount`` write.
        """
        if change:
            super().save_model(request, obj, form, change)
            return

        from services.engines.savings_provisioning import (
            open_savings_account,
        )

        saving = open_savings_account(
            membership=obj.membership,
            savings_type=obj.savings_type,
            opening_balance=obj.amount or None,
        )
        # Point the admin at the row that was actually created.
        obj.pk = saving.pk
        obj.id = saving.id

    @admin.action(
        description='Create a manual balance adjustment (reason required)',
    )
    def create_balance_adjustment(self, request, queryset):
        from services.engines.savings_admin_ops import (
            SavingsAdminOpError,
            create_savings_adjustment,
        )

        if not request.user.is_superuser:
            self.message_user(
                request,
                'Only a super admin can adjust a savings balance.',
                level=messages.ERROR,
            )
            return

        reason = (request.POST.get('reason') or '').strip()
        direction = (request.POST.get('adjustment_direction') or '').strip()
        raw_amount = (request.POST.get('adjustment_amount') or '').strip()
        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, TypeError):
            self.message_user(
                request,
                'Enter a valid adjustment amount.',
                level=messages.ERROR,
            )
            return

        done = 0
        for saving in queryset:
            try:
                entry = create_savings_adjustment(
                    saving,
                    amount=amount,
                    direction=direction,
                    actor=request.user,
                    reason=reason,
                )
            except SavingsAdminOpError as exc:
                self.message_user(
                    request, f'{saving}: {exc}', level=messages.ERROR,
                )
                continue
            done += 1
            self.message_user(
                request,
                f'{saving}: {direction} {amount} posted as ledger entry '
                f'{entry.reference}; new balance {saving.amount}.',
                level=messages.SUCCESS,
            )

        if not done:
            self.message_user(
                request,
                'No adjustments were posted.',
                level=messages.WARNING,
            )

    @admin.action(
        description='Change account status: ACTIVE / FROZEN / CLOSED '
        '(reason required)',
    )
    def change_account_status(self, request, queryset):
        from services.engines.savings_admin_ops import (
            MIN_REASON_LENGTH,
            SavingsAdminOpError,
            set_saving_status,
        )

        if not request.user.is_superuser:
            self.message_user(
                request,
                'Only a super admin can change a savings account status.',
                level=messages.ERROR,
            )
            return

        reason = (request.POST.get('reason') or '').strip()
        new_status = (request.POST.get('new_status') or '').strip()
        if len(reason) < MIN_REASON_LENGTH:
            self.message_user(
                request,
                f'A reason of at least {MIN_REASON_LENGTH} characters is '
                'required to change an account status.',
                level=messages.ERROR,
            )
            return

        done = 0
        for saving in queryset:
            try:
                set_saving_status(
                    saving,
                    new_status=new_status,
                    actor=request.user,
                    reason=reason,
                )
            except SavingsAdminOpError as exc:
                self.message_user(
                    request, f'{saving}: {exc}', level=messages.ERROR,
                )
                continue
            done += 1

        if done:
            self.message_user(
                request,
                f'{done} account(s) set to {new_status}.',
                level=messages.SUCCESS,
            )


@admin.register(DividendDeclaration)
class DividendDeclarationAdmin(admin.ModelAdmin):
    list_display = (
        'sacco',
        'savings_type',
        'financial_year',
        'declared_rate',
        'status',
        'total_dividend_amount',
        'created_by',
        'approved_by',
        'created_at',
    )
    list_filter = ('status', 'sacco', 'savings_type')
    search_fields = ('sacco__name', 'financial_year')
    readonly_fields = ('created_by',)

    def save_model(self, request, obj, form, change):
        """Stamp the creating admin so the four-eyes approval check has a
        creator to compare against for admin-created declarations."""
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(DividendPayout)
class DividendPayoutAdmin(admin.ModelAdmin):
    list_display = (
        'declaration',
        'membership',
        'saving',
        'average_balance',
        'dividend_amount',
        'status',
        'created_at',
    )
    list_filter = ('status', 'declaration__sacco')
    search_fields = (
        'membership__user__email',
        'membership__member_number',
        'declaration__financial_year',
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


class DisbursementRetryActionForm(admin.helpers.ActionForm):
    reason = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Reason (required to retry a disbursement)',
                'size': 60,
            },
        ),
    )


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
        'disbursement_status',
        'created_at',
    )
    list_filter = ('status', 'disbursement_status', 'loan_type', 'created_at')
    search_fields = (
        'membership__user__email',
        'membership__member_number',
        'membership__sacco__name',
    )
    action_form = DisbursementRetryActionForm
    actions = ['retry_failed_disbursement']

    # Disbursement is fraud-aware, money-movement state: it must only ever
    # change through the locked, idempotency-checked code path in
    # payments.disbursements or the audited retry_failed_disbursement
    # action below - never by a direct field edit in this form, which
    # previously let a false-FAILED timeout be hand-reset and silently
    # re-trigger a real M-Pesa payout with no audit trail.
    readonly_fields = (
        'disbursement_status',
        'disbursement_idempotency_key',
        'disbursement_transaction',
        'mpesa_transaction_record',
        'mpesa_conversation_id',
        'mpesa_transaction_id',
        'disbursed_amount',
        'disbursement_date',
        'disbursement_initiated_at',
        'disbursement_confirmed_at',
        'member_confirmation_sent_at',
        'member_confirmed_at',
        'member_disputed_at',
        'dispute_reason',
    )

    # Note: a richer retry workflow (reason taxonomy, a required second
    # approver) may be worth adding once this has real-world use; the
    # current free-text reason plus the append-only audit row is the
    # agreed minimum for the compliance record.
    @admin.action(
        description=(
            'Unlock selected loans for a disbursement retry (reason required)'
        ),
    )
    def retry_failed_disbursement(self, request, queryset):
        from services.models import DisbursementAuditLog

        if not request.user.is_superuser:
            self.message_user(
                request,
                'Only a super admin can unlock a disbursement for retry.',
                level=messages.ERROR,
            )
            return

        reason = (request.POST.get('reason') or '').strip()
        if len(reason) < 10:
            self.message_user(
                request,
                'A reason of at least 10 characters is required to '
                'retry a disbursement.',
                level=messages.ERROR,
            )
            return

        # Only FAILED / PENDING_CONFIRMATION may be unlocked. This is the
        # idempotency check re-run: a still-live attempt (INITIATING /
        # INITIATED / DISBURSED / ...) is never stomped, and clearing the
        # key + resetting to PENDING re-opens exactly one fresh
        # compare-and-swap slot in initiate_b2c_loan_disbursement.
        recoverable_statuses = {
            Loan.DisbursementStatus.FAILED,
            Loan.DisbursementStatus.PENDING_CONFIRMATION,
        }
        retried = 0
        skipped = 0
        for loan in queryset:
            with transaction.atomic():
                locked_loan = Loan.objects.select_for_update(
                    of=('self',),
                ).get(id=loan.id)
                if (
                    locked_loan.disbursement_status
                    not in recoverable_statuses
                ):
                    skipped += 1
                    continue

                previous_status = locked_loan.disbursement_status
                locked_loan.disbursement_status = (
                    Loan.DisbursementStatus.PENDING
                )
                locked_loan.disbursement_idempotency_key = None
                locked_loan.save(
                    update_fields=[
                        'disbursement_status',
                        'disbursement_idempotency_key',
                        'updated_at',
                    ],
                )
                DisbursementAuditLog.objects.create(
                    loan=locked_loan,
                    event='RESOLVED_BY_ADMIN',
                    actor=request.user,
                    actor_role='super_admin',
                    details={
                        'action': 'disbursement_retry_unlocked',
                        'reason': reason,
                        'previous_disbursement_status': previous_status,
                    },
                )
                retried += 1

        if retried:
            self.message_user(
                request,
                f'{retried} loan(s) unlocked for a disbursement retry.',
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f'{skipped} loan(s) skipped - not in a recoverable '
                'disbursement state (FAILED or PENDING_CONFIRMATION).',
                level=messages.WARNING,
            )


@admin.register(DisbursementAuditLog)
class DisbursementAuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'loan',
        'event',
        'actor',
        'actor_role',
        'mpesa_ref',
        'created_at',
    )
    list_filter = ('event', 'actor_role', 'created_at')
    search_fields = (
        'loan__id',
        'actor__email',
        'mpesa_ref',
    )
    readonly_fields = (
        'id',
        'loan',
        'event',
        'actor',
        'actor_role',
        'details',
        'ip_address',
        'mpesa_ref',
        'created_at',
    )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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


@admin.register(ReminderLog)
class ReminderLogAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'schedule_item',
        'reminder_type',
        'notification_created',
        'sms_sent',
        'sent_at',
    )
    list_filter = (
        'reminder_type',
        'notification_created',
        'sms_sent',
        'sent_at',
    )
    search_fields = (
        'schedule_item__loan__membership__user__email',
        'schedule_item__loan__membership__member_number',
    )
    autocomplete_fields = ('schedule_item',)
    readonly_fields = (
        'id',
        'schedule_item',
        'reminder_type',
        'notification_created',
        'sms_sent',
        'sent_at',
    )
    list_select_related = ('schedule_item',)
    list_per_page = 50
    ordering = ('-sent_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'schedule_item',
                    'reminder_type',
                    'notification_created',
                    'sms_sent',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('id', 'sent_at'),
            },
        ),
    )


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


@admin.register(LiquidityAlert)
class LiquidityAlertAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'sacco',
        'utilisation_pct',
        'pending_disbursements',
        'available_reserves',
        'resolved',
        'created_at',
    )
    list_filter = ('sacco', 'resolved', 'created_at')
    search_fields = ('sacco__name', 'sacco__registration_number')
    autocomplete_fields = ('sacco',)
    readonly_fields = (
        'id',
        'sacco',
        'available_reserves',
        'pending_disbursements',
        'utilisation_pct',
        'resolved',
        'resolved_at',
        'created_at',
    )
    list_select_related = ('sacco',)
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'utilisation_pct',
                    'resolved',
                    'resolved_at',
                ),
            },
        ),
        (
            'Financial snapshot',
            {
                'fields': (
                    'available_reserves',
                    'pending_disbursements',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('id', 'created_at'),
            },
        ),
    )


@admin.register(NPLFlag)
class NPLFlagAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'loan',
        'threshold_days',
        'resolved',
        'resolved_at',
        'flagged_at',
    )
    list_filter = ('threshold_days', 'resolved', 'flagged_at')
    search_fields = (
        'loan__membership__user__email',
        'loan__membership__member_number',
        'loan__membership__sacco__name',
    )
    autocomplete_fields = ('loan',)
    readonly_fields = (
        'id',
        'loan',
        'threshold_days',
        'flagged_at',
        'resolved',
        'resolved_at',
    )
    list_select_related = ('loan',)
    list_per_page = 50
    ordering = ('-flagged_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'loan',
                    'threshold_days',
                    'resolved',
                    'resolved_at',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('id', 'flagged_at'),
            },
        ),
    )


@admin.register(CRBCheck)
class CRBCheckAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'loan',
        'provider',
        'reference',
        'band',
        'listed_negative',
        'checked_at',
    )
    list_filter = ('band', 'listed_negative', 'checked_at')
    search_fields = (
        'loan__membership__user__email',
        'loan__membership__member_number',
        'loan__membership__sacco__name',
        'reference',
        'provider',
        'checked_by__email',
    )
    autocomplete_fields = ('loan', 'checked_by')
    readonly_fields = (
        'id',
        'loan',
        'score',
        'band',
        'listed_negative',
        'provider',
        'reference',
        'raw_response',
        'checked_by',
        'checked_at',
    )
    list_select_related = ('loan', 'checked_by')
    list_per_page = 50
    ordering = ('-checked_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'loan',
                    'provider',
                    'reference',
                    'score',
                    'band',
                    'listed_negative',
                    'checked_by',
                ),
            },
        ),
        (
            'Provider response',
            {
                'classes': ('collapse',),
                'fields': ('raw_response',),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('id', 'checked_at'),
            },
        ),
    )


