import re

from rest_framework import serializers

from .models import KYCVerification, OTPToken, Sacco, User


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

        if password != password2:
            raise serializers.ValidationError(
                {'password2': 'Passwords do not match.'}
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


class UserProfileSerializer(serializers.ModelSerializer):
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
        )
        read_only_fields = ('id', 'email', 'date_joined')


class SaccoListSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)

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
        )


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
    class Meta:
        model = KYCVerification
        fields = (
            'id',
            'status',
            'iprs_verified',
            'submitted_at',
            'rejection_reason',
        )
        read_only_fields = fields


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


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# accounts/admin.py
#
# UserAdmin registers your custom User model in the Django admin site.
# It shows useful user columns like email, name, phone number, active
# status, staff status, and date joined. It also tells Django admin how to
# search users, filter them, edit their profile fields, and create new users.
#
# SaccoAdmin registers Sacco records. list_display controls the columns shown
# in the admin table. list_filter adds the right-side filters. search_fields
# lets staff search by SACCO name or registration number.
#
# KYCVerificationAdmin registers KYC records. The user_email method displays
# the related user's email instead of the full user string. @admin.display
# tells Django how to label and sort that custom column.
#
# OTPTokenAdmin registers OTP records. The is_expired method exposes the
# model's is_expired property as a clean boolean column in admin.
#
# UserConsentAdmin registers consent records so staff can see what policy
# version a user accepted or rejected, and when that happened.
#
# accounts/serializers.py
#
# validate_password_strength is a small helper used by registration, password
# reset, and password change serializers. It keeps password rules in one
# place: at least 8 characters, with uppercase, lowercase, and a digit.
#
# validate_kenyan_phone_number checks that phone numbers look like Kenyan
# E.164 numbers. It accepts +254712345678 and 254712345678 because your model
# help text uses the no-plus format, while many APIs call the plus format
# E.164.
#
# UserRegistrationSerializer accepts signup data. password and password2 are
# write-only, meaning they can be received from the API but will not be shown
# in responses. validate checks matching passwords and strength. create uses
# User.objects.create_user so the password is hashed correctly, then creates a
# KYCVerification record with NOT_STARTED status for the new user.
#
# UserLoginSerializer is a plain Serializer, not a ModelSerializer, because it
# is only describing login input for docs and future views. It does not create
# or update a database model.
#
# UserProfileSerializer exposes safe profile fields. id, email, and date_joined
# are read-only so a member cannot change their identity fields through this
# serializer.
#
# SaccoListSerializer returns the compact SACCO list data. member_count is
# read-only and can come from a database annotation in a future view, or from
# the Sacco model property.
#
# SaccoDetailSerializer extends SaccoListSerializer by adding fields needed on
# a detail page, such as description, loan settings, and contact information.
#
# KYCStatusSerializer exposes the member-facing KYC status fields as read-only.
# This is important because members should see their review status but should
# not approve or reject their own KYC.
#
# OTPRequestSerializer describes the data needed to request an OTP: a Kenyan
# phone number and the OTP purpose.
#
# OTPVerifySerializer describes the data needed to verify an OTP: phone number
# and a 6-character code.
#
# PasswordResetRequestSerializer describes the first password reset step: send
# the phone number that should receive a reset OTP.
#
# PasswordResetConfirmSerializer describes the second password reset step:
# phone number, code, new password, and confirmation password. It validates
# matching passwords and password strength.
#
# PasswordChangeSerializer is for logged-in users. It checks the old password
# against the authenticated user from serializer context, then validates the
# new password confirmation and strength.
#
# Django/Python concepts you might not know well
#
# A ModelAdmin controls how a model appears in Django admin. It does not change
# the database table.
#
# A ModelSerializer builds serializer fields from a Django model automatically.
# A plain Serializer is better when the input is not directly saved to one
# model, like login or OTP verification.
#
# write_only=True means the API accepts the field in incoming data but does not
# include it in outgoing serialized data. This is important for passwords.
#
# read_only_fields means the API can return those fields but should ignore or
# reject attempts to edit them.
#
# A database annotation is an extra calculated value added to queryset rows.
# For example, a view can calculate member_count with Count(). The serializer
# can read that value as if it were a normal attribute.
#
# Manual test to confirm it works
#
# Open Django shell and try UserRegistrationSerializer with a strong matching
# password. After serializer.save(), confirm that a new User exists and that
# user.kyc.status is NOT_STARTED.
#
# Important design decision
#
# Password strength validation is shared in one helper so registration,
# password reset, and password change always enforce the same rule. This avoids
# a security bug where one path accepts weaker passwords than another.
#
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
