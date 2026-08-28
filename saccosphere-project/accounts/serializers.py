import re
from io import BytesIO
from pathlib import Path

import filetype
from config.utils import InvalidPhoneNumberError, normalize_phone_number
from django.conf import settings
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from .models import (
    DataErasureRequest,
    KYCVerification,
    OTPToken,
    Sacco,
    User,
    UserDevice,
)
from .role_utils import get_sacco_admin_id
from .utils import get_user_sacco_context


KENYAN_PHONE_REGEX = re.compile(r'^\+?254(?:7|1)\d{8}$')


def validate_password_strength(password):
    if len(password) < 8:
        raise serializers.ValidationError(
            'Password must be at least 8 characters long.'
        )
    if not any(char.isupper() for char in password):
        raise serializers.ValidationError(
            'Password must contain at least one uppercase letter.'
        )
    if not any(char.islower() for char in password):
        raise serializers.ValidationError(
            'Password must contain at least one lowercase letter.'
        )
    if not any(char.isdigit() for char in password):
        raise serializers.ValidationError(
            'Password must contain at least one digit.'
        )


def validate_kenyan_phone_number(phone_number):
    """Validate and normalize a Kenyan phone number.
    
    This function validates that the phone number is a valid Kenyan mobile number
    and returns it in canonical E.164 format (+254712345678).
    
    Args:
        phone_number: Phone number in any accepted format
    
    Returns:
        str: Phone number in E.164 format (+254712345678)
    
    Raises:
        serializers.ValidationError: If the phone number is invalid
    """
    try:
        return normalize_phone_number(phone_number)
    except InvalidPhoneNumberError as exc:
        raise serializers.ValidationError(str(exc))


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = (
            'email',
            'first_name',
            'last_name',
            'phone_number',
            'password',
            'password2',
        )

    def validate(self, attrs):
        password = attrs.get('password')
        password2 = attrs.get('password2')
        phone_number = attrs.get('phone_number')

        if password != password2:
            raise serializers.ValidationError(
                {'password2': 'Passwords do not match.'}
            )

        validate_kenyan_phone_number(phone_number)
        if User.objects.filter(phone_number=phone_number).exists():
            raise serializers.ValidationError(
                {'phone_number': 'A user with this phone number already exists.'}
            )

        validate_password_strength(password)
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        password = validated_data.pop('password')
        user = User.objects.create_user(
            password=password,
            **validated_data,
        )
        KYCVerification.objects.create(
            user=user,
            status=KYCVerification.Status.NOT_STARTED,
        )
        return user


class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)
    flow = serializers.ChoiceField(
        choices=('login', 'signup'),
        default='login',
    )
    nonce = serializers.CharField(required=False, allow_blank=True)


class UserProfileSerializer(serializers.ModelSerializer):
    sacco_id = serializers.SerializerMethodField()
    sacco_context = serializers.SerializerMethodField()
    biometric_login_enabled = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'first_name',
            'last_name',
            'phone_number',
            'profile_picture',
            'date_of_birth',
            'date_joined',
            'sacco_id',
            'sacco_context',
            'biometric_login_enabled',
        )
        read_only_fields = (
            'id',
            'email',
            'date_joined',
            'sacco_id',
            'sacco_context',
            'biometric_login_enabled',
        )

    def get_sacco_id(self, obj):
        return get_sacco_admin_id(obj)

    def get_sacco_context(self, obj):
        return get_user_sacco_context(obj)

    def get_biometric_login_enabled(self, obj):
        return obj.devices.filter(biometric_enabled=True).exists()


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = (
            'id',
            'user',
            'device_id',
            'device_name',
            'platform',
            'push_token',
            'biometric_enabled',
            'last_seen',
            'created_at',
        )
        read_only_fields = fields


class DeviceListSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = (
            'device_id',
            'device_name',
            'platform',
            'biometric_enabled',
            'last_seen',
        )
        read_only_fields = fields


class DeviceRegistrationSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    device_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    platform = serializers.ChoiceField(choices=UserDevice.Platform.choices)
    push_token = serializers.CharField(
        max_length=200,
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    biometric_enabled = serializers.BooleanField(default=False)


class SaccoListSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    membership_open = serializers.SerializerMethodField()
    can_apply = serializers.SerializerMethodField()

    class Meta:
        model = Sacco
        fields = (
            'id',
            'name',
            'logo',
            'sector',
            'county',
            'membership_type',
            'is_verified',
            'member_count',
            'registration_fee',
            'membership_open',
            'can_apply',
        )

    def get_membership_open(self, obj):
        """Check if membership is open for applications."""
        return obj.membership_type == Sacco.MembershipType.OPEN

    def get_can_apply(self, obj):
        """Check if current user can apply (always False for AllowAny)."""
        return False


class SaccoDetailSerializer(SaccoListSerializer):
    class Meta(SaccoListSerializer.Meta):
        fields = SaccoListSerializer.Meta.fields + (
            'description',
            'default_interest_rate',
            'loan_multiplier',
            'website',
            'email',
            'phone',
        )


class KYCStatusSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
    )
    admin_review_reason = serializers.SerializerMethodField()

    class Meta:
        model = KYCVerification
        fields = (
            'id',
            'status',
            'status_display',
            'iprs_verified',
            'iprs_attempted_at',
            'iprs_error',
            'admin_review_reason',
            'manual_verification_reason',
            'submitted_at',
            'rejection_reason',
            'id_front',
            'id_back',
            'passport',
        )
        read_only_fields = fields

    def get_admin_review_reason(self, obj):
        if obj.status == KYCVerification.Status.IPRS_MISMATCH:
            return obj.iprs_error or 'IPRS returned a mismatch.'

        if obj.status == KYCVerification.Status.PENDING_MANUAL:
            return obj.iprs_error or 'IPRS was unavailable.'

        if obj.status == KYCVerification.Status.PENDING:
            return 'Awaiting admin KYC review.'

        return ''


class KYCUploadSerializer(serializers.Serializer):
    """Validate KYC document upload input."""

    # Allowed MIME types based on current product requirements
    ALLOWED_MIME_TYPES = {
        'image/jpeg',
        'image/png',
        'application/pdf',
    }

    # Mapping of MIME types to expected extensions
    MIME_TO_EXTENSION = {
        'image/jpeg': ['jpg', 'jpeg'],
        'image/png': ['png'],
        'application/pdf': ['pdf'],
    }

    # Maximum image dimensions to prevent decompression bombs
    # 10000x10000 = 100 megapixels, which is a reasonable upper bound
    MAX_IMAGE_DIMENSION = 10000

    document_type = serializers.ChoiceField(
        choices=(
            ('id_front', 'ID front'),
            ('id_back', 'ID back'),
            ('passport', 'Passport'),
            ('huduma', 'Huduma'),
        ),
    )
    file = serializers.FileField()

    def validate_file(self, value):
        """Validate uploaded KYC document with comprehensive security checks."""
        # 1. Size limit check first (fail fast before expensive operations)
        max_size = settings.FILE_UPLOAD_MAX_MEMORY_SIZE
        if value.size > max_size:
            raise serializers.ValidationError(
                'File size must not exceed 5MB.'
            )

        # 2. Detect actual file type from magic bytes
        value.seek(0)
        file_bytes = value.read()
        value.seek(0)

        detected_type = filetype.guess(file_bytes)
        if detected_type is None:
            raise serializers.ValidationError(
                'Unsupported file type.'
            )

        detected_mime = detected_type.mime
        detected_extension = detected_type.extension

        # 3. Check against MIME type allow-list
        if detected_mime not in self.ALLOWED_MIME_TYPES:
            raise serializers.ValidationError(
                'Unsupported file type.'
            )

        # 4. Validate extension matches detected type
        declared_extension = Path(value.name).suffix.lower().lstrip('.')
        expected_extensions = self.MIME_TO_EXTENSION.get(detected_mime, [])

        if declared_extension not in expected_extensions:
            # Decision: Reject mismatched extensions to prevent confusion
            # and ensure file integrity
            raise serializers.ValidationError(
                'Unsupported file type.'
            )

        # 5. For images, validate with Pillow and strip EXIF
        if detected_mime != 'application/pdf':
            value = self._validate_and_clean_image(value, detected_mime)

        return value

    def _validate_and_clean_image(self, value, detected_mime):
        """Validate image with Pillow and strip EXIF metadata."""
        try:
            value.seek(0)
            with Image.open(value) as image:
                # Check dimensions before loading to prevent decompression bombs
                width, height = image.size

                if width > self.MAX_IMAGE_DIMENSION or height > self.MAX_IMAGE_DIMENSION:
                    raise serializers.ValidationError(
                        'Image dimensions exceed maximum allowed size.'
                    )

                # Check minimum dimensions
                if width < 400 or height < 300:
                    raise serializers.ValidationError(
                        'Image dimensions must be at least 400x300 pixels.'
                    )

                # Strip EXIF metadata by converting to RGB and saving
                # This removes all metadata including GPS coordinates
                output = BytesIO()

                # Convert to RGB to ensure consistent format
                if image.mode in ('RGBA', 'P'):
                    image = image.convert('RGB')

                # Save without EXIF
                image.save(output, format='JPEG' if detected_mime == 'image/jpeg' else 'PNG')
                output.seek(0)

                # Replace the original file with the cleaned version
                value.file = output
                value.size = output.getbuffer().nbytes

        except Image.DecompressionBombError as exc:
            raise serializers.ValidationError(
                'Image dimensions exceed maximum allowed size.'
            ) from exc
        except (UnidentifiedImageError, OSError) as exc:
            raise serializers.ValidationError(
                'Uploaded image is invalid or corrupted.'
            ) from exc
        finally:
            value.seek(0)

        return value


class AdminKYCReviewSerializer(serializers.Serializer):
    """Validate admin KYC review decisions."""

    status = serializers.ChoiceField(
        choices=(
            (KYCVerification.Status.APPROVED, 'Approved'),
            (KYCVerification.Status.REJECTED, 'Rejected'),
        ),
    )
    rejection_reason = serializers.CharField(
        allow_blank=True,
        required=False,
    )
    manual_verification_reason = serializers.CharField(
        max_length=255,
        min_length=10,
        required=False,
    )

    def validate(self, attrs):
        """Require a rejection reason when rejecting KYC."""
        if (
            attrs['status'] == KYCVerification.Status.REJECTED
            and not attrs.get('rejection_reason')
        ):
            raise serializers.ValidationError(
                {
                    'rejection_reason': (
                        'Rejection reason is required when rejecting KYC.'
                    ),
                }
            )

        return attrs


class OTPRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField(
        validators=[validate_kenyan_phone_number],
    )
    purpose = serializers.ChoiceField(choices=OTPToken.Purpose.choices)
    channel = serializers.ChoiceField(
        choices=OTPToken.Channel.choices,
        default=OTPToken.Channel.PHONE,
        required=False,
    )

    def validate(self, attrs):
        channel = attrs.get('channel', OTPToken.Channel.PHONE)

        if channel == OTPToken.Channel.EMAIL:
            if not settings.OTP_EMAIL_ENABLED:
                raise serializers.ValidationError(
                    'Email verification codes are not available yet — please use SMS.'
                )

            request = self.context.get('request')
            if request and request.user and request.user.is_authenticated:
                if not request.user.email:
                    raise serializers.ValidationError(
                        'Add an email address to your account before requesting an email code.'
                    )

        if channel == OTPToken.Channel.PHONE:
            request = self.context.get('request')
            if request and request.user and request.user.is_authenticated:
                if not request.user.phone_number:
                    raise serializers.ValidationError(
                        'Add a phone number to your account before requesting an SMS code.'
                    )

        return attrs


class OTPVerifySerializer(serializers.Serializer):
    phone_number = serializers.CharField(
        validators=[validate_kenyan_phone_number],
    )
    code = serializers.CharField(max_length=6, min_length=6)


class PasswordResetRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField(
        validators=[validate_kenyan_phone_number],
    )


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Serializer for verifying OTP and getting a reset token."""
    phone_number = serializers.CharField(
        validators=[validate_kenyan_phone_number],
    )
    code = serializers.CharField(max_length=6)


class PasswordResetCompleteSerializer(serializers.Serializer):
    """Serializer for completing password reset with the reset token."""
    reset_token = serializers.UUIDField()
    new_password = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def validate(self, attrs):
        new_password = attrs.get('new_password')
        new_password2 = attrs.get('new_password2')

        if new_password != new_password2:
            raise serializers.ValidationError(
                {'new_password2': 'Passwords do not match.'}
            )

        validate_password_strength(new_password)
        return attrs


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        old_password = attrs.get('old_password')
        new_password = attrs.get('new_password')
        new_password2 = attrs.get('new_password2')

        if user and user.is_authenticated:
            if not user.check_password(old_password):
                raise serializers.ValidationError(
                    {'old_password': 'Old password is incorrect.'}
                )

        if new_password != new_password2:
            raise serializers.ValidationError(
                {'new_password2': 'Passwords do not match.'}
            )

        validate_password_strength(new_password)
        return attrs


class DataErasureRequestSerializer(serializers.ModelSerializer):
    """Serializer for creating data erasure requests."""

    class Meta:
        model = DataErasureRequest
        fields = ('reason', 'status', 'hold_reason', 'hold_until')
        read_only_fields = ('status', 'hold_reason', 'hold_until')

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if not user or not user.is_authenticated:
            raise serializers.ValidationError(
                'Authentication required.'
            )

        # Check for existing pending request
        existing = DataErasureRequest.objects.filter(
            user=user,
            status=DataErasureRequest.Status.PENDING
        ).first()

        if existing:
            raise serializers.ValidationError(
                'You already have a pending erasure request.'
            )

        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        user = request.user

        # Extract optional fields that may be set by the view
        status = validated_data.pop('status', DataErasureRequest.Status.PENDING)
        hold_reason = validated_data.pop('hold_reason', None)
        hold_until = validated_data.pop('hold_until', None)

        return DataErasureRequest.objects.create(
            user=user,
            reason=validated_data.get('reason'),
            status=status,
            hold_reason=hold_reason,
            hold_until=hold_until,
        )


class DataErasureReviewSerializer(serializers.Serializer):
    """Serializer for staff review of erasure requests."""

    action = serializers.ChoiceField(choices=['approve', 'reject'])
    reviewer_notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if not user or not user.is_authenticated or not user.is_staff:
            raise serializers.ValidationError(
                'Staff access required.'
            )

        return attrs


