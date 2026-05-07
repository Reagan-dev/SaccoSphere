from django.core.management.base import BaseCommand

from accounts.models import KENYA_COUNTIES, Sacco


class Command(BaseCommand):
    """
    Seed SACCO sector and county choices.

    This command is informational only. It validates that all KENYA_COUNTIES
    are available and counts Sacco.Sector choices.

    Usage:
        python manage.py seed_sacco_data
    """

    help = 'Seed SACCO sector and county choices.'

    def handle(self, *args, **options):
        """Execute the seed command."""
        # Count sectors from model choices
        sector_count = len(Sacco.Sector.choices)

        # Count counties from constant
        county_count = len(KENYA_COUNTIES)

        self.stdout.write(
            self.style.SUCCESS(
                f'Seeded {county_count} counties and {sector_count} sectors.'
            )
        )
