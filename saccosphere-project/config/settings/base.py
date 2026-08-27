import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import dj_database_url
from corsheaders.defaults import default_headers
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured
from decouple import Csv, config



BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _cast_debug(value):
    normalized = str(value).strip().lower()
    if normalized in {'1', 'yes', 'true', 'on', 'y', 't'}:
        return True
    if normalized in {'0', 'no', 'false', 'off', 'n', 'f', '', 'release'}:
        return False
    raise ValueError('Invalid truth value: ' + str(value))

SECRET_KEY = config(
    'SECRET_KEY',
    default='django-insecure-development-only-change-me',
)
DEBUG = config('DEBUG', default=False, cast=_cast_debug)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='', cast=Csv())
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = list(default_headers) + [
    'x-request-id',
]
FRONTEND_BASE_URL = config(
    'FRONTEND_BASE_URL',
    default=config('FRONTEND_URL', default='http://localhost:3000'),
)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_yasg',
    'django_celery_beat',
    'accounts',
    'guarantor',
    'saccomembership',
    'saccomanagement',
    'services.apps.ServicesConfig',
    'payments',
    'notifications',
    'ledger',
    'dashboard',
    'billing',
    'health',
    'storages',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'config.middleware.RequestCorrelationMiddleware',
    'config.middleware.LoggingMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'saccomanagement.middleware.BillingSuspensionMiddleware',
    'saccomanagement.middleware.SaccoContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',

    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.parse(
        config(
            "DATABASE_URL",
            default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        ),
        conn_max_age=60,
        conn_health_checks=True
    ),
}

STORAGE_BACKEND = config('STORAGE_BACKEND', default='local').lower()
AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID', default='')
AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default='')
AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME', default='')
AWS_S3_ENDPOINT_URL = config('AWS_S3_ENDPOINT_URL', default='')
AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='')
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = config('AWS_QUERYSTRING_AUTH', default=True, cast=bool)



AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': (
            'django.contrib.auth.password_validation.'
            'UserAttributeSimilarityValidator'
        ),
    },
    {
        'NAME': (
            'django.contrib.auth.password_validation.MinimumLengthValidator'
        ),
    },
    {
        'NAME': (
            'django.contrib.auth.password_validation.CommonPasswordValidator'
        ),
    },
    {
        'NAME': (
            'django.contrib.auth.password_validation.NumericPasswordValidator'
        ),
    },
]

AUTH_USER_MODEL = 'accounts.User'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

if STORAGE_BACKEND not in {'local', 's3'}:
    raise ImproperlyConfigured(
        "STORAGE_BACKEND must be either 'local' or 's3'."
    )

if STORAGE_BACKEND == 's3' and not AWS_STORAGE_BUCKET_NAME:
    raise ImproperlyConfigured(
        'AWS_STORAGE_BUCKET_NAME is required when STORAGE_BACKEND=s3.'
    )

default_storage_backend = 'django.core.files.storage.FileSystemStorage'
if STORAGE_BACKEND == 's3':
    default_storage_backend = 'storages.backends.s3boto3.S3Boto3Storage'

STORAGES = {
    'default': {
        'BACKEND': default_storage_backend,
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PAGINATION_CLASS': (
        'config.pagination.SaccoSpherePagination'
    ),
    'PAGE_SIZE': 20,
    'EXCEPTION_HANDLER': (
        'config.exception_handler.custom_exception_handler'
    ),
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
        'google_oauth': '10/minute',
    },    
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

SWAGGER_SETTINGS = {
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
            'description': 'JWT Authorization header using Bearer scheme.',
        },
    },
    'USE_SESSION_AUTH': False,
    'JSON_EDITOR': True,
}

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST', default='')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config(
    'DEFAULT_FROM_EMAIL',
    default='SaccoSphere <no-reply@saccosphere.local>',
)
OTP_EMAIL_ENABLED = config('OTP_EMAIL_ENABLED', default=False, cast=bool)

# OTP Configuration
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = 3
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_HASH_KEY = config('OTP_HASH_KEY', default='')
OTP_HASH_KEY_USES_SECRET_KEY_FALLBACK = not bool(OTP_HASH_KEY)
if OTP_HASH_KEY_USES_SECRET_KEY_FALLBACK:
    OTP_HASH_KEY = f'otp-hash:{SECRET_KEY}'

# Africa's Talking SMS Configuration
AT_API_KEY = config('AT_API_KEY', default='')
AT_USERNAME = config('AT_USERNAME', default='sandbox')
AT_ENVIRONMENT = config('AT_ENVIRONMENT', default='sandbox' if DEBUG else 'production')
FCM_SERVER_KEY = config('FCM_SERVER_KEY', default='')

# IPRS Configuration
IPRS_API_KEY = config('IPRS_API_KEY', default='')
IPRS_API_URL = config(
    'IPRS_API_URL',
    default='https://iprs-mock.saccosphere.dev/verify',
)
IPRS_MOCK = config('IPRS_MOCK', cast=bool, default=True)

# Metropol CRB Configuration
METROPOL_API_KEY = config('METROPOL_API_KEY', default='')
METROPOL_API_URL = config(
    'METROPOL_API_URL',
    default='https://metropol-mock.saccosphere.dev/credit-check',
)
METROPOL_MOCK = config('METROPOL_MOCK', cast=bool, default=True)

# Google OAuth Configuration
OAUTH_MOCK = config('OAUTH_MOCK', cast=bool, default=True)
GOOGLE_OAUTH_CLIENT_ID = config('GOOGLE_OAUTH_CLIENT_ID', default='')
# NOTE: GOOGLE_OAUTH_CLIENT_SECRET is unused by the current mobile ID-token flow.
# It is retained here for potential future browser-based OAuth implementation.
GOOGLE_OAUTH_CLIENT_SECRET = config(
    'GOOGLE_OAUTH_CLIENT_SECRET',
    default='',
)
# NOTE: GOOGLE_OAUTH_REDIRECT_URI is unused by the current mobile ID-token flow.
# It is retained here for potential future browser-based OAuth implementation.
GOOGLE_OAUTH_REDIRECT_URI = config('GOOGLE_OAUTH_REDIRECT_URI', default='')
# List of allowed Google OAuth client IDs for audience validation.
# Both Android and iOS apps use a single shared Web-application-type Client ID
# as the serverClientId, so this list typically has exactly one entry.
# Supports multiple IDs for future web clients or Google Cloud Console changes.
GOOGLE_OAUTH_ALLOWED_CLIENT_IDS = config(
    'GOOGLE_OAUTH_ALLOWED_CLIENT_IDS',
    default='',
    cast=Csv(),
)
# If no explicit list is provided, fall back to the single client ID
if not GOOGLE_OAUTH_ALLOWED_CLIENT_IDS:
    GOOGLE_OAUTH_ALLOWED_CLIENT_IDS = [GOOGLE_OAUTH_CLIENT_ID]
# Require nonce validation for replay protection.
# When True, requests without a nonce are rejected with 401.
# When False (default), requests without a nonce are allowed with a warning log.
NONCE_REQUIRED = config('NONCE_REQUIRED', cast=bool, default=False)

# M-Pesa Daraja Configuration
MPESA_CONSUMER_KEY = config('MPESA_CONSUMER_KEY', default='')
MPESA_CONSUMER_SECRET = config('MPESA_CONSUMER_SECRET', default='')
MPESA_SHORTCODE = config('MPESA_SHORTCODE', default='')
MPESA_PASSKEY = config('MPESA_PASSKEY', default='')
MPESA_ENVIRONMENT = config('MPESA_ENVIRONMENT', default='sandbox')
MPESA_CALLBACK_BASE_URL = config('MPESA_CALLBACK_BASE_URL', default='')
GUARANTOR_RESPONSE_BASE_URL = config(
    'GUARANTOR_RESPONSE_BASE_URL',
    default=MPESA_CALLBACK_BASE_URL,
)
MPESA_B2C_INITIATOR_NAME = config(
    'MPESA_B2C_INITIATOR_NAME',
    default='',
)
MPESA_B2C_SECURITY_CREDENTIAL = config(
    'MPESA_B2C_SECURITY_CREDENTIAL',
    default='',
)

# M-Pesa Reconciliation Configuration
MPESA_RECONCILIATION_THRESHOLD_MINUTES = config(
    'MPESA_RECONCILIATION_THRESHOLD_MINUTES',
    cast=int,
    default=5,
)
MPESA_MAX_RECONCILIATION_ATTEMPTS = config(
    'MPESA_MAX_RECONCILIATION_ATTEMPTS',
    cast=int,
    default=3,
)

# M-Pesa IP Allowlist Configuration
# Production: Only Safaricom production IPs (no private/sandbox ranges)
# Sandbox: Includes sandbox IPs for testing
MPESA_IP_RANGES_SANDBOX = config(
    'MPESA_IP_RANGES_SANDBOX',
    default='196.201.212.0/24,196.201.213.0/24,196.201.214.0/24,196.201.214.0/23,192.168.201.0/24',
    cast=Csv(),
)
MPESA_IP_RANGES_PRODUCTION = config(
    'MPESA_IP_RANGES_PRODUCTION',
    default='196.201.212.0/24,196.201.213.0/24,196.201.214.0/24,196.201.214.0/23',
    cast=Csv(),
)

# M-Pesa Callback Security Token
# Unguessable token for callback URL path validation
MPESA_CALLBACK_TOKEN = config('MPESA_CALLBACK_TOKEN', default='')

BILLING_ACCOUNT_NAME = config('BILLING_ACCOUNT_NAME', default='')
BILLING_ACCOUNT_NUMBER = config('BILLING_ACCOUNT_NUMBER', default='')
BILLING_PAYBILL = config('BILLING_PAYBILL', default='')
BILLING_SUPPORT_EMAIL = config('BILLING_SUPPORT_EMAIL', default='')

PLATFORM_FEES = {
    'deposit': config(
        'FEE_DEPOSIT_RATE',
        default='0.01',
        cast=lambda value: Decimal(value),
    ),
    'repayment': config(
        'FEE_REPAYMENT_RATE',
        default='0.005',
        cast=lambda value: Decimal(value),
    ),
}

_raw_disbursement_tiers = config(
    'DISBURSEMENT_TIERS',
    default=(
        '[[10000,"50"],[30000,"100"],[70000,"200"],'
        '[150000,"350"],[300000,"500"],[null,"750"]]'
    ),
)
DISBURSEMENT_TIERS = [
    (int(ceiling) if ceiling else None, Decimal(fee))
    for ceiling, fee in json.loads(_raw_disbursement_tiers)
]

_raw_withdrawal_tiers = config(
    'WITHDRAWAL_TIERS',
    default=(
        '[[2000,"15"],[5000,"25"],[10000,"40"],'
        '[20000,"60"],[null,"100"]]'
    ),
)
WITHDRAWAL_TIERS = [
    (int(ceiling) if ceiling else None, Decimal(fee))
    for ceiling, fee in json.loads(_raw_withdrawal_tiers)
]

REDIS_URL = config('REDIS_URL', default='redis://localhost:6379/0')

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

if DEBUG:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "saccosphere-dev-cache",
        }
    }
    # Sessions fall back to DB in dev so Redis isn't required locally
    SESSION_ENGINE = "django.contrib.sessions.backends.db"
else:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",  # ← switch to django_redis
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SOCKET_CONNECT_TIMEOUT": 5,
                "SOCKET_TIMEOUT": 5,
            },
            "KEY_PREFIX": "saccosphere",  # ← namespace all keys
        }
    }

CELERY_BROKER_URL = config(
    'REDIS_URL',
    default='redis://localhost:6379/0',
)
CELERY_RESULT_BACKEND = config(
    'REDIS_URL',
    default='redis://localhost:6379/0',
)
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Africa/Nairobi'
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_BEAT_SCHEDULE = {
    'generate-monthly-invoices': {
        'task': 'billing.generate_monthly_invoices',
        'schedule': crontab(minute=0, hour=0, day_of_month=1),
    },
    'check-overdue-invoices': {
        'task': 'billing.update_overdue_invoices',
        'schedule': crontab(minute=0, hour=8),
    },
    'suspend-overdue-saccos': {
        'task': 'billing.suspend_overdue_saccos',
        'schedule': crontab(minute=0, hour=9),
    },
    'reconcile-stale-mpesa-transactions': {
        'task': 'payments.tasks.reconcile_stale_mpesa_transactions',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
}

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'saccosphere.iprs': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
