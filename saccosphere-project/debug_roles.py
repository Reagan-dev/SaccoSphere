import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from accounts.models import User
from saccomanagement.models import Role

admin_a = User.objects.get(email='admin_a@test.com')
print(f"Admin A user: {admin_a}")
print(f"Admin A ID: {admin_a.id}")
print(f"Admin A roles count: {admin_a.roles.count()}")
print(f"Admin A roles: {list(admin_a.roles.all())}")

for role in admin_a.roles.all():
    print(f"  Role ID: {role.id}")
    print(f"  Role name: {role.name}")
    print(f"  Role sacco: {role.sacco}")
    print(f"  Role sacco_id: {role.sacco_id}")
    print(f"  Sacco is null: {role.sacco_id is None}")

# Query admin roles
admin_roles = admin_a.roles.filter(
    name=Role.SACCO_ADMIN,
    sacco__isnull=False,
)
print(f"\nFiltered admin roles: {list(admin_roles)}")
print(f"Admin roles count: {admin_roles.count()}")
