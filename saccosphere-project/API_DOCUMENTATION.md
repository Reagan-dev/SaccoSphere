# SaccoSphere Backend API Documentation

## Table of Contents
- [Base URL and Conventions](#base-url-and-conventions)
- [Authentication](#authentication)
- [Accounts](#accounts)
- [Membership](#membership)
- [Management (Admin)](#management-admin)
- [Services](#services)
- [Payments](#payments)
- [Notifications](#notifications)
- [Ledger](#ledger)
- [Dashboard](#dashboard)
- [Billing](#billing)
- [Health](#health)

## Base URL and Conventions
- Base API prefix: `/api/v1`
- Example host: `http://localhost:8000`
- Auth header: `Authorization: Bearer <access_token>`
- SACCO admin context header (where required): `X-Sacco-ID: <sacco_uuid>`
- Default pagination shape (list endpoints): `count`, `next`, `previous`, `results`

## Authentication
- JWT access/refresh tokens are used.
- Some endpoints return standard envelope:
```json
{ "success": true, "message": "...", "data": {} }
```
- Validation errors generally return `400`.

## Accounts
### POST `/api/v1/accounts/register/`
- Auth: No
- Body: `email` (string, req), `first_name` (string, req), `last_name` (string, req), `phone_number` (string, opt), `password` (string, req), `password2` (string, req)
- 201: user profile envelope
- Errors: 400 validation
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/register/ -H "Content-Type: application/json" -d '{"email":"user@example.com","first_name":"Jane","last_name":"Doe","password":"StrongPass1","password2":"StrongPass1"}'
```
- Postman: POST, JSON body above
- Check: `success=true`, user `id` returned

### POST `/api/v1/accounts/login/`
- Auth: No
- Body: `email`, `password` (req)
- 200: `{access, refresh, user}` envelope
- Errors: 401 invalid credentials
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/login/ -H "Content-Type: application/json" -d '{"email":"user@example.com","password":"StrongPass1"}'
```
- Check: access token present

### POST `/api/v1/accounts/logout/`
- Auth: Yes
- Body: `refresh` (req)
- 200: logged out message
- Errors: 400 missing/invalid refresh
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/logout/ -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d '{"refresh":"<refresh>"}'
```
- Check: `success=true`

### POST `/api/v1/accounts/token/`
- Auth: No
- Body: `email`, `password`
- 200: JWT pair from SimpleJWT
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/token/ -H "Content-Type: application/json" -d '{"email":"user@example.com","password":"StrongPass1"}'
```
- Check: `access`, `refresh`

### POST `/api/v1/accounts/token/refresh/`
- Auth: No
- Body: `refresh`
- 200: new `access`
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/token/refresh/ -H "Content-Type: application/json" -d '{"refresh":"<refresh>"}'
```

### GET `/api/v1/accounts/me/`
- Auth: Yes
- 200: current user profile envelope
- cURL:
```bash
curl http://localhost:8000/api/v1/accounts/me/ -H "Authorization: Bearer <access>"
```

### PATCH `/api/v1/accounts/me/`
- Auth: Yes
- Body (partial): `first_name`, `last_name`, `phone_number`, `profile_picture`, `date_of_birth`
- 200: updated profile envelope
- cURL:
```bash
curl -X PATCH http://localhost:8000/api/v1/accounts/me/ -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d '{"first_name":"Janet"}'
```

### POST `/api/v1/accounts/password/change/`
- Auth: Yes
- Body: `old_password`, `new_password`, `new_password2`
- 200: success message
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/password/change/ -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d '{"old_password":"Old1Pass","new_password":"New1PassWord","new_password2":"New1PassWord"}'
```

### POST `/api/v1/accounts/kyc/upload/`
- Auth: Yes
- Headers: `Content-Type: multipart/form-data`
- Body: `document_type` (`id_front|id_back|passport|huduma`), `file`
- 200: KYC status object
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/kyc/upload/ -H "Authorization: Bearer <access>" -F "document_type=id_front" -F "file=@id_front.png"
```

### POST `/api/v1/accounts/kyc/submit-id/`
- Auth: Yes
- Body: `id_number` (req), `date_of_birth` (opt)
- 200: IPRS verification payload
- cURL:
```bash
curl -X POST http://localhost:8000/api/v1/accounts/kyc/submit-id/ -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d '{"id_number":"12345678"}'
```

### GET `/api/v1/accounts/kyc/status/`
- Auth: Yes
- 200: KYC status object
- cURL:
```bash
curl http://localhost:8000/api/v1/accounts/kyc/status/ -H "Authorization: Bearer <access>"
```

### GET `/api/v1/accounts/saccos/`
- Auth: No
- Query: `search`, `sector`, `county`, `membership_type`, `verified_only`, `min_members`, `max_members`, `ordering`
- 200: paginated sacco list
- cURL:
```bash
curl "http://localhost:8000/api/v1/accounts/saccos/?search=teachers&ordering=-member_count"
```

### GET `/api/v1/accounts/saccos/{id}/`
- Auth: No
- 200: sacco detail envelope
- cURL:
```bash
curl http://localhost:8000/api/v1/accounts/saccos/<sacco_uuid>/
```

### POST `/api/v1/accounts/otp/send/`
- Auth: No
- Body: `phone_number`, `purpose`
- 200: OTP sent message
- Errors: 400, 429, 502

### POST `/api/v1/accounts/otp/verify/`
- Auth: No
- Body: `phone_number`, `code`
- 200: user profile
- Errors: 400 invalid/expired OTP

### POST `/api/v1/accounts/otp/resend/`
- Auth: No
- Body: `phone_number`, `purpose`
- 200: OTP resent, 429 cooldown

### POST `/api/v1/accounts/password/reset/`
- Auth: No
- Body: `phone_number`, `purpose` (serializer accepts OTP purpose)
- 200: generic success message

### POST `/api/v1/accounts/password/reset/confirm/`
- Auth: No
- Body: `phone_number`, `code`, `new_password`, `new_password2`
- 200: password reset success

## Membership
### GET/POST `/api/v1/members/memberships/`
- Auth: Yes
- GET query: `sacco`, `status`
- POST body: `sacco` (uuid, req), `custom_fields` (opt), `employment_status` (opt), `employer_name` (opt), `monthly_income` (opt)
- 200/201: membership list / created membership detail
- cURL (POST):
```bash
curl -X POST http://localhost:8000/api/v1/members/memberships/ -H "Authorization: Bearer <access>" -H "Content-Type: application/json" -d '{"sacco":"<sacco_uuid>","custom_fields":[]}'
```

### GET `/api/v1/members/memberships/{id}/`
- Auth: Yes
- 200: membership detail

### POST `/api/v1/members/memberships/{id}/leave/`
- Auth: Yes
- 200: left successfully
- Errors: 400 active loans, 404 not found

### GET `/api/v1/members/saccos/{sacco_id}/fields/`
- Auth: No
- 200: list of membership form fields

## Management (Admin)
- Also available under `/api/v1/saccomanagement/...` alias.

### POST `/api/v1/management/roles/assign/`
- Auth: Yes (SUPER_ADMIN)
- Body: `user_id`, `role_name` (`MEMBER|SACCO_ADMIN|SUPER_ADMIN`), `sacco_id` (opt)
- 201: role object

### DELETE `/api/v1/management/roles/{role_id}/`
- Auth: Yes (SUPER_ADMIN)
- 200: revoke confirmation

### GET `/api/v1/management/roles/?user_id=...`
- Auth: Yes (SACCO_ADMIN/SUPER_ADMIN)
- 200: user roles list

### GET `/api/v1/management/audit-logs/`
- Auth: Yes (SUPER_ADMIN)
- Query: `action`, `resource_type`, `user`

### GET `/api/v1/management/members/`
- Auth: Yes (SACCO_ADMIN)
- Header: `X-Sacco-ID` recommended
- Query: `search`, `status`

### GET `/api/v1/management/members/{membership_id}/`
- Auth: Yes (SACCO_ADMIN)
- 200: member detail with savings/loans/transactions

### GET `/api/v1/management/stats/`
- Auth: Yes (SACCO_ADMIN)
- 200: aggregate sacco metrics

### PATCH `/api/v1/management/applications/{id}/review/`
- Auth: Yes (SACCO_ADMIN)
- Body: `status` (`APPROVED|REJECTED`), `review_notes` (opt)

### GET `/api/v1/management/kyc/queue/`
- Auth: Yes (SACCO_ADMIN/SUPER_ADMIN)
- Query: `status`, `search`

### PATCH `/api/v1/management/kyc/{kyc_id}/review/`
- Auth: Yes (SACCO_ADMIN/SUPER_ADMIN)
- Body: `status` (`APPROVED|REJECTED`), `rejection_reason` (required if rejected)

### PATCH `/api/v1/management/loans/{id}/status/`
- Auth: Yes (SACCO_ADMIN)
- Body: `status` (workflow transition), `notes` (opt)

### POST `/api/v1/management/import/`
- Auth: Yes (SACCO_ADMIN)
- Header: `X-Sacco-ID`
- Body multipart: `file` (`.csv|.xlsx|.xls`)
- 202: job queued

### GET `/api/v1/management/import/{job_id}/`
- Auth: Yes (SACCO_ADMIN)
- 200: job status summary

## Services
### Router: Savings Types (`/api/v1/services/savings-types/`)
- `GET /` list (AllowAny)
- `POST /` create (Admin)
- `GET /{id}/` retrieve (AllowAny)
- `PUT /{id}/` update (Admin)
- `PATCH /{id}/` partial update (Admin)
- `DELETE /{id}/` delete (Admin)
- Query on list: `sacco`, `sacco_id`

### GET `/api/v1/services/savings/`
- Auth: Yes
- Query: `sacco`

### GET `/api/v1/services/savings/breakdown/?sacco_id=...`
- Auth: Yes
- 200: totals by BOSA/FOSA/SHARE_CAPITAL

### GET `/api/v1/services/loan-types/`
- Auth: No
- Query: `sacco_id`

### GET/POST `/api/v1/services/loans/`
- Auth: Yes
- GET query: `status`, `sacco`
- POST body: `loan_type`, `amount`, `term_months`, `application_notes` (opt)

### GET `/api/v1/services/loans/list/`
- Auth: Yes
- Query: `status`, `sacco`

### POST `/api/v1/services/loans/apply/`
- Auth: Yes
- Body: same as loan create

### GET `/api/v1/services/loans/eligibility/?sacco_id=...`
- Auth: Yes

### GET `/api/v1/services/loans/{id}/`
- Auth: Yes

### GET `/api/v1/services/loans/{id}/schedule/`
- Auth: Yes
- Generates schedule if loan status allows

### GET `/api/v1/services/loans/{loan_id}/guarantors/search/`
- Auth: Yes
- Query: `phone` or `member_number` (one required)

### POST `/api/v1/services/loans/{loan_id}/guarantors/`
- Auth: Yes
- Body: `guarantor_user_id`, `guarantee_amount`

### POST `/api/v1/services/loans/{loan_id}/guarantors/{guarantor_id}/respond/`
- Auth: Yes (must be target guarantor)
- Body: `action` (`APPROVE|DECLINE`), `notes` (opt)

## Payments
### GET `/api/v1/payments/transactions/`
- Auth: Yes
- 200: user transactions

### GET `/api/v1/payments/transactions/{id}/`
- Auth: Yes

### GET `/api/v1/payments/mpesa/{id}/`
- Auth: Yes

### POST `/api/v1/payments/mpesa/stk-push/`
- Auth: Yes
- Body: `phone_number`, `amount`, `purpose` (`SAVING_DEPOSIT|LOAN_REPAYMENT`), `sacco_id`, plus `saving_id` or (`loan_id` + `instalment_number`)
- 201: `checkout_request_id`, `merchant_request_id`

### GET `/api/v1/payments/mpesa/stk/{checkout_request_id}/status/`
- Auth: Yes

### POST `/api/v1/payments/mpesa/b2c/disburse/`
- Auth: Yes (SACCO_ADMIN)
- Header: `X-Sacco-ID`
- Body: `loan_id`, `phone_number`, `amount`, `remarks` (opt)

### GET `/api/v1/payments/mpesa/b2c/{conversation_id}/status/`
- Auth: Yes (SACCO_ADMIN)

### GET `/api/v1/payments/mpesa/b2c/history/`
- Auth: Yes (SACCO_ADMIN)

### POST `/api/v1/payments/callback/mpesa/stk/`
- Auth: No (Safaricom callback, signature/IP checks enforced)

### POST `/api/v1/payments/callback/mpesa/b2c/`
- Auth: No (Safaricom callback)

### POST `/api/v1/payments/callbacks/`
- Auth: No
- Body: `transaction`, `provider`, `raw_payload`

## Notifications
### GET `/api/v1/notifications/`
- Auth: Yes
- Query: `category`, `is_read` (`true|false`)

### POST `/api/v1/notifications/{id}/read/`
- Auth: Yes

### POST `/api/v1/notifications/read-all/`
- Auth: Yes

### POST `/api/v1/notifications/device/`
- Auth: Yes
- Body: `token`, `platform`

## Ledger
### GET `/api/v1/ledger/entries/`
- Auth: Yes
- Query: `sacco_id` (req), `from_date`, `to_date`, `category`

### GET `/api/v1/ledger/balance/`
- Auth: Yes
- Query: `sacco_id` (req)

### GET `/api/v1/ledger/statement/`
- Auth: Yes
- Query: `sacco_id` (req), `from_date` (req, YYYY-MM-DD), `to_date` (req)

### GET `/api/v1/ledger/statement/pdf/`
- Auth: Yes
- Same query params as statement JSON
- Returns PDF download

## Dashboard
### GET `/api/v1/dashboard/portfolio/`
- Auth: Yes

### GET `/api/v1/dashboard/state/`
- Auth: Yes

### GET `/api/v1/dashboard/saccos/`
- Auth: Yes

### GET `/api/v1/dashboard/activity/?limit=20`
- Auth: Yes
- Query: `limit` (1..100)

### GET `/api/v1/dashboard/loans/compare/?amount=50000&term=12`
- Auth: Yes
- Query: `amount` (req), `term` (req)

## Billing
### GET `/api/v1/billing/invoices/`
- Auth: Yes (SACCO_ADMIN/SUPER_ADMIN)

### GET `/api/v1/billing/invoices/{id}/`
- Auth: Yes (SACCO_ADMIN/SUPER_ADMIN)

### POST `/api/v1/billing/invoices/{invoice_id}/resend/`
- Auth: Yes (SACCO_ADMIN/SUPER_ADMIN)

### GET `/api/v1/billing/invoices/{invoice_id}/download/?format=csv|pdf`
- Auth: Yes (SACCO_ADMIN/SUPER_ADMIN)

## Health
### GET `/health/`
- Auth: No
- 200: `{ "status": "ok" }`

### GET `/health/ready/`
- Auth: No
- 200 or 503 with database/cache check details

### GET `/api/v1/health/`
- Auth: No

### GET `/api/v1/health/live/`
- Auth: No

### GET `/api/v1/health/ready/`
- Auth: No

## Postman-Style Request Breakdown (applies to all endpoints)
- Method: Use endpoint method exactly.
- URL: `{{baseUrl}}` + path above.
- Auth tab: `Bearer Token` for protected endpoints.
- Headers: `Content-Type: application/json` unless multipart/file endpoint.
- Body: raw JSON or form-data per endpoint.
- Tests/checks:
  - Status code matches expected success code.
  - Required keys exist in response payload.
  - Protected endpoints reject missing/invalid token with `401`.
  - Admin endpoints reject wrong role/context with `403`.
