# SaccoSphere

> A multi-tenant SACCO management backend — savings, loans, insurance, and payment processing across multiple cooperative organizations on a single platform.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.2.3-092E20?style=flat-square&logo=django&logoColor=white)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.16.0-092E20?style=flat-square&logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Production-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![JWT](https://img.shields.io/badge/Auth-JWT-000000?style=flat-square&logo=jsonwebtokens&logoColor=white)](https://jwt.io/)
[![Deployed on Render](https://img.shields.io/badge/Deployed-Render-46E3B7?style=flat-square&logo=render&logoColor=white)](https://render.com/)
[![Swagger](https://img.shields.io/badge/API_Docs-Swagger-85EA2D?style=flat-square&logo=swagger&logoColor=black)](https://saccosphere_backend.onrender.com/swagger/)

**Live API:** `https://saccosphere_backend.onrender.com/api/`
&nbsp;·&nbsp;
**API Docs (Swagger):** `https://saccosphere_backend.onrender.com/swagger/`

---

## Overview

SACCOs (Savings and Credit Cooperatives) typically operate in silos — each running its own spreadsheets, manual ledgers, or disconnected software. SaccoSphere puts multiple SACCOs on one unified backend, letting each operate independently while sharing infrastructure.

The platform handles the full financial lifecycle: member enrollment with per-SACCO custom registration fields, savings deposits and withdrawals, loan applications and approvals, insurance policy management, and payment processing through M-Pesa, Airtel Money, and bank integrations.

---

## Architecture

### Modular Monolith with Domain-Driven Design

The codebase is split into domain-focused Django apps with clear boundaries and no cross-app business logic leakage:

```
saccosphere_backend/
├── accounts/          # User management, auth, SACCO registration
├── saccomembership/   # Member enrollment, dynamic fields, approval workflows
├── saccomanagement/   # SACCO admin operations, verification
├── services/          # Financial services — savings, loans, insurance
└── payments/          # Payment providers, transactions, callback handling
```

### Multi-Tenancy Strategy

SaccoSphere uses a **shared database, shared schema** model with logical tenant isolation:

```
┌─────────────────────────────────────────────────────┐
│                   Single Database                   │
│                                                     │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────┐  │
│  │   SACCO A    │   │   SACCO B    │   │ SACCO C │  │
│  │  (Members)   │   │  (Members)   │   │(Members)│  │
│  │  (Fields)    │   │  (Fields)    │   │(Fields) │  │
│  │  (Services)  │   │  (Services)  │   │(Svc)    │  │
│  └──────────────┘   └──────────────┘   └─────────┘  │
│         └──────────────────┴──────────────┘          │
│                  FK-based isolation                  │
└─────────────────────────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │   Django REST API       │
              │  (JWT + RBAC + CORS)    │
              └────────────┬────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌──────▼──────┐  ┌─────▼──────┐
   │  Financial  │  │  Membership │  │  Payment   │
   │  Services   │  │  & Fields   │  │ Providers  │
   └─────────────┘  └─────────────┘  └────────────┘
                                            │
                         ┌──────────────────┼──────────────┐
                         │                  │              │
                    ┌────▼───┐        ┌─────▼──┐    ┌──────▼─┐
                    │ M-Pesa │        │ Airtel │    │  Bank  │
                    └────────┘        └────────┘    └────────┘
```

Each SACCO's data is isolated through foreign key relationships — no row-level security or separate schemas needed, while still providing full logical separation between tenants.

---

## Key Features

- **Multi-Tenant Platform** — multiple SACCOs share infrastructure with full data isolation per organization
- **Dynamic Registration Fields** — each SACCO defines its own membership form fields (text, number, date, file) with ordering and validation
- **Membership Lifecycle** — application → admin approval → active membership, with status tracking (`pending`, `approved`, `rejected`, `left`)
- **Financial Services** — savings (deposit/withdrawal), loan management (application → approval → disbursement → repayment), and insurance policy management
- **Multi-Provider Payments** — pluggable payment provider model supporting M-Pesa, Airtel Money, and bank integrations with callback handling
- **JWT Auth with Rotation** — short-lived access tokens (5 min), long-lived refresh tokens (1 day), automatic rotation, and blacklisting on logout
- **Swagger/OpenAPI Docs** — auto-generated API documentation via drf-yasg available at `/swagger/`
- **Production Deployed** — live on Render with PostgreSQL, Gunicorn, WhiteNoise static serving, and Git-triggered CI/CD
- **Security Hardened** — CSP headers, CORS restrictions, bcrypt password hashing, and DRF serializer validation on all inputs

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Django 5.2.3 + Django REST Framework 3.16.0 |
| Authentication | JWT (SimpleJWT 5.5.1) — rotation + blacklisting |
| Database | SQLite (dev) · PostgreSQL (production) |
| Deployment | Render (PaaS) with Gunicorn 23.0.0 |
| Static Files | WhiteNoise with compression |
| API Docs | drf-yasg (Swagger/OpenAPI) |
| Security | django-csp 4.0 · bcrypt 4.3.0 · django-cors-headers 4.7.0 |
| Cryptography | cryptography 45.0.4 |

---

## Database Schema

### Core Entity Relationships

```
User (UUID PK, email-based)
 ├── Profile (1:1)
 │    └── phone_number, profile_picture, bio
 │
 └── Membership (1:many — user joins multiple SACCOs)
      ├── status: pending | approved | rejected | left
      └── MembershipFieldData (dynamic field responses)

Sacco (UUID PK)
 ├── Membership (1:many — sacco has many members)
 └── SaccoField (1:many — dynamic registration fields per SACCO)
      └── field_type: text | number | date | email | file

Services
 ├── Saving  → member, service, amount, transaction_type (deposit/withdrawal)
 ├── Loan    → member, service, amount, interest_rate, duration_months, status
 └── Insurance → member, policy_number, coverage_amount, premium, start/end dates

Payments
 ├── PaymentProvider → name, provider_code, api_key, callback_url
 ├── Transaction → user, provider, amount, status (PENDING/SUCCESS/FAILED/CANCELLED)
 └── Callback → transaction, provider, payload (JSONField), processed flag
```

**Key design choices:**
- All primary keys are UUIDs — prevents enumeration of members, transactions, and financial records
- `MembershipFieldData` allows each SACCO to collect arbitrary structured data at registration without schema migrations
- `Callback` model stores raw provider payloads as JSON, decoupling ingestion from processing

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL (for production parity) or SQLite (development)

### Installation

```bash
# Clone the repository
git clone https://github.com/Reagan-dev/SaccoSphere.git
cd SaccoSphere/saccosphere-project/saccosphere_backend

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root. See `.env.example` for all required variables:

```env
# Django
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database (leave blank to use SQLite in development)
DATABASE_URL=postgresql://user:password@localhost:5432/saccosphere_db

# JWT
ACCESS_TOKEN_LIFETIME_MINUTES=5
REFRESH_TOKEN_LIFETIME_DAYS=1

# CORS
CORS_ALLOWED_ORIGINS=http://localhost:3000

# Payment Providers (add as needed)
MPESA_CONSUMER_KEY=your-key
MPESA_CONSUMER_SECRET=your-secret
MPESA_CALLBACK_URL=https://yourdomain.com/api/payments/callback/
```

### Run the Application

```bash
# Apply migrations
python manage.py migrate

# (Optional) Load seed data
python manage.py loaddata initial_data

# Create a superuser
python manage.py createsuperuser

# Start the development server
python manage.py runserver
```

API available at `http://localhost:8000`
Swagger docs at `http://localhost:8000/swagger/`

---

## API Overview

All endpoints under `/api/`. Full interactive documentation at `/swagger/`.

### Authentication (`/api/accounts/`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/accounts/register/` | Register a new user | No |
| `POST` | `/accounts/login/` | Login, returns JWT tokens | No |
| `POST` | `/accounts/logout/` | Blacklist refresh token | Required |
| `GET/POST` | `/accounts/saccos/` | List / create SACCOs | Admin for POST |
| `GET/PUT/DELETE` | `/accounts/saccos/{id}/` | SACCO detail operations | Admin |
| `GET/PUT` | `/accounts/profiles/{id}/` | User profile management | Required |

### Membership (`/api/members/`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET/POST` | `/members/memberships/` | List / apply for membership | Required |
| `GET/PUT/DELETE` | `/members/memberships/{id}/` | Membership detail | Required |
| `GET/POST` | `/members/sacco_fields/` | Dynamic field configuration | Admin |
| `GET/PUT/DELETE` | `/members/sacco_fields/{id}/` | Field management | Admin |

### Financial Services (`/api/services/`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET/POST` | `/services/savings/` | Savings deposits and withdrawals | Required |
| `GET/POST` | `/services/loans/` | Loan applications | Required |
| `GET/PUT` | `/services/loans/{id}/` | Loan status and management | Required |
| `GET/POST` | `/services/insurance/` | Insurance policy management | Required |

### Payments (`/api/payments/`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET/POST` | `/payments/transactions/` | Initiate and list transactions | Required |
| `GET` | `/payments/transactions/{id}/` | Transaction status | Required |
| `POST` | `/payments/callback/{provider}/` | Payment provider webhook | Provider |

### Example: Register and Join a SACCO

```bash
# 1. Register a user
curl -X POST http://localhost:8000/api/accounts/register/ \
  -H "Content-Type: application/json" \
  -d '{"email": "member@example.com", "password": "securepass123",
       "first_name": "Jane", "last_name": "Doe"}'

# 2. Login to get tokens
curl -X POST http://localhost:8000/api/accounts/login/ \
  -H "Content-Type: application/json" \
  -d '{"email": "member@example.com", "password": "securepass123"}'

# 3. Apply for SACCO membership
curl -X POST http://localhost:8000/api/members/memberships/ \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"sacco": "<sacco-uuid>"}'
```

---

## Key Workflows

### Member Onboarding
```
Register account → Complete profile → Browse SACCOs
    → Apply for membership (fill dynamic fields)
        → Admin review → Approved → Access financial services
```

### Loan Lifecycle
```
Member applies → PENDING
    → Admin reviews → APPROVED or REJECTED
        → If approved: funds disbursed
            → Member repays → PAID
```

### Payment Processing
```
Transaction initiated via API
    → Provider (M-Pesa / Airtel / Bank) processes payment
        → Provider sends callback to /api/payments/callback/{provider}/
            → Callback stored (raw JSON) → Processed → Transaction status updated
```

---

## Deployment

SaccoSphere is deployed on **Render** with automatic deployments on push to `main`.

```yaml
# render.yaml
services:
  - type: web
    name: saccosphere_backend
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn saccosphere_backend.wsgi:application
```

The build pipeline runs on every `git push main`: installs dependencies → collects static files → starts Gunicorn workers.

**Live base URL:** `https://saccosphere_backend.onrender.com/api/`

---

## Security

- **Custom user model** — email-based auth, no username
- **JWT with token blacklisting** — tokens invalidated server-side on logout
- **CSP headers** — Content Security Policy via django-csp
- **CORS** — restricted to configured allowed origins
- **bcrypt** — password hashing beyond Django defaults
- **DRF serializers** — input validation on every endpoint
- **ORM parameterized queries** — SQL injection protection by default
- **UUID primary keys** — prevents enumeration attacks on members and financial records
- **Environment-based config** — no secrets in source code

---

## Design Decisions

**Why shared schema multi-tenancy?** Separate databases per SACCO adds operational complexity (migrations, backups, connection pools) at the scale SaccoSphere targets. Logical FK-based isolation is simpler to operate, easier to query across tenants for admin purposes, and sufficient for cooperative-scale data volumes.

**Why dynamic `SaccoField` + `MembershipFieldData`?** Each SACCO has different regulatory requirements for member registration. Hardcoding these fields would require schema migrations every time a new SACCO onboards. The `SaccoField` model lets SACCO admins define their own form — field type, label, required flag, display order — without touching code.

**Why store raw callback payloads as JSON?** Payment provider webhook formats change. Storing the raw payload decouples receipt from processing — if the parsing logic has a bug, the original data is preserved and can be reprocessed without the provider re-sending.

**Why UUID primary keys?** Sequential integer IDs on loan records or member IDs would let anyone with API access enumerate all records by incrementing IDs. UUIDs are non-guessable.

---

## Project Structure

```
SaccoSphere/
└── saccosphere-project/
    └── saccosphere_backend/
        ├── accounts/              # User model, auth, SACCO model
        ├── saccomembership/       # Membership, dynamic fields, field data
        ├── saccomanagement/       # Admin operations, SACCO verification
        ├── services/              # Savings, Loan, Insurance models + views
        ├── payments/              # PaymentProvider, Transaction, Callback
        ├── saccosphere_backend/   # Django settings, root URLs, WSGI
        ├── requirements.txt
        ├── .env.example
        ├── render.yaml
        └── manage.py
```

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

*Built with Django REST Framework · PostgreSQL · Deployed on Render*
