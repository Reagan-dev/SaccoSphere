"""KYCVerification.id_number / CRBCheck.raw_response are encrypted at rest."""

from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from accounts.models import KYCVerification, Sacco, User
from saccomembership.models import Membership
from services.models import CRBCheck, Loan, LoanType


def _raw_column(table, column):
    """Every value of one column, straight from the DB (no ORM field)."""
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {column} FROM {table}')
        return [row[0] for row in cursor.fetchall()]


def _fernet_looking(value):
    return isinstance(value, str) and value.startswith('gAAAAA')


class EncryptedIdNumberTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='enc-kyc@example.com',
            phone_number='254700009001',
            password='StrongPass1',
        )

    def test_id_number_ciphertext_in_db_plaintext_via_orm(self):
        kyc = KYCVerification.objects.create(
            user=self.user,
            id_number='12345678',
        )

        raw = _raw_column('accounts_kycverification', 'id_number')[0]
        self.assertNotEqual(raw, '12345678')
        self.assertNotIn('12345678', raw)
        self.assertTrue(_fernet_looking(raw))

        # ORM round-trips to plaintext.
        self.assertEqual(
            KYCVerification.objects.get(pk=kyc.pk).id_number,
            '12345678',
        )

    def test_encryption_is_non_deterministic(self):
        field = KYCVerification._meta.get_field('id_number')
        self.assertNotEqual(
            field.get_prep_value('12345678'),
            field.get_prep_value('12345678'),
        )

    def test_normalized_id_number_still_queryable(self):
        KYCVerification.objects.create(user=self.user, id_number='12 345 678')

        # normalized_id_number is the plaintext lookup key.
        self.assertTrue(
            KYCVerification.objects.filter(
                normalized_id_number='12345678',
            ).exists()
        )


class EncryptedCRBRawResponseTests(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Enc CRB SACCO',
            registration_number='ENC-CRB-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        user = User.objects.create_user(
            email='enc-crb@example.com',
            phone_number='254700009010',
            password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ENC-CRB-M-001',
            approved_date=timezone.now(),
        )
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Enc CRB Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
        )
        self.loan = Loan.objects.create(
            membership=membership,
            loan_type=loan_type,
            amount=Decimal('50000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('50000.00'),
        )

    def test_raw_response_ciphertext_in_db_dict_via_orm(self):
        payload = {
            'checked': True,
            'score': 720,
            'band': 'GOOD',
            'id_number': '12345678',
            'reference': 'METROPOL-XYZ',
        }
        crb = CRBCheck.objects.create(
            loan=self.loan,
            score=720,
            band='GOOD',
            reference='METROPOL-XYZ',
            raw_response=payload,
        )

        raw = _raw_column('services_crbcheck', 'raw_response')[0]

        self.assertTrue(_fernet_looking(raw))
        self.assertNotIn('12345678', raw)
        self.assertNotIn('METROPOL-XYZ', raw)

        # ORM round-trips to the original dict.
        self.assertEqual(
            CRBCheck.objects.get(pk=crb.pk).raw_response,
            payload,
        )

    def test_purge_date_stamped_when_retention_configured(self):
        with self.settings(CRB_RAW_RESPONSE_RETENTION_DAYS=365):
            crb = CRBCheck.objects.create(
                loan=self.loan,
                score=600,
                band='FAIR',
                reference='METROPOL-ABC',
                raw_response={'score': 600},
            )
        self.assertIsNotNone(crb.raw_response_purge_at)

        # Clearing raw_response clears the purge date.
        crb.raw_response = None
        crb.save(update_fields=['raw_response', 'raw_response_purge_at'])
        crb.refresh_from_db()
        self.assertIsNone(crb.raw_response_purge_at)

    def test_retention_sweep_clears_expired_raw_response_keeps_facts(self):
        from django.core.management import call_command

        expired = CRBCheck.objects.create(
            loan=self.loan,
            score=650,
            band='GOOD',
            reference='METROPOL-EXPIRED',
            raw_response={'score': 650, 'id_number': '12345678'},
        )
        CRBCheck.objects.filter(pk=expired.pk).update(
            raw_response_purge_at=timezone.now() - timezone.timedelta(days=1),
        )
        fresh = CRBCheck.objects.create(
            loan=self.loan,
            score=700,
            band='VERY_GOOD',
            reference='METROPOL-FRESH',
            raw_response={'score': 700},
        )
        CRBCheck.objects.filter(pk=fresh.pk).update(
            raw_response_purge_at=timezone.now() + timezone.timedelta(days=30),
        )

        with self.settings(CRB_RAW_RESPONSE_RETENTION_DAYS=365):
            call_command('purge_expired_crb_raw_response')

        expired.refresh_from_db()
        fresh.refresh_from_db()
        self.assertIsNone(expired.raw_response)
        self.assertIsNone(expired.raw_response_purge_at)
        # Decision facts survive the purge.
        self.assertEqual(expired.score, 650)
        self.assertEqual(expired.band, 'GOOD')
        self.assertEqual(expired.reference, 'METROPOL-EXPIRED')
        # A not-yet-expired row is untouched.
        self.assertEqual(fresh.raw_response, {'score': 700})
