from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import KYCVerification, OTPToken, Sacco, User, UserConsent


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

    @admin.display(description='User email', ordering='user__email')
    def user_email(self, obj):
        return obj.user.email


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


@admin.register(UserConsent)
class UserConsentAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'consent_type',
        'version',
        'consented',
        'timestamp',
    )
    list_filter = ('consent_type', 'consented', 'version')
    search_fields = ('user__email', 'version')
