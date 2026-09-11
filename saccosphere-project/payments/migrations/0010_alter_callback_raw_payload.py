"""Encrypt Callback.raw_payload at rest (Fernet).

raw_payload carries the member's phone number and, for B2C, their name
- personal data under Kenya's DPA 2019 - and was the one PII-bearing
field in this app not already routed through the Fernet-encrypted-field
infrastructure protecting id_number/CRB raw responses/Daraja
credentials.

Online-safety: the AlterField changes the column from jsonb to text
(PostgreSQL: ALTER COLUMN ... TYPE text USING raw_payload::text), which
rewrites the table under an ACCESS EXCLUSIVE lock for the rewrite
duration - mirrors services.0010_encrypt_pii_fields; see that migration's
note for the maintenance-window reasoning. The RunPython step then
encrypts each row's now-plaintext JSON text in place, chunked, and is
idempotent (a row that is already a Fernet token is left alone).
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
            'payments.0010_alter_callback_raw_payload.'
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
            'SELECT id, raw_payload FROM payments_callback '
            "WHERE raw_payload IS NOT NULL AND raw_payload <> ''"
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
                    'UPDATE payments_callback SET raw_payload = %s '
                    'WHERE id = %s',
                    [new_value, pk],
                )


def encrypt_raw_payloads(apps, schema_editor):
    def transform(fernet, value):
        if _is_fernet_token(fernet, value):
            return None
        return fernet.encrypt(value.encode()).decode()

    _rewrite(schema_editor, transform)


def decrypt_raw_payloads(apps, schema_editor):
    def transform(fernet, value):
        try:
            return fernet.decrypt(value.encode()).decode()
        except Exception:
            return None

    _rewrite(schema_editor, transform)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0009_alter_transaction_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='callback',
            name='raw_payload',
            field=accounts.models.EncryptedJSONField(
                help_text=(
                    'Raw provider callback payload, encrypted at rest '
                    "(Fernet) - carries the member's phone number and, "
                    'for B2C, their name. Deleted by the retention '
                    'sweep once CALLBACK_RETENTION_DAYS passes - see '
                    'payments.tasks.purge_expired_callbacks.'
                ),
            ),
        ),
        migrations.RunPython(encrypt_raw_payloads, decrypt_raw_payloads),
    ]
