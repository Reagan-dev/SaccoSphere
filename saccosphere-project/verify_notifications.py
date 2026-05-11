#!/usr/bin/env python
"""
Verification script for notifications API endpoints
Tests:
1. GET /api/v1/notifications/ - should return paginated list (200)
2. POST /api/v1/notifications/read-all/ - should mark all as read
3. create_notification() function - should create DB record without crashing
"""
import os
import sys
import django
import requests
from decimal import Decimal

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.contrib.auth import get_user_model
from notifications.models import Notification
from notifications.utils import create_notification
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

BASE_URL = 'http://127.0.0.1:8000/api/v1'

def get_tokens(user):
    """Get JWT tokens for user"""
    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }

def test_notifications_api():
    """Test notifications API endpoints"""
    print("=" * 70)
    print("NOTIFICATIONS API VERIFICATION")
    print("=" * 70)
    
    # Create test user
    print("\n1. Creating test user...")
    user, created = User.objects.get_or_create(
        email='test_notification_user@example.com',
        defaults={
            'first_name': 'Test',
            'last_name': 'User',
        }
    )
    if created:
        user.set_password('testpass123')
        user.save()
        print(f"   ✓ User created: {user.email}")
    else:
        print(f"   ✓ User exists: {user.email}")
    
    # Get JWT token
    tokens = get_tokens(user)
    headers = {
        'Authorization': f'Bearer {tokens["access"]}',
        'Content-Type': 'application/json',
    }
    
    # Clean up old notifications
    Notification.objects.filter(user=user).delete()
    print(f"   ✓ Cleaned up old notifications for user")
    
    # Test 1: GET /api/v1/notifications/ - Empty list
    print("\n2. Testing GET /api/v1/notifications/ (empty)...")
    response = requests.get(f'{BASE_URL}/notifications/', headers=headers)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ Response structure: {list(data.keys())}")
        if 'results' in data:
            print(f"   ✓ Paginated list: {len(data['results'])} results")
        else:
            print(f"   ⚠ Not properly paginated: {data}")
    else:
        print(f"   ✗ Unexpected status: {response.status_code}")
        print(f"     Response: {response.text}")
    
    # Test 2: create_notification() function
    print("\n3. Testing create_notification() function...")
    try:
        notification = create_notification(
            user=user,
            title='Test Notification',
            message='This is a test notification',
            category='SYSTEM',
        )
        print(f"   ✓ Notification created successfully")
        print(f"     - ID: {notification.id}")
        print(f"     - Title: {notification.title}")
        print(f"     - Message: {notification.message}")
        print(f"     - Is Read: {notification.is_read}")
        
        # Verify in database
        count = Notification.objects.filter(user=user).count()
        print(f"   ✓ Verified in database: {count} notification(s) for user")
    except Exception as e:
        print(f"   ✗ Error creating notification: {e}")
        import traceback
        traceback.print_exc()
    
    # Test 3: GET /api/v1/notifications/ - With data
    print("\n4. Testing GET /api/v1/notifications/ (with data)...")
    response = requests.get(f'{BASE_URL}/notifications/', headers=headers)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        results_count = len(data.get('results', []))
        print(f"   ✓ Paginated list: {results_count} result(s)")
        if results_count > 0:
            first_notif = data['results'][0]
            print(f"     - First notification:")
            print(f"       * Title: {first_notif.get('title')}")
            print(f"       * Is Read: {first_notif.get('is_read')}")
    else:
        print(f"   ✗ Unexpected status: {response.status_code}")
    
    # Test 4: POST /api/v1/notifications/read-all/
    print("\n5. Testing POST /api/v1/notifications/read-all/...")
    response = requests.post(f'{BASE_URL}/notifications/read-all/', headers=headers)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print(f"   ✓ Mark all as read successful")
        data = response.json()
        if isinstance(data, dict):
            print(f"     Response: {data}")
    else:
        print(f"   ✗ Unexpected status: {response.status_code}")
        print(f"     Response: {response.text}")
    
    # Verify all are marked read
    print("\n6. Verifying all notifications are marked as read...")
    response = requests.get(f'{BASE_URL}/notifications/', headers=headers)
    if response.status_code == 200:
        data = response.json()
        results = data.get('results', [])
        all_read = all(r.get('is_read') for r in results)
        unread_count = sum(1 for r in results if not r.get('is_read'))
        
        if all_read:
            print(f"   ✓ All {len(results)} notifications are read")
        else:
            print(f"   ⚠ {unread_count} unread notification(s) found")
            for i, notif in enumerate(results):
                if not notif.get('is_read'):
                    print(f"     - Notification {i+1}: is_read={notif.get('is_read')}")
    
    print("\n" + "=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)

if __name__ == '__main__':
    test_notifications_api()
