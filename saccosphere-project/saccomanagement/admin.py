from django.contrib import admin

from .models import (
    ComplianceFlag,
    DataConsentLog,
    ImportJob,
    MemberImportJob,
    Role,
    RolePermission,
    SMSCampaign,
    SMSCampaignRecipient,
    SystemAuditLog,
)


class ViewOnlyAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class NoChangeAdminMixin(ViewOnlyAdminMixin):
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SystemAuditLog)
class SystemAuditLogAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'user',
        'action',
        'resource_type',
        'resource_id',
        'ip_address',
        'created_at',
    )
    list_filter = ('action', 'resource_type', 'created_at')
    search_fields = (
        'user__email',
        'action',
        'resource_type',
        'resource_id',
        'ip_address',
    )
    autocomplete_fields = ('user',)
    readonly_fields = (
        'id',
        'user',
        'action',
        'resource_type',
        'resource_id',
        'old_values',
        'new_values',
        'ip_address',
        'user_agent',
        'created_at',
    )
    list_select_related = ('user',)
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'user',
                    'action',
                    'resource_type',
                    'resource_id',
                    'ip_address',
                ),
            },
        ),
        (
            'Change details',
            {
                'classes': ('collapse',),
                'fields': ('old_values', 'new_values', 'user_agent'),
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


@admin.register(DataConsentLog)
class DataConsentLogAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'user',
        'accessed_by',
        'data_type',
        'reason',
        'timestamp',
    )
    list_filter = ('data_type', 'timestamp')
    search_fields = (
        'user__email',
        'accessed_by__email',
        'data_type',
        'reason',
    )
    autocomplete_fields = ('user', 'accessed_by')
    readonly_fields = (
        'id',
        'user',
        'accessed_by',
        'data_type',
        'reason',
        'timestamp',
    )
    list_select_related = ('user', 'accessed_by')
    list_per_page = 50
    ordering = ('-timestamp',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'user',
                    'accessed_by',
                    'data_type',
                    'reason',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('id', 'timestamp'),
            },
        ),
    )


@admin.register(SMSCampaign)
class SMSCampaignAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'sacco',
        'created_by',
        'status',
        'total_recipients',
        'sent_count',
        'failed_count',
        'created_at',
    )
    list_filter = ('status', 'sacco', 'created_at')
    search_fields = (
        'sacco__name',
        'created_by__email',
        'message',
    )
    autocomplete_fields = ('sacco', 'created_by')
    readonly_fields = (
        'id',
        'sacco',
        'created_by',
        'message',
        'audience_filter',
        'status',
        'total_recipients',
        'sent_count',
        'failed_count',
        'created_at',
    )
    list_select_related = ('sacco', 'created_by')
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'created_by',
                    'status',
                    'message',
                ),
            },
        ),
        (
            'Delivery details',
            {
                'fields': (
                    'total_recipients',
                    'sent_count',
                    'failed_count',
                ),
            },
        ),
        (
            'Audience and audit',
            {
                'classes': ('collapse',),
                'fields': ('audience_filter', 'id', 'created_at'),
            },
        ),
    )


@admin.register(SMSCampaignRecipient)
class SMSCampaignRecipientAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'campaign',
        'membership',
        'phone_number',
        'status',
        'sent_at',
    )
    list_filter = ('status', 'campaign__sacco', 'sent_at')
    search_fields = (
        'campaign__sacco__name',
        'membership__user__email',
        'membership__member_number',
        'phone_number',
        'error_message',
    )
    autocomplete_fields = ('campaign', 'membership')
    readonly_fields = (
        'id',
        'campaign',
        'membership',
        'phone_number',
        'status',
        'sent_at',
        'error_message',
    )
    list_select_related = ('campaign', 'membership')
    list_per_page = 50
    ordering = ('-sent_at', 'id')
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'campaign',
                    'membership',
                    'phone_number',
                    'status',
                    'sent_at',
                ),
            },
        ),
        (
            'Failure details',
            {
                'classes': ('collapse',),
                'fields': ('error_message', 'id'),
            },
        ),
    )


@admin.register(Role)
class RoleAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    # Role assignments must go through role_views so validation is preserved.
    list_display = ('user', 'sacco', 'name', 'created_at')
    list_filter = ('name', 'sacco', 'created_at')
    search_fields = (
        'user__email',
        'user__first_name',
        'user__last_name',
        'sacco__name',
    )
    autocomplete_fields = ('user', 'sacco')
    readonly_fields = ('id', 'user', 'sacco', 'name', 'created_at')
    list_select_related = ('user', 'sacco')
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'user',
                    'sacco',
                    'name',
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


@admin.register(RolePermission)
class RolePermissionAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    # Permission edits are access-control changes and need dedicated review.
    list_display = (
        'role',
        'resource',
        'can_create',
        'can_read',
        'can_update',
        'can_delete',
    )
    list_filter = (
        'role__sacco',
        'can_create',
        'can_read',
        'can_update',
        'can_delete',
    )
    search_fields = (
        'role__user__email',
        'role__sacco__name',
        'resource',
    )
    autocomplete_fields = ('role',)
    readonly_fields = (
        'id',
        'role',
        'resource',
        'can_create',
        'can_read',
        'can_update',
        'can_delete',
    )
    list_select_related = ('role',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'role',
                    'resource',
                    'can_create',
                    'can_read',
                    'can_update',
                    'can_delete',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('id',),
            },
        ),
    )


@admin.register(ImportJob)
class ImportJobAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'sacco',
        'imported_by',
        'status',
        'total_rows',
        'success_count',
        'fail_count',
        'created_at',
    )
    list_filter = ('status', 'sacco', 'created_at')
    search_fields = (
        'sacco__name',
        'imported_by__email',
        '=id',
    )
    autocomplete_fields = ('sacco', 'imported_by')
    readonly_fields = (
        'id',
        'sacco',
        'imported_by',
        'file',
        'status',
        'total_rows',
        'success_count',
        'fail_count',
        'error_summary',
        'created_at',
        'completed_at',
    )
    list_select_related = ('sacco', 'imported_by')
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'imported_by',
                    'file',
                    'status',
                ),
            },
        ),
        (
            'Results',
            {
                'fields': (
                    'total_rows',
                    'success_count',
                    'fail_count',
                    'completed_at',
                ),
            },
        ),
        (
            'Errors and audit',
            {
                'classes': ('collapse',),
                'fields': ('error_summary', 'id', 'created_at'),
            },
        ),
    )


@admin.register(MemberImportJob)
class MemberImportJobAdmin(NoChangeAdminMixin, admin.ModelAdmin):
    list_display = (
        'sacco',
        'created_by',
        'file_name',
        'status',
        'progress_pct',
        'success_rows',
        'error_rows',
        'created_at',
    )
    list_filter = ('status', 'sacco', 'created_at')
    search_fields = (
        'sacco__name',
        'created_by__email',
        'file_name',
        '=id',
    )
    autocomplete_fields = ('sacco', 'created_by')
    readonly_fields = (
        'id',
        'sacco',
        'created_by',
        'file_name',
        'status',
        'total_rows',
        'processed_rows',
        'success_rows',
        'error_rows',
        'errors',
        'started_at',
        'completed_at',
        'created_at',
    )
    list_select_related = ('sacco', 'created_by')
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'created_by',
                    'file_name',
                    'status',
                ),
            },
        ),
        (
            'Progress',
            {
                'fields': (
                    'total_rows',
                    'processed_rows',
                    'success_rows',
                    'error_rows',
                ),
            },
        ),
        (
            'Errors and audit',
            {
                'classes': ('collapse',),
                'fields': (
                    'errors',
                    'started_at',
                    'completed_at',
                    'id',
                    'created_at',
                ),
            },
        ),
    )


@admin.register(ComplianceFlag)
class ComplianceFlagAdmin(admin.ModelAdmin):
    list_display = (
        'sacco',
        'flag_type',
        'severity',
        'status',
        'resolved_by',
        'created_at',
    )
    list_filter = (
        'flag_type',
        'severity',
        'status',
        'sacco',
        'created_at',
    )
    search_fields = (
        'sacco__name',
        'description',
        'resolved_by__email',
    )
    autocomplete_fields = ('sacco', 'resolved_by')
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('sacco', 'resolved_by')
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'flag_type',
                    'severity',
                    'status',
                    'description',
                ),
            },
        ),
        (
            'Resolution',
            {
                'fields': ('resolved_at', 'resolved_by'),
            },
        ),
        (
            'Metadata and audit',
            {
                'classes': ('collapse',),
                'fields': ('metadata', 'created_at', 'updated_at'),
            },
        ),
    )
