#!/usr/bin/env python
"""
Detailed API test to debug queryset in view
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from notifications.models import Notification
from notifications.views import NotificationListView
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

print("=" * 70)
print("NOTIFICATIONS API VIEW DEBUG")
print("=" * 70)

# Get test user
user = User.objects.get(email='test_notification_user@example.com')
print(f"\n✓ Test User: {user.email} (ID: {user.id})")

# Check raw query
notifs = Notification.objects.filter(user=user)
print(f"✓ Raw queryset filter: {notifs.count()} notifications")

# Create a mock request
factory = RequestFactory()
request = factory.get('/api/v1/notifications/')
request.user = user

# Test the view's get_queryset method
view = NotificationListView()
view.request = request
view.format_kwarg = None

try:
    queryset = view.get_queryset()
    print(f"\n✓ View.get_queryset() returned: {queryset.count()} notifications")
    
    # Get serialized data
    serializer_class = view.get_serializer_class()
    serializer = serializer_class(queryset, many=True)
    print(f"✓ Serialized data count: {len(serializer.data)}")
    
    if serializer.data:
        print(f"  First item: {serializer.data[0]}")
    else:
        print("  ⚠ Serializer returned empty list!")
        
except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Test with actual API call
print(f"\n\nTesting with HTTP request:")
import requests
from rest_framework_simplejwt.tokens import RefreshToken

refresh = RefreshToken.for_user(user)
headers = {
    'Authorization': f'Bearer {refresh.access_token}',
    'Content-Type': 'application/json',
}

response = requests.get('http://127.0.0.1:8000/api/v1/notifications/', headers=headers)
print(f"Status: {response.status_code}")
data = response.json()

print(f"\nFull Response:")
import json
print(json.dumps(data, indent=2))

print("\n" + "=" * 70)
