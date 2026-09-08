"""Utilities for SACCO member CSV/Excel import jobs."""

import csv
import io
from decimal import Decimal, InvalidOperation

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import User
from saccomembership.models import Membership
from saccomembership.services import generate_member_number

from .audit_logger import log_audit
from .models import MemberImportJob


MAX_IMPORT_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.csv', '.xlsx'}
REQUIRED_FIELDS = ('first_name', 'last_name', 'email')
OPTIONAL_FIELDS = ('phone_number', 'employment_status', 'monthly_income')
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
HEADER_ALIASES = {
    'phone': 'phone_number',
    'phone number': 'phone_number',
    'employment status': 'employment_status',
    'monthly income': 'monthly_income',
}

# Existing membership statuses that represent a deliberate, out-of-band
# decision (a suspension, a member leaving, a still-pending review, or a
# rejection) that a bulk re-import must never silently reverse back to
# APPROVED.
STATUS_PROTECTED_STATUSES = (
    Membership.Status.SUSPENDED,
    Membership.Status.LEFT,
    Membership.Status.UNDER_REVIEW,
    Membership.Status.REJECTED,
)

ROW_RESULT_CREATED = 'created'
ROW_RESULT_UPDATED = 'updated'
ROW_RESULT_STATUS_PROTECTED = 'status_protected'

# Ported from the retired saccomanagement.data_imports pipeline: a CSV with
# more than this fraction of rows failing basic validation is rejected
# outright rather than partially imported, so one malformed export can't
# quietly corrupt/reactivate a large slice of a SACCO's membership.
IMPORT_ABORT_FAILURE_RATE = 0.05

# TODO(product): confirm the minimum batch size the abort threshold should
# apply to. Below this row count a percentage is noisy (one bad row in a
# 2-row file is a 50% "failure rate" but isn't the mass-corruption scenario
# this circuit breaker exists for), so small batches fall back to per-row
# error reporting instead of an outright abort.
IMPORT_ABORT_MIN_ROWS = 20


def parse_import_file(uploaded_file):
    """
    Parse a CSV or XLSX member import file.

    Returns:
        tuple[list[dict], str | None]: Parsed rows and optional error message.
    """
    filename = (getattr(uploaded_file, 'name', '') or '').lower()
    extension = _file_extension(filename)

    if extension not in ALLOWED_EXTENSIONS:
        return [], 'Only .csv and .xlsx files are supported.'

    if getattr(uploaded_file, 'size', 0) > MAX_IMPORT_FILE_SIZE:
        return [], 'Import file must be smaller than 5MB.'

    try:
        if extension == '.csv':
            rows = _parse_csv(uploaded_file)
        else:
            rows = _parse_xlsx(uploaded_file)
    except Exception as exc:
        return [], f'Unable to read import file: {exc}'

    if not rows:
        return [], 'Import file contains no data rows.'

    return rows, None


@shared_task(
    bind=True,
    max_retries=3,
    name='saccomanagement.import_utils.process_import_job',
)
def process_import_job(self, job_id, rows=None):
    """
    Process all rows for one MemberImportJob in a Celery worker.
    """
    try:
        job = _process_import_job(job_id, rows=rows)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

        _mark_import_job_failed(job_id, exc)
        raise

    return {'job_id': str(job.id), 'status': job.status}


def _process_import_job(job_id, rows=None):
    """Process all rows for one MemberImportJob."""
    job = MemberImportJob.objects.select_related('sacco', 'created_by').get(
        id=job_id,
    )
    job.status = MemberImportJob.Status.PROCESSING
    job.started_at = timezone.now()
    job.save(update_fields=['status', 'started_at'])

    if rows is None:
        return job

    total_rows = len(rows)
    job.total_rows = total_rows

    failure_count = _count_validation_failures(rows) if total_rows else 0
    should_check_abort = total_rows >= IMPORT_ABORT_MIN_ROWS
    if should_check_abort and (
        failure_count / total_rows
    ) > IMPORT_ABORT_FAILURE_RATE:
        return _abort_import_job(job, total_rows, failure_count)

    job.processed_rows = 0
    job.success_rows = 0
    job.error_rows = 0
    job.protected_rows = 0
    job.errors = []
    job.protected_details = []
    job.save(
        update_fields=[
            'total_rows',
            'processed_rows',
            'success_rows',
            'error_rows',
            'protected_rows',
            'errors',
            'protected_details',
        ],
    )

    created_count = 0
    updated_count = 0
    for row_index, row in enumerate(rows, start=1):
        try:
            result = _import_member_row(row, job)
            job.success_rows += 1
            if result == ROW_RESULT_CREATED:
                created_count += 1
            elif result == ROW_RESULT_UPDATED:
                updated_count += 1
            elif result == ROW_RESULT_STATUS_PROTECTED:
                job.protected_rows += 1
                job.protected_details.append(
                    {
                        'row': row_index,
                        'email': row.get('email'),
                        'reason': (
                            'Existing membership status was SUSPENDED, '
                            'LEFT, UNDER_REVIEW, or REJECTED; status left '
                            'unchanged by this import.'
                        ),
                    },
                )
        except Exception as exc:
            job.error_rows += 1
            job.errors.append(
                {
                    'row': row_index,
                    'field': _error_field_for_exception(exc, row),
                    'error': str(exc),
                },
            )
        job.processed_rows += 1

    if job.success_rows == 0 and job.error_rows > 0:
        job.status = MemberImportJob.Status.FAILED
    else:
        job.status = MemberImportJob.Status.COMPLETED

    job.completed_at = timezone.now()
    job.save(
        update_fields=[
            'status',
            'processed_rows',
            'success_rows',
            'error_rows',
            'protected_rows',
            'errors',
            'protected_details',
            'completed_at',
        ],
    )
    log_audit(
        job.created_by,
        'MEMBER_IMPORT_RUN',
        'MemberImportJob',
        job.id,
        new_values={
            'sacco_id': str(job.sacco_id),
            'status': job.status,
            'total_rows': job.total_rows,
            'created': created_count,
            'updated': updated_count,
            'status_protected': job.protected_rows,
            'failed': job.error_rows,
        },
    )
    return job


def _count_validation_failures(rows):
    """Count rows that would fail per-row validation, without writing to the DB."""
    failures = 0
    for row in rows:
        try:
            _validate_row(row)
        except ValueError:
            failures += 1
    return failures


def _abort_import_job(job, total_rows, failure_count):
    """
    Mark the whole job FAILED without processing or committing any row.

    Used when more than IMPORT_ABORT_FAILURE_RATE of rows fail basic
    validation - the entire file is rejected rather than partially imported.
    """
    failure_rate = failure_count / total_rows
    job.status = MemberImportJob.Status.FAILED
    job.processed_rows = 0
    job.success_rows = 0
    job.error_rows = failure_count
    job.protected_rows = 0
    job.errors = [
        {
            'error': (
                f'Import aborted: {failure_count} of {total_rows} rows '
                f'({failure_rate:.1%}) failed validation, exceeding the '
                f'{IMPORT_ABORT_FAILURE_RATE:.0%} abort threshold. No rows '
                'were imported.'
            ),
        },
    ]
    job.protected_details = []
    job.completed_at = timezone.now()
    job.save(
        update_fields=[
            'status',
            'total_rows',
            'processed_rows',
            'success_rows',
            'error_rows',
            'protected_rows',
            'errors',
            'protected_details',
            'completed_at',
        ],
    )
    log_audit(
        job.created_by,
        'MEMBER_IMPORT_RUN',
        'MemberImportJob',
        job.id,
        new_values={
            'sacco_id': str(job.sacco_id),
            'status': job.status,
            'total_rows': total_rows,
            'created': 0,
            'updated': 0,
            'status_protected': 0,
            'failed': failure_count,
            'aborted': True,
        },
    )
    return job


def _mark_import_job_failed(job_id, exc):
    MemberImportJob.objects.filter(id=job_id).update(
        status=MemberImportJob.Status.FAILED,
        completed_at=timezone.now(),
        errors=[{'error': str(exc)}],
    )


def _file_extension(filename):
    if filename.endswith('.xlsx'):
        return '.xlsx'
    if filename.endswith('.csv'):
        return '.csv'
    return ''


def _normalize_header(header):
    normalized = str(header or '').strip().lower()
    return HEADER_ALIASES.get(normalized, normalized.replace(' ', '_'))


def _normalize_row(raw_row):
    normalized = {}
    for key, value in raw_row.items():
        field_name = _normalize_header(key)
        if field_name not in ALL_FIELDS:
            continue
        if value is None:
            normalized[field_name] = None
            continue
        normalized[field_name] = str(value).strip()
    return normalized


def _parse_csv(uploaded_file):
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(0)

    content = uploaded_file.read()
    if isinstance(content, bytes):
        text_stream = io.StringIO(content.decode('utf-8-sig'))
    else:
        text_stream = io.StringIO(content)

    reader = csv.DictReader(text_stream)
    return [_normalize_row(row) for row in reader if any(row.values())]


def _parse_xlsx(uploaded_file):
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(0)

    workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    headers = next(rows_iter, None)
    if not headers:
        workbook.close()
        return []

    field_names = [_normalize_header(header) for header in headers]
    parsed_rows = []
    for values in rows_iter:
        if not any(values):
            continue
        raw_row = dict(zip(field_names, values))
        parsed_rows.append(_normalize_row(raw_row))

    workbook.close()
    return parsed_rows


def _validate_row(row):
    missing = [
        field for field in REQUIRED_FIELDS
        if not row.get(field)
    ]
    if missing:
        raise ValueError(
            f'Missing required fields: {", ".join(missing)}.',
        )

    monthly_income = row.get('monthly_income')
    if monthly_income:
        try:
            Decimal(str(monthly_income))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError('monthly_income must be a valid number.') from exc


@transaction.atomic
def _import_member_row(row, job):
    """
    Create or update the Membership for one import row.

    Returns one of ROW_RESULT_CREATED, ROW_RESULT_UPDATED, or
    ROW_RESULT_STATUS_PROTECTED, so the caller can report the outcome.
    """
    _validate_row(row)

    user, created = User.objects.get_or_create(
        email=row['email'].lower(),
        defaults={
            'first_name': row['first_name'],
            'last_name': row['last_name'],
            'phone_number': row.get('phone_number') or None,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])

    notes = None
    if row.get('employment_status'):
        notes = f'Employment status: {row["employment_status"]}'

    # Locked so a concurrent import/approval for the same member can't read
    # a stale status between this check and the write below.
    existing_membership = Membership.objects.select_for_update().filter(
        user=user,
        sacco=job.sacco,
    ).first()

    if existing_membership is None:
        membership = Membership.objects.create(
            user=user,
            sacco=job.sacco,
            status=Membership.Status.APPROVED,
            notes=notes,
        )
        membership.member_number = generate_member_number(membership.sacco)
        membership.save(update_fields=['member_number'])
        return ROW_RESULT_CREATED

    if existing_membership.status in STATUS_PROTECTED_STATUSES:
        if notes is not None:
            existing_membership.notes = notes
            existing_membership.save(update_fields=['notes'])
        return ROW_RESULT_STATUS_PROTECTED

    update_fields = ['status']
    existing_membership.status = Membership.Status.APPROVED
    if notes is not None:
        existing_membership.notes = notes
        update_fields.append('notes')
    existing_membership.save(update_fields=update_fields)

    if not existing_membership.member_number:
        existing_membership.member_number = generate_member_number(
            existing_membership.sacco,
        )
        existing_membership.save(update_fields=['member_number'])

    return ROW_RESULT_UPDATED


def _error_field_for_exception(exc, row):
    message = str(exc).lower()
    for field in REQUIRED_FIELDS + ('monthly_income',):
        if field in message:
            return field
    if 'email' in message:
        return 'email'
    return next(
        (field for field in REQUIRED_FIELDS if not row.get(field)),
        'row',
    )
