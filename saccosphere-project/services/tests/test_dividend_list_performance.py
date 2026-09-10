"""Pagination + query-shape guards for the dividend list/disburse paths.

- DividendDeclarationListCreateView / DividendPayoutListView are paginated
  by the project-standard SaccoSpherePagination (no more "return
  everything").
- DividendPayout has a (declaration, status) index; the disburse
  status=PENDING filter and the ?declaration= list filter are O(1)
  queries w.r.t. payout count.
- _get_saving_balance_at_date is a single SQL aggregate per saving-per
  -month, not a fetch-then-sum-in-Python loop.
"""

from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import create_ledger_entry
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.engines.dividend_calculator import calculate_average_balance
from services.models import (
    DividendDeclaration,
    DividendPayout,
    Saving,
    SavingsType,
)


class _Fixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Perf SACCO',
            registration_number='DLPERF-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.admin = User.objects.create_user(
            email='dlperf-admin@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(self.admin)

    def _member_saving(self, i):
        user = User.objects.create_user(
            email=f'dlperf-m{i}@example.com', password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number=f'DLPERF-M{i:04d}',
        )
        return Saving.objects.create(
            membership=membership,
            savings_type=self.savings_type,
            amount=Decimal('1000.00'),
            status=Saving.Status.ACTIVE,
        )

    def _declaration(self, start_year, *, status=DividendDeclaration.Status.DRAFT):
        return DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year=f'{start_year}/{start_year + 1}',
            declared_rate=Decimal('10.00'),
            period_start=date(start_year, 1, 1),
            period_end=date(start_year, 12, 31),
            status=status,
        )


class DividendListPaginationTests(_Fixture):
    DECL_URL = '/api/v1/services/dividends/declarations/'
    PAYOUT_URL = '/api/v1/services/dividends/payouts/'

    def _headers(self):
        return {'HTTP_X_SACCO_ID': str(self.sacco.id)}

    def test_declaration_list_is_paginated(self):
        for year in range(2000, 2025):  # 25 declarations
            self._declaration(year)

        page1 = self.client.get(self.DECL_URL, **self._headers()).json()
        self.assertEqual(page1['data']['count'], 25)
        self.assertEqual(len(page1['data']['results']), 20)
        self.assertIsNotNone(page1['data']['next'])

        page2 = self.client.get(
            self.DECL_URL, {'page': 2}, **self._headers(),
        ).json()
        self.assertEqual(len(page2['data']['results']), 5)
        self.assertIsNone(page2['data']['next'])

    def test_declaration_list_respects_page_size(self):
        for year in range(2000, 2010):  # 10
            self._declaration(year)

        body = self.client.get(
            self.DECL_URL, {'page_size': 4}, **self._headers(),
        ).json()
        self.assertEqual(body['data']['count'], 10)
        self.assertEqual(len(body['data']['results']), 4)

    def test_payout_list_is_paginated_and_scoped_to_declaration(self):
        decl_a = self._declaration(
            2025, status=DividendDeclaration.Status.APPROVED,
        )
        decl_b = self._declaration(
            2026, status=DividendDeclaration.Status.APPROVED,
        )
        for i in range(22):
            saving = self._member_saving(i)
            DividendPayout.objects.create(
                declaration=decl_a,
                membership=saving.membership,
                saving=saving,
                average_balance=Decimal('1000.00'),
                dividend_amount=Decimal('100.00'),
            )
        # One payout on the other declaration.
        saving_b = self._member_saving(999)
        DividendPayout.objects.create(
            declaration=decl_b,
            membership=saving_b.membership,
            saving=saving_b,
            average_balance=Decimal('1000.00'),
            dividend_amount=Decimal('100.00'),
        )

        body = self.client.get(
            self.PAYOUT_URL, {'declaration': str(decl_a.id)},
            **self._headers(),
        ).json()
        self.assertEqual(body['data']['count'], 22)
        self.assertEqual(len(body['data']['results']), 20)
        self.assertIsNotNone(body['data']['next'])

        page2 = self.client.get(
            self.PAYOUT_URL,
            {'declaration': str(decl_a.id), 'page': 2},
            **self._headers(),
        ).json()
        self.assertEqual(len(page2['data']['results']), 2)


class DividendListQueryShapeTests(_Fixture):
    def test_dividendpayout_has_the_declaration_status_index(self):
        index_names = {ix.name for ix in DividendPayout._meta.indexes}
        self.assertIn('divpayout_decl_status_idx', index_names)
        composite = next(
            ix for ix in DividendPayout._meta.indexes
            if ix.name == 'divpayout_decl_status_idx'
        )
        self.assertEqual(list(composite.fields), ['declaration', 'status'])

    def test_disburse_pending_filter_is_a_single_query(self):
        decl = self._declaration(
            2025, status=DividendDeclaration.Status.APPROVED,
        )
        for i in range(12):
            saving = self._member_saving(i)
            DividendPayout.objects.create(
                declaration=decl,
                membership=saving.membership,
                saving=saving,
                average_balance=Decimal('1000.00'),
                dividend_amount=Decimal('100.00'),
            )

        with self.assertNumQueries(1):
            list(
                decl.payouts.filter(
                    status=DividendPayout.Status.PENDING,
                ).values_list('id', flat=True)
            )

    def test_payout_list_query_count_is_constant_in_payout_count(self):
        self.client.force_authenticate(self.admin)
        decl = self._declaration(
            2025, status=DividendDeclaration.Status.APPROVED,
        )

        def _payouts(n, offset):
            for i in range(offset, offset + n):
                saving = self._member_saving(i)
                DividendPayout.objects.create(
                    declaration=decl,
                    membership=saving.membership,
                    saving=saving,
                    average_balance=Decimal('1000.00'),
                    dividend_amount=Decimal('100.00'),
                )

        url = '/api/v1/services/dividends/payouts/'
        headers = {'HTTP_X_SACCO_ID': str(self.sacco.id)}

        _payouts(5, 0)
        with CaptureQueriesContext(connection) as small:
            self.client.get(url, {'declaration': str(decl.id)}, **headers)

        _payouts(25, 100)  # 30 total, still one page of 20
        with CaptureQueriesContext(connection) as large:
            self.client.get(url, {'declaration': str(decl.id)}, **headers)

        self.assertEqual(
            len(large.captured_queries),
            len(small.captured_queries),
            msg=(
                'Payout list query count grew with payout count: '
                f'{len(small.captured_queries)} -> '
                f'{len(large.captured_queries)}'
            ),
        )

    def test_balance_lookup_is_one_aggregate_query_per_month(self):
        saving = self._member_saving(1)
        # A fully-ledgered opening balance.
        create_ledger_entry(
            membership=saving.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal('1000.00'),
            description='seed',
            reference='DLPERF-SEED',
        )
        saving = Saving.objects.select_related(
            'membership', 'savings_type',
        ).get(pk=saving.pk)

        # 1 query for the dividend-reference set + 1 aggregate per
        # month-end (12 for a full year) = 13. Never O(rows).
        with self.assertNumQueries(13):
            calculate_average_balance(
                saving, date(2025, 1, 1), date(2025, 12, 31),
            )

        # Adding many more ledger rows must not change the query count -
        # a fetch-then-sum-in-Python loop would still be one query but a
        # real aggregate is what keeps this flat and cheap.
        for n in range(40):
            create_ledger_entry(
                membership=saving.membership,
                entry_type=LedgerEntry.EntryType.CREDIT,
                category=LedgerEntry.Category.SAVING_DEPOSIT,
                amount=Decimal('1.00'),
                description='noise',
                reference=f'DLPERF-NOISE-{n}',
            )
        with self.assertNumQueries(13):
            calculate_average_balance(
                saving, date(2025, 1, 1), date(2025, 12, 31),
            )

    def test_balance_lookup_sql_is_a_sum_aggregate(self):
        saving = self._member_saving(2)
        create_ledger_entry(
            membership=saving.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal('500.00'),
            description='seed',
            reference='DLPERF-SEED-2',
        )
        saving = Saving.objects.select_related('membership').get(pk=saving.pk)

        with CaptureQueriesContext(connection) as ctx:
            calculate_average_balance(
                saving, date(2025, 1, 1), date(2025, 1, 31),
            )

        ledger_sql = [
            q['sql'] for q in ctx.captured_queries
            if 'ledger_ledgerentry' in q['sql'].lower()
        ]
        self.assertTrue(ledger_sql)
        # The balance query aggregates in SQL (SUM(CASE ...)) - it does
        # not pull every row back to sum in Python.
        self.assertTrue(
            any(
                'sum(' in sql.lower() and 'case' in sql.lower()
                for sql in ledger_sql
            ),
            msg=f'No SUM(CASE ...) aggregate in: {ledger_sql}',
        )
