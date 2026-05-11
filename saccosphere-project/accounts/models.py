from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

from .storage import KYCDocumentStorage


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
    phone_number = models.CharField(
        max_length=13,
        null=True,
        blank=True,
        db_index=True,
        help_text='Phone number in E.164 format, for example 254712345678.',
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

    @property
    def member_count(self):
        try:
            from saccomembership.models import Membership
        except ImportError:
            return 0

        return Membership.objects.filter(
            sacco=self,
            status='APPROVED',
        ).count()


class KYCVerification(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        NOT_STARTED = 'NOT_STARTED', 'Not started'

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
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Date and time this KYC record was created.',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'KYC: {self.user.email} — {self.status}'


class OTPToken(models.Model):
    class Purpose(models.TextChoices):
        PHONE_VERIFY = 'PHONE_VERIFY', 'Phone verification'
        PASSWORD_RESET = 'PASSWORD_RESET', 'Password reset'
        LOGIN = 'LOGIN', 'Login'

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
        help_text='User who owns this OTP token.',
    )
    phone_number = models.CharField(
        max_length=13,
        help_text='Phone number receiving the OTP.',
    )
    code = models.CharField(
        max_length=6,
        help_text='Six-digit OTP code stored as a string.',
    )
    purpose = models.CharField(
        max_length=20,
        choices=Purpose.choices,
        default=Purpose.PHONE_VERIFY,
        help_text='Reason this OTP was created.',
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

    @property
    def is_expired(self):
        return self.expires_at < timezone.now()

    def __str__(self):
        return f'OTP for {self.phone_number} — {self.purpose}'


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
        help_text='IP address used when consent was recorded.',
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

    def __str__(self):
        return f'{self.user.email} — {self.consent_type} — {self.consented}'
