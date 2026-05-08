#!/usr/bin/env python
"""
Verification script to test:
1. No import errors
2. IsSaccoAdmin has correct docstring and message attribute
3. Each class can be instantiated without error
"""

import os
import sys
import django
from django.conf import settings

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

print("=" * 80)
print("VERIFICATION: Testing import errors and class instantiation")
print("=" * 80)

# Test 1: Import all permission classes
print("\n1. Testing imports...")
try:
    from accounts.permissions import (
        IsKYCVerified,
        IsPhoneVerified,
        IsSaccoAdmin,
        IsSuperAdmin,
        IsSaccoAdminOrSuperAdmin,
        IsMemberOfSacco,
        IsOwnerOrSaccoAdmin,
        IsEligibleGuarantor,
        GuarantorCapacityCheck,
    )
    print("OK All permission classes imported successfully")
except ImportError as e:
    print(f"FAIL Import error: {e}")
    sys.exit(1)

# Test 2: Verify IsSaccoAdmin has correct docstring and message
print("\n2. Testing IsSaccoAdmin attributes...")
try:
    # Check docstring
    if IsSaccoAdmin.__doc__:
        docstring = IsSaccoAdmin.__doc__.strip()
        if "Allow access only to users with SACCO_ADMIN role" in docstring:
            print("OK IsSaccoAdmin has correct docstring")
        else:
            print(f"FAIL IsSaccoAdmin docstring incorrect: {docstring}")
    else:
        print("FAIL IsSaccoAdmin has no docstring")
    
    # Check message attribute
    if hasattr(IsSaccoAdmin, 'message'):
        message = IsSaccoAdmin.message
        if message == 'You must be a SACCO admin to perform this action.':
            print("OK IsSaccoAdmin has correct message attribute")
        else:
            print(f"FAIL IsSaccoAdmin message incorrect: {message}")
    else:
        print("FAIL IsSaccoAdmin has no message attribute")
        
except Exception as e:
    print(f"Error checking IsSaccoAdmin attributes: {e}")

# Test 3: Test class instantiation
print("\n3. Testing class instantiation...")
classes_to_test = [
    IsKYCVerified,
    IsPhoneVerified,
    IsSaccoAdmin,
    IsSuperAdmin,
    IsSaccoAdminOrSuperAdmin,
    IsMemberOfSacco,
    IsOwnerOrSaccoAdmin,
    IsEligibleGuarantor,
    GuarantorCapacityCheck,
]

for cls in classes_to_test:
    try:
        instance = cls()
        print(f"OK {cls.__name__} instantiated successfully")
    except Exception as e:
        print(f"FAIL {cls.__name__} instantiation failed: {e}")

# Test 4: Test middleware classes
print("\n4. Testing middleware classes...")
try:
    from config.middleware import (
        RequestCorrelationMiddleware,
        LoggingMiddleware,
        SaccoContextMiddleware,
    )
    
    middleware_classes = [
        RequestCorrelationMiddleware,
        LoggingMiddleware,
        SaccoContextMiddleware,
    ]
    
    for cls in middleware_classes:
        try:
            instance = cls()
            print(f"OK {cls.__name__} instantiated successfully")
        except Exception as e:
            print(f"FAIL {cls.__name__} instantiation failed: {e}")
            
except ImportError as e:
    print(f"FAIL Middleware import error: {e}")

# Test 5: Test pagination classes
print("\n5. Testing pagination classes...")
try:
    from config.pagination import (
        SaccoSpherePagination,
        FinancialPagination,
        NotificationPagination,
    )
    
    pagination_classes = [
        SaccoSpherePagination,
        FinancialPagination,
        NotificationPagination,
    ]
    
    for cls in pagination_classes:
        try:
            instance = cls()
            print(f"OK {cls.__name__} instantiated successfully")
        except Exception as e:
            print(f"FAIL {cls.__name__} instantiation failed: {e}")
            
except ImportError as e:
    print(f"FAIL Pagination import error: {e}")

# Test 6: Test response mixin
print("\n6. Testing response mixin...")
try:
    from config.response import StandardResponseMixin
    
    try:
        instance = StandardResponseMixin()
        print("OK StandardResponseMixin instantiated successfully")
    except Exception as e:
        print(f"FAIL StandardResponseMixin instantiation failed: {e}")
        
except ImportError as e:
    print(f"FAIL Response mixin import error: {e}")

print("\n" + "=" * 80)
print("VERIFICATION COMPLETE")
print("=" * 80)
