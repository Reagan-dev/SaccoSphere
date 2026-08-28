"""Unit tests for KYC serializers."""

from io import BytesIO
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import serializers

from accounts.serializers import KYCUploadSerializer, AdminKYCReviewSerializer
from accounts.models import KYCVerification


class KYCUploadSerializerTestCase(TestCase):
    """Test KYC upload serializer validation."""

    def _create_jpeg_image(self, width=600, height=400, color='white'):
        """Create a valid JPEG image for testing."""
        image = Image.new('RGB', (width, height), color=color)
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        return SimpleUploadedFile('test_image.jpg', image_bytes.read(), content_type='image/jpeg')

    def _create_png_image(self, width=600, height=400, color='white'):
        """Create a valid PNG image for testing."""
        image = Image.new('RGB', (width, height), color=color)
        image_bytes = BytesIO()
        image.save(image_bytes, format='PNG')
        image_bytes.seek(0)
        return SimpleUploadedFile('test_image.png', image_bytes.read(), content_type='image/png')

    def _create_pdf_file(self):
        """Create a minimal PDF file for testing."""
        # Minimal PDF header
        pdf_bytes = b'%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n%%EOF'
        return SimpleUploadedFile('test.pdf', pdf_bytes, content_type='application/pdf')

    def _create_oversized_image(self):
        """Create an image larger than the size limit."""
        # Create a file that exceeds 5MB by using raw bytes
        large_bytes = b'x' * (6 * 1024 * 1024)  # 6MB
        return SimpleUploadedFile('large.jpg', large_bytes, content_type='image/jpeg')

    def _create_wrong_extension_file(self):
        """Create a JPEG file with .png extension."""
        image = Image.new('RGB', (600, 400), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        return SimpleUploadedFile('wrong.png', image_bytes.read(), content_type='image/png')

    def test_valid_jpeg_upload(self):
        """Test that a valid JPEG upload passes validation."""
        file = self._create_jpeg_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': file,
        })
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['document_type'], 'id_front')

    def test_valid_png_upload(self):
        """Test that a valid PNG upload passes validation."""
        file = self._create_png_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_back',
            'file': file,
        })
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['document_type'], 'id_back')

    def test_valid_pdf_upload(self):
        """Test that a valid PDF upload passes validation."""
        file = self._create_pdf_file()
        serializer = KYCUploadSerializer(data={
            'document_type': 'passport',
            'file': file,
        })
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['document_type'], 'passport')

    def test_oversized_file_rejected(self):
        """Test that files exceeding size limit are rejected."""
        file = self._create_oversized_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('size', str(serializer.errors['file']).lower())

    def test_wrong_extension_rejected(self):
        """Test that files with mismatched extensions are rejected."""
        file = self._create_wrong_extension_file()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)

    def test_missing_document_type(self):
        """Test that missing document_type field is rejected."""
        file = self._create_jpeg_image()
        serializer = KYCUploadSerializer(data={
            'file': file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('document_type', serializer.errors)

    def test_missing_file(self):
        """Test that missing file field is rejected."""
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)

    def test_invalid_document_type(self):
        """Test that invalid document_type choice is rejected."""
        file = self._create_jpeg_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'invalid_type',
            'file': file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('document_type', serializer.errors)

    def test_image_too_small(self):
        """Test that images below minimum dimensions are rejected."""
        image = Image.new('RGB', (300, 200), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        file = SimpleUploadedFile('small.jpg', image_bytes.read(), content_type='image/jpeg')

        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('dimension', str(serializer.errors['file']).lower())

    def test_image_too_large(self):
        """Test that images above maximum dimensions are rejected."""
        image = Image.new('RGB', (12000, 100), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        file = SimpleUploadedFile('large_dim.jpg', image_bytes.read(), content_type='image/jpeg')

        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('dimension', str(serializer.errors['file']).lower())

    def test_all_document_types_valid(self):
        """Test that all valid document types are accepted."""
        valid_types = ['id_front', 'id_back', 'passport', 'huduma']
        for doc_type in valid_types:
            file = self._create_jpeg_image()
            serializer = KYCUploadSerializer(data={
                'document_type': doc_type,
                'file': file,
            })
            self.assertTrue(serializer.is_valid(), f"Failed for document_type: {doc_type}")


class AdminKYCReviewSerializerTestCase(TestCase):
    """Test admin KYC review serializer validation."""

    def test_valid_approval(self):
        """Test that a valid approval passes validation."""
        serializer = AdminKYCReviewSerializer(data={
            'status': KYCVerification.Status.APPROVED,
        })
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['status'], KYCVerification.Status.APPROVED)

    def test_valid_rejection_with_reason(self):
        """Test that a valid rejection with reason passes validation."""
        serializer = AdminKYCReviewSerializer(data={
            'status': KYCVerification.Status.REJECTED,
            'rejection_reason': 'Document unclear',
        })
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['status'], KYCVerification.Status.REJECTED)
        self.assertEqual(serializer.validated_data['rejection_reason'], 'Document unclear')

    def test_rejection_without_reason_fails(self):
        """Test that rejection without reason fails validation."""
        serializer = AdminKYCReviewSerializer(data={
            'status': KYCVerification.Status.REJECTED,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('rejection_reason', serializer.errors)

    def test_invalid_status(self):
        """Test that invalid status choice is rejected."""
        serializer = AdminKYCReviewSerializer(data={
            'status': 'INVALID_STATUS',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('status', serializer.errors)

    def test_manual_verification_reason_too_short(self):
        """Test that manual verification reason below minimum length fails."""
        serializer = AdminKYCReviewSerializer(data={
            'status': KYCVerification.Status.APPROVED,
            'manual_verification_reason': 'short',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('manual_verification_reason', serializer.errors)

    def test_manual_verification_reason_valid(self):
        """Test that manual verification reason with valid length passes."""
        serializer = AdminKYCReviewSerializer(data={
            'status': KYCVerification.Status.APPROVED,
            'manual_verification_reason': 'Verified manually with physical document',
        })
        self.assertTrue(serializer.is_valid())
