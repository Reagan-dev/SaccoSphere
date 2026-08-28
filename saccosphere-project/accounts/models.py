from decimal import Decimal

from uuid import uuid4

from cryptography.fernet import Fernet

from django.conf import settings

from django.contrib.auth.models import AbstractUser, BaseUserManager

from django.db import models

from django.utils import timezone

from .storage import KYCDocumentStorage
import re


def normalize_id_number(id_number):
    """
    Normalize a Kenyan national ID number for comparison and storage.

    Normalization rules:
    - Strip leading/trailing whitespace
    - Remove all non-alphanumeric characters (spaces, dashes, slashes, etc.)
    - Convert to uppercase for consistency

    This ensures that "12345678", "1234-5678", and " 12345678 " are treated
    as the same ID number.

    Args:
        id_number: The raw ID number string (can be None or empty)

    Returns:
        Normalized ID number string, or None if input is None, empty string if input is empty/whitespace
    """
    if id_number is None:
        return None

    if not isinstance(id_number, str):
        id_number = str(id_number)

    # Strip whitespace
    id_number = id_number.strip()

    # Return empty string if input was just whitespace
    if not id_number:
        return ''

    # Remove all non-alphanumeric characters
    normalized = re.sub(r'[^a-zA-Z0-9]', '', id_number)

    # Convert to uppercase
    normalized = normalized.upper()

    return normalized


class EncryptedFieldMixin:
    """Mixin for encrypting sensitive fields at rest using Fernet encryption."""

    def _get_fernet(self):
        """Get or create Fernet instance for encryption/decryption."""
        encryption_key = getattr(settings, 'FIELD_ENCRYPTION_KEY', None)
        if not encryption_key:
            raise ValueError(
                'FIELD_ENCRYPTION_KEY must be set in settings for encrypted fields. '
                'Generate one with: from cryptography.fernet import Fernet; '
                'Fernet.generate_key()'
            )
        return Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    def _encrypt(self, value):
        """Encrypt a plaintext value."""
        if value is None or value == '':
            return value
        fernet = self._get_fernet()
        return fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value):
        """Decrypt an encrypted value."""
        if value is None or value == '':
            return value
        fernet = self._get_fernet()
        return fernet.decrypt(value.encode()).decode()


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    """CharField that encrypts values on save and decrypts on load."""

    def from_db_value(self, value, expression, connection):
        """Decrypt value when loading from database."""
        if value is None:
            return value
        return self._decrypt(value)

    def to_python(self, value):
        """Decrypt value when loading from forms/serializers."""
        if value is None:
            return value
        if isinstance(value, str):
            try:
                return self._decrypt(value)
            except Exception:
                # If decryption fails, return as-is (might already be plaintext)
                return value
        return value

    def get_prep_value(self, value):
        """Encrypt value before saving to database."""
        if value is None:
            return value
        return self._encrypt(value)





KENYA_COUNTIES = [

    'Baringo',

    'Bomet',

    'Bungoma',

    'Baricho',

    'Elgeyo-Marakwet',

    'Embu',

    'Garissa',

    'Homa Bay',

    'Isiolo',

    'Kajiado',

    'Kakamega',

    'Kamba',

    'Kericho',

    'Kiambu',

    'Kilifi',

    'Kirinyaga',

    'Kisii',

    'Kisumu',

    'Kitui',

    'Kwale',

    'Laikipia',

    'Lamu',

    'Machakos',

    'Makueni',

    'Mandera',

    'Marsabit',

    'Meru',

    'Migori',

    'Mombasa',

    'Murang\'a',

    'Nairobi',

    'Nakuru',

    'Nandi',

    'Narok',

    'Nyamira',

    'Nyandarua',

    'Nyeri',

    'Samburu',

    'Siaya',

    'Taita-Taveta',

    'Tana River',

    'Transnzoia',

    'Turkana',

    'Tharaka-Nithi',

    'Uasin Gishu',

    'Vihiga',

    'Wajir',

    'West Pokot',

]





class UserManager(BaseUserManager):

    use_in_migrations = True



    def create_user(self, email, password=None, **extra_fields):

        if not email:

            raise ValueError('The email address is required.')



        email = self.normalize_email(email)

        user = self.model(email=email, **extra_fields)

        user.set_password(password)

        user.save(using=self._db)

        return user



    def create_superuser(self, email, password=None, **extra_fields):

        extra_fields.setdefault('is_staff', True)

        extra_fields.setdefault('is_superuser', True)

        extra_fields.setdefault('is_active', True)



        if extra_fields.get('is_staff') is not True:

            raise ValueError('Superuser must have is_staff=True.')

        if extra_fields.get('is_superuser') is not True:

            raise ValueError('Superuser must have is_superuser=True.')



        return self.create_user(email, password, **extra_fields)





class User(AbstractUser):

    """

    SaccoSphere user account.



    first_name and last_name are inherited from Django's AbstractUser.

    """



    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

        help_text='Unique user identifier.',

    )

    username = models.CharField(

        max_length=150,

        null=True,

        blank=True,

        unique=False,

        help_text='Optional legacy username. Email is used for login.',

    )

    email = models.EmailField(

        unique=True,

        db_index=True,

        help_text='User email address. Used as the login identifier.',

    )

    google_id = models.CharField(

        max_length=255,

        null=True,

        blank=True,

        unique=True,

        db_index=True,

        help_text='Google account identifier (sub claim) for OAuth sign-in.',

    )

    phone_number = models.CharField(

        max_length=13,

        null=True,

        blank=True,

        db_index=True,

        help_text='Phone number in E.164 format, for example 254712345678.',

    )

    phone_verified_at = models.DateTimeField(

        null=True,

        blank=True,

        db_index=True,

        help_text='Timestamp when the phone number was verified via OTP.',

    )

    profile_picture = models.ImageField(

        upload_to='profiles/',

        null=True,

        blank=True,

        help_text='Optional user profile picture.',

    )

    date_of_birth = models.DateField(

        null=True,

        blank=True,

        help_text='Optional user date of birth.',

    )



    USERNAME_FIELD = 'email'

    REQUIRED_FIELDS = ['first_name', 'last_name']

    objects = UserManager()



    class Meta:

        ordering = ['first_name', 'last_name', 'email']



    def __str__(self):

        return f'{self.first_name} {self.last_name} <{self.email}>'





class UserDevice(models.Model):

    class Platform(models.TextChoices):

        IOS = 'ios', 'iOS'

        ANDROID = 'android', 'Android'



    user = models.ForeignKey(

        settings.AUTH_USER_MODEL,

        on_delete=models.CASCADE,

        related_name='devices',

    )

    device_id = models.CharField(max_length=100)

    device_name = models.CharField(max_length=100, null=True, blank=True)

    platform = models.CharField(max_length=20, choices=Platform.choices)

    push_token = models.CharField(max_length=200, null=True, blank=True)

    biometric_enabled = models.BooleanField(default=False)

    last_seen = models.DateTimeField(auto_now=True)

    created_at = models.DateTimeField(auto_now_add=True)



    class Meta:

        unique_together = ['user', 'device_id']



    def __str__(self):

        device_name = self.device_name or self.device_id

        return f'{self.user.email} - {device_name} ({self.platform})'





class Sacco(models.Model):

    class Sector(models.TextChoices):

        EDUCATION = 'EDUCATION', 'Education'

        HEALTHCARE = 'HEALTHCARE', 'Healthcare'

        AGRICULTURE = 'AGRICULTURE', 'Agriculture'

        TRANSPORT = 'TRANSPORT', 'Transport'

        GOVERNMENT = 'GOVERNMENT', 'Government'

        TECHNOLOGY = 'TECHNOLOGY', 'Technology'

        FINANCE = 'FINANCE', 'Finance'

        RETAIL = 'RETAIL', 'Retail'

        OTHER = 'OTHER', 'Other'



    class MembershipType(models.TextChoices):

        OPEN = 'OPEN', 'Open'

        CLOSED = 'CLOSED', 'Closed'

        STAFF_ONLY = 'STAFF_ONLY', 'Staff only'



    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

        help_text='Unique SACCO identifier.',

    )

    name = models.CharField(

        max_length=200,

        unique=True,

        help_text='Official SACCO name.',

    )

    registration_number = models.CharField(

        max_length=50,

        unique=True,

        null=True,

        blank=True,

        help_text='Official cooperative registration number.',

    )

    description = models.TextField(

        null=True,

        blank=True,

        help_text='Short public description of the SACCO.',

    )

    logo = models.ImageField(

        upload_to='sacco_logos/',

        null=True,

        blank=True,

        help_text='Optional SACCO logo.',

    )

    sector = models.CharField(

        max_length=50,

        choices=Sector.choices,

        help_text='Main sector served by the SACCO.',

    )

    county = models.CharField(

        max_length=50,

        help_text='Kenya county where the SACCO is based.',

    )

    membership_type = models.CharField(

        max_length=20,

        choices=MembershipType.choices,

        default=MembershipType.OPEN,

        help_text='Controls who can join this SACCO.',

    )

    is_publicly_listed = models.BooleanField(

        default=True,

        help_text='Whether the SACCO is visible in public listings.',

    )

    is_verified = models.BooleanField(

        default=False,

        help_text='Whether SaccoSphere has verified this SACCO.',

    )

    is_active = models.BooleanField(

        default=True,

        help_text='Whether this SACCO can currently operate on the platform.',

    )

    is_billing_suspended = models.BooleanField(default=False)

    suspended_at = models.DateTimeField(null=True, blank=True)

    payment_ready = models.BooleanField(
        default=False,
        help_text='Whether this SACCO has completed M-Pesa Daraja onboarding and can process payments.',
    )

    suspension_reason = models.TextField(blank=True)

    default_interest_rate = models.DecimalField(

        max_digits=5,

        decimal_places=2,

        default=Decimal('12.00'),

        help_text='Default annual loan interest rate percentage.',

    )

    loan_multiplier = models.DecimalField(

        max_digits=4,

        decimal_places=2,

        default=Decimal('3.00'),

        help_text='Maximum loan multiplier based on member savings.',

    )

    min_loan_months = models.PositiveIntegerField(

        default=3,

        help_text='Minimum membership duration before loan eligibility.',

    )

    registration_fee = models.DecimalField(

        max_digits=10,

        decimal_places=2,

        default=Decimal('0.00'),

        help_text='Joining fee in KES.',

    )

    website = models.URLField(

        null=True,

        blank=True,

        help_text='Optional SACCO website URL.',

    )

    email = models.EmailField(

        null=True,

        blank=True,

        help_text='Optional SACCO contact email.',

    )

    phone = models.CharField(

        max_length=13,

        null=True,

        blank=True,

        help_text='Optional SACCO phone number in E.164 format.',

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

        db_index=True,

        help_text='Date and time this SACCO was created.',

    )

    updated_at = models.DateTimeField(

        auto_now=True,

        help_text='Date and time this SACCO was last updated.',

    )



    class Meta:

        ordering = ['-created_at']

        indexes = [

            models.Index(fields=['sector', 'county']),

        ]



    def __str__(self):

        return self.name





class SaccoSettings(models.Model):

    """SACCO-specific configuration overrides for loans and membership."""



    class GuarantorTypeAllowed(models.TextChoices):

        MEMBER_ONLY = 'MEMBER_ONLY', 'Member only'

        EXTERNAL_ONLY = 'EXTERNAL_ONLY', 'External only'

        BOTH = 'BOTH', 'Both'



    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

    )

    sacco = models.OneToOneField(

        Sacco,

        on_delete=models.CASCADE,

        related_name='settings',

    )

    min_loan_amount = models.DecimalField(

        max_digits=12,

        decimal_places=2,

        default=Decimal('1000.00'),

    )

    max_loan_amount = models.DecimalField(

        max_digits=12,

        decimal_places=2,

        default=Decimal('500000.00'),

    )

    loan_multiplier = models.PositiveSmallIntegerField(default=3)

    requires_guarantor = models.BooleanField(default=True)

    guarantor_type_allowed = models.CharField(

        max_length=20,

        choices=GuarantorTypeAllowed.choices,

        default=GuarantorTypeAllowed.BOTH,

    )

    registration_fee = models.DecimalField(

        max_digits=10,

        decimal_places=2,

        default=Decimal('0.00'),

    )

    monthly_contribution_amount = models.DecimalField(

        max_digits=10,

        decimal_places=2,

        default=Decimal('0.00'),

    )

    liquidity_threshold_percentage = models.DecimalField(

        max_digits=5,

        decimal_places=2,

        default=Decimal('80.00'),

        help_text='Liquidity utilisation percentage at which a warning fires.',

    )

    sms_daily_limit = models.PositiveIntegerField(
        default=1000,
        help_text='Daily SMS send limit to control costs.',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)



    class Meta:

        ordering = ['sacco__name']



    def __str__(self):

        return f'Settings — {self.sacco.name}'


class SaccoPaymentConfig(models.Model):
    """SACCO-specific M-Pesa Daraja payment configuration."""

    class ShortcodeType(models.TextChoices):
        PAYBILL = 'PAYBILL', 'Paybill Number'
        TILL_NUMBER = 'TILL_NUMBER', 'Till Number'

    class Environment(models.TextChoices):
        SANDBOX = 'SANDBOX', 'Sandbox'
        LIVE = 'LIVE', 'Live'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique payment config identifier.',
    )

    sacco = models.OneToOneField(
        Sacco,
        on_delete=models.CASCADE,
        related_name='payment_config',
        help_text='The SACCO this payment configuration belongs to.',
    )

    shortcode_type = models.CharField(
        max_length=20,
        choices=ShortcodeType.choices,
        default=ShortcodeType.PAYBILL,
        help_text='Type of M-Pesa shortcode (Paybill or Till Number).',
    )

    shortcode = models.CharField(
        max_length=10,
        help_text='M-Pesa shortcode number (Paybill or Till Number).',
    )

    stk_passkey = EncryptedCharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='STK push passkey for this shortcode (encrypted at rest).',
    )

    daraja_consumer_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='Daraja consumer key (nullable if using platform aggregator).',
    )

    daraja_consumer_secret = EncryptedCharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='Daraja consumer secret (encrypted at rest, nullable if using platform aggregator).',
    )

    environment = models.CharField(
        max_length=20,
        choices=Environment.choices,
        default=Environment.SANDBOX,
        help_text='Daraja environment (Sandbox or Live).',
    )

    b2c_initiator_name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='B2C initiator name for disbursements (nullable if SACCO does not disburse).',
    )

    b2c_security_credential = EncryptedCharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='B2C encrypted security credential (encrypted at rest, nullable if SACCO does not disburse).',
    )

    is_active = models.BooleanField(
        default=True,
        help_text='Whether this payment configuration is active.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'SACCO Payment Configuration'
        verbose_name_plural = 'SACCO Payment Configurations'
        ordering = ['sacco__name']

    def __str__(self):
        return f'Payment Config — {self.sacco.name} ({self.shortcode})'

    def has_b2c_config(self):
        """Check if this SACCO has B2C disbursement configured."""
        return bool(
            self.b2c_initiator_name and self.b2c_security_credential
        )





class KYCVerification(models.Model):

    class Status(models.TextChoices):

        PENDING = 'PENDING', 'Pending'

        IPRS_MISMATCH = 'IPRS_MISMATCH', 'IPRS mismatch'

        PENDING_MANUAL = 'PENDING_MANUAL', 'Pending manual review'

        APPROVED = 'APPROVED', 'Approved'

        REJECTED = 'REJECTED', 'Rejected'

        NOT_STARTED = 'NOT_STARTED', 'Not started'

        IPRS_REJECTED = 'IPRS_REJECTED', 'Rejected by IPRS'

        IPRS_UNAVAILABLE = 'IPRS_UNAVAILABLE', 'IPRS unavailable'



    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

        help_text='Unique KYC verification identifier.',

    )

    user = models.OneToOneField(

        User,

        on_delete=models.CASCADE,

        related_name='kyc',

        help_text='User whose identity is being verified.',

    )

    id_number = models.CharField(

        max_length=20,

        null=True,

        blank=True,

        help_text='Kenya National ID number.',

    )

    normalized_id_number = models.CharField(

        max_length=20,

        null=True,

        blank=True,

        db_index=True,

        help_text='Normalized ID number (stripped of punctuation, uppercase).',

    )

    id_front = models.ImageField(

        storage=KYCDocumentStorage(),

        upload_to='kyc/front/',

        null=True,

        blank=True,

        help_text='Front image of the national ID.',

    )

    id_back = models.ImageField(

        storage=KYCDocumentStorage(),

        upload_to='kyc/back/',

        null=True,

        blank=True,

        help_text='Back image of the national ID.',

    )

    passport = models.ImageField(

        storage=KYCDocumentStorage(),

        upload_to='kyc/passport/',

        null=True,

        blank=True,

        help_text='Optional passport image.',

    )

    huduma = models.ImageField(

        storage=KYCDocumentStorage(),

        upload_to='kyc/huduma/',

        null=True,

        blank=True,

        help_text='Optional Huduma card image.',

    )

    huduma_namba = models.CharField(

        max_length=20,

        null=True,

        blank=True,

        help_text='Optional Huduma Namba.',

    )

    iprs_verified = models.BooleanField(

        default=False,

        help_text='Whether identity was verified through IPRS.',

    )

    iprs_reference = models.CharField(

        max_length=100,

        null=True,

        blank=True,

        help_text='IPRS verification reference number.',

    )

    iprs_attempted_at = models.DateTimeField(

        null=True,

        blank=True,

        help_text='Date and time IPRS verification was last attempted.',

    )

    iprs_error = models.CharField(

        max_length=255,

        null=True,

        blank=True,

        help_text='Short IPRS error or mismatch reason.',

    )

    manual_verification_reason = models.CharField(

        max_length=255,

        null=True,

        blank=True,

        help_text='Admin reason for approving a manually verified KYC.',

    )

    status = models.CharField(

        max_length=20,

        choices=Status.choices,

        default=Status.NOT_STARTED,

        help_text='Current KYC review status.',

    )

    rejection_reason = models.TextField(

        null=True,

        blank=True,

        help_text='Reason provided when KYC is rejected.',

    )

    verified_at = models.DateTimeField(

        null=True,

        blank=True,

        help_text='Date and time the KYC was approved.',

    )

    reviewed_by = models.ForeignKey(

        User,

        null=True,

        blank=True,

        on_delete=models.SET_NULL,

        related_name='kyc_reviews',

        help_text='Staff user who reviewed this KYC record.',

    )

    submitted_at = models.DateTimeField(

        null=True,

        blank=True,

        help_text='Date and time the user submitted KYC documents.',

    )

    retention_until = models.DateTimeField(

        null=True,

        blank=True,

        help_text='Date until which KYC documents must be retained.',

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

        help_text='Date and time this KYC record was created.',

    )



    class Meta:

        ordering = ['-created_at']

        constraints = [
            models.UniqueConstraint(
                fields=['normalized_id_number'],
                condition=~models.Q(normalized_id_number__isnull=True)
                & ~models.Q(normalized_id_number__exact=''),
                name='unique_normalized_id_number',
                violation_error_message=(
                    'A user with this national ID number already exists.'
                ),
            )
        ]

    def save(self, *args, **kwargs):
        """Normalize id_number and set retention_until before saving."""
        if self.id_number:
            self.normalized_id_number = normalize_id_number(self.id_number)
        elif self.id_number == '':
            self.normalized_id_number = ''
        else:
            self.normalized_id_number = None

        # Set retention_until if not already set and submitted_at is present
        from django.conf import settings
        retention_days = getattr(settings, 'KYC_RETENTION_DAYS', None)
        if (
            retention_days
            and not self.retention_until
            and self.submitted_at
        ):
            from datetime import timedelta
            from django.utils import timezone
            self.retention_until = self.submitted_at + timedelta(days=retention_days)

        super().save(*args, **kwargs)

    def __str__(self):

        return f'KYC: {self.user.email} — {self.status}'





class OTPToken(models.Model):

    class Purpose(models.TextChoices):

        PHONE_VERIFY = 'PHONE_VERIFY', 'Phone verification'

        PASSWORD_RESET = 'PASSWORD_RESET', 'Password reset'

        LOGIN = 'LOGIN', 'Login'

    class Channel(models.TextChoices):

        PHONE = 'PHONE', 'Phone (SMS)'

        EMAIL = 'EMAIL', 'Email'




    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

        help_text='Unique OTP token identifier.',

    )

    user = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        related_name='otp_tokens',

        null=True,

        blank=True,

        help_text='User who owns this OTP token. Null for registration OTPs.',

    )

    phone_number = models.CharField(

        max_length=13,

        help_text='Phone number receiving the OTP.',

    )

    code = models.CharField(

        max_length=64,

        help_text='HMAC-SHA256 hash of the six-digit OTP code.',

    )

    purpose = models.CharField(

        max_length=20,

        choices=Purpose.choices,

        default=Purpose.PHONE_VERIFY,

        help_text='Reason this OTP was created.',

    )

    channel = models.CharField(
        max_length=10,
        choices=Channel.choices,
        default=Channel.PHONE,
        help_text='Delivery channel used to send the OTP (SMS or email).',
    )

    is_used = models.BooleanField(

        default=False,

        db_index=True,

        help_text='Whether this OTP has already been used.',

    )

    attempts = models.PositiveSmallIntegerField(

        default=0,

        help_text='Number of failed verification attempts.',

    )

    expires_at = models.DateTimeField(

        db_index=True,

        help_text='Date and time this OTP expires.',

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

        help_text='Date and time this OTP was created.',

    )



    class Meta:

        ordering = ['-created_at']

        constraints = [
            models.UniqueConstraint(
                fields=['phone_number', 'purpose'],
                condition=models.Q(is_used=False),
                name='unique_active_otp_per_phone_purpose',
                violation_error_message=(
                    'An active OTP token already exists for this phone number '
                    'and purpose. Please wait for the existing token to expire '
                    'or be used before requesting a new one.'
                ),
            ),
        ]



    @property

    def is_expired(self):

        return self.expires_at < timezone.now()



    def __str__(self):

        return f'OTP for {self.phone_number} — {self.purpose}'



class PasswordResetToken(models.Model):
    """Short-lived, single-use token for password reset confirmation."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique password reset token identifier.',
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='password_reset_tokens',
        help_text='User requesting password reset.',
    )

    otp_token = models.OneToOneField(
        OTPToken,
        on_delete=models.CASCADE,
        related_name='password_reset_token',
        help_text='The OTP token that was verified to generate this reset token.',
    )

    is_used = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Whether this reset token has been used to change the password.',
    )

    expires_at = models.DateTimeField(
        db_index=True,
        help_text='Token expiration timestamp.',
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Token creation timestamp.',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_used']),
            models.Index(fields=['expires_at']),
        ]

    @property
    def is_expired(self):
        return self.expires_at < timezone.now()

    def __str__(self):
        return f'PasswordResetToken for {self.user.email}'





class UserConsent(models.Model):

    class ConsentType(models.TextChoices):

        TERMS = 'TERMS', 'Terms'

        PRIVACY = 'PRIVACY', 'Privacy'

        DATA_PROCESSING = 'DATA_PROCESSING', 'Data processing'

        MARKETING = 'MARKETING', 'Marketing'



    id = models.UUIDField(

        primary_key=True,

        default=uuid4,

        editable=False,

        help_text='Unique user consent identifier.',

    )

    user = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        related_name='consents',

        help_text='User who gave or denied consent.',

    )

    consent_type = models.CharField(

        max_length=30,

        choices=ConsentType.choices,

        help_text='Type of consent being recorded.',

    )

    version = models.CharField(

        max_length=20,

        help_text='Policy version, for example v1.2.',

    )

    consented = models.BooleanField(

        help_text='Whether the user accepted this consent.',

    )

    ip_address = models.GenericIPAddressField(

        null=True,

        blank=True,

        help_text='IP address used when consent was recorded. '
                  'Nullable at model level for admin/migrated records, '
                  'but API layer should require this for public consent endpoints.',

    )

    user_agent = models.CharField(

        max_length=255,

        null=True,

        blank=True,

        help_text='Browser or client user agent.',

    )

    timestamp = models.DateTimeField(

        auto_now_add=True,

        help_text='Date and time the consent was recorded.',

    )



    class Meta:
        ordering = ['-timestamp']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'consent_type', 'version'],
                name='unique_user_consent_per_version',
                violation_error_message='A consent record for this user, consent type, and version already exists.',
            )
        ]
        indexes = [
            models.Index(
                fields=['user', 'consent_type', '-timestamp'],
                name='user_consent_lookup_idx',
            ),
        ]

    def get_status(self):
        """
        Return the current status of this consent record.

        Possible statuses:
        - active: This is the current, valid consent for this consent_type
        - outdated: Superseded by a newer policy version
        - withdrawn: User explicitly withdrew consent
        - never_given: No consent record exists

        TODO: This is a placeholder. Full implementation requires:
        - Version comparison logic against CONSENT_POLICY_VERSIONS setting
        - Withdrawal tracking (add a withdrawn_at field or separate model)
        - Policy version history tracking
        """
        # Placeholder: return 'active' if consented=True, otherwise 'never_given'
        # This will be enhanced once version-validation logic lands
        if self.consented:
            return 'active'
        return 'never_given'


class DataErasureRequest(models.Model):
    """Model for tracking user data erasure/anonymization requests."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        COMPLETED = 'COMPLETED', 'Completed'
        ON_HOLD = 'ON_HOLD', 'On Hold'

    class HoldReason(models.TextChoices):
        REGULATORY_INVESTIGATION = 'REGULATORY_INVESTIGATION', 'Regulatory Investigation'
        DISPUTE_IN_PROGRESS = 'DISPUTE_IN_PROGRESS', 'Dispute In Progress'
        LEGAL_HOLD = 'LEGAL_HOLD', 'Legal Hold'
        AUDIT_IN_PROGRESS = 'AUDIT_IN_PROGRESS', 'Audit In Progress'

    id = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Unique data erasure request identifier.',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='erasure_requests',
        help_text='User requesting erasure. Null after anonymization.',
    )
    user_email_anonymized = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='Anonymized email reference for audit trail after user deletion.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        help_text='Current status of the erasure request.',
    )
    hold_reason = models.CharField(
        max_length=50,
        choices=HoldReason.choices,
        null=True,
        blank=True,
        help_text='Reason for hold if status is ON_HOLD.',
    )
    hold_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Date until which the hold applies.',
    )
    requested_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Timestamp when the erasure request was submitted.',
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the request was reviewed.',
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_erasure_requests',
        help_text='Staff user who reviewed the request.',
    )
    reviewer_notes = models.TextField(
        null=True,
        blank=True,
        help_text='Notes from the reviewer explaining the decision.',
    )
    reason = models.TextField(
        null=True,
        blank=True,
        help_text='User-provided reason for the erasure request.',
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when anonymization was completed.',
    )

    class Meta:
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'requested_at']),
            models.Index(fields=['status', 'hold_until']),
        ]

    def __str__(self):
        user_ref = self.user_email_anonymized or (self.user.email if self.user else 'Unknown')
        return f'Erasure request for {user_ref} - {self.status}'

    def is_on_hold(self):
        """Check if the request is currently on hold."""
        if self.status != self.Status.ON_HOLD:
            return False
        if self.hold_until and self.hold_until > timezone.now():
            return True
        return False

