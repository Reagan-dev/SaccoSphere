# Scaling API Guide

This document details the new scaling-related endpoints added to SaccoSphere. These endpoints enable SACCO admins to manage loan approvals, configure SACCO-specific settings, and generate reports. They also provide Super Admins with platform-wide visibility for monitoring and scaling decisions. Additionally, this guide covers biometric authentication, OAuth integration, external guarantors, and membership document management.

---

## Accounts Endpoints

### POST /api/v1/accounts/oauth/google/callback/

**Purpose:** Handles Google Sign-In and Sign-Up callback with a flow parameter. This enables users to authenticate using their Google account, reducing friction in the onboarding process—critical for scaling user acquisition.

**Input Requirements:**
- Headers:
  - `Content-Type: application/json`
- Body:
```json
{
  "code": "google_oauth_code",
  "flow": "signup"
}
```

**Expected Outcome:**
```json
{
  "access": "jwt_access_token",
  "refresh": "jwt_refresh_token",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "first_name": "John",
    "last_name": "Doe"
  },
  "sacco_context": {
    "active_saccos": [],
    "pending_applications": []
  }
}
```

**Integration Tip:** Implement Google OAuth flow using the official Google Identity Services SDK. The `flow` parameter distinguishes between new sign-ups and returning sign-ins. Handle both scenarios appropriately in your frontend.

---

### POST /api/v1/accounts/device/register/

**Purpose:** Registers a biometric device (Face ID, Touch ID) for authentication. This enables passwordless authentication, improving security and user experience—essential for scaling mobile adoption.

**Input Requirements:**
- Headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer {{access_token}}`
- Body:
```json
{
  "device_name": "iPhone 14 Pro",
  "device_type": "ios",
  "device_identifier": "unique_device_id"
}
```

**Expected Outcome:**
```json
{
  "id": "uuid",
  "device_name": "iPhone 14 Pro",
  "device_type": "ios",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**Integration Tip:** Use platform-specific biometric APIs (Face ID for iOS, BiometricPrompt for Android). Store the returned device identifier locally for future authentication attempts. Limit the number of registered devices per user (e.g., 3 devices max).

---

### GET /api/v1/accounts/devices/

**Purpose:** Lists all registered biometric devices for the authenticated user. This allows users to manage their trusted devices and revoke access if needed—critical for security at scale.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Query params: None

**Expected Outcome:**
```json
[
  {
    "id": "uuid",
    "device_name": "iPhone 14 Pro",
    "device_type": "ios",
    "last_used_at": "2024-01-15T10:30:00Z",
    "created_at": "2024-01-10T14:20:00Z"
  }
]
```

**Integration Tip:** Display this list in a "Security Settings" section. Allow users to revoke devices with a confirmation dialog. Show the last used timestamp to help users identify inactive devices.

---

### DELETE /api/v1/accounts/device/<device_id>/

**Purpose:** Revokes a registered biometric device, preventing future authentication attempts from that device. This is essential for security when devices are lost or compromised.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Query params: None

**Expected Outcome:**
```json
{
  "success": true,
  "message": "Device revoked successfully"
}
```

**Integration Tip:** Implement a confirmation modal before revoking, explaining that the user will need to re-authenticate with their password on that device. Refresh the device list after successful revocation.

---

### GET /api/v1/accounts/public-stats/

**Purpose:** Returns public platform statistics without authentication. This can be used on landing pages and marketing materials to showcase platform growth—critical for user acquisition at scale.

**Input Requirements:**
- Headers: None
- Query params: None

**Expected Outcome:**
```json
{
  "total_saccos": 150,
  "total_members": 45000,
  "total_loans_disbursed": 500000000.00,
  "active_saccos": 145
}
```

**Integration Tip:** Cache this data for 5 minutes on the client side. Display these statistics prominently on your landing page with animated counters for visual appeal.

---

### GET /api/v1/accounts/me/ (Updated)

**Purpose:** Returns the authenticated user's profile with SACCO context. The response now includes `sacco_context` to help the frontend determine which SACCOs the user belongs to and which applications are pending.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Query params: None

**Expected Outcome:**
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "phone_number": "+254712345678",
  "sacco_context": {
    "active_saccos": [
      {
        "sacco_id": "uuid",
        "sacco_name": "Teachers SACCO",
        "membership_id": "uuid",
        "role": "MEMBER"
      }
    ],
    "pending_applications": [
      {
        "application_id": "uuid",
        "sacco_name": "Health Workers SACCO",
        "status": "PENDING",
        "applied_at": "2024-01-15T10:30:00Z"
      }
    ]
  }
}
```

**Integration Tip:** Use `sacco_context` to drive your SACCO switcher UI. Show pending applications in a "Join SACCO" flow. Cache this data locally to reduce API calls during session.

---

### POST /api/v1/accounts/login/ (Updated)

**Purpose:** Authenticates user credentials and returns JWT tokens. The response now includes `sacco_context` for immediate SACCO context after login.

**Input Requirements:**
- Headers:
  - `Content-Type: application/json`
- Body:
```json
{
  "email": "user@example.com",
  "password": "SecurePass123"
}
```

**Expected Outcome:**
```json
{
  "access": "jwt_access_token",
  "refresh": "jwt_refresh_token",
  "sacco_context": {
    "active_saccos": [...],
    "pending_applications": [...]
  }
}
```

**Integration Tip:** After successful login, use `sacco_context` to automatically select the user's primary SACCO or prompt them to choose from multiple SACCOs. Store the context in your state management library.

---

## SACCO Membership Endpoints

### POST /api/v1/members/applications/<application_id>/documents/

**Purpose:** Uploads supporting documents (payslips, ID cards, bank statements) for a membership application. This enables SACCOs to verify member eligibility—critical for compliance and risk management at scale.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Body (formdata):
  - `document_type`: `payslip` | `id_card` | `bank_statement`
  - `file`: The document file

**Expected Outcome:**
```json
{
  "id": "uuid",
  "document_type": "payslip",
  "file_url": "https://...",
  "uploaded_at": "2024-01-15T10:30:00Z"
}
```

**Integration Tip:** Implement file upload with progress indicators. Validate file types and sizes on the client side before upload. Allow multiple document uploads per application. Show a preview of uploaded documents.

---

### GET /api/v1/members/applications/<application_id>/documents/

**Purpose:** Lists all documents uploaded for a membership application. This allows users to review their submissions and SACCO admins to verify documentation.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Query params: None

**Expected Outcome:**
```json
[
  {
    "id": "uuid",
    "document_type": "payslip",
    "file_url": "https://...",
    "uploaded_at": "2024-01-15T10:30:00Z"
  }
]
```

**Integration Tip:** Display documents in a grid with thumbnails for images. Allow users to download or view documents in a modal. Show the upload date to help users identify recent submissions.

---

### DELETE /api/v1/members/applications/<application_id>/documents/<id>/

**Purpose:** Removes a document from a membership application. This allows users to correct mistakes or update expired documents.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Query params: None

**Expected Outcome:**
```json
{
  "success": true,
  "message": "Document deleted successfully"
}
```

**Integration Tip:** Implement a confirmation dialog before deletion. Refresh the document list after successful deletion. Allow re-uploading immediately after deletion.

---

## Services Endpoints

### POST /api/v1/services/loans/<loan_id>/external-guarantors/

**Purpose:** Submits an external guarantor (non-member) for a loan. This enables borrowers to use guarantors outside their SACCO—critical for increasing loan approval rates.

**Input Requirements:**
- Headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer {{access_token}}`
- Body:
```json
{
  "full_name": "Jane Smith",
  "phone_number": "+254723456789",
  "email": "jane@example.com",
  "guarantee_amount": 25000.00
}
```

**Expected Outcome:**
```json
{
  "id": "uuid",
  "full_name": "Jane Smith",
  "phone_number": "+254723456789",
  "status": "PENDING",
  "response_token": "abc123xyz",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**Integration Tip:** Validate phone number format and email address before submission. Show the `response_token` to the user so they can share it with the external guarantor. Send the guarantor an SMS/email with the response link.

---

### GET /api/v1/services/loans/<loan_id>/external-guarantors/

**Purpose:** Lists all external guarantors submitted for a loan. This allows borrowers to track the status of their external guarantor requests.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Query params: None

**Expected Outcome:**
```json
[
  {
    "id": "uuid",
    "full_name": "Jane Smith",
    "phone_number": "+254723456789",
    "status": "ACCEPTED",
    "guarantee_amount": 25000.00,
    "responded_at": "2024-01-15T11:30:00Z"
  }
]
```

**Integration Tip:** Display status badges (PENDING = yellow, ACCEPTED = green, DECLINED = red). Show the responded date for completed requests. Allow re-sending the request link for pending guarantors.

---

## Guarantors Endpoints

### POST /api/v1/guarantors/external/respond/<response_token>/

**Purpose:** Allows external guarantors to accept or decline guarantee requests via a unique token. This enables non-members to participate in the guarantor process without creating accounts—critical for scaling loan approvals.

**Input Requirements:**
- Headers:
  - `Content-Type: application/json`
- Body:
```json
{
  "response": "ACCEPT",
  "notes": "I accept to be a guarantor"
}
```

**Expected Outcome:**
```json
{
  "success": true,
  "message": "Guarantor response recorded successfully",
  "status": "ACCEPTED"
}
```

**Integration Tip:** Create a dedicated public-facing page for external guarantors. The URL should include the `response_token` as a query parameter. Validate the token before showing the form. Show a success message after submission.

---

## SACCO Admin Endpoints

### GET /api/v1/management/loans/approvals/

**Purpose:** Provides SACCO admins with a dedicated view of all pending loans requiring approval. This endpoint centralizes loan approval workflows, enabling admins to efficiently review and process loan applications without navigating through individual member profiles. This is critical for scaling as loan volumes grow.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (JWT token)
  - `X-Sacco-ID: {{sacco_id}}` (SACCO context header)
- Query params: None

**Expected Outcome:**
```json
{
  "results": [
    {
      "loan_id": "uuid",
      "member_name": "John Doe",
      "member_number": "001",
      "loan_type_name": "Emergency Loan",
      "amount": 50000.00,
      "term_months": 12,
      "application_notes": "Medical expenses",
      "applied_at": "2024-01-15T10:30:00Z",
      "status": "PENDING_APPROVAL",
      "guarantors_summary": {
        "internal_approved": 2,
        "external_approved": 1,
        "total_coverage": 75000.00
      },
      "required_documents": [
        {
          "document_type": "payslip",
          "file_url": "https://...",
          "uploaded_at": "2024-01-15T10:25:00Z"
        }
      ]
    }
  ]
}
```

**Integration Tip:** Implement a paginated table view with status badges (PENDING_APPROVAL = yellow, UNDER_REVIEW = blue). Add quick-action buttons for Approve/Reject that call the loan status update endpoint. Cache this data for 30 seconds to reduce load during high-volume periods.

---

### GET /api/v1/management/reports/

**Purpose:** Enables SACCO admins to generate on-demand reports for loans, contributions, and member growth within specific date ranges. This replaces manual spreadsheet work and provides data-driven insights for SACCO management decisions as the member base scales.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
- Query params:
  - `type` (required): `loans` | `contributions` | `members`
  - `from_date` (required): YYYY-MM-DD format
  - `to_date` (required): YYYY-MM-DD format

**Expected Outcome (type=loans):**
```json
{
  "type": "loans",
  "period": "2024-01-01 to 2024-12-31",
  "total_loans_disbursed": 150,
  "total_amount_disbursed": 7500000.00,
  "total_amount_repaid": 5200000.00,
  "outstanding_balance": 2300000.00,
  "default_rate": 3.2,
  "by_loan_type": [
    {
      "loan_type": "Emergency Loan",
      "count": 80,
      "amount": 4000000.00
    }
  ]
}
```

**Expected Outcome (type=contributions):**
```json
{
  "type": "contributions",
  "period": "2024-01-01 to 2024-12-31",
  "monthly_breakdown": [
    {
      "month": "2024-01",
      "total_contributions": 150000.00,
      "member_count": 120
    }
  ]
}
```

**Expected Outcome (type=members):**
```json
{
  "type": "members",
  "period": "2024-01-01 to 2024-12-31",
  "new_members": 45,
  "active_members": 320,
  "churn_rate": 2.5
}
```

**Integration Tip:** Use a date range picker component with preset options (This Month, Last Month, This Year, Custom). For large date ranges, show a loading indicator as report generation may take 2-5 seconds. Consider implementing client-side charting libraries (Chart.js, Recharts) for visualizing monthly breakdowns.

---

### GET /api/v1/management/settings/

**Purpose:** Retrieves SACCO-specific configuration settings that control loan limits, guarantor requirements, and contribution amounts. This enables each SACCO to customize their lending rules based on their risk appetite and member demographics, essential for scaling across diverse SACCO types.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
- Query params: None

**Expected Outcome:**
```json
{
  "min_loan_amount": 1000.00,
  "max_loan_amount": 500000.00,
  "loan_multiplier": 3,
  "requires_guarantor": true,
  "guarantor_type_allowed": "BOTH",
  "registration_fee": 500.00,
  "monthly_contribution_amount": 1000.00
}
```

**Integration Tip:** Display these settings in a read-only view initially, with an "Edit" button that opens a form. Show real-time validation when changing loan_multiplier (e.g., if member has 10,000 in savings, show calculated max loan = 30,000). Cache these settings locally to avoid repeated API calls during loan eligibility checks.

---

### PATCH /api/v1/management/settings/

**Purpose:** Allows SACCO admins to update their configuration settings. This is critical for scaling as it enables SACCOs to self-manage their lending rules without requiring platform administrator intervention.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
  - `Content-Type: application/json`
- Body (all fields optional):
```json
{
  "min_loan_amount": 1000.00,
  "max_loan_amount": 500000.00,
  "loan_multiplier": 3,
  "requires_guarantor": true,
  "guarantor_type_allowed": "BOTH",
  "registration_fee": 500.00,
  "monthly_contribution_amount": 1000.00
}
```

**Expected Outcome:**
```json
{
  "min_loan_amount": 1000.00,
  "max_loan_amount": 500000.00,
  "loan_multiplier": 3,
  "requires_guarantor": true,
  "guarantor_type_allowed": "BOTH",
  "registration_fee": 500.00,
  "monthly_contribution_amount": 1000.00
}
```

**Integration Tip:** Implement a confirmation modal before saving changes, especially for loan_multiplier and max_loan_amount as these affect all pending loan applications. Show a success toast and refresh the local cache. Consider adding an audit log view to track who changed settings and when.

---

## Super Admin Endpoints

### GET /api/v1/management/superadmin/overview/

**Purpose:** Provides Super Admins with a real-time snapshot of platform health across all SACCOs. This is essential for scaling decisions—identifying growth trends, revenue patterns, and operational issues at the platform level rather than per-SACCO.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (SUPER_ADMIN role required)
- Query params: None

**Expected Outcome:**
```json
{
  "platform_transaction_volume_mtd": 15000000.00,
  "platform_transaction_volume_change_pct": 12.5,
  "active_saccos_count": 45,
  "active_saccos_change_this_month": 3,
  "total_members": 12500,
  "total_members_change_this_month": 150,
  "platform_revenue_mtd": 300000.00,
  "all_systems_operational": true
}
```

**Integration Tip:** Display this data in a dashboard card layout with trend indicators (green up arrow for positive change, red down arrow for negative). Use the `all_systems_operational` flag to trigger a banner alert if false. Auto-refresh this data every 60 seconds to keep the dashboard current.

---

### GET /api/v1/management/superadmin/revenue-chart/

**Purpose:** Returns 12 months of revenue breakdown separated by SaaS fees (subscriptions) and transaction fees. This enables Super Admins to analyze revenue composition and identify which revenue stream is driving growth—critical for pricing strategy and business planning as the platform scales.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (SUPER_ADMIN role required)
- Query params: None

**Expected Outcome:**
```json
[
  {
    "month": "2024-01",
    "saas_fees": 25000.00,
    "transaction_fees": 5000.00,
    "total_mrr": 30000.00
  },
  {
    "month": "2024-02",
    "saas_fees": 27000.00,
    "transaction_fees": 6000.00,
    "total_mrr": 33000.00
  }
]
```

**Integration Tip:** Use a stacked bar chart or line chart to visualize revenue trends. Show tooltips on hover with exact values. Consider adding a toggle to switch between absolute values and percentage growth. This data is computationally expensive, so cache it for 5 minutes.

---

### GET /api/v1/management/superadmin/top-saccos/

**Purpose:** Identifies the top 10 SACCOs by transaction volume each month. This helps Super Admins recognize high-performing SACCOs for case studies, identify SACCOs that may need support, and understand which SACCOs drive the majority of platform revenue—key insights for prioritizing resources as the platform scales.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (SUPER_ADMIN role required)
- Query params: None

**Expected Outcome:**
```json
[
  {
    "sacco_id": "uuid",
    "sacco_name": "Teachers SACCO",
    "member_count": 850,
    "txn_volume_this_month": 2500000.00,
    "platform_fee_this_month": 50000.00,
    "health_status": "GOOD"
  },
  {
    "sacco_id": "uuid",
    "sacco_name": "Health Workers SACCO",
    "member_count": 620,
    "txn_volume_this_month": 1800000.00,
    "platform_fee_this_month": 36000.00,
    "health_status": "REVIEW"
  }
]
```

**Integration Tip:** Display as a leaderboard table with health status badges (GOOD = green, REVIEW = yellow, API_ISSUE = red). Make the SACCO name clickable to navigate to a detailed SACCO view. Show percentage contribution to total platform volume in a progress bar.

---

### GET /api/v1/management/superadmin/alerts/

**Purpose:** Returns all open compliance flags ordered by severity (CRITICAL first). This is the Super Admin's triage view for operational issues across all SACCOs—essential for maintaining platform health and trust as transaction volumes increase.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (SUPER_ADMIN role required)
- Query params: None

**Expected Outcome:**
```json
[
  {
    "sacco_name": "Teachers SACCO",
    "flag_type": "API_ISSUE",
    "description": "High rate of failed STK push callbacks",
    "severity": "CRITICAL",
    "created_at": "2024-01-15T14:30:00Z"
  },
  {
    "sacco_name": "Health Workers SACCO",
    "flag_type": "PAYMENT_FAILURE",
    "description": "Multiple B2C disbursement failures",
    "severity": "HIGH",
    "created_at": "2024-01-14T09:15:00Z"
  }
]
```

**Integration Tip:** Display alerts in a card layout with color-coded severity borders (CRITICAL = red, HIGH = orange, MEDIUM = yellow, LOW = blue). Implement real-time polling every 30 seconds for this endpoint. Add a "Resolve" button that opens a modal to mark the flag as resolved with notes.

---

### GET /api/v1/management/superadmin/transactions/live/

**Purpose:** Provides a near real-time feed of the last 50 M-Pesa transactions across all SACCOs. This enables Super Admins to monitor platform activity, detect anomalies, and ensure payment systems are functioning correctly—critical for maintaining trust as transaction volumes scale.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (SUPER_ADMIN role required)
- Query params: None

**Expected Outcome:**
```json
[
  {
    "sacco_name": "Teachers SACCO",
    "user_name": "John Doe",
    "amount": 5000.00,
    "transaction_type": "DEPOSIT",
    "stk_status": "0",
    "created_at": "2024-01-15T15:45:00Z"
  },
  {
    "sacco_name": "Health Workers SACCO",
    "user_name": "Jane Smith",
    "amount": 10000.00,
    "transaction_type": "LOAN_REPAYMENT",
    "stk_status": "0",
    "created_at": "2024-01-15T15:44:00Z"
  }
]
```

**Integration Tip:** Display as a scrolling list with the newest transactions at the top. Auto-refresh every 10 seconds (the cache timeout). Use WebSocket or Server-Sent Events if available for true real-time updates. Add filters by transaction type and SACCO for focused monitoring.

---

### GET /api/v1/management/superadmin/saccos/

**Purpose:** Lists all SACCOs with health status, member counts, and last transaction timestamps. This provides Super Admins with a comprehensive view of the SACCO ecosystem—essential for onboarding decisions, support prioritization, and understanding platform composition as the number of SACCOs scales.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (SUPER_ADMIN role required)
- Query params: None

**Expected Outcome:**
```json
[
  {
    "id": "uuid",
    "name": "Teachers SACCO",
    "member_count": 850,
    "is_active": true,
    "created_at": "2023-06-15T10:00:00Z",
    "health_status": "GOOD",
    "last_transaction_at": "2024-01-15T15:45:00Z"
  },
  {
    "id": "uuid",
    "name": "Health Workers SACCO",
    "member_count": 620,
    "is_active": true,
    "created_at": "2023-08-20T14:30:00Z",
    "health_status": "REVIEW",
    "last_transaction_at": "2024-01-15T14:30:00Z"
  }
]
```

**Integration Tip:** Display as a data table with sortable columns (member_count, created_at, last_transaction_at). Add search/filter by name and health status. Implement infinite scroll or pagination for large SACCO counts. Show an "inactive" badge for SACCOs where `is_active` is false.

---

### GET /api/v1/management/superadmin/members/

**Purpose:** Provides a paginated directory of all members across all SACCOs with search and SACCO filtering. This enables Super Admins to investigate specific users, understand cross-SACCO membership patterns, and perform platform-wide user management—necessary for compliance and support as the user base scales.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}` (SUPER_ADMIN role required)
- Query params:
  - `sacco_id` (optional): Filter by specific SACCO
  - `search` (optional): Search by email or name
  - `page` (optional): Page number (default: 1)
  - `page_size` (optional): Items per page (default: 20, max: 100)

**Expected Outcome:**
```json
{
  "count": 12500,
  "next": "http://.../members/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "full_name": "John Doe",
      "email": "john@example.com",
      "phone_number": "+254712345678",
      "kyc_status": "APPROVED",
      "member_since": "2023-06-15"
    }
  ]
}
```

**Integration Tip:** Implement a search bar with debounced input (300ms delay) to avoid excessive API calls. Use server-side pagination with "Load More" button at the bottom. Add a "View Details" button that opens a modal with the member's SACCO memberships and transaction history. Consider exporting this data to CSV for compliance reporting.

---

## Authentication & Authorization Notes

All endpoints require JWT Bearer token authentication. The `X-Sacco-ID` header is required for SACCO Admin endpoints to enforce multi-tenancy. Super Admin endpoints bypass SACCO context checks but require the SUPER_ADMIN role.

**Permission Matrix:**

| Endpoint | MEMBER | SACCO_ADMIN | SUPER_ADMIN |
|----------|--------|-------------|-------------|
| /loans/approvals/ | 403 | 200 | 200 |
| /reports/ | 403 | 200 | 200 |
| /settings/ | 403 | 200 | 200 |
| /superadmin/* | 403 | 403 | 200 |

---

## Performance Considerations

- **Caching Strategy:** Super Admin endpoints with heavy aggregations (overview, revenue-chart, top-saccos) should be cached client-side for 1-5 minutes. The live transaction feed has a 10-second server-side cache.
- **Pagination:** Always use pagination for list endpoints to prevent memory issues as data scales.
- **Debouncing:** Implement debounced search inputs (300-500ms) to avoid API spamming.
- **Error Handling:** Implement retry logic with exponential backoff for failed requests. Show user-friendly error messages that distinguish between permission errors (403) and server errors (500).

---

## Testing Checklist

Before deploying to production, verify:

- [ ] SACCO Admin can access their endpoints with valid X-Sacco-ID header
- [ ] SACCO Admin cannot access Super Admin endpoints (403 response)
- [ ] Super Admin can access all Super Admin endpoints
- [ ] Super Admin cannot access SACCO-specific endpoints without X-Sacco-ID
- [ ] Loan approvals endpoint returns guarantors_summary correctly
- [ ] Reports endpoint validates date ranges (from_date ≤ to_date)
- [ ] Settings endpoint updates persist and are reflected in subsequent GET requests
- [ ] System overview shows correct change percentages
- [ ] Revenue chart returns exactly 12 months of data
- [ ] Top SACCOs are ordered by transaction volume descending
- [ ] Platform alerts show CRITICAL flags first
- [ ] Live transaction feed refreshes with new data after 10 seconds
- [ ] All SACCOs endpoint includes health_status calculation
- [ ] Members endpoint pagination works correctly
