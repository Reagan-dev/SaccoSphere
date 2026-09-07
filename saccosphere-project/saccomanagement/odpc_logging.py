import logging
import time

from django.db.utils import InterfaceError, OperationalError

from config.utils import emit_metric

from .models import DataConsentLog


logger = logging.getLogger('saccosphere.odpc')

MAX_WRITE_ATTEMPTS = 3
# Backoff between attempt 1->2 and 2->3, in seconds. Kept small since this
# runs synchronously inside a request/response cycle - a request should not
# hang waiting on audit-log retries.
RETRY_BACKOFF_SECONDS = (0.1, 0.3)

# Only database connectivity errors are worth retrying. IntegrityError and
# other data-shape errors are not transient - retrying them wastes the
# retry budget and delays surfacing the real cause, so they fail immediately.
TRANSIENT_DB_ERRORS = (OperationalError, InterfaceError)


class ConsentLogWriteError(Exception):
    """
    Raised when a DataConsentLog write could not be completed after retries.

    Distinguishes "the audit record failed to persist" from "logged
    successfully" - create_data_consent_log no longer returns None on
    failure, so callers must not treat catching this exception as silent
    success. The underlying failure is always logged and counted before this
    is raised, so a caller that catches it (because audit-logging failures
    must not block its primary operation) does not lose the failure signal.
    """

    def __init__(self, message, *, data_type=None, user_id=None):
        super().__init__(message)
        self.data_type = data_type
        self.user_id = user_id


def create_data_consent_log(
    user,
    accessed_by,
    data_type,
    reason,
    request=None,
):
    """
    Log access to member personal data by an admin for ODPC compliance.

    Retries up to MAX_WRITE_ATTEMPTS times on transient database errors
    (connection issues), with a short backoff between attempts. On a
    non-transient error, or once retries are exhausted, raises
    ConsentLogWriteError rather than returning None - a silent None return
    on failure made a lost audit record indistinguishable from a
    successfully written one. Callers that must not let an audit-logging
    failure block their primary operation should catch ConsentLogWriteError
    explicitly and decide how to proceed.
    """
    user_id = getattr(user, 'id', None)
    user_ref = f'{user.id}:{user.email}' if user else 'unknown'
    accessed_by_ref = (
        f'{accessed_by.id}:{accessed_by.email}' if accessed_by else 'unknown'
    )

    last_exception = None
    attempt = 0
    while attempt < MAX_WRITE_ATTEMPTS:
        attempt += 1
        try:
            log = DataConsentLog.objects.create(
                user=user,
                accessed_by=accessed_by,
                user_reference=user_ref,
                accessed_by_reference=accessed_by_ref,
                data_type=data_type,
                reason=reason,
            )
        except TRANSIENT_DB_ERRORS as exc:
            last_exception = exc
            logger.warning(
                'Transient failure writing data consent log '
                '(attempt=%s/%s, user_id=%s, data_type=%s): %s',
                attempt,
                MAX_WRITE_ATTEMPTS,
                user_id,
                data_type,
                exc,
                exc_info=True,
            )
            if attempt < MAX_WRITE_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
            continue
        except Exception as exc:
            # Not a transient DB error - retrying will not help.
            last_exception = exc
            logger.exception(
                'Non-transient failure writing data consent log '
                '(user_id=%s, data_type=%s).',
                user_id,
                data_type,
            )
            break
        else:
            emit_metric(
                'consent_audit_log_write_success',
                data_type=data_type,
                user_id=user_id,
                attempts=attempt,
            )
            return log

    emit_metric(
        'consent_audit_log_write_failure',
        data_type=data_type,
        user_id=user_id,
        attempts=attempt,
    )
    logger.error(
        'CONSENT_AUDIT_LOG_WRITE_FAILED: could not persist data consent '
        'log after %s attempt(s) (user_id=%s, accessed_by_id=%s, '
        'data_type=%s).',
        attempt,
        user_id,
        getattr(accessed_by, 'id', None),
        data_type,
    )
    raise ConsentLogWriteError(
        f'Failed to write data consent log for user_id={user_id} '
        f'after {attempt} attempt(s).',
        data_type=data_type,
        user_id=user_id,
    ) from last_exception


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

        try:
            create_data_consent_log(
                member_user,
                request.user,
                self.data_access_type,
                self.data_access_reason,
                request,
            )
        except ConsentLogWriteError:
            # Already logged and counted inside create_data_consent_log.
            # An audit-log write failure must not block the admin from
            # seeing the member data they are otherwise authorized to view.
            pass

    def _get_member_user(self, obj):
        if hasattr(obj, 'user'):
            return obj.user
        if hasattr(obj, 'membership') and hasattr(obj.membership, 'user'):
            return obj.membership.user
        if hasattr(obj, 'loan') and hasattr(obj.loan, 'membership'):
            return obj.loan.membership.user
        return None
