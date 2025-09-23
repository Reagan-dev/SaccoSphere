from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import saving
from payments.models import Transaction, PaymentProvider
from django.utils import timezone
import uuid

@receiver(post_save, sender=saving)
def create_transaction_for_saving(sender, instance, created, **kwargs):
    if created:
        Transaction.objects.create(
            user=instance.member,
            provider=PaymentProvider.objects.first(), 
            amount=instance.amount,
            status="SUCCESS",
            reference=str(uuid.uuid4()),
            description=f"{instance.transaction_type} saving",
            created_at=timezone.now(),
        )