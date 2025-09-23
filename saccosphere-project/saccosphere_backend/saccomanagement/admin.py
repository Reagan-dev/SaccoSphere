from django.contrib import admin
from .models import Management

@admin.register(Management)
class ManagementAdmin(admin.ModelAdmin):
    list_display = ('id', 'sacco', 'management')
    list_filter = ('management',)
