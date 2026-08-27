"""Tests for KYC upload security validation including magic byte detection, EXIF stripping, and decompression bomb protection."""

from io import BytesIO
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image
from rest_framework import serializers

from accounts.serializers import KYCUploadSerializer


class KYCUploadSecurityTestCase(TestCase):
    """Test comprehensive security validation for KYC document uploads."""

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

    def _create_executable_with_jpg_extension(self):
        """Create a malicious file renamed to .jpg."""
        # Create a simple executable-like file with magic bytes
        # ELF magic bytes for Linux executable
        elf_magic = b'\x7fELF'
        file_bytes = elf_magic + b'\x00' * 100
        return SimpleUploadedFile('malicious.jpg', file_bytes, content_type='image/jpeg')

    def _create_renamed_executable(self):
        """Create a renamed executable/script file."""
        # PE magic bytes for Windows executable
        pe_magic = b'MZ'
        file_bytes = pe_magic + b'\x00' * 100
        return SimpleUploadedFile('photo.jpg', file_bytes, content_type='image/jpeg')

    def _create_zero_byte_file(self):
        """Create a zero-byte file."""
        return SimpleUploadedFile('empty.jpg', b'', content_type='image/jpeg')

    def _create_truncated_jpeg(self):
        """Create a truncated/corrupted JPEG file."""
        # JPEG magic bytes but truncated
        jpeg_magic = b'\xff\xd8\xff\xe0'
        file_bytes = jpeg_magic + b'\x00' * 10  # Too short to be valid
        return SimpleUploadedFile('truncated.jpg', file_bytes, content_type='image/jpeg')

    def _create_jpeg_with_png_extension(self):
        """Create a real JPEG file with .png extension."""
        image = Image.new('RGB', (600, 400), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        return SimpleUploadedFile('real_jpeg_but_wrong_extension.png', image_bytes.read(), content_type='image/png')

    def _create_png_with_jpg_extension(self):
        """Create a real PNG file with .jpg extension."""
        image = Image.new('RGB', (600, 400), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='PNG')
        image_bytes.seek(0)
        return SimpleUploadedFile('real_png_but_wrong_extension.jpg', image_bytes.read(), content_type='image/jpeg')

    def _create_heic_file(self):
        """Create a HEIC file (simulated with HEIC magic bytes)."""
        # HEIC magic bytes (ftypheic)
        heic_magic = b'\x00\x00\x00\x20\x66\x74\x79\x70\x68\x65\x69\x63'
        file_bytes = heic_magic + b'\x00' * 100
        return SimpleUploadedFile('iphone_photo.heic', file_bytes, content_type='image/heic')

    def _create_large_dimension_image(self):
        """Create an image with dimensions exceeding the limit."""
        # Create an image header that claims to be very large
        # This simulates a decompression bomb
        # Use dimensions that exceed our limit but won't trigger Pillow's built-in check
        image = Image.new('RGB', (12000, 12000), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG', quality=95)  # High quality to keep size reasonable
        image_bytes.seek(0)
        return SimpleUploadedFile('large_image.jpg', image_bytes.read(), content_type='image/jpeg')

    def _create_small_dimension_image(self):
        """Create an image with dimensions below the minimum."""
        image = Image.new('RGB', (300, 200), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        return SimpleUploadedFile('small_image.jpg', image_bytes.read(), content_type='image/jpeg')

    def test_valid_jpeg_accepted(self):
        """Test that a valid JPEG image is accepted."""
        image = self._create_jpeg_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': image,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_valid_png_accepted(self):
        """Test that a valid PNG image is accepted."""
        image = self._create_png_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': image,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_renamed_executable_rejected(self):
        """Test that a renamed executable is rejected by magic byte detection."""
        malicious_file = self._create_renamed_executable()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': malicious_file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('Unsupported file type', str(serializer.errors['file']))

    def test_executable_with_jpg_extension_rejected(self):
        """Test that an executable with .jpg extension is rejected."""
        malicious_file = self._create_executable_with_jpg_extension()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': malicious_file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('Unsupported file type', str(serializer.errors['file']))

    def test_jpeg_with_png_extension_rejected(self):
        """Test that a real JPEG with .png extension is rejected (mismatch decision)."""
        # Decision: Reject mismatched extensions to prevent confusion
        mismatched_file = self._create_jpeg_with_png_extension()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': mismatched_file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('Unsupported file type', str(serializer.errors['file']))

    def test_png_with_jpg_extension_rejected(self):
        """Test that a real PNG with .jpg extension is rejected (mismatch decision)."""
        mismatched_file = self._create_png_with_jpg_extension()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': mismatched_file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('Unsupported file type', str(serializer.errors['file']))

    def test_zero_byte_file_rejected(self):
        """Test that a zero-byte file is rejected."""
        empty_file = self._create_zero_byte_file()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': empty_file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        # Zero-byte files are caught by DRF's empty file check
        self.assertIn('empty', str(serializer.errors['file']).lower())

    def test_truncated_jpeg_rejected(self):
        """Test that a truncated/corrupted JPEG is rejected."""
        truncated_file = self._create_truncated_jpeg()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': truncated_file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)

    def test_heic_file_rejected(self):
        """Test that HEIC files are rejected (pillow-heif not installed)."""
        heic_file = self._create_heic_file()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': heic_file,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('Unsupported file type', str(serializer.errors['file']))

    def test_large_dimension_image_rejected(self):
        """Test that images exceeding max dimensions are rejected (decompression bomb protection)."""
        large_image = self._create_large_dimension_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': large_image,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('exceed maximum', str(serializer.errors['file']))

    def test_small_dimension_image_rejected(self):
        """Test that images below minimum dimensions are rejected."""
        small_image = self._create_small_dimension_image()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': small_image,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)
        self.assertIn('at least 400x300', str(serializer.errors['file']))

    def test_exif_metadata_stripped(self):
        """Test that EXIF metadata is stripped from images."""
        from PIL import Image as PILImage
        from PIL.ExifTags import TAGS

        # Create an image with EXIF data
        image = PILImage.new('RGB', (600, 400), color='white')
        
        # Add some EXIF data
        exif = PILImage.Exif()
        exif[0x0112] = 1  # Orientation
        exif[0x010F] = 'Manufacturer'  # Make
        exif[0x0110] = 'Camera Model'  # Model
        
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG', exif=exif)
        image_bytes.seek(0)

        uploaded_file = SimpleUploadedFile('image_with_exif.jpg', image_bytes.read(), content_type='image/jpeg')

        # Validate through serializer
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': uploaded_file,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

        # Get the cleaned file
        cleaned_file = serializer.validated_data['file']
        cleaned_file.seek(0)

        # Verify EXIF is stripped
        with PILImage.open(cleaned_file) as img:
            exif_data = img.getexif()
            # EXIF should be empty or minimal
            self.assertEqual(len(exif_data), 0, 'EXIF metadata should be stripped')

    def test_rgba_image_converted_to_rgb(self):
        """Test that RGBA images are converted to RGB."""
        image = Image.new('RGBA', (600, 400), color=(255, 255, 255, 255))
        image_bytes = BytesIO()
        image.save(image_bytes, format='PNG')
        image_bytes.seek(0)

        uploaded_file = SimpleUploadedFile('rgba_image.png', image_bytes.read(), content_type='image/png')

        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': uploaded_file,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

        # Verify the cleaned file is RGB
        cleaned_file = serializer.validated_data['file']
        cleaned_file.seek(0)
        with Image.open(cleaned_file) as img:
            self.assertEqual(img.mode, 'RGB')

    def test_palette_image_converted_to_rgb(self):
        """Test that palette images are converted to RGB."""
        image = Image.new('P', (600, 400))
        image_bytes = BytesIO()
        image.save(image_bytes, format='PNG')
        image_bytes.seek(0)

        uploaded_file = SimpleUploadedFile('palette_image.png', image_bytes.read(), content_type='image/png')

        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': uploaded_file,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

        # Verify the cleaned file is RGB
        cleaned_file = serializer.validated_data['file']
        cleaned_file.seek(0)
        with Image.open(cleaned_file) as img:
            self.assertEqual(img.mode, 'RGB')

    def test_size_limit_check_runs_first(self):
        """Test that size limit check runs before expensive magic byte detection."""
        # Create a large file that would fail size check
        large_file_bytes = b'\x00' * 10 * 1024 * 1024  # 10MB
        large_file = SimpleUploadedFile('large.jpg', large_file_bytes, content_type='image/jpeg')

        # Mock filetype.guess to track if it was called
        with patch('accounts.serializers.filetype.guess') as mock_guess:
            mock_guess.return_value = None
            serializer = KYCUploadSerializer(data={
                'document_type': 'id_front',
                'file': large_file,
            })
            self.assertFalse(serializer.is_valid())
            self.assertIn('file', serializer.errors)
            self.assertIn('File size', str(serializer.errors['file']))
            # filetype.guess should not have been called (failed fast on size)
            mock_guess.assert_not_called()

    def test_generic_error_message(self):
        """Test that error messages are generic and don't expose internals."""
        malicious_file = self._create_renamed_executable()
        serializer = KYCUploadSerializer(data={
            'document_type': 'id_front',
            'file': malicious_file,
        })
        self.assertFalse(serializer.is_valid())
        error_message = str(serializer.errors['file'])
        # Should not mention magic bytes, MIME types, or specific detection methods
        self.assertNotIn('magic', error_message.lower())
        self.assertNotIn('mime', error_message.lower())
        self.assertNotIn('byte', error_message.lower())
        self.assertIn('Unsupported file type', error_message)
