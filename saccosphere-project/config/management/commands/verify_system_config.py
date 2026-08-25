import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Sacco
from payments.providers.registry import PROVIDER_REGISTRY, get_provider_class


class Command(BaseCommand):
    help = 'Verify critical SaccoSphere runtime settings.'

    def handle(self, *args, **options):
        rows = [
            self._check_payment_provider(),
            self._check_disbursement_tiers(),
            self._check_withdrawal_tiers(),
            self._check_platform_fees(),
            self._check_celery_broker(),
            self._check_email(),
            self._check_billing_paybill(),
            self._check_media_root(),
            self._check_sacco_payment_configs(),
        ]
        self._print_table(rows)

        failed = [row for row in rows if row['status'] != 'OK']
        if failed:
            labels = ', '.join(row['setting'] for row in failed)
            raise CommandError(f'Config verification failed: {labels}')

        self.stdout.write(self.style.SUCCESS('All critical settings are OK.'))

    def _ok(self, setting, details):
        return {
            'setting': setting,
            'status': 'OK',
            'details': details,
        }

    def _fail(self, setting, details):
        return {
            'setting': setting,
            'status': 'FAIL',
            'details': details,
        }

    def _check_payment_provider(self):
        provider = getattr(settings, 'PAYMENT_PROVIDER', '').strip().lower()
        if not provider:
            return self._fail(
                'PAYMENT_PROVIDER',
                'Not set. Registered providers: '
                + ', '.join(sorted(PROVIDER_REGISTRY)),
            )

        try:
            provider_class = get_provider_class(provider)
        except Exception as exc:
            return self._fail('PAYMENT_PROVIDER', str(exc))

        return self._ok(
            'PAYMENT_PROVIDER',
            f'{provider} -> {provider_class.__name__}',
        )

    def _check_disbursement_tiers(self):
        tiers = getattr(settings, 'DISBURSEMENT_TIERS', None)
        if not self._tiers_are_valid(tiers):
            return self._fail(
                'DISBURSEMENT_TIERS',
                f'Invalid tiers: {tiers!r}',
            )
        return self._ok('DISBURSEMENT_TIERS', self._format_tiers(tiers))

    def _check_withdrawal_tiers(self):
        tiers = getattr(settings, 'WITHDRAWAL_TIERS', None)
        if not self._tiers_are_valid(tiers):
            return self._fail(
                'WITHDRAWAL_TIERS',
                f'Invalid tiers: {tiers!r}',
            )
        return self._ok('WITHDRAWAL_TIERS', self._format_tiers(tiers))

    def _check_platform_fees(self):
        fees = getattr(settings, 'PLATFORM_FEES', {})
        deposit = fees.get('deposit')
        repayment = fees.get('repayment')
        if deposit is None or repayment is None:
            return self._fail(
                'FEE_DEPOSIT_RATE / FEE_REPAYMENT_RATE',
                f'Loaded fees: {fees!r}',
            )

        return self._ok(
            'FEE_DEPOSIT_RATE / FEE_REPAYMENT_RATE',
            f'deposit={deposit}, repayment={repayment}',
        )

    def _check_celery_broker(self):
        broker_url = getattr(settings, 'CELERY_BROKER_URL', '')
        parsed = urlparse(broker_url)
        if parsed.scheme not in {'redis', 'rediss'}:
            return self._fail(
                'CELERY_BROKER_URL',
                f'Unsupported broker scheme: {parsed.scheme or "missing"}',
            )

        host = parsed.hostname
        port = parsed.port or 6379
        if not host:
            return self._fail('CELERY_BROKER_URL', 'Missing broker host.')

        try:
            with socket.create_connection((host, port), timeout=3):
                pass
        except OSError as exc:
            return self._fail(
                'CELERY_BROKER_URL',
                f'{host}:{port} not reachable ({exc})',
            )

        return self._ok('CELERY_BROKER_URL', f'{host}:{port} reachable')

    def _check_email(self):
        required = {
            'EMAIL_HOST': getattr(settings, 'EMAIL_HOST', ''),
            'EMAIL_PORT': getattr(settings, 'EMAIL_PORT', ''),
            'DEFAULT_FROM_EMAIL': getattr(settings, 'DEFAULT_FROM_EMAIL', ''),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            return self._fail('EMAIL', 'Missing: ' + ', '.join(missing))

        return self._ok(
            'EMAIL',
            (
                f'host={settings.EMAIL_HOST}, port={settings.EMAIL_PORT}, '
                f'from={settings.DEFAULT_FROM_EMAIL}'
            ),
        )

    def _check_billing_paybill(self):
        paybill = getattr(settings, 'BILLING_PAYBILL', '').strip()
        if not paybill:
            return self._fail('BILLING_PAYBILL', 'Not set.')
        return self._ok('BILLING_PAYBILL', paybill)

    def _check_media_root(self):
        media_root = Path(settings.MEDIA_ROOT)
        try:
            media_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=media_root, delete=True):
                pass
        except OSError as exc:
            return self._fail('MEDIA_ROOT', f'Not writable: {exc}')

        return self._ok('MEDIA_ROOT', f'{media_root} is writable')

    def _check_sacco_payment_configs(self):
        """Check that all payment-ready SACCOs have payment configuration."""
        payment_ready_saccos = Sacco.objects.filter(
            is_active=True,
            payment_ready=True
        )
        missing_configs = []
        
        for sacco in payment_ready_saccos:
            try:
                payment_config = sacco.payment_config
                if not payment_config.is_active:
                    missing_configs.append(
                        f'{sacco.name} (config exists but inactive)'
                    )
            except AttributeError:
                missing_configs.append(sacco.name)
        
        if missing_configs:
            return self._fail(
                'SACCO_PAYMENT_CONFIGS',
                f'Missing/inactive for payment-ready SACCOs: {", ".join(missing_configs)}'
            )
        
        if payment_ready_saccos.exists():
            return self._ok(
                'SACCO_PAYMENT_CONFIGS',
                f'{payment_ready_saccos.count()} payment-ready SACCO(s) configured'
            )
        
        return self._ok(
            'SACCO_PAYMENT_CONFIGS',
            'No payment-ready SACCOs (onboarding in progress)'
        )

    def _tiers_are_valid(self, tiers):
        if not isinstance(tiers, list) or not tiers:
            return False

        for ceiling, fee in tiers:
            if ceiling is not None and not isinstance(ceiling, int):
                return False
            if fee <= 0:
                return False

        return tiers[-1][0] is None

    def _format_tiers(self, tiers):
        return ', '.join(
            f'<= {ceiling}: {fee}' if ceiling is not None else f'> max: {fee}'
            for ceiling, fee in tiers
        )

    def _print_table(self, rows):
        setting_width = max(len(row['setting']) for row in rows)
        status_width = len('STATUS')
        separator = (
            f'+-{"-" * setting_width}-+-{"-" * status_width}-+'
            f'-{"-" * 72}-+'
        )
        self.stdout.write(separator)
        self.stdout.write(
            f'| {"SETTING".ljust(setting_width)} | STATUS | DETAILS'
            f'{" " * 65}|'
        )
        self.stdout.write(separator)

        for row in rows:
            status = row['status']
            styled_status = (
                self.style.SUCCESS(status)
                if status == 'OK'
                else self.style.ERROR(status)
            )
            detail = row['details'][:72]
            self.stdout.write(
                f'| {row["setting"].ljust(setting_width)} | '
                f'{styled_status.ljust(status_width)} | '
                f'{detail.ljust(72)} |'
            )

        self.stdout.write(separator)
