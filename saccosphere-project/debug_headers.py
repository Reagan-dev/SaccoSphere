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
    exit(1)

access_token_admin_a = response.json()["access"]
print(f"[OK] Got admin token")

# Debug: Check what headers are being sent
import logging
logging.basicConfig(level=logging.DEBUG)
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
requests_log.propagate = True

headers = {
    "Authorization": f"Bearer {access_token_admin_a}",
    "X-Sacco-ID": sacco_b_id
}

print(f"\nHeaders being sent: {headers}")
print(f"X-Sacco-ID value: {headers['X-Sacco-ID']}")

print("\n" + "=" * 80)
print("TEST 1: Accessing with WRONG Sacco ID")
print("=" * 80)

response = requests.get(
    f"{BASE_URL}/saccomanagement/members/",
    headers=headers,
)

print(f"Status: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")
print(f"\nExpected message: 'You do not have access to this SACCO'")
print(f"Actual message: '{response.json().get('message')}'")
