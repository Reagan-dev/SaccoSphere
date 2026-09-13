import logging

from config.utils import emit_metric, get_client_ip

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

    A failure to write is not swallowed silently: it is logged at ERROR
    and counted via emit_metric so a lost audit record is observable
    (alertable) rather than indistinguishable from a successful write.
    Callers still get None back, since audit-logging failures must never
    block the primary operation they are attached to.
    """
    try:
        ip_address = None
        user_agent = None

        if request:
            ip_address = get_client_ip(request)
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
            'AUDIT_LOG_WRITE_FAILED: could not persist SystemAuditLog for '
            'resource_type=%s resource_id=%s action=%s.',
            resource_type,
            resource_id,
            action,
        )
        emit_metric(
            'system_audit_log_write_failure',
            resource_type=resource_type,
            action=action,
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
