from django.contrib import admin
from .models import User, Sacco, Profile

# using @admin.register decorator to register the User model with custom admin interface
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'first_name', 'last_name', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('email', 'first_name', 'last_name')
    list_filter = ('is_active', 'is_staff')
    ordering = ('email',)
    readonly_fields = ('date_joined',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important Dates', {'fields': ('date_joined',)}),
    )

@admin.register(Sacco)
class SaccoAdmin(admin.ModelAdmin):
    list_display = ('name', 'registration_number', 'email', 'verified', 'created_at')
    search_fields = ('name', 'registration_number', 'email')
    list_filter = ('verified', 'created_at')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Basic Information', {'fields': ('name', 'registration_number', 'email', 'phone_number', 'website')}),
        ('Address & Description', {'fields': ('address', 'description')}),
        ('Logo & Verification', {'fields': ('logo', 'verified')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone_number')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'phone_number')
    ordering = ('user__email',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('User', {'fields': ('user',)}),
        ('Contact Information', {'fields': ('phone_number',)}),
        ('Additional Info', {'fields': ('profile_picture', 'bio')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )
