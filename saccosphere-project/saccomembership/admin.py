from django.contrib import admin

from .models import (
    MemberFieldData,
    Membership,
    MembershipDocument,
    SaccoApplication,
    SaccoFieldDefinition,
)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'sacco',
        'member_number',
        'status',
        'application_date',
        'approved_date',
    )
    list_filter = ('status', 'sacco', 'application_date')
    search_fields = (
        'user__email',
        'user__first_name',
        'user__last_name',
        'sacco__name',
        'member_number',
    )
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SaccoFieldDefinition)
class SaccoFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        'label',
        'sacco',
        'field_type',
        'is_required',
        'display_order',
    )
    list_filter = ('field_type', 'is_required', 'sacco')
    search_fields = ('label', 'sacco__name')
    ordering = ('sacco__name', 'display_order')


@admin.register(MemberFieldData)
class MemberFieldDataAdmin(admin.ModelAdmin):
    list_display = ('membership', 'field', 'value', 'file_value')
    list_filter = ('field__field_type', 'field__sacco')
    search_fields = (
        'membership__user__email',
        'membership__sacco__name',
        'field__label',
        'value',
    )


@admin.register(SaccoApplication)
class SaccoApplicationAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'sacco',
        'application_type',
        'status',
        'registration_fee_paid',
        'submitted_at',
        'created_at',
    )
    list_filter = (
        'application_type',
        'status',
        'registration_fee_paid',
        'sacco',
        'created_at',
    )
    search_fields = (
        'user__email',
        'user__first_name',
        'user__last_name',
        'sacco__name',
        'employer_name',
    )
    readonly_fields = ('created_at', 'updated_at')


@admin.register(MembershipDocument)
class MembershipDocumentAdmin(admin.ModelAdmin):
    list_display = (
        'application',
        'document_type',
        'file_name',
        'is_verified',
        'uploaded_at',
    )
    list_filter = ('document_type', 'is_verified', 'uploaded_at')
    search_fields = (
        'application__user__email',
        'application__sacco__name',
        'file_name',
        'notes',
    )
    autocomplete_fields = ('application',)
    readonly_fields = ('uploaded_at',)
    list_select_related = ('application',)
    ordering = ('-uploaded_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'application',
                    'document_type',
                    'is_verified',
                ),
            },
        ),
        (
            'File details',
            {
                'classes': ('collapse',),
                'fields': (
                    'file',
                    'file_name',
                    'file_size_bytes',
                    'notes',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('uploaded_at',),
            },
        ),
    )
