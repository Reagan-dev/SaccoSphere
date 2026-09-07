"""Shared account helper utilities."""

from saccomanagement.models import Role


def get_client_ip(request):
    """
    Extract the client IP address from the request.

    Checks X-Forwarded-For header (for proxy/load balancer setups) and falls
    back to REMOTE_ADDR. Takes the rightmost IP from X-Forwarded-For, not the
    leftmost: this deployment sits behind exactly one reverse proxy (Render or
    Railway), which appends the true client IP as the last entry in the chain.
    Anything before that is client-supplied and can be spoofed by sending a
    fabricated X-Forwarded-For header, so trusting the leftmost entry would
    let a client forge its own IP for throttling and audit purposes. This
    matches payments.integrations.mpesa.security._get_client_ip, which reasons
    through the same single-hop trust boundary for M-Pesa callback security.

    Args:
        request: The Django request object.

    Returns:
        str: The client IP address, or None if not found.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # X-Forwarded-For format here is [client-supplied, ..., real_client_ip]
        # since our one reverse proxy hop appends the true IP last.
        ips = [ip.strip() for ip in x_forwarded_for.split(',') if ip.strip()]
        if ips:
            return ips[-1]

    remote_addr = request.META.get('REMOTE_ADDR')
    return remote_addr


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
