"""Tests for ledger statement generation."""

from datetime import datetime, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from ledger.engines.statement_builder import build_statement
from ledger.models import LedgerEntry
from ledger.utils import create_ledger_entry
from saccomembership.models import Membership


class StatementBuilderTestCase(TestCase):
    """Test financial statement totals and balances."""

    def setUp(self):
        """Create a member with ledger entries across multiple dates."""
        self.user = User.objects.create_user(
            email='statement@example.com',
            password='StrongPass123',
            first_name='Statement',
            last_name='Member',
        )
        self.sacco = Sacco.objects.create(
            name='Statement SACCO',
            registration_number='STAT001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='STAT-M001',
        )
        self.from_date = datetime(2026, 5, 1).date()
        self.to_date = datetime(2026, 5, 31).date()

    def test_opening_balance_correct(self):
        """Entries before from_date should affect opening balance."""
        self._create_entry(
            LedgerEntry.EntryType.CREDIT,
            Decimal('1000.00'),
            datetime(2026, 4, 25).date(),
            'OPENING-CREDIT',
        )
        self._create_entry(
            LedgerEntry.EntryType.DEBIT,
            Decimal('250.00'),
            datetime(2026, 4, 28).date(),
            'OPENING-DEBIT',
        )

        statement = build_statement(
            self.membership,
            self.from_date,
            self.to_date,
        )

        self.assertEqual(statement['opening_balance'], Decimal('750.00'))

    def test_closing_balance_correct(self):
        """Closing balance should equal opening plus credits minus debits."""
        self._create_entry(
            LedgerEntry.EntryType.CREDIT,
            Decimal('1000.00'),
            datetime(2026, 4, 25).date(),
            'CLOSING-OPENING',
        )
        self._create_entry(
            LedgerEntry.EntryType.CREDIT,
            Decimal('500.00'),
            datetime(2026, 5, 10).date(),
            'CLOSING-CREDIT',
        )
        self._create_entry(
            LedgerEntry.EntryType.DEBIT,
            Decimal('200.00'),
            datetime(2026, 5, 15).date(),
            'CLOSING-DEBIT',
        )

        statement = build_statement(
            self.membership,
            self.from_date,
            self.to_date,
        )
        expected_closing = (
            statement['opening_balance']
            + statement['total_credits']
            - statement['total_debits']
        )

        self.assertEqual(statement['closing_balance'], expected_closing)
        self.assertEqual(statement['closing_balance'], Decimal('1300.00'))

    def test_empty_range_returns_zero_movement(self):
        """A range with no entries should return zero debit and credit totals."""
        self._create_entry(
            LedgerEntry.EntryType.CREDIT,
            Decimal('1000.00'),
            datetime(2026, 4, 25).date(),
            'EMPTY-OPENING',
        )

        statement = build_statement(
            self.membership,
            self.from_date,
            self.to_date,
        )

        self.assertEqual(statement['total_credits'], Decimal('0.00'))
        self.assertEqual(statement['total_debits'], Decimal('0.00'))
        self.assertEqual(statement['entries'], [])
        self.assertEqual(statement['opening_balance'], Decimal('1000.00'))
        self.assertEqual(statement['closing_balance'], Decimal('1000.00'))

    def _create_entry(self, entry_type, amount, created_date, reference):
        entry = create_ledger_entry(
            membership=self.membership,
            entry_type=entry_type,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=amount,
            description='Statement test entry',
            reference=reference,
        )
        aware_datetime = timezone.make_aware(
            datetime.combine(created_date, time(hour=12)),
        )
        LedgerEntry.objects.filter(id=entry.id).update(
            created_at=aware_datetime,
        )
        entry.refresh_from_db()
        return entry
