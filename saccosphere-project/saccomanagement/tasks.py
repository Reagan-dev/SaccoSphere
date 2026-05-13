"""Background tasks for SACCO management workflows."""

from celery import shared_task
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
from saccomanagement.models import ImportJob


@shared_task(name='saccomanagement.tasks.run_member_import')
def run_member_import_task(import_job_id):
    """Parse, validate, and import one queued member import job."""
    import_job = ImportJob.objects.select_related(
        'sacco',
        'imported_by',
    ).get(id=import_job_id)
    import_job.status = ImportJob.Status.PROCESSING
    import_job.save(update_fields=['status'])

    try:
        import_job.file.open('rb')
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
    except (ImportParseError, ImportAbortError, Exception) as exc:
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
        raise
    finally:
        import_job.file.close()
