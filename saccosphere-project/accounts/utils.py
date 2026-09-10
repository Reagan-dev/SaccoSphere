"""Shared account helper utilities."""

from config.utils import get_client_ip as _get_client_ip
from saccomanagement.models import Role


def get_client_ip(request):
    """Return the client IP address from the request.

    Thin wrapper around :func:`config.utils.get_client_ip` - the single
    canonical client-IP resolver for this project (Railway single-hop
    Envoy edge; prefers ``X-Envoy-External-Address``, else the rightmost
    non-internal ``X-Forwarded-For`` entry, else ``REMOTE_ADDR``). Kept as
    a named export because callers across the accounts app import it from
    here.

    Returns:
        str | None: the client IP, or None if nothing usable is present.
    """
    return _get_client_ip(request)


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
        is_active=True,
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
            is_active=True,
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
