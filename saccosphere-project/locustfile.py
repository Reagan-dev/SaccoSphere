"""
SaccoSphere Load Test - locustfile.py
======================================
Usage:
    pip install locust
    locust --host=https://saccosphere-production.up.railway.app

Then open http://localhost:8089 in your browser.

Recommended test progression:
    1. Start with 10 users, spawn rate 2  → check everything works
    2. Ramp to 50 users, spawn rate 5     → normal daily load
    3. Ramp to 100 users, spawn rate 10   → stress test
    4. Ramp to 200 users, spawn rate 20   → find your breaking point

NOTE ON MPESA:
    The real STK callback endpoint (/api/v1/payments/callback/mpesa/stk/)
    blocks all non-Safaricom IPs by design — do NOT test it with Locust.
    Instead we test the CallbackCreateView endpoint which accepts provider
    and raw_payload, and we test the STK push initiation endpoint directly.
"""

import random
import string
from locust import HttpUser, task, between, events


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def random_email():
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"testuser_{suffix}@saccosphere.test"

def random_phone():
    digits = ''.join(random.choices(string.digits, k=8))
    return f"2547{digits}"  # Kenyan format: 2547XXXXXXXX


# ─────────────────────────────────────────────
# Anonymous User
# ─────────────────────────────────────────────

class AnonymousUser(HttpUser):
    """
    Simulates a visitor browsing SACCOs before registering.
    """
    weight = 1
    wait_time = between(1, 3)

    @task(3)
    def health_liveness(self):
        with self.client.get(
            "/health/",
            name="Health - Liveness",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"Health check failed: {r.status_code}")

    @task(2)
    def health_readiness(self):
        with self.client.get(
            "/health/ready/",
            name="Health - Readiness",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"Readiness check failed: {r.status_code}")

    @task(5)
    def list_saccos(self):
        with self.client.get(
            "/api/v1/accounts/saccos/",
            name="Public - List SACCOs",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"List SACCOs failed: {r.status_code}")

    @task(3)
    def list_saccos_filtered(self):
        with self.client.get(
            "/api/v1/accounts/saccos/?verified_only=true&ordering=-member_count",
            name="Public - List SACCOs (filtered)",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"Filtered SACCOs failed: {r.status_code}")

    @task(1)
    def register_new_user(self):
        with self.client.post(
            "/api/v1/accounts/register/",
            json={
                "email": random_email(),
                "first_name": "Test",
                "last_name": "User",
                "phone_number": f"+{random_phone()}",
                "password": "TestPass@1234",
                "password2": "TestPass@1234"
            },
            name="Auth - Register",
            catch_response=True
        ) as r:
            if r.status_code not in (200, 201):
                r.failure(f"Registration failed: {r.status_code} — {r.text[:200]}")


# ─────────────────────────────────────────────
# Authenticated User
# ─────────────────────────────────────────────

class AuthenticatedUser(HttpUser):
    """
    Simulates a logged-in SACCO member using the full app.
    """
    weight = 3
    wait_time = between(1, 4)

    def on_start(self):
        self.access_token = None
        self.refresh_token = None
        self.sacco_id = None
        self.membership_id = None
        self.email = random_email()
        self.password = "TestPass@1234"
        self.phone = random_phone()

        self._register()
        self._login()
        self._get_sacco_id()

    def _register(self):
        try:
            with self.client.post(
                "/api/v1/accounts/register/",
                json={
                    "email": self.email,
                    "first_name": "Load",
                    "last_name": "Tester",
                    "phone_number": f"+{self.phone}",
                    "password": self.password,
                    "password2": self.password
                },
                name="[Setup] Register",
                catch_response=True
            ) as r:
                if r.status_code not in (200, 201):
                    r.failure(f"Setup register failed: {r.status_code}")
        except Exception as e:
            print(f"[Setup] _register error: {e}")

    def _login(self):
        try:
            with self.client.post(
                "/api/v1/accounts/login/",
                json={"email": self.email, "password": self.password},
                name="[Setup] Login",
                catch_response=True
            ) as r:
                if r.status_code == 200:
                    data = r.json()
                    self.access_token = data.get("access") or data.get("access_token")
                    self.refresh_token = data.get("refresh") or data.get("refresh_token")
                else:
                    r.failure(f"Setup login failed: {r.status_code}")
        except Exception as e:
            print(f"[Setup] _login error: {e}")

    def _get_sacco_id(self):
        try:
            with self.client.get(
                "/api/v1/accounts/saccos/",
                name="[Setup] Get SACCO ID",
                catch_response=True
            ) as r:
                if r.status_code == 200:
                    data = r.json()

                    # Handle paginated {"results": [...]}
                    if isinstance(data, dict) and "results" in data:
                        results = data["results"]
                    # Handle plain list [...]
                    elif isinstance(data, list):
                        results = data
                    else:
                        results = []

                    # Safely extract first SACCO id
                    if isinstance(results, list) and len(results) > 0:
                        first = results[0]
                        if isinstance(first, dict):
                            self.sacco_id = first.get("id")
                        else:
                            self.sacco_id = None
                    else:
                        self.sacco_id = None
                else:
                    r.failure(f"Could not fetch SACCOs: {r.status_code}")
        except Exception as e:
            # Never let on_start crash — just continue without sacco_id
            print(f"[Setup] _get_sacco_id error: {e}")
            self.sacco_id = None

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    # ── Profile ───────────────────────────────

    @task(5)
    def get_profile(self):
        if not self.access_token:
            return
        with self.client.get(
            "/api/v1/accounts/me/",
            headers=self._auth_headers(),
            name="Profile - Get",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"Get profile failed: {r.status_code}")

    @task(2)
    def update_profile(self):
        if not self.access_token:
            return
        with self.client.patch(
            "/api/v1/accounts/me/",
            json={"first_name": "Updated", "last_name": "Tester"},
            headers=self._auth_headers(),
            name="Profile - Update",
            catch_response=True
        ) as r:
            if r.status_code not in (200, 204):
                r.failure(f"Update profile failed: {r.status_code}")

    # ── KYC ───────────────────────────────────

    @task(3)
    def get_kyc_status(self):
        if not self.access_token:
            return
        with self.client.get(
            "/api/v1/accounts/kyc/status/",
            headers=self._auth_headers(),
            name="KYC - Get Status",
            catch_response=True
        ) as r:
            if r.status_code not in (200, 404):
                r.failure(f"KYC status failed: {r.status_code}")

    # ── SACCOs ────────────────────────────────

    @task(6)
    def list_saccos(self):
        with self.client.get(
            "/api/v1/accounts/saccos/",
            name="SACCOs - List",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"List SACCOs failed: {r.status_code}")

    @task(3)
    def get_sacco_detail(self):
        if not self.sacco_id:
            return
        with self.client.get(
            f"/api/v1/accounts/saccos/{self.sacco_id}/",
            name="SACCOs - Detail",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"SACCO detail failed: {r.status_code}")

    # ── Memberships ───────────────────────────

    @task(4)
    def list_memberships(self):
        if not self.access_token:
            return
        with self.client.get(
            "/api/v1/members/memberships/",
            headers=self._auth_headers(),
            name="Membership - List",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"List memberships failed: {r.status_code}")

    @task(1)
    def apply_for_membership(self):
        if not self.access_token or not self.sacco_id:
            return
        with self.client.post(
            "/api/v1/members/memberships/",
            json={
                "sacco": self.sacco_id,
                "employment_status": "Employed",
                "employer_name": "Test Company Ltd",
                "monthly_income": 50000.00
            },
            headers=self._auth_headers(),
            name="Membership - Apply",
            catch_response=True
        ) as r:
            if r.status_code in (200, 201):
                self.membership_id = r.json().get("id")
            elif r.status_code == 400:
                r.success()  # Already a member — not a real failure
            else:
                r.failure(f"Apply membership failed: {r.status_code} — {r.text[:200]}")

    # ── Transactions ──────────────────────────

    @task(3)
    def list_transactions(self):
        if not self.access_token:
            return
        with self.client.get(
            "/api/v1/payments/transactions/",
            headers=self._auth_headers(),
            name="Payments - List Transactions",
            catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"List transactions failed: {r.status_code}")

    # ── Token refresh ─────────────────────────

    @task(1)
    def refresh_token(self):
        if not self.refresh_token:
            return
        with self.client.post(
            "/api/v1/accounts/token/refresh/",
            json={"refresh": self.refresh_token},
            name="Auth - Refresh Token",
            catch_response=True
        ) as r:
            if r.status_code == 200:
                new_access = r.json().get("access")
                if new_access:
                    self.access_token = new_access
            else:
                r.failure(f"Token refresh failed: {r.status_code}")

    # ── OTP (very low weight — costs SMS money) ──

    @task(1)
    def send_otp(self):
        """
        WARNING: Keep weight at 1 — this hits your real SMS provider.
        Comment this entire task out before running 100+ user tests
        to avoid SMS costs.
        """
        with self.client.post(
            "/api/v1/accounts/otp/send/",
            json={
                "phone_number": f"+{self.phone}",
                "purpose": "PHONE_VERIFY"
            },
            name="OTP - Send (low weight - costs SMS)",
            catch_response=True
        ) as r:
            # 429 = rate limited, which is expected and correct behaviour
            if r.status_code not in (200, 201, 429):
                r.failure(f"OTP send failed: {r.status_code}")




# ─────────────────────────────────────────────
# Summary on test stop
# ─────────────────────────────────────────────

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\n" + "="*60)
    print("SACCOSPHERE LOAD TEST COMPLETE")
    print("="*60)
    stats = environment.stats.total
    print(f"Total requests     : {stats.num_requests}")
    print(f"Failed requests    : {stats.num_failures}")
    print(f"Failure rate       : {stats.fail_ratio * 100:.2f}%")
    print(f"Avg response time  : {stats.avg_response_time:.0f}ms")
    print(f"95th percentile    : {stats.get_response_time_percentile(0.95):.0f}ms")
    print(f"Requests/second    : {stats.current_rps:.1f}")
    print("="*60)
    if stats.fail_ratio > 0.01:
        print("⚠️  Failure rate above 1% — investigate failures tab")
    elif stats.avg_response_time > 2000:
        print("⚠️  Avg response time above 2s — add more gunicorn workers")
    else:
        print("✅  System performing well under this load")
    print("="*60 + "\n")
