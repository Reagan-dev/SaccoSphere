from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.signing import TimestampSigner
from django.http import HttpRequest
from django.urls import reverse

from saccomanagement.admin import NoChangeAdminMixin

from .kyc_document_access import generate_kyc_document_url
from .models import (
    DataErasureRequest,
    KYCVerification,
    OTPToken,
    Sacco,
    SaccoPaymentConfig,
    SaccoSettings,
    User,
    UserConsent,
    UserDevice,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        'email',
        'first_name',
        'last_name',
        'phone_number',
        'is_active',
        'is_staff',
        'date_joined',
    )
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'date_joined')
    search_fields = ('email', 'first_name', 'last_name', 'phone_number')
    ordering = ('email',)
    readonly_fields = ('date_joined', 'last_login')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (
            'Personal info',
            {
                'fields': (
                    'first_name',
                    'last_name',
                    'phone_number',
                    'profile_picture',
                    'date_of_birth',
                ),
            },
        ),
        (
            'Permissions',
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                ),
            },
        ),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': (
                    'email',
                    'first_name',
                    'last_name',
                    'phone_number',
                    'password1',
                    'password2',
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                ),
            },
        ),
    )


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'device_name',
        'platform',
        'biometric_enabled',
        'last_seen',
        'created_at',
    )
    list_filter = ('platform', 'biometric_enabled', 'created_at')
    search_fields = (
        'user__email',
        'user__first_name',
        'user__last_name',
        'device_id',
        'device_name',
        'push_token',
    )
    autocomplete_fields = ('user',)
    readonly_fields = ('last_seen', 'created_at')
    list_select_related = ('user',)
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'user',
                    'device_id',
                    'device_name',
                    'platform',
                    'biometric_enabled',
                ),
            },
        ),
        (
            'Push token',
            {
                'classes': ('collapse',),
                'fields': ('push_token',),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('last_seen', 'created_at'),
            },
        ),
    )


@admin.register(Sacco)
class SaccoAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'sector',
        'county',
        'membership_type',
        'is_verified',
        'is_active',
        'member_count',
    )
    list_filter = ('sector', 'county', 'is_verified', 'membership_type')
    search_fields = ('name', 'registration_number')

    # Add this 👇
    def member_count(self, obj):
        return obj.membership_set.filter(status='APPROVED').count()
    
    member_count.short_description = 'Members'


@admin.register(SaccoSettings)
class SaccoSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'sacco',
        'min_loan_amount',
        'max_loan_amount',
        'requires_guarantor',
        'guarantor_type_allowed',
        'updated_at',
    )
    list_filter = (
        'sacco',
        'requires_guarantor',
        'guarantor_type_allowed',
    )
    search_fields = ('sacco__name', 'sacco__registration_number')
    autocomplete_fields = ('sacco',)
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('sacco',)
    ordering = ('sacco__name',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'min_loan_amount',
                    'max_loan_amount',
                    'loan_multiplier',
                    'requires_guarantor',
                    'guarantor_type_allowed',
                ),
            },
        ),
        (
            'Contribution and liquidity settings',
            {
                'fields': (
                    'registration_fee',
                    'monthly_contribution_amount',
                    'liquidity_threshold_percentage',
                    'sms_daily_limit',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('created_at', 'updated_at'),
            },
        ),
    )


@admin.register(SaccoPaymentConfig)
class SaccoPaymentConfigAdmin(admin.ModelAdmin):
    """Admin interface for SACCO-specific M-Pesa payment configuration."""
    
    list_display = (
        'sacco',
        'shortcode',
        'shortcode_type',
        'environment',
        'is_active',
        'has_b2c',
        'updated_at',
    )
    list_filter = (
        'shortcode_type',
        'environment',
        'is_active',
    )
    search_fields = ('sacco__name', 'sacco__registration_number', 'shortcode')
    autocomplete_fields = ('sacco',)
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('sacco',)
    ordering = ('sacco__name',)
    
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'is_active',
                ),
            },
        ),
        (
            'M-Pesa Shortcode Configuration',
            {
                'fields': (
                    'shortcode_type',
                    'shortcode',
                    'stk_passkey',
                ),
            },
        ),
        (
            'Daraja API Credentials',
            {
                'fields': (
                    'daraja_consumer_key',
                    'daraja_consumer_secret',
                    'environment',
                ),
                'description': (
                    'Consumer key and secret are optional if using a platform '
                    'aggregator credential. Leave blank to use global settings.'
                ),
            },
        ),
        (
            'B2C Disbursement Configuration',
            {
                'fields': (
                    'b2c_initiator_name',
                    'b2c_security_credential',
                ),
                'classes': ('collapse',),
                'description': (
                    'Required only if this SACCO performs loan disbursements. '
                    'Leave blank if SACCO does not disburse.'
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('created_at', 'updated_at'),
            },
        ),
    )
    
    @admin.display(boolean=True, description='B2C Configured')
    def has_b2c(self, obj):
        return obj.has_b2c_config()
    
    def get_readonly_fields(self, request, obj=None):
        """Make sensitive fields read-only after creation to prevent accidental exposure."""
        if obj:  # Editing an existing object
            return self.readonly_fields + (
                'daraja_consumer_secret',
                'stk_passkey',
                'b2c_security_credential',
            )
        return self.readonly_fields


@admin.register(KYCVerification)
class KYCVerificationAdmin(admin.ModelAdmin):
    list_display = (
        'user_email',
        'status',
        'iprs_verified',
        'submitted_at',
    )
    list_filter = ('status', 'iprs_verified')
    search_fields = ('user__email', 'user__first_name', 'user__last_name')
    readonly_fields = (
        'id_front_link',
        'id_back_link',
        'passport_link',
        'huduma_link',
    )

    @admin.display(description='User email', ordering='user__email')
    def user_email(self, obj):
        return obj.user.email

    def id_front_link(self, obj):
        return self._document_link(obj, 'id_front')

    def id_back_link(self, obj):
        return self._document_link(obj, 'id_back')

    def passport_link(self, obj):
        return self._document_link(obj, 'passport')

    def huduma_link(self, obj):
        return self._document_link(obj, 'huduma')

    def _document_link(self, obj, field_name):
        """Generate a signed URL for document viewing with audit logging."""
        if not getattr(obj, field_name):
            return 'No document'

        # Create a mock request for audit logging
        request = HttpRequest()
        request.META = {
            'REMOTE_ADDR': '127.0.0.1',
            'HTTP_USER_AGENT': 'Django Admin',
        }

        try:
            url = generate_kyc_document_url(
                kyc_verification=obj,
                document_field=field_name,
                viewer=self.request.user if hasattr(self, 'request') else None,
                request=request,
            )
            if url:
                return f'<a href="{url}" target="_blank">View Document</a>'
            return 'Error generating URL'
        except Exception:
            return 'Error generating URL'

    id_front_link.short_description = 'ID Front'
    id_back_link.short_description = 'ID Back'
    passport_link.short_description = 'Passport'
    huduma_link.short_description = 'Huduma'


@admin.register(OTPToken)
class OTPTokenAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'phone_number',
        'purpose',
        'is_used',
        'is_expired',
        'expires_at',
    )
    list_filter = ('purpose', 'is_used')
    search_fields = ('user__email', 'phone_number', 'code')

    @admin.display(boolean=True, description='Is expired')
    def is_expired(self, obj):
        return obj.is_expired


# -----------------------------------------------------------------------
# UserConsent admin: read-only vs. audited-manual-edit policy
#
# UserConsent is a DPA/ODPC compliance record. Whether the Django admin
# should allow it to be hand-edited at all is a real policy decision, not
# an implementation detail - so it is made explicit here instead of being
# decided implicitly by whoever next touches this file.
#
# OPTION A (ACTIVE, default) - read-only, no exceptions:
#   Staff (including superusers) can view but never add, change, or delete
#   UserConsent rows through /admin/. This matches the existing convention
#   for this codebase's other audit-sensitive models - SystemAuditLogAdmin
#   and DataConsentLogAdmin (saccomanagement/admin.py) both use the same
#   NoChangeAdminMixin below with no superuser carve-out. Corrections must
#   go through the application's own consent-give/withdraw API, which
#   stamps ip_address/user_agent/timestamp correctly and creates a new,
#   immutable row rather than mutating history in place.
#
# OPTION B (written, inactive) - audited manual edits:
#   Staff can edit, but the change form requires a "reason", and
#   save_model writes a DataConsentLog entry (who/what/when/why) before the
#   save is allowed to complete. Deletion is still never allowed even under
#   this option - deleting a compliance record leaves no trail at all, and
#   there is no "reason" to attach to something that no longer exists.
#   Choose this only if compliance/support has a genuine, recurring
#   operational need to hand-correct bad consent data (e.g. a data-import
#   error) that the consent API cannot already resolve.
#
# Trade-off: Option A makes "this record was never silently altered" a
# guarantee enforced by the admin refusing edits outright - the strongest
# form of evidence, but it means a genuinely bad row (e.g. a migration
# typo) can only be fixed by direct database access outside the admin,
# which is slower and, ironically, less auditable than a logged admin edit
# would be. Option B keeps a documented, in-admin escape hatch for that
# case, at the cost of making "immutable audit trail" a property enforced
# by convention (the reason requirement + DataConsentLog write) rather than
# by the admin refusing to allow the edit at all.
#
# To switch: change ACTIVE_CONSENT_ADMIN_POLICY below to 'B'. Nothing else
# needs to change - both admin classes are already fully written.

ACTIVE_CONSENT_ADMIN_POLICY = 'A'  # 'A' = read-only (default), 'B' = audited edits


class UserConsentReadOnlyAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    """OPTION A: fully read-only, including for superusers."""

    list_display = (
        'user',
        'consent_type',
        'version',
        'consented',
        'timestamp',
    )
    list_filter = ('consent_type', 'consented', 'version')
    search_fields = ('user__email', 'version')


class ConsentEditReasonForm(forms.ModelForm):
    """OPTION B: change form requiring a reason for any manual edit."""

    reason = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'rows': 2}),
        help_text=(
            'Required: why is this consent record being edited by hand? '
            'Recorded in the ODPC audit log before the save is allowed.'
        ),
    )

    class Meta:
        model = UserConsent
        fields = '__all__'


class UserConsentAuditedEditAdmin(admin.ModelAdmin):
    """
    OPTION B: manual edits allowed, but every change is written to
    DataConsentLog (who, what, when, why) before the save completes.

    Timestamp and IP-derived fields stay read-only even under this option -
    those are evidence of when and where consent was actually given, and
    must not be editable regardless of the reason supplied. consent_type,
    version, consented, and withdrawn_at remain editable, since those are
    exactly the fields a genuine data-correction would need to touch.
    """

    form = ConsentEditReasonForm
    list_display = (
        'user',
        'consent_type',
        'version',
        'consented',
        'timestamp',
    )
    list_filter = ('consent_type', 'consented', 'version')
    search_fields = ('user__email', 'version')
    readonly_fields = ('timestamp', 'ip_address', 'user_agent')

    def has_delete_permission(self, request, obj=None):
        # Deleting the record destroys the compliance evidence outright,
        # with no audit trail possible - never allowed, under either option.
        return False

    def save_model(self, request, obj, form, change):
        from saccomanagement.odpc_logging import create_data_consent_log

        old_values = None
        if change:
            previous = UserConsent.objects.get(pk=obj.pk)
            old_values = {
                'consent_type': previous.consent_type,
                'version': previous.version,
                'consented': previous.consented,
                'withdrawn_at': str(previous.withdrawn_at),
            }

        # Deliberately not caught: if the audit-log write fails, the edit
        # must not silently succeed. A real deployment may want to catch
        # ConsentLogWriteError here and surface it via self.message_user /
        # forms.ValidationError instead of Django's default error page.
        create_data_consent_log(
            user=obj.user,
            accessed_by=request.user,
            data_type='USER_CONSENT_ADMIN_EDIT',
            reason=(
                f'{"Changed" if change else "Created"} via admin: '
                f'{form.cleaned_data["reason"]}. '
                f'Previous values: {old_values}.'
            ),
        )

        super().save_model(request, obj, form, change)


if ACTIVE_CONSENT_ADMIN_POLICY == 'B':
    admin.site.register(UserConsent, UserConsentAuditedEditAdmin)
else:
    admin.site.register(UserConsent, UserConsentReadOnlyAdmin)


@admin.register(DataErasureRequest)
class DataErasureRequestAdmin(admin.ModelAdmin):
    list_display = (
        'user_ref',
        'status',
        'requested_at',
        'reviewed_at',
        'reviewed_by',
    )
    list_filter = ('status', 'requested_at', 'reviewed_at')
    search_fields = ('user__email', 'user_email_anonymized')
    readonly_fields = ('requested_at', 'reviewed_at', 'completed_at')
    actions = ['approve_requests', 'reject_requests']

    def user_ref(self, obj):
        if obj.user:
            return obj.user.email
        return obj.user_email_anonymized or 'Unknown'
    user_ref.short_description = 'User'

    def approve_requests(self, request, queryset):
        from django.utils import timezone
        from saccomanagement.audit_logger import log_audit
        from notifications.utils import create_notification

        count = 0
        for erasure_request in queryset.filter(status='PENDING'):
            erasure_request.status = 'APPROVED'
            erasure_request.reviewed_at = timezone.now()
            erasure_request.reviewed_by = request.user
            erasure_request.save()

            # Perform anonymization
            user = erasure_request.user
            if user:
                user.first_name = 'Anonymized'
                user.last_name = 'User'
                user.email = f'anonymized_{user.id}@deleted.local'
                user.phone_number = None
                user.is_active = False
                user.save()

                # Revoke tokens
                from rest_framework_simplejwt.token_blacklist.models import (
                    OutstandingToken,
                )
                OutstandingToken.objects.filter(user=user).delete()

                # Notify user
                create_notification(
                    user=user,
                    title='Data Erasure Completed',
                    message='Your data has been anonymized as requested.',
                    category='SYSTEM',
                )

            # Complete request
            erasure_request.status = 'COMPLETED'
            erasure_request.completed_at = timezone.now()
            erasure_request.user_email_anonymized = f'anonymized_{erasure_request.id}'
            erasure_request.save()

            # Log
            log_audit(
                user=request.user,
                action='APPROVE',
                resource_type='DataErasureRequest',
                resource_id=str(erasure_request.id),
                old_values={'status': 'PENDING'},
                new_values={'status': 'COMPLETED'},
                request=request,
            )

            count += 1

        self.message_user(request, f'{count} erasure request(s) approved.')
    approve_requests.short_description = 'Approve selected requests'

    def reject_requests(self, request, queryset):
        from django.utils import timezone
        from saccomanagement.audit_logger import log_audit
        from notifications.utils import create_notification

        count = 0
        for erasure_request in queryset.filter(status='PENDING'):
            erasure_request.status = 'REJECTED'
            erasure_request.reviewed_at = timezone.now()
            erasure_request.reviewed_by = request.user
            erasure_request.save()

            # Log
            log_audit(
                user=request.user,
                action='REJECT',
                resource_type='DataErasureRequest',
                resource_id=str(erasure_request.id),
                old_values={'status': 'PENDING'},
                new_values={'status': 'REJECTED'},
                request=request,
            )

            # Notify user
            if erasure_request.user:
                create_notification(
                    user=erasure_request.user,
                    title='Data Erasure Request Rejected',
                    message='Your erasure request was rejected.',
                    category='SYSTEM',
                )

            count += 1

        self.message_user(request, f'{count} erasure request(s) rejected.')
    reject_requests.short_description = 'Reject selected requests'
