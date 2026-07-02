from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
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

        # Check if role already exists
        role_exists = Role.objects.filter(
            user=target_user,
            sacco=sacco,
            name=role_name,
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
    
    Only SUPER_ADMIN users can revoke roles.
    A user cannot revoke their own SUPER_ADMIN role.
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def delete(self, request, role_id):
        try:
            role = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            raise ValidationError({'role_id': 'Role not found.'})

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

        role_user_email = role.user.email
        role_name = role.name
        role.delete()

        return Response(
            {
                'detail': (
                    f'Role {role_name} revoked from user {role_user_email}.'
                )
            },
            status=status.HTTP_200_OK,
        )


class UserRolesView(ListAPIView):
    """
    List all roles for a specific user.

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
        return Role.objects.filter(user_id=user_id).select_related(
            'sacco',
            'user',
        )

    def _is_super_admin(self, user):
        return (
            user.is_staff
            or Role.objects.filter(
                user=user,
                name=Role.SUPER_ADMIN,
            ).exists()
        )

    def _target_user_in_admin_sacco(self, admin_user, target_user):
        admin_sacco_ids = Role.objects.filter(
            user=admin_user,
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
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
        ).exists()


