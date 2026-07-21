import re
from pathlib import Path

from django.conf import settings
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from .models import KYCVerification, OTPToken, Sacco, User, UserDevice
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
    if not KENYAN_PHONE_REGEX.match(phone_number):
        raise serializers.ValidationError(
            'Phone number must be a Kenyan E.164 number, for example '
            '+254712345678 or 254712345678.'
        )
    return phone_number


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
        """Validate uploaded KYC document size, type, and dimensions."""
        max_size = settings.FILE_UPLOAD_MAX_MEMORY_SIZE
        allowed_extensions = {'jpg', 'jpeg', 'png', 'pdf'}
        extension = Path(value.name).suffix.lower().lstrip('.')

        if value.size > max_size:
            raise serializers.ValidationError(
                'File size must not exceed 5MB.'
            )

        if extension not in allowed_extensions:
            raise serializers.ValidationError(
                'File extension must be jpg, jpeg, png, or pdf.'
            )

        if extension != 'pdf':
            self._validate_image_dimensions(value)

        return value

    def _validate_image_dimensions(self, value):
        """Validate minimum image dimensions for uploaded images."""
        try:
            value.seek(0)
            with Image.open(value) as image:
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise serializers.ValidationError(
                'Uploaded image is invalid or corrupted.'
            ) from exc
        finally:
            value.seek(0)

        if width < 400 or height < 300:
            raise serializers.ValidationError(
                'Image dimensions must be at least 400x300 pixels.'
            )


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
    phone_number = serializers.CharField(
        validators=[validate_kenyan_phone_number],
    )
    code = serializers.CharField(max_length=6)
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


