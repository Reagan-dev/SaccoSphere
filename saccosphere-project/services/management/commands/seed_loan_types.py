from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Sacco
from services.models import LoanType


class Command(BaseCommand):
    """
    Seed loan types for each SACCO.
    
    Creates default loan products for all existing SACCOs.
    Placeholder for Day 4 implementation.
    
    Usage:
        python manage.py seed_loan_types
    """

    help = 'Seed loan types for each SACCO.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing loan types.',
        )

    def handle(self, *args, **options):
        """Execute the seed command."""
        overwrite = options.get('overwrite', False)
        
        with transaction.atomic():
            saccos = Sacco.objects.filter(is_active=True)
            total_saccos = saccos.count()
            total_created = 0
            total_skipped = 0

            for sacco in saccos:
                self.stdout.write(f'Processing SACCO: {sacco.name}')
                
                # Default loan types to create
                default_loan_types = [
                    {
                        'name': 'Emergency Loan',
                        'description': 'Short-term emergency loan for urgent needs',
                        'interest_rate': Decimal('15.00'),
                        'max_term_months': 12,
                        'min_amount': Decimal('1000.00'),
                        'max_amount': Decimal('50000.00'),
                        'requires_guarantors': True,
                        'min_guarantors': 2,
                    },
                    {
                        'name': 'Development Loan',
                        'description': 'Medium-term loan for personal development',
                        'interest_rate': Decimal('12.00'),
                        'max_term_months': 36,
                        'min_amount': Decimal('10000.00'),
                        'max_amount': Decimal('500000.00'),
                        'requires_guarantors': True,
                        'min_guarantors': 1,
                    },
                    {
                        'name': 'Business Loan',
                        'description': 'Long-term loan for business expansion',
                        'interest_rate': Decimal('10.00'),
                        'max_term_months': 60,
                        'min_amount': Decimal('50000.00'),
                        'max_amount': Decimal('2000000.00'),
                        'requires_guarantors': True,
                        'min_guarantors': 3,
                    },
                    {
                        'name': 'School Fees Loan',
                        'description': 'Educational expenses loan',
                        'interest_rate': Decimal('8.00'),
                        'max_term_months': 24,
                        'min_amount': Decimal('5000.00'),
                        'max_amount': Decimal('200000.00'),
                        'requires_guarantors': False,
                        'min_guarantors': 0,
                    },
                ]

                for loan_type_data in default_loan_types:
                    # Check if loan type already exists
                    existing = LoanType.objects.filter(
                        sacco=sacco,
                        name=loan_type_data['name']
                    ).first()

                    if existing and not overwrite:
                        self.stdout.write(
                            f'  Skipped: {loan_type_data["name"]} (already exists)'
                        )
                        total_skipped += 1
                        continue

                    if existing and overwrite:
                        existing.delete()
                        self.stdout.write(
                            f'  Overwriting: {loan_type_data["name"]}'
                        )

                    # Create new loan type
                    LoanType.objects.create(sacco=sacco, **loan_type_data)
                    self.stdout.write(
                        f'  Created: {loan_type_data["name"]}'
                    )
                    total_created += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSeeding complete!\n'
                    f'SACCOs processed: {total_saccos}\n'
                    f'Loan types created: {total_created}\n'
                    f'Loan types skipped: {total_skipped}'
                )
            )
