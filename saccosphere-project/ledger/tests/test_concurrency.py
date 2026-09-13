"""Concurrency regression tests for create_ledger_entry().

Two near-simultaneous ledger writes for the same membership must not
each compute their running balance from a stale pre-write snapshot. The
threaded case needs real row-level locking, so it runs on PostgreSQL and
skips on SQLite (select_for_update is a no-op there), matching
services.tests.test_concurrency_limits and
accounts.tests.test_otp_security.OTPRaceConditionTestCase. A
deterministic sequential counterpart proves the aggregate invariant on
every backend.

NOTE: this environment has no local PostgreSQL/psycopg2, so the
threaded case below is written against the documented locking contract
in create_ledger_entry() but has not been run against a real Postgres
instance in this session - run it in CI/staging before relying on it.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import create_ledger_entry
from saccomembership.models import Membership


class CreateLedgerEntryConcurrencyTest(TransactionTestCase):
    """Two concurrent writers for one membership must fully serialize."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='ledger-race@example.com',
            phone_number='254700009101',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Race SACCO',
            registration_number='RACE-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='RACE-M-001',
        )

    def _write(self, reference, amount):
        return create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal(amount),
            description='race test entry',
            reference=reference,
        )

    def _assert_fully_serialized(self):
        """The aggregate and the last entry's balance_after must agree.

        The aggregate (a fresh SUM over every committed row) is correct
        by construction. If the two writes were not actually serialized,
        the entry that committed second would have computed
        balance_after from a snapshot that did not yet include the
        first writer's row, so it would fall short of the true total.
        """
        entries = list(
            LedgerEntry.objects.filter(
                membership=self.membership,
            ).order_by('created_at'),
        )
        self.assertEqual(len(entries), 2)

        total = sum((entry.amount for entry in entries), Decimal('0.00'))
        last_entry = entries[-1]
        self.assertEqual(
            last_entry.balance_after,
            total,
            'the second-committed entry\'s balance_after does not '
            'include the first entry - the two writers were not '
            'serialized against each other',
        )

    def test_sequential_writes_produce_the_correct_running_balance(self):
        self._write('RACE-SEQ-1', '1000.00')
        self._write('RACE-SEQ-2', '500.00')

        self._assert_fully_serialized()

    def test_concurrent_writes_to_a_brand_new_membership_serialize(self):
        """The zero-existing-rows edge case.

        With no prior entries, a naive `SELECT ... FOR UPDATE` over the
        membership's (empty) ledger rows locks nothing, so it cannot by
        itself force two concurrent first-writers to serialize.
        """
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite select_for_update is a no-op; run on PostgreSQL.'
            )

        barrier = threading.Barrier(2)

        def worker(args):
            reference, amount = args
            barrier.wait()
            try:
                self._write(reference, amount)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    worker,
                    [('RACE-CONC-1', '1000.00'), ('RACE-CONC-2', '500.00')],
                )
            )

        self._assert_fully_serialized()
