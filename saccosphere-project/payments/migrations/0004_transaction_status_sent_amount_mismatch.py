from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_remove_platformfee_processed'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending'),
                    ('PROCESSING', 'Processing'),
                    ('SENT', 'Sent'),
                    ('COMPLETED', 'Completed'),
                    ('FAILED', 'Failed'),
                    ('AMOUNT_MISMATCH', 'Amount mismatch'),
                    ('REVERSED', 'Reversed'),
                ],
                default='PENDING',
                help_text='Current transaction status.',
                max_length=20,
            ),
        ),
    ]
