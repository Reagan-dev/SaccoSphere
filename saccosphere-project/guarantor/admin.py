from django.contrib import admin

from .models import ExternalGuarantor


@admin.register(ExternalGuarantor)
class ExternalGuarantorAdmin(admin.ModelAdmin):
    list_display = (
        'full_name',
        'phone_number',
        'sacco',
        'loan_short_id',
        'guarantee_amount',
        'status',
        'created_at',
    )
    list_filter = ('status', 'employment_status', 'sacco', 'created_at')
    search_fields = (
        'full_name',
        'phone_number',
        'id_number',
        'loan__id',
        'requested_by__email',
    )
    readonly_fields = (
        'response_token',
        'response_token_expires_at',
        'created_at',
        'updated_at',
    )

    @admin.display(description='Loan')
    def loan_short_id(self, obj):
        return str(obj.loan_id)[:8]
