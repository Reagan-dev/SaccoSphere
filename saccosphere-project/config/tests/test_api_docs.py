"""Tests for the OpenAPI/Swagger documentation endpoints.

A single malformed @swagger_auto_schema(responses=...) annotation
anywhere in the project breaks schema generation for the entire API,
since drf-yasg builds one schema for the whole site. These tests exist
so that regression is caught here instead of by someone loading
/swagger/ in a browser and finding it broken.

The docs are staff-only (they enumerate every endpoint on the
platform, including staff/superadmin-only views), so each endpoint is
also checked for anonymous access being denied.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


@override_settings(
    # The real WhiteNoise manifest storage requires `collectstatic` to
    # have been run (it is, on every deploy - see the Procfile), which
    # this test suite does not otherwise depend on. Swap to plain
    # staticfiles storage so these tests check schema/template
    # rendering, not whether collectstatic has been run locally.
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class APIDocumentationTestCase(TestCase):
    """Test that the API documentation endpoints render for staff only."""

    def setUp(self):
        """Create a staff user who is allowed to view the docs."""
        self.staff_user = User.objects.create_user(
            email='docs-staff@example.com',
            password='testpass123',
            is_staff=True,
        )

    def test_anonymous_cannot_view_swagger_ui(self):
        """Test that an anonymous request to Swagger UI is denied."""
        response = self.client.get(reverse('schema-swagger-ui'))
        self.assertIn(response.status_code, (401, 403))

    def test_anonymous_cannot_view_redoc(self):
        """Test that an anonymous request to ReDoc is denied."""
        response = self.client.get(reverse('schema-redoc'))
        self.assertIn(response.status_code, (401, 403))

    def test_staff_can_view_swagger_ui(self):
        """Test that a staff user can load the Swagger UI page."""
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('schema-swagger-ui'))
        self.assertEqual(response.status_code, 200)

    def test_staff_can_view_redoc(self):
        """Test that a staff user can load the ReDoc page."""
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('schema-redoc'))
        self.assertEqual(response.status_code, 200)

    def test_openapi_schema_generates_without_error(self):
        """
        Test that the underlying OpenAPI schema generates successfully.

        The Swagger/ReDoc HTML shells return 200 even when schema
        generation itself is broken, since they fetch the schema
        asynchronously via JavaScript. This hits the same schema
        generator directly to catch that failure mode.
        """
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('schema-swagger-ui') + '?format=openapi'
        )
        self.assertEqual(response.status_code, 200)

        schema = json.loads(response.content)
        self.assertIn('paths', schema)
        self.assertGreater(len(schema['paths']), 0)
