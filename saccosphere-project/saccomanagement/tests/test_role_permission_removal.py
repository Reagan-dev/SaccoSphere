"""
Regression tests for the RolePermission model removal.

RolePermission (per-resource CRUD grants: resource, can_create/read/update/
delete) was never instantiated or queried anywhere in the codebase before
removal - confirmed by exhaustive grep (only its own model definition, admin
registration, and initial migration referenced it). Access control has
always been enforced by role tier alone, via accounts/permissions.py and
services/permissions.py. These tests confirm removing the dead model
changed nothing about that enforcement - today's effective permissions for
MEMBER, SACCO_ADMIN, and SUPER_ADMIN are identical to before.
"""

from django.test import TestCase

from accounts.models import Sacco, User
from accounts.permissions import (
    IsSaccoAdmin,
    IsSaccoAdminOrSuperAdmin,
    IsSuperAdmin,
)
from saccomanagement import admin as saccomanagement_admin
from saccomanagement import models as saccomanagement_models
from saccomanagement.models import Role


class RolePermissionRemovedTestCase(TestCase):
    """Confirm the dead model and its admin registration are actually gone."""

    def test_role_permission_model_no_longer_exists(self):
        self.assertFalse(hasattr(saccomanagement_models, 'RolePermission'))

    def test_role_permission_not_registered_in_admin(self):
        registered_model_names = [
            model.__name__
            for model in saccomanagement_admin.admin.site._registry
        ]
        self.assertNotIn('RolePermission', registered_model_names)


class RoleTierPermissionsUnchangedTestCase(TestCase):
    """
    Confirm role-tier enforcement - the only enforcement mechanism this
    feature has ever had - still allows/denies exactly as it did before,
    for each of MEMBER, SACCO_ADMIN, and SUPER_ADMIN.
    """

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Role Tier SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Other Tier SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Kisumu',
        )
        self.member = User.objects.create_user(
            email='tier-member@example.com',
            phone_number='+254700000200',
            password='testpass123',
        )
        self.sacco_admin = User.objects.create_user(
            email='tier-sacco-admin@example.com',
            phone_number='+254700000201',
            password='testpass123',
        )
        Role.objects.create(
            user=self.sacco_admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.other_sacco_admin = User.objects.create_user(
            email='tier-other-sacco-admin@example.com',
            phone_number='+254700000202',
            password='testpass123',
        )
        Role.objects.create(
            user=self.other_sacco_admin,
            sacco=self.other_sacco,
            name=Role.SACCO_ADMIN,
        )
        self.super_admin = User.objects.create_user(
            email='tier-super-admin@example.com',
            phone_number='+254700000203',
            password='testpass123',
        )
        Role.objects.create(
            user=self.super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )

    def _req(self, user):
        return type('_Req', (), {'user': user})()

    def _obj_for_sacco(self, sacco):
        return type('_Obj', (), {'sacco': sacco})()

    def test_is_sacco_admin_view_level(self):
        permission = IsSaccoAdmin()
        self.assertFalse(
            permission.has_permission(self._req(self.member), None),
        )
        self.assertTrue(
            permission.has_permission(self._req(self.sacco_admin), None),
        )
        self.assertFalse(
            permission.has_permission(self._req(self.super_admin), None),
        )

    def test_is_sacco_admin_object_level_scoped_to_own_sacco(self):
        permission = IsSaccoAdmin()
        own_sacco_obj = self._obj_for_sacco(self.sacco)
        other_sacco_obj = self._obj_for_sacco(self.other_sacco)

        self.assertTrue(
            permission.has_object_permission(
                self._req(self.sacco_admin), None, own_sacco_obj,
            ),
        )
        self.assertFalse(
            permission.has_object_permission(
                self._req(self.sacco_admin), None, other_sacco_obj,
            ),
        )

    def test_is_super_admin_view_level(self):
        permission = IsSuperAdmin()
        self.assertFalse(
            permission.has_permission(self._req(self.member), None),
        )
        self.assertFalse(
            permission.has_permission(self._req(self.sacco_admin), None),
        )
        self.assertTrue(
            permission.has_permission(self._req(self.super_admin), None),
        )

    def test_is_sacco_admin_or_super_admin_view_level(self):
        permission = IsSaccoAdminOrSuperAdmin()
        self.assertFalse(
            permission.has_permission(self._req(self.member), None),
        )
        self.assertTrue(
            permission.has_permission(self._req(self.sacco_admin), None),
        )
        self.assertTrue(
            permission.has_permission(self._req(self.super_admin), None),
        )

    def test_is_sacco_admin_or_super_admin_object_level(self):
        permission = IsSaccoAdminOrSuperAdmin()
        own_sacco_obj = self._obj_for_sacco(self.sacco)
        other_sacco_obj = self._obj_for_sacco(self.other_sacco)

        # SUPER_ADMIN bypasses the sacco check entirely.
        self.assertTrue(
            permission.has_object_permission(
                self._req(self.super_admin), None, other_sacco_obj,
            ),
        )
        # SACCO_ADMIN is still scoped to their own sacco.
        self.assertTrue(
            permission.has_object_permission(
                self._req(self.sacco_admin), None, own_sacco_obj,
            ),
        )
        self.assertFalse(
            permission.has_object_permission(
                self._req(self.sacco_admin), None, other_sacco_obj,
            ),
        )
