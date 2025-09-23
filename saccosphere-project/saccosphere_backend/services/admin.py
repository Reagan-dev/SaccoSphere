from django.contrib import admin
from .models import Service, Saving, Loan, Insurance

# using @admin.register decorator to register the service model with custom admin interface
@admin.register(Service)
class serviceAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'created_at')
    search_fields = ('name',)
    list_filter = ('created_at',)
    ordering = ('name',)
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Service Information', {'fields': ('name', 'description')}),
        ('Timestamps', {'fields': ('created_at',)}),
    )

@admin.register(Saving)
class savingAdmin(admin.ModelAdmin):
    list_display = ("member", "transaction_type", "amount", "service", "created_at")
    list_filter = ("transaction_type", "service")
    search_fields = ("member__email", "member__username", "service__name")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Saving Information", {"fields": ("member", "transaction_type", "amount", "service")}),
        ("Timestamps", {"fields": ("created_at",)}),
    )

@admin.register(Loan)
class loanAdmin(admin.ModelAdmin):
    list_display = ("member", "amount", "interest_rate", "duration_months", "status", "service", "created_at")
    list_filter = ("status", "service")
    search_fields = ("member__email", "member__username", "service__name")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Loan Information", {"fields": ("member", "amount", "interest_rate", "duration_months", "status", "service")}),
        ("Timestamps", {"fields": ("created_at",)}),
    )

@admin.register(Insurance)
class insuranceAdmin(admin.ModelAdmin):
    list_display = ("policy_number", "member", "coverage_amount", "premium", "service", "start_date", "end_date")
    list_filter = ("service", "start_date", "end_date")
    search_fields = ("policy_number", "member__email", "member__username", "service__name")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Insurance Information", {"fields": ("policy_number", "member", "coverage_amount", "premium", "service", "start_date", "end_date")}),
        ("Timestamps", {"fields": ("created_at",)}),
    )
