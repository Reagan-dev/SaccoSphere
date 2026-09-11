"""Add MpesaIdempotencyRecord.kind and scope uniqueness to it.

external_reference_id alone used to be the unique key - correct only
because STK's CheckoutRequestID (ws_CO_...) and B2C's ConversationID
(AG_...) namespaces happen not to collide, an implementation detail of
Safaricom's own ID generation this app has no contract for. kind makes
the STK/B2C distinction explicit, backfilled here from the existing
prefix pattern (ws_CO_ -> STK, AG_ -> B2C), and uniqueness moves to the
(kind, external_reference_id) pair - see the model docstring for the
reasoning.

kind is added nullable, backfilled, then tightened to NOT NULL in the
same migration - the standard safe sequence for adding a required field
to a table that already has rows.
"""

from django.db import migrations, models


def backfill_kind(apps, schema_editor):
    MpesaIdempotencyRecord = apps.get_model(
        'payments', 'MpesaIdempotencyRecord',
    )
    MpesaIdempotencyRecord.objects.filter(
        external_reference_id__startswith='ws_CO_',
    ).update(kind='STK')
    MpesaIdempotencyRecord.objects.filter(
        external_reference_id__startswith='AG_',
    ).update(kind='B2C')
    # Anything matching neither prefix shouldn't happen - these are the
    # only two id shapes Safaricom issues for the flows this table
    # covers - but fall back to STK (the original, far more common
    # caller) rather than leave a row NULL going into the NOT NULL step
    # below.
    MpesaIdempotencyRecord.objects.filter(kind__isnull=True).update(
        kind='STK',
    )


class Migration(migrations.Migration):

    dependencies = [
        (
            'payments',
            '0011_rename_checkout_request_id_to_external_reference_id',
        ),
    ]

    operations = [
        migrations.AddField(
            model_name='mpesaidempotencyrecord',
            name='kind',
            field=models.CharField(
                choices=[('STK', 'STK Push'), ('B2C', 'B2C')],
                help_text=(
                    'Which M-Pesa flow this identifier belongs to.'
                ),
                max_length=3,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_kind, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='mpesaidempotencyrecord',
            name='kind',
            field=models.CharField(
                choices=[('STK', 'STK Push'), ('B2C', 'B2C')],
                help_text=(
                    'Which M-Pesa flow this identifier belongs to.'
                ),
                max_length=3,
            ),
        ),
        migrations.AlterField(
            model_name='mpesaidempotencyrecord',
            name='external_reference_id',
            field=models.CharField(
                help_text=(
                    "Safaricom's own identifier for the already-"
                    'processed request - CheckoutRequestID for STK, '
                    'ConversationID for B2C.'
                ),
                max_length=100,
            ),
        ),
        migrations.AddConstraint(
            model_name='mpesaidempotencyrecord',
            constraint=models.UniqueConstraint(
                fields=('kind', 'external_reference_id'),
                name='unique_mpesa_idempotency_kind_external_reference',
            ),
        ),
        migrations.AlterField(
            model_name='mpesaidempotencyrecord',
            name='processed_at',
            field=models.DateTimeField(
                auto_now_add=True,
                help_text='Date and time this request was processed.',
            ),
        ),
    ]
