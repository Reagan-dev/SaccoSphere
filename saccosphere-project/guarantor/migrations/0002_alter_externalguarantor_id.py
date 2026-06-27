from uuid import uuid4

from django.db import migrations, models


def populate_external_guarantor_ids(apps, schema_editor):
    external_guarantor = apps.get_model('guarantor', 'ExternalGuarantor')

    for guarantor in external_guarantor.objects.filter(uuid__isnull=True):
        guarantor.uuid = uuid4()
        guarantor.save(update_fields=['uuid'])


class Migration(migrations.Migration):

    dependencies = [
        ('guarantor', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='externalguarantor',
            name='uuid',
            field=models.UUIDField(
                editable=False,
                null=True,
            ),
        ),
        migrations.RunPython(
            populate_external_guarantor_ids,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name='externalguarantor',
            name='id',
        ),
        migrations.RenameField(
            model_name='externalguarantor',
            old_name='uuid',
            new_name='id',
        ),
        migrations.AlterField(
            model_name='externalguarantor',
            name='id',
            field=models.UUIDField(
                default=uuid4,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
    ]
