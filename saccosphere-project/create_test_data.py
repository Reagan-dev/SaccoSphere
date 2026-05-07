import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from accounts.models import User, Sacco
from saccomanagement.models import Role
from saccomembership.models import Membership

# Create two test SACCOs
sacco_a, _ = Sacco.objects.get_or_create(
    name='Sacco A',
    defaults={
        'sector': 'EDUCATION',
        'county': 'Nairobi',
        'membership_type': 'OPEN',
        'is_publicly_listed': True,
        'is_active': True,
    }
)

sacco_b, _ = Sacco.objects.get_or_create(
    name='Sacco B',
    defaults={
        'sector': 'HEALTHCARE',
        'county': 'Mombasa',
        'membership_type': 'OPEN',
        'is_publicly_listed': True,
        'is_active': True,
    }
)

# Create SACCO_ADMIN for Sacco A
admin_a, created = User.objects.get_or_create(
    email='admin_a@test.com',
    defaults={
        'first_name': 'Admin',
        'last_name': 'A',
    }
)
admin_a.set_password('testpass123')
admin_a.save()

# Assign SACCO_ADMIN role to admin_a for Sacco A
Role.objects.get_or_create(
    user=admin_a,
    sacco=sacco_a,
    name=Role.SACCO_ADMIN,
)

# Create MEMBER user
member_user, created = User.objects.get_or_create(
    email='member@test.com',
    defaults={
        'first_name': 'Member',
        'last_name': 'User',
    }
)
member_user.set_password('testpass123')
member_user.save()

# Add member to Sacco A
Membership.objects.get_or_create(
    user=member_user,
    sacco=sacco_a,
    defaults={
        'status': 'APPROVED',
    }
)

# Assign MEMBER role
Role.objects.get_or_create(
    user=member_user,
    sacco=sacco_a,
    name=Role.MEMBER,
)

print('✓ Test data created:')
print(f'  Sacco A: {sacco_a.id}')
print(f'  Sacco B: {sacco_b.id}')
print(f'  Admin A email: admin_a@test.com (SACCO_ADMIN for Sacco A)')
print(f'  Member email: member@test.com (MEMBER in Sacco A)')
