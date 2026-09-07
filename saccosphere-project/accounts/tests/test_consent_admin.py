"""Tests for the UserConsent Django admin under the active (Option A) policy.

Option A is read-only for everyone, including superusers - matching this
codebase's existing convention for audit-sensitive models (SystemAuditLog,
DataConsentLog). These tests confirm that policy is actually enforced by
the admin site, not just documented in a comment.

Note on scope: this test environment has no staticfiles manifest
(collectstatic has never been run here - confirmed by the "Missing
staticfiles manifest" warnings/errors this project's whole test suite
already produces for any full admin-page render), so tests that would
require rendering a complete admin HTML page are written against the
ModelAdmin permission methods directly instead of via a live HTTP GET.
The add/delete views raise PermissionDenied before any template is
touched, so those are tested over real HTTP; the change view (which
Django gates on has_view_or_change_permission, not has_change_permission
alone) is tested at the permission-method level for the same reason.
"""

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User, UserConsent


class UserConsentAdminReadOnlyTestCase(TestCase):
    """Confirm a realistic staff user (no special UserConsent permissions,
    the default state for a fresh is_staff account) cannot add/change/
    delete via /admin/."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email='consent-admin-staff@example.com',
            phone_number='+254700000030',
            password='testpass123',
            is_staff=True,
        )
        self.consent = UserConsent.objects.create(
            user=self.staff_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

    def test_staff_cannot_add_consent(self):
        """The add view is forbidden - has_add_permission is always False."""
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('admin:accounts_userconsent_add'),
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_change_consent(self):
        """The change view is forbidden without an underlying view/change permission."""
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse(
                'admin:accounts_userconsent_change',
                args=[self.consent.id],
            ),
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_delete_consent(self):
        """The delete view is forbidden - has_delete_permission is always False."""
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse(
                'admin:accounts_userconsent_delete',
                args=[self.consent.id],
            ),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            UserConsent.objects.filter(id=self.consent.id).exists()
        )

    def test_permission_methods_deny_add_change_delete(self):
        """has_add/change/delete_permission are all False for this ModelAdmin."""
        model_admin = admin.site._registry[UserConsent]
        request = type('_Req', (), {'user': self.staff_user})()
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))


class UserConsentAdminViewPermissionTestCase(TestCase):
    """Confirm granting the underlying Django 'view' permission allows
    viewing but still never editing - the 'staff can view but never edit'
    half of Option A."""

    def setUp(self):
        self.staff_user = User.objects.create_user(
            email='consent-admin-viewer@example.com',
            phone_number='+254700000032',
            password='testpass123',
            is_staff=True,
        )
        content_type = ContentType.objects.get_for_model(UserConsent)
        view_permission = Permission.objects.get(
            content_type=content_type,
            codename='view_userconsent',
        )
        self.staff_user.user_permissions.add(view_permission)

    def test_view_permission_allows_view_but_not_edit(self):
        """With only the 'view' permission granted: view yes, edit/add/delete no."""
        model_admin = admin.site._registry[UserConsent]
        request = type('_Req', (), {'user': self.staff_user})()

        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        # The change view's actual gate is has_view_or_change_permission -
        # confirm having 'view' alone is still enough to pass it (so the
        # user genuinely can look, even though has_change_permission=False
        # keeps the resulting form read-only).
        self.assertTrue(
            model_admin.has_view_or_change_permission(request)
        )


class UserConsentAdminSuperuserAlsoReadOnlyTestCase(TestCase):
    """Confirm Option A applies with no superuser carve-out."""

    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            email='consent-admin-super@example.com',
            phone_number='+254700000031',
            password='testpass123',
        )
        self.consent = UserConsent.objects.create(
            user=self.superuser,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v1.0',
            consented=True,
        )

    def test_superuser_cannot_add_consent(self):
        """Even a superuser is denied - matching NoChangeAdminMixin's convention."""
        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('admin:accounts_userconsent_add'),
        )
        self.assertEqual(response.status_code, 403)

    def test_superuser_cannot_delete_consent(self):
        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse(
                'admin:accounts_userconsent_delete',
                args=[self.consent.id],
            ),
        )
        self.assertEqual(response.status_code, 403)

    def test_superuser_change_permission_is_still_false(self):
        """A superuser implicitly passes has_view_permission (so the change
        view renders, read-only, rather than 403ing like add/delete do),
        but has_change_permission itself is still False - the form cannot
        actually be saved, matching the 'no exceptions' policy."""
        model_admin = admin.site._registry[UserConsent]
        request = type('_Req', (), {'user': self.superuser})()

        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
