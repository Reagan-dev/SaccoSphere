def create_data_consent_log(user, data_type, reason, accessed_by=None):
    """Record that a user's data was accessed for an auditable reason."""
    from .odpc_logging import create_data_consent_log as create_odpc_log

    return create_odpc_log(
        user=user,
        accessed_by=accessed_by or user,
        data_type=data_type,
        reason=reason,
    )
