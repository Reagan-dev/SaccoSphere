"""Savings-withdrawal initiation idempotency key.

New table only - no change to existing rows. Reversible: rolling back
drops the table (no data migration, nothing to restore). Online-safe, no
maintenance window.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0006_transaction_status_initiation_failed'),
        ('saccomembership', '0002_membershipdocument'),
        ('services', '0012_index_loan_repaymentschedule_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='SavingsWithdrawalIdempotencyKey',
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
                    'key',
                    models.CharField(
                        help_text=(
                            'member:saving:gross_amount:request_id '
                            'composite key.'
                        ),
                        max_length=255,
                        unique=True,
                    ),
                ),
                (
                    'amount',
                    models.DecimalField(
                        decimal_places=2,
                        help_text=(
                            'Gross amount requested for this withdrawal.'
                        ),
                        max_digits=12,
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'membership',
                    models.ForeignKey(
                        help_text=(
                            'Membership the withdrawal belongs to '
                            '(tenant anchor).'
                        ),
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='withdrawal_idempotency_keys',
                        to='saccomembership.membership',
                    ),
                ),
                (
                    'saving',
                    models.ForeignKey(
                        help_text=(
                            'Saving account debited by the withdrawal.'
                        ),
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='withdrawal_idempotency_keys',
                        to='services.saving',
                    ),
                ),
                (
                    'transaction',
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            'Withdrawal Transaction created for the first '
                            'request.'
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='payments.transaction',
                    ),
                ),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
