"""API-layer tests for the ledger views.

Covers what the utils/engine-layer tests don't: authentication,
ownership scoping, query-param validation, caching, PDF generation
fallback, and ODPC statement-access logging.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import create_ledger_entry
from ledger.views import StatementPDFThrottle, StatementPDFView
from saccomanagement.models import DataConsentLog
from saccomembership.models import Membership


class _LedgerAPITestCase(APITestCase):
    """Two members in one SACCO, each with their own ledger history."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Views SACCO',
            registration_number='VIEWS-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.user = User.objects.create_user(
            email='ledger-views@example.com',
            phone_number='254700008001',
            password='StrongPass1',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='VIEWS-M-001',
        )
        self.other_user = User.objects.create_user(
            email='ledger-views-other@example.com',
            phone_number='254700008002',
            password='StrongPass1',
        )
        self.other_membership = Membership.objects.create(
            user=self.other_user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='VIEWS-M-002',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _post_entry(self, membership, entry_type, amount, reference):
        return create_ledger_entry(
            membership=membership,
            entry_type=entry_type,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal(amount),
            description='test entry',
            reference=reference,
        )


class LedgerEntryListViewTest(_LedgerAPITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('ledger:entry-list')

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.url, {'sacco_id': self.sacco.id})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_sacco_id_is_a_clean_400(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_sacco_id_is_a_400_not_a_500(self):
        response = self.client.get(self.url, {'sacco_id': 'not-a-uuid'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('sacco_id', response.data['errors'])

    def test_sacco_id_with_no_membership_is_a_400(self):
        foreign_sacco = Sacco.objects.create(
            name='Foreign SACCO',
            registration_number='VIEWS-2',
            sector=Sacco.Sector.FINANCE,
            county='Kisumu',
        )
        response = self.client.get(self.url, {'sacco_id': foreign_sacco.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_from_date_is_a_400_not_a_500(self):
        response = self.client.get(
            self.url,
            {'sacco_id': self.sacco.id, 'from_date': 'not-a-date'},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('from_date', response.data['errors'])

    def test_malformed_to_date_is_a_400_not_a_500(self):
        response = self.client.get(
            self.url,
            {'sacco_id': self.sacco.id, 'to_date': 'not-a-date'},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('to_date', response.data['errors'])

    def test_only_returns_the_caller_s_own_entries(self):
        self._post_entry(
            self.membership, LedgerEntry.EntryType.CREDIT,
            '1000.00', 'OWN-1',
        )
        self._post_entry(
            self.other_membership, LedgerEntry.EntryType.CREDIT,
            '2000.00', 'OTHER-1',
        )

        response = self.client.get(self.url, {'sacco_id': self.sacco.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['data']['results']
        references = {row['reference'] for row in results}
        self.assertEqual(references, {'OWN-1'})

    def test_category_filter(self):
        self._post_entry(
            self.membership, LedgerEntry.EntryType.CREDIT,
            '1000.00', 'CAT-DEP-1',
        )
        create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.LOAN_DISBURSEMENT,
            amount=Decimal('500.00'),
            description='loan out',
            reference='CAT-LOAN-1',
        )

        response = self.client.get(
            self.url,
            {'sacco_id': self.sacco.id, 'category': 'LOAN_DISBURSEMENT'},
        )

        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['reference'], 'CAT-LOAN-1')


class BalanceViewTest(_LedgerAPITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('ledger:balance')

    def test_malformed_sacco_id_is_a_400_not_a_500(self):
        response = self.client.get(self.url, {'sacco_id': 'garbage'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_balance_is_scoped_to_the_caller_s_membership(self):
        self._post_entry(
            self.membership, LedgerEntry.EntryType.CREDIT,
            '3000.00', 'BAL-OWN-1',
        )
        self._post_entry(
            self.other_membership, LedgerEntry.EntryType.CREDIT,
            '9000.00', 'BAL-OTHER-1',
        )

        response = self.client.get(self.url, {'sacco_id': self.sacco.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Decimal(response.data['current_balance']), Decimal('3000.00'),
        )

    def test_balance_excludes_loan_and_fee_categories(self):
        """A loan on the same membership must not reduce the savings
        balance shown to the member - see the SAVINGS_LEDGER_CATEGORIES
        scoping in balance_calculator.get_running_balance().
        """
        self._post_entry(
            self.membership, LedgerEntry.EntryType.CREDIT,
            '5000.00', 'BAL-DEP-1',
        )
        create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.LOAN_DISBURSEMENT,
            amount=Decimal('3000.00'),
            description='loan out',
            reference='BAL-LOAN-1',
        )
        create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.PENALTY,
            amount=Decimal('100.00'),
            description='late fee',
            reference='BAL-FEE-1',
        )

        response = self.client.get(self.url, {'sacco_id': self.sacco.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Decimal(response.data['current_balance']), Decimal('5000.00'),
        )


class StatementViewTest(_LedgerAPITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('ledger:statement')

    def _params(self, **overrides):
        params = {
            'sacco_id': self.sacco.id,
            'from_date': '2026-01-01',
            'to_date': '2026-01-31',
        }
        params.update(overrides)
        return params

    def test_malformed_sacco_id_is_a_400_not_a_500(self):
        response = self.client.get(self.url, self._params(sacco_id='xx'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_date_is_a_400_not_a_500(self):
        response = self.client.get(
            self.url, self._params(from_date='not-a-date'),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_to_date_before_from_date_is_a_400(self):
        response = self.client.get(
            self.url,
            self._params(from_date='2026-01-31', to_date='2026-01-01'),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_range_over_one_year_is_a_400(self):
        response = self.client.get(
            self.url,
            self._params(from_date='2020-01-01', to_date='2026-01-31'),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sacco_id_with_no_membership_is_a_404(self):
        foreign_sacco = Sacco.objects.create(
            name='Foreign SACCO 2',
            registration_number='VIEWS-3',
            sector=Sacco.Sector.FINANCE,
            county='Kisumu',
        )
        response = self.client.get(
            self.url, self._params(sacco_id=foreign_sacco.id),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_statement_reflects_only_the_caller_s_entries(self):
        entry = self._post_entry(
            self.membership, LedgerEntry.EntryType.CREDIT,
            '1500.00', 'STMT-OWN-1',
        )
        entry.created_at = timezone.make_aware(
            timezone.datetime(2026, 1, 15),
        )
        LedgerEntry.objects.filter(pk=entry.pk).update(
            created_at=entry.created_at,
        )
        self._post_entry(
            self.other_membership, LedgerEntry.EntryType.CREDIT,
            '9999.00', 'STMT-OTHER-1',
        )

        response = self.client.get(self.url, self._params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Decimal(response.data['closing_balance']), Decimal('1500.00'),
        )

    def test_statement_excludes_loan_and_fee_categories(self):
        """A loan/fee on the same membership must not appear in, or move
        the balance of, the member's savings statement - see the
        SAVINGS_LEDGER_CATEGORIES scoping in build_statement().
        """
        deposit = self._post_entry(
            self.membership, LedgerEntry.EntryType.CREDIT,
            '4000.00', 'STMT-DEP-1',
        )
        loan = create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.LOAN_DISBURSEMENT,
            amount=Decimal('2500.00'),
            description='loan out',
            reference='STMT-LOAN-1',
        )
        mid_month = timezone.make_aware(timezone.datetime(2026, 1, 15))
        LedgerEntry.objects.filter(
            pk__in=[deposit.pk, loan.pk],
        ).update(created_at=mid_month)

        response = self.client.get(self.url, self._params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Decimal(response.data['closing_balance']), Decimal('4000.00'),
        )
        references = {row['reference'] for row in response.data['entries']}
        self.assertEqual(references, {'STMT-DEP-1'})

    def test_every_view_is_logged_including_cache_hits(self):
        """Regression test: a cached statement must still be logged.

        Before the fix, build_statement() (and its access log) only ran
        on a cache miss, so repeat views within the 300s cache window
        were invisible to ODPC access logging.
        """
        before = DataConsentLog.objects.filter(
            user=self.user, data_type='MEMBER_STATEMENT',
        ).count()

        first = self.client.get(self.url, self._params())
        second = self.client.get(self.url, self._params())

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        after = DataConsentLog.objects.filter(
            user=self.user, data_type='MEMBER_STATEMENT',
        ).count()
        self.assertEqual(after - before, 2)

    @patch('config.utils.emit_metric')
    def test_cache_miss_then_hit_emit_the_right_metrics(self, mock_emit):
        first = self.client.get(self.url, self._params())
        second = self.client.get(self.url, self._params())

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        mock_emit.assert_any_call('ledger_statement_cache_miss')
        mock_emit.assert_any_call('ledger_statement_cache_hit')

    @patch(
        'config.utils.emit_metric', side_effect=RuntimeError('boom'),
    )
    def test_a_metrics_failure_does_not_break_the_statement_view(
        self, _mock_emit,
    ):
        response = self.client.get(self.url, self._params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class StatementPDFViewTest(_LedgerAPITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('ledger:statement-pdf')

    def _params(self, **overrides):
        params = {
            'sacco_id': self.sacco.id,
            'from_date': '2026-01-01',
            'to_date': '2026-01-31',
        }
        params.update(overrides)
        return params

    def test_malformed_sacco_id_is_a_400_not_a_500(self):
        response = self.client.get(self.url, self._params(sacco_id='xx'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('ledger.views.generate_statement_pdf')
    def test_happy_path_returns_a_pdf_attachment(self, mock_generate):
        mock_generate.return_value = b'%PDF-1.4 fake pdf bytes'

        response = self.client.get(self.url, self._params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(
            'attachment; filename=', response['Content-Disposition'],
        )
        self.assertEqual(response.content, b'%PDF-1.4 fake pdf bytes')

    @patch('ledger.views.generate_statement_pdf')
    def test_pdf_backend_unavailable_returns_503(self, mock_generate):
        mock_generate.side_effect = OSError('libgobject not found')

        response = self.client.get(self.url, self._params())

        self.assertEqual(
            response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @patch('config.utils.emit_metric')
    @patch('ledger.views.generate_statement_pdf')
    def test_pdf_failure_is_logged_and_emits_a_metric(
        self, mock_generate, mock_emit,
    ):
        mock_generate.side_effect = OSError('libgobject not found')

        with self.assertLogs('saccosphere.ledger', level='ERROR'):
            response = self.client.get(self.url, self._params())

        self.assertEqual(
            response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        mock_emit.assert_any_call(
            'ledger_statement_pdf_failed', reason='OSError',
        )

    def test_pdf_generation_always_logs_access(self):
        before = DataConsentLog.objects.filter(
            user=self.user, data_type='MEMBER_STATEMENT',
        ).count()

        with patch('ledger.views.generate_statement_pdf') as mock_generate:
            mock_generate.return_value = b'%PDF-1.4'
            self.client.get(self.url, self._params())
            self.client.get(self.url, self._params())

        after = DataConsentLog.objects.filter(
            user=self.user, data_type='MEMBER_STATEMENT',
        ).count()
        self.assertEqual(after - before, 2)


class StatementPDFThrottleTest(SimpleTestCase):
    """The PDF endpoint has its own, tighter rate limit.

    A CPU-heavy synchronous WeasyPrint render should not share the
    blanket 1000/hour user throttle - see StatementPDFThrottle's
    docstring in ledger.views.
    """

    def test_view_uses_the_dedicated_throttle(self):
        self.assertEqual(
            StatementPDFView.throttle_classes, [StatementPDFThrottle],
        )

    def test_throttle_scope_has_a_configured_rate(self):
        throttle = StatementPDFThrottle()
        self.assertEqual(throttle.scope, 'ledger_statement_pdf')
        self.assertEqual(throttle.get_rate(), '30/hour')
