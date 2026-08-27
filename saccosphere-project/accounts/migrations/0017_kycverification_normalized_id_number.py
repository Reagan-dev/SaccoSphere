# Generated migration for KYC ID number normalization and unique constraint

from django.db import migrations, models


def normalize_id_number(id_number):
    """
    Normalize a Kenyan national ID number for comparison and storage.

    Normalization rules:
    - Strip leading/trailing whitespace
    - Remove all non-alphanumeric characters (spaces, dashes, slashes, etc.)
    - Convert to uppercase for consistency
    """
    if id_number is None:
        return None

    if not isinstance(id_number, str):
        id_number = str(id_number)

    # Strip whitespace
    id_number = id_number.strip()

    # Return empty string if input was just whitespace
    if not id_number:
        return ''

    # Remove all non-alphanumeric characters
    import re
    normalized = re.sub(r'[^a-zA-Z0-9]', '', id_number)

    # Convert to uppercase
    normalized = normalized.upper()

    return normalized


def populate_normalized_id_numbers(apps, schema_editor):
    """Populate normalized_id_number from existing id_number values."""
    KYCVerification = apps.get_model('accounts', 'KYCVerification')

    for kyc in KYCVerification.objects.all():
        if kyc.id_number:
            kyc.normalized_id_number = normalize_id_number(kyc.id_number)
            kyc.save(update_fields=['normalized_id_number'])


def reverse_populate_normalized_id_numbers(apps, schema_editor):
    """Reverse: clear normalized_id_number values."""
    KYCVerification = apps.get_model('accounts', 'KYCVerification')
    KYCVerification.objects.all().update(normalized_id_number=None)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_otptoken_unique_active_otp_per_phone_purpose'),
    ]

    operations = [
        # Step 1: Add the normalized_id_number field (nullable initially)
        migrations.AddField(
            model_name='kycverification',
            name='normalized_id_number',
            field=models.CharField(
                max_length=20,
                null=True,
                blank=True,
                db_index=True,
                help_text='Normalized ID number (stripped of punctuation, uppercase).',
            ),
        ),
        # Step 2: Populate normalized values from existing data
        migrations.RunPython(
            populate_normalized_id_numbers,
            reverse_populate_normalized_id_numbers,
        ),
        # Step 3: Add the partial unique constraint
        migrations.AddConstraint(
            model_name='kycverification',
            constraint=models.UniqueConstraint(
                fields=['normalized_id_number'],
                condition=~models.Q(normalized_id_number__isnull=True)
                & ~models.Q(normalized_id_number__exact=''),
                name='unique_normalized_id_number',
                violation_error_message=(
                    'A user with this national ID number already exists.'
                ),
            ),
        ),
    ]
