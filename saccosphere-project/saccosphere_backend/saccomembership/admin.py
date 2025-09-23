from django.contrib import admin
from .models import Membership

@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'sacco', 'status', 'date_joined', 'is_active')
    list_filter = ('status', 'is_active')
    search_fields = ('user__email', 'sacco__name')

