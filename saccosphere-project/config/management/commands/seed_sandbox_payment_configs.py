"""Seed SACCO payment configs with shared sandbox credentials for testing.

This command is strictly for sandbox/testing environments only.
It will refuse to run in production (MPESA_ENVIRONMENT=live).

Usage:
    python manage.py seed_sandbox_payment_configs
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Sacco, SaccoPaymentConfig


class Command(BaseCommand):
    help = (
        'Seed SACCO payment configs with shared sandbox credentials. '
        'Refuses to run in production (MPESA_ENVIRONMENT=live).'
    )

    def handle(self, *args, **options):
        # Verify we're in sandbox environment
        environment = getattr(settings, 'MPESA_ENVIRONMENT', '').lower()
        if environment != 'sandbox':
            raise CommandError(
                f'Command only allowed in sandbox environment. '
                f'Current environment: {environment or "not set"}'
            )
        
        self.stdout.write(
            self.style.WARNING(
                'Running in SANDBOX mode. This command will NOT run in production.'
            )
        )
        
        # Get shared sandbox credentials from settings
        consumer_key = getattr(settings, 'MPESA_CONSUMER_KEY', None)
        consumer_secret = getattr(settings, 'MPESA_CONSUMER_SECRET', None)
        shortcode = getattr(settings, 'MPESA_SHORTCODE', None)
        passkey = getattr(settings, 'MPESA_PASSKEY', None)
        
        if not all([consumer_key, consumer_secret, shortcode, passkey]):
            raise CommandError(
                'Missing required sandbox credentials in settings: '
                'MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET, MPESA_SHORTCODE, MPESA_PASSKEY'
            )
        
        # Find SACCOs without payment config
        saccos_without_config = Sacco.objects.filter(
            is_active=True
        ).exclude(
            payment_config__isnull=False
        )
        
        if not saccos_without_config.exists():
            self.stdout.write(
                self.style.SUCCESS(
                    'All active SACCOs already have payment configuration.'
                )
            )
            return
        
        # Create payment configs for SACCOs without them
        created_count = 0
        for sacco in saccos_without_config:
            try:
                config = SaccoPaymentConfig.objects.create(
                    sacco=sacco,
                    shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
                    shortcode=shortcode,
                    stk_passkey=passkey,
                    daraja_consumer_key=consumer_key,
                    daraja_consumer_secret=consumer_secret,
                    environment=SaccoPaymentConfig.Environment.SANDBOX,
                    is_active=True,
                )
                created_count += 1
                self.stdout.write(
                    f'Created payment config for SACCO: {sacco.name} ({sacco.registration_number})'
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f'Failed to create config for {sacco.name}: {exc}'
                    )
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully created {created_count} payment config(s) '
                f'with shared sandbox credentials.'
            )
        )
