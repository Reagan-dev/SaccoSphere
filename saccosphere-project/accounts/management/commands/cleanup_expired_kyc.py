"""Management command to clean up expired KYC documents."""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from accounts.models import KYCVerification
from saccomanagement.audit_logger import log_audit


logger = logging.getLogger('accounts.kyc_cleanup')


class Command(BaseCommand):
    help = (
        'Clean up KYC documents past their retention period. '
        'Deletes S3 objects, anonymizes PII fields, and preserves '
        'minimal verification trail for business records.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of records to process per batch.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        # Check if retention is configured
        retention_days = getattr(settings, 'KYC_RETENTION_DAYS', None)
        if not retention_days:
            self.stdout.write(
                self.style.WARNING(
                    'KYC_RETENTION_DAYS is not configured. '
                    'No cleanup will be performed.'
                )
            )
            return

        self.stdout.write(
            f'Finding KYC records past retention period ({retention_days} days)...'
        )

        # Find records past retention
        now = timezone.now()
        expired_records = KYCVerification.objects.filter(
            retention_until__isnull=False,
            retention_until__lt=now,
        ).select_related('user')

        total_count = expired_records.count()
        if total_count == 0:
            self.stdout.write(self.style.SUCCESS('No expired records found.'))
            return

        self.stdout.write(f'Found {total_count} expired records.')

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    'Dry run mode - no changes will be made.'
                )
            )
            for kyc in expired_records[:10]:  # Show first 10
                self.stdout.write(
                    f'  - {kyc.user.email} (retention_until: {kyc.retention_until})'
                )
            if total_count > 10:
                self.stdout.write(f'  ... and {total_count - 10} more')
            return

        # Process in batches
        processed = 0
        for kyc in expired_records.iterator(chunk_size=batch_size):
            try:
                self._cleanup_kyc_record(kyc)
                processed += 1
                if processed % 100 == 0:
                    self.stdout.write(f'Processed {processed}/{total_count}...')
            except Exception as e:
                logger.error(
                    f'Failed to cleanup KYC record {kyc.id}',
                    exc_info=True,
                    extra={'kyc_id': str(kyc.id)},
                )
                self.stdout.write(
                    self.style.ERROR(
                        f'Failed to cleanup {kyc.user.email}: {e}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Cleanup complete. Processed {processed}/{total_count} records.'
            )
        )

    def _cleanup_kyc_record(self, kyc):
        """
        Clean up a single KYC record.

        This method:
        1. Deletes S3 objects (id_front, id_back, passport, huduma)
        2. Anonymizes PII fields (id_number, normalized_id_number, huduma_namba)
        3. Preserves minimal trail (status, verified_at, submitted_at)
        4. Logs the cleanup event to audit trail
        """
        # Delete S3 objects
        self._delete_s3_objects(kyc)

        # Anonymize PII fields
        old_values = {
            'id_number': kyc.id_number,
            'normalized_id_number': kyc.normalized_id_number,
            'huduma_namba': kyc.huduma_namba,
            'id_front': bool(kyc.id_front),
            'id_back': bool(kyc.id_back),
            'passport': bool(kyc.passport),
            'huduma': bool(kyc.huduma),
        }

        kyc.id_number = None
        kyc.normalized_id_number = None
        kyc.huduma_namba = None

        # Clear document fields (this triggers S3 deletion if not already done)
        kyc.id_front = None
        kyc.id_back = None
        kyc.passport = None
        kyc.huduma = None

        # Mark as anonymized
        kyc.status = KYCVerification.Status.REJECTED  # Or add new status
        kyc.rejection_reason = 'Documents expired and were anonymized per retention policy.'

        # Save changes
        kyc.save()

        # Log to audit trail
        log_audit(
            user=None,  # System-initiated cleanup
            action='KYC_RETENTION_CLEANUP',
            resource_type='KYCDocument',
            resource_id=str(kyc.id),
            old_values=old_values,
            new_values={
                'status': kyc.status,
                'rejection_reason': kyc.rejection_reason,
            },
        )

        logger.info(
            'KYC record cleaned up',
            extra={
                'kyc_id': str(kyc.id),
                'user_email': kyc.user.email,
            },
        )

    def _delete_s3_objects(self, kyc):
        """Delete S3 objects for a KYC record."""
        document_fields = ['id_front', 'id_back', 'passport', 'huduma']

        for field_name in document_fields:
            document = getattr(kyc, field_name, None)
            if document and document.name:
                try:
                    if hasattr(document, 'delete'):
                        document.delete(save=False)
                    logger.info(
                        f'Deleted S3 object: {document.name}',
                        extra={'field': field_name, 'kyc_id': str(kyc.id)},
                    )
                except Exception as e:
                    logger.error(
                        f'Failed to delete S3 object: {document.name}',
                        exc_info=True,
                        extra={'field': field_name, 'kyc_id': str(kyc.id)},
                    )
                    raise
