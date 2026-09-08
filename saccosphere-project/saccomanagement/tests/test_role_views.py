"""Tests for RoleAssignView, RoleRevokeView, and UserRolesView."""

from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Sacco, User
from accounts.permissions import IsSaccoAdmin
from django.urls import reverse

from saccomanagement.models import Role, SystemAuditLog


class RoleAssignViewTestCase(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            email='role-assign-super@example.com',
            phone_number='+254700000100',
            password='testpass123',
            is_staff=True,
        )
        self.sacco = Sacco.objects.create(
            name='Role Assign SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.sacco_admin = User.objects.create_user(
            email='role-assign-sacco-admin@example.com',
            phone_number='+254700000101',
            password='testpass123',
        )
        Role.objects.create(
            user=self.sacco_admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.target_user = User.objects.create_user(
            email='role-assign-target@example.com',
            phone_number='+254700000102',
            password='testpass123',
        )

    def _assign(self, **overrides):
        payload = {
            'user_id': str(self.target_user.id),
            'role_name': Role.SACCO_ADMIN,
            'sacco_id': str(self.sacco.id),
        }
        payload.update(overrides)
        return self.client.post(reverse('management:role-assign'), payload)

    def test_assign_role_happy_path(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self._assign()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        role = Role.objects.get(
            user=self.target_user,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
            is_active=True,
        )
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.super_admin,
                action='ROLE_ASSIGN',
                resource_type='Role',
                resource_id=str(role.id),
            ).exists(),
        )

    def test_assign_role_to_inactive_user_rejected(self):
        self.target_user.is_active = False
        self.target_user.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.super_admin)

        response = self._assign()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            Role.objects.filter(
                user=self.target_user, sacco=self.sacco,
            ).exists(),
        )

    def test_non_super_admin_cannot_assign_roles(self):
        """
        Privilege escalation guard: RoleAssignView is gated entirely to
        SUPER_ADMIN by permission_classes, so a SACCO_ADMIN (or MEMBER)
        cannot reach the assignment logic at all - confirmed here rather
        than assumed.
        """
        self.client.force_authenticate(user=self.sacco_admin)

        response = self._assign(role_name=Role.SUPER_ADMIN, sacco_id='')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            Role.objects.filter(
                user=self.target_user, name=Role.SUPER_ADMIN,
            ).exists(),
        )


class RoleRevokeViewTestCase(APITestCase):
    def setUp(self):
        # A Django-staff actor with no SUPER_ADMIN Role row of its own -
        # is_staff is treated as SUPER_ADMIN-equivalent throughout this
        # codebase, and using it here lets "last SUPER_ADMIN" scenarios be
        # set up without the actor's own membership muddying the count.
        self.staff_actor = User.objects.create_user(
            email='role-revoke-staff@example.com',
            phone_number='+254700000110',
            password='testpass123',
            is_staff=True,
        )
        self.sacco = Sacco.objects.create(
            name='Role Revoke SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )

    def _revoke_url(self, role):
        return reverse('management:role-revoke', kwargs={'role_id': role.id})

    def test_revoke_role_happy_path_not_last_admin(self):
        user_a = User.objects.create_user(
            email='revoke-admin-a@example.com',
            phone_number='+254700000111',
            password='testpass123',
        )
        user_b = User.objects.create_user(
            email='revoke-admin-b@example.com',
            phone_number='+254700000112',
            password='testpass123',
        )
        role_a = Role.objects.create(
            user=user_a, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        Role.objects.create(
            user=user_b, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.staff_actor)

        response = self.client.delete(self._revoke_url(role_a))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        role_a.refresh_from_db()
        self.assertFalse(role_a.is_active)
        self.assertIsNotNone(role_a.revoked_at)
        self.assertEqual(role_a.revoked_by, self.staff_actor)
        self.assertEqual(response.data['forced_last_admin_removal'], False)
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.staff_actor,
                action='ROLE_REVOKE',
                resource_type='Role',
                resource_id=str(role_a.id),
            ).exists(),
        )

    def test_revoke_last_sacco_admin_rejected_without_force(self):
        only_admin = User.objects.create_user(
            email='revoke-last-sacco-admin@example.com',
            phone_number='+254700000113',
            password='testpass123',
        )
        role = Role.objects.create(
            user=only_admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.staff_actor)

        response = self.client.delete(self._revoke_url(role))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        role.refresh_from_db()
        self.assertTrue(role.is_active)
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.staff_actor,
                action='ROLE_REVOKE_REJECTED',
                resource_type='Role',
                resource_id=str(role.id),
            ).exists(),
        )

    def test_revoke_last_super_admin_rejected_without_force(self):
        only_super_admin = User.objects.create_user(
            email='revoke-last-super-admin@example.com',
            phone_number='+254700000114',
            password='testpass123',
        )
        role = Role.objects.create(
            user=only_super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )
        self.client.force_authenticate(user=self.staff_actor)

        response = self.client.delete(self._revoke_url(role))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        role.refresh_from_db()
        self.assertTrue(role.is_active)

    def test_force_revoke_last_admin_succeeds_for_super_admin(self):
        only_super_admin = User.objects.create_user(
            email='force-revoke-target@example.com',
            phone_number='+254700000115',
            password='testpass123',
        )
        role = Role.objects.create(
            user=only_super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )
        self.client.force_authenticate(user=self.staff_actor)

        response = self.client.delete(
            self._revoke_url(role), {'force': True},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        role.refresh_from_db()
        self.assertFalse(role.is_active)
        self.assertEqual(response.data['forced_last_admin_removal'], True)

    def test_force_revoke_rejected_for_non_super_admin_actor(self):
        """
        RoleRevokeView is gated entirely to SUPER_ADMIN by
        permission_classes, so a non-super-admin cannot reach the force
        path (or any revoke) at all - confirmed here as the black-box
        behavior the force restriction is meant to guarantee.
        """
        sacco_admin = User.objects.create_user(
            email='force-revoke-actor@example.com',
            phone_number='+254700000116',
            password='testpass123',
        )
        Role.objects.create(
            user=sacco_admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        target = User.objects.create_user(
            email='force-revoke-actor-target@example.com',
            phone_number='+254700000117',
            password='testpass123',
        )
        role = Role.objects.create(
            user=target, sacco=None, name=Role.SUPER_ADMIN,
        )
        self.client.force_authenticate(user=sacco_admin)

        response = self.client.delete(
            self._revoke_url(role), {'force': True},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        role.refresh_from_db()
        self.assertTrue(role.is_active)

    def test_self_revoke_own_super_admin_rejected(self):
        """Regression: the pre-existing self-revoke guard must still hold."""
        Role.objects.create(
            user=self.staff_actor, sacco=None, name=Role.SUPER_ADMIN,
        )
        other_super_admin = User.objects.create_user(
            email='self-revoke-other-super@example.com',
            phone_number='+254700000118',
            password='testpass123',
        )
        Role.objects.create(
            user=other_super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )
        own_role = Role.objects.get(user=self.staff_actor)
        self.client.force_authenticate(user=self.staff_actor)

        response = self.client.delete(self._revoke_url(own_role))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        own_role.refresh_from_db()
        self.assertTrue(own_role.is_active)

    def test_revoked_role_no_longer_grants_sacco_admin_access(self):
        """
        Confirms one of the call sites fixed in accounts/permissions.py:
        IsSaccoAdmin.has_permission must stop passing once the role backing
        it has been soft-revoked.
        """
        admin_user = User.objects.create_user(
            email='revoked-access-check@example.com',
            phone_number='+254700000119',
            password='testpass123',
        )
        backup_admin = User.objects.create_user(
            email='revoked-access-check-backup@example.com',
            phone_number='+254700000120',
            password='testpass123',
        )
        Role.objects.create(
            user=backup_admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        role = Role.objects.create(
            user=admin_user, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        stub_request = type('_Req', (), {'user': admin_user})()
        self.assertTrue(IsSaccoAdmin().has_permission(stub_request, None))

        self.client.force_authenticate(user=self.staff_actor)
        response = self.client.delete(self._revoke_url(role))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertFalse(IsSaccoAdmin().has_permission(stub_request, None))


class UserRolesViewTestCase(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            email='user-roles-super@example.com',
            phone_number='+254700000130',
            password='testpass123',
            is_staff=True,
        )
        # IsSaccoAdminOrSuperAdmin (unlike IsSuperAdmin) checks only for an
        # active SUPER_ADMIN Role row, not is_staff - so is_staff alone
        # isn't enough to reach UserRolesView.
        Role.objects.create(
            user=self.super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )
        self.sacco = Sacco.objects.create(
            name='User Roles SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.target_user = User.objects.create_user(
            email='user-roles-target@example.com',
            phone_number='+254700000131',
            password='testpass123',
        )

    def test_super_admin_can_list_active_roles(self):
        Role.objects.create(
            user=self.target_user, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(
            reverse('management:user-roles'),
            {'user_id': str(self.target_user.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['count'], 1)

    def test_revoked_role_excluded_from_listing(self):
        role = Role.objects.create(
            user=self.target_user, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        role.is_active = False
        role.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(
            reverse('management:user-roles'),
            {'user_id': str(self.target_user.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['count'], 0)
