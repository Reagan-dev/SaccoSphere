import requests
import json

BASE_URL = "http://localhost:8000/api/v1"

# Test data
sacco_a_id = "237bbfa7-3a2b-4766-8a9d-a12bf73b35b8"
sacco_b_id = "2b41c95f-fb0c-44ae-ba8b-fa47ebf4abef"

# Get fresh tokens
print("Getting fresh tokens...")
response = requests.post(
    f"{BASE_URL}/accounts/token/",
    json={"email": "admin_a@test.com", "password": "testpass123"}
)
if response.status_code != 200:
    print(f"Failed to get admin token: {response.status_code}")
    print(response.json())
    exit(1)

access_token_admin_a = response.json()["access"]
print(f"Admin A token: {access_token_admin_a[:20]}...")

headers = {
    "Authorization": f"Bearer {access_token_admin_a}",
    "X-Sacco-ID": sacco_b_id  # Wrong SACCO ID
}

print("=" * 80)
print("TEST 1: Admin accessing with wrong Sacco ID (should return 403)")
print("=" * 80)

response = requests.get(
    f"{BASE_URL}/saccomanagement/members/",
    headers=headers
)

print(f"Status Code: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")
print()

# Test 2: Admin accessing with correct Sacco ID (should return 200)
print("=" * 80)
print("TEST 2: Admin accessing with correct Sacco ID (should return 200)")
print("=" * 80)

headers["X-Sacco-ID"] = sacco_a_id  # Correct SACCO ID

response = requests.get(
    f"{BASE_URL}/saccomanagement/members/",
    headers=headers
)

print(f"Status Code: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")
print()

# Test 3: Member accessing members endpoint (should return 403 - not admin)
print("=" * 80)
print("TEST 3: Member accessing members endpoint (should return 403 - not admin)")
print("=" * 80)

# First, login as member
response = requests.post(
    f"{BASE_URL}/accounts/token/",
    json={"email": "member@test.com", "password": "testpass123"}
)

if response.status_code == 200:
    member_token = response.json()["access"]
    
    headers = {
        "Authorization": f"Bearer {member_token}",
        "X-Sacco-ID": sacco_a_id  # Same SACCO where member is
    }
    
    response = requests.get(
        f"{BASE_URL}/saccomanagement/members/",
        headers=headers
    )
    
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
else:
    print(f"Failed to login member: {response.status_code}")
    print(response.json())
