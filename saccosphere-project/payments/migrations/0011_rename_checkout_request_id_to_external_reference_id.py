"""Rename MpesaIdempotencyRecord.checkout_request_id to
external_reference_id.

checkout_request_id was reused to hold Safaricom's ConversationID for
B2C too, not just STK's CheckoutRequestID - misleading to read. This is
a straight column rename (PostgreSQL: ALTER TABLE ... RENAME COLUMN,
metadata-only, no table rewrite) - existing data is preserved as-is, no
backfill needed for the rename itself. The kind discriminator field that
actually distinguishes STK from B2C rows is added, and backfilled, in
the next migration.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0010_alter_callback_raw_payload'),
    ]

    operations = [
        migrations.RenameField(
            model_name='mpesaidempotencyrecord',
            old_name='checkout_request_id',
            new_name='external_reference_id',
        ),
    ]
