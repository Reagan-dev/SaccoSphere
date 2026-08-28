"""KYC document retention and erasure utilities."""

import logging

from django.utils import timezone
from django.conf import settings
from django.db import models

from accounts.models import KYCVerification, DataErasureRequest
from saccomanagement.audit_logger import log_audit


logger = logging.getLogger('accounts.kyc_retention')


def check_kyc_erasure_holds(user):
    """
    Check if any regulatory or dispute holds apply to this user's KYC data.

    Returns:
        tuple: (has_hold, hold_reason, hold_until)
            - has_hold: bool - True if a hold is currently active
            - hold_reason: str or None - The reason for the hold
            - hold_until: datetime or None - When the hold expires
    """
    # Check for active erasure requests on hold
    active_holds = DataErasureRequest.objects.filter(
        user=user,
        status=DataErasureRequest.Status.ON_HOLD,
    ).filter(
        models.Q(hold_until__isnull=True) | models.Q(hold_until__gt=timezone.now())
    )

    if active_holds.exists():
        hold = active_holds.first()
        return True, hold.get_hold_reason_display(), hold.hold_until

    return False, None, None


def anonymize_kyc_record(kyc, triggered_by=None, reason=None):
    """
    Anonymize a KYC record by deleting S3 objects and PII fields.

    This function:
    1. Deletes S3 objects (id_front, id_back, passport, huduma)
    2. Anonymizes PII fields (id_number, normalized_id_number, huduma_namba)
    3. Preserves minimal trail (status, verified_at, submitted_at)
    4. Logs the cleanup event to audit trail

    Args:
        kyc: KYCVerification instance to anonymize
        triggered_by: User instance who triggered the erasure (None for system)
        reason: Reason for the erasure

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Delete S3 objects
        _delete_s3_objects(kyc)

        # Anonymize PII fields
        old_values = {
            'id_number': kyc.id_number,
            'normalized_id_number': kyc.normalized_id_number,
            'huduma_namba': kyc.huduma_namba,
            'id_front': bool(kyc.id_front),
            'id_back': bool(kyc.id_back),
            'passport': bool(kyc.passport),
            'huduma': bool(kyc.huduma),
        }

        kyc.id_number = None
        kyc.normalized_id_number = None
        kyc.huduma_namba = None

        # Clear document fields
        kyc.id_front = None
        kyc.id_back = None
        kyc.passport = None
        kyc.huduma = None

        # Mark as anonymized
        kyc.status = KYCVerification.Status.REJECTED
        kyc.rejection_reason = reason or 'Documents anonymized per erasure request.'

        # Save changes
        kyc.save()

        # Log to audit trail
        log_audit(
            user=triggered_by,
            action='KYC_ERASURE',
            resource_type='KYCDocument',
            resource_id=str(kyc.id),
            old_values=old_values,
            new_values={
                'status': kyc.status,
                'rejection_reason': kyc.rejection_reason,
                'triggered_by': triggered_by.email if triggered_by else 'System',
            },
        )

        logger.info(
            'KYC record anonymized',
            extra={
                'kyc_id': str(kyc.id),
                'user_email': kyc.user.email,
                'triggered_by': triggered_by.email if triggered_by else 'System',
            },
        )

        return True

    except Exception as e:
        logger.error(
            f'Failed to anonymize KYC record {kyc.id}',
            exc_info=True,
            extra={'kyc_id': str(kyc.id)},
        )
        return False


def _delete_s3_objects(kyc):
    """Delete S3 objects for a KYC record."""
    document_fields = ['id_front', 'id_back', 'passport', 'huduma']

    for field_name in document_fields:
        document = getattr(kyc, field_name, None)
        if document and document.name:
            try:
                if hasattr(document, 'delete'):
                    document.delete(save=False)
                logger.info(
                    f'Deleted S3 object: {document.name}',
                    extra={'field': field_name, 'kyc_id': str(kyc.id)},
                )
            except Exception as e:
                logger.error(
                    f'Failed to delete S3 object: {document.name}',
                    exc_info=True,
                    extra={'field': field_name, 'kyc_id': str(kyc.id)},
                )
                raise


def process_queued_erasure_requests():
    """
    Process erasure requests that were on hold but are now eligible.

    This function should be called periodically (e.g., via Celery beat)
    to check for erasure requests whose holds have expired.
    """
    from django.db import models

    now = timezone.now()

    # Find requests on hold where hold_until has passed
    eligible_requests = DataErasureRequest.objects.filter(
        status=DataErasureRequest.Status.ON_HOLD,
        hold_until__lte=now,
    )

    for request in eligible_requests:
        try:
            # Get the user's KYC record
            kyc = KYCVerification.objects.filter(user=request.user).first()
            if kyc:
                success = anonymize_kyc_record(
                    kyc,
                    triggered_by=None,
                    reason=f'Erasure request processed after hold lifted: {request.get_hold_reason_display()}',
                )
                if success:
                    request.status = DataErasureRequest.Status.COMPLETED
                    request.completed_at = now
                    request.save()
                    logger.info(
                        f'Processed queued erasure request {request.id}',
                        extra={'request_id': str(request.id)},
                    )
        except Exception as e:
            logger.error(
                f'Failed to process erasure request {request.id}',
                exc_info=True,
                extra={'request_id': str(request.id)},
            )
