"""Encrypt KYCVerification.id_number at rest (Fernet), backfilling rows.

Online-safety: the schema step is a varchar widen (20 -> 255), which on
PostgreSQL is a catalog-only change with no table rewrite and only a
brief ACCESS EXCLUSIVE lock. The data step below re-writes each non-empty
id_number in place, chunked, and is idempotent (already-encrypted values
are skipped), so it is safe to run online. See the migration in the PR
description for the maintenance-window assessment and row-count basis.
"""

import accounts.models
from django.db import migrations


BATCH_SIZE = 1000


def _fernet():
    from cryptography.fernet import Fernet
    from django.conf import settings

    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', None)
    if not key:
        raise RuntimeError(
            'FIELD_ENCRYPTION_KEY must be set to run migration '
            'accounts.0026_encrypt_pii_fields.'
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def _is_fernet_token(fernet, value):
    try:
        fernet.decrypt(value.encode())
        return True
    except Exception:
        return False


def _rewrite(schema_editor, transform):
    fernet = _fernet()
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, id_number FROM accounts_kycverification "
            "WHERE id_number IS NOT NULL AND id_number <> ''"
        )
        rows = cursor.fetchall()

    updates = []
    for pk, value in rows:
        new_value = transform(fernet, value)
        if new_value is not None and new_value != value:
            updates.append((new_value, pk))

    with connection.cursor() as cursor:
        for start in range(0, len(updates), BATCH_SIZE):
            for new_value, pk in updates[start:start + BATCH_SIZE]:
                cursor.execute(
                    "UPDATE accounts_kycverification SET id_number = %s "
                    "WHERE id = %s",
                    [new_value, pk],
                )


def encrypt_id_numbers(apps, schema_editor):
    def transform(fernet, value):
        if _is_fernet_token(fernet, value):
            return None
        return fernet.encrypt(value.encode()).decode()

    _rewrite(schema_editor, transform)


def decrypt_id_numbers(apps, schema_editor):
    def transform(fernet, value):
        try:
            return fernet.decrypt(value.encode()).decode()
        except Exception:
            return None

    _rewrite(schema_editor, transform)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0025_remove_saccosettings_requires_guarantor'),
    ]

    operations = [
        migrations.AlterField(
            model_name='kycverification',
            name='id_number',
            field=accounts.models.EncryptedCharField(
                blank=True,
                help_text=(
                    'Kenya National ID number (encrypted at rest). '
                    'Lookups go through normalized_id_number, which is '
                    'the queryable key.'
                ),
                max_length=255,
                null=True,
            ),
        ),
        migrations.RunPython(encrypt_id_numbers, decrypt_id_numbers),
    ]
