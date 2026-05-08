# Generated migration for savings categorisation

from decimal import Decimal
from django.db import migrations, transaction
import uuid


def set_default_savings_type(apps, schema_editor):
    """
    Migrate existing Saving records to BOSA savings type.
    
    If existing Saving records exist, create BOSA SavingsType for each SACCO
    and update all null savings_type FKs to point to BOSA type.
    """
    Saving = apps.get_model('services', 'Saving')
    SavingsType = apps.get_model('services', 'SavingsType')
    Sacco = apps.get_model('accounts', 'Sacco')
    
    with transaction.atomic():
        # Find all SACCOs that have savings without savings_type
        sacco_ids_with_savings = Saving.objects.filter(
            savings_type__isnull=True
        ).values_list('membership__sacco_id', flat=True).distinct()
        
        for sacco_id in sacco_ids_with_savings:
            # Create BOSA SavingsType for this SACCO if it doesn't exist
            bosa_type, created = SavingsType.objects.get_or_create(
                id=uuid.uuid4(),
                sacco_id=sacco_id,
                name=SavingsType.Name.BOSA,
                defaults={
                    'description': 'Basic Ordinary Savings Account',
                    'minimum_contribution': Decimal('0.00'),
                    'is_active': True,
                }
            )
            
            # Update all savings for this SACCO to use BOSA type
            Saving.objects.filter(
                membership__sacco_id=sacco_id,
                savings_type__isnull=True
            ).update(savings_type=bosa_type)


def reverse_set_default_savings_type(apps, schema_editor):
    """
    Reverse migration: set savings_type back to null.
    """
    Saving = apps.get_model('services', 'Saving')
    
    # Set all savings_type back to null
    Saving.objects.all().update(savings_type=None)


class Migration(migrations.Migration):
    """
    Migrate existing savings to BOSA type and ensure data integrity.
    """
    
    dependencies = [
        ('services', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            set_default_savings_type,
            reverse_set_default_savings_type,
        ),
    ]
