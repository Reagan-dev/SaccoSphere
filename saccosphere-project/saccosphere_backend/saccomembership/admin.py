from django.contrib import admin
from .models import Membership

# using @admin.register decorator to register the Membership model with custom admin interface
@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'sacco', 'status', 'is_active', 'date_joined')
    list_filter = ('status', 'is_active', 'date_joined')
    search_fields = ('user__email', 'sacco__name')
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined', 'id')
    fieldseets = (
        ('Membership Info', {'fields': ('user', 'sacco', 'status', 'is_active')}),
        ('Timestamps', {'fields': ('date_joined', 'id')}),
    )
