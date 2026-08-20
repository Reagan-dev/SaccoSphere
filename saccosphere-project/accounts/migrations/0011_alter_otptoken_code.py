from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_user_google_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='otptoken',
            name='code',
            field=models.CharField(
                help_text='HMAC-SHA256 hash of the six-digit OTP code.',
                max_length=64,
            ),
        ),
    ]
