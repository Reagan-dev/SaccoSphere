"""Shared account helper utilities."""

from saccomanagement.models import Role


def get_user_sacco_context(user):
    """
    Return the user's primary SACCO role context for login and profile responses.

    Priority: SUPER_ADMIN, then SACCO_ADMIN, then MEMBER.
    """
    if user is None or not getattr(user, 'is_authenticated', True):
        return {
            'is_sacco_admin': False,
            'sacco_id': None,
            'sacco_name': None,
            'role': Role.MEMBER,
        }

    super_role = Role.objects.filter(
        user=user,
        name=Role.SUPER_ADMIN,
    ).first()
    if super_role is not None:
        return {
            'is_sacco_admin': False,
            'sacco_id': None,
            'sacco_name': None,
            'role': Role.SUPER_ADMIN,
        }

    admin_role = (
        Role.objects.filter(
            user=user,
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
        )
        .select_related('sacco')
        .first()
    )
    if admin_role is not None:
        return {
            'is_sacco_admin': True,
            'sacco_id': str(admin_role.sacco.id),
            'sacco_name': admin_role.sacco.name,
            'role': Role.SACCO_ADMIN,
        }

    return {
        'is_sacco_admin': False,
        'sacco_id': None,
        'sacco_name': None,
        'role': Role.MEMBER,
    }
