from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomanagement.models import Role

from .models import (
    MemberFieldData,
    Membership,
    MembershipDocument,
    SaccoApplication,
    SaccoFieldDefinition,
)


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


class CustomFieldTypeValidationTests(TestCase):
    """Type-aware validation of custom field values on application submit."""

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Custom Field SACCO',
            registration_number='CF001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.number_field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Age',
            field_type=SaccoFieldDefinition.FieldType.NUMBER,
            is_required=False,
        )
        self.date_field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Date of Birth',
            field_type=SaccoFieldDefinition.FieldType.DATE,
            is_required=False,
        )
        self.boolean_field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Is Employed',
            field_type=SaccoFieldDefinition.FieldType.BOOLEAN,
            is_required=False,
        )
        self.select_field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='County of Residence',
            field_type=SaccoFieldDefinition.FieldType.SELECT,
            options=['Nairobi', 'Kisumu', 'Mombasa'],
            is_required=False,
        )
        self._applicant_counter = 0

    def _new_applicant(self):
        self._applicant_counter += 1
        return User.objects.create_user(
            email=f'cf-applicant-{self._applicant_counter}@example.com',
            password='StrongPass123',
        )

    def _apply(self, field, value):
        applicant = self._new_applicant()
        self.client.force_authenticate(user=applicant)
        response = self.client.post(
            reverse('saccomembership:membership-list'),
            {
                'sacco': str(self.sacco.id),
                'custom_fields': [
                    {'field_id': str(field.id), 'value': value},
                ],
            },
            format='json',
        )
        return applicant, response

    def test_number_field_rejects_non_numeric_value(self):
        _, response = self._apply(self.number_field, 'not-a-number')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_number_field_accepts_valid_number(self):
        applicant, response = self._apply(self.number_field, '29')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        membership = Membership.objects.get(
            user=applicant, sacco=self.sacco,
        )
        self.assertTrue(
            MemberFieldData.objects.filter(
                membership=membership, field=self.number_field, value='29',
            ).exists(),
        )

    def test_date_field_rejects_invalid_date(self):
        _, response = self._apply(self.date_field, '31/02/2020')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_date_field_accepts_valid_iso_date(self):
        _, response = self._apply(self.date_field, '1995-06-15')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_boolean_field_rejects_invalid_value(self):
        _, response = self._apply(self.boolean_field, 'maybe')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_boolean_field_accepts_normalized_true_false(self):
        _, response_1 = self._apply(self.boolean_field, 'true')
        _, response_2 = self._apply(self.boolean_field, 'No')

        self.assertEqual(response_1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response_2.status_code, status.HTTP_201_CREATED)

    def test_select_field_rejects_option_not_in_list(self):
        _, response = self._apply(self.select_field, 'Nakuru')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_select_field_accepts_declared_option(self):
        _, response = self._apply(self.select_field, 'Kisumu')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class SaccoFieldDefinitionAdminTests(TestCase):
    """SACCO_ADMIN-scoped CRUD for custom field definitions."""

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Field Admin SACCO',
            registration_number='FA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Other Field Admin SACCO',
            registration_number='FA002',
            sector=Sacco.Sector.FINANCE,
            county='Kisumu',
        )
        self.admin = User.objects.create_user(
            email='field-admin@example.com', password='StrongPass123',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.other_admin = User.objects.create_user(
            email='other-field-admin@example.com', password='StrongPass123',
        )
        Role.objects.create(
            user=self.other_admin,
            sacco=self.other_sacco,
            name=Role.SACCO_ADMIN,
        )
        self.member = User.objects.create_user(
            email='field-member@example.com', password='StrongPass123',
        )
        self.list_url = reverse('saccomembership:field-definition-list-create')

    def _detail_url(self, field):
        return reverse(
            'saccomembership:field-definition-detail',
            kwargs={'id': field.id},
        )

    def test_admin_can_create_field_definition(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            self.list_url,
            {
                'label': 'Employer Name',
                'field_type': SaccoFieldDefinition.FieldType.TEXT,
                'is_required': False,
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            SaccoFieldDefinition.objects.filter(
                sacco=self.sacco, label='Employer Name',
            ).exists(),
        )

    def test_admin_can_edit_field_definition(self):
        field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Old Label',
            field_type=SaccoFieldDefinition.FieldType.TEXT,
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self._detail_url(field),
            {'label': 'New Label'},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        field.refresh_from_db()
        self.assertEqual(field.label, 'New Label')

    def test_admin_can_delete_field_definition(self):
        field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Deletable',
            field_type=SaccoFieldDefinition.FieldType.TEXT,
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete(
            self._detail_url(field), HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            SaccoFieldDefinition.objects.filter(id=field.id).exists(),
        )

    def test_admin_of_different_sacco_cannot_access(self):
        field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Protected Field',
            field_type=SaccoFieldDefinition.FieldType.TEXT,
        )
        self.client.force_authenticate(user=self.other_admin)

        response = self.client.get(
            self._detail_url(field),
            HTTP_X_SACCO_ID=str(self.other_sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_admin_member_forbidden(self):
        self.client.force_authenticate(user=self.member)

        response = self.client.post(
            self.list_url,
            {
                'label': 'Should Not Work',
                'field_type': SaccoFieldDefinition.FieldType.TEXT,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_changing_field_type_with_existing_data_rejected(self):
        field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Has Data',
            field_type=SaccoFieldDefinition.FieldType.TEXT,
        )
        applicant = User.objects.create_user(
            email='has-data-applicant@example.com', password='StrongPass123',
        )
        membership = Membership.objects.create(
            user=applicant, sacco=self.sacco,
        )
        MemberFieldData.objects.create(
            membership=membership, field=field, value='some free text',
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self._detail_url(field),
            {'field_type': SaccoFieldDefinition.FieldType.NUMBER},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        field.refresh_from_db()
        self.assertEqual(field.field_type, SaccoFieldDefinition.FieldType.TEXT)

    def test_field_type_unchanged_edit_still_allowed_with_existing_data(self):
        """Editing other attributes of a field that already has data must
        still work - only a FieldType change is blocked."""
        field = SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Has Data Editable',
            field_type=SaccoFieldDefinition.FieldType.TEXT,
        )
        applicant = User.objects.create_user(
            email='has-data-editable@example.com', password='StrongPass123',
        )
        membership = Membership.objects.create(
            user=applicant, sacco=self.sacco,
        )
        MemberFieldData.objects.create(
            membership=membership, field=field, value='some free text',
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self._detail_url(field),
            {'label': 'Renamed Without Type Change'},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SaccoFieldsPublicViewRegressionTests(TestCase):
    """SaccoFieldsView must remain public and read-only, unchanged."""

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Public Fields SACCO',
            registration_number='PF001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        SaccoFieldDefinition.objects.create(
            sacco=self.sacco,
            label='Employer Name',
            field_type=SaccoFieldDefinition.FieldType.TEXT,
            display_order=1,
        )

    def test_unauthenticated_user_can_list_fields(self):
        response = self.client.get(
            reverse(
                'saccomembership:sacco-fields',
                kwargs={'sacco_id': self.sacco.id},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['data'][0]['label'], 'Employer Name')
