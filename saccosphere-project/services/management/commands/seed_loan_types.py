"""Seed default loan types for SACCOs."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Sacco
from services.models import LoanType


DEFAULT_LOAN_TYPES = (
    {
        'name': 'Development Loan',
        'description': 'Medium-term loan for member development.',
        'interest_rate': Decimal('12.00'),
        'max_term_months': 36,
        'min_amount': Decimal('1000.00'),
        'max_amount': None,
        'requires_guarantors': True,
        'min_guarantors': 1,
    },
    {
        'name': 'Emergency Loan',
        'description': 'Short-term loan for urgent member needs.',
        'interest_rate': Decimal('15.00'),
        'max_term_months': 12,
        'min_amount': Decimal('1000.00'),
        'max_amount': None,
        'requires_guarantors': True,
        'min_guarantors': 1,
    },
    {
        'name': 'School Fees Loan',
        'description': 'Loan for education and school fee expenses.',
        'interest_rate': Decimal('10.00'),
        'max_term_months': 24,
        'min_amount': Decimal('1000.00'),
        'max_amount': None,
        'requires_guarantors': True,
        'min_guarantors': 1,
    },
    {
        'name': 'Asset Financing',
        'description': 'Longer-term financing for member assets.',
        'interest_rate': Decimal('14.00'),
        'max_term_months': 60,
        'min_amount': Decimal('1000.00'),
        'max_amount': None,
        'requires_guarantors': True,
        'min_guarantors': 1,
    },
)


class Command(BaseCommand):
    """Seed default loan products for SACCOs without existing loan types."""

    help = 'Seed default loan types for each SACCO with no loan types.'

    def handle(self, *args, **options):
        """Execute the seed command."""
        total_created = 0
        total_skipped = 0

        with transaction.atomic():
            saccos = Sacco.objects.filter(is_active=True)

            for sacco in saccos:
                if LoanType.objects.filter(sacco=sacco).exists():
                    total_skipped += 1
                    self.stdout.write(
                        f'Skipped {sacco.name}: loan types already exist.'
                    )
                    continue

                loan_types = [
                    LoanType(sacco=sacco, **loan_type_data)
                    for loan_type_data in DEFAULT_LOAN_TYPES
                ]
                LoanType.objects.bulk_create(loan_types)
                total_created += len(loan_types)
                self.stdout.write(f'Seeded {sacco.name}.')

        self.stdout.write(
            self.style.SUCCESS(
                'Seeding complete. '
                f'Loan types created: {total_created}. '
                f'SACCOs skipped: {total_skipped}.'
            )
        )
