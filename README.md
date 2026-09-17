# SaccoSphere

Multi-tenant backend API for managing SACCOs (Savings and Credit Co-operative
Organizations) in Kenya — member savings, loans and guarantors, M-Pesa
collections and disbursements, dividends, compliance/KYC, and platform
billing — built on Django REST Framework and deployed on Railway.

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running Tests](#running-tests)
- [API Documentation](#api-documentation)
- [Deployment on Railway](#deployment-on-railway)
- [Security & Compliance Notes](#security--compliance-notes)
- [Contributing](#contributing)
- [License](#license)

## Overview

SaccoSphere is the backend for a platform that hosts many independent SACCOs
("tenants") in one deployment. Each SACCO manages its own members, savings
products, loans, guarantors, dividends, and M-Pesa payment configuration,
while a platform-level superadmin role oversees billing, system-wide reports,
and compliance across all SACCOs. It's built for engineers operating or
extending that platform, not as a public open-source library.

## Key Features

Grounded in the actual models, views, and Celery tasks in this repository:

- **Multi-tenant SACCO management** — `Sacco`, `SaccoSettings`,
  `SaccoPaymentConfig` per tenant; SACCO-scoped request context via
  `saccomanagement.middleware.SaccoContextMiddleware`, with an optional
  strict `X-Sacco-ID` header requirement for multi-SACCO admins
  (`SaccoScopedMixin.require_sacco_header`).
- **Membership & onboarding** — `SaccoApplication` review workflow,
  `Membership`, per-SACCO custom `SaccoFieldDefinition`/`MemberFieldData`,
  membership document uploads, member CSV import (`ImportJob`,
  `MemberImportJob`).
- **KYC & identity verification** — ID submission, document upload with
  S3/local storage, IPRS ID verification integration (mockable via
  `IPRS_MOCK`), admin KYC review queue, retention-based purge jobs.
- **Savings** — configurable `SavingsType` products (minimum contribution,
  multi-account flag, dividend eligibility), deposits/withdrawals routed
  through a single ledger-backed `apply_ledger_entry` writer, account
  freeze/close/reactivate lifecycle, interest accrual
  (`services/engines/savings_interest.py`, opt-in per SACCO).
- **Loans & guarantors** — loan application, eligibility and guarantor
  capacity checks (`guarantor_logic.py`), internal member guarantor
  requests/responses plus SMS-invited `ExternalGuarantor`s, loan approval,
  disbursement via M-Pesa B2C, repayment schedules, a full loan state
  machine (`APPROVED → DISBURSED → ACTIVE → COMPLETED/DEFAULTED`),
  NPL flagging and an NPL dashboard.
- **M-Pesa integration (Daraja)** — STK Push collections, B2C
  disbursements/withdrawals, signed/unsigned callback handling with IP
  allowlisting, idempotency records for callbacks and withdrawals,
  stale-transaction reconciliation and reversal-on-failure logic.
- **Dividends** — per-`SavingsType`/financial-year dividend declarations,
  pluggable calculation strategy (`AVERAGE_MONTH_END` implemented;
  `DAY_WEIGHTED`/`MINIMUM_BALANCE` stubbed), async calculate/disburse via
  Celery, four-eyes approval control (declarer ≠ approver ≠ disburser).
- **CRB / credit checks** — Metropol CRB integration with a fail-loud mock
  toggle (`METROPOL_MOCK`) and encrypted raw-response storage with
  time-based retention.
- **Billing** — per-SACCO `SaccoSubscription`, monthly `Invoice` generation,
  overdue-invoice suspension workflow (`BillingSuspensionMiddleware`),
  platform revenue reporting, PDF invoice download.
- **Regulatory & platform reporting** — SASRA return generation
  (`saccomanagement/sasra_reports.py`), plus superadmin dashboards (system
  overview, revenue chart, top SACCOs, live transaction feed, platform
  alerts).
- **Compliance & data protection** — `UserConsent` records with versioned
  policy tracking, `DataErasureRequest` workflow, `SystemAuditLog` /
  `DataConsentLog` audit trails, and configurable retention windows for
  KYC documents, CRB raw responses, M-Pesa callbacks, and notification
  content.
- **Notifications** — in-app `Notification` feed, push notifications via
  Firebase Cloud Messaging (HTTP v1), SMS via Africa's Talking (including
  bulk SMS campaigns), read/mark-read endpoints.
- **Ledger & statements** — append-only `LedgerEntry` as the system of
  record for balances, member balance/statement endpoints, PDF statement
  generation via WeasyPrint.
- **Auth** — JWT auth (`djangorestframework-simplejwt`) with refresh-token
  rotation and blacklisting, OTP-based phone verification, password
  reset, Google OAuth (ID-token based, mobile flow), device
  registration/biometric device list.
- **Operational health** — liveness/readiness probes and a
  `JobHeartbeat`-backed job-health endpoint reporting staleness of
  scheduled Celery jobs.

## Tech Stack

| Layer | Choice |
| --- | --- |
| Language | Python (no version pinned in-repo — see note below) |
| Web framework | Django 5.2 + Django REST Framework 3.17 |
| Auth | `djangorestframework-simplejwt` (JWT, rotation + blacklist) |
| Database | PostgreSQL in production (`dj-database-url` parses `DATABASE_URL`); SQLite locally by default |
| Cache / broker | Redis (`django-redis` for cache, Celery broker + result backend) |
| Task queue | Celery 5.6, with `django-celery-beat` (database-backed schedule) |
| API docs | `drf-yasg` (Swagger UI / ReDoc), staff-only |
| File storage | Local filesystem by default; S3 via `django-storages`/`boto3` when `STORAGE_BACKEND=s3` |
| Static files | WhiteNoise (compressed manifest storage) |
| PDF generation | WeasyPrint (member statements, invoices) |
| SMS | Africa's Talking |
| Push notifications | Firebase Cloud Messaging (HTTP v1 API) |
| Payments | Safaricom Daraja (M-Pesa STK Push + B2C) |
| Error tracking | Sentry (`sentry-sdk`, production only, opt-in via `SENTRY_DSN`) |
| Load testing | Locust (`locustfile.py`) |
| WSGI server | Gunicorn |
| Deployment | Railway (Nixpacks build) |

**Note on Python version:** no `.python-version`, `runtime.txt`, or
`pyproject.toml` pin was found in the repository, so Nixpacks selects a
Python version automatically at build time — confirm the resolved version
before relying on it for anything version-sensitive.

## Project Structure

```
saccosphere-project/
├── config/            # Settings (base/development/production), URLs, Celery app,
│                       # middleware, custom pagination/exception handling
├── accounts/           # Users, Sacco/SaccoSettings, auth, OTP, KYC, consent, data erasure
├── saccomembership/    # Membership lifecycle, SACCO applications, custom member fields
├── saccomanagement/    # Admin/superadmin tooling: roles, audit log, bulk SMS, reports,
│                       # dividend admin routes, member import, SASRA returns
├── services/           # Core domain: savings, loans, guarantors, CRB checks, dividends, NPL
├── guarantor/          # External (non-member) guarantor invite/response flow
├── payments/           # M-Pesa STK/B2C, transactions, callbacks, withdrawal idempotency
├── ledger/             # Append-only ledger entries, balances, statements (incl. PDF)
├── notifications/      # In-app notifications, device tokens, push/SMS delivery
├── billing/            # Platform subscriptions, invoicing, revenue reporting
├── dashboard/          # Member-facing dashboard/portfolio/activity feed endpoints
├── health/             # Liveness/readiness/job-health endpoints
├── docs/               # Design/compliance/runbook notes (not auto-generated)
├── templates/          # Django templates (emails, PDFs, etc.)
├── tests/              # Cross-app / integration tests
├── manage.py
├── requirements.txt
├── Procfile            # Railway process definitions: web, worker, beat
├── nixpacks.toml       # Native packages (WeasyPrint deps) for the Nixpacks build
├── build.sh            # install → collectstatic → migrate (not referenced by Procfile)
└── docker-compose.test.yml  # Postgres service for Postgres-only tests
```

## Getting Started

### Prerequisites

- Python 3.11+ (see version note above — not pinned in-repo)
- Redis (required for Celery and, outside `DEBUG`, for cache/sessions; not
  required to run the dev server itself since dev settings use in-memory
  cache and DB-backed sessions)
- PostgreSQL (optional for local dev — SQLite is the default; required for
  the Postgres-only concurrency tests)
- A virtual environment tool (`venv`, etc.)

### Setup

```bash
git clone <repository-url>
cd SaccoSphere/saccosphere-project

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env            # then edit values as needed
```

By default `manage.py` sets `DJANGO_SETTINGS_MODULE=config.settings.development`,
which forces SQLite (`db.sqlite3`) and an in-memory cache regardless of
`DATABASE_URL` — no local Postgres/Redis setup is required to get started.

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

To run against production-like settings locally (Postgres/Redis required):

```bash
export DJANGO_SETTINGS_MODULE=config.settings.production
export SECRET_KEY=dev-secret
python manage.py migrate
python manage.py runserver
```

### Background jobs (optional, for payment/dividend/notification flows)

```bash
celery -A config.celery worker -Q payments,notifications,reports,default -l info
celery -A config.celery beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Environment Variables

All variables are read via `python-decouple` in `config/settings/base.py`
(with overrides in `development.py`/`production.py`). See `.env.example` for
the full annotated list — the table below covers the ones that matter most
for getting the app running; placeholders only.

| Name | Description | Required | Example |
| --- | --- | --- | --- |
| `SECRET_KEY` | Django secret key | Required in production (`config.settings.production` reads it with no default) | `change-me` |
| `DEBUG` | Enables debug mode | Optional (default `False`) | `True` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | Required outside DEBUG | `localhost,127.0.0.1` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated CORS origins | Optional | `http://localhost:3000` |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins for CSRF | Required in production if serving cross-origin admin/session traffic | `https://your-domain.com` |
| `DATABASE_URL` | Full DB connection string, parsed by `dj-database-url` | Optional locally (defaults to SQLite); required in production | `postgres://user:pass@host:5432/db` |
| `REDIS_URL` | Redis connection string (cache + Celery broker/result backend) | Required outside DEBUG | `redis://127.0.0.1:6379/0` |
| `WEB_CONCURRENCY` | Gunicorn worker count | Optional (default 3) | `3` |
| `GUNICORN_TIMEOUT` | Gunicorn per-request timeout (s) | Optional (default 60) | `60` |
| `CELERY_WORKER_CONCURRENCY` | Celery worker pool size | Optional (default 4) | `4` |
| `FIELD_ENCRYPTION_KEY` | Fernet key for `EncryptedCharField`/`EncryptedJSONField` (payment configs, CRB raw responses, M-Pesa callback payloads) | Required for any encrypted-field data to work | output of `Fernet.generate_key()` |
| `OTP_HASH_KEY` | HMAC key for OTP hashing; falls back to `SECRET_KEY`-derived value if unset | Recommended in production | random string |
| `MPESA_CONSUMER_KEY` / `MPESA_CONSUMER_SECRET` | Daraja app credentials | Required for real M-Pesa calls | `change-me` |
| `MPESA_SHORTCODE` / `MPESA_PASSKEY` | Daraja STK push shortcode/passkey | Optional (Safaricom sandbox defaults provided) | `174379` |
| `MPESA_ENVIRONMENT` | `sandbox` or `production` | Optional (default `sandbox`) | `sandbox` |
| `MPESA_CALLBACK_BASE_URL` | Public base URL Daraja calls back to | Required for STK/B2C to work end-to-end | `https://your-ngrok-url` |
| `MPESA_B2C_INITIATOR_NAME` / `MPESA_B2C_SECURITY_CREDENTIAL` | B2C disbursement credentials | Required for B2C disbursement/withdrawal | `change-me` |
| `MPESA_CALLBACK_TOKEN` | Unguessable token embedded in callback URL paths | Recommended in production | random string |
| `AT_USERNAME` / `AT_API_KEY` | Africa's Talking SMS credentials | Required for SMS delivery | `sandbox` / blank |
| `FCM_PROJECT_ID` / `FCM_CREDENTIALS_JSON` | Firebase service-account credentials (HTTP v1) | Required for push notifications | blank |
| `IPRS_API_KEY` / `IPRS_API_URL` / `IPRS_MOCK` | ID verification integration | `IPRS_MOCK=True` by default | see `.env.example` |
| `METROPOL_API_KEY` / `METROPOL_API_URL` / `METROPOL_MOCK` | CRB integration | `METROPOL_MOCK` **must be set explicitly** when `DEBUG=False` — app refuses to start otherwise | see `.env.example` |
| `STORAGE_BACKEND` | `local` or `s3` | Optional (default `local`) | `s3` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_STORAGE_BUCKET_NAME` | S3 storage credentials | Required if `STORAGE_BACKEND=s3` | — |
| `SENTRY_DSN` | Sentry error tracking (production only) | Optional | blank |
| `KYC_RETENTION_DAYS` / `CRB_RAW_RESPONSE_RETENTION_DAYS` / `CALLBACK_RETENTION_DAYS` / `NOTIFICATION_CONTENT_RETENTION_DAYS` / `MPESA_IDEMPOTENCY_RETENTION_DAYS` | Data retention windows for purge jobs | Optional (have defaults; some flagged in code as compliance placeholders pending legal sign-off) | see `.env.example` |
| `BILLING_ACCOUNT_NAME` / `BILLING_ACCOUNT_NUMBER` / `BILLING_PAYBILL` / `BILLING_SUPPORT_EMAIL` | Platform billing collection details | Optional | see `.env.example` |
| `DISBURSEMENT_TIERS` / `WITHDRAWAL_TIERS` / `FEE_DEPOSIT_RATE` / `FEE_REPAYMENT_RATE` | Fee schedule configuration (JSON tiers / decimal rates) | Optional (defaults set) | see `.env.example` |

## Running Tests

```bash
python manage.py test
```

- Tests run under whatever `DJANGO_SETTINGS_MODULE` is active (defaults to
  `config.settings.development`, i.e. SQLite).
- A handful of concurrency tests require PostgreSQL and **self-skip on
  SQLite** rather than failing — e.g.
  `accounts/tests/test_otp_security.py` checks `connection.vendor ==
  'sqlite'` and calls `self.skipTest(...)` for the OTP race-condition
  tests, since SQLite doesn't enforce partial unique constraints the same
  way Postgres does. Similar patterns exist in
  `payments/tests/test_payments.py`, `payments/tests/test_savings_withdrawal.py`,
  `payments/tests/test_withdrawal_reconciliation.py`,
  `ledger/tests/test_concurrency.py`, `services/tests/test_dividend_calculator.py`,
  `services/tests/test_concurrency_limits.py`, `saccomanagement/tests/test_bulk_sms.py`,
  and `billing/tests.py`.
- To actually exercise those tests, bring up the provided Postgres
  container and point the app at it (see `POSTGRES_TEST_SETUP.md`):

  ```bash
  docker-compose -f docker-compose.test.yml up -d
  export DATABASE_URL=postgresql://saccosphere:saccosphere_test_pass@localhost:5433/saccosphere_test
  python manage.py test accounts.tests.test_otp_security.OTPRaceConditionTestCase -v 2
  ```
- No `pytest.ini`/`tox.ini`/CI workflow was found in the repository — tests
  are run via Django's own test runner (`manage.py test`), not `pytest`,
  despite `pytest` being listed in `requirements.txt`. Confirm before
  assuming a `pytest`-based workflow.

## API Documentation

- Interactive docs are generated by `drf-yasg` at `/swagger/` (Swagger UI)
  and `/redoc/` (ReDoc).
- The schema is **not public**: it's session-authenticated and restricted
  to `IsAdminUser` (`public=False`, `SessionAuthentication`), because it
  enumerates every endpoint including staff/superadmin-only views. Log in
  via `/admin/login/` first, then browse `/swagger/` or `/redoc/` in the
  same browser session.
- All application endpoints are namespaced under `/api/v1/`:

  | Prefix | App | Covers |
  | --- | --- | --- |
  | `/api/v1/accounts/` | `accounts` | Register/login/JWT, OTP, KYC, password reset, consent, data erasure, device management, Google OAuth |
  | `/api/v1/members/` | `saccomembership` | Memberships, SACCO custom fields, membership documents |
  | `/api/v1/management/` and `/api/v1/saccomanagement/` | `saccomanagement` | Role management, audit log, admin dashboards, KYC/loan approval queues, bulk SMS, dividend admin, member import, SASRA returns, superadmin views |
  | `/api/v1/services/` | `services` | Savings types/accounts, loans, guarantors, CRB checks, dividends |
  | `/api/v1/payments/` | `payments` | STK push, B2C disbursement/withdrawal, transactions, M-Pesa callbacks |
  | `/api/v1/guarantors/` | `guarantor` | External (non-member) guarantor response flow |
  | `/api/v1/notifications/` | `notifications` | Notification feed, device token registration |
  | `/api/v1/ledger/` | `ledger` | Ledger entries, balance, statements (JSON + PDF) |
  | `/api/v1/dashboard/` | `dashboard` | Portfolio, activity feed, SACCO switcher |
  | `/api/v1/billing/` | `billing` | Invoices, revenue summary, billing exemptions |
  | `/api/v1/health/` and `/health/` | `health` | Liveness, readiness, job-health |

## Deployment on Railway

Deployment is Nixpacks-based (no Dockerfile in the repo). Confirmed from
`Procfile`, `nixpacks.toml`, `build.sh`, and `config/settings/production.py`:

- **Three separate Railway services** should run from this repository:

  | Service | Start command (from `Procfile`) |
  | --- | --- |
  | Web/API | `python manage.py migrate && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --workers=${WEB_CONCURRENCY:-3} --timeout=${GUNICORN_TIMEOUT:-60} --graceful-timeout=30` |
  | Worker | `celery -A config.celery worker -Q payments,notifications,reports,default -l info --concurrency=${CELERY_WORKER_CONCURRENCY:-4}` |
  | Beat | `celery -A config.celery beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler` |

  Only the web service needs a public domain. All three must set
  `DJANGO_SETTINGS_MODULE=config.settings.production` and share the same
  `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, and application env vars.
- **Build**: Nixpacks. `nixpacks.toml` declares native packages
  (`pango`, `cairo`, `gdk-pixbuf`, `libffi`, `shared-mime-info`,
  `fontconfig`) required by WeasyPrint for PDF generation — without them,
  the PDF statement/invoice endpoints degrade to
  `503 PDF generation temporarily unavailable.` rather than failing the
  build. `build.sh` (install → `collectstatic` → `migrate`) exists in the
  repo but is **not referenced by the Procfile**, which runs its own
  `migrate`/`collectstatic` inline — confirm which one Railway is actually
  configured to use before relying on `build.sh`.
- **Database**: PostgreSQL via `DATABASE_URL`, parsed by `dj-database-url`
  in `config/settings/base.py` (`conn_max_age=60`, `conn_health_checks=True`).
- **Redis**: required outside `DEBUG` for cache, sessions
  (`SESSION_ENGINE` falls back to DB-backed sessions only in `DEBUG`), and
  as the Celery broker/result backend (`REDIS_URL`, shared by all three
  services).
- **Static files**: served via WhiteNoise
  (`whitenoise.middleware.WhiteNoiseMiddleware` +
  `CompressedManifestStaticFilesStorage`); `collectstatic` runs as part of
  the web service's start command on every deploy.
- **Migrations**: run inline in the web service's start command on every
  boot. `production.py`'s own comment (mirrored in the pre-existing
  README content) flags that this is only safe with a single web replica —
  if scaled to multiple replicas, move `migrate` into Railway's
  **Pre-Deploy Command** instead, so it runs once per deploy rather than
  once per replica.
- **Reverse proxy awareness**: `SECURE_PROXY_SSL_HEADER =
  ('HTTP_X_FORWARDED_PROTO', 'https')` and `USE_X_FORWARDED_HOST = True`
  are set for Railway's proxy; `config/utils.py` has a
  Railway-internal-network-aware `get_client_ip()` used for M-Pesa IP
  allowlisting and rate limiting.
- **File storage**: local disk by default; can be switched to S3 via
  `STORAGE_BACKEND=s3` (`django-storages`, with optional SSE-KMS via
  `AWS_KMS_KEY_ID`) for KYC documents and other media.
- **Error tracking**: Sentry initializes automatically in
  `config/settings/production.py` if `SENTRY_DSN` is set (Django, Celery,
  and Redis integrations; `send_default_pii=False`).
- **Health checks**: `/health/`, `/health/ready/` (DB + cache check),
  `/health/jobs/` (staleness of scheduled Celery jobs) — also duplicated
  under `/api/v1/health/...` for API clients. No `railway.json`/
  `railway.toml` was found specifying which path Railway's own healthcheck
  targets — confirm in the Railway dashboard before assuming one is wired
  up.
- **Custom domain**: not found in-repo (Railway domain config isn't
  version-controlled) — confirm directly in the Railway project settings.

## Security & Compliance Notes

Factual only, based on what the code actually does:

- Secrets (Daraja keys, Firebase credentials, `SECRET_KEY`,
  `FIELD_ENCRYPTION_KEY`, etc.) are read from environment variables via
  `python-decouple`; `.env` is gitignored and only `.env.example` (with
  placeholder values) is committed.
- Sensitive fields — SACCO M-Pesa payment credentials, CRB raw bureau
  responses, M-Pesa callback raw payloads (which carry member phone
  numbers and, for B2C, names) — are stored using custom
  `EncryptedCharField`/`EncryptedJSONField` model fields
  (`accounts/models.py`), keyed by `FIELD_ENCRYPTION_KEY`. Encryption is
  explicitly documented in-code as not a substitute for a retention
  policy: several `*_RETENTION_DAYS` settings exist to purge this data on
  a schedule.
- KYC documents are uploaded with rate limiting
  (`KYC_UPLOAD_USER_RATE`/`KYC_UPLOAD_IP_RATE`) and served through a
  tokenized URL (`kyc/documents/<kyc_id>/<field>/<token>/`) rather than
  directly from storage.
- `METROPOL_MOCK` (CRB) is fail-loud: the app refuses to start outside
  `DEBUG` unless it's set explicitly, to prevent silently issuing fake
  credit scores in production or silently hitting the real bureau from a
  non-production box.
- `UserConsent` and `DataConsentLog` track consent by type and version
  (`CONSENT_POLICY_VERSIONS`); `DataErasureRequest` implements a
  request/review workflow for data-subject erasure requests. Several
  retention windows in `config/settings/base.py`
  (`CALLBACK_RETENTION_DAYS`, `NOTIFICATION_CONTENT_RETENTION_DAYS`) are
  explicitly commented in-code as **placeholder values pending
  compliance/legal sign-off** — do not treat the current defaults as an
  approved retention policy.
- `SystemAuditLog` records administrative and financial actions (loan
  approvals, dividend approvals/disbursements, KYC review decisions,
  etc.) for audit purposes.
- Password validation uses Django's standard validators; JWTs are
  short-lived (15 min access / 7 day refresh) with rotation and
  blacklisting on refresh.
- This is a factual summary of implemented controls, not a compliance
  certification — no claim is made here (or should be inferred) about
  formal regulatory approval (e.g. SASRA, Kenya DPA 2019) beyond what's
  described above.

Also present in the repo but not verified/incorporated above: several
standalone audit/review documents (`PRE_PRODUCTION_AUDIT_REPORT.md`,
`docs/SACCO_MANAGEMENT_AUDIT_REPORT.md`,
`docs/KYC_RETENTION_COMPLIANCE_ASSUMPTIONS.md`,
`docs/CONSENT_TYPE_TAXONOMY_REVIEW.md`, `docs/S3_SECURITY_CHECKLIST.md`,
and others under `docs/`) — worth reading directly for deeper context on
specific subsystems; some may describe past findings that have since been
fixed, so treat them as historical record rather than current state
without cross-checking the code.

## Contributing

No `CONTRIBUTING.md` or PR/issue template was found in the repository —
there's no documented contribution process to link to here.

## License

No `LICENSE` file was found in the repository. Do not assume a license
(e.g. MIT) applies — confirm licensing terms with the repository owner
before reuse or distribution.
