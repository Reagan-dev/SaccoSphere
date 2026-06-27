from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomanagement.models import Role

from .models import MembershipDocument, SaccoApplication


class MembershipDocumentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Document SACCO',
            registration_number='DOC001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.user = User.objects.create_user(
            email='member-docs@example.com',
            password='StrongPass123',
        )
        self.admin = User.objects.create_user(
            email='doc-admin@example.com',
            password='StrongPass123',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.application = SaccoApplication.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=SaccoApplication.Status.DRAFT,
        )

    def document_url(self, application=None):
        application = application or self.application
        return (
            f'/api/v1/members/applications/{application.id}/documents/'
        )

    def delete_url(self, document):
        return (
            f'/api/v1/members/applications/{self.application.id}/'
            f'documents/{document.id}/'
        )

    def upload_file(self, name='payslip.pdf', content=b'test file'):
        return SimpleUploadedFile(
            name,
            content,
            content_type='application/pdf',
        )

    def test_member_can_upload_document_for_own_application(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.document_url(),
            {
                'document_type': (
                    MembershipDocument.DocumentType.LATEST_PAYSLIP
                ),
                'file': self.upload_file(),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        document = MembershipDocument.objects.get(
            application=self.application,
        )
        self.assertEqual(document.file_name, 'payslip.pdf')
        self.assertEqual(document.file_size_bytes, len(b'test file'))
        self.assertEqual(
            document.document_type,
            MembershipDocument.DocumentType.LATEST_PAYSLIP,
        )

    def test_member_cannot_upload_invalid_file_type(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.document_url(),
            {
                'document_type': MembershipDocument.DocumentType.OTHER,
                'file': self.upload_file(name='document.exe'),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_can_delete_document_only_in_draft(self):
        document = MembershipDocument.objects.create(
            application=self.application,
            document_type=MembershipDocument.DocumentType.OTHER,
            file=self.upload_file(),
            file_name='payslip.pdf',
            file_size_bytes=len(b'test file'),
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(self.delete_url(document))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            MembershipDocument.objects.filter(id=document.id).exists()
        )

    def test_member_cannot_delete_document_after_draft(self):
        self.application.status = SaccoApplication.Status.SUBMITTED
        self.application.save(update_fields=['status', 'updated_at'])
        document = MembershipDocument.objects.create(
            application=self.application,
            document_type=MembershipDocument.DocumentType.OTHER,
            file=self.upload_file(),
            file_name='payslip.pdf',
            file_size_bytes=len(b'test file'),
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(self.delete_url(document))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            MembershipDocument.objects.filter(id=document.id).exists()
        )

    def test_sacco_admin_can_list_application_documents(self):
        MembershipDocument.objects.create(
            application=self.application,
            document_type=MembershipDocument.DocumentType.OTHER,
            file=self.upload_file(),
            file_name='payslip.pdf',
            file_size_bytes=len(b'test file'),
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(
            self.document_url(),
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['results']), 1)

    def test_admin_review_get_includes_membership_documents(self):
        MembershipDocument.objects.create(
            application=self.application,
            document_type=MembershipDocument.DocumentType.OTHER,
            file=self.upload_file(),
            file_name='payslip.pdf',
            file_size_bytes=len(b'test file'),
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(
            f'/api/v1/management/applications/'
            f'{self.application.id}/review/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['membership_documents']), 1)
