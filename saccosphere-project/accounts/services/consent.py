"""Consent management service functions."""

from django.conf import settings
from django.core.exceptions import PermissionDenied


class ConsentRequiredError(PermissionDenied):
    """Raised when a required consent is not active."""

    def __init__(self, consent_type):
        self.consent_type = consent_type
        message = f'Active consent for {consent_type} is required for this operation.'
        super().__init__(message)


def has_active_consent(user, consent_type):
    """
    Check if user has active consent for the given consent type.

    Active consent requires:
    - A UserConsent record exists for the user and consent_type
    - withdrawn_at is null (not withdrawn)
    - version matches the current policy version from CONSENT_POLICY_VERSIONS

    Args:
        user: The User instance
        consent_type: The consent type to check (e.g., 'MARKETING')

    Returns:
        bool: True if active consent exists, False otherwise
    """
    if not user or not user.is_authenticated:
        return False

    # Get current policy version for this consent type
    current_version = settings.CONSENT_POLICY_VERSIONS.get(consent_type)
    if not current_version:
        # No policy version defined for this type
        return False

    # Check for active consent matching current version
    from accounts.models import UserConsent

    return UserConsent.objects.filter(
        user=user,
        consent_type=consent_type,
        version=current_version,
        withdrawn_at__isnull=True,
        consented=True,
    ).exists()


def get_consent_status(user, consent_type):
    """
    Get the current consent status for a user and consent type.

    Returns one of:
    - "active": User has consented to the current policy version
    - "outdated": User has consented, but to an outdated version
    - "withdrawn": User had consent but withdrew it
    - "never_given": User has never given consent for this type

    Args:
        user: The User instance
        consent_type: The consent type to check

    Returns:
        str: The consent status
    """
    if not user or not user.is_authenticated:
        return 'never_given'

    from accounts.models import UserConsent

    # Get the most recent consent for this type
    consent = UserConsent.objects.filter(
        user=user,
        consent_type=consent_type,
    ).order_by('-timestamp').first()

    if not consent:
        return 'never_given'

    # Check if consented=False (explicit denial)
    if not consent.consented:
        return 'never_given'

    # Check if withdrawn
    if consent.withdrawn_at is not None:
        return 'withdrawn'

    # Get current policy version
    current_version = settings.CONSENT_POLICY_VERSIONS.get(consent_type)

    # Check if version matches current
    if current_version and consent.version == current_version:
        return 'active'

    # Version exists but doesn't match current
    return 'outdated'


def require_consent(consent_type):
    """
    Decorator that requires active consent for a given consent type.

    Raises ConsentRequiredError if the user does not have active consent.

    Args:
        consent_type: The consent type required (e.g., 'MARKETING')

    Usage:
        @require_consent('MARKETING')
        def send_marketing_email(user, message):
            # This will only run if user has active marketing consent
            pass
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            # Try to extract user from first argument or kwargs
            user = None
            if args and hasattr(args[0], 'user'):
                # View instance with user attribute
                user = args[0].user
            elif 'user' in kwargs:
                # User passed as keyword argument
                user = kwargs['user']
            elif 'request' in kwargs and hasattr(kwargs['request'], 'user'):
                # Request object passed
                user = kwargs['request'].user
            elif args and len(args) > 0 and hasattr(args[0], 'request'):
                # View with request attribute
                user = args[0].request.user

            if not has_active_consent(user, consent_type):
                raise ConsentRequiredError(consent_type)

            return func(*args, **kwargs)

        return wrapper

    return decorator
