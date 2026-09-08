from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User, UserConsent
from saccomanagement.models import (
    ComplianceFlag,
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
            marketing_consent=True,
        )
        self._membership(
            email='second@example.com',
            phone_number='254712345002',
            member_number='SMS-M002',
            marketing_consent=True,
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
            marketing_consent=True,
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

    def test_create_campaign_excludes_members_without_marketing_consent(self):
        """A member who never gave MARKETING consent is not a campaign recipient."""
        consented_member = self._membership(
            email='consented@example.com',
            phone_number='254712345008',
            member_number='SMS-M008',
            marketing_consent=True,
        )
        self._membership(
            email='no-consent@example.com',
            phone_number='254712345009',
            member_number='SMS-M009',
        )

        response = self.client.post(
            '/api/v1/management/sms/campaigns/',
            {
                'message': 'Special offer for members.',
                'audience_filter': {'status': Membership.Status.APPROVED},
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 201)
        campaign = SMSCampaign.objects.get(id=response.json()['data']['id'])
        self.assertEqual(campaign.total_recipients, 1)
        self.assertEqual(campaign.recipients.get().membership, consented_member)

    def test_create_campaign_excludes_members_with_withdrawn_marketing_consent(self):
        """A member who withdrew MARKETING consent is not a campaign recipient."""
        withdrawn_user = User.objects.create_user(
            email='withdrawn@example.com',
            password='secret',
            phone_number='254712345010',
        )
        self._give_marketing_consent(withdrawn_user, withdrawn=True)
        Membership.objects.create(
            user=withdrawn_user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='SMS-M010',
        )

        response = self.client.post(
            '/api/v1/management/sms/campaigns/',
            {
                'message': 'Special offer for members.',
                'audience_filter': {'status': Membership.Status.APPROVED},
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 201)
        campaign = SMSCampaign.objects.get(id=response.json()['data']['id'])
        self.assertEqual(campaign.total_recipients, 0)

    @patch('notifications.tasks.send_bulk_sms_campaign_task.delay')
    def test_send_view_only_queues_draft_campaign(self, delay_mock):
        campaign = self._campaign(status=SMSCampaign.Status.DRAFT)

        response = self.client.post(
            f'/api/v1/management/sms/campaigns/{campaign.id}/send/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, 200)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, SMSCampaign.Status.QUEUED)
        delay_mock.assert_called_once_with(str(campaign.id))

    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_task_transitions_queued_to_sending_when_started(
        self, client_mock,
    ):
        from notifications.tasks import send_bulk_sms_campaign_task

        campaign = self._campaign(status=SMSCampaign.Status.QUEUED)
        member = self._membership(
            email='queued-to-sending@example.com',
            phone_number='254712345030',
            member_number='SMS-Q001',
        )
        SMSCampaignRecipient.objects.create(
            campaign=campaign,
            membership=member,
            phone_number=member.user.phone_number,
        )
        campaign.total_recipients = 1
        campaign.save(update_fields=['total_recipients'])
        status_during_send = []

        def _record_status_and_send(*args, **kwargs):
            campaign.refresh_from_db()
            status_during_send.append(campaign.status)
            return True

        client_mock.return_value.send_sms.side_effect = (
            _record_status_and_send
        )

        send_bulk_sms_campaign_task(str(campaign.id))

        self.assertEqual(status_during_send, [SMSCampaign.Status.SENDING])

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
        # Mixed outcome (some sent, some failed) is PARTIAL, not COMPLETED.
        self.assertEqual(campaign.status, SMSCampaign.Status.PARTIAL)
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

    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_campaign_with_every_recipient_failing_marked_failed(
        self, client_mock,
    ):
        """Every send attempt failing (not the daily limit) also ends FAILED."""
        from accounts.integrations.otp_service import ATSMSError
        from notifications.tasks import send_bulk_sms_campaign_task

        campaign = self._campaign(status=SMSCampaign.Status.SENDING)
        members = [
            self._membership(
                email=f'allfail-{index}@example.com',
                phone_number=f'25471234504{index}',
                member_number=f'SMS-AF{index}',
            )
            for index in range(2)
        ]
        for member in members:
            SMSCampaignRecipient.objects.create(
                campaign=campaign,
                membership=member,
                phone_number=member.user.phone_number,
            )
        campaign.total_recipients = 2
        campaign.save(update_fields=['total_recipients'])
        client_mock.return_value.send_sms.side_effect = ATSMSError('down')

        result = send_bulk_sms_campaign_task(str(campaign.id))

        campaign.refresh_from_db()
        self.assertEqual(result['sent'], 0)
        self.assertEqual(result['failed'], 2)
        self.assertEqual(campaign.status, SMSCampaign.Status.FAILED)

    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_task_exhausting_retries_marks_campaign_out_of_sending(
        self, client_mock,
    ):
        from notifications.tasks import send_bulk_sms_campaign_task

        campaign = self._campaign(status=SMSCampaign.Status.SENDING)
        member = self._membership(
            email='retries-exhausted@example.com',
            phone_number='254712345050',
            member_number='SMS-RE001',
        )
        SMSCampaignRecipient.objects.create(
            campaign=campaign,
            membership=member,
            phone_number=member.user.phone_number,
        )
        campaign.total_recipients = 1
        campaign.save(update_fields=['total_recipients'])
        client_mock.return_value.send_sms.side_effect = RuntimeError(
            'unexpected worker error',
        )

        with patch.object(send_bulk_sms_campaign_task, 'max_retries', 0):
            with self.assertRaises(RuntimeError):
                send_bulk_sms_campaign_task(str(campaign.id))

        campaign.refresh_from_db()
        self.assertNotEqual(campaign.status, SMSCampaign.Status.SENDING)
        self.assertEqual(campaign.status, SMSCampaign.Status.FAILED)

    def test_stuck_campaign_sweep_flags_campaign_past_timeout(self):
        from saccomanagement.tasks import flag_stuck_sms_campaigns

        stuck_campaign = self._campaign(status=SMSCampaign.Status.SENDING)
        SMSCampaign.objects.filter(id=stuck_campaign.id).update(
            updated_at=timezone.now() - timedelta(hours=2),
        )
        fresh_campaign = self._campaign(status=SMSCampaign.Status.SENDING)

        flagged_count = flag_stuck_sms_campaigns()

        self.assertEqual(flagged_count, 1)
        self.assertTrue(
            ComplianceFlag.objects.filter(
                sacco=self.sacco,
                flag_type=ComplianceFlag.FlagType.PERFORMANCE,
                metadata__campaign_id=str(stuck_campaign.id),
            ).exists(),
        )
        self.assertFalse(
            ComplianceFlag.objects.filter(
                metadata__campaign_id=str(fresh_campaign.id),
            ).exists(),
        )

    def test_stuck_campaign_sweep_does_not_duplicate_flags(self):
        from saccomanagement.tasks import flag_stuck_sms_campaigns

        stuck_campaign = self._campaign(status=SMSCampaign.Status.SENDING)
        SMSCampaign.objects.filter(id=stuck_campaign.id).update(
            updated_at=timezone.now() - timedelta(hours=2),
        )

        flag_stuck_sms_campaigns()
        flag_stuck_sms_campaigns()

        self.assertEqual(
            ComplianceFlag.objects.filter(
                metadata__campaign_id=str(stuck_campaign.id),
            ).count(),
            1,
        )

    def _membership(
        self,
        email,
        phone_number,
        member_number,
        sacco=None,
        status=Membership.Status.APPROVED,
        marketing_consent=False,
    ):
        user = User.objects.create_user(
            email=email,
            password='secret',
            phone_number=phone_number,
        )
        if marketing_consent:
            self._give_marketing_consent(user)
        return Membership.objects.create(
            user=user,
            sacco=sacco or self.sacco,
            status=status,
            member_number=member_number,
        )

    def _give_marketing_consent(self, user, withdrawn=False):
        consent = UserConsent.objects.create(
            user=user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version=settings.CONSENT_POLICY_VERSIONS['MARKETING'],
            consented=True,
        )
        if withdrawn:
            consent.withdrawn_at = timezone.now()
            consent.save(update_fields=['withdrawn_at'])
        return consent

    def _campaign(self, status):
        return SMSCampaign.objects.create(
            sacco=self.sacco,
            created_by=self.admin,
            message='Hello SACCO members.',
            audience_filter={'status': Membership.Status.APPROVED},
            status=status,
        )


class BulkSMSSendThrottleTests(TestCase):
    """
    The send-endpoint throttle is scoped per SACCO, not per admin user -
    distinct from and in addition to the daily-message-volume check inside
    the Celery task.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Throttle SACCO',
            registration_number='THR001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Other Throttle SACCO',
            registration_number='THR002',
            sector=Sacco.Sector.FINANCE,
            county='Kisumu',
        )
        self.admin = User.objects.create_user(
            email='throttle-admin@example.com', password='secret',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.other_admin = User.objects.create_user(
            email='throttle-other-admin@example.com', password='secret',
        )
        Role.objects.create(
            user=self.other_admin,
            sacco=self.other_sacco,
            name=Role.SACCO_ADMIN,
        )

    def _draft_campaign(self, sacco):
        return SMSCampaign.objects.create(
            sacco=sacco,
            message='Hi.',
            status=SMSCampaign.Status.DRAFT,
        )

    def _send(self, sacco):
        campaign = self._draft_campaign(sacco)
        return self.client.post(
            f'/api/v1/management/sms/campaigns/{campaign.id}/send/',
            HTTP_X_SACCO_ID=str(sacco.id),
        )

    @override_settings(BULK_SMS_SEND_THROTTLE_RATE='2/hour')
    @patch('notifications.tasks.send_bulk_sms_campaign_task.delay')
    def test_throttle_rejects_burst_beyond_limit_for_one_sacco(
        self, delay_mock,
    ):
        self.client.force_authenticate(user=self.admin)

        responses = [self._send(self.sacco) for _ in range(3)]

        self.assertEqual(
            [response.status_code for response in responses[:2]],
            [200, 200],
        )
        self.assertEqual(responses[2].status_code, 429)

    @override_settings(BULK_SMS_SEND_THROTTLE_RATE='2/hour')
    @patch('notifications.tasks.send_bulk_sms_campaign_task.delay')
    def test_throttle_does_not_affect_a_different_sacco(self, delay_mock):
        self.client.force_authenticate(user=self.admin)
        self._send(self.sacco)
        self._send(self.sacco)
        exhausted_response = self._send(self.sacco)
        self.assertEqual(exhausted_response.status_code, 429)

        self.client.force_authenticate(user=self.other_admin)
        other_response = self._send(self.other_sacco)

        self.assertEqual(other_response.status_code, 200)
