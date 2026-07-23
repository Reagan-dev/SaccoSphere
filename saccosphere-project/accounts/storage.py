"""Storage backends for account documents."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage


if settings.STORAGE_BACKEND == 's3':
    from storages.backends.s3boto3 import S3Boto3Storage

    class KYCDocumentStorage(S3Boto3Storage):
        """Store KYC documents in the configured S3 bucket."""

        file_overwrite = False

else:

    class KYCDocumentStorage(FileSystemStorage):
        """Store KYC documents on the local filesystem."""

        pass
