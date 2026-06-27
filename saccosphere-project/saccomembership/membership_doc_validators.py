from pathlib import Path

from django.core.exceptions import ValidationError


ALLOWED_MEMBERSHIP_DOCUMENT_EXTENSIONS = {
    'pdf',
    'jpg',
    'jpeg',
    'png',
}
MAX_MEMBERSHIP_DOCUMENT_SIZE = 10 * 1024 * 1024


def validate_membership_document(file):
    extension = Path(file.name).suffix.lower().lstrip('.')

    if extension not in ALLOWED_MEMBERSHIP_DOCUMENT_EXTENSIONS:
        raise ValidationError(
            'Membership documents must be PDF, JPG, JPEG, or PNG files.'
        )

    if file.size > MAX_MEMBERSHIP_DOCUMENT_SIZE:
        raise ValidationError(
            'Membership documents must be 10MB or smaller.'
        )

    return file
