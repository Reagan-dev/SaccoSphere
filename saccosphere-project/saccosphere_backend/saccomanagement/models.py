from django.db import models
from django.conf import settings
from accounts.models import Sacco
import uuid

# sacco managememnt model 
class Management(models.Model):
    MANAGEMENT_CHOICES =[
        ("verified", "Verified"),
        ("updated", "Updated"),
        ("removed", "Removed"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sacco = models.ForeignKey(Sacco, on_delete=models.CASCADE, related_name="saccomanagement")
    management = models.CharField(max_length=20, choices=MANAGEMENT_CHOICES)

    def __str__ (self):
        return f"{self.sacco.name} - {self.management}"
