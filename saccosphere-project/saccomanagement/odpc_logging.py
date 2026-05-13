import logging

from .models import DataConsentLog


logger = logging.getLogger('saccosphere.odpc')


def create_data_consent_log(
    user,
    accessed_by,
    data_type,
    reason,
    request=None,
):
    """
    Log access to member personal data by an admin for ODPC compliance.
    """
    try:
        return DataConsentLog.objects.create(
            user=user,
            accessed_by=accessed_by,
            data_type=data_type,
            reason=reason,
        )
    except Exception:
        logger.exception(
            'Failed to create data consent log for user_id=%s.',
            getattr(user, 'id', None),
        )
        return None


class DataAccessMixin:
    """Mixin for admin views that access member personal data."""

    data_access_type = ''
    data_access_reason = ''

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        if response.status_code == 200:
            self._log_object_access(self.get_object(), request)
        return response

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if response.status_code == 200:
            queryset = self.filter_queryset(self.get_queryset())
            page = self.paginate_queryset(queryset)
            objects = page if page is not None else queryset
            for obj in objects:
                self._log_object_access(obj, request)
        return response

    def _log_object_access(self, obj, request):
        member_user = self._get_member_user(obj)
        if member_user is None:
            return

        create_data_consent_log(
            member_user,
            request.user,
            self.data_access_type,
            self.data_access_reason,
            request,
        )

    def _get_member_user(self, obj):
        if hasattr(obj, 'user'):
            return obj.user
        if hasattr(obj, 'membership') and hasattr(obj.membership, 'user'):
            return obj.membership.user
        if hasattr(obj, 'loan') and hasattr(obj.loan, 'membership'):
            return obj.loan.membership.user
        return None
