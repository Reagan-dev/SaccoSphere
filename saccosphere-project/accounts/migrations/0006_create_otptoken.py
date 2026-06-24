# accounts/migrations/0006_create_otptoken.py

import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_kyc_document_storage_and_huduma'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OTPToken',
            fields=[
                ('id', models.UUIDField(
                    primary_key=True,
                    default=uuid.uuid4,
                    editable=False,
                    serialize=False,
                    help_text='Unique OTP token identifier.',
                )),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='otp_tokens',
                    to=settings.AUTH_USER_MODEL,
                    help_text='User who owns this OTP token. Null for registration OTPs.',
                )),
                ('phone_number', models.CharField(
                    max_length=13,
                    help_text='Phone number receiving the OTP.',
                )),
                ('code', models.CharField(
                    max_length=6,
                    help_text='Six-digit OTP code stored as a string.',
                )),
                ('purpose', models.CharField(
                    max_length=20,
                    default='PHONE_VERIFY',
                    choices=[
                        ('PHONE_VERIFY', 'Phone verification'),
                        ('PASSWORD_RESET', 'Password reset'),
                        ('LOGIN', 'Login'),
                    ],
                    help_text='Reason this OTP was created.',
                )),
                ('is_used', models.BooleanField(
                    default=False,
                    db_index=True,
                    help_text='Whether this OTP has already been used.',
                )),
                ('attempts', models.PositiveSmallIntegerField(
                    default=0,
                    help_text='Number of failed verification attempts.',
                )),
                ('expires_at', models.DateTimeField(
                    db_index=True,
                    help_text='Date and time this OTP expires.',
                )),
                ('created_at', models.DateTimeField(
                    auto_now_add=True,
                    help_text='Date and time this OTP was created.',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]