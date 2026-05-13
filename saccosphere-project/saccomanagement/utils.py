import logging


logger = logging.getLogger('saccosphere.data_consent')


def create_data_consent_log(user, data_type, reason, accessed_by=None):
    """Record that a user's data was accessed for an auditable reason."""
    from .models import DataConsentLog

    try:
        return DataConsentLog.objects.create(
            user=user,
            accessed_by=accessed_by or user,
            data_type=data_type,
            reason=reason,
        )
    except Exception:
        logger.exception(
            'Failed to create data consent log for user_id=%s.',
            getattr(user, 'id', None),
        )
        return None
