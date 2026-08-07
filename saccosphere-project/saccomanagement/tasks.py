"""Background tasks for SACCO management workflows."""

import logging

from celery import shared_task
from django.db import DatabaseError, InterfaceError, OperationalError
from django.utils import timezone

from saccomanagement.data_imports.bulk_operations import (
    ImportAbortError,
    import_members_to_sacco,
)
from saccomanagement.data_imports.parsers import (
    ImportParseError,
    parse_member_import_file,
)
from saccomanagement.data_imports.validators import validate_import_file
from saccomanagement.import_utils import process_import_job  # noqa: F401
from saccomanagement.models import ImportJob


logger = logging.getLogger('saccosphere.saccomanagement')

TRANSIENT_IMPORT_ERRORS = (DatabaseError, InterfaceError, OperationalError)
DATA_IMPORT_ERRORS = (ImportParseError, ImportAbortError)


@shared_task(
    bind=True,
    name='saccomanagement.tasks.run_member_import',
    max_retries=3,
    default_retry_delay=60,
)
def run_member_import_task(self, import_job_id):
    """Parse, validate, and import one queued member import job."""
    import_job = None
    file_opened = False

    try:
        import_job = ImportJob.objects.select_related(
            'sacco',
            'imported_by',
        ).get(id=import_job_id)
        import_job.status = ImportJob.Status.PROCESSING
        import_job.save(update_fields=['status'])

        import_job.file.open('rb')
        file_opened = True
        rows = parse_member_import_file(import_job.file)
        valid_rows, error_rows, summary = validate_import_file(rows)

        import_result = import_members_to_sacco(
            valid_rows=valid_rows,
            sacco=import_job.sacco,
            imported_by=import_job.imported_by,
        )
        combined_errors = error_rows + import_result['errors']

        import_job.total_rows = summary['total_rows']
        import_job.success_count = import_result['success_count']
        import_job.fail_count = (
            summary['error_rows'] + import_result['fail_count']
        )
        import_job.error_summary = combined_errors
        import_job.completed_at = timezone.now()
        if import_job.fail_count == 0:
            import_job.status = ImportJob.Status.COMPLETED
        elif import_job.success_count == 0:
            import_job.status = ImportJob.Status.FAILED
        else:
            import_job.status = ImportJob.Status.PARTIAL
        import_job.save(
            update_fields=[
                'total_rows',
                'success_count',
                'fail_count',
                'error_summary',
                'completed_at',
                'status',
            ],
        )
    except DATA_IMPORT_ERRORS as exc:
        _mark_import_job_failed(import_job, exc)
        logger.warning(
            'Member import job_id=%s failed with non-retryable data error.',
            import_job_id,
            exc_info=True,
        )
        raise
    except TRANSIENT_IMPORT_ERRORS as exc:
        if self.request.retries >= self.max_retries:
            _mark_import_job_failed(import_job, exc)
            logger.exception(
                'Member import job_id=%s exhausted transient retries.',
                import_job_id,
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Member import job_id=%s hit a transient DB/connection error. '
            'Retrying in %s seconds.',
            import_job_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)
    except ImportJob.DoesNotExist:
        logger.warning(
            'Member import job_id=%s does not exist.',
            import_job_id,
        )
        return False
    except Exception as exc:
        _mark_import_job_failed(import_job, exc)
        logger.exception(
            'Member import job_id=%s failed with non-retryable error.',
            import_job_id,
        )
        raise
    finally:
        if import_job is not None and file_opened:
            import_job.file.close()


def _mark_import_job_failed(import_job, exc):
    if import_job is None:
        return

    import_job.status = ImportJob.Status.FAILED
    import_job.completed_at = timezone.now()
    import_job.error_summary = [{'error': str(exc)}]
    import_job.save(
        update_fields=[
            'status',
            'completed_at',
            'error_summary',
        ],
    )
