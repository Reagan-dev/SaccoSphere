"""
M-Pesa Daraja API Diagnostic Tool

This script helps diagnose M-Pesa integration issues by checking:
1. Configuration completeness
2. Access token generation
3. API endpoint accessibility
4. Sandbox vs Live environment
"""
import json
import logging
from datetime import datetime

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class MpesaDiagnostic:
    """Diagnose M-Pesa integration issues."""

    SANDBOX_BASE_URL = 'https://sandbox.safaricom.co.ke'
    LIVE_BASE_URL = 'https://api.safaricom.co.ke'

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.results = {}

    def run_all_checks(self):
        """Run all diagnostic checks."""
        print("\n" + "=" * 60)
        print("M-Pesa Daraja API Diagnostic Report")
        print("=" * 60)
        print(f"Timestamp: {datetime.now().isoformat()}\n")

        self.check_configuration()
        self.check_environment()
        self.check_access_token()
        self.print_summary()

    def check_configuration(self):
        """Check if all required M-Pesa settings are configured."""
        print("\n[1/4] Checking Configuration...")
        print("-" * 60)

        config_checks = {
            'MPESA_CONSUMER_KEY': settings.MPESA_CONSUMER_KEY,
            'MPESA_CONSUMER_SECRET': settings.MPESA_CONSUMER_SECRET,
            'MPESA_SHORTCODE': settings.MPESA_SHORTCODE,
            'MPESA_PASSKEY': settings.MPESA_PASSKEY,
            'MPESA_ENVIRONMENT': settings.MPESA_ENVIRONMENT,
            'MPESA_CALLBACK_BASE_URL': settings.MPESA_CALLBACK_BASE_URL,
        }

        self.results['configuration'] = {}

        for key, value in config_checks.items():
            is_set = bool(value)
            status = "✓ SET" if is_set else "✗ MISSING"
            print(f"  {key}: {status}")

            if key in ['MPESA_CONSUMER_KEY', 'MPESA_CONSUMER_SECRET']:
                # Show masked value for security
                if is_set:
                    masked = f"{str(value)[:10]}...{str(value)[-4:]}"
                    print(f"    Value: {masked}")
            elif is_set:
                print(f"    Value: {value}")

            self.results['configuration'][key] = is_set

    def check_environment(self):
        """Check current environment settings."""
        print("\n[2/4] Checking Environment...")
        print("-" * 60)

        environment = settings.MPESA_ENVIRONMENT.lower()
        base_url = (
            self.SANDBOX_BASE_URL
            if environment == 'sandbox'
            else self.LIVE_BASE_URL
        )

        print(f"  Environment: {environment}")
        print(f"  Base URL: {base_url}")
        print(f"  Debug Mode: {settings.DEBUG}")

        self.results['environment'] = {
            'environment': environment,
            'base_url': base_url,
            'debug_mode': settings.DEBUG,
        }

    def check_access_token(self):
        """Check if access token can be obtained."""
        print("\n[3/4] Checking Access Token Generation...")
        print("-" * 60)

        if not (settings.MPESA_CONSUMER_KEY and settings.MPESA_CONSUMER_SECRET):
            print("  ✗ Cannot check: Consumer credentials are missing")
            self.results['access_token'] = {'status': 'skipped', 'reason': 'Missing credentials'}
            return

        try:
            import base64

            auth_value = (
                f'{settings.MPESA_CONSUMER_KEY}:'
                f'{settings.MPESA_CONSUMER_SECRET}'
            )
            encoded_auth = base64.b64encode(auth_value.encode()).decode()
            headers = {'Authorization': f'Basic {encoded_auth}'}

            environment = settings.MPESA_ENVIRONMENT.lower()
            base_url = (
                self.SANDBOX_BASE_URL
                if environment == 'sandbox'
                else self.LIVE_BASE_URL
            )

            auth_url = f'{base_url}/oauth/v1/generate?grant_type=client_credentials'

            print(f"  Requesting token from: {auth_url}")
            response = requests.get(auth_url, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                token = data.get('access_token')
                expires_in = data.get('expires_in')
                print(f"  ✓ Access token obtained")
                print(f"    Expires in: {expires_in} seconds")
                self.results['access_token'] = {
                    'status': 'success',
                    'expires_in': expires_in,
                }
            else:
                error_msg = response.text
                print(f"  ✗ Failed to get access token")
                print(f"    Status Code: {response.status_code}")
                print(f"    Response: {error_msg[:200]}")
                self.results['access_token'] = {
                    'status': 'failed',
                    'status_code': response.status_code,
                    'error': error_msg[:200],
                }
        except requests.RequestException as e:
            print(f"  ✗ Network error: {str(e)}")
            self.results['access_token'] = {
                'status': 'error',
                'error': str(e),
            }

    def print_summary(self):
        """Print diagnostic summary and recommendations."""
        print("\n[4/4] Summary & Recommendations...")
        print("-" * 60)

        # Check if all configurations are set
        config = self.results.get('configuration', {})
        missing_config = [k for k, v in config.items() if not v]

        if missing_config:
            print(f"\n⚠ Missing Configuration ({len(missing_config)}):")
            for key in missing_config:
                print(f"  - {key}")

        # Check access token status
        token_result = self.results.get('access_token', {})
        token_status = token_result.get('status')

        if token_status == 'success':
            print("\n✓ Access Token: Successfully obtained")
        elif token_status == 'failed':
            print(f"\n✗ Access Token: Failed with status {token_result.get('status_code')}")
            print("  Recommendations:")
            print("    1. Verify MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET")
            print("    2. Check if app is registered in M-Pesa Daraja portal")
            print("    3. Verify app status is 'Active' in Daraja")
        elif token_status == 'skipped':
            print("\n⊘ Access Token: Skipped (missing credentials)")

        print("\n" + "=" * 60)
        print("\nCommon Issues & Solutions:\n")

        print("1. 404 on STK Push endpoint:")
        print("   - App is not registered for STK Push capability")
        print("   - Solution: Log into Daraja portal and enable STK Push")
        print("   - Portal: https://developer.safaricom.co.ke/\n")

        print("2. 401 Unauthorized:")
        print("   - Invalid or expired credentials")
        print("   - Solution: Verify MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET")
        print("   - Check if credentials have been rotated\n")

        print("3. 403 Forbidden:")
        print("   - IP address not whitelisted")
        print("   - Solution: Whitelist your server IP in Daraja portal\n")

        print("4. Callback URL returning 404:")
        print("   - ngrok tunnel is down or URL has changed")
        print("   - Solution: Restart ngrok and update MPESA_CALLBACK_BASE_URL")
        print("=" * 60 + "\n")


def run_diagnostic():
    """Run the diagnostic tool."""
    diagnostic = MpesaDiagnostic()
    diagnostic.run_all_checks()


if __name__ == '__main__':
    import django
    import os

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
    django.setup()

    run_diagnostic()
