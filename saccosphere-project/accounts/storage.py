"""Storage backends for account documents."""

from django.core.files.storage import FileSystemStorage


class KYCDocumentStorage(FileSystemStorage):
    """Store KYC documents on the local filesystem in development."""

    # TODO: Replace with django-storages S3 backend for production.
