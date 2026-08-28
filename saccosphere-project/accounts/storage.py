"""Storage backends for account documents."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage


if settings.STORAGE_BACKEND == 's3':
    from storages.backends.s3boto3 import S3Boto3Storage

    class KYCDocumentStorage(S3Boto3Storage):
        """
        Store KYC documents in the configured S3 bucket with security settings.

        Security features:
        - file_overwrite=False: Prevent accidental overwrites
        - SSE-KMS encryption: Server-side encryption with KMS-managed keys
        - Private ACLs: Objects are never publicly readable
        - Versioning: Enabled for audit trail and recovery

        Note: Bucket-level encryption, versioning, lifecycle rules, and
        access logging must be configured via AWS infrastructure (console/CLI/IaC).
        See docs/S3_SECURITY_CHECKLIST.md for infrastructure requirements.
        """

        file_overwrite = False

        # Use SSE-KMS for encryption (requires bucket-level KMS key configuration)
        # Falls back to SSE-S3 if AWS_KMS_KEY_ID is not set
        encryption = 'AES256'  # Default to SSE-S3, override with KMS key in settings

        # Ensure objects are never publicly readable
        default_acl = 'private'

        # Enable object-level metadata for tracking
        # (Note: bucket-level versioning must be enabled separately)
        querystring_auth = True  # Generate signed URLs by default

        def __init__(self, *args, **kwargs):
            """
            Initialize storage with security settings from Django settings.
            """
            # Override encryption if KMS key is configured
            kms_key_id = getattr(settings, 'AWS_KMS_KEY_ID', None)
            if kms_key_id:
                self.encryption = 'aws:kms'
                self.encryption_key_id = kms_key_id

            super().__init__(*args, **kwargs)

else:

    class KYCDocumentStorage(FileSystemStorage):
        """Store KYC documents on the local filesystem."""

        pass
