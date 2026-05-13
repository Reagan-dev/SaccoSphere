import logging

from .models import SystemAuditLog


logger = logging.getLogger('saccosphere.audit')


def log_audit(
    user,
    action,
    resource_type,
    resource_id,
    old_values=None,
    new_values=None,
    request=None,
):
    """
    Create a SystemAuditLog entry without crashing calling code.
    """
    try:
        ip_address = None
        user_agent = None

        if request:
            forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if forwarded_for:
                ip_address = forwarded_for.split(',')[0].strip()
            else:
                ip_address = request.META.get('REMOTE_ADDR')
            user_agent = request.META.get('HTTP_USER_AGENT')

        return SystemAuditLog.objects.create(
            user=user,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception:
        logger.exception(
            'Failed to create audit log for resource_type=%s resource_id=%s.',
            resource_type,
            resource_id,
        )
        return None


class AuditMixin:
    """Mixin for admin ViewSets that auto-log create/update/delete."""

    audit_resource_type = ''

    def perform_create(self, serializer):
        instance = serializer.save()
        log_audit(
            self.request.user,
            'CREATE',
            self.audit_resource_type,
            instance.pk,
            new_values=serializer.data,
            request=self.request,
        )

    def perform_update(self, serializer):
        old_data = type(serializer.instance).objects.values().get(
            pk=serializer.instance.pk,
        )
        instance = serializer.save()
        log_audit(
            self.request.user,
            'UPDATE',
            self.audit_resource_type,
            instance.pk,
            old_values=dict(old_data),
            new_values=serializer.data,
            request=self.request,
        )

    def perform_destroy(self, instance):
        log_audit(
            self.request.user,
            'DELETE',
            self.audit_resource_type,
            instance.pk,
            old_values=str(instance),
            request=self.request,
        )
        instance.delete()
