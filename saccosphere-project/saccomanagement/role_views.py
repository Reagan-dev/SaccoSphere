from django.utils import timezone

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdminOrSuperAdmin, IsSuperAdmin
from accounts.models import User, Sacco

from saccomembership.models import Membership

from .models import Role
from .role_serializers import RoleSerializer


class RoleAssignView(APIView):
    """
    Assign a role to a user.

    POST /api/v1/management/roles/assign/
    Body: {
        "user_id": "<uuid>",
        "role_name": "MEMBER|SACCO_ADMIN|SUPER_ADMIN",
        "sacco_id": "<uuid>" (optional)
    }

    Only SUPER_ADMIN users can assign roles.
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request):
        user_id = request.data.get('user_id')
        role_name = request.data.get('role_name')
        sacco_id = request.data.get('sacco_id')

        # Validate user_id
        if not user_id:
            raise ValidationError({'user_id': 'This field is required.'})

        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise ValidationError({'user_id': 'User not found.'})

        if not target_user.is_active:
            raise ValidationError(
                {'user_id': 'Cannot assign a role to an inactive user.'}
            )

        # Validate role_name
        valid_roles = [Role.MEMBER, Role.SACCO_ADMIN, Role.SUPER_ADMIN]
        if role_name not in valid_roles:
            raise ValidationError(
                {
                    'role_name': f'Invalid role. Must be one of {valid_roles}.'
                }
            )

        # Validate sacco_id if provided
        sacco = None
        if sacco_id:
            try:
                sacco = Sacco.objects.get(id=sacco_id)
            except Sacco.DoesNotExist:
                raise ValidationError({'sacco_id': 'Sacco not found.'})

        # Check if an active role already exists. A revoked (inactive)
        # grant for the same (user, sacco, name) does not block a fresh
        # assignment - that is exactly how a role gets re-granted.
        role_exists = Role.objects.filter(
            user=target_user,
            sacco=sacco,
            name=role_name,
            is_active=True,
        ).exists()

        if role_exists:
            raise ValidationError(
                {
                    'role': 'This role is already assigned to this user '
                    'in this context.'
                }
            )

        # Create the role
        role = Role.objects.create(
            user=target_user,
            sacco=sacco,
            name=role_name,
        )

        serializer = RoleSerializer(role)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class RoleRevokeView(APIView):
    """
    Revoke a role from a user.

    DELETE /api/v1/management/roles/{role_id}/
    Body (optional): {"force": true}

    Only SUPER_ADMIN users can revoke roles. A user cannot revoke their own
    SUPER_ADMIN role. Revoking the last active SACCO_ADMIN for a SACCO, or
    the last active SUPER_ADMIN platform-wide, is rejected unless force is
    passed - and force is honored only when the actor is themselves a
    SUPER_ADMIN.

    Revocation is a soft-delete: the role row is kept (is_active=False,
    revoked_at, revoked_by set) rather than deleted, so it remains visible
    as history and does not stop this exact role from being re-granted
    later.
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def delete(self, request, role_id):
        try:
            role = Role.objects.get(id=role_id, is_active=True)
        except Role.DoesNotExist:
            raise ValidationError(
                {'role_id': 'Active role not found.'}
            )

        # Prevent revoking your own SUPER_ADMIN role
        if (
            role.user == request.user
            and role.name == Role.SUPER_ADMIN
        ):
            raise ValidationError(
                {
                    'role': 'You cannot revoke your own SUPER_ADMIN role.'
                }
            )

        force = self._parse_force(request)
        actor_is_super_admin = self._is_super_admin(request.user)

        if self._is_last_active_admin(role) and not (
            force and actor_is_super_admin
        ):
            if force and not actor_is_super_admin:
                raise PermissionDenied(
                    'Only a SUPER_ADMIN may force-revoke the last '
                    'admin of a kind.'
                )
            return Response(
                {'detail': self._last_admin_message(role)},
                status=status.HTTP_409_CONFLICT,
            )

        # Context for a future audit-logging pass: who did this, to which
        # role/user/sacco, and whether it was a forced last-admin removal.
        actor = request.user
        target_user = role.user
        target_sacco = role.sacco
        target_role_name = role.name
        forced_last_admin_removal = force and self._is_last_active_admin(
            role,
        )

        role.is_active = False
        role.revoked_at = timezone.now()
        role.revoked_by = actor
        role.save(update_fields=['is_active', 'revoked_at', 'revoked_by'])

        return Response(
            {
                'detail': (
                    f'Role {target_role_name} revoked from user '
                    f'{target_user.email}.'
                ),
                'actor_id': str(actor.id),
                'target_user_id': str(target_user.id),
                'target_role': target_role_name,
                'target_sacco_id': (
                    str(target_sacco.id) if target_sacco else None
                ),
                'forced_last_admin_removal': forced_last_admin_removal,
            },
            status=status.HTTP_200_OK,
        )

    def _parse_force(self, request):
        force = request.data.get('force')
        if force is None:
            force = request.query_params.get('force')
        return str(force).strip().lower() in ('true', '1', 'yes')

    def _is_super_admin(self, user):
        return (
            user.is_staff
            or Role.objects.filter(
                user=user,
                name=Role.SUPER_ADMIN,
                is_active=True,
            ).exists()
        )

    def _is_last_active_admin(self, role):
        """
        Whether revoking `role` would leave zero other active admins of
        its kind - SACCO-scoped for SACCO_ADMIN, platform-wide for
        SUPER_ADMIN. MEMBER roles have no "last admin" concept.
        """
        if role.name == Role.SACCO_ADMIN:
            remaining = Role.objects.filter(
                name=Role.SACCO_ADMIN,
                sacco=role.sacco,
                is_active=True,
            ).exclude(pk=role.pk)
            return not remaining.exists()

        if role.name == Role.SUPER_ADMIN:
            remaining = Role.objects.filter(
                name=Role.SUPER_ADMIN,
                is_active=True,
            ).exclude(pk=role.pk)
            return not remaining.exists()

        return False

    def _last_admin_message(self, role):
        if role.name == Role.SACCO_ADMIN:
            return (
                'Cannot revoke the last admin for this SACCO — assign '
                'another admin first, or pass force=true.'
            )
        return (
            'Cannot revoke the last SUPER_ADMIN — assign another '
            'SUPER_ADMIN first, or pass force=true.'
        )


class UserRolesView(ListAPIView):
    """
    List all active roles for a specific user.

    GET /api/v1/management/roles/?user_id=<uuid>

    SACCO admins can only query roles for users in their SACCO.
    SUPER_ADMIN users can query roles for any user.
    """

    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]

    def list(self, request, *args, **kwargs):
        user_id = request.query_params.get('user_id')

        if not user_id:
            raise ValidationError(
                {'user_id': 'Query parameter user_id is required.'},
            )

        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise ValidationError({'user_id': 'User not found.'})

        if not self._is_super_admin(request.user):
            if not self._target_user_in_admin_sacco(request.user, target_user):
                return Response(
                    {
                        'detail': (
                            'You can only view roles for members of '
                            'your SACCO.'
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        user_id = self.request.query_params.get('user_id')
        return Role.objects.filter(
            user_id=user_id,
            is_active=True,
        ).select_related(
            'sacco',
            'user',
        )

    def _is_super_admin(self, user):
        return (
            user.is_staff
            or Role.objects.filter(
                user=user,
                name=Role.SUPER_ADMIN,
                is_active=True,
            ).exists()
        )

    def _target_user_in_admin_sacco(self, admin_user, target_user):
        admin_sacco_ids = Role.objects.filter(
            user=admin_user,
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
            is_active=True,
        ).values_list('sacco_id', flat=True)

        if not admin_sacco_ids:
            return False

        has_membership = Membership.objects.filter(
            user=target_user,
            sacco_id__in=admin_sacco_ids,
        ).exists()
        if has_membership:
            return True

        return Role.objects.filter(
            user=target_user,
            sacco_id__in=admin_sacco_ids,
            is_active=True,
        ).exists()
