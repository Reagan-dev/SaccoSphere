from django.db import models
from django.conf import settings
import uuid
from accounts.models import Sacco

# saccosphere app containing models related to sacco membership and roles within the multitenant application.
class Membership(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('left', 'Left Sacco'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    sacco = models.ForeignKey(Sacco, on_delete=models.CASCADE, related_name='saccomembership')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    date_joined = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    def approve(self):
        self.status = 'approved'
        self.is_active = True
        self.save()
        
    def reject(self):
        self.status = 'rejected'
        self.is_active = False
        self.save()    

    class Meta:
        unique_together = ('user', 'sacco')

    def __str__(self):
        return f"{self.user.email} - {self.sacco.name} ({self.status})"



# Dynamic registration fields per sacco
class Saccofield(models.Model):
    FIELD_TYPES = [
        ('text', 'Text'),
        ('number', 'Number'),
        ('date', 'Date'),
        ('email', 'Email'),
        ('file', 'File Upload'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sacco = models.ForeignKey(Sacco, on_delete=models.CASCADE, related_name='custom_fields')
    field_name = models.CharField(max_length=100)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default='text')
    required = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.sacco.name} - {self.field_name})"


class MembershipFieldData(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name='field_data')
    sacco_field = models.ForeignKey(Saccofield, on_delete=models.CASCADE)
    value = models.TextField(blank = True, null = True)

    def __str__(self):
        return f"{self.sacco_field.field_name}: {self.value}"