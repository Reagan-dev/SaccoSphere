from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


ACTIVE_STATUSES = (
    'PENDING_SMS',
    'SMS_SENT',
    'ACCEPTED',
    'UNDER_ADMIN_REVIEW',
    'APPROVED_BY_ADMIN',
)


def dedupe_active_external_guarantors(apps, schema_editor):
    """Resolve any pre-existing duplicate active (loan, id_number) rows.

    Before this migration only APPROVED_BY_ADMIN duplicates were blocked,
    so PENDING_SMS / SMS_SENT / ACCEPTED duplicates for the same person on
    the same loan may exist. Keep the most recently created active row per
    (loan_id, id_number) and mark the rest EXPIRED so the partial unique
    index below can be built. Safe to run online: the table is small and
    only redundant rows are touched.
    """
    ExternalGuarantor = apps.get_model('guarantor', 'ExternalGuarantor')

    dupe_keys = (
        ExternalGuarantor.objects.filter(status__in=ACTIVE_STATUSES)
        .values('loan_id', 'id_number')
        .annotate(n=Count('id'))
        .filter(n__gt=1)
    )
    for key in dupe_keys:
        rows = list(
            ExternalGuarantor.objects.filter(
                loan_id=key['loan_id'],
                id_number=key['id_number'],
                status__in=ACTIVE_STATUSES,
            ).order_by('-created_at', '-id')
        )
        for stale in rows[1:]:
            stale.status = 'EXPIRED'
            stale.save(update_fields=['status', 'updated_at'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0025_remove_saccosettings_requires_guarantor'),
        ('guarantor', '0002_alter_externalguarantor_id'),
        (
            'services',
            '0009_loan_disbursement_idempotency_key_and_more',
        ),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='externalguarantor',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING_SMS', 'Pending SMS'),
                    ('SMS_SENT', 'SMS sent'),
                    ('ACCEPTED', 'Accepted'),
                    ('DECLINED', 'Declined'),
                    ('EXPIRED', 'Expired (no response)'),
                    ('UNDER_ADMIN_REVIEW', 'Under admin review'),
                    ('APPROVED_BY_ADMIN', 'Approved by admin'),
                    ('REJECTED_BY_ADMIN', 'Rejected by admin'),
                ],
                default='PENDING_SMS',
                max_length=30,
            ),
        ),
        migrations.RunPython(
            dedupe_active_external_guarantors,
            noop_reverse,
        ),
        migrations.AddConstraint(
            model_name='externalguarantor',
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ('status__in', ACTIVE_STATUSES),
                ),
                fields=('loan', 'id_number'),
                name='uniq_active_external_guarantor_per_loan_id_number',
            ),
        ),
    ]
