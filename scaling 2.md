# Scaling 2 API Guide

This document details the second set of scaling-related endpoints added to SaccoSphere. These endpoints cover liquidity monitoring, non-performing loan tracking, CRB checks, dividend workflows, SASRA returns, bulk SMS campaigns, and payment callbacks.

---

## Health Endpoints

### GET /api/v1/health/live/

**Purpose:** Confirms that the API process is alive through the versioned API path. This is useful for load balancers and deployment health checks.

**Input Requirements:**
- Headers: None
- Query params: None

**Expected Outcome:**
```json
{
  "status": "ok"
}
```

**Integration Tip:** Use this endpoint for simple uptime checks. It should not be used to prove that database-dependent features are ready.

---

### GET /api/v1/health/ready/

**Purpose:** Confirms that the API is ready to serve traffic, including dependency checks such as the database and cache.

**Input Requirements:**
- Headers: None
- Query params: None

**Expected Outcome:**
```json
{
  "status": "ready"
}
```

**Integration Tip:** Use this endpoint for Kubernetes readiness probes or deployment gates before routing user traffic.

---

## Services Endpoints

### POST /api/v1/services/savings-types/

**Purpose:** Creates a SACCO savings type such as BOSA, FOSA, or share capital. This allows admins to configure products without code changes.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `Content-Type: application/json`
- Body:
```json
{
  "name": "BOSA",
  "description": "Back office savings account",
  "minimum_contribution": "1000.00",
  "interest_rate": "0.00",
  "sacco": "{{sacco_id}}"
}
```

**Expected Outcome:**
```json
{
  "id": "uuid",
  "name": "BOSA",
  "minimum_contribution": "1000.00",
  "interest_rate": "0.00",
  "sacco": "uuid"
}
```

**Integration Tip:** Only admin users can create savings types. Keep money fields as strings so the backend can process them as Decimal values.

---

### GET /api/v1/services/savings-types/<savings_type_id>/

**Purpose:** Retrieves one savings type for product detail pages or loan/savings setup screens.

**Input Requirements:**
- Headers: None
- Query params: None

**Expected Outcome:**
```json
{
  "id": "uuid",
  "name": "BOSA",
  "minimum_contribution": "1000.00",
  "interest_rate": "0.00"
}
```

**Integration Tip:** This endpoint is public like the list endpoint, so it can be used before login when showing SACCO product information.

---

### PATCH /api/v1/services/savings-types/<savings_type_id>/

**Purpose:** Updates a savings type, such as changing the minimum contribution amount.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `Content-Type: application/json`
- Body:
```json
{
  "minimum_contribution": "1500.00"
}
```

**Expected Outcome:**
```json
{
  "id": "uuid",
  "minimum_contribution": "1500.00"
}
```

**Integration Tip:** Show a warning before changing contribution rules because they can affect member eligibility calculations.

---

### DELETE /api/v1/services/savings-types/<savings_type_id>/

**Purpose:** Deletes a savings type. This should be reserved for setup mistakes, not active products with member balances.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`

**Expected Outcome:**
```json
{}
```

**Integration Tip:** Prefer deactivation over deletion if a savings type has ever been used by members.

---

### GET /api/v1/services/loans/

**Purpose:** Provides a collection-level loans endpoint for the authenticated user. This supports scalable member loan dashboards.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
- Query params: Optional filters supported by the backend view.

**Expected Outcome:**
```json
{
  "success": true,
  "data": []
}
```

**Integration Tip:** Use this as the main loan overview source when the frontend needs a compact list before opening a specific loan detail page.

---

### POST /api/v1/services/loans/<loan_id>/crb-check/?force_refresh=false

**Purpose:** Runs a CRB check for a loan application or returns a recent cached check. This helps SACCO admins make credit decisions at scale.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
- Query params:
  - `force_refresh` optional boolean. Use `true` to bypass the 30-day cache.

**Expected Outcome:**
```json
{
  "id": "uuid",
  "score": 720,
  "band": "LOW_RISK",
  "listed_negative": false,
  "provider": "metropol",
  "reference": "CRB-REFERENCE",
  "checked_at": "2026-07-16T09:30:00+03:00",
  "cached": false
}
```

**Integration Tip:** Show whether the result is cached so admins know if they are using a recent prior check or a fresh provider response. Production CRB access still depends on active provider credentials and agreement terms.

---

## Dividend Endpoints

### GET /api/v1/services/dividends/declarations/

**Purpose:** Lists dividend declarations for the SACCO context.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "financial_year": 2026,
      "declared_rate": "12.50",
      "status": "DRAFT"
    }
  ]
}
```

**Integration Tip:** Display declarations as a workflow list: Draft, Calculated, Approved, and Disbursed.

---

### POST /api/v1/services/dividends/declarations/

**Purpose:** Creates a draft dividend declaration for a financial year and savings type.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
  - `Content-Type: application/json`
- Body:
```json
{
  "financial_year": 2026,
  "declared_rate": "12.50",
  "savings_type": "{{savings_type_id}}"
}
```

**Expected Outcome:**
```json
{
  "id": "uuid",
  "financial_year": 2026,
  "declared_rate": "12.50",
  "status": "DRAFT"
}
```

**Integration Tip:** Send money and rate values as strings from the frontend to preserve Decimal precision.

---

### GET /api/v1/services/dividends/declarations/<dividend_declaration_id>/

**Purpose:** Retrieves one dividend declaration.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "id": "uuid",
  "financial_year": 2026,
  "declared_rate": "12.50",
  "status": "DRAFT"
}
```

**Integration Tip:** Use this before editing so the admin sees the latest workflow status.

---

### PATCH /api/v1/services/dividends/declarations/<dividend_declaration_id>/

**Purpose:** Updates a draft dividend declaration before it is calculated.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
  - `Content-Type: application/json`
- Body:
```json
{
  "declared_rate": "13.00"
}
```

**Expected Outcome:**
```json
{
  "id": "uuid",
  "declared_rate": "13.00",
  "status": "DRAFT"
}
```

**Integration Tip:** Disable edits once the declaration leaves `DRAFT`.

---

### DELETE /api/v1/services/dividends/declarations/<dividend_declaration_id>/

**Purpose:** Deletes a draft dividend declaration.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{}
```

**Integration Tip:** Show a confirmation dialog because this removes the draft workflow.

---

### POST /api/v1/services/dividends/declarations/<dividend_declaration_id>/calculate/

**Purpose:** Calculates member dividend payouts for a draft declaration.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "total_dividend_amount": "250000.00",
  "payout_count": 180
}
```

**Integration Tip:** Treat this as a long-running business action in the UI. Show a loading state and refresh the declaration after completion.

---

### POST /api/v1/services/dividends/declarations/<dividend_declaration_id>/approve/

**Purpose:** Approves a calculated dividend declaration before disbursement.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "id": "uuid",
  "status": "APPROVED",
  "approved_by": "admin@example.com"
}
```

**Integration Tip:** Only show this action when the declaration status is `CALCULATED`.

---

### POST /api/v1/services/dividends/declarations/<dividend_declaration_id>/disburse/

**Purpose:** Posts approved dividend payouts into member savings accounts and creates ledger entries.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "id": "uuid",
  "status": "DISBURSED",
  "paid_count": 180
}
```

**Integration Tip:** Use a final confirmation step because this action changes member balances.

---

### GET /api/v1/services/dividends/payouts/?declaration=<dividend_declaration_id>

**Purpose:** Lists dividend payouts, optionally filtered by declaration.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
- Query params:
  - `declaration` optional declaration UUID.

**Expected Outcome:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "dividend_amount": "1250.00",
      "status": "PENDING"
    }
  ]
}
```

**Integration Tip:** Use this endpoint for payout audit screens and member-level drilldowns.

---

## SACCO Admin Endpoints

### GET /api/v1/management/reports/sasra/

**Purpose:** Generates SASRA regulatory returns for PAR, financial position, or membership reporting.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
- Query params:
  - `type`: `par` | `financial_position` | `membership`
  - `as_of_date`: YYYY-MM-DD
  - `period_start`: YYYY-MM-DD, membership report only
  - `period_end`: YYYY-MM-DD, membership report only
  - `format`: `json` | `xlsx`

**Expected Outcome:**
```json
{
  "sacco_id": "uuid",
  "as_of_date": "2026-07-16",
  "categories": {},
  "par30_ratio": "0.0500",
  "par90_ratio": "0.0200"
}
```

**Integration Tip:** For `format=xlsx`, trigger a file download in the frontend. These reports still require compliance sign-off before official regulatory submission.

---

### GET /api/v1/management/sms/campaigns/

**Purpose:** Lists bulk SMS campaigns for the current SACCO.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "message": "Monthly contribution reminder",
      "status": "DRAFT",
      "total_recipients": 120,
      "sent_count": 0,
      "failed_count": 0
    }
  ]
}
```

**Integration Tip:** Display campaign status badges and recipient counts so admins understand reach before sending.

---

### POST /api/v1/management/sms/campaigns/

**Purpose:** Creates a draft bulk SMS campaign and recipient preview.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
  - `Content-Type: application/json`
- Body:
```json
{
  "message": "Member reminder: monthly contributions are due by Friday.",
  "audience_filter": {
    "status": "APPROVED"
  }
}
```

**Expected Outcome:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "status": "DRAFT",
    "total_recipients": 120
  }
}
```

**Integration Tip:** Enforce the SMS character limit in the frontend and show the estimated recipient count before sending.

---

### GET /api/v1/management/sms/campaigns/<campaign_id>/

**Purpose:** Retrieves a campaign with recipient delivery statuses.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "status": "SENDING",
    "recipients": []
  }
}
```

**Integration Tip:** Refresh this view after sending to show delivery progress and failed recipients.

---

### POST /api/v1/management/sms/campaigns/<campaign_id>/send/

**Purpose:** Queues a draft bulk SMS campaign for background sending.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "status": "SENDING"
  }
}
```

**Integration Tip:** This endpoint depends on the Celery worker and SMS provider configuration. Disable the send button after the first click.

---

### GET /api/v1/management/liquidity/

**Purpose:** Returns current liquidity risk and recent liquidity alerts for the SACCO.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "success": true,
  "data": {
    "sacco_id": "uuid",
    "sacco_name": "Teachers SACCO",
    "current": {
      "available_reserves": "800000.00",
      "pending_disbursements": "720000.00",
      "utilisation_pct": "90.00",
      "at_risk": true
    },
    "recent_alerts": []
  }
}
```

**Integration Tip:** Highlight `at_risk=true` clearly on the admin dashboard before approving more disbursements.

---

### GET /api/v1/management/npl/

**Purpose:** Returns unresolved non-performing loan warning counts and portfolio NPL ratio.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`

**Expected Outcome:**
```json
{
  "success": true,
  "data": {
    "unresolved_counts": {
      "30": 4,
      "60": 2,
      "90": 1
    },
    "npl_outstanding_balance": "100000.00",
    "active_outstanding_balance": "2000000.00",
    "npl_ratio": "0.0500"
  }
}
```

**Integration Tip:** Use threshold badges for 30, 60, and 90 day warnings so credit teams can prioritize follow-up.

---

## Management Dividend Endpoints

These endpoints mirror the services dividend workflow under the SACCO management namespace:

- `GET /api/v1/management/dividends/declarations/`
- `POST /api/v1/management/dividends/declarations/`
- `GET /api/v1/management/dividends/declarations/<dividend_declaration_id>/`
- `PATCH /api/v1/management/dividends/declarations/<dividend_declaration_id>/`
- `DELETE /api/v1/management/dividends/declarations/<dividend_declaration_id>/`
- `POST /api/v1/management/dividends/declarations/<dividend_declaration_id>/calculate/`
- `POST /api/v1/management/dividends/declarations/<dividend_declaration_id>/approve/`
- `POST /api/v1/management/dividends/declarations/<dividend_declaration_id>/disburse/`
- `GET /api/v1/management/dividends/payouts/?declaration=<dividend_declaration_id>`

**Purpose:** Provides the same dividend lifecycle from the admin namespace, which is useful when the frontend groups all SACCO admin workflows under `/management`.

**Input Requirements:**
- Headers:
  - `Authorization: Bearer {{access_token}}`
  - `X-Sacco-ID: {{sacco_id}}`
  - `Content-Type: application/json` for create/update requests.

**Expected Outcome:** Same response shape as the services dividend endpoints.

**Integration Tip:** Pick one namespace for the frontend navigation to avoid duplicate menu actions. The collection includes both because both routes exist in the backend.

---

## Payments Endpoints

### POST /api/v1/payments/callback/mpesa/stk/

**Purpose:** Receives Safaricom STK push callback payloads. This endpoint is public because Safaricom calls it directly.

**Input Requirements:**
- Headers:
  - `Content-Type: application/json`
- Body:
```json
{
  "Body": {
    "stkCallback": {
      "MerchantRequestID": "29115-34620561-1",
      "CheckoutRequestID": "ws_CO_16072026123456789",
      "ResultCode": 0,
      "ResultDesc": "The service request is processed successfully."
    }
  }
}
```

**Expected Outcome:**
```json
{
  "ResultCode": 0,
  "ResultDesc": "Accepted"
}
```

**Integration Tip:** Do not call this from the normal frontend. Configure it in Daraja as the callback URL and keep IP/signature validation enabled.

---

### POST /api/v1/payments/callback/mpesa/b2c/

**Purpose:** Receives Safaricom B2C disbursement callback payloads for loan payouts.

**Input Requirements:**
- Headers:
  - `Content-Type: application/json`
- Body:
```json
{
  "Result": {
    "ConversationID": "AG_20260716_123456789",
    "ResultCode": 0,
    "ResultDesc": "The service request is processed successfully."
  }
}
```

**Expected Outcome:**
```json
{
  "ResultCode": 0,
  "ResultDesc": "Accepted"
}
```

**Integration Tip:** This endpoint depends on Daraja B2C credentials, callback URL allowlisting, Celery workers, and provider-side callback delivery.

---

## Dependency And Compliance Notes

- CRB checks require active Metropol or approved CRB provider credentials and legal approval for credit reference access.
- SASRA returns produced by the API still need compliance review before formal submission.
- Bulk SMS sending requires the SMS provider configuration and Celery worker to be running.
- M-Pesa callback endpoints require Daraja callback URLs, IP validation, and signature validation to be configured in production.
- All financial values should continue to be handled as Python `Decimal` values in backend calculations. Avoid JavaScript float math for money in the frontend; treat API money values as strings where possible.

---

## Testing Checklist

- [ ] SACCO admin can run a CRB check and receive cached results unless `force_refresh=true`.
- [ ] Dividend declaration can move through Draft, Calculated, Approved, and Disbursed states.
- [ ] Dividend disbursement creates ledger entries and updates member savings balances.
- [ ] SASRA PAR, financial position, and membership reports return JSON.
- [ ] SASRA `format=xlsx` downloads a spreadsheet.
- [ ] Bulk SMS draft shows the expected recipient count.
- [ ] Bulk SMS send queues the Celery task and changes status to `SENDING`.
- [ ] Liquidity endpoint marks high utilisation as at risk.
- [ ] NPL endpoint returns correct 30, 60, and 90 day unresolved counts.
- [ ] STK and B2C callbacks accept valid Safaricom payloads and reject invalid sources in production.
