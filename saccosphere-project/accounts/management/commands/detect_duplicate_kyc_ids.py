"""Django management command to detect duplicate national ID numbers in KYC records."""

from django.db import models
from django.core.management.base import BaseCommand

from accounts.models import KYCVerification


class Command(BaseCommand):
    help = (
        'Scan KYCVerification table for duplicate id_number values and '
        'report details for human compliance review. Does not delete or merge.'
    )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Scanning for duplicate KYC ID numbers...'))
        self.stdout.write('=' * 80)

        # Find normalized_id_numbers that appear more than once (excluding NULL/empty)
        duplicates = (
            KYCVerification.objects.exclude(normalized_id_number__isnull=True)
            .exclude(normalized_id_number__exact='')
            .values('normalized_id_number')
            .annotate(count=models.Count('normalized_id_number'))
            .filter(count__gt=1)
            .order_by('-count')
        )

        if not duplicates:
            self.stdout.write(self.style.SUCCESS('No duplicate ID numbers found.'))
            return

        duplicate_count = duplicates.count()
        self.stdout.write(
            self.style.WARNING(f'Found {duplicate_count} duplicate ID number(s).')
        )
        self.stdout.write('')

        for dup in duplicates:
            normalized_id = dup['normalized_id_number']
            count = dup['count']

            self.stdout.write(
                self.style.ERROR(f'\nNormalized ID: {normalized_id} (appears {count} times)')
            )
            self.stdout.write('-' * 80)

            # Get all records for this normalized ID number
            records = KYCVerification.objects.filter(normalized_id_number=normalized_id).select_related(
                'user'
            )

            for record in records:
                self.stdout.write(f'  User ID: {record.user.id}')
                self.stdout.write(f'  Email: {record.user.email}')
                self.stdout.write(f'  Status: {record.status}')
                self.stdout.write(f'  Submitted At: {record.submitted_at}')
                self.stdout.write(f'  Created At: {record.created_at}')
                self.stdout.write(f'  IPRS Verified: {record.iprs_verified}')
                if record.iprs_reference:
                    self.stdout.write(f'  IPRS Reference: {record.iprs_reference}')
                self.stdout.write('')

        self.stdout.write('=' * 80)
        self.stdout.write(
            self.style.WARNING(
                'Review the duplicates above. This command does not modify data.'
            )
        )
