from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from saccomanagement.models import (
    Role,
    SMSCampaign,
    SMSCampaignRecipient,
)
from saccomembership.models import Membership
from services.models import Saving, SavingsType


class BulkSMSTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Bulk SMS SACCO',
            registration_number='SMS001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Other SMS SACCO',
            registration_number='SMS002',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )
        SaccoSettings.objects.create(
            sacco=self.sacco,
            sms_daily_limit=1000,
        )
        self.admin = User.objects.create_user(
            email='sms-admin@example.com',
            password='secret',
            first_name='SMS',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def test_create_campaign_builds_draft_recipient_preview(self):
        first_member = self._membership(
            email='first@example.com',
            phone_number='254712345001',
            member_number='SMS-M001',
        )
        self._membership(
            email='second@example.com',
            phone_number='254712345002',
            member_number='SMS-M002',
        )
        self._membership(
            email='pending@example.com',
            phone_number='254712345003',
            member_number='SMS-M003',
            status=Membership.Status.PENDING,
        )
        self._membership(
            email='no-phone@example.com',
            phone_number='',
            member_number='SMS-M004',
        )
        self._membership(
            email='other@example.com',
            phone_number='254712345004',
            member_number='SMS-M005',
            sacco=self.other_sacco,
        )

        response = self.client.post(
            '/api/v1/management/sms/campaigns/',
            {
                'message': 'Annual general meeting starts at 10am.',
                'audience_filter': {'status': Membership.Status.APPROVED},
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 201)
        campaign = SMSCampaign.objects.get(id=response.json()['data']['id'])
        self.assertEqual(campaign.status, SMSCampaign.Status.DRAFT)
        self.assertEqual(campaign.total_recipients, 2)
        self.assertEqual(campaign.created_by, self.admin)
        self.assertTrue(
            campaign.recipients.filter(membership=first_member).exists()
        )

    def test_create_campaign_rejects_unsafe_filter_key(self):
        response = self.client.post(
            '/api/v1/management/sms/campaigns/',
            {
                'message': 'Hello members.',
                'audience_filter': {'user__is_staff': True},
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SMSCampaign.objects.count(), 0)

    def test_create_campaign_supports_savings_type_filter(self):
        bosa_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        fosa_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.FOSA,
            minimum_contribution=Decimal('500.00'),
        )
        bosa_member = self._membership(
            email='bosa@example.com',
            phone_number='254712345006',
            member_number='SMS-M006',
        )
        fosa_member = self._membership(
            email='fosa@example.com',
            phone_number='254712345007',
            member_number='SMS-M007',
        )
        Saving.objects.create(
            membership=bosa_member,
            savings_type=bosa_type,
            amount=Decimal('1000.00'),
            status=Saving.Status.ACTIVE,
        )
        Saving.objects.create(
            membership=fosa_member,
            savings_type=fosa_type,
            amount=Decimal('1000.00'),
            status=Saving.Status.ACTIVE,
        )

        response = self.client.post(
            '/api/v1/management/sms/campaigns/',
            {
                'message': 'BOSA update.',
                'audience_filter': {
                    'status': Membership.Status.APPROVED,
                    'savings_type': SavingsType.Name.BOSA,
                },
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 201)
        campaign = SMSCampaign.objects.get(id=response.json()['data']['id'])
        self.assertEqual(campaign.total_recipients, 1)
        self.assertEqual(campaign.recipients.get().membership, bosa_member)

    @patch('notifications.tasks.send_bulk_sms_campaign_task.delay')
    def test_send_view_only_queues_draft_campaign(self, delay_mock):
        campaign = self._campaign(status=SMSCampaign.Status.DRAFT)

        response = self.client.post(
            f'/api/v1/management/sms/campaigns/{campaign.id}/send/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 200)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, SMSCampaign.Status.SENDING)
        delay_mock.assert_called_once_with(str(campaign.id))

    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_bulk_sms_task_obeys_daily_limit(self, client_mock):
        from notifications.tasks import send_bulk_sms_campaign_task

        SaccoSettings.objects.filter(sacco=self.sacco).update(
            sms_daily_limit=2,
        )
        campaign = self._campaign(status=SMSCampaign.Status.SENDING)
        members = [
            self._membership(
                email=f'task-{index}@example.com',
                phone_number=f'25471234501{index}',
                member_number=f'SMS-T{index}',
            )
            for index in range(3)
        ]
        for member in members:
            SMSCampaignRecipient.objects.create(
                campaign=campaign,
                membership=member,
                phone_number=member.user.phone_number,
            )
        campaign.total_recipients = 3
        campaign.save(update_fields=['total_recipients'])
        client_mock.return_value.send_sms.return_value = True

        result = send_bulk_sms_campaign_task(str(campaign.id))

        campaign.refresh_from_db()
        self.assertEqual(result['sent'], 2)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(campaign.status, SMSCampaign.Status.COMPLETED)
        self.assertEqual(campaign.sent_count, 2)
        self.assertEqual(campaign.failed_count, 1)
        self.assertEqual(
            campaign.recipients.filter(
                status=SMSCampaignRecipient.Status.FAILED,
                error_message='daily SMS limit reached',
            ).count(),
            1,
        )

    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_bulk_sms_task_fails_campaign_when_limit_is_exhausted(
        self,
        client_mock,
    ):
        from notifications.tasks import send_bulk_sms_campaign_task

        SaccoSettings.objects.filter(sacco=self.sacco).update(
            sms_daily_limit=1,
        )
        previous_campaign = self._campaign(
            status=SMSCampaign.Status.COMPLETED,
        )
        previous_member = self._membership(
            email='previous@example.com',
            phone_number='254712345020',
            member_number='SMS-P001',
        )
        SMSCampaignRecipient.objects.create(
            campaign=previous_campaign,
            membership=previous_member,
            phone_number=previous_member.user.phone_number,
            status=SMSCampaignRecipient.Status.SENT,
            sent_at=timezone.now(),
        )
        campaign = self._campaign(status=SMSCampaign.Status.SENDING)
        member = self._membership(
            email='limited@example.com',
            phone_number='254712345021',
            member_number='SMS-L001',
        )
        SMSCampaignRecipient.objects.create(
            campaign=campaign,
            membership=member,
            phone_number=member.user.phone_number,
        )
        campaign.total_recipients = 1
        campaign.save(update_fields=['total_recipients'])

        result = send_bulk_sms_campaign_task(str(campaign.id))

        campaign.refresh_from_db()
        self.assertEqual(result['sent'], 0)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(campaign.status, SMSCampaign.Status.FAILED)
        client_mock.return_value.send_sms.assert_not_called()

    def _membership(
        self,
        email,
        phone_number,
        member_number,
        sacco=None,
        status=Membership.Status.APPROVED,
    ):
        user = User.objects.create_user(
            email=email,
            password='secret',
            phone_number=phone_number,
        )
        return Membership.objects.create(
            user=user,
            sacco=sacco or self.sacco,
            status=status,
            member_number=member_number,
        )

    def _campaign(self, status):
        return SMSCampaign.objects.create(
            sacco=self.sacco,
            created_by=self.admin,
            message='Hello SACCO members.',
            audience_filter={'status': Membership.Status.APPROVED},
            status=status,
        )
