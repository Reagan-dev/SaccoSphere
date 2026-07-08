# Generated migration for PlatformRevenue.is_collected field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformrevenue',
            name='is_collected',
            field=models.BooleanField(
                default=True,
                help_text='Whether this revenue was actually collected in cash.',
            ),
        ),
    ]
