from unittest.mock import patch

from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import JobHeartbeat
from .monitored_jobs import MONITORED_JOBS
from .tasks import ALERT_STATE_CACHE_KEY, check_job_heartbeats


class LivenessViewTests(APITestCase):

    def test_returns_ok_without_touching_any_dependency(self):
        response = self.client.get(reverse('health:liveness'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'status': 'ok'})


class ReadinessViewTests(APITestCase):

    def test_ready_when_database_and_cache_are_reachable(self):
        response = self.client.get(reverse('health:readiness'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ok')
        self.assertTrue(response.data['checks']['database'])
        self.assertTrue(response.data['checks']['cache'])

    def test_unavailable_when_database_check_raises(self):
        with patch(
            'health.views.connection.cursor',
            side_effect=OperationalError('connection refused'),
        ):
            response = self.client.get(reverse('health:readiness'))

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(response.data['status'], 'unavailable')
        self.assertFalse(response.data['checks']['database'])
        self.assertTrue(response.data['checks']['cache'])

    def test_unavailable_when_cache_check_raises(self):
        with patch(
            'health.views.cache.set',
            side_effect=ConnectionError('redis unreachable'),
        ):
            response = self.client.get(reverse('health:readiness'))

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(response.data['status'], 'unavailable')
        self.assertTrue(response.data['checks']['database'])
        self.assertFalse(response.data['checks']['cache'])

    def test_unavailable_when_cache_round_trip_returns_stale_value(self):
        with patch('health.views.cache.get', return_value='stale'):
            response = self.client.get(reverse('health:readiness'))

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertFalse(response.data['checks']['cache'])


class CheckJobHeartbeatsTaskTests(TestCase):

    def setUp(self):
        cache.delete(ALERT_STATE_CACHE_KEY)
        for job_name in MONITORED_JOBS:
            JobHeartbeat.objects.update_or_create(
                job_name=job_name,
                defaults={
                    'last_run_at': timezone.now(),
                    'last_status': JobHeartbeat.Status.OK,
                },
            )

    def test_no_alert_when_all_jobs_are_healthy(self):
        with patch('health.tasks.mail_admins') as mail_admins:
            check_job_heartbeats()

        mail_admins.assert_not_called()
        self.assertFalse(cache.get(ALERT_STATE_CACHE_KEY, False))

    def test_alert_sent_once_for_an_unhealthy_job(self):
        stale_job = next(iter(MONITORED_JOBS))
        JobHeartbeat.objects.filter(job_name=stale_job).update(
            last_status=JobHeartbeat.Status.ERROR,
        )

        with patch('health.tasks.mail_admins') as mail_admins:
            check_job_heartbeats()
            check_job_heartbeats()

        mail_admins.assert_called_once()
        self.assertTrue(cache.get(ALERT_STATE_CACHE_KEY, False))

    def test_recovery_sent_after_job_becomes_healthy_again(self):
        cache.set(ALERT_STATE_CACHE_KEY, True, 3600)

        with patch('health.tasks.mail_admins') as mail_admins:
            check_job_heartbeats()

        mail_admins.assert_called_once()
        self.assertFalse(cache.get(ALERT_STATE_CACHE_KEY, False))
