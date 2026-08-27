"""KYC document access logging and signed URL generation."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.signing import TimestampSigner
from django.utils import timezone

from saccomanagement.audit_logger import log_audit


logger = logging.getLogger('accounts.kyc_document_access')


def generate_kyc_document_url(
    kyc_verification,
    document_field,
    viewer,
    request=None,
    expiration_minutes=15,
):
    """
    Generate a signed URL for accessing a KYC document with audit logging.

    This function creates a time-limited, signed URL for KYC document access
    and logs the access event in the SystemAuditLog for compliance tracking.

    Args:
        kyc_verification: KYCVerification instance
        document_field: Field name ('id_front', 'id_back', 'passport', 'huduma')
        viewer: User instance requesting access
        request: Optional HTTP request for IP/user-agent logging
        expiration_minutes: URL validity duration (default: 15 minutes)

    Returns:
        str: Signed URL for document access, or None if document doesn't exist

    Raises:
        ValueError: If document_field is invalid or document is None
    """
    valid_fields = ['id_front', 'id_back', 'passport', 'huduma']
    if document_field not in valid_fields:
        raise ValueError(
            f'Invalid document_field: {document_field}. '
            f'Must be one of: {valid_fields}'
        )

    document = getattr(kyc_verification, document_field, None)
    if document is None or not document.name:
        logger.warning(
            'Attempted to generate URL for non-existent document',
            extra={
                'kyc_verification_id': str(kyc_verification.id),
                'document_field': document_field,
                'viewer_id': str(viewer.id) if viewer else None,
            },
        )
        return None

    # Log the access event before generating URL
    log_audit(
        user=viewer,
        action='KYC_DOCUMENT_ACCESS',
        resource_type='KYCDocument',
        resource_id=str(kyc_verification.id),
        new_values={
            'document_field': document_field,
            'document_name': document.name,
            'viewer_email': viewer.email if viewer else None,
        },
        request=request,
    )

    # Generate signed URL based on storage backend
    if settings.STORAGE_BACKEND == 's3':
        return _generate_s3_presigned_url(document, expiration_minutes)
    else:
        return _generate_local_signed_url(
            kyc_verification, document_field, expiration_minutes
        )


def _generate_s3_presigned_url(document, expiration_minutes):
    """
    Generate a presigned URL for S3-stored documents.

    Uses boto3's S3 client to create a time-limited URL that grants
    temporary access without requiring AWS credentials.

    Args:
        document: FileField instance
        expiration_minutes: URL validity duration in minutes

    Returns:
        str: Presigned S3 URL
    """
    try:
        import boto3
        from botocore.exceptions import ClientError

        s3_client = boto3.client('s3')
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        key = document.name

        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=expiration_minutes * 60,
        )
        logger.info(
            'Generated S3 presigned URL for KYC document',
            extra={'key': key, 'bucket': bucket_name},
        )
        return url
    except (ImportError, ClientError) as e:
        logger.error(
            'Failed to generate S3 presigned URL',
            extra={'error': str(e), 'document_name': document.name},
            exc_info=True,
        )
        raise


def _generate_local_signed_url(
    kyc_verification, document_field, expiration_minutes
):
    """
    Generate a signed URL for locally-stored documents.

    Uses Django's TimestampSigner to create a token that can be verified
    by a serving view. The URL includes a signature that expires after
    the specified duration.

    Args:
        kyc_verification: KYCVerification instance
        document_field: Field name
        expiration_minutes: URL validity duration in minutes

    Returns:
        str: Signed URL for local file access
    """
    signer = TimestampSigner()
    token = signer.sign_object({
        'kyc_id': str(kyc_verification.id),
        'document_field': document_field,
    })

    # Build URL to a serving view (to be implemented)
    from django.urls import reverse
    url = reverse(
        'accounts:kyc-document-serve',
        kwargs={
            'kyc_id': str(kyc_verification.id),
            'document_field': document_field,
            'token': token,
        },
    )
    return url
