from django.contrib import admin
from .models import Management

# using @admin.register decorator to register the Management model with custom admin interface
@admin.register(Management)
class ManagementAdmin(admin.ModelAdmin):
    list_display = ('sacco', 'management')
    search_fields = ('sacco__name', 'management')
    list_filter = ('management',)
    ordering = ('sacco__name',)
    fieldsets = (
        ('Sacco Information', {'fields': ('sacco',)}),
        ('Management Status', {'fields': ('management',)}),
    )
