"""Tests for the notifications app: in-app notifications, device-token
registration, and the push/SMS/email delivery tasks.
"""
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.integrations.otp_service import (
    ATSMSDeliveryUncertainError,
    ATSMSError,
    ATSMSInvalidRecipientError,
)
from notifications.integrations.fcm_push import FCMError, FCMPushClient
from notifications.models import DeviceToken, Notification
from notifications.tasks import (
    notify_user_task,
    send_email_task,
    send_push_notification_task,
    send_sms_task,
)
from notifications.tasks import purge_expired_notification_content
from notifications.utils import create_notification


User = get_user_model()


def _create_user(email, **extra_fields):
    return User.objects.create_user(
        email=email,
        password='secret',
        first_name='Test',
        last_name='User',
        **extra_fields,
    )


class NotificationListViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _create_user('member@example.com')
        self.other_user = _create_user('other-member@example.com')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('notifications:notification-list')

    def test_list_only_returns_the_authenticated_users_notifications(self):
        Notification.objects.create(
            user=self.user,
            title='Mine',
            message='Belongs to me',
        )
        Notification.objects.create(
            user=self.other_user,
            title='Not mine',
            message='Belongs to someone else',
        )

        response = self.client.get(self.url)

        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Mine')

    def test_list_filters_by_category(self):
        Notification.objects.create(
            user=self.user,
            title='Loan update',
            message='...',
            category=Notification.Category.LOAN,
        )
        Notification.objects.create(
            user=self.user,
            title='System note',
            message='...',
            category=Notification.Category.SYSTEM,
        )

        response = self.client.get(self.url, {'category': 'LOAN'})

        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['category'], 'LOAN')

    def test_list_filters_by_is_read(self):
        Notification.objects.create(
            user=self.user,
            title='Read one',
            message='...',
            is_read=True,
        )
        Notification.objects.create(
            user=self.user,
            title='Unread one',
            message='...',
            is_read=False,
        )

        response = self.client.get(self.url, {'is_read': 'false'})

        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Unread one')

    def test_list_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class MarkReadViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _create_user('member@example.com')
        self.other_user = _create_user('other-member@example.com')
        self.client.force_authenticate(user=self.user)

    def test_marks_own_notification_as_read(self):
        notification = Notification.objects.create(
            user=self.user,
            title='Mine',
            message='...',
        )

        response = self.client.post(
            reverse(
                'notifications:notification-read',
                kwargs={'id': notification.id},
            ),
        )

        notification.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(notification.is_read)

    def test_cannot_mark_another_users_notification_as_read(self):
        notification = Notification.objects.create(
            user=self.other_user,
            title='Not mine',
            message='...',
        )

        response = self.client.post(
            reverse(
                'notifications:notification-read',
                kwargs={'id': notification.id},
            ),
        )

        notification.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(notification.is_read)


class MarkAllReadViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _create_user('member@example.com')
        self.other_user = _create_user('other-member@example.com')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('notifications:notification-read-all')

    def test_marks_only_the_users_unread_notifications(self):
        Notification.objects.create(
            user=self.user,
            title='Unread A',
            message='...',
        )
        Notification.objects.create(
            user=self.user,
            title='Unread B',
            message='...',
        )
        already_read = Notification.objects.create(
            user=self.user,
            title='Already read',
            message='...',
            is_read=True,
        )
        other_users_notification = Notification.objects.create(
            user=self.other_user,
            title='Not mine',
            message='...',
        )

        response = self.client.post(self.url)

        self.assertEqual(response.data['count'], 2)
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, is_read=False,
            ).count(),
            0,
        )
        already_read.refresh_from_db()
        other_users_notification.refresh_from_db()
        self.assertTrue(already_read.is_read)
        self.assertFalse(other_users_notification.is_read)


class DeviceTokenRegisterViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _create_user('member@example.com')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('notifications:device-token-register')

    def test_registers_a_new_device_token(self):
        response = self.client.post(
            self.url,
            {'token': 'device-token-1', 'platform': 'ANDROID'},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        device_token = DeviceToken.objects.get(token='device-token-1')
        self.assertEqual(device_token.user, self.user)
        self.assertTrue(device_token.is_active)

    def test_reregistering_the_same_token_reactivates_it(self):
        DeviceToken.objects.create(
            user=self.user,
            token='device-token-1',
            platform=DeviceToken.Platform.ANDROID,
            is_active=False,
        )

        response = self.client.post(
            self.url,
            {'token': 'device-token-1', 'platform': 'ANDROID'},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        device_token = DeviceToken.objects.get(token='device-token-1')
        self.assertTrue(device_token.is_active)

    def test_registering_a_brand_new_token_does_not_log_reassignment(self):
        from saccomanagement.models import SystemAuditLog

        self.client.post(
            self.url,
            {'token': 'device-token-1', 'platform': 'ANDROID'},
        )

        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='DEVICE_TOKEN_REASSIGNED',
            ).exists(),
        )

    def test_reactivating_own_token_does_not_log_reassignment(self):
        from saccomanagement.models import SystemAuditLog

        DeviceToken.objects.create(
            user=self.user,
            token='device-token-1',
            platform=DeviceToken.Platform.ANDROID,
            is_active=False,
        )

        self.client.post(
            self.url,
            {'token': 'device-token-1', 'platform': 'ANDROID'},
        )

        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='DEVICE_TOKEN_REASSIGNED',
            ).exists(),
        )

    def test_claiming_another_users_token_logs_reassignment(self):
        from saccomanagement.models import SystemAuditLog

        previous_owner = _create_user('previous-owner@example.com')
        DeviceToken.objects.create(
            user=previous_owner,
            token='shared-device-token',
            platform=DeviceToken.Platform.ANDROID,
        )

        response = self.client.post(
            self.url,
            {'token': 'shared-device-token', 'platform': 'ANDROID'},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        device_token = DeviceToken.objects.get(token='shared-device-token')
        self.assertEqual(device_token.user, self.user)
        audit_entry = SystemAuditLog.objects.get(
            action='DEVICE_TOKEN_REASSIGNED',
        )
        self.assertEqual(
            audit_entry.old_values['user_id'], str(previous_owner.id),
        )
        self.assertEqual(
            audit_entry.new_values['user_id'], str(self.user.id),
        )


class CreateNotificationTests(TestCase):
    def setUp(self):
        self.user = _create_user('member@example.com')

    def test_creates_an_in_app_notification_row(self):
        notification = create_notification(
            user=self.user,
            title='Hello',
            message='World',
            dispatch_async=False,
        )

        self.assertIsNotNone(notification)
        self.assertEqual(Notification.objects.count(), 1)

    def test_returns_none_and_does_not_raise_on_db_failure(self):
        with patch(
            'notifications.utils.Notification.objects.create',
            side_effect=RuntimeError('db down'),
        ):
            notification = create_notification(
                user=self.user,
                title='Hello',
                message='World',
            )

        self.assertIsNone(notification)

    @patch('notifications.tasks.notify_user_task.s')
    def test_dispatches_push_for_payment_category_by_default(self, task_s):
        create_notification(
            user=self.user,
            title='Payment confirmed',
            message='...',
            category=Notification.Category.PAYMENT,
        )

        task_s.assert_called_once()

    @patch('notifications.tasks.notify_user_task.s')
    def test_does_not_dispatch_for_system_category_by_default(self, task_s):
        create_notification(
            user=self.user,
            title='System note',
            message='...',
            category=Notification.Category.SYSTEM,
        )

        task_s.assert_not_called()

    @patch('notifications.tasks.notify_user_task.s')
    def test_dispatch_async_false_never_dispatches(self, task_s):
        create_notification(
            user=self.user,
            title='Payment confirmed',
            message='...',
            category=Notification.Category.PAYMENT,
            dispatch_async=False,
        )

        task_s.assert_not_called()


class NotifyUserTaskTests(TestCase):
    def setUp(self):
        self.user = _create_user(
            'member@example.com', phone_number='254712345678',
        )

    @patch('notifications.tasks.chain')
    def test_creates_in_app_notification_by_default(self, mock_chain):
        notification_id = notify_user_task(
            str(self.user.id),
            'Title',
            'Message',
            Notification.Category.LOAN,
        )

        self.assertIsNotNone(notification_id)
        self.assertEqual(Notification.objects.count(), 1)

    @patch('notifications.tasks.chain')
    def test_skips_in_app_notification_when_disabled(self, mock_chain):
        result = notify_user_task(
            str(self.user.id),
            'Title',
            'Message',
            Notification.Category.LOAN,
            create_in_app=False,
        )

        self.assertIsNone(result)
        self.assertEqual(Notification.objects.count(), 0)

    @patch('notifications.tasks.send_sms_task.s')
    @patch('notifications.tasks.send_push_notification_task.s')
    @patch('notifications.tasks.chain')
    def test_queues_push_by_default(
        self, mock_chain, push_signature, sms_signature,
    ):
        notify_user_task(
            str(self.user.id),
            'Title',
            'Message',
            Notification.Category.LOAN,
        )

        push_signature.assert_called_once()
        sms_signature.assert_not_called()

    @patch('notifications.tasks.send_push_notification_task.s')
    @patch('notifications.tasks.chain')
    def test_push_signature_carries_the_in_app_notification_id(
        self, mock_chain, push_signature,
    ):
        notify_user_task(
            str(self.user.id),
            'Title',
            'Message',
            Notification.Category.LOAN,
        )

        notification = Notification.objects.get()
        self.assertEqual(
            push_signature.call_args.kwargs['notification_id'],
            str(notification.id),
        )

    @patch('notifications.tasks.send_sms_task.s')
    @patch('notifications.tasks.send_push_notification_task.s')
    @patch('notifications.tasks.chain')
    def test_queues_sms_only_when_requested_and_phone_present(
        self, mock_chain, push_signature, sms_signature,
    ):
        notify_user_task(
            str(self.user.id),
            'Title',
            'Message',
            Notification.Category.LOAN,
            send_sms=True,
        )

        sms_signature.assert_called_once_with(
            self.user.phone_number, 'Message',
        )

    @patch('notifications.tasks.send_sms_task.s')
    @patch('notifications.tasks.chain')
    def test_skips_sms_when_user_has_no_phone_number(
        self, mock_chain, sms_signature,
    ):
        user_without_phone = _create_user('no-phone@example.com')

        notify_user_task(
            str(user_without_phone.id),
            'Title',
            'Message',
            Notification.Category.LOAN,
            send_sms=True,
        )

        sms_signature.assert_not_called()

    def test_returns_none_for_unknown_user(self):
        result = notify_user_task(
            '00000000-0000-0000-0000-000000000000',
            'Title',
            'Message',
            Notification.Category.LOAN,
        )

        self.assertIsNone(result)

    @patch.object(notify_user_task, 'retry')
    @patch('notifications.tasks.chain')
    def test_dispatch_failure_retries_with_notification_id_to_avoid_dup(
        self, mock_chain, mock_retry,
    ):
        """A retry must carry the already-created Notification's id so a
        re-run of this task (after the push/SMS dispatch step failed)
        does not create a second in-app row."""
        mock_chain.return_value.delay.side_effect = RuntimeError(
            'broker down',
        )
        mock_retry.side_effect = RuntimeError('broker down')

        with self.assertRaises(RuntimeError):
            notify_user_task(
                str(self.user.id),
                'Title',
                'Message',
                Notification.Category.LOAN,
            )

        notification = Notification.objects.get()
        mock_retry.assert_called_once()
        self.assertEqual(
            mock_retry.call_args.kwargs['kwargs']['_notification_id'],
            str(notification.id),
        )

    @patch('notifications.tasks.chain')
    def test_retry_with_existing_notification_id_reuses_it(
        self, mock_chain,
    ):
        existing = Notification.objects.create(
            user=self.user,
            title='Title',
            message='Message',
            category=Notification.Category.LOAN,
        )

        result = notify_user_task(
            str(self.user.id),
            'Title',
            'Message',
            Notification.Category.LOAN,
            _notification_id=str(existing.id),
        )

        self.assertEqual(result, str(existing.id))
        self.assertEqual(Notification.objects.count(), 1)

    @patch('notifications.tasks.chain')
    def test_dispatch_exhausted_retries_raises(self, mock_chain):
        mock_chain.return_value.delay.side_effect = RuntimeError(
            'broker down',
        )

        with patch.object(notify_user_task, 'max_retries', 0):
            with self.assertRaises(RuntimeError):
                notify_user_task(
                    str(self.user.id),
                    'Title',
                    'Message',
                    Notification.Category.LOAN,
                )


class SendSmsTaskTests(TestCase):
    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_success_returns_true(self, client_class):
        client_class.return_value.send_sms.return_value = True

        result = send_sms_task('254712345678', 'hello')

        self.assertTrue(result)

    @patch('notifications.tasks.emit_metric')
    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_non_retryable_error_returns_false_without_raising(
        self, client_class, emit_metric_mock,
    ):
        client_class.return_value.send_sms.side_effect = (
            ATSMSInvalidRecipientError('bad number')
        )

        result = send_sms_task('254712345678', 'hello')

        self.assertFalse(result)
        emit_metric_mock.assert_called_once_with(
            'sms_notification_failed',
            reason='ATSMSInvalidRecipientError',
            retryable=False,
        )

    @patch('notifications.tasks.emit_metric')
    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_retryable_error_propagates(
        self, client_class, emit_metric_mock,
    ):
        client_class.return_value.send_sms.side_effect = ATSMSError(
            'gateway timeout',
        )

        with self.assertRaises(ATSMSError):
            send_sms_task('254712345678', 'hello')

        self.assertEqual(
            emit_metric_mock.call_args.kwargs.get('outcome'),
            'retrying',
        )

    @patch('notifications.tasks.emit_metric')
    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_retryable_error_reports_exhausted_once_out_of_retries(
        self, client_class, emit_metric_mock,
    ):
        client_class.return_value.send_sms.side_effect = ATSMSError(
            'gateway timeout',
        )

        with patch.object(send_sms_task, 'max_retries', 0):
            with self.assertRaises(ATSMSError):
                send_sms_task('254712345678', 'hello')

        self.assertEqual(
            emit_metric_mock.call_args.kwargs.get('outcome'),
            'exhausted',
        )

    @patch('notifications.tasks.emit_metric')
    @patch('accounts.integrations.otp_service.ATSMSClient')
    def test_uncertain_delivery_returns_false_without_retrying(
        self, client_class, emit_metric_mock,
    ):
        """A read-phase timeout means Africa's Talking may already have
        queued the message - send_sms_task must not retry (that would
        risk a duplicate, chargeable SMS) and must report it distinctly
        from a clean rejection."""
        client_class.return_value.send_sms.side_effect = (
            ATSMSDeliveryUncertainError('read timeout')
        )

        result = send_sms_task('254712345678', 'hello')

        self.assertFalse(result)
        emit_metric_mock.assert_called_once_with(
            'sms_notification_failed',
            reason='ATSMSDeliveryUncertainError',
            retryable=False,
            outcome='uncertain',
        )


class SendEmailTaskTests(TestCase):
    def test_success_sends_mail(self):
        result = send_email_task(
            'admin@example.com', 'Subject', 'Body text',
        )

        self.assertEqual(result, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['admin@example.com'])

    @patch('notifications.tasks.emit_metric')
    @patch('notifications.tasks.send_mail')
    def test_failure_exhausted_raises_and_reports_metric(
        self, send_mail_mock, emit_metric_mock,
    ):
        send_mail_mock.side_effect = RuntimeError('smtp down')

        with patch.object(send_email_task, 'max_retries', 0):
            with self.assertRaises(RuntimeError):
                send_email_task('admin@example.com', 'Subject', 'Body')

        self.assertEqual(
            emit_metric_mock.call_args.kwargs.get('outcome'),
            'exhausted',
        )

    @patch('notifications.tasks.emit_metric')
    @patch('notifications.tasks.send_mail')
    def test_server_disconnected_mid_send_returns_false_without_retrying(
        self, send_mail_mock, emit_metric_mock,
    ):
        """The SMTP session can drop after the message body was already
        flushed to the server but before we see the final response - a
        retry here could send the same email twice, so this must not be
        retried."""
        import smtplib

        send_mail_mock.side_effect = smtplib.SMTPServerDisconnected(
            'connection lost',
        )

        result = send_email_task('admin@example.com', 'Subject', 'Body')

        self.assertFalse(result)
        emit_metric_mock.assert_called_once_with(
            'email_notification_failed',
            reason='SMTPServerDisconnected',
            retryable=False,
            outcome='uncertain',
        )

    @patch('notifications.tasks.emit_metric')
    @patch('notifications.tasks.send_mail')
    def test_timeout_returns_false_without_retrying(
        self, send_mail_mock, emit_metric_mock,
    ):
        send_mail_mock.side_effect = TimeoutError('read timed out')

        result = send_email_task('admin@example.com', 'Subject', 'Body')

        self.assertFalse(result)
        emit_metric_mock.assert_called_once_with(
            'email_notification_failed',
            reason='TimeoutError',
            retryable=False,
            outcome='uncertain',
        )


@override_settings(
    FCM_PROJECT_ID='test-project',
    FCM_CREDENTIALS_JSON='{"type": "service_account"}',
    DEBUG=False,
)
class SendPushNotificationTaskTests(TestCase):
    def setUp(self):
        self.user = _create_user('member@example.com')

    def test_sends_to_every_active_token_and_skips_inactive_ones(self):
        DeviceToken.objects.create(
            user=self.user, token='active-1',
            platform=DeviceToken.Platform.ANDROID,
        )
        DeviceToken.objects.create(
            user=self.user, token='inactive-1',
            platform=DeviceToken.Platform.ANDROID, is_active=False,
        )

        with patch(
            'notifications.integrations.fcm_push.FCMPushClient',
        ) as client_class:
            client_class.return_value.send.return_value = {'success': 1}
            sent_count = send_push_notification_task(
                str(self.user.id), 'Title', 'Body',
            )

        self.assertEqual(sent_count, 1)

    def test_marks_notification_push_sent_on_success(self):
        DeviceToken.objects.create(
            user=self.user, token='active-1',
            platform=DeviceToken.Platform.ANDROID,
        )
        notification = Notification.objects.create(
            user=self.user,
            title='Title',
            message='Body',
            category=Notification.Category.LOAN,
        )
        self.assertFalse(notification.push_sent)

        with patch(
            'notifications.integrations.fcm_push.FCMPushClient',
        ) as client_class:
            client_class.return_value.send.return_value = {'success': 1}
            send_push_notification_task(
                str(self.user.id),
                'Title',
                'Body',
                notification_id=str(notification.id),
            )

        notification.refresh_from_db()
        self.assertTrue(notification.push_sent)

    def test_does_not_mark_push_sent_when_no_device_received_it(self):
        notification = Notification.objects.create(
            user=self.user,
            title='Title',
            message='Body',
            category=Notification.Category.LOAN,
        )

        send_push_notification_task(
            str(self.user.id),
            'Title',
            'Body',
            notification_id=str(notification.id),
        )

        notification.refresh_from_db()
        self.assertFalse(notification.push_sent)

    def test_deactivates_token_on_invalid_registration_without_raising(self):
        token = DeviceToken.objects.create(
            user=self.user, token='dead-token',
            platform=DeviceToken.Platform.ANDROID,
        )

        with patch(
            'notifications.integrations.fcm_push.FCMPushClient',
        ) as client_class:
            client_class.return_value.send.side_effect = FCMError(
                'gone', error_status='UNREGISTERED',
            )
            sent_count = send_push_notification_task(
                str(self.user.id), 'Title', 'Body',
            )

        token.refresh_from_db()
        self.assertFalse(token.is_active)
        self.assertEqual(sent_count, 0)

    @patch('notifications.tasks.emit_metric')
    def test_permanent_failure_raises_without_retrying(
        self, emit_metric_mock,
    ):
        DeviceToken.objects.create(
            user=self.user, token='some-token',
            platform=DeviceToken.Platform.ANDROID,
        )

        with patch(
            'notifications.integrations.fcm_push.FCMPushClient',
        ) as client_class:
            client_class.return_value.send.side_effect = FCMError(
                'bad credentials', error_status='UNAUTHENTICATED',
            )
            with self.assertRaises(FCMError):
                send_push_notification_task(
                    str(self.user.id), 'Title', 'Body',
                )

        self.assertEqual(
            emit_metric_mock.call_args.kwargs.get('retryable'),
            False,
        )

    @patch('notifications.tasks.emit_metric')
    def test_transient_failure_propagates_for_retry(self, emit_metric_mock):
        DeviceToken.objects.create(
            user=self.user, token='some-token',
            platform=DeviceToken.Platform.ANDROID,
        )

        with patch(
            'notifications.integrations.fcm_push.FCMPushClient',
        ) as client_class:
            client_class.return_value.send.side_effect = FCMError(
                'unavailable', error_status='UNAVAILABLE',
            )
            with self.assertRaises(FCMError):
                send_push_notification_task(
                    str(self.user.id), 'Title', 'Body',
                )

        self.assertEqual(
            emit_metric_mock.call_args.kwargs.get('outcome'),
            'retrying',
        )


class FCMErrorTests(TestCase):
    def test_unregistered_is_invalid_registration_but_not_permanent(self):
        error = FCMError('gone', error_status='UNREGISTERED')

        self.assertTrue(error.invalid_registration)
        self.assertFalse(error.is_permanent)

    def test_unauthenticated_is_permanent_but_not_invalid_registration(self):
        error = FCMError('bad creds', error_status='UNAUTHENTICATED')

        self.assertFalse(error.invalid_registration)
        self.assertTrue(error.is_permanent)

    def test_unavailable_is_neither_permanent_nor_invalid_registration(self):
        error = FCMError('busy', error_status='UNAVAILABLE')

        self.assertFalse(error.invalid_registration)
        self.assertFalse(error.is_permanent)


@override_settings(DEBUG=False)
class FCMPushClientTests(TestCase):
    def test_debug_mode_short_circuits_without_project_id(self):
        with override_settings(DEBUG=True):
            client = FCMPushClient()
            result = client.send('token', 'Title', 'Body')

        self.assertEqual(result, {'success': 1, 'debug': True})

    def test_raises_unconfigured_when_project_id_missing(self):
        with override_settings(FCM_PROJECT_ID='', FCM_CREDENTIALS_JSON=''):
            client = FCMPushClient()
            with self.assertRaises(FCMError) as ctx:
                client.send('token', 'Title', 'Body')

        self.assertEqual(ctx.exception.error_status, 'UNCONFIGURED')

    @override_settings(
        FCM_PROJECT_ID='test-project',
        FCM_CREDENTIALS_JSON='{"type": "service_account"}',
    )
    @patch('notifications.integrations.fcm_push.requests.post')
    def test_send_success_posts_v1_payload_and_returns_response(
        self, post_mock,
    ):
        post_mock.return_value = Mock(
            status_code=200, json=lambda: {'name': 'projects/x/messages/1'},
        )
        client = FCMPushClient()
        with patch.object(
            client, '_get_access_token', return_value='token-abc',
        ):
            result = client.send(
                'device-token', 'Title', 'Body', {'key': 1},
            )

        self.assertEqual(result, {'name': 'projects/x/messages/1'})
        called_url = post_mock.call_args.args[0]
        self.assertIn('test-project', called_url)
        sent_payload = post_mock.call_args.kwargs['json']
        self.assertEqual(sent_payload['message']['token'], 'device-token')
        self.assertEqual(sent_payload['message']['data'], {'key': '1'})
        self.assertEqual(
            post_mock.call_args.kwargs['headers']['Authorization'],
            'Bearer token-abc',
        )

    @override_settings(
        FCM_PROJECT_ID='test-project',
        FCM_CREDENTIALS_JSON='{"type": "service_account"}',
    )
    @patch('notifications.integrations.fcm_push.requests.post')
    def test_send_classifies_v1_error_body(self, post_mock):
        post_mock.return_value = Mock(
            status_code=400,
            json=lambda: {
                'error': {
                    'status': 'INVALID_ARGUMENT',
                    'message': 'Invalid registration token',
                    'details': [
                        {
                            '@type': (
                                'type.googleapis.com/google.firebase'
                                '.fcm.v1.FcmError'
                            ),
                            'errorCode': 'UNREGISTERED',
                        },
                    ],
                },
            },
        )
        client = FCMPushClient()
        with patch.object(
            client, '_get_access_token', return_value='token-abc',
        ):
            with self.assertRaises(FCMError) as ctx:
                client.send('device-token', 'Title', 'Body')

        self.assertEqual(ctx.exception.error_status, 'UNREGISTERED')
        self.assertTrue(ctx.exception.invalid_registration)

    @override_settings(
        FCM_PROJECT_ID='test-project',
        FCM_CREDENTIALS_JSON='{"type": "service_account"}',
    )
    def test_access_token_is_cached_between_sends(self):
        client = FCMPushClient()
        fake_credentials = Mock(token='cached-token', expiry=None)

        with patch(
            'notifications.integrations.fcm_push.service_account'
            '.Credentials.from_service_account_info',
            return_value=fake_credentials,
        ) as from_info:
            with patch(
                'notifications.integrations.fcm_push.GoogleAuthRequest',
            ):
                first = client._get_access_token()
                second = client._get_access_token()

        self.assertEqual(first, 'cached-token')
        self.assertEqual(second, 'cached-token')
        from_info.assert_called_once()


class PurgeExpiredNotificationContentTests(TestCase):
    def setUp(self):
        self.user = _create_user('member@example.com')

    def _backdated_notification(self, days_old, **extra_fields):
        notification = Notification.objects.create(
            user=self.user,
            title='Loan approved',
            message='Your loan of KES 10,000 has been approved.',
            category=Notification.Category.LOAN,
            action_url='/loans/1/',
            related_object_type='Loan',
            related_object_id='1',
            **extra_fields,
        )
        Notification.objects.filter(pk=notification.pk).update(
            created_at=timezone.now() - timedelta(days=days_old),
        )
        notification.refresh_from_db()
        return notification

    @override_settings(NOTIFICATION_CONTENT_RETENTION_DAYS=90)
    def test_clears_content_for_expired_notifications_keeps_facts(self):
        expired = self._backdated_notification(days_old=91, is_read=True)
        fresh = self._backdated_notification(days_old=1)

        call_command('purge_expired_notification_content')

        expired.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(expired.title, '[removed]')
        self.assertNotIn('KES 10,000', expired.message)
        self.assertIsNone(expired.action_url)
        # Audit-trail-shaped fields survive the purge.
        self.assertEqual(expired.category, Notification.Category.LOAN)
        self.assertTrue(expired.is_read)
        self.assertEqual(expired.related_object_type, 'Loan')
        self.assertEqual(expired.related_object_id, '1')
        # A not-yet-expired row is untouched.
        self.assertEqual(fresh.title, 'Loan approved')

    @override_settings(NOTIFICATION_CONTENT_RETENTION_DAYS=90)
    def test_is_idempotent_on_already_purged_rows(self):
        expired = self._backdated_notification(days_old=91)

        call_command('purge_expired_notification_content')
        call_command('purge_expired_notification_content')

        expired.refresh_from_db()
        self.assertEqual(expired.title, '[removed]')

    @override_settings(NOTIFICATION_CONTENT_RETENTION_DAYS=90)
    def test_dry_run_makes_no_changes(self):
        expired = self._backdated_notification(days_old=91)

        call_command('purge_expired_notification_content', '--dry-run')

        expired.refresh_from_db()
        self.assertEqual(expired.title, 'Loan approved')

    @override_settings(NOTIFICATION_CONTENT_RETENTION_DAYS=None)
    def test_disabled_when_retention_is_not_configured(self):
        expired = self._backdated_notification(days_old=9999)

        call_command('purge_expired_notification_content')

        expired.refresh_from_db()
        self.assertEqual(expired.title, 'Loan approved')

    @override_settings(NOTIFICATION_CONTENT_RETENTION_DAYS=90)
    def test_task_wraps_command_and_returns_output(self):
        self._backdated_notification(days_old=91)

        result = purge_expired_notification_content()

        self.assertIn('Purge complete', result)
