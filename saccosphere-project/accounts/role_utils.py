def get_sacco_admin_id(user):
    """Return the first SACCO id administered by the user, if any."""
    from .utils import get_user_sacco_context

    return get_user_sacco_context(user).get('sacco_id')
