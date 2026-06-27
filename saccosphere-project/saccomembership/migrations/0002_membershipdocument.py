import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('saccomembership', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MembershipDocument',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'document_type',
                    models.CharField(
                        choices=[
                            (
                                'NATIONAL_ID_FRONT',
                                'National ID front',
                            ),
                            (
                                'NATIONAL_ID_BACK',
                                'National ID back',
                            ),
                            ('PASSPORT', 'Passport'),
                            ('PASSPORT_PHOTO', 'Passport photo'),
                            ('LATEST_PAYSLIP', 'Latest payslip'),
                            (
                                'BANK_STATEMENT_3M',
                                'Bank statement - 3 months',
                            ),
                            (
                                'MPESA_STATEMENT_3M',
                                'M-Pesa statement - 3 months',
                            ),
                            ('OTHER', 'Other'),
                        ],
                        max_length=30,
                    ),
                ),
                ('file', models.FileField(upload_to='membership_docs/')),
                ('file_name', models.CharField(blank=True, max_length=100)),
                ('file_size_bytes', models.PositiveIntegerField(default=0)),
                (
                    'notes',
                    models.CharField(
                        blank=True,
                        max_length=200,
                        null=True,
                    ),
                ),
                ('is_verified', models.BooleanField(default=False)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                (
                    'application',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='membership_documents',
                        to='saccomembership.saccoapplication',
                    ),
                ),
            ],
            options={
                'ordering': ['document_type'],
            },
        ),
    ]
