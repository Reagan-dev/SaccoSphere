import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.test import RequestFactory
from django.contrib.auth.models import AnonymousUser
from accounts.models import User
from saccomanagement.models import Role
from config.middleware import SaccoContextMiddleware

# Get test user
admin_a = User.objects.get(email='admin_a@test.com')
print(f"User: {admin_a}")
print(f"User.roles: {admin_a.roles.all()}")
print()

# Create a mock request
factory = RequestFactory()
request = factory.get('/api/v1/saccomanagement/members/', HTTP_X_SACCO_ID='2b41c95f-fb0c-44ae-ba8b-fa47ebf4abef')
request.user = admin_a

# Test middleware
middleware = SaccoContextMiddleware(lambda r: None)

print("Before middleware:")
print(f"  request.current_sacco: {getattr(request, 'current_sacco', 'NOT SET')}")
print()

# Process request
result = middleware.process_request(request)

print("After middleware:")
print(f"  request.current_sacco: {getattr(request, 'current_sacco', 'NOT SET')}")
print(f"  middleware returned: {result}")
print()

# Now test with correct sacco ID
print("=" * 80)
sacco_a_id = "237bbfa7-3a2b-4766-8a9d-a12bf73b35b8"
request2 = factory.get('/api/v1/saccomanagement/members/', HTTP_X_SACCO_ID=sacco_a_id)
request2.user = admin_a

print("Test 2: With correct Sacco ID")
print(f"Before middleware:")
print(f"  request.current_sacco: {getattr(request2, 'current_sacco', 'NOT SET')}")

result2 = middleware.process_request(request2)

print(f"After middleware:")
print(f"  request.current_sacco: {getattr(request2, 'current_sacco', 'NOT SET')}")
if getattr(request2, 'current_sacco', None):
    print(f"  Sacco name: {request2.current_sacco.name}")
print(f"  middleware returned: {result2}")
