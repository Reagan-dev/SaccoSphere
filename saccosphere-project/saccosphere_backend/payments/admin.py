from django.contrib import admin
from .models import PaymentProvider, Transaction, Callback

# using @admin.register decorator to register the PaymentProvider model with custom admin interface
@admin.register(PaymentProvider)
class PaymentProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "provider_code", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "provider_code")
    ordering = ("name",)
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Provider Information", {"fields": ("name", "provider_code", "is_active")}),
        ("API Credentials", {"fields": ("api_key", "api_secret", "callback_url")}),
        ("Timestamps", {"fields": ("created_at",)}),
    )

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "provider", "amount", "currency", "status", "created_at", "updated_at")
    list_filter = ("status", "provider", "currency", "created_at")
    search_fields = ("reference", "provider_reference", "user__email", "user__username")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Transaction Information", {"fields": ("user", "provider", "amount", "currency", "status")}),
        ("References", {"fields": ("reference", "provider_reference")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

@admin.register(Callback)
class CallbackAdmin(admin.ModelAdmin):
    list_display = ("transaction", "provider", "processed", "received_at")
    list_filter = ("processed", "provider", "received_at")
    search_fields = ("transaction__reference", "provider__name")
    ordering = ("-received_at",)
    readonly_fields = ("received_at",)
    fieldsets = (
        ("Callback Information", {"fields": ("transaction", "provider", "processed")}),
        ("Timestamps", {"fields": ("received_at",)}),
    )
