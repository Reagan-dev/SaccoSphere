from django.contrib import admin
from .models import Membership, SaccoField, MembershipFieldData

# using @admin.register decorator to register the Membership model with custom admin interface
@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'sacco', 'status', 'date_joined', 'is_active')
    list_filter = ('status', 'is_active', 'date_joined')
    search_fields = ('user__email', 'sacco__name')
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined', 'id')
    fieldsets = (
        ('Membership Info', {'fields': ('user', 'sacco', 'status', 'is_active')}),
        ('Timestamps', {'fields': ('date_joined', 'id')}),
    )

@admin.register(SaccoField)
class SaccoFieldAdmin(admin.ModelAdmin):
    list_display = (
        "field_label",
        "field_key",
        "field_type",
        "sacco",
        "required",
        "order",
    )
    list_filter = ("sacco", "field_type")
    search_fields = ("field_label", "field_key")
    ordering = ("order",)


@admin.register(MembershipFieldData)
class MembershipFieldDataAdmin(admin.ModelAdmin):
    list_display = (
        "membership",
        "sacco_field",
        "value",
    )
    list_filter = ("sacco_field",)