from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSuperAdmin, IsSaccoAdmin
from accounts.models import User, Sacco

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
    
    Accessible to SUPER_ADMIN and SACCO_ADMIN users.
    """

    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get_queryset(self):
        user_id = self.request.query_params.get('user_id')

        if not user_id:
            raise ValidationError(
                {'user_id': 'Query parameter user_id is required.'}
            )

        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise ValidationError({'user_id': 'User not found.'})

        return Role.objects.filter(user=target_user)


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# WHAT EACH VIEW DOES AND WHY:
#
# 1. RoleAssignView (POST /api/v1/management/roles/assign/)
#    - Only SUPER_ADMIN users can call this (enforced by IsSuperAdmin permission)
#    - Takes: user_id (the person getting the role), role_name (MEMBER/SACCO_ADMIN/
#      SUPER_ADMIN), and optional sacco_id (which sacco context)
#    - Validates all inputs exist in the database
#    - Checks if the role already exists (prevents duplicates via unique_together)
#    - Creates a new Role record and returns it serialized (201 Created)
#    - WHY: You need a way to grant roles to users. This is the admin function.
#
# 2. RoleRevokeView (DELETE /api/v1/management/roles/{role_id}/)
#    - Only SUPER_ADMIN users can call this
#    - Prevents a user from revoking their own SUPER_ADMIN role (safety guard)
#    - Deletes the role and returns confirmation (200 OK)
#    - WHY: Admins need to remove roles (e.g., user leaves, gets demoted)
#      The self-revoke check prevents accidental lockout.
#
# 3. UserRolesView (GET /api/v1/management/roles/?user_id=<uuid>)
#    - Accessible to SACCO_ADMIN or SUPER_ADMIN (via IsSaccoAdmin permission)
#    - Requires user_id query parameter
#    - Returns all Role records for that user (paginated via ListAPIView)
#    - WHY: Need to see what roles a user has and in which sacco contexts
#
#
# DJANGO/PYTHON CONCEPTS EXPLAINED:
#
# - APIView: Base class for all view classes. You define post(), get(), delete()
#   methods. More control than generic views but more boilerplate.
#
# - ListAPIView: A generic view that automatically handles GET, filtering, 
#   pagination, and serialization. Great for read-only lists.
#
# - permission_classes: A list of permission classes that ALL must pass. 
#   [IsAuthenticated, IsSuperAdmin] means: user must be logged in AND have 
#   SUPER_ADMIN role.
#
# - Foreign Key with on_delete=CASCADE: When the referenced object (e.g., a User)
#   is deleted, all related Role records are automatically deleted.
#   on_delete=SET_NULL would set the FK to NULL instead (requires null=True).
#
# - ValidationError: DRF exception that returns a 400 Bad Request with error details.
#   Automatically formatted as JSON for API responses.
#
# - get_queryset(): In ListAPIView, this method defines what records are returned.
#   Override it to filter based on request parameters (like user_id).
#
# - unique_together: A database constraint that prevents duplicate records.
#   E.g., same (user, sacco, role_name) cannot exist twice.
#
#
# HOW TO TEST MANUALLY:
#
# 1. Create a test user and a test sacco in Django shell or via API
# 2. Assign SUPER_ADMIN role to yourself:
#    POST /api/v1/management/roles/assign/
#    {
#      "user_id": "<your_user_id>",
#      "role_name": "SUPER_ADMIN",
#      "sacco_id": null
#    }
# 3. List your roles: GET /api/v1/management/roles/?user_id=<your_user_id>
# 4. Try to revoke your own SUPER_ADMIN role — it should fail with the message
#    "You cannot revoke your own SUPER_ADMIN role."
# 5. Assign a MEMBER role to another user, then revoke it
#
#
# KEY DESIGN DECISIONS AND WHY:
#
# - SCOPED BY SACCO: A role can be sacco-specific (sacco_id set) or platform-wide
#   (sacco_id null). This means a user can be MEMBER in one sacco and SACCO_ADMIN
#   in another. Supports multi-tenancy.
#
# - SUPER_ADMIN SELF-REVOKE PROTECTION: Prevents an admin from accidentally
#   locking themselves out. Only another SUPER_ADMIN can revoke your role.
#
# - READ-ONLY SERIALIZER: RoleSerializer uses get_name_display() to show the
#   human-readable role name. All fields are read-only—clients can't modify
#   roles via the serializer (they use the dedicated POST/DELETE endpoints).
#
# - PERMISSION CHECK IN get_queryset(): UserRolesView checks IsSaccoAdmin
#   (SACCO_ADMIN or SUPER_ADMIN). This enforces who can query roles. The check
#   happens at the view level, not the database level.
#
# - Import permissions locally to avoid circular imports: Django apps can have
#   circular dependencies. Importing inside the function (has_permission) avoids
#   this issue.
#
# ============================================================
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
