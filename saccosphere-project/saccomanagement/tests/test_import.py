"""Tests for member CSV/XLSX import endpoints."""

import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Sacco, User
from saccomanagement.import_utils import _process_import_job, parse_import_file
from saccomanagement.models import MemberImportJob, Role
from saccomembership.models import Membership


CSV_HEADER = (
    'first_name,last_name,email,phone_number,employment_status,'
    'monthly_income\n'
)


class ImportParsingTest(TestCase):
    """Validate import file parsing helpers."""

    def test_csv_parse_returns_rows(self):
        csv_content = (
            f'{CSV_HEADER}'
            'Jane,Doe,jane.doe@example.com,254712345678,Employed,50000\n'
        )
        upload = SimpleUploadedFile(
            'members.csv',
            csv_content.encode('utf-8'),
            content_type='text/csv',
        )

        rows, error = parse_import_file(upload)

        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['email'], 'jane.doe@example.com')

    def test_xlsx_parse_returns_rows(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([
            'first_name',
            'last_name',
            'email',
            'phone_number',
            'employment_status',
            'monthly_income',
        ])
        sheet.append([
            'John',
            'Smith',
            'john.smith@example.com',
            '254700000001',
            'Self employed',
            '45000',
        ])

        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        upload = SimpleUploadedFile(
            'members.xlsx',
            buffer.read(),
            content_type=(
                'application/vnd.openxmlformats-officedocument.'
                'spreadsheetml.sheet'
            ),
        )

        rows, error = parse_import_file(upload)

        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['first_name'], 'John')

    def test_invalid_file_format_returns_error(self):
        upload = SimpleUploadedFile(
            'members.pdf',
            b'%PDF-1.4 fake content',
            content_type='application/pdf',
        )

        rows, error = parse_import_file(upload)

        self.assertEqual(rows, [])
        self.assertIn('Only .csv and .xlsx', error)


class ImportJobTest(APITestCase):
    """Validate member import API workflow."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Import SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='import-admin@example.com',
            password='StrongPass123',
            first_name='Import',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def _post_csv(self, csv_body):
        upload = SimpleUploadedFile(
            'members.csv',
            (CSV_HEADER + csv_body).encode('utf-8'),
            content_type='text/csv',
        )
        return self.client.post(
            reverse('management:member-import'),
            {'file': upload},
            format='multipart',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

    @patch('saccomanagement.import_views.process_import_job.delay')
    def test_import_creates_members(self, delay_task):
        delay_task.return_value.id = 'task-123'

        response = self._post_csv(
            'Alice,Member,alice.import@example.com,254711111111,Employed,30000\n'
            'Bob,Member,bob.import@example.com,254722222222,Employed,35000\n',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['status'], 'queued')
        self.assertEqual(response.data['task_id'], 'task-123')
        job = MemberImportJob.objects.get(id=response.data['job_id'])
        self.assertEqual(job.status, MemberImportJob.Status.PENDING)

        _process_import_job(job.id, rows=delay_task.call_args.kwargs['rows'])
        job.refresh_from_db()
        self.assertEqual(job.status, MemberImportJob.Status.COMPLETED)
        self.assertEqual(job.success_rows, 2)
        self.assertEqual(
            Membership.objects.filter(
                sacco=self.sacco,
                user__email__in=[
                    'alice.import@example.com',
                    'bob.import@example.com',
                ],
            ).count(),
            2,
        )

    @patch('saccomanagement.import_views.process_import_job.delay')
    def test_import_errors_captured(self, delay_task):
        delay_task.return_value.id = 'task-456'

        response = self._post_csv(
            'Good,Member,good.import@example.com,254733333333,Employed,30000\n'
            ',Missing,invalid-row@example.com,254744444444,Employed,30000\n',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        job = MemberImportJob.objects.get(id=response.data['job_id'])
        _process_import_job(job.id, rows=delay_task.call_args.kwargs['rows'])
        job.refresh_from_db()
        self.assertEqual(job.success_rows, 1)
        self.assertEqual(job.error_rows, 1)
        self.assertEqual(len(job.errors), 1)
        self.assertEqual(job.errors[0]['row'], 2)

        status_response = self.client.get(
            reverse(
                'management:member-import-status',
                kwargs={'job_id': job.id},
            ),
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data['status'], 'completed')
        self.assertEqual(status_response.data['errors_summary']['count'], 1)

    def _run_import(self, csv_body):
        """POST a CSV import and synchronously process the resulting job."""
        with patch(
            'saccomanagement.import_views.process_import_job.delay',
        ) as delay_task:
            delay_task.return_value.id = 'task-test'
            response = self._post_csv(csv_body)
            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
            job = MemberImportJob.objects.get(id=response.data['job_id'])
            _process_import_job(
                job.id, rows=delay_task.call_args.kwargs['rows'],
            )

        job.refresh_from_db()
        return job

    def test_reimport_suspended_member_status_is_protected(self):
        user = User.objects.create_user(
            email='suspended.import@example.com',
            password='StrongPass123',
            first_name='Old',
            last_name='Name',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.SUSPENDED,
        )

        job = self._run_import(
            'Old,Name,suspended.import@example.com,254700000099,'
            'Employed,20000\n',
        )

        membership.refresh_from_db()
        self.assertEqual(membership.status, Membership.Status.SUSPENDED)
        self.assertEqual(job.status, MemberImportJob.Status.COMPLETED)
        self.assertEqual(job.success_rows, 1)
        self.assertEqual(job.protected_rows, 1)
        self.assertEqual(job.protected_details[0]['row'], 1)
        self.assertEqual(
            job.protected_details[0]['email'],
            'suspended.import@example.com',
        )

    def test_reimport_left_member_status_is_protected(self):
        user = User.objects.create_user(
            email='left.import@example.com',
            password='StrongPass123',
            first_name='Old',
            last_name='Name',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.LEFT,
        )

        job = self._run_import(
            'Old,Name,left.import@example.com,254700000098,'
            'Employed,20000\n',
        )

        membership.refresh_from_db()
        self.assertEqual(membership.status, Membership.Status.LEFT)
        self.assertEqual(job.protected_rows, 1)
        self.assertEqual(job.success_rows, 1)

    def test_reimport_pending_member_becomes_approved(self):
        """Regression: import must still approve non-protected members."""
        user = User.objects.create_user(
            email='pending.import@example.com',
            password='StrongPass123',
            first_name='Old',
            last_name='Name',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.PENDING,
        )

        job = self._run_import(
            'Old,Name,pending.import@example.com,254700000097,'
            'Employed,20000\n',
        )

        membership.refresh_from_db()
        self.assertEqual(membership.status, Membership.Status.APPROVED)
        self.assertEqual(job.protected_rows, 0)
        self.assertEqual(job.success_rows, 1)
        self.assertTrue(membership.member_number)

    def test_new_members_get_unique_member_numbers(self):
        csv_body = ''.join(
            f'First{i},Last{i},new{i}.import@example.com,25470000{i:04d},'
            f'Employed,30000\n'
            for i in range(50)
        )

        job = self._run_import(csv_body)

        self.assertEqual(job.success_rows, 50)
        member_numbers = list(
            Membership.objects.filter(
                sacco=self.sacco,
                user__email__startswith='new',
                user__email__endswith='.import@example.com',
            ).values_list('member_number', flat=True),
        )
        self.assertEqual(len(member_numbers), 50)
        self.assertTrue(all(member_numbers))
        self.assertEqual(len(set(member_numbers)), 50)

    def test_import_aborts_when_failure_rate_exceeds_threshold(self):
        # 18 valid + 2 invalid = 20 rows, clearing IMPORT_ABORT_MIN_ROWS,
        # at a 10% failure rate (> the 5% threshold).
        valid_rows = ''.join(
            f'Good{i},Member{i},good{i}.import@example.com,254711{i:04d},'
            f'Employed,30000\n'
            for i in range(18)
        )
        invalid_rows = (
            'Bad,RowOne,,254722220001,Employed,30000\n'
            'Bad,RowTwo,,254722220002,Employed,30000\n'
        )

        job = self._run_import(valid_rows + invalid_rows)

        self.assertEqual(job.status, MemberImportJob.Status.FAILED)
        self.assertEqual(job.processed_rows, 0)
        self.assertEqual(job.success_rows, 0)
        self.assertIn('aborted', job.errors[0]['error'])
        self.assertEqual(
            Membership.objects.filter(
                sacco=self.sacco,
                user__email__startswith='good',
            ).count(),
            0,
        )
