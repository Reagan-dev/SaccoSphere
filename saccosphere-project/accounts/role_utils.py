def get_sacco_admin_id(user):
    """Return the first SACCO id administered by the user, if any."""
    if user is None:
        return None

    try:
        from saccomanagement.models import Role
    except ImportError:
        return None

    admin_role = (
        Role.objects.filter(
            user=user,
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
        )
        .select_related('sacco')
        .first()
    )
    if admin_role is None:
        return None

    return str(admin_role.sacco.id)
