#!/usr/bin/env python
"""
Debug script to investigate notification retrieval issue
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.contrib.auth import get_user_model
from notifications.models import Notification
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

print("=" * 70)
print("NOTIFICATIONS DEBUG - User & Queryset Check")
print("=" * 70)

# Get test user
user = User.objects.get(email='test_notification_user@example.com')
print(f"\n✓ Test User: {user}")
print(f"  - ID: {user.id}")
print(f"  - Email: {user.email}")

# Check all notifications in DB
all_notifs = Notification.objects.all()
print(f"\nAll Notifications in Database: {all_notifs.count()}")
for n in all_notifs:
    print(f"  - {n.id}")
    print(f"    User: {n.user} (ID: {n.user.id})")
    print(f"    Title: {n.title}")
    print(f"    Is Read: {n.is_read}")

# Check notifications for this specific user
user_notifs = Notification.objects.filter(user=user)
print(f"\nNotifications for test user: {user_notifs.count()}")
for n in user_notifs:
    print(f"  - {n.id}")
    print(f"    Title: {n.title}")
    print(f"    Is Read: {n.is_read}")

# Test queryset filter with exact matches
print(f"\nFilter tests:")
print(f"  - Filter by user_id={user.id}: {Notification.objects.filter(user_id=user.id).count()}")
print(f"  - Filter by user={user}: {Notification.objects.filter(user=user).count()}")

# Check JWT token
print(f"\nJWT Token Check:")
refresh = RefreshToken.for_user(user)
print(f"  - Access Token user_id claim: {refresh.access_token.get('user_id')}")
print(f"  - User ID from object: {user.id}")
print(f"  - Match: {refresh.access_token.get('user_id') == str(user.id)}")

print("\n" + "=" * 70)
