from django.contrib import admin

from .models import LedgerEntry


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        'membership',
        'entry_type',
        'category',
        'amount',
        'reference',
        'balance_after',
        'created_at',
    )
    list_filter = ('entry_type', 'category', 'created_at')
    search_fields = (
        'membership__user__email',
        'membership__member_number',
        'membership__sacco__name',
        'reference',
        'description',
        'transaction__reference',
    )
    autocomplete_fields = ('membership', 'transaction')
    readonly_fields = (
        'id',
        'membership',
        'entry_type',
        'category',
        'amount',
        'reference',
        'description',
        'balance_after',
        'transaction',
        'created_at',
    )
    list_select_related = ('membership', 'transaction')
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'membership',
                    'entry_type',
                    'category',
                    'amount',
                    'balance_after',
                    'reference',
                ),
            },
        ),
        (
            'Transaction details',
            {
                'fields': ('transaction', 'description'),
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

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
