# API Frontend Handover

## Base URL
- Local: `/api/v1/`
- Example full local URL: `http://localhost:8000/api/v1/`

## Auth (JWT Pattern)
1. Login with `POST /accounts/login/` or `POST /accounts/token/`.
2. Store `access` and `refresh` tokens securely.
3. Send header on protected endpoints:
   - `Authorization: Bearer <access_token>`
4. Refresh with `POST /accounts/token/refresh/`.

## Standard Response Shape
Most application endpoints use this wrapper:

```json
{
  "success": true,
  "message": "Optional message",
  "data": {},
  "errors": null,
  "error_code": null,
  "status_code": 200
}
```

Some raw DRF/APIView endpoints return plain payloads without wrapper (for example M-Pesa callbacks and some utility endpoints). Frontend should handle both wrapped and plain JSON.

## Pagination Shape
List endpoints using default pagination return:

```json
{
  "count": 123,
  "next": "http://localhost:8000/api/v1/.../?page=2",
  "previous": null,
  "results": []
}
```

## Error Codes Reference
- `400 BAD_REQUEST`: validation errors, missing params, invalid transitions.
- `401 UNAUTHORIZED`: missing/invalid token.
- `403 FORBIDDEN`: role/context mismatch (for example SACCO scope restrictions).
- `404 NOT_FOUND`: resource not found or inaccessible in current scope.
- `429 TOO_MANY_REQUESTS`: OTP throttling.
- `500 SERVER_ERROR`: unexpected backend failure.
- `502 BAD_GATEWAY`: upstream provider failure (Daraja/Africa's Talking).
- `503 SERVICE_UNAVAILABLE`: async import queue unavailable.

## M-Pesa STK Flow (Text Diagram)
1. Frontend calls `POST /payments/mpesa/stk-push/`.
2. Backend creates pending `Transaction` + `MpesaTransaction`.
3. User confirms PIN on phone.
4. Safaricom posts callback to `/payments/callback/mpesa/stk/`.
5. Backend validates callback and enqueues task.
6. Worker runs `process_stk_callback_task`:
   - marks payment completed/failed
   - updates savings/loan balances
   - writes ledger entry
   - creates notification
7. Frontend polls `GET /payments/mpesa/stk/{checkout_request_id}/status/`.

## Endpoints Table
| Method | URL | Auth | Role | Purpose |
|---|---|---|---|---|
| POST | `/accounts/register/` | No | Public | Register account |
| POST | `/accounts/login/` | No | Public | Login and issue JWT |
| POST | `/accounts/logout/` | Yes | User | Logout and blacklist refresh token |
| GET | `/accounts/me/` | Yes | User | Fetch profile |
| PATCH | `/accounts/me/` | Yes | User | Update profile |
| POST | `/accounts/password/change/` | Yes | User | Change password |
| POST | `/accounts/otp/send/` | No | Public | Send OTP |
| POST | `/accounts/otp/verify/` | No | Public | Verify OTP |
| POST | `/accounts/otp/resend/` | No | Public | Resend OTP |
| POST | `/accounts/password/reset/` | No | Public | Request reset OTP |
| POST | `/accounts/password/reset/confirm/` | No | Public | Confirm reset OTP |
| POST | `/accounts/kyc/upload/` | Yes | User | Upload KYC document |
| POST | `/accounts/kyc/submit-id/` | Yes | User | Submit ID for IPRS |
| GET | `/accounts/kyc/status/` | Yes | User | Get KYC status |
| GET | `/accounts/saccos/` | No | Public | List SACCOs |
| GET | `/accounts/saccos/{id}/` | No | Public | SACCO detail |
| GET | `/members/memberships/` | Yes | User | List member memberships |
| POST | `/members/memberships/` | Yes | User | Apply to SACCO |
| GET | `/members/memberships/{id}/` | Yes | User | Membership detail |
| POST | `/members/memberships/{id}/leave/` | Yes | User | Leave membership |
| GET | `/management/members/` | Yes | SACCO_ADMIN/SUPER_ADMIN | Admin member list |
| GET | `/management/members/{membership_id}/` | Yes | SACCO_ADMIN/SUPER_ADMIN | Admin member detail |
| GET | `/management/stats/` | Yes | SACCO_ADMIN/SUPER_ADMIN | Admin SACCO stats |
| PATCH | `/management/applications/{id}/review/` | Yes | SACCO_ADMIN/SUPER_ADMIN | Review membership application |
| PATCH | `/management/loans/{id}/status/` | Yes | SACCO_ADMIN/SUPER_ADMIN | Loan status workflow |
| POST | `/management/import/` | Yes | SACCO_ADMIN/SUPER_ADMIN | Start bulk member import |
| GET | `/management/import/{job_id}/` | Yes | SACCO_ADMIN/SUPER_ADMIN | Check import job status |
| GET | `/services/savings/` | Yes | User | List savings |
| GET | `/services/savings/breakdown/` | Yes | User | Savings breakdown |
| GET | `/services/loan-types/` | No | Public | List loan products |
| POST | `/services/loans/apply/` | Yes | User | Apply for loan |
| GET | `/services/loans/eligibility/` | Yes | User | Check eligibility |
| GET | `/services/loans/list/` | Yes | User | List own loans |
| GET | `/services/loans/{id}/` | Yes | User | Loan detail |
| GET | `/services/loans/{id}/schedule/` | Yes | User | Repayment schedule |
| GET | `/services/loans/{loan_id}/guarantors/search/` | Yes | User | Search guarantor |
| POST | `/services/loans/{loan_id}/guarantors/` | Yes | User | Request guarantor |
| POST | `/services/loans/{loan_id}/guarantors/{guarantor_id}/respond/` | Yes | Guarantor | Approve/decline guarantee |
| POST | `/payments/mpesa/stk-push/` | Yes | User | Start STK payment |
| GET | `/payments/mpesa/stk/{checkout_request_id}/status/` | Yes | User | STK status |
| POST | `/payments/mpesa/b2c/disburse/` | Yes | SACCO_ADMIN | Start loan disbursement |
| GET | `/payments/mpesa/b2c/{conversation_id}/status/` | Yes | SACCO_ADMIN | B2C status |
| GET | `/payments/mpesa/b2c/history/` | Yes | SACCO_ADMIN | B2C history |
| GET | `/dashboard/portfolio/` | Yes | User | Portfolio summary |
| GET | `/dashboard/state/` | Yes | User | Dashboard state |
| GET | `/dashboard/saccos/` | Yes | User | SACCO switcher |
| GET | `/dashboard/activity/` | Yes | User | Activity feed |
| GET | `/dashboard/loans/compare/` | Yes | User | Loan comparison |

## Dashboard States (Examples)
1. `NO_MEMBERSHIPS`
```json
{"state": "NO_MEMBERSHIPS", "message": "Join a SACCO to get started."}
```

2. `PENDING_APPROVAL`
```json
{"state": "PENDING_APPROVAL", "pending_count": 1}
```

3. `ACTIVE_NO_LOANS`
```json
{"state": "ACTIVE_NO_LOANS", "active_saccos": 1, "active_loans": 0}
```

4. `ACTIVE_WITH_LOANS`
```json
{"state": "ACTIVE_WITH_LOANS", "active_saccos": 2, "active_loans": 1}
```

5. `SUSPENDED_OR_RESTRICTED`
```json
{"state": "SUSPENDED_OR_RESTRICTED", "restricted_count": 1}
```

## Frontend Environment Variables
- `VITE_API_URL=http://localhost:8000/api/v1`
- `VITE_APP_NAME=SaccoSphere`
- `VITE_ENABLE_DEBUG=true`
- `VITE_REQUEST_TIMEOUT_MS=30000`
- `VITE_POLL_IMPORT_INTERVAL_MS=5000`
- `VITE_POLL_STK_INTERVAL_MS=4000`
