# SaccoSphere API Documentation

**Version:** v1.0.0  
**Base URL:** `https://api.saccosphere.com/api/v1`  
**Authentication:** JWT Bearer Token

---

## Table of Contents

- [Health Endpoints](#health-endpoints)
- [Authentication & Accounts](#authentication--accounts)
- [SACCO Membership](#sacco-membership)
- [SACCO Management](#sacco-management)
- [Services (Loans & Savings)](#services-loans--savings)
- [Payments (M-Pesa)](#payments-m-pesa)
- [Ledger & Statements](#ledger--statements)
- [Notifications](#notifications)
- [Billing](#billing)
- [Dashboard](#dashboard)

---

## Health Endpoints

### 1. Health Check (Liveness)

**Endpoint Overview**
- **Method:** GET
- **URL:** `/health/` or `/api/v1/health/`
- **Description:** Basic liveness check to verify the API is running
- **Authentication Required:** No

**Request Details**
- **Headers:** None required
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "status": "ok"
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/health/
```

**Postman Request Breakdown**
- Method: GET
- URL: `https://api.saccosphere.com/health/`
- Headers: None
- Body: None

**What to Check**
- Response status should be 200
- Response body should contain `{"status": "ok"}`

---

### 2. Readiness Check

**Endpoint Overview**
- **Method:** GET
- **URL:** `/health/ready/` or `/api/v1/health/ready/`
- **Description:** Checks if the API is ready to accept requests (database and cache)
- **Authentication Required:** No

**Request Details**
- **Headers:** None required
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "status": "ok",
  "checks": {
    "database": true,
    "cache": true
  }
}
```

**Error Response (503 Service Unavailable)**
```json
{
  "status": "unavailable",
  "checks": {
    "database": false,
    "cache": true
  }
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/health/ready/
```

**Postman Request Breakdown**
- Method: GET
- URL: `https://api.saccosphere.com/health/ready/`
- Headers: None
- Body: None

**What to Check**
- Response status should be 200 when all checks pass
- Response status should be 503 when any check fails
- Response body should show status of each dependency

---

## Authentication & Accounts

### 3. User Registration

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/register/`
- **Description:** Register a new user account
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "email": "user@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "phone_number": "+254712345678",
  "password": "SecurePass123",
  "password2": "SecurePass123"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | Yes | User email address |
| first_name | string | Yes | User first name |
| last_name | string | Yes | User last name |
| phone_number | string | Yes | Kenyan phone number (E.164 format) |
| password | string | Yes | Password (min 8 chars, 1 uppercase, 1 lowercase, 1 digit) |
| password2 | string | Yes | Password confirmation |

**Response Details**

**Success Response (201 Created)**
```json
{
  "success": true,
  "message": "User registered successfully",
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "phone_number": "+254712345678",
    "profile_picture": null,
    "date_of_birth": null,
    "date_joined": "2024-01-01T00:00:00Z"
  }
}
```

**Error Response (400 Bad Request)**
```json
{
  "success": false,
  "message": "Validation error",
  "errors": {
    "email": ["This field is required."],
    "password": ["Password must be at least 8 characters long."]
  }
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/register/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "phone_number": "+254712345678",
    "password": "SecurePass123",
    "password2": "SecurePass123"
  }'
```

**Postman Request Breakdown**
- Method: POST
- URL: `https://api.saccosphere.com/api/v1/accounts/register/`
- Headers: `Content-Type: application/json`
- Body: Raw JSON with user registration data

**What to Check**
- Response status should be 201
- Response should contain user data
- KYC verification record should be created with NOT_STARTED status

---

### 4. User Login

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/login/`
- **Description:** Authenticate user and receive JWT tokens
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "email": "user@example.com",
  "password": "SecurePass123"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | Yes | User email address |
| password | string | Yes | User password |

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": "Login successful",
  "data": {
    "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
    "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
    "user": {
      "id": "uuid",
      "email": "user@example.com",
      "first_name": "John",
      "last_name": "Doe",
      "phone_number": "+254712345678",
      "profile_picture": null,
      "date_of_birth": null,
      "date_joined": "2024-01-01T00:00:00Z"
    }
  }
}
```

**Error Response (401 Unauthorized)**
```json
{
  "success": false,
  "message": "Invalid email or password",
  "errors": null,
  "error_code": "UNAUTHORIZED",
  "status_code": 401
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/login/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123"
  }'
```

**Postman Request Breakdown**
- Method: POST
- URL: `https://api.saccosphere.com/api/v1/accounts/login/`
- Headers: `Content-Type: application/json`
- Body: Raw JSON with email and password

**What to Check**
- Response status should be 200
- Response should contain access and refresh tokens
- Save access token for authenticated requests

---

### 5. User Logout

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/logout/`
- **Description:** Logout user and blacklist refresh token
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| refresh | string | Yes | Refresh token to blacklist |

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": "Logged out successfully",
  "data": null
}
```

**Error Response (400 Bad Request)**
```json
{
  "success": false,
  "message": "Refresh token is required",
  "errors": {
    "refresh": "This field is required."
  }
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/logout/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
  }'
```

**Postman Request Breakdown**
- Method: POST
- URL: `https://api.saccosphere.com/api/v1/accounts/logout/`
- Headers: `Content-Type: application/json`, `Authorization: Bearer <token>`
- Body: Raw JSON with refresh token

**What to Check**
- Response status should be 200
- Refresh token should be blacklisted
- Token should no longer be usable for refresh

---

### 6. Get JWT Token

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/token/`
- **Description:** Obtain JWT access and refresh tokens (standard DRF endpoint)
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "email": "user@example.com",
  "password": "SecurePass123"
}
```

**Response Details**

**Success Response (200 OK)**
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/token/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123"
  }'
```

---

### 7. Refresh JWT Token

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/token/refresh/`
- **Description:** Refresh access token using refresh token
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
}
```

**Response Details**

**Success Response (200 OK)**
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/token/refresh/ \
  -H "Content-Type: application/json" \
  -d '{
    "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
  }'
```

---

### 8. Get Current User Profile

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/accounts/me/`
- **Description:** Get authenticated user's profile information
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "phone_number": "+254712345678",
    "profile_picture": null,
    "date_of_birth": null,
    "date_joined": "2024-01-01T00:00:00Z"
  }
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/accounts/me/ \
  -H "Authorization: Bearer <access_token>"
```

**Postman Request Breakdown**
- Method: GET
- URL: `https://api.saccosphere.com/api/v1/accounts/me/`
- Headers: `Authorization: Bearer <token>`
- Body: None

**What to Check**
- Response status should be 200
- Response should contain user profile data
- Read-only fields (id, email, date_joined) should be present

---

### 9. Update Current User Profile

**Endpoint Overview**
- **Method:** PATCH
- **URL:** `/api/v1/accounts/me/`
- **Description:** Update authenticated user's profile information
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "first_name": "Jane",
  "last_name": "Smith",
  "phone_number": "+254723456789",
  "date_of_birth": "1990-01-01"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| first_name | string | No | User first name |
| last_name | string | No | User last name |
| phone_number | string | No | Kenyan phone number |
| date_of_birth | string | No | Date of birth (YYYY-MM-DD) |
| profile_picture | string | No | Profile picture URL |

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": "Profile updated successfully",
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "first_name": "Jane",
    "last_name": "Smith",
    "phone_number": "+254723456789",
    "profile_picture": null,
    "date_of_birth": "1990-01-01",
    "date_joined": "2024-01-01T00:00:00Z"
  }
}
```

**How to Test It**
```bash
curl -X PATCH https://api.saccosphere.com/api/v1/accounts/me/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "first_name": "Jane",
    "last_name": "Smith"
  }'
```

**Postman Request Breakdown**
- Method: PATCH
- URL: `https://api.saccosphere.com/api/v1/accounts/me/`
- Headers: `Content-Type: application/json`, `Authorization: Bearer <token>`
- Body: Raw JSON with fields to update

**What to Check**
- Response status should be 200
- Only provided fields should be updated
- Read-only fields should remain unchanged

---

### 10. Change Password

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/password/change/`
- **Description:** Change authenticated user's password
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "old_password": "SecurePass123",
  "new_password": "NewSecurePass456",
  "new_password2": "NewSecurePass456"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| old_password | string | Yes | Current password |
| new_password | string | Yes | New password (min 8 chars, 1 uppercase, 1 lowercase, 1 digit) |
| new_password2 | string | Yes | New password confirmation |

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": "Password changed successfully",
  "data": null
}
```

**Error Response (400 Bad Request)**
```json
{
  "success": false,
  "message": "Old password is incorrect",
  "errors": {
    "old_password": "Old password is incorrect."
  }
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/password/change/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "old_password": "SecurePass123",
    "new_password": "NewSecurePass456",
    "new_password2": "NewSecurePass456"
  }'
```

**Postman Request Breakdown**
- Method: POST
- URL: `https://api.saccosphere.com/api/v1/accounts/password/change/`
- Headers: `Content-Type: application/json`, `Authorization: Bearer <token>`
- Body: Raw JSON with old and new passwords

**What to Check**
- Response status should be 200
- Old password must be correct
- New passwords must match and meet strength requirements

---

### 11. Submit KYC ID

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/kyc/submit-id/`
- **Description:** Submit national ID number for IPRS verification
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "id_number": "12345678",
  "date_of_birth": "1990-01-01"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id_number | string | Yes | National ID number |
| date_of_birth | string | No | Date of birth (YYYY-MM-DD) |

**Response Details**

**Success Response (200 OK)**
```json
{
  "iprs_verified": true,
  "id_number": "12345678",
  "name": "John Doe",
  "iprs_reference": "IPRS-REF-123",
  "error": null
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/kyc/submit-id/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "id_number": "12345678",
    "date_of_birth": "1990-01-01"
  }'
```

---

### 12. Upload KYC Document

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/kyc/upload/`
- **Description:** Upload KYC document (ID front, ID back, passport, or Huduma)
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** multipart/form-data
  - `document_type`: string (id_front, id_back, passport, huduma)
  - `file`: file (jpg, jpeg, png, pdf, max 5MB, min 400x300px for images)

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "status": "PENDING",
  "iprs_verified": false,
  "submitted_at": "2024-01-01T00:00:00Z",
  "rejection_reason": "",
  "id_front": "http://example.com/media/id_front.jpg",
  "id_back": null,
  "passport": null
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/kyc/upload/ \
  -H "Authorization: Bearer <access_token>" \
  -F "document_type=id_front" \
  -F "file=@/path/to/id_front.jpg"
```

---

### 13. Get KYC Status

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/accounts/kyc/status/`
- **Description:** Get authenticated user's KYC verification status
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "status": "PENDING",
  "iprs_verified": true,
  "submitted_at": "2024-01-01T00:00:00Z",
  "rejection_reason": "",
  "id_front": "http://example.com/media/id_front.jpg",
  "id_back": "http://example.com/media/id_back.jpg",
  "passport": null
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/accounts/kyc/status/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 14. List SACCOs

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/accounts/saccos/`
- **Description:** List available SACCOs with filtering and search
- **Authentication Required:** No

**Request Details**
- **Headers:** None required
- **URL Parameters:** None
- **Query Parameters:**
  - `search`: string - Search SACCO name, description, or registration number
  - `sector`: string - Filter by sector
  - `county`: string - Filter by Kenya county
  - `membership_type`: string - Filter by membership type (OPEN, CLOSED)
  - `verified_only`: boolean - Only return verified SACCOs
  - `min_members`: integer - Minimum number of approved members
  - `max_members`: integer - Maximum number of approved members
  - `ordering`: string - Order by field (name, -name, member_count, -member_count, created_at, -created_at)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": [
    {
      "id": "uuid",
      "name": "Example SACCO",
      "logo": "http://example.com/media/logo.png",
      "sector": "Agriculture",
      "county": "Nairobi",
      "membership_type": "OPEN",
      "is_verified": true,
      "member_count": 150,
      "registration_fee": 500.00,
      "membership_open": true,
      "can_apply": false
    }
  ],
  "pagination": {
    "count": 100,
    "next": "http://api.saccosphere.com/api/v1/accounts/saccos/?page=2",
    "previous": null,
    "total_pages": 10
  }
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/accounts/saccos/?search=Example&verified_only=true&ordering=-member_count"
```

---

### 15. Get SACCO Details

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/accounts/saccos/<id>/`
- **Description:** Get detailed information about a specific SACCO
- **Authentication Required:** No

**Request Details**
- **Headers:** None required
- **URL Parameters:**
  - `id`: UUID - SACCO ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": {
    "id": "uuid",
    "name": "Example SACCO",
    "logo": "http://example.com/media/logo.png",
    "sector": "Agriculture",
    "county": "Nairobi",
    "membership_type": "OPEN",
    "is_verified": true,
    "member_count": 150,
    "registration_fee": 500.00,
    "membership_open": true,
    "can_apply": false,
    "description": "Example SACCO description",
    "default_interest_rate": 12.5,
    "loan_multiplier": 3.0,
    "website": "https://example.com",
    "email": "info@example.com",
    "phone": "+254712345678"
  }
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/accounts/saccos/<id>/
```

---

### 16. Send OTP

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/otp/send/`
- **Description:** Send OTP code to user's phone number
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "phone_number": "+254712345678",
  "purpose": "PHONE_VERIFY"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| phone_number | string | Yes | Kenyan phone number |
| purpose | string | Yes | OTP purpose (PHONE_VERIFY, PASSWORD_RESET) |

**Response Details**

**Success Response (200 OK)**
```json
{
  "message": "OTP sent. Check your phone."
}
```

**Error Response (429 Too Many Requests)**
```json
{
  "error": "Too many OTP requests. Try again in 12 minutes.",
  "seconds_remaining": 720
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/otp/send/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+254712345678",
    "purpose": "PHONE_VERIFY"
  }'
```

---

### 17. Verify OTP

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/otp/verify/`
- **Description:** Verify OTP code and update user's phone number
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "phone_number": "+254712345678",
  "code": "123456"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| phone_number | string | Yes | Kenyan phone number |
| code | string | Yes | 6-digit OTP code |

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "phone_number": "+254712345678",
  "profile_picture": null,
  "date_of_birth": null,
  "date_joined": "2024-01-01T00:00:00Z"
}
```

**Error Response (400 Bad Request)**
```json
{
  "error": "Invalid or expired OTP code"
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/otp/verify/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+254712345678",
    "code": "123456"
  }'
```

---

### 18. Resend OTP

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/otp/resend/`
- **Description:** Resend OTP code (with cooldown period)
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "phone_number": "+254712345678",
  "purpose": "PHONE_VERIFY"
}
```

**Response Details**

**Success Response (200 OK)**
```json
{
  "message": "OTP sent. Check your phone."
}
```

**Error Response (429 Too Many Requests)**
```json
{
  "error": "Too many OTP requests. Try again in 10 minutes.",
  "seconds_remaining": 600
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/otp/resend/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+254712345678",
    "purpose": "PHONE_VERIFY"
  }'
```

---

### 19. Request Password Reset

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/password/reset/`
- **Description:** Request password reset via OTP
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "phone_number": "+254712345678"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| phone_number | string | Yes | Kenyan phone number |

**Response Details**

**Success Response (200 OK)**
```json
{
  "message": "Password reset OTP sent. Check your phone."
}
```

**Note:** Always returns 200 to prevent phone number enumeration

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/password/reset/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+254712345678"
  }'
```

---

### 20. Confirm Password Reset

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/accounts/password/reset/confirm/`
- **Description:** Confirm password reset with OTP
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "phone_number": "+254712345678",
  "code": "123456",
  "new_password": "NewSecurePass456",
  "new_password2": "NewSecurePass456"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| phone_number | string | Yes | Kenyan phone number |
| code | string | Yes | 6-digit OTP code |
| new_password | string | Yes | New password |
| new_password2 | string | Yes | New password confirmation |

**Response Details**

**Success Response (200 OK)**
```json
{
  "message": "Password reset successful."
}
```

**Error Response (400 Bad Request)**
```json
{
  "error": "Invalid or expired OTP code"
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/accounts/password/reset/confirm/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+254712345678",
    "code": "123456",
    "new_password": "NewSecurePass456",
    "new_password2": "NewSecurePass456"
  }'
```

---

## SACCO Membership

### 21. List Memberships

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/members/memberships/`
- **Description:** List authenticated user's SACCO memberships
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco`: UUID - Filter by SACCO ID
  - `status`: string - Filter by status (PENDING, APPROVED, REJECTED, SUSPENDED, LEFT)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": [
    {
      "id": "uuid",
      "user": {
        "email": "user@example.com",
        "full_name": "John Doe"
      },
      "sacco": {
        "name": "Example SACCO",
        "logo": "http://example.com/media/logo.png"
      },
      "member_number": "MEM-001",
      "status": "APPROVED",
      "application_date": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {
    "count": 5,
    "next": null,
    "previous": null,
    "total_pages": 1
  }
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/members/memberships/?status=APPROVED" \
  -H "Authorization: Bearer <access_token>"
```

---

### 22. Apply for Membership

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/members/memberships/`
- **Description:** Apply for SACCO membership
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "sacco": "uuid",
  "custom_fields": [
    {
      "field_id": "uuid",
      "value": "Custom value"
    }
  ],
  "employment_status": "Employed",
  "employer_name": "Example Company",
  "monthly_income": 50000.00
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| sacco | UUID | Yes | SACCO ID to apply to |
| custom_fields | array | No | Custom field data |
| employment_status | string | No | Employment status |
| employer_name | string | No | Employer name |
| monthly_income | decimal | No | Monthly income |

**Response Details**

**Success Response (201 Created)**
```json
{
  "success": true,
  "message": "Membership application submitted",
  "data": {
    "id": "uuid",
    "user": {
      "email": "user@example.com",
      "full_name": "John Doe"
    },
    "sacco": {
      "name": "Example SACCO",
      "logo": "http://example.com/media/logo.png"
    },
    "member_number": null,
    "status": "PENDING",
    "application_date": "2024-01-01T00:00:00Z",
    "approved_date": null,
    "rejection_reason": null,
    "notes": null
  }
}
```

**Error Response (400 Bad Request)**
```json
{
  "sacco": ["This SACCO is not accepting public applications."]
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/members/memberships/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "sacco": "uuid",
    "employment_status": "Employed",
    "employer_name": "Example Company",
    "monthly_income": 50000.00
  }'
```

---

### 23. Get Membership Details

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/members/memberships/<id>/`
- **Description:** Get detailed information about a specific membership
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `id`: UUID - Membership ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": {
    "id": "uuid",
    "user": {
      "email": "user@example.com",
      "full_name": "John Doe"
    },
    "sacco": {
      "name": "Example SACCO",
      "logo": "http://example.com/media/logo.png"
    },
    "member_number": "MEM-001",
    "status": "APPROVED",
    "application_date": "2024-01-01T00:00:00Z",
    "approved_date": "2024-01-15T00:00:00Z",
    "rejection_reason": null,
    "notes": null
  }
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/members/memberships/<id>/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 24. Leave Membership

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/members/memberships/<id>/leave/`
- **Description:** Leave a SACCO membership
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `id`: UUID - Membership ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": "Membership left successfully",
  "data": {
    "id": "uuid",
    "status": "LEFT",
    "member_number": "MEM-001"
  }
}
```

**Error Response (400 Bad Request)**
```json
{
  "success": false,
  "message": "You cannot leave a SACCO while you have active loans.",
  "errors": {
    "loans": "Active loans must be cleared first."
  }
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/members/memberships/<id>/leave/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 25. Get SACCO Custom Fields

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/members/saccos/<sacco_id>/fields/`
- **Description:** Get custom field definitions for a SACCO
- **Authentication Required:** No

**Request Details**
- **Headers:** None required
- **URL Parameters:**
  - `sacco_id`: UUID - SACCO ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": [
    {
      "id": "uuid",
      "label": "Occupation",
      "field_type": "text",
      "is_required": true,
      "options": null,
      "display_order": 1
    },
    {
      "id": "uuid",
      "label": "Education Level",
      "field_type": "select",
      "is_required": false,
      "options": ["Primary", "Secondary", "College", "University"],
      "display_order": 2
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/members/saccos/<sacco_id>/fields/
```

---

## SACCO Management

### 26. Assign Role

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/management/roles/assign/`
- **Description:** Assign a role to a user (SUPER_ADMIN only)
- **Authentication Required:** Yes (SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "user_id": "uuid",
  "role_name": "SACCO_ADMIN",
  "sacco_id": "uuid"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| user_id | UUID | Yes | User ID to assign role to |
| role_name | string | Yes | Role name (MEMBER, SACCO_ADMIN, SUPER_ADMIN) |
| sacco_id | UUID | No | SACCO ID (required for SACCO_ADMIN) |

**Response Details**

**Success Response (201 Created)**
```json
{
  "id": "uuid",
  "user": "uuid",
  "sacco": "uuid",
  "name": "SACCO_ADMIN",
  "created_at": "2024-01-01T00:00:00Z"
}
```

**Error Response (400 Bad Request)**
```json
{
  "user_id": "User not found."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/management/roles/assign/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <super_admin_token>" \
  -d '{
    "user_id": "uuid",
    "role_name": "SACCO_ADMIN",
    "sacco_id": "uuid"
  }'
```

---

### 27. Revoke Role

**Endpoint Overview**
- **Method:** DELETE
- **URL:** `/api/v1/management/roles/<role_id>/`
- **Description:** Revoke a role from a user (SUPER_ADMIN only)
- **Authentication Required:** Yes (SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `role_id`: UUID - Role ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "detail": "Role SACCO_ADMIN revoked from user user@example.com."
}
```

**Error Response (400 Bad Request)**
```json
{
  "role": "You cannot revoke your own SUPER_ADMIN role."
}
```

**How to Test It**
```bash
curl -X DELETE https://api.saccosphere.com/api/v1/management/roles/<role_id>/ \
  -H "Authorization: Bearer <super_admin_token>"
```

---

### 28. List User Roles

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/management/roles/`
- **Description:** List all roles for a specific user
- **Authentication Required:** Yes (SACCO_ADMIN or SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `user_id`: UUID - User ID (required)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "user": "uuid",
      "sacco": "uuid",
      "name": "MEMBER",
      "created_at": "2024-01-01T00:00:00Z"
    },
    {
      "id": "uuid",
      "user": "uuid",
      "sacco": null,
      "name": "SUPER_ADMIN",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/management/roles/?user_id=uuid" \
  -H "Authorization: Bearer <access_token>"
```

---

### 29. List Admin Members

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/management/members/`
- **Description:** List members of the current SACCO (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:** None
- **Query Parameters:**
  - `search`: string - Search member email or member number
  - `status`: string - Filter by status (PENDING, APPROVED, REJECTED, SUSPENDED)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 50,
  "next": "http://api.saccosphere.com/api/v1/management/members/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "user": {
        "email": "user@example.com",
        "full_name": "John Doe"
      },
      "sacco": {
        "name": "Example SACCO",
        "logo": "http://example.com/media/logo.png"
      },
      "member_number": "MEM-001",
      "status": "APPROVED",
      "application_date": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/management/members/?search=John&status=APPROVED" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 30. Get Admin Member Detail

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/management/members/<membership_id>/`
- **Description:** Get detailed member information with savings and loans (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `membership_id`: UUID - Membership ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "user": {
    "email": "user@example.com",
    "full_name": "John Doe",
    "phone_number": "+254712345678",
    "kyc_status": "APPROVED"
  },
  "sacco": {
    "id": "uuid",
    "name": "Example SACCO"
  },
  "member_number": "MEM-001",
  "status": "APPROVED",
  "application_date": "2024-01-01T00:00:00Z",
  "approved_date": "2024-01-15T00:00:00Z",
  "savings_total": 50000.00,
  "outstanding_loans": 25000.00,
  "savings_breakdown": [
    {
      "savings_type": "BOSA",
      "amount": 30000.00,
      "total_contributions": 35000.00,
      "total_withdrawals": 5000.00,
      "status": "ACTIVE"
    }
  ],
  "active_loans": [
    {
      "id": "uuid",
      "loan_type": "Emergency Loan",
      "amount": 50000.00,
      "interest_rate": 12.0,
      "term_months": 12,
      "outstanding_balance": 25000.00,
      "status": "ACTIVE",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "recent_transactions": [
    {
      "id": "uuid",
      "reference": "TXN-001",
      "transaction_type": "DEPOSIT",
      "amount": 5000.00,
      "status": "COMPLETED",
      "description": "Saving deposit",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/management/members/<membership_id>/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 31. Get SACCO Statistics

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/management/stats/`
- **Description:** Get aggregate dashboard statistics for current SACCO (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "total_members": 150,
  "pending_applications": 5,
  "total_savings_portfolio": 7500000.00,
  "total_loans_portfolio": 3500000.00,
  "active_loans_count": 45,
  "pending_loan_approvals": 8,
  "default_count": 3,
  "default_rate": 6.67,
  "monthly_contributions": 250000.00,
  "recent_transactions": [
    {
      "id": "uuid",
      "reference": "TXN-001",
      "transaction_type": "DEPOSIT",
      "amount": 5000.00,
      "status": "COMPLETED",
      "description": "Saving deposit",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/management/stats/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 32. Review Application

**Endpoint Overview**
- **Method:** PATCH
- **URL:** `/api/v1/management/applications/<id>/review/`
- **Description:** Approve or reject a SACCO membership application (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `id`: UUID - Application ID
- **Query Parameters:** None
- **Request Body:**
```json
{
  "status": "APPROVED",
  "review_notes": "Application approved"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | Yes | Status (APPROVED, REJECTED) |
| review_notes | string | No | Review notes (required when rejecting) |

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "status": "APPROVED",
  "review_notes": "Application approved",
  "membership_id": "uuid"
}
```

**How to Test It**
```bash
curl -X PATCH https://api.saccosphere.com/api/v1/management/applications/<id>/review/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>" \
  -d '{
    "status": "APPROVED",
    "review_notes": "Application approved"
  }'
```

---

### 33. List KYC Queue

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/management/kyc/queue/`
- **Description:** List pending KYC verification records for admin review (SACCO_ADMIN or SUPER_ADMIN)
- **Authentication Required:** Yes (SACCO_ADMIN or SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:** None
- **Query Parameters:**
  - `status`: string - Filter by status (default: PENDING)
  - `search`: string - Search by email or ID number
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 10,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "status": "PENDING",
      "iprs_verified": true,
      "submitted_at": "2024-01-01T00:00:00Z",
      "rejection_reason": "",
      "id_front": "http://example.com/media/id_front.jpg",
      "id_back": "http://example.com/media/id_back.jpg",
      "passport": null
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/management/kyc/queue/?status=PENDING" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 34. Review KYC

**Endpoint Overview**
- **Method:** PATCH
- **URL:** `/api/v1/management/kyc/<kyc_id>/review/`
- **Description:** Approve or reject a member KYC verification (SACCO_ADMIN or SUPER_ADMIN)
- **Authentication Required:** Yes (SACCO_ADMIN or SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `kyc_id`: UUID - KYC record ID
- **Query Parameters:** None
- **Request Body:**
```json
{
  "status": "APPROVED",
  "rejection_reason": ""
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | Yes | Status (APPROVED, REJECTED) |
| rejection_reason | string | No | Rejection reason (required when rejecting) |

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "status": "APPROVED",
  "iprs_verified": true,
  "submitted_at": "2024-01-01T00:00:00Z",
  "rejection_reason": "",
  "id_front": "http://example.com/media/id_front.jpg",
  "id_back": "http://example.com/media/id_back.jpg",
  "passport": null
}
```

**How to Test It**
```bash
curl -X PATCH https://api.saccosphere.com/api/v1/management/kyc/<kyc_id>/review/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>" \
  -d '{
    "status": "APPROVED",
    "rejection_reason": ""
  }'
```

---

### 35. Update Loan Status

**Endpoint Overview**
- **Method:** PATCH
- **URL:** `/api/v1/management/loans/<id>/status/`
- **Description:** Update loan status through approval workflow (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `id`: UUID - Loan ID
- **Query Parameters:** None
- **Request Body:**
```json
{
  "status": "APPROVED",
  "notes": "Loan approved"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | Yes | New status (BOARD_REVIEW, APPROVED, REJECTED) |
| notes | string | No | Admin notes |

**Valid Transitions:**
- PENDING → BOARD_REVIEW
- BOARD_REVIEW → APPROVED or REJECTED

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "membership": "uuid",
  "loan_type": "uuid",
  "amount": 50000.00,
  "interest_rate": 12.0,
  "term_months": 12,
  "status": "APPROVED",
  "outstanding_balance": 50000.00,
  "disbursement_date": null,
  "application_notes": "Emergency loan",
  "rejection_reason": null,
  "admin_notes": "Loan approved",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**Error Response (400 Bad Request)**
```json
{
  "status": "Cannot transition from APPROVED to REJECTED."
}
```

**How to Test It**
```bash
curl -X PATCH https://api.saccosphere.com/api/v1/management/loans/<id>/status/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>" \
  -d '{
    "status": "APPROVED",
    "notes": "Loan approved"
  }'
```

---

### 36. List Audit Logs

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/management/audit-logs/`
- **Description:** List system audit logs (SUPER_ADMIN only)
- **Authentication Required:** Yes (SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `action`: string - Filter by action (CREATE, UPDATE, DELETE)
  - `resource_type`: string - Filter by resource type (Loan, Membership, etc.)
  - `user`: string - Filter by user email
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 100,
  "next": "http://api.saccosphere.com/api/v1/management/audit-logs/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "user": "uuid",
      "user_email": "admin@example.com",
      "action": "UPDATE",
      "resource_type": "Loan",
      "resource_id": "uuid",
      "old_values": {"status": "PENDING"},
      "new_values": {"status": "APPROVED"},
      "ip_address": "192.168.1.1",
      "user_agent": "Mozilla/5.0...",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/management/audit-logs/?action=UPDATE&resource_type=Loan" \
  -H "Authorization: Bearer <super_admin_token>"
```

---

### 37. Import Members

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/management/import/`
- **Description:** Create an asynchronous SACCO member import job from uploaded file (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** multipart/form-data
  - `file`: file (CSV, XLSX, or XLS)

**Response Details**

**Success Response (202 Accepted)**
```json
{
  "job_id": "uuid",
  "message": "Import started. Check status at /management/import/<job_id>/"
}
```

**Error Response (400 Bad Request)**
```json
{
  "detail": "Only .csv, .xlsx, or .xls files are supported."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/management/import/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>" \
  -F "file=@/path/to/members.csv"
```

---

### 38. Get Import Job Status

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/management/import/<job_id>/`
- **Description:** Get SACCO-scoped progress and summary for one import job (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `job_id`: UUID - Import job ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "job_id": "uuid",
  "sacco_id": "uuid",
  "status": "COMPLETED",
  "total_rows": 100,
  "success_count": 95,
  "fail_count": 5,
  "error_summary": [
    {
      "row": 10,
      "error": "Invalid email format"
    }
  ],
  "created_at": "2024-01-01T00:00:00Z",
  "completed_at": "2024-01-01T00:05:00Z"
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/management/import/<job_id>/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

## Services (Loans & Savings)

### 39. List Savings Types

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/savings-types/`
- **Description:** List savings types (ViewSet with CRUD operations)
- **Authentication Required:** No (read), Yes (write - admin only)

**Request Details**
- **Headers:** None required for GET
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco`: UUID - Filter by SACCO ID
  - `sacco_id`: UUID - Filter by SACCO ID
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 3,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "sacco": "uuid",
      "name": "BOSA",
      "description": "Back Office Savings Account",
      "minimum_balance": 1000.00,
      "interest_rate": 8.0,
      "is_active": true,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/services/savings-types/?sacco_id=uuid"
```

---

### 40. List Savings

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/savings/`
- **Description:** List authenticated user's savings accounts
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco`: UUID - Filter by SACCO ID
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "membership": {
        "member_number": "MEM-001",
        "sacco_name": "Example SACCO"
      },
      "savings_type": {
        "id": "uuid",
        "name": "BOSA"
      },
      "savings_type_id": "uuid",
      "amount": 30000.00,
      "total_contributions": 35000.00,
      "total_withdrawals": 5000.00,
      "status": "ACTIVE",
      "dividend_eligible": true,
      "last_transaction_date": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/services/savings/?sacco=uuid" \
  -H "Authorization: Bearer <access_token>"
```

---

### 41. Get Savings Breakdown

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/savings/breakdown/`
- **Description:** Get savings breakdown by type for a specific SACCO
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco_id`: UUID - SACCO ID (required)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "data": {
    "sacco_id": "uuid",
    "sacco_name": "Example SACCO",
    "bosa_total": 30000.00,
    "fosa_total": 15000.00,
    "share_capital_total": 5000.00,
    "dividend_eligible_total": 35000.00,
    "total": 50000.00
  }
}
```

**Error Response (400 Bad Request)**
```json
{
  "success": false,
  "message": "sacco_id parameter is required.",
  "error_code": "MISSING_PARAMETER"
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/services/savings/breakdown/?sacco_id=uuid" \
  -H "Authorization: Bearer <access_token>"
```

---

### 42. List Loan Types

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/loan-types/`
- **Description:** List available loan types
- **Authentication Required:** No

**Request Details**
- **Headers:** None required
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco_id`: UUID - Filter by SACCO ID
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 5,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "sacco": "uuid",
      "name": "Emergency Loan",
      "description": "Short-term emergency loan",
      "min_amount": 5000.00,
      "max_amount": 100000.00,
      "interest_rate": 12.0,
      "max_term_months": 12,
      "requires_guarantors": true,
      "min_guarantors": 2,
      "is_active": true,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/services/loan-types/?sacco_id=uuid"
```

---

### 43. Check Loan Eligibility

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/loans/eligibility/`
- **Description:** Check loan eligibility for a SACCO
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco_id`: UUID - SACCO ID (required)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "eligible": true,
  "max_amount": 150000.00,
  "reason": null,
  "savings_total": 50000.00,
  "active_loans_total": 25000.00,
  "loan_multiplier": 3.0
}
```

**Error Response (400 Bad Request)**
```json
{
  "eligible": false,
  "max_amount": 0,
  "reason": "You must have an approved membership in this SACCO."
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/services/loans/eligibility/?sacco_id=uuid" \
  -H "Authorization: Bearer <access_token>"
```

---

### 44. Apply for Loan

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/services/loans/apply/`
- **Description:** Apply for a loan
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "loan_type": "uuid",
  "amount": 50000.00,
  "term_months": 12,
  "application_notes": "Emergency medical expenses"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| loan_type | UUID | Yes | Loan type ID |
| amount | decimal | Yes | Loan amount (must be > 0) |
| term_months | integer | Yes | Loan term in months |
| application_notes | string | No | Application notes |

**Response Details**

**Success Response (201 Created)**
```json
{
  "id": "uuid",
  "membership": "uuid",
  "loan_type": "uuid",
  "amount": 50000.00,
  "interest_rate": 12.0,
  "term_months": 12,
  "status": "PENDING",
  "outstanding_balance": 50000.00,
  "disbursement_date": null,
  "application_notes": "Emergency medical expenses",
  "rejection_reason": null,
  "admin_notes": null,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**Error Response (400 Bad Request)**
```json
{
  "detail": "Requested amount exceeds your loan limit of KES 150000.00."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/services/loans/apply/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "loan_type": "uuid",
    "amount": 50000.00,
    "term_months": 12,
    "application_notes": "Emergency medical expenses"
  }'
```

---

### 45. List Loans

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/loans/list/`
- **Description:** List authenticated user's loans
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `status`: string - Filter by status (PENDING, ACTIVE, APPROVED, etc.)
  - `sacco`: UUID - Filter by SACCO ID
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 3,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "membership": {
        "member_number": "MEM-001",
        "sacco_name": "Example SACCO"
      },
      "loan_type": {
        "name": "Emergency Loan"
      },
      "amount": 50000.00,
      "outstanding_balance": 25000.00,
      "interest_rate": 12.0,
      "term_months": 12,
      "status": "ACTIVE",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/services/loans/list/?status=ACTIVE" \
  -H "Authorization: Bearer <access_token>"
```

---

### 46. Get Loan Detail

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/loans/<id>/`
- **Description:** Get detailed information about a specific loan
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `id`: UUID - Loan ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "membership": {
    "member_number": "MEM-001",
    "sacco_name": "Example SACCO"
  },
  "loan_type": {
    "name": "Emergency Loan"
  },
  "amount": 50000.00,
  "outstanding_balance": 25000.00,
  "interest_rate": 12.0,
  "term_months": 12,
  "status": "ACTIVE",
  "disbursement_date": "2024-01-01T00:00:00Z",
  "application_notes": "Emergency medical expenses",
  "rejection_reason": null,
  "admin_notes": null,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/services/loans/<id>/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 47. Get Repayment Schedule

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/loans/<id>/schedule/`
- **Description:** Get or generate repayment schedule for a loan
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `id`: UUID - Loan ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 12,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "loan": "uuid",
      "instalment_number": 1,
      "due_date": "2024-02-01",
      "amount": 4442.42,
      "principal": 3758.42,
      "interest": 684.00,
      "balance_after": 46241.58,
      "is_paid": false,
      "is_overdue": false,
      "days_overdue": 0,
      "paid_at": null
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/services/loans/<id>/schedule/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 48. Search Guarantor

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/services/loans/<loan_id>/guarantors/search/`
- **Description:** Search for a possible guarantor for a loan
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `loan_id`: UUID - Loan ID
- **Query Parameters:**
  - `phone`: string - Search by phone number
  - `member_number`: string - Search by member number
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "user": {
    "id": "uuid",
    "email": "guarantor@example.com",
    "full_name": "Jane Smith",
    "phone_number": "+254723456789"
  },
  "member_number": "MEM-002",
  "savings_total": 75000.00,
  "available_capacity": 50000.00,
  "can_guarantee": true
}
```

**Error Response (404 Not Found)**
```json
{
  "detail": "No matching guarantor found."
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/services/loans/<loan_id>/guarantors/search/?phone=+254723456789" \
  -H "Authorization: Bearer <access_token>"
```

---

### 49. Request Guarantor

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/services/loans/<loan_id>/guarantors/`
- **Description:** Request a member to guarantee a loan
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `loan_id`: UUID - Loan ID
- **Query Parameters:** None
- **Request Body:**
```json
{
  "guarantor_user_id": "uuid",
  "guarantee_amount": 25000.00
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| guarantor_user_id | UUID | Yes | User ID of guarantor |
| guarantee_amount | decimal | Yes | Amount to guarantee (must be > 0 and ≤ loan amount) |

**Response Details**

**Success Response (201 Created)**
```json
{
  "id": "uuid",
  "guarantor": {
    "id": "uuid",
    "email": "guarantor@example.com",
    "full_name": "Jane Smith",
    "phone_number": "+254723456789"
  },
  "status": "PENDING",
  "guarantee_amount": 25000.00,
  "requested_at": "2024-01-01T00:00:00Z",
  "responded_at": null
}
```

**Error Response (400 Bad Request)**
```json
{
  "detail": "Guarantor has insufficient capacity."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/services/loans/<loan_id>/guarantors/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "guarantor_user_id": "uuid",
    "guarantee_amount": 25000.00
  }'
```

---

### 50. Respond to Guarantor Request

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/services/loans/<loan_id>/guarantors/<guarantor_id>/respond/`
- **Description:** Approve or decline a guarantor request
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `loan_id`: UUID - Loan ID
  - `guarantor_id`: UUID - Guarantor ID
- **Query Parameters:** None
- **Request Body:**
```json
{
  "action": "APPROVE",
  "notes": "I can guarantee this loan"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| action | string | Yes - APPROVE or DECLINE | Action to take |
| notes | string | No | Optional notes |

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "guarantor": {
    "id": "uuid",
    "email": "guarantor@example.com",
    "full_name": "Jane Smith",
    "phone_number": "+254723456789"
  },
  "status": "APPROVED",
  "guarantee_amount": 25000.00,
  "requested_at": "2024-01-01T00:00:00Z",
  "responded_at": "2024-01-01T00:00:00Z"
}
```

**Error Response (400 Bad Request)**
```json
{
  "detail": "Insufficient guarantee capacity for this amount."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/services/loans/<loan_id>/guarantors/<guarantor_id>/respond/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "action": "APPROVE",
    "notes": "I can guarantee this loan"
  }'
```

---

## Payments (M-Pesa)

### 51. List Transactions

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/payments/transactions/`
- **Description:** List authenticated user's payment transactions
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": [
    {
      "id": "uuid",
      "provider": "uuid",
      "provider_name": "M-Pesa",
      "reference": "SS-ABC123",
      "external_reference": "ws_CO_123456789",
      "transaction_type": "DEPOSIT",
      "amount": 5000.00,
      "fee_amount": 0.00,
      "currency": "KES",
      "status": "COMPLETED",
      "description": "SaccoSphere saving deposit",
      "metadata": {},
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z"
    }
  ],
  "pagination": {
    "count": 50,
    "next": "http://api.saccosphere.com/api/v1/payments/transactions/?page=2",
    "previous": null,
    "total_pages": 5
  }
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/payments/transactions/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 52. Get Transaction Detail

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/payments/transactions/<id>/`
- **Description:** Get detailed information about a specific transaction
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `id`: UUID - Transaction ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": {
    "id": "uuid",
    "provider": "uuid",
    "provider_name": "M-Pesa",
    "reference": "SS-ABC123",
    "external_reference": "ws_CO_123456789",
    "transaction_type": "DEPOSIT",
    "amount": 5000.00,
    "fee_amount": 0.00,
    "currency": "KES",
    "status": "COMPLETED",
    "description": "SaccoSphere saving deposit",
    "metadata": {},
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/payments/transactions/<id>/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 53. Get M-Pesa Transaction Detail

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/payments/mpesa/<id>/`
- **Description:** Get detailed M-Pesa transaction information
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `id`: UUID - M-Pesa transaction ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "message": null,
  "data": {
    "id": "uuid",
    "transaction": "uuid",
    "phone_number": "+254712345678",
    "merchant_request_id": "12345-67890-12345",
    "checkout_request_id": "ws_CO_123456789",
    "conversation_id": null,
    "originator_conversation_id": null,
    "transaction_type": "STK",
    "result_code": 0,
    "result_description": "The service request is processed successfully.",
    "mpesa_receipt_number": "ABC123XYZ",
    "callback_received": true,
    "related_saving": "uuid",
    "related_loan": null,
    "related_instalment_number": null,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/payments/mpesa/<id>/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 54. Initiate STK Push

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/payments/mpesa/stk-push/`
- **Description:** Initiate M-Pesa STK push for saving deposit or loan repayment
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "phone_number": "+254712345678",
  "amount": 5000.00,
  "purpose": "SAVING_DEPOSIT",
  "sacco_id": "uuid",
  "saving_id": "uuid"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| phone_number | string | Yes | Kenyan M-Pesa phone number |
| amount | decimal | Yes | Amount (10.00 - 300000.00) |
| purpose | string | Yes - SAVING_DEPOSIT or LOAN_REPAYMENT | Payment purpose |
| sacco_id | UUID | Yes | SACCO ID |
| saving_id | UUID | Yes for SAVING_DEPOSIT | Saving account ID |
| loan_id | UUID | Yes for LOAN_REPAYMENT | Loan ID |
| instalment_number | integer | Yes for LOAN_REPAYMENT | Instalment number |

**Response Details**

**Success Response (201 Created)**
```json
{
  "checkout_request_id": "ws_CO_123456789",
  "merchant_request_id": "12345-67890-12345",
  "message": "Check your phone to enter your M-Pesa PIN."
}
```

**Error Response (502 Bad Gateway)**
```json
{
  "error": "Internal server error from M-Pesa",
  "response_code": 500
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/payments/mpesa/stk-push/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "phone_number": "+254712345678",
    "amount": 5000.00,
    "purpose": "SAVING_DEPOSIT",
    "sacco_id": "uuid",
    "saving_id": "uuid"
  }'
```

---

### 55. Get STK Status

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/payments/mpesa/stk/<checkout_request_id>/status/`
- **Description:** Get STK transaction status by checkout request ID
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `checkout_request_id`: string - M-Pesa checkout request ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "checkout_request_id": "ws_CO_123456789",
  "merchant_request_id": "12345-67890-12345",
  "status": "COMPLETED",
  "result_code": 0,
  "result_description": "The service request is processed successfully.",
  "callback_received": true
}
```

**Error Response (403 Forbidden)**
```json
{
  "detail": "You do not have permission to view this status."
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/payments/mpesa/stk/<checkout_request_id>/status/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 56. Initiate B2C Disbursement

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/payments/mpesa/b2c/disburse/`
- **Description:** Initiate M-Pesa B2C loan disbursement (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "loan_id": "uuid",
  "phone_number": "+254712345678",
  "amount": 50000.00,
  "remarks": "Loan Disbursement"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| loan_id | UUID | Yes | Loan ID to disburse |
| phone_number | string | Yes | Kenyan M-Pesa phone number |
| amount | decimal | Yes | Amount to disburse (must be > 0) |
| remarks | string | No - default "Loan Disbursement" | Disbursement remarks |

**Response Details**

**Success Response (201 Created)**
```json
{
  "conversation_id": "CONV-123456789",
  "message": "Disbursement initiated."
}
```

**Error Response (400 Bad Request)**
```json
{
  "detail": "Only approved loans can be disbursed."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/payments/mpesa/b2c/disburse/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>" \
  -d '{
    "loan_id": "uuid",
    "phone_number": "+254712345678",
    "amount": 50000.00,
    "remarks": "Loan Disbursement"
  }'
```

---

### 57. Get B2C Status

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/payments/mpesa/b2c/<conversation_id>/status/`
- **Description:** Get B2C disbursement status by conversation ID (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>`
- **URL Parameters:**
  - `conversation_id`: string - M-Pesa conversation ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "conversation_id": "CONV-123456789",
  "originator_conversation_id": "ORIG-123456789",
  "status": "COMPLETED",
  "result_code": 0,
  "result_description": "The service request is processed successfully.",
  "mpesa_receipt_number": "ABC123XYZ",
  "callback_received": true,
  "loan_id": "uuid",
  "amount": 50000.00,
  "created_at": "2024-01-01T00:00:00Z"
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/payments/mpesa/b2c/<conversation_id>/status/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 58. Get B2C History

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/payments/mpesa/b2c/history/`
- **Description:** List B2C disbursement history for current SACCO (SACCO_ADMIN only)
- **Authentication Required:** Yes (SACCO_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
[
  {
    "conversation_id": "CONV-123456789",
    "originator_conversation_id": "ORIG-123456789",
    "status": "COMPLETED",
    "result_code": 0,
    "result_description": "The service request is processed successfully.",
    "mpesa_receipt_number": "ABC123XYZ",
    "callback_received": true,
    "loan_id": "uuid",
    "amount": 50000.00,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/payments/mpesa/b2c/history/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 59. M-Pesa STK Callback

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/payments/callback/mpesa/stk/`
- **Description:** Receive Safaricom STK callback payload (webhook)
- **Authentication Required:** No (Safaricom IP whitelist)

**Request Details**
- **Headers:** None required (IP whitelist enforced)
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** M-Pesa callback payload

**Response Details**

**Success Response (200 OK)**
```json
{
  "ResultCode": 0,
  "ResultDesc": "Accepted"
}
```

**Note:** This is a webhook endpoint called by Safaricom

---

### 60. M-Pesa B2C Callback

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/payments/callback/mpesa/b2c/`
- **Description:** Receive Safaricom B2C callback payload (webhook)
- **Authentication Required:** No (Safaricom IP whitelist)

**Request Details**
- **Headers:** None required (IP whitelist enforced)
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** M-Pesa callback payload

**Response Details**

**Success Response (200 OK)**
```json
{
  "ResultCode": 0,
  "ResultDesc": "Accepted"
}
```

**Note:** This is a webhook endpoint called by Safaricom

---

### 61. Create Callback

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/payments/callbacks/`
- **Description:** Create a callback record (testing/debugging)
- **Authentication Required:** No

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "transaction": "uuid",
  "provider": "uuid",
  "raw_payload": {}
}
```

**Response Details**

**Success Response (201 Created)**
```json
{
  "id": "uuid",
  "transaction": "uuid",
  "provider": "uuid",
  "raw_payload": {},
  "processed": false,
  "processing_error": null,
  "received_at": "2024-01-01T00:00:00Z",
  "processed_at": null
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/payments/callbacks/ \
  -H "Content-Type: application/json" \
  -d '{
    "transaction": "uuid",
    "provider": "uuid",
    "raw_payload": {}
  }'
```

---

## Ledger & Statements

### 62. List Ledger Entries

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/ledger/entries/`
- **Description:** List ledger entries for user's membership in a SACCO
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco_id`: UUID - SACCO ID (required)
  - `from_date`: string - Filter by start date (YYYY-MM-DD)
  - `to_date`: string - Filter by end date (YYYY-MM-DD)
  - `category`: string - Filter by category
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 100,
  "next": "http://api.saccosphere.com/api/v1/ledger/entries/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "membership": "uuid",
      "transaction": "uuid",
      "entry_type": "CREDIT",
      "category": "DEPOSIT",
      "amount": 5000.00,
      "description": "Saving deposit",
      "reference": "SAV-20260113103022-ABC123",
      "balance_before": 45000.00,
      "balance_after": 50000.00,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**Error Response (400 Bad Request)**
```json
{
  "sacco_id": ["This query param is required."]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/ledger/entries/?sacco_id=uuid&from_date=2024-01-01&to_date=2024-12-31" \
  -H "Authorization: Bearer <access_token>"
```

---

### 63. Get Balance

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/ledger/balance/`
- **Description:** Get user's current ledger balance in a SACCO
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco_id`: UUID - SACCO ID (required)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "sacco_id": "uuid",
  "sacco_name": "Example SACCO",
  "current_balance": 50000.00,
  "as_of_date": null
}
```

**Error Response (400 Bad Request)**
```json
{
  "sacco_id": ["This query param is required."]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/ledger/balance/?sacco_id=uuid" \
  -H "Authorization: Bearer <access_token>"
```

---

### 64. Get Statement

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/ledger/statement/`
- **Description:** Get paginated ledger statement for a SACCO membership
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco_id`: UUID - SACCO ID (required)
  - `from_date`: string - Start date (YYYY-MM-DD, required)
  - `to_date`: string - End date (YYYY-MM-DD, required)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "member_name": "John Doe",
  "member_number": "MEM-001",
  "sacco_name": "Example SACCO",
  "sacco_logo_url": "http://example.com/media/logo.png",
  "from_date": "2024-01-01",
  "to_date": "2024-12-31",
  "generated_at": "2024-01-01T00:00:00Z",
  "opening_balance": 45000.00,
  "closing_balance": 50000.00,
  "total_credits": 10000.00,
  "total_debits": 5000.00,
  "entries": [
    {
      "entry_type": "CREDIT",
      "category": "DEPOSIT",
      "amount": 5000.00,
      "description": "Saving deposit",
      "reference": "SAV-20260113103022-ABC123",
      "balance_after": 50000.00,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "currency": "KES",
  "entries_pagination": {
    "count": 100,
    "total_pages": 10,
    "current_page": 1,
    "next": "http://api.saccosphere.com/api/v1/ledger/statement/?page=2",
    "previous": null
  }
}
```

**Error Response (400 Bad Request)**
```json
{
  "to_date": "Statement date range cannot exceed 1 year."
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/ledger/statement/?sacco_id=uuid&from_date=2024-01-01&to_date=2024-12-31" \
  -H "Authorization: Bearer <access_token>"
```

---

### 65. Get Statement PDF

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/ledger/statement/pdf/`
- **Description:** Download ledger statement as PDF
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `sacco_id`: UUID - SACCO ID (required)
  - `from_date`: string - Start date (YYYY-MM-DD, required)
  - `to_date`: string - End date (YYYY-MM-DD, required)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
- Content-Type: application/pdf
- Content-Disposition: attachment; filename="statement_MEM-001_2024-01-01_2024-12-31.pdf"
- Body: PDF file bytes

**Error Response (503 Service Unavailable)**
```json
{
  "message": "PDF generation temporarily unavailable."
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/ledger/statement/pdf/?sacco_id=uuid&from_date=2024-01-01&to_date=2024-12-31" \
  -H "Authorization: Bearer <access_token>" \
  --output statement.pdf
```

---

## Notifications

### 66. List Notifications

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/notifications/`
- **Description:** List notifications for the authenticated user
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `category`: string - Filter by category
  - `is_read`: boolean - Filter by read status (true/false)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 20,
  "next": "http://api.saccosphere.com/api/v1/notifications/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "title": "Loan Approved",
      "message": "Your loan has been approved.",
      "category": "LOAN",
      "is_read": false,
      "action_url": "/loans/uuid/",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/notifications/?is_read=false" \
  -H "Authorization: Bearer <access_token>"
```

---

### 67. Mark Notification as Read

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/notifications/<id>/read/`
- **Description:** Mark one notification as read
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:**
  - `id`: UUID - Notification ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/notifications/<id>/read/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 68. Mark All Notifications as Read

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/notifications/read-all/`
- **Description:** Mark all notifications for the user as read
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "success": true,
  "count": 15
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/notifications/read-all/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 69. Register Device Token

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/notifications/device/`
- **Description:** Register or reactivate a device token for push notifications
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Content-Type: application/json`
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:**
```json
{
  "token": "device_push_token_string",
  "platform": "ios"
}
```

**Request Body Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| token | string | Yes | Device push token |
| platform | string | Yes - ios or android | Device platform |

**Response Details**

**Success Response (201 Created)**
```json
{
  "id": "uuid",
  "token": "device_push_token_string",
  "platform": "ios",
  "user": "uuid",
  "is_active": true,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "token": "device_push_token_string",
  "platform": "ios",
  "user": "uuid",
  "is_active": true,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/notifications/device/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "token": "device_push_token_string",
    "platform": "ios"
  }'
```

---

## Billing

### 70. List Invoices

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/billing/invoices/`
- **Description:** List monthly invoices visible to current SACCO admin or super admin
- **Authentication Required:** Yes (SACCO_ADMIN or SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "count": 12,
  "next": "http://api.saccosphere.com/api/v1/billing/invoices/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "sacco": "uuid",
      "sacco_name": "Example SACCO",
      "period_start": "2024-01-01",
      "period_end": "2024-01-31",
      "amount_due": 5000.00,
      "currency": "KES",
      "status": "PAID",
      "report_payload": {},
      "sent_at": "2024-02-01T00:00:00Z",
      "due_date": "2024-02-15",
      "created_at": "2024-02-01T00:00:00Z",
      "updated_at": "2024-02-01T00:00:00Z"
    }
  ]
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/billing/invoices/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 71. Get Invoice Detail

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/billing/invoices/<id>/`
- **Description:** Retrieve one invoice by ID
- **Authentication Required:** Yes (SACCO_ADMIN or SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `id`: UUID - Invoice ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "id": "uuid",
  "sacco": "uuid",
  "sacco_name": "Example SACCO",
  "period_start": "2024-01-01",
  "period_end": "2024-01-31",
  "amount_due": 5000.00,
  "currency": "KES",
  "status": "PAID",
  "report_payload": {},
  "sent_at": "2024-02-01T00:00:00Z",
  "due_date": "2024-02-15",
  "created_at": "2024-02-01T00:00:00Z",
  "updated_at": "2024-02-01T00:00:00Z"
}
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/billing/invoices/<id>/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 72. Resend Invoice

**Endpoint Overview**
- **Method:** POST
- **URL:** `/api/v1/billing/invoices/<invoice_id>/resend/`
- **Description:** Resend existing invoice report to SACCO emails/admin recipients
- **Authentication Required:** Yes (SACCO_ADMIN or SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `invoice_id`: UUID - Invoice ID
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "detail": "Invoice resent successfully."
}
```

**Error Response (400 Bad Request)**
```json
{
  "detail": "No invoice recipients configured for this SACCO."
}
```

**How to Test It**
```bash
curl -X POST https://api.saccosphere.com/api/v1/billing/invoices/<invoice_id>/resend/ \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>"
```

---

### 73. Download Invoice

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/billing/invoices/<invoice_id>/download/`
- **Description:** Download invoice report as CSV or PDF
- **Authentication Required:** Yes (SACCO_ADMIN or SUPER_ADMIN)

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
  - `X-Sacco-ID: <sacco_id>` (optional for multi-SACCO admins)
- **URL Parameters:**
  - `invoice_id`: UUID - Invoice ID
- **Query Parameters:**
  - `format`: string - File format (csv or pdf, default: csv)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
- Content-Type: text/csv (for CSV) or application/pdf (for PDF)
- Content-Disposition: attachment; filename="invoice-uuid.csv" or "invoice-uuid.pdf"
- Body: File bytes

**How to Test It**
```bash
# Download as CSV
curl -X GET "https://api.saccosphere.com/api/v1/billing/invoices/<invoice_id>/download/?format=csv" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>" \
  --output invoice.csv

# Download as PDF
curl -X GET "https://api.saccosphere.com/api/v1/billing/invoices/<invoice_id>/download/?format=pdf" \
  -H "Authorization: Bearer <sacco_admin_token>" \
  -H "X-Sacco-ID: <sacco_id>" \
  --output invoice.pdf
```

---

## Dashboard

### 74. Get Portfolio

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/dashboard/portfolio/`
- **Description:** Get unified member portfolio across SACCO memberships
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "total_savings": 75000.00,
  "total_loans": 50000.00,
  "total_share_capital": 10000.00,
  "saccos": [
    {
      "sacco_id": "uuid",
      "sacco_name": "Example SACCO",
      "sacco_logo": "http://example.com/media/logo.png",
      "savings": 50000.00,
      "loans": 30000.00,
      "share_capital": 5000.00,
      "active_savings_count": 2,
      "active_loans_count": 1
    }
  ],
  "recent_transactions": [
    {
      "id": "uuid",
      "reference": "TXN-001",
      "transaction_type": "DEPOSIT",
      "amount": 5000.00,
      "status": "COMPLETED",
      "description": "Saving deposit",
      "created_at": "2024-01-01T00:00:00Z",
      "sacco_name": "Example SACCO"
    }
  ]
}
```

**Response Headers:**
- `X-Cache: HIT` or `X-Cache: MISS`

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/dashboard/portfolio/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 75. Get Dashboard State

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/dashboard/state/`
- **Description:** Get current dashboard state for authenticated member
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
{
  "state": "ACTIVE_MEMBERSHIPS",
  "has_pending_applications": false,
  "has_active_loans": true,
  "has_savings": true,
  "kyc_status": "APPROVED",
  "unread_notifications": 5,
  "saccos": [
    {
      "sacco_id": "uuid",
      "sacco_name": "Example SACCO",
      "membership_status": "APPROVED"
    }
  ]
}
```

**Response Headers:**
- `X-Cache: HIT` or `X-Cache: MISS`

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/dashboard/state/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 76. Get SACCO Switcher

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/dashboard/saccos/`
- **Description:** List SACCO switcher cards for authenticated member
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:** None
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
[
  {
    "sacco_id": "uuid",
    "sacco_name": "Example SACCO",
    "sacco_logo": "http://example.com/media/logo.png",
    "membership_status": "APPROVED",
    "active_savings_count": 2,
    "active_loans_count": 1,
    "unread_notifications": 3
  }
]
```

**How to Test It**
```bash
curl -X GET https://api.saccosphere.com/api/v1/dashboard/saccos/ \
  -H "Authorization: Bearer <access_token>"
```

---

### 77. Get Activity Feed

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/dashboard/activity/`
- **Description:** Get member activity feed entries
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `limit`: integer - Number of entries (default: 20, max: 100)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
[
  {
    "id": "uuid",
    "type": "PAYMENT",
    "title": "Saving Deposit",
    "description": "You deposited KES 5,000.00",
    "amount": 5000.00,
    "sacco_name": "Example SACCO",
    "created_at": "2024-01-01T00:00:00Z"
  },
  {
    "id": "uuid",
    "type": "LOAN_REPAYMENT",
    "title": "Loan Repayment",
    "description": "You repaid KES 4,442.42 for your loan",
    "amount": 4442.42,
    "sacco_name": "Example SACCO",
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

**Response Headers:**
- `X-Cache: HIT` or `X-Cache: MISS`

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/dashboard/activity/?limit=20" \
  -H "Authorization: Bearer <access_token>"
```

---

### 78. Compare Loans

**Endpoint Overview**
- **Method:** GET
- **URL:** `/api/v1/dashboard/loans/compare/`
- **Description:** Compare eligible loan options for requested amount and term
- **Authentication Required:** Yes

**Request Details**
- **Headers:** 
  - `Authorization: Bearer <access_token>`
- **URL Parameters:** None
- **Query Parameters:**
  - `amount`: decimal - Loan amount (required)
  - `term`: integer - Loan term in months (required)
- **Request Body:** None

**Response Details**

**Success Response (200 OK)**
```json
[
  {
    "sacco_id": "uuid",
    "sacco_name": "Example SACCO",
    "loan_type_id": "uuid",
    "loan_type_name": "Emergency Loan",
    "interest_rate": 12.0,
    "monthly_payment": 4442.42,
    "total_cost": 53309.04,
    "total_interest": 3309.04
  },
  {
    "sacco_id": "uuid",
    "sacco_name": "Another SACCO",
    "loan_type_id": "uuid",
    "loan_type_name": "Development Loan",
    "interest_rate": 10.0,
    "monthly_payment": 4395.79,
    "total_cost": 52749.48,
    "total_interest": 2749.48
  }
]
```

**Error Response (400 Bad Request)**
```json
{
  "amount": "Amount is required."
}
```

**How to Test It**
```bash
curl -X GET "https://api.saccosphere.com/api/v1/dashboard/loans/compare/?amount=50000&term=12" \
  -H "Authorization: Bearer <access_token>"
```

---

## Authentication Notes

### JWT Token Usage

Most endpoints require JWT authentication. Include the access token in the Authorization header:

```
Authorization: Bearer <your_access_token>
```

### Token Refresh

Access tokens expire after a set time. Use the refresh endpoint to get a new access token:

```
POST /api/v1/accounts/token/refresh/
{
  "refresh": "<your_refresh_token>"
}
```

### SACCO Context Header

For SACCO_ADMIN users managing multiple SACCOs, include the X-Sacco-ID header:

```
X-Sacco-ID: <sacco_id>
```

This header is optional if the admin only has one SACCO.

---

## Error Handling

### Standard Error Response Format

```json
{
  "success": false,
  "message": "Error message",
  "errors": {
    "field_name": ["Error details"]
  },
  "error_code": "ERROR_CODE",
  "status_code": 400
}
```

### Common HTTP Status Codes

- **200 OK** - Request successful
- **201 Created** - Resource created successfully
- **202 Accepted** - Request accepted for processing
- **400 Bad Request** - Invalid request data
- **401 Unauthorized** - Authentication required or failed
- **403 Forbidden** - Permission denied
- **404 Not Found** - Resource not found
- **429 Too Many Requests** - Rate limit exceeded
- **500 Internal Server Error** - Server error
- **502 Bad Gateway** - External service error (e.g., M-Pesa)
- **503 Service Unavailable** - Service temporarily unavailable

---

## Rate Limiting

- **OTP endpoints:** 5 requests per hour per phone number
- **General API:** 1000 requests per hour per user

Rate limit information is included in response headers:
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1609459200
```

---

## Pagination

List endpoints support pagination using the `page` query parameter:

```
GET /api/v1/accounts/saccos/?page=2
```

Pagination response format:
```json
{
  "count": 100,
  "next": "http://api.saccosphere.com/api/v1/accounts/saccos/?page=3",
  "previous": "http://api.saccosphere.com/api/v1/accounts/saccos/?page=1",
  "total_pages": 10
}
```

---

## Swagger/OpenAPI Documentation

Interactive API documentation is available at:

- **Swagger UI:** `https://api.saccosphere.com/swagger/`
- **ReDoc:** `https://api.saccosphere.com/redoc/`

---

## Support

For API support or questions, contact: support@saccosphere.com

---

**Document Version:** 1.0.0  
**Last Updated:** 2024-01-01  
**API Version:** v1.0.0
