"""SaccoScopedMixin._set_sacco_context is the code that actually resolves
tenant context for DRF-authenticated API requests (SaccoContextMiddleware
runs before DRF resolves the JWT user, so it never sees a real user for
API traffic). It must populate the request-context thread-local so JSON
logs carry the real sacco_id."""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from config.middleware import get_current_sacco_id
from saccomanagement.models import Role


class SaccoScopedMixinThreadLocalTests(TestCase):

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Verify Sacco',
            registration_number='VERIFY-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='verify-admin@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.url = reverse('management:sacco-settings')

    def test_sacco_admin_request_sets_thread_local_sacco_id(self):
        seen = {}

        from saccomanagement import mixins

        original = mixins.SaccoScopedMixin._set_sacco_context

        def spy(mixin_self):
            result = original(mixin_self)
            seen['sacco_id'] = get_current_sacco_id()
            return result

        mixins.SaccoScopedMixin._set_sacco_context = spy
        try:
            client = APIClient()
            client.force_authenticate(user=self.admin)
            client.get(self.url)
        finally:
            mixins.SaccoScopedMixin._set_sacco_context = original

        self.assertEqual(seen.get('sacco_id'), str(self.sacco.id))

    def test_thread_local_resets_between_requests(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        client.get(self.url)
        self.assertEqual(get_current_sacco_id(), str(self.sacco.id))

        other = User.objects.create_user(
            email='verify-other@example.com', password='StrongPass1',
        )
        client.force_authenticate(user=other)
        client.get('/api/v1/health/')

        self.assertIsNone(get_current_sacco_id())
