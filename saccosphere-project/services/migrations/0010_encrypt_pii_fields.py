"""Encrypt CRBCheck.raw_response at rest (Fernet) + add its purge date.

Online-safety: the AlterField changes the column from jsonb to text
(PostgreSQL: ALTER COLUMN ... TYPE text USING raw_response::text), which
rewrites the table under an ACCESS EXCLUSIVE lock for the rewrite
duration. At realistic row counts this is seconds; see the PR
description for the maintenance-window assessment and its row-count
basis. The RunPython step then encrypts each row's now-plaintext JSON
text in place, chunked, and is idempotent.
"""

import accounts.models
from django.db import migrations, models


BATCH_SIZE = 1000


def _fernet():
    from cryptography.fernet import Fernet
    from django.conf import settings

    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', None)
    if not key:
        raise RuntimeError(
            'FIELD_ENCRYPTION_KEY must be set to run migration '
            'services.0010_encrypt_pii_fields.'
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
            "SELECT id, raw_response FROM services_crbcheck "
            "WHERE raw_response IS NOT NULL AND raw_response <> ''"
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
                    "UPDATE services_crbcheck SET raw_response = %s "
                    "WHERE id = %s",
                    [new_value, pk],
                )


def _backfill_purge_dates(schema_editor):
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    retention_days = getattr(
        settings,
        'CRB_RAW_RESPONSE_RETENTION_DAYS',
        None,
    )
    if not retention_days:
        return

    purge_at = timezone.now() + timedelta(days=retention_days)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "UPDATE services_crbcheck SET raw_response_purge_at = %s "
            "WHERE raw_response IS NOT NULL AND raw_response <> '' "
            "AND raw_response_purge_at IS NULL",
            [purge_at],
        )


def encrypt_raw_responses(apps, schema_editor):
    def transform(fernet, value):
        if _is_fernet_token(fernet, value):
            return None
        return fernet.encrypt(value.encode()).decode()

    _rewrite(schema_editor, transform)
    _backfill_purge_dates(schema_editor)


def decrypt_raw_responses(apps, schema_editor):
    def transform(fernet, value):
        try:
            return fernet.decrypt(value.encode()).decode()
        except Exception:
            return None

    _rewrite(schema_editor, transform)


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0009_loan_disbursement_idempotency_key_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='crbcheck',
            name='raw_response_purge_at',
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text=(
                    'When raw_response becomes eligible for the '
                    'retention purge. Set on save from '
                    'CRB_RAW_RESPONSE_RETENTION_DAYS.'
                ),
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='crbcheck',
            name='raw_response',
            field=accounts.models.EncryptedJSONField(
                blank=True,
                help_text=(
                    'Raw response from the CRB provider, encrypted at '
                    'rest. Kept for audit only and purged by the '
                    'retention sweep once raw_response_purge_at passes.'
                ),
                null=True,
            ),
        ),
        migrations.RunPython(encrypt_raw_responses, decrypt_raw_responses),
    ]
