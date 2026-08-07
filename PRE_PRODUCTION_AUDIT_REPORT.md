# SaccoSphere Pre-Production Backend Audit

Audit date: 2026-07-17  
Scope confirmed in code: `accounts`, `saccomembership`, `saccomanagement`, `services`, `payments`, `ledger`, `guarantor`, `notifications`, `dashboard`, `billing`, `health`, `config/settings`, Celery config, integrations, engines, and tests directories.  
Not inspected in detail: Django `admin.py` display configuration and individual migration operations beyond model existence. Media uploads were not inspected as application logic.

Evidence convention:

- **CONFIRMED IN CODE** means I opened and read the named file/function/class.
- **INFERRED** means the behavior follows from code structure or naming but is not explicitly documented or fully wired in code.
- **Second-pass note:** after the initial report, I also opened and read `accounts/permissions.py`, `services/permissions.py`, `accounts/biometric_views.py`, `accounts/oauth_views.py`, `accounts/throttles.py`, `accounts/role_utils.py`, `saccomanagement/role_views.py`, `saccomanagement/settings_views.py`, `saccomanagement/reports_views.py`, `saccomanagement/sasra_reports.py`, `saccomanagement/bulk_sms_views.py`, `saccomanagement/import_views.py`, `saccomanagement/superadmin_views.py`, `saccomanagement/dashboard_views.py`, and `saccomanagement/loan_utils.py` to tighten endpoint and permission claims.

---

# PART 1 - SYSTEM MAP

## accounts

**Responsibility.** CONFIRMED IN CODE: `accounts` owns identity, authentication profile data, public SACCO discovery, KYC/IPRS, OTP, biometric device registration, Google OAuth stub behavior, and DPA-style consent records. Primary files read: `accounts/models.py`, `accounts/views.py`, `accounts/serializers.py`, `accounts/permissions.py`, `accounts/tasks.py`, `accounts/otp_utils.py`, `accounts/integrations/iprs_client.py`, `accounts/integrations/otp_service.py`, `accounts/integrations/oauth.py`, `accounts/biometric_views.py`, and tests under `accounts/tests/`.

**Models.**

- `accounts.models.User`: custom `AbstractUser` using `email` as `USERNAME_FIELD`; UUID `id`; `username` nullable/non-unique legacy field; `email` unique; phone/profile/date fields. Represents a platform user: member, SACCO admin, or super admin.
- `accounts.models.UserDevice`: FK `user -> AUTH_USER_MODEL` with `CASCADE`; fields `device_id`, `device_name`, `platform`, `push_token`, `biometric_enabled`, timestamps; `unique_together = ['user', 'device_id']`. Represents a biometric-capable registered device.
- `accounts.models.Sacco`: UUID entity for a SACCO; fields include name/registration/sector/county/membership type, visibility, loan settings, registration fee, contact fields, timestamps. Represents the cooperative institution tenant.
- `accounts.models.SaccoSettings`: O2O `sacco -> Sacco` with `CASCADE`; per-SACCO min/max loan settings, guarantor settings, registration fee, contribution amount, liquidity threshold, SMS daily limit. Represents tenant-specific policy overrides.
- `accounts.models.KYCVerification`: O2O `user -> User` with `CASCADE`; document images through `KYCDocumentStorage`; IPRS metadata; `reviewed_by -> User` with `SET_NULL`; statuses `NOT_STARTED`, `PENDING`, `IPRS_MISMATCH`, `PENDING_MANUAL`, `APPROVED`, `REJECTED`. Represents member identity verification.
- `accounts.models.OTPToken`: FK `user -> User` nullable with `CASCADE`; phone, code, purpose, attempt count, expiry. Represents OTP lifecycle for phone verification/login/password reset.
- `accounts.models.UserConsent`: FK `user -> User` with `CASCADE`; consent type/version/boolean/IP/user agent/timestamp. Represents DPA 2019 consent evidence.

**Background jobs.**

- `accounts.tasks.cleanup_expired_otps`: CONFIRMED IN CODE. Beat entry in `config/celery.py` named `cleanup-expired-otps`, schedule `300.0` seconds. Deletes expired used OTPs and abandoned unused OTPs older than 24h. No explicit retry decorator settings on the task, but global Celery annotations in `config/celery.py` set `max_retries=3`; the function itself does not call `self.retry`, so failures bubble to Celery without custom recovery.

**External integrations.**

- IPRS: `accounts.integrations.iprs_client.IPRSClient`. Controlled by `settings.DEBUG or settings.IPRS_MOCK`; settings in `config/settings/base.py`: `IPRS_API_KEY`, `IPRS_API_URL`, `IPRS_MOCK=True` default. In mock mode returns deterministic verified response. Production path posts to configured API with retries for connection/timeout and returns `unavailable` response instead of raising for most failures.
- Africa's Talking SMS: `accounts.integrations.otp_service.ATSMSClient`. Controlled by `settings.DEBUG`; in debug logs OTP/SMS instead of sending. Production requires `AT_API_KEY`, `AT_USERNAME`, and `africastalking` SDK.
- Google OAuth: `accounts.integrations.oauth.GoogleOAuthClient`. CONFIRMED STUB. In `DEBUG` returns mock token/user; in production raises `NotImplementedError`.

**Dependency graph.**

- Imports outward: `accounts.views` imports IPRS, SMS OTP, permissions, OTP utils; `accounts.serializers` imports role/context helpers; `accounts.permissions` imports `saccomanagement.models.Role`.
- Imported by: almost every app imports `accounts.models.Sacco/User` or `accounts.permissions`.
- Circular-import workarounds: `accounts.otp_utils` imports `OTPToken` inside functions; `accounts.views` uses local imports for `OTPToken`; `accounts.serializers` pulls role context helpers. These lazy imports are mostly to avoid auth/model import cycles.

## saccomembership

**Responsibility.** CONFIRMED IN CODE: member applications to SACCOs, membership state, dynamic SACCO application fields, and membership document uploads. Primary files read: `saccomembership/models.py`, `views.py`, `serializers.py`, `membership_doc_views.py`, `membership_doc_serializers.py`, validators, URLs, tests.

**Models.**

- `saccomembership.models.Membership`: FK `user -> AUTH_USER_MODEL` `CASCADE`; FK `sacco -> accounts.Sacco` `CASCADE`; member number/status/application/review fields; `unique_together = ['user', 'sacco']`. Represents a user’s membership in a tenant SACCO.
- `SaccoFieldDefinition`: FK `sacco -> accounts.Sacco` `CASCADE`; label/type/required/options/order. Represents per-SACCO application form schema.
- `MemberFieldData`: FK `membership -> Membership` `CASCADE`; FK `field -> SaccoFieldDefinition` `CASCADE`; value/file. Represents a member’s answers to custom fields.
- `SaccoApplication`: FK `user -> AUTH_USER_MODEL` `CASCADE`; FK `sacco -> accounts.Sacco` `CASCADE`; employment/income/docs/registration fee state; FK `fee_transaction -> payments.Transaction` `SET_NULL`; FK `reviewed_by -> AUTH_USER_MODEL` `SET_NULL`. Represents a join/application workflow.
- `MembershipDocument`: FK `application -> SaccoApplication` `CASCADE`; document type/file metadata/verified flag. Represents uploaded supporting documents.

**Background jobs.** None in this app.

**External integrations.** None direct.

**Dependency graph.**

- Imports outward: `saccomembership.serializers` imports `accounts.Sacco`; document views import `saccomanagement.models.Role`.
- Imported by: `services`, `ledger`, `dashboard`, `saccomanagement`, `payments`.
- Workaround: `MembershipLeaveView._has_active_loans` uses `django.apps.apps.get_model('services', 'Loan')`, a lazy model lookup that avoids a direct import cycle.

## saccomanagement

**Responsibility.** CONFIRMED IN CODE: SACCO admin operations, role management, audit logs, member dashboards, application review, KYC admin routes, loan approval workflow, SASRA reports, bulk SMS campaigns, imports, SACCO settings, and super-admin dashboard. Primary files read: `saccomanagement/models.py`, `views.py`, `admin_views.py`, `loan_utils.py`, `mixins.py`, `role_views.py`, `settings_views.py`, `reports_views.py`, `sasra_reports.py`, `bulk_sms_views.py`, `import_views.py`, `import_utils.py`, `tasks.py`, `superadmin_views.py`, `dashboard_views.py`, `data_imports/*`, tests.

**Models.**

- `SystemAuditLog`: FK `user -> AUTH_USER_MODEL` `SET_NULL`; action/resource/old/new/IP/user-agent. Represents audit trail.
- `DataConsentLog`: FK `user -> AUTH_USER_MODEL` `CASCADE`; FK `accessed_by -> AUTH_USER_MODEL` `CASCADE`; data type/reason. Represents DPA access log.
- `SMSCampaign`: FK `sacco -> accounts.Sacco` `CASCADE`; FK `created_by -> AUTH_USER_MODEL` `SET_NULL`; message/audience/status/counts. Represents bulk SMS campaign.
- `SMSCampaignRecipient`: FK `campaign -> SMSCampaign` `CASCADE`; FK `membership -> Membership` `CASCADE`; phone/status/error; unique per campaign/membership.
- `Role`: FK `user -> AUTH_USER_MODEL` `CASCADE`; FK `sacco -> accounts.Sacco` nullable `CASCADE`; names `MEMBER`, `SACCO_ADMIN`, `SUPER_ADMIN`; unique user/sacco/name. Represents RBAC assignment.
- `RolePermission`: FK `role -> Role` `CASCADE`; resource CRUD booleans. Represents granular permissions, but I did not find broad enforcement in views.
- `ImportJob`: FK `sacco -> accounts.Sacco` `CASCADE`; FK `imported_by -> AUTH_USER_MODEL` `CASCADE`; uploaded file/status/counts/errors. Represents asynchronous member import.
- `MemberImportJob`: FK `sacco -> accounts.Sacco` `CASCADE`; FK `created_by -> AUTH_USER_MODEL` `CASCADE`; sync import progress/errors. Represents legacy/synchronous import job.
- `ComplianceFlag`: FK `sacco -> accounts.Sacco` `CASCADE`; type/severity/status/metadata/resolution. Represents platform health/compliance issue.

**Background jobs.**

- `saccomanagement.tasks.run_member_import_task`: queued by import flow; parses, validates, imports members; marks `ImportJob` `PROCESSING`, then `COMPLETED`/`FAILED`/`PARTIAL`. It catches exceptions, marks failed, then re-raises. No `bind=True`, no explicit retry.

**External integrations.**

- Bulk SMS through `notifications.tasks.send_bulk_sms_campaign_task` and `accounts.integrations.otp_service.ATSMSClient`; controlled by `DEBUG` and Africa's Talking settings.
- M-Pesa B2C loan disbursement helper in `saccomanagement.loan_utils.initiate_loan_disbursement`, using `payments.integrations.mpesa.daraja.DarajaClient`. Controlled by M-Pesa settings.
- SASRA returns are local reporting in `saccomanagement.sasra_reports`; no external e-filing integration confirmed.

**Dependency graph.**

- Imports outward: accounts permissions, payments transactions, membership documents, services loans/savings, notifications, billing/report helpers.
- Imported by: payments/tasks and services views/tasks rely on `Role` and SACCO scoping.
- Workarounds: `loan_utils.initiate_loan_disbursement` imports payment models inside the function to avoid cycles.

## services

**Responsibility.** CONFIRMED IN CODE: savings products/accounts, loan products/applications, guarantors, guarantee capacity, repayment schedules, CRB checks, liquidity/NPL monitoring, dividends. Primary files read: `services/models.py`, `views.py`, `serializers.py`, `tasks.py`, `engines/*`, `integrations/metropol_client.py`, tests.

**Models.**

- `SavingsType`: FK `sacco -> accounts.Sacco` `CASCADE`; name/rate/min contribution/active; unique name+sacco. Represents a SACCO savings product.
- `Saving`: FK `membership -> Membership` `PROTECT`; FK `savings_type -> SavingsType` nullable `SET_NULL`; amount/totals/status/dividend flag. Represents a member savings account.
- `LoanType`: FK `sacco -> accounts.Sacco` `CASCADE`; product rate/term/min/max/guarantor settings. Represents SACCO loan product.
- `Loan`: FK `membership -> Membership` `PROTECT`; FK `loan_type -> LoanType` nullable `SET_NULL`; FK `approved_by -> AUTH_USER_MODEL` `SET_NULL`; FK `mpesa_transaction -> payments.MpesaTransaction` `SET_NULL`; amount/rate/term/outstanding/disbursed/status. Represents loan lifecycle.
- `RepaymentSchedule`: FK `loan -> Loan` `CASCADE`; instalment number/due/amount/principal/interest/balance/status/paid fields/penalty; unique loan+instalment. Represents a scheduled repayment row.
- `ReminderLog`: FK `schedule_item -> RepaymentSchedule` `CASCADE`; type/sent flags; unique schedule+type. Represents reminder history.
- `Guarantor`: FK `loan -> Loan` `CASCADE`; FK `guarantor -> AUTH_USER_MODEL` `PROTECT`; status/amount/timestamps; unique loan+guarantor. Represents internal member guarantee request.
- `GuaranteeCapacity`: O2O `user -> AUTH_USER_MODEL` `CASCADE`; total savings/active guarantees/available capacity. Represents cached guarantee capacity.
- `Insurance`: FK `membership -> Membership` `CASCADE`; policy fields. Represents insurance product/policy.
- `LiquidityAlert`: FK `sacco -> accounts.Sacco` `CASCADE`; reserves/pending/utilisation/resolution. Represents liquidity warning snapshot.
- `NPLFlag`: FK `loan -> Loan` `CASCADE`; threshold 30/60/90; resolution; unique loan+threshold. Represents arrears/NPL early warning.
- `CRBCheck`: FK `loan -> Loan` `CASCADE`; score/band/listed/provider/reference/raw; FK `checked_by -> AUTH_USER_MODEL` `SET_NULL`. Represents CRB result.
- `DividendDeclaration`: FK `sacco -> accounts.Sacco` `CASCADE`; FK `savings_type -> SavingsType` `PROTECT`; year/rate/period/status/approver/total. Represents dividend batch.
- `DividendPayout`: FK `declaration -> DividendDeclaration` `CASCADE`; FK `membership -> Membership` `CASCADE`; FK `saving -> Saving` `CASCADE`; average/dividend/status. Represents per-member dividend line.

**Background jobs.**

- `services.tasks.notify_guarantors_task`: queued by `LoanApplyView.perform_create`; loops pending `Guarantor` rows, queues `notify_user_task`. No retry; catches per-guarantor failures and continues.
- `services.tasks.check_all_sacco_liquidity`: beat hourly in `config/celery.py`; `bind=True`, `max_retries=3`, retries on outer exception. Creates/resolves `LiquidityAlert`; notifies admins.
- `services.tasks.flag_npl_arrears`: beat daily 06:30; `bind=True`, retries on outer exception. Creates staged `NPLFlag`; notifies admins/member.

**External integrations.**

- Metropol/CRB: `services.integrations.metropol_client.MetropolClient`; controlled by `settings.DEBUG or settings.METROPOL_MOCK`. I did not find `METROPOL_API_KEY`, `METROPOL_API_URL`, or `METROPOL_MOCK` in `config/settings/base.py`; they are referenced but not defined there. That is a production config risk.

**Dependency graph.**

- Imports outward: membership, accounts, payments, notifications, ledger, saccomanagement.
- Imported by: payments, dashboard, saccomanagement, ledger.
- Workarounds: `services.views.LoanApplyView.perform_create` lazy-imports `notify_guarantors_task`; `CRBCheckView` lazy-imports `KYCVerification` and `MetropolClient`; `RepaymentScheduleView` lazy-imports amortization.

## payments

**Responsibility.** CONFIRMED IN CODE: internal payment transactions, M-Pesa STK, M-Pesa B2C, PSP abstraction, callbacks, platform fees, pending reconciliation. Primary files read: `payments/models.py`, `views.py`, `serializers.py`, `tasks.py`, `validators.py`, `integrations/mpesa/*`, `providers/*`, tests.

**Models.**

- `PaymentProvider`: provider name/type/config/active. Represents payment rail.
- `Transaction`: FK `provider -> PaymentProvider` nullable `SET_NULL`; FK `user -> AUTH_USER_MODEL` `PROTECT`; unique `reference`; amount/fee/currency/status/metadata. Represents business transaction.
- `Callback`: FK `transaction -> Transaction` nullable `SET_NULL`; FK `provider -> PaymentProvider` `PROTECT`; raw payload/processed/error timestamps. Represents incoming PSP callback.
- `MpesaTransaction`: O2O `transaction -> Transaction` nullable `SET_NULL`; FK `related_saving -> services.Saving` `SET_NULL`; FK `related_loan -> services.Loan` `SET_NULL`; checkout/conversation/result/receipt fields. Represents M-Pesa rail metadata.
- `MpesaIdempotencyRecord`: unique checkout request id. CONFIRMED IN CODE: model exists but I did not find it used in `payments.tasks` or `payments.views`; idempotency is instead via `callback_received` and status.
- `PlatformFee`: FK `transaction -> Transaction` `PROTECT`; fee type/amount/invoice/processed. Represents platform charge line.

**Background jobs.**

- `payments.tasks.process_stk_callback_task`: queued by `MPesaSTKCallbackView`; atomic with `select_for_update`; no explicit retry. Applies savings deposit or loan repayment and notifications.
- `payments.tasks.process_payment_callback`: generic PSP callback task; `bind=True`, `max_retries=3`, but does not wrap its body in try/except or call `self.retry`. It references `callback.payload`, but `Callback` model field is `raw_payload`, so this path is broken.
- `payments.tasks.reconcile_pending_transactions`: no retry; queries pending transactions older than 10 minutes and creates callbacks for provider status results.
- `payments.tasks.process_b2c_callback_task`: queued by B2C callback; atomic with `select_for_update`; no explicit retry.

**External integrations.**

- M-Pesa Daraja: `payments.integrations.mpesa.daraja.DarajaClient`; controlled by `MPESA_ENVIRONMENT` (`live` uses live URL, otherwise sandbox), and required credentials in `config/settings/base.py`.
- M-Pesa callback security: `payments.integrations.mpesa.security.is_safaricom_ip` returns true in `DEBUG`; production uses hardcoded Safaricom IP ranges. `verify_mpesa_signature` usually returns true when callbacks lack password/timestamp, relying on IP + replay cache.
- PSP abstraction: `payments.providers.get_psp_provider`; if `DEBUG` and no `PAYMENT_PROVIDER`, uses mock. Otherwise uses `settings.PAYMENT_PROVIDER` or `sacco.payment_provider` if present. Risk: `accounts.models.Sacco` has no `payment_provider` field in code read.
- Cellulant/Tingg: `payments.providers.cellulant.*`; debug returns mock checkout; production requires settings not found in base settings.
- IntaSend: `payments.providers.intasend.provider.IntaSendProvider`; debug returns mock; production uses API key settings.
- Registry includes Flutterwave path but no `payments/providers/flutterwave/provider.py` file exists. Selecting `flutterwave` will import-fail.

**Dependency graph.**

- Imports outward: accounts, services, billing, notifications, ledger, guarantor utils.
- Imported by: billing, membership, services.
- Workarounds: tasks import ledger/services inside helper functions; views lazy-import tasks in callback handlers.

## ledger

**Responsibility.** CONFIRMED IN CODE: immutable-ish member ledger entries, running balances, statements, PDFs. Primary files read: `ledger/models.py`, `utils.py`, `views.py`, `engines/*`, tests.

**Models.**

- `LedgerEntry`: FK `membership -> Membership` `PROTECT`; FK `transaction -> payments.Transaction` nullable `SET_NULL`; entry type debit/credit; category; amount; unique reference; balance_after. Represents member ledger line.

**Background jobs.** None.

**External integrations.** PDF generation uses WeasyPrint in `ledger.engines.pdf_generator` through `StatementPDFView`; no external network integration.

**Dependency graph.**

- Imports outward: membership, payments, saccomanagement ODPC logging.
- Workaround/bug: `ledger.engines.statement_builder._record_statement_access` does `from saccomanagement import create_data_consent_log`, but `saccomanagement/__init__.py` does not expose that function in the files read. The import likely fails silently and skips DPA access logging.

## guarantor

**Responsibility.** CONFIRMED IN CODE: external guarantor records, SMS response token flow, applicant/admin review of external guarantors. Primary files read: `guarantor/models.py`, `external_views.py`, `external_serializers.py`, `utils.py`, tests.

**Models.**

- `ExternalGuarantor`: FK `loan -> services.Loan` `CASCADE`; FK `requested_by -> AUTH_USER_MODEL` `CASCADE`; FK `sacco -> accounts.Sacco` `CASCADE`; ID/income/guarantee docs/status/token/review fields. Represents non-member guarantor workflow.

**Background jobs.** None; SMS is sent synchronously by `guarantor.utils.send_guarantor_sms`.

**External integrations.** Africa's Talking via `ATSMSClient`; debug logs only.

**Dependency graph.**

- Imports outward: services Loan, accounts serializer phone validator, notifications, saccomanagement Role, Africa's Talking client.
- Imported by: services URLs and payments/admin loan guarantor checks.

## notifications

**Responsibility.** CONFIRMED IN CODE: in-app notifications, FCM device tokens, SMS/email/push async dispatch, bulk SMS delivery. Primary files read: `notifications/models.py`, `views.py`, `serializers.py`, `tasks.py`, `utils.py`, `integrations/fcm_push.py`, tests.

**Models.**

- `Notification`: FK `user -> AUTH_USER_MODEL` `CASCADE`; title/message/category/read/push/action/related object. Represents in-app notification.
- `DeviceToken`: FK `user -> AUTH_USER_MODEL` `CASCADE`; token unique/platform/active timestamp. Represents push subscription.

**Background jobs.**

- `send_sms_task`, `send_email_task`, `send_push_notification_task`, `notify_user_task`, `send_bulk_sms_campaign_task`: all `bind=True`, `max_retries=3`, exponential-ish countdown. Bulk SMS tracks recipient statuses and daily SACCO limit.

**External integrations.**

- Africa's Talking SMS via `ATSMSClient`, debug logs only.
- FCM via `notifications.integrations.fcm_push.FCMPushClient`; debug logs only; production requires `FCM_SERVER_KEY`.
- Email via Django SMTP settings.

**Dependency graph.**

- Imports outward: accounts SMS, saccomanagement SMSCampaign, FCM.
- Imported by: payments, services, saccomanagement, guarantor.

## dashboard

**Responsibility.** CONFIRMED IN CODE: member dashboard aggregation across SACCOs: portfolio, state, SACCO switcher, activity feed, loan comparison. Primary files read: `dashboard/views.py`, `dashboard/engines/*`, tests.

**Models.** `dashboard/models.py` contains only Django scaffold comments; no models.

**Background jobs.** None.

**External integrations.** None.

**Dependency graph.**

- Imports outward: payments, services, saccomembership, notifications.
- Uses lazy imports inside engines to reduce app load coupling.

## billing

**Responsibility.** CONFIRMED IN CODE: platform subscription/revenue models, monthly SACCO fee invoices, invoice email/download, recording collected transaction fees. Primary files read: `billing/models.py`, `services.py`, `views.py`, `tasks.py`, tests.

**Models.**

- `SaccoSubscription`: O2O `sacco -> accounts.Sacco` `CASCADE`; plan/status/monthly fee/dates. Represents SaaS subscription.
- `PlatformRevenue`: FK `sacco -> accounts.Sacco` nullable `SET_NULL`; FK `transaction -> payments.Transaction` nullable `SET_NULL`; type/amount/collected flag. Represents platform revenue.
- `MonthlySaccoInvoice`: FK `sacco -> accounts.Sacco` `CASCADE`; period/amount/status/report payload/sent/due; unique sacco+period. Represents invoice.

**Background jobs.**

- `billing.tasks.generate_and_send_monthly_fee_reports`: beat monthly on day 1 00:00; loops active SACCOs, generates invoice and emails it. No explicit retry/error isolation per SACCO; one email failure can stop the task.

**External integrations.** Email via Django SMTP; PDF via WeasyPrint fallback to CSV bytes.

**Dependency graph.**

- Imports outward: accounts Sacco, payments PlatformFee/Transaction, saccomanagement Role.

## health

**Responsibility.** CONFIRMED IN CODE: liveness/readiness endpoints. Models file is empty scaffold.

**Models.** None.

**Background jobs.** None.

**External integrations.** Database and cache readiness checks only.

## App Dependency Graph in Words

The core dependency direction is:

- `accounts` is foundational, but imports `saccomanagement.Role` for permissions/context, creating a soft cycle.
- `saccomembership` depends on `accounts` and is depended on by `services`, `ledger`, `dashboard`, `saccomanagement`, and `payments`.
- `services` is the domain core for loans/savings and imports `notifications`, `accounts`, `saccomanagement`, `ledger`, and `saccomembership`.
- `payments` imports `services`, `billing`, `ledger`, `notifications`, and `guarantor`.
- `saccomanagement` imports almost everything because it is the admin orchestration layer.
- `ledger` depends on membership and payments, while money-moving code in `payments.tasks` writes ledger rows.

Circular-import workarounds worth revisiting:

- `saccomembership.views.MembershipLeaveView._has_active_loans` uses `apps.get_model`.
- `saccomanagement.loan_utils.initiate_loan_disbursement` imports payment models inside the function.
- `payments.tasks` imports ledger/services inside helper functions.
- `services.views` lazy-imports tasks, CRB client, KYC model, and amortization.
- `ledger.engines.statement_builder._record_statement_access` attempts lazy import from `saccomanagement` package root and likely never logs access.

---

# PART 2 - EVERY API ENDPOINT, EXPLAINED AS A FRONTEND WOULD USE IT

Base prefix from `config/urls.py`: most API endpoints are under `/api/v1/`. `saccomanagement.urls` is mounted twice: `/api/v1/management/` and `/api/v1/saccomanagement/`. Both expose the same routes.

## Endpoint Detail Matrix

This matrix is the exhaustive URL pass from the app `urls.py` files read in code. Where one view supports multiple methods, I list each method. For the duplicated management mount, every `/api/v1/management/...` path also exists as `/api/v1/saccomanagement/...`.

### Member: Registration, Auth, Profile, KYC

| METHOD + full path | Who calls it | Permission/auth and SACCO scope | Screen interaction | Request and response shape | State changes | Side effects |
|---|---|---|---|---|---|---|
| POST `/api/v1/accounts/register/` | Unauthenticated visitor | `accounts.views.RegisterView`, `AllowAny`; no SACCO scope | User submits sign-up form | Request: email, first/last name, Kenyan phone, password pair. Response: created user fields | Creates `accounts.models.User`; creates `KYCVerification(NOT_STARTED)` in `UserRegistrationSerializer.create` | None confirmed |
| POST `/api/v1/accounts/login/` | Unauthenticated user | `LoginView`, `AllowAny`; no SACCO scope | User submits login form | Request: email/password. Response: JWTs and user profile/context | None | None confirmed |
| POST `/api/v1/accounts/oauth/google/callback/` | Frontend after Google auth | `accounts.oauth_views.GoogleOAuthCallbackView`, `AllowAny`; no SACCO scope | User taps “Continue with Google” | Request: `id_token`, `flow=login|signup`. Response: JWTs and profile | Signup creates `User` and `KYCVerification` | Verifies Google token using Google auth libraries; separate `accounts/integrations/oauth.py` stub is not used by this view |
| POST `/api/v1/accounts/logout/` | Authenticated user | `LogoutView`, `IsAuthenticated` | User taps logout | Request: refresh token. Response: success message | Blacklists refresh token if supplied | None confirmed |
| POST `/api/v1/accounts/token/` | Auth client | SimpleJWT `TokenObtainPairView` | Login/token refresh flow | Request: email/password. Response: access/refresh tokens | None | None confirmed |
| POST `/api/v1/accounts/token/refresh/` | Auth client | SimpleJWT `TokenRefreshView` | App refreshes token | Request: refresh token. Response: new access token | Refresh token rotation/blacklist per settings | None confirmed |
| GET `/api/v1/accounts/me/` | Authenticated user | `MeView`, `IsAuthenticated`; user object only | App loads profile/account screen | Request: none. Response: profile, phone, profile picture, SACCO context, biometric flag | None | None confirmed |
| PATCH `/api/v1/accounts/me/` | Authenticated user | `MeView`, `IsAuthenticated`; user object only | User edits profile | Request: editable profile fields. Response: updated profile | Updates `User` profile fields | None confirmed |
| POST `/api/v1/accounts/device/register/` | Authenticated mobile app | `DeviceRegistrationView`, `IsAuthenticated`; user-scoped | User enables biometrics/push on device | Request: device id/name/platform/push token/biometric flag. Response: registration message | Creates or updates `UserDevice` unique by user/device id | None confirmed |
| GET `/api/v1/accounts/devices/` | Authenticated user | `DeviceListView`, `IsAuthenticated`; `request.user.devices` only | Security/devices screen opens | Request: none. Response: device id/name/platform/biometric/last seen | None | None |
| DELETE `/api/v1/accounts/device/<device_id>/` | Authenticated user | `RevokeDeviceView`, `IsAuthenticated`; filters by user/device id | User revokes a device | Request: path device id. Response: 204 | Deletes matching `UserDevice` | None |
| POST `/api/v1/accounts/kyc/submit-id/` | Authenticated member | `KYCSubmitIDView`, `IsAuthenticated`; user KYC only | Member submits national ID data | Request: ID details. Response: IPRS/KYC status | Updates `KYCVerification` ID/IPRS/status fields | Calls `IPRSClient.verify_id` |
| POST `/api/v1/accounts/kyc/upload/` | Authenticated member | `KYCUploadView`, `IsAuthenticated`; user KYC only | Member uploads ID/passport/Huduma document | Request: `document_type`, file. Response: KYC state/document info | Updates document field, submission/status fields | Attempts IPRS inline; failure does not block upload |
| GET `/api/v1/accounts/kyc/status/` | Authenticated member | `KYCStatusView`, `IsAuthenticated`; own KYC | KYC status screen opens | Request: none. Response: status, IPRS flags/errors, admin reasons, document URLs | None | None |
| POST `/api/v1/accounts/password/change/` | Authenticated user | `PasswordChangeView`, `IsAuthenticated` | User changes password in settings | Request: old password, new password pair. Response: success/errors | Updates password | None |
| GET `/api/v1/accounts/public-stats/` | Public site/app | `PublicStatsView`, `AllowAny` | Landing/explore screen loads platform stats | Request: none. Response: public aggregate stats | None | None |
| GET `/api/v1/accounts/saccos/` | Public/member | `SaccoListView`, `AllowAny`; public list only | User browses/searches SACCOs | Query filters for search/sector/county/type/member count. Response: SACCO cards | None | None |
| GET `/api/v1/accounts/saccos/<uuid:id>/` | Public/member | `SaccoDetailView`, `AllowAny`; public detail | User opens a SACCO profile | Request: SACCO id. Response: details/contact/loan defaults | None | None |
| POST `/api/v1/accounts/otp/send/` | Public auth flow | `OTPSendView`, `AllowAny`, `OTPSendThrottle`; no SACCO scope | User requests OTP | Request: phone, purpose. Response: generic OTP sent/error | Creates `OTPToken` | Africa's Talking SMS or debug log |
| POST `/api/v1/accounts/otp/verify/` | Public auth flow | `OTPVerifyView`, `AllowAny`; no SACCO scope | User enters OTP code | Request: phone, 6-digit code. Response: verification success/token context depending purpose | Marks OTP used; increments attempts on bad code | None |
| POST `/api/v1/accounts/otp/resend/` | Public auth flow | `OTPResendView`, `AllowAny`, `OTPSendThrottle`; no SACCO scope | User taps resend code | Request: phone, purpose. Response: OTP sent or cooldown | Expires old token and creates new OTP | SMS/debug log |
| POST `/api/v1/accounts/password/reset/` | Public auth flow | `PasswordResetRequestView`, `AllowAny`; no SACCO scope | User requests password reset | Request: phone. Response: generic sent message | Creates OTP if user exists | SMS/debug log; intentionally hides account existence |
| POST `/api/v1/accounts/password/reset/confirm/` | Public auth flow | `PasswordResetConfirmView`, `AllowAny`; no SACCO scope | User enters OTP and new password | Request: phone, code, new password pair. Response: success/error | Marks OTP used, updates password | None |

### Member: Membership and Documents

| METHOD + full path | Who calls it | Permission/auth and SACCO scope | Screen interaction | Request and response shape | State changes | Side effects |
|---|---|---|---|---|---|---|
| GET `/api/v1/members/memberships/` | Member | `MembershipListView`, `IsAuthenticated`; filters `user=request.user` | Member opens “My SACCOs” | Query: optional `sacco`, `status`. Response: memberships | None | None |
| POST `/api/v1/members/memberships/` | Member | `MembershipListView.post`, `IsAuthenticated`; SACCO validated active/open | Member applies to join SACCO | Request: SACCO id, custom fields, employment/income. Response: membership detail | Creates `Membership(PENDING)`, `SaccoApplication(SUBMITTED)`, `MemberFieldData` | None |
| GET `/api/v1/members/memberships/<uuid:id>/` | Member | `MembershipDetailView`, owner filter | Member opens membership detail | Request: membership id. Response: member number/status/dates/reasons | None | None |
| POST `/api/v1/members/memberships/<uuid:id>/leave/` | Member | `MembershipLeaveView`, filters by id/user | Member taps leave SACCO | Request: path id. Response: updated membership | Sets membership `LEFT` if no active/approved/disbursed loans | None |
| GET `/api/v1/members/saccos/<uuid:sacco_id>/fields/` | Public/member | `SaccoFieldsView`, `AllowAny` | Application form loads custom fields | Request: SACCO id. Response: labels/types/options|required | None | None |
| GET `/api/v1/members/applications/<uuid:application_id>/documents/` | Applicant or SACCO admin | `MembershipDocumentListView`, `IsAuthenticated`; owner or admin role check | Application review/documents screen | Request: application id. Response: documents and file URLs | None | None |
| POST `/api/v1/members/applications/<uuid:application_id>/documents/` | Applicant | `MembershipDocumentUploadView`, `IsAuthenticated`; application owner only in serializer | Applicant uploads support doc | Request: document type, file, notes. Response: document detail | Creates `MembershipDocument` | File storage write |
| DELETE `/api/v1/members/applications/<uuid:application_id>/documents/<uuid:id>/` | Applicant | `MembershipDocumentDeleteView`, owner only | Applicant removes draft doc | Request: path ids. Response: 204 | Deletes doc only if application `DRAFT` | File record removed |

### Member: Savings, Loans, Guarantors, Dividends

| METHOD + full path | Who calls it | Permission/auth and SACCO scope | Screen interaction | Request and response shape | State changes | Side effects |
|---|---|---|---|---|---|---|
| GET `/api/v1/services/savings-types/` | Public/member | `SavingsTypeViewSet`, list/retrieve `AllowAny`; optional SACCO filter | Product catalog loads | Query `sacco`/`sacco_id`. Response: savings product fields | None | None |
| POST/PUT/PATCH/DELETE `/api/v1/services/savings-types/` and detail routes | Django admin/staff | `SavingsTypeViewSet`, write uses `IsAdminUser`, not SACCO admin scoped | Staff maintains product catalog | Request: savings type fields. Response: saved/deleted resource | Creates/updates/deletes `SavingsType` | None |
| GET `/api/v1/services/savings/` | Member | `SavingListView`, `IsAuthenticated`; filters `membership__user` | Member opens savings screen | Query optional `sacco`. Response: balances by savings account | None | None |
| GET `/api/v1/services/savings/breakdown/` | Member | `SavingsBreakdownView`, `IsAuthenticated`; validates member SACCO | Savings dashboard loads breakdown | Query `sacco_id`. Response: BOSA/FOSA/share/dividend totals | None | None |
| GET `/api/v1/services/loan-types/` | Public/member | `LoanTypeListView`, `AllowAny`; optional SACCO filter | Loan product picker loads | Query `sacco_id`. Response: loan products/rates/terms | None | None |
| GET `/api/v1/services/loans/` | Member | `LoanCollectionView`, `IsAuthenticated`; own loans only | Loan list screen | Query optional status/SACCO. Response: own loan cards | None | None |
| POST `/api/v1/services/loans/` | Member | `LoanCollectionView`, `IsAuthenticated`; membership validated in serializer | Member submits loan application | Request: loan type, amount, term, notes. Response: loan fields | Creates `Loan(PENDING)` then `GUARANTORS_PENDING` or `PENDING_APPROVAL` | Queues guarantor notification task |
| GET `/api/v1/services/loans/eligibility/` | Member | `LoanEligibilityView`, `IsAuthenticated`; membership checked in engine | Loan form asks “how much can I borrow?” | Query `sacco_id`. Response: eligible/max amount/reason/savings | None | Caches result 300s |
| POST `/api/v1/services/loans/apply/` | Member | Same as loan create | Member submits loan application | Same as `POST /services/loans/` | Same | Same |
| GET `/api/v1/services/loans/list/` | Member | `LoanListView`, own loans only | Legacy loan list endpoint | Query optional status/SACCO. Response: own loans | None | None |
| GET `/api/v1/services/loans/<uuid:id>/` | Member | `LoanDetailView`, own loan only | Member opens loan detail | Request: loan id. Response: loan detail | None | None |
| GET `/api/v1/services/loans/<uuid:id>/schedule/` | Member | `RepaymentScheduleView`, own loan only | Member opens repayment schedule | Request: loan id. Response: schedule rows | May create schedule rows if missing and loan approved/pending disbursement/active | Uses amortization engine |
| GET `/api/v1/services/loans/<uuid:loan_id>/guarantors/search/` | Loan applicant | `GuarantorSearchView`, own loan only | Applicant searches guarantor by phone/member number | Query phone or member number. Response: guarantor identity/capacity | Updates/creates `GuaranteeCapacity` snapshot | None |
| POST `/api/v1/services/loans/<uuid:loan_id>/guarantors/` | Loan applicant | `GuarantorRequestView`, own pending loan only | Applicant adds guarantor | Request: guarantor user id, guarantee amount. Response: guarantor row | Creates `Guarantor(PENDING)`; loan `GUARANTORS_PENDING` | None |
| POST `/api/v1/services/loans/<uuid:loan_id>/guarantors/<uuid:guarantor_id>/respond/` | Internal guarantor | `GuarantorRespondView`, `IsAuthenticated`, `services.permissions.GuarantorCapacityCheck`; verifies user is guarantor | Guarantor approves/declines request | Request: `action`, notes. Response: guarantor row | Guarantor `APPROVED`/`DECLINED`; loan may move `PENDING_APPROVAL` or `PENDING` | Notifications |
| GET `/api/v1/services/loans/<uuid:loan_id>/external-guarantors/` | Applicant/admin | `ExternalGuarantorListView`, owner/admin/super-admin check | External guarantor list opens | Request: loan id. Response: external guarantor rows | None | None |
| POST `/api/v1/services/loans/<uuid:loan_id>/external-guarantors/` | Applicant | `ExternalGuarantorCreateView`, own loan in serializer | Applicant adds non-member guarantor | Request: name, phone, ID, income, guarantee amount, optional docs. Response: external guarantor | Creates `ExternalGuarantor(PENDING_SMS/SMS_SENT)` | Sends SMS synchronously; notification |
| POST `/api/v1/services/loans/<uuid:pk>/crb-check/` | SACCO admin | `CRBCheckView`, `IsAuthenticated`, `IsSaccoAdmin`; explicit role check for loan SACCO | Admin clicks “Run CRB Check” | Query optional `force_refresh`. Response: score/band/listed/reference | Creates `CRBCheck` unless cached | Calls Metropol mock/live client |
| GET `/api/v1/services/dividends/declarations/` | SACCO admin | `DividendDeclarationListCreateView`, `IsSaccoAdmin`, `SaccoScopedMixin` | Admin opens dividends | Query none. Response: declarations | None | None |
| POST `/api/v1/services/dividends/declarations/` | SACCO admin | Same | Admin creates dividend declaration | Request: savings type, year, rate, period. Response: declaration | Creates `DividendDeclaration(DRAFT)` | None |
| GET/PUT/PATCH/DELETE `/api/v1/services/dividends/declarations/<uuid:uuid>/` | SACCO admin | `DividendDeclarationDetailView`, SACCO scoped | Admin views/edits/deletes declaration | Request: path id plus update fields. Response: declaration | Updates/deletes only if `DRAFT` | None |
| POST `/api/v1/services/dividends/declarations/<uuid:uuid>/calculate/` | SACCO admin | `DividendCalculateView`, SACCO scoped | Admin clicks calculate | Request: none. Response: total/count | Creates/replaces `DividendPayout(PENDING)`, declaration `CALCULATED` | Uses ledger balances |
| POST `/api/v1/services/dividends/declarations/<uuid:uuid>/approve/` | SACCO admin | `DividendApproveView`, SACCO scoped | Admin approves calculated declaration | Request: none. Response: approved status | Declaration `CALCULATED -> APPROVED` | None |
| POST `/api/v1/services/dividends/declarations/<uuid:uuid>/disburse/` | SACCO admin | `DividendDisburseView`, SACCO scoped | Admin posts dividends to accounts | Request: none. Response: paid count | Payouts `PENDING -> PAID`; saving balances increase; declaration `DISBURSED`; ledger `CREDIT/DIVIDEND_PAYOUT` | None external |
| GET `/api/v1/services/dividends/payouts/` | SACCO admin | `DividendPayoutListView`, SACCO scoped | Admin reviews payout lines | Query optional declaration. Response: payout rows | None | None |

### Payments and Webhooks

| METHOD + full path | Who calls it | Permission/auth and SACCO scope | Screen interaction | Request and response shape | State changes | Side effects |
|---|---|---|---|---|---|---|
| GET `/api/v1/payments/transactions/` | Member | `TransactionListView`, own transactions only | Member opens payment history | Request: none. Response: transaction rows | None | None |
| GET `/api/v1/payments/transactions/<uuid:id>/` | Member | `TransactionDetailView`, own transaction only | Member opens payment receipt | Request: transaction id. Response: transaction detail | None | None |
| GET `/api/v1/payments/mpesa/<uuid:id>/` | Member | `MpesaTransactionDetailView`, `transaction__user=request.user` | Member opens M-Pesa transaction detail | Request: M-Pesa id. Response: provider metadata | None | None |
| POST `/api/v1/payments/deposit/` | Member | `DepositInitiateView`, `IsAuthenticated`; **no membership/SACCO ownership check** | Member starts generic PSP deposit | Request: phone, gross/net/fee, SACCO id. Response: transaction id/fee breakdown/status | Creates `Transaction(PENDING)` | Calls configured PSP checkout |
| POST `/api/v1/payments/callback/` | PSP system | `PaymentCallbackView`, `AllowAny`; provider webhook verification | PSP posts generic payment callback | Request: provider payload. Response: received | Creates `Callback(processed=False)` | Queues `process_payment_callback` |
| POST `/api/v1/payments/mpesa/stk-push/` | Member | `STKPushView`, `IsAuthenticated`; owned saving/loan + SACCO checked | Member taps Pay/Repay, enters PIN prompt | Request: phone, net amount, purpose, SACCO id, saving id or loan id/instalment. Response: checkout ids and gross/net/fee | Creates `Transaction(PENDING)` and `MpesaTransaction` after Daraja accepts | Initiates M-Pesa STK |
| GET `/api/v1/payments/mpesa/stk/<checkout_request_id>/status/` | Member | `STKStatusView`, authenticated; user checked after lookup | Member polls payment status | Request: checkout id. Response: status/result/callback flag | None | None |
| POST `/api/v1/payments/mpesa/b2c/disburse/` | SACCO admin | `B2CDisbursementView`, `IsSaccoAdmin`; loan filtered by `current_sacco` | Admin disburses approved loan | Request: loan id, phone, amount, remarks. Response: conversation id | Creates B2C `Transaction`/`MpesaTransaction`; loan `DISBURSEMENT_PENDING` | Initiates Daraja B2C |
| GET `/api/v1/payments/mpesa/b2c/<conversation_id>/status/` | SACCO admin | `B2CStatusView`, `IsSaccoAdmin`; filters related loan SACCO | Admin checks disbursement status | Request: conversation id. Response: status/receipt/loan/amount | None | None |
| GET `/api/v1/payments/mpesa/b2c/history/` | SACCO admin | `B2CHistoryView`, same SACCO filter | Admin opens disbursement history | Request: none. Response: B2C rows | None | None |
| POST `/api/v1/payments/callback/mpesa/stk/` | Safaricom | `MPesaSTKCallbackView`, `AllowAny`; IP/replay/signature checks | No human user | Request: M-Pesa STK callback JSON. Response: `ResultCode:0` accepted | Queues task; task updates transaction/saving/loan/ledger | May silently accept if enqueue fails |
| POST `/api/v1/payments/callback/mpesa/b2c/` | Safaricom | `B2CCallbackView`, `AllowAny`; IP/replay/signature checks | No human user | Request: M-Pesa B2C result JSON. Response: accepted | Queues task; task updates transaction/loan/ledger | May silently accept if enqueue fails |
| POST `/api/v1/payments/callbacks/` | Internal/test/provider | `CallbackCreateView`, `AllowAny`; only M-Pesa provider gets IP check | Callback test/admin route | Request: provider, raw payload, optional transaction. Response: callback record | Creates `Callback`; does not queue processing | None |

### Ledger, Dashboard, Notifications, Billing, Health

| METHOD + full path | Who calls it | Permission/auth and SACCO scope | Screen interaction | Request and response shape | State changes | Side effects |
|---|---|---|---|---|---|---|
| GET `/api/v1/ledger/entries/` | Member | `LedgerEntryListView`, approved membership owned by user | Member opens ledger entries | Query: `sacco_id`, optional dates/category. Response: ledger rows | None | None |
| GET `/api/v1/ledger/balance/` | Member | `BalanceView`, approved membership owned by user | Dashboard balance loads | Query `sacco_id`. Response: current ledger balance | None | None |
| GET `/api/v1/ledger/statement/` | Member | `StatementView`, approved membership owned by user | Member generates statement | Query `sacco_id`, from/to dates. Response: statement with entries/pagination | None | Intended data access log likely skipped by bad import |
| GET `/api/v1/ledger/statement/pdf/` | Member | `StatementPDFView`, approved membership owned by user | Member downloads statement PDF | Query same. Response: PDF | None | PDF generation via WeasyPrint |
| GET `/api/v1/dashboard/activity/` | Member | `ActivityFeedView`, own user | Dashboard activity feed | Query optional limit. Response: payments/repayments feed | None | Cache 60s |
| GET `/api/v1/dashboard/loans/compare/` | Member | `LoanComparisonView`, approved memberships in engine | Loan comparison screen | Query amount/term. Response: product comparisons | None | None |
| GET `/api/v1/dashboard/portfolio/` | Member | `PortfolioView`, own memberships | Portfolio dashboard | Request none. Response: aggregate savings/loans/SACCOs | None | Cache 120s |
| GET `/api/v1/dashboard/saccos/` | Member | `SACCOSwitcherView`, own approved memberships | SACCO switcher | Request none. Response: SACCO cards | None | None |
| GET `/api/v1/dashboard/state/` | Member | `DashboardStateView`, own memberships | Dashboard shell decides empty/pending state | Request none. Response: state/message/counts | None | Cache 60s |
| GET `/api/v1/notifications/` | Member/admin | `NotificationListView`, `user=request.user` | Notification center | Query category/is_read. Response: notification rows | None | None |
| POST `/api/v1/notifications/<uuid:id>/read/` | Member/admin | `MarkReadView`, notification owner only | User opens/marks one notification | Request path id. Response success | Sets `Notification.is_read=True` | None |
| POST `/api/v1/notifications/read-all/` | Member/admin | `MarkAllReadView`, own notifications | User taps mark all read | Request none. Response count | Updates all unread notifications for user | None |
| POST `/api/v1/notifications/device/` | Mobile/web app | `DeviceTokenRegisterView`, authenticated | App registers push token | Request token/platform. Response token/platform | Upserts `DeviceToken` active | Enables FCM push later |
| GET `/api/v1/billing/invoices/` | SACCO admin/super admin | `MonthlyInvoiceListView`, role-scoped by admin SACCO ids or super admin | Billing invoices list | Request none. Response invoice rows | None | None |
| GET `/api/v1/billing/invoices/<uuid:id>/` | SACCO admin/super admin | `MonthlyInvoiceDetailView`, same queryset scoping | Invoice detail screen | Request id. Response invoice detail/payload | None | None |
| POST `/api/v1/billing/invoices/<uuid:invoice_id>/resend/` | SACCO admin/super admin | `MonthlyInvoiceResendView`, object permission check | Admin clicks resend invoice | Request id. Response success/error | Updates invoice `SENT`/`sent_at` in service | Sends email |
| GET `/api/v1/billing/invoices/<uuid:invoice_id>/download/` | SACCO admin/super admin | `MonthlyInvoiceDownloadView`, object permission check | Admin downloads invoice | Query `format=csv|pdf`. Response file | None | Builds CSV/PDF |
| GET `/api/v1/health/` and `/api/v1/health/live/` | Load balancer/system | No auth | Health probe | Request none. Response `status: ok` | None | None |
| GET `/api/v1/health/ready/` | Load balancer/system | No auth | Readiness probe | Request none. Response DB/cache checks | None | Touches cache and DB |
| GET `/health/` | Load balancer/system | No auth | Root health probe | Same as liveness | None | None |
| GET `/health/ready/` | Load balancer/system | No auth | Root readiness probe | Same as readiness | None | None |
| GET `/swagger/`, GET `/redoc/` | Developer/admin | Public schema view in `config/urls.py` | API docs opened | Request none. Response API documentation UI | None | None |
| `/admin/` | Django admin user | Django admin auth | Back-office admin opens Django admin | Standard Django admin | Can mutate registered models depending admin permissions | Django admin side effects |

### SACCO Admin and Super Admin

Every path below is exposed twice: `/api/v1/management/...` and `/api/v1/saccomanagement/...`.

| METHOD + full path | Who calls it | Permission/auth and SACCO scope | Screen interaction | Request and response shape | State changes | Side effects |
|---|---|---|---|---|---|---|
| POST `/api/v1/management/roles/assign/` | Super admin | `RoleAssignView`, `IsSuperAdmin` | Platform operator assigns user role | Request user id, role name, optional SACCO id. Response role | Creates `Role` | None |
| DELETE `/api/v1/management/roles/<uuid:role_id>/` | Super admin | `RoleRevokeView`, `IsSuperAdmin` | Platform operator revokes role | Request role id. Response detail | Deletes `Role`; blocks self SUPER_ADMIN revoke | None |
| GET `/api/v1/management/roles/?user_id=` | SACCO admin/super admin | `UserRolesView`, `IsSaccoAdminOrSuperAdmin`; SACCO admin limited to users in their SACCO/roles | Admin views roles for user | Query user id. Response role list | None | None |
| GET `/api/v1/management/audit-logs/` | Super admin | `AuditLogListView`, `IsSuperAdmin` | Audit log screen | Query action/resource/user. Response audit rows | None | None |
| GET `/api/v1/management/dashboard/disbursements/` | SACCO admin | `DisbursementsDashboardView`, `IsSaccoAdmin`, `SaccoScopedMixin` | Admin dashboard disbursements card | Request `X-Sacco-ID` optional/required for multi-admin. Response counts/totals/recent | None | None |
| GET `/api/v1/management/dashboard/contributions/` | SACCO admin | `ContributionsDashboardView`, `IsSaccoAdmin`, `SaccoScopedMixin` | Admin dashboard contributions card | Response received/expected/missed/recent | None | None |
| GET `/api/v1/management/members/` | SACCO admin | `AdminMemberListView`, SACCO scoped | Admin member list | Query search/status. Response members | None | None |
| GET `/api/v1/management/members/<uuid:membership_id>/` | SACCO admin | `AdminMemberDetailView`, SACCO scoped | Admin opens member profile | Request membership id. Response KYC/financial/member detail | None | Creates data access log through `DataAccessMixin` |
| GET `/api/v1/management/stats/` | SACCO admin | `AdminSaccoStatsView`, SACCO scoped | Admin stats dashboard | Response counts/savings/loans/defaults/recent txns | None | None |
| GET `/api/v1/management/applications/<uuid:id>/review/` | SACCO admin | `ApplicationReviewView`, SACCO scoped | Admin opens application review | Response application + documents | None | None |
| PATCH `/api/v1/management/applications/<uuid:id>/review/` | SACCO admin | Same | Admin approves/rejects application | Request status, notes. Response application + membership id | Updates `SaccoApplication`; approval creates/updates `Membership(APPROVED)` | Notification; audit log |
| GET `/api/v1/management/kyc/queue/` | SACCO admin/super admin | `AdminKYCQueueView`, `IsSaccoAdminOrSuperAdmin`; queryset from `AdminKYCQuerysetMixin` | Admin opens KYC queue | Response KYC records | None | Data access mixin |
| PATCH `/api/v1/management/kyc/<uuid:kyc_id>/review/` | SACCO admin/super admin | `AdminKYCReviewView`, `IsSaccoAdminOrSuperAdmin`; queryset filtered by accessible SACCO members | Admin approves/rejects KYC | Request status, rejection/manual reason. Response KYC | Updates KYC status/reviewer/verified timestamp | None confirmed |
| GET `/api/v1/management/loans/approvals/` | SACCO admin | `LoanApprovalListView`, SACCO scoped | Loan approval queue | Response loans, guarantor summary, docs, CRB fields | None | None |
| PATCH `/api/v1/management/loans/<uuid:id>/status/` | SACCO admin | `AdminLoanApprovalView`, SACCO scoped | Admin changes loan workflow status | Request status, notes, override reason. Response loan status/disbursement payload | `UNDER_REVIEW`, `APPROVED`, `REJECTED`, or `DISBURSEMENT_PENDING` via disbursement helper | Notifications, audit, schedule creation, Daraja B2C for disbursement |
| GET `/api/v1/management/reports/` | SACCO admin | `SaccoReportView`, SACCO scoped | Admin generates operational report | Query type, dates. Response report JSON | None | None |
| GET `/api/v1/management/reports/sasra/` | SACCO admin | `SASRAReturnView`, `IsSaccoAdmin`; requires `X-Sacco-ID` and explicit role check | Admin exports SASRA return | Query type/as-of/period/format. Response JSON/XLSX | Creates `SystemAuditLog` | XLSX generation |
| GET `/api/v1/management/settings/` | SACCO admin | `SaccoSettingsView`, SACCO scoped | Admin opens SACCO settings | Response settings | Creates default `SaccoSettings` if missing | None |
| PATCH `/api/v1/management/settings/` | SACCO admin | Same | Admin edits settings | Request allowed settings fields. Response updated settings | Updates `SaccoSettings`; syncs registration fee/loan multiplier to `Sacco` | None |
| GET `/api/v1/management/sms/campaigns/` | SACCO admin | `BulkSMSCampaignCollectionView`, SACCO role first/current | Bulk SMS list | Response campaigns | None | None |
| POST `/api/v1/management/sms/campaigns/` | SACCO admin | Same; audience scoped to SACCO memberships | Admin drafts SMS campaign | Request message, audience filter. Response campaign id/status/count | Creates `SMSCampaign(DRAFT)` and recipients | None yet |
| GET `/api/v1/management/sms/campaigns/<uuid:id>/` | SACCO admin | SACCO campaign queryset | Admin opens campaign detail | Response campaign and recipients | None | None |
| POST `/api/v1/management/sms/campaigns/<uuid:id>/send/` | SACCO admin | SACCO campaign queryset | Admin clicks send | Request id. Response campaign | Sets campaign `SENDING` | Queues `send_bulk_sms_campaign_task` |
| GET `/api/v1/management/liquidity/` | SACCO admin | `LiquidityStatusView`, `IsSaccoAdmin`; obtains SACCO by current context or first role | Admin opens liquidity view | Response reserves/pending/utilisation/risk | None | None |
| GET `/api/v1/management/npl/` | SACCO admin | `NPLDashboardView`, `IsSaccoAdmin`; obtains SACCO by current context or first role | Admin opens NPL view | Response unresolved flags/outstanding/NPL ratio | None | None |
| GET/POST `/api/v1/management/dividends/declarations/` | SACCO admin | Same dividend view as services; SACCO scoped | Admin manages dividends | Same as services route | Same | Same |
| GET/PUT/PATCH/DELETE `/api/v1/management/dividends/declarations/<uuid:pk>/` | SACCO admin | Same view, pk alias handling | Admin views/edits/deletes dividend | Same | Same | Same |
| POST `/api/v1/management/dividends/declarations/<uuid:pk>/calculate/` | SACCO admin | Same | Admin calculates dividend | Same | Same | Same |
| POST `/api/v1/management/dividends/declarations/<uuid:pk>/approve/` | SACCO admin | Same | Admin approves dividend | Same | Same | Same |
| POST `/api/v1/management/dividends/declarations/<uuid:pk>/disburse/` | SACCO admin | Same | Admin posts dividend | Same | Same | Same |
| GET `/api/v1/management/dividends/payouts/` | SACCO admin | Same | Admin reviews payouts | Same | None | None |
| GET `/api/v1/management/external-guarantors/` | SACCO admin | `ExternalGuarantorAdminListView`, `get_admin_sacco` role/header scoping | Admin reviews external guarantors | Query status. Response guarantors | None | None |
| PATCH `/api/v1/management/external-guarantors/<uuid:id>/review/` | SACCO admin | `ExternalGuarantorAdminReviewView`, id + SACCO filter | Admin approves/rejects external guarantor | Request action, notes. Response guarantor detail | Status to `APPROVED_BY_ADMIN` or `REJECTED_BY_ADMIN` | Applicant notification |
| POST `/api/v1/management/import/` | SACCO admin | `MemberImportCreateView`, SACCO scoped | Admin uploads member CSV/XLSX | Multipart `file`. Response job id/status | Creates `MemberImportJob`; processes synchronously | Creates users/members/savings/ledger via import utils |
| GET `/api/v1/management/import/<uuid:job_id>/` | SACCO admin | `MemberImportStatusView`, SACCO scoped | Admin checks import result | Request job id. Response progress/errors | None | None |
| GET `/api/v1/management/superadmin/overview/` | Super admin | `SystemOverviewView`, `IsSuperAdmin` | Platform overview dashboard | Response volume/SACCO/member/revenue/system status | None | None |
| GET `/api/v1/management/superadmin/revenue-chart/` | Super admin | `PlatformRevenueChartView`, `IsSuperAdmin` | Revenue chart | Response 12 months revenue | None | None |
| GET `/api/v1/management/superadmin/top-saccos/` | Super admin | `TopSaccosView`, `IsSuperAdmin` | Top SACCOs leaderboard | Response SACCO volume/member/fee/health | None | None |
| GET `/api/v1/management/superadmin/alerts/` | Super admin | `PlatformAlertsView`, `IsSuperAdmin` | Compliance alerts screen | Response open/investigating flags | None | None |
| GET `/api/v1/management/superadmin/transactions/live/` | Super admin | `LiveTransactionFeedView`, `IsSuperAdmin` | Live transaction feed | Response last 50 M-Pesa txns | None | Cache 10s |
| GET `/api/v1/management/superadmin/saccos/` | Super admin | `AllSaccosListView`, `IsSuperAdmin` | Platform SACCO directory | Response all SACCOs health | None | None |
| GET `/api/v1/management/superadmin/members/` | Super admin | `AllMembersListView`, `IsSuperAdmin` | Platform member directory | Query SACCO/search/page. Response members | None | None |

## Public / Auth / KYC

### `POST /api/v1/accounts/register/`

- Who calls it: unauthenticated visitor.
- Auth/permission: `AllowAny` via `accounts.views.RegisterView`.
- Screen trigger: user fills registration form with email, name, phone, password.
- Request: `email`, `first_name`, `last_name`, `phone_number`, `password`, `password2`.
- Response: registered user profile fields from `UserRegistrationSerializer`.
- State changes: creates `accounts.User`; creates `accounts.KYCVerification` with `NOT_STARTED`.
- Side effects: none confirmed.

### `POST /api/v1/accounts/login/`

- Who calls it: unauthenticated user.
- Auth/permission: `AllowAny`, `accounts.views.LoginView`.
- Screen trigger: login form submit.
- Request: `email`, `password`.
- Response: JWT token pair plus user/profile/SACCO context.
- State changes: none.
- Side effects: none confirmed.

### `POST /api/v1/accounts/oauth/google/callback/`

- Who calls it: frontend after Google OAuth redirect.
- Auth/permission: `AllowAny`, `accounts.oauth_views.GoogleOAuthCallbackView`.
- Request: Google auth payload per `GoogleAuthSerializer`.
- Response: login/signup response if debug mock works.
- State changes: may create/login user.
- Side effects: CONFIRMED STUB in `accounts/integrations/oauth.py`; production raises `NotImplementedError`.

### `POST /api/v1/accounts/logout/`

- Who calls it: authenticated user.
- Auth/permission: `IsAuthenticated`, `accounts.views.LogoutView`.
- Request: refresh token.
- Response: logout success.
- State changes: blacklists token if provided.

### `POST /api/v1/accounts/token/`, `POST /api/v1/accounts/token/refresh/`

- Who calls it: frontend auth client.
- Auth/permission: SimpleJWT views.
- Request: credentials or refresh token.
- Response: JWT access/refresh.

### `GET/PATCH /api/v1/accounts/me/`

- Who calls it: authenticated user.
- Auth/permission: `IsAuthenticated`, `MeView`.
- Request: GET none; PATCH editable profile fields.
- Response: `UserProfileSerializer` with SACCO context and biometric flag.
- State changes: PATCH updates profile fields.

### `POST /api/v1/accounts/device/register/`, `GET /api/v1/accounts/devices/`, `DELETE /api/v1/accounts/device/<device_id>/`

- Who calls it: authenticated mobile app.
- Auth/permission: biometric views, `IsAuthenticated`.
- Trigger: enabling biometric login, listing trusted devices, revoking device.
- Request: `device_id`, `device_name`, `platform`, `push_token`, `biometric_enabled`.
- State changes: creates/updates/deletes `accounts.UserDevice`.

### `POST /api/v1/accounts/kyc/submit-id/`

- Who calls it: authenticated member.
- Auth/permission: `IsAuthenticated`, `KYCSubmitIDView`.
- Trigger: member enters ID details for verification.
- Request: national ID and optional date/name data.
- Response: KYC/IPRS outcome.
- State changes: updates `KYCVerification` IPRS fields/status.
- Side effects: calls `IPRSClient.verify_id`.

### `POST /api/v1/accounts/kyc/upload/`

- Who calls it: authenticated member.
- Auth/permission: `IsAuthenticated`, `KYCUploadView`.
- Trigger: member uploads ID/passport/Huduma file.
- Request: `document_type`, `file`.
- Response: KYC status/file metadata.
- State changes: updates file field and KYC status/submission timestamp.
- Side effects: attempts IPRS without blocking document submission.

### `GET /api/v1/accounts/kyc/status/`

- Who calls it: authenticated member.
- Auth/permission: `IsAuthenticated`, `KYCStatusView`.
- Response: KYC status, IPRS status/error, admin review reason, uploaded docs.

### `POST /api/v1/accounts/password/change/`

- Who calls it: authenticated user.
- Request: `old_password`, `new_password`, `new_password2`.
- State changes: updates password.

### `GET /api/v1/accounts/public-stats/`

- Who calls it: unauthenticated landing/dashboard.
- Auth/permission: `AllowAny`.
- Response: public platform stats.

### `GET /api/v1/accounts/saccos/`, `GET /api/v1/accounts/saccos/<id>/`

- Who calls it: public/member frontend.
- Auth/permission: list/detail allow public in `SaccoListView`/`SaccoDetailView`.
- Trigger: SACCO discovery/search.
- Response: SACCO identity, sector, county, membership type, fee, loan defaults.

### `POST /api/v1/accounts/otp/send/`, `POST /api/v1/accounts/otp/verify/`, `POST /api/v1/accounts/otp/resend/`

- Who calls it: unauthenticated visitor or auth flow.
- Auth/permission: public; `OTPSendThrottle` for send/resend.
- Trigger: phone verification/password reset/login OTP screens.
- Request: send/resend `phone_number`, `purpose`; verify `phone_number`, `code`.
- State changes: creates/uses `OTPToken`; increments attempts in `otp_utils.verify_otp`.
- Side effects: Africa's Talking SMS or debug log.

### `POST /api/v1/accounts/password/reset/`, `POST /api/v1/accounts/password/reset/confirm/`

- Who calls it: unauthenticated user.
- Request: phone number; confirm uses phone/code/new password fields.
- State changes: OTP token and password update.
- Side effects: SMS, but request endpoint hides whether user exists.

## Member: Membership Applications

### `GET/POST /api/v1/members/memberships/`

- Who calls it: authenticated member.
- Auth/permission: `IsAuthenticated`.
- Trigger: list memberships or submit join SACCO form.
- Request POST: `sacco`, optional `custom_fields`, employment fields, monthly income.
- Response: membership list/detail.
- State changes: creates `Membership(PENDING)`, `SaccoApplication(SUBMITTED)`, `MemberFieldData`.
- Side effects: none confirmed.

### `GET /api/v1/members/memberships/<id>/`

- Who calls it: membership owner.
- Scoping: filters `Membership.user=request.user`.
- Response: membership detail.

### `POST /api/v1/members/memberships/<id>/leave/`

- Who calls it: member.
- Scoping: filters membership by `id` and `user`.
- State changes: sets `Membership.status=LEFT` if no active/approved/disbursed loans.

### `GET /api/v1/members/saccos/<sacco_id>/fields/`

- Who calls it: application form.
- Auth/permission: `AllowAny`.
- Response: dynamic application fields.

### `GET/POST /api/v1/members/applications/<application_id>/documents/`

- Who calls it: applicant; SACCO admin can list.
- Auth/permission: `IsAuthenticated`.
- Request POST: `document_type`, `file`, optional `notes`.
- State changes: creates `MembershipDocument`.

### `DELETE /api/v1/members/applications/<application_id>/documents/<id>/`

- Who calls it: applicant.
- Scoping: application owner only.
- State changes: deletes document only when application is `DRAFT`.

## Member: Savings, Loans, Guarantors, Dividends

### Router `/api/v1/services/savings-types/`

- Methods: ViewSet provides list/retrieve public and create/update/delete admin-only via `IsAdminUser`.
- Trigger: load SACCO product catalog.
- Request create/update: `SavingsType` fields.

### `GET /api/v1/services/savings/`

- Who calls it: authenticated member.
- Scoping: `Saving.membership.user=request.user`, optional `?sacco=`.
- Response: savings account balances/totals/status.

### `GET /api/v1/services/savings/breakdown/`

- Who calls it: member.
- Response: BOSA/FOSA/share capital/dividend eligible totals.

### `GET /api/v1/services/loan-types/`

- Public product list; optional `sacco_id`.

### `GET/POST /api/v1/services/loans/`

- Who calls it: member.
- GET: list own loans, optional `status`, `sacco`.
- POST: same as loan application.
- Request POST: `loan_type`, `amount`, `term_months`, `application_notes`.
- State changes: creates `Loan(PENDING)`, then `GUARANTORS_PENDING` if guarantors required or `PENDING_APPROVAL` if not.
- Side effects: queues `notify_guarantors_task`, but only pre-existing `Guarantor` rows are notified.

### `GET /api/v1/services/loans/eligibility/`

- Who calls it: member.
- Request: `?sacco_id=`.
- Response: eligibility, max amount, savings, existing loan balance, guarantor count.
- State changes: none; caches 5 minutes.

### `POST /api/v1/services/loans/apply/`

- Same create behavior as `POST /services/loans/`.

### `GET /api/v1/services/loans/list/`

- Same list behavior as `GET /services/loans/`.

### `GET /api/v1/services/loans/<id>/`

- Who calls it: loan owner.
- Response: loan detail.

### `GET /api/v1/services/loans/<id>/schedule/`

- Who calls it: loan owner.
- Response: repayment schedule.
- State changes: if no schedule exists and loan status is `APPROVED`, `DISBURSEMENT_PENDING`, or `ACTIVE`, creates `RepaymentSchedule` rows using amortization engine.

### `GET /api/v1/services/loans/<loan_id>/guarantors/search/`

- Who calls it: loan applicant.
- Request: `phone` or `member_number`.
- Response: matching approved member in same SACCO and available capacity.

### `POST /api/v1/services/loans/<loan_id>/guarantors/`

- Who calls it: loan applicant.
- Request: `guarantor_user_id`, `guarantee_amount`.
- State changes: creates `Guarantor(PENDING)`, loan moves to `GUARANTORS_PENDING`.

### `POST /api/v1/services/loans/<loan_id>/guarantors/<guarantor_id>/respond/`

- Who calls it: requested internal guarantor.
- Request: `action=APPROVE|DECLINE`, optional `notes`.
- State changes: guarantor `APPROVED`/`DECLINED`; loan may become `PENDING_APPROVAL` or reset to `PENDING`.
- Side effects: guarantee capacity recalculation and notification.

### `GET/POST /api/v1/services/loans/<loan_id>/external-guarantors/`

- Who calls it: applicant lists/adds external guarantors; admins can list.
- Request POST: full name, phone, ID, employment status, monthly income, guarantee amount, optional ID images.
- State changes: creates `ExternalGuarantor(PENDING_SMS/SMS_SENT)`.
- Side effects: sends SMS synchronously; creates notification.

### `POST /api/v1/services/loans/<pk>/crb-check/`

- Who calls it: SACCO admin.
- Auth/scoping: `IsSaccoAdmin` and explicit role check against loan SACCO.
- Request: optional `?force_refresh=true`.
- State changes: creates `CRBCheck` unless recent cached check exists.
- Side effects: Metropol/CRB API or mock.

### Dividend endpoints

- `GET/POST /api/v1/services/dividends/declarations/`
- `GET/PUT/PATCH/DELETE /api/v1/services/dividends/declarations/<uuid>/`
- `POST /api/v1/services/dividends/declarations/<uuid>/calculate/`
- `POST /api/v1/services/dividends/declarations/<uuid>/approve/`
- `POST /api/v1/services/dividends/declarations/<uuid>/disburse/`
- `GET /api/v1/services/dividends/payouts/`

Who calls them: SACCO admin. Auth/scoping: `IsAuthenticated`, `IsSaccoAdmin`, `SaccoScopedMixin`. Requests: declaration uses `savings_type`, `financial_year`, `declared_rate`, `period_start`, `period_end`; calculate/approve/disburse have no required body. State changes: declaration `DRAFT -> CALCULATED -> APPROVED -> DISBURSED`; payout `PENDING -> PAID`; savings balance increases; ledger entries created as `DIVIDEND_PAYOUT`.

## Payments / Webhooks

### `GET /api/v1/payments/transactions/`, `GET /api/v1/payments/transactions/<id>/`

- Who calls it: authenticated member.
- Scoping: `Transaction.user=request.user`.
- Response: transaction history/detail.

### `GET /api/v1/payments/mpesa/<id>/`

- Who calls it: member.
- Scoping: `MpesaTransaction.transaction.user=request.user`.
- Response: M-Pesa metadata/status.

### `POST /api/v1/payments/deposit/`

- Who calls it: member/frontend using generic PSP abstraction.
- Request: `phone`, `gross_amount`, `net_amount`, `platform_fee`, `sacco`.
- State changes: creates `Transaction(PENDING)`.
- Side effects: calls configured PSP checkout. CONFIRMED RISK: no saving account is linked, so successful generic callback cannot credit a saving.

### `POST /api/v1/payments/mpesa/stk-push/`

- Who calls it: member.
- Request: `phone_number`, `amount` (net amount), `purpose=SAVING_DEPOSIT|LOAN_REPAYMENT`, `sacco_id`, plus `saving_id` or `loan_id` and `instalment_number`.
- State changes: after Daraja accepts, creates `Transaction(PENDING)` with gross-up fee and `MpesaTransaction`.
- Side effects: STK push to phone.

### `GET /api/v1/payments/mpesa/stk/<checkout_request_id>/status/`

- Who calls it: member polling payment.
- Scoping: explicit transaction user check.
- Response: transaction status, result code/description, callback flag.

### `POST /api/v1/payments/callback/mpesa/stk/`

- Who calls it: Safaricom.
- Auth/permission: public with IP/replay/signature checks in code.
- State changes: queues `process_stk_callback_task`; returns success even on some errors to stop retries.
- Side effects: async ledger/savings/loan repayment/notification.

### `POST /api/v1/payments/mpesa/b2c/disburse/`

- Who calls it: SACCO admin.
- Request: `loan_id`, `phone_number`, `amount`, optional `remarks`.
- State changes: creates B2C `Transaction(PENDING)`, `MpesaTransaction`, loan `DISBURSEMENT_PENDING`.
- Side effects: Daraja B2C request.

### `GET /api/v1/payments/mpesa/b2c/<conversation_id>/status/`, `GET /api/v1/payments/mpesa/b2c/history/`

- Who calls it: SACCO admin.
- Scoping: `related_loan.membership.sacco=current_sacco`.
- Response: B2C status/history.

### `POST /api/v1/payments/callback/mpesa/b2c/`

- Who calls it: Safaricom.
- State changes: queues B2C callback task; success callback activates loan and ledger; failure returns loan to `APPROVED`.

### `POST /api/v1/payments/callback/`

- Who calls it: generic PSP.
- Auth: provider webhook verification.
- State changes: creates `Callback`, queues generic processing.
- Risk: generic task references nonexistent `callback.payload`.

### `POST /api/v1/payments/callbacks/`

- Who calls it: internal/admin/test callback poster.
- Auth: `AllowAny`; if provider is M-Pesa checks IP.
- State changes: creates `Callback` only; no async processing called here.

## Ledger

### `GET /api/v1/ledger/entries/`

- Who calls it: member.
- Request: required `sacco_id`; optional date/category filters.
- Response: paginated ledger rows.
- Scoping: approved membership owned by request user.

### `GET /api/v1/ledger/balance/`

- Who calls it: member.
- Request: required `sacco_id`.
- Response: current ledger balance.

### `GET /api/v1/ledger/statement/`, `GET /api/v1/ledger/statement/pdf/`

- Who calls it: member.
- Request: `sacco_id`, `from_date`, `to_date` max one year.
- Response: statement JSON or PDF.
- Side effects: intended ODPC access log, but likely silently skipped due bad import.

## SACCO Admin / Management

All routes below are available under both `/api/v1/management/` and `/api/v1/saccomanagement/`.

- `POST /roles/assign/`: assign role; admin/super-admin path in `RoleAssignView`.
- `DELETE /roles/<role_id>/`: revoke role.
- `GET /roles/`: list roles.
- `GET /audit-logs/`: super admin audit log list.
- `GET /dashboard/disbursements/`: SACCO admin disbursement dashboard.
- `GET /dashboard/contributions/`: SACCO admin contributions dashboard.
- `GET /members/`: SACCO member list scoped by `SaccoScopedMixin`.
- `GET /members/<membership_id>/`: SACCO member detail, ODPC log attempted.
- `GET /stats/`: SACCO dashboard stats.
- `GET/PATCH /applications/<id>/review/`: admin review application; PATCH sets application status and creates/updates approved membership.
- `GET /kyc/queue/`: SACCO admin KYC queue.
- `PATCH /kyc/<kyc_id>/review/`: KYC approve/reject.
- `GET /loans/approvals/`: loan approval queue.
- `PATCH /loans/<id>/status/`: loan workflow transition to `UNDER_REVIEW`, `APPROVED`, `REJECTED`, or `DISBURSED`.
- `GET /reports/`: SACCO reports.
- `GET /reports/sasra/`: SASRA return.
- `GET/PATCH /settings/`: SACCO settings.
- `GET/POST /sms/campaigns/`: list/create bulk SMS campaigns.
- `GET /sms/campaigns/<id>/`: campaign detail.
- `POST /sms/campaigns/<id>/send/`: queue bulk SMS send.
- `GET /liquidity/`: liquidity status.
- `GET /npl/`: NPL dashboard.
- Dividend routes duplicate service dividend routes but use `<pk>` instead of `<uuid>`.
- `GET /external-guarantors/`: admin external guarantor list.
- `PATCH /external-guarantors/<id>/review/`: approve/reject external guarantor.
- `POST /import/`: upload/queue member import.
- `GET /import/<job_id>/`: import job status.
- Super admin: `GET /superadmin/overview/`, `/revenue-chart/`, `/top-saccos/`, `/alerts/`, `/transactions/live/`, `/saccos/`, `/members/`.

Frontend meaning: these are admin dashboard screens for roles, member lists, application review, KYC review, loan review/disbursement, compliance reporting, SMS campaign creation, imports, and platform operator dashboards. State-changing routes all mutate the named workflow rows and often create notifications/audit logs.

## External Guarantor Public

### `POST /api/v1/guarantors/external/respond/<response_token>/`

- Who calls it: external guarantor via SMS link/page.
- Auth/permission: `AllowAny`; bearer token is URL token.
- Request: `action=ACCEPT|DECLINE`, optional `notes`.
- State changes: `ExternalGuarantor` status `SMS_SENT -> ACCEPTED|DECLINED`.
- Side effects: applicant notification.

## Notifications

- `GET /api/v1/notifications/`: list current user notifications with optional `category`, `is_read`.
- `POST /api/v1/notifications/<id>/read/`: marks one notification read.
- `POST /api/v1/notifications/read-all/`: marks all read.
- `POST /api/v1/notifications/device/`: registers FCM device token.

## Dashboard

- `GET /api/v1/dashboard/activity/`: member activity feed.
- `GET /api/v1/dashboard/loans/compare/?amount=&term=`: compare loan products.
- `GET /api/v1/dashboard/portfolio/`: unified portfolio.
- `GET /api/v1/dashboard/saccos/`: SACCO switcher cards.
- `GET /api/v1/dashboard/state/`: dashboard state.

## Billing

- `GET /api/v1/billing/invoices/`: SACCO admin/super admin invoice list scoped by role.
- `GET /api/v1/billing/invoices/<id>/`: invoice detail.
- `POST /api/v1/billing/invoices/<invoice_id>/resend/`: resend invoice email.
- `GET /api/v1/billing/invoices/<invoice_id>/download/?format=csv|pdf`: invoice download.

## Health / Docs / Admin

- `GET /health/`, `GET /health/ready/`: root health checks.
- `GET /api/v1/health/`, `/live/`, `/ready/`: API health checks.
- `GET /swagger/`, `/redoc/`: API docs.
- `/admin/`: Django admin.

---

# PART 3 - THE MONEY, TRACED END TO END

## 1. Member deposits money

### M-Pesa STK saving contribution

1. Member submits `POST /api/v1/payments/mpesa/stk-push/` with `purpose=SAVING_DEPOSIT`, `amount`, `sacco_id`, `saving_id`, `phone_number`. CONFIRMED IN CODE: `payments.views.STKPushView.post`.
2. `STKPushRequestSerializer` validates phone, amount between KES 10 and 300,000, purpose, and required `saving_id`.
3. `STKPushView._get_owned_saving` confirms the saving belongs to `request.user` and `sacco_id`.
4. Fee is calculated as `amount * billing.services.TRANSACTION_FEE_RATE` (`0.02`) and rounded `ROUND_HALF_UP`; gross is net + fee.
5. `DarajaClient.initiate_stk_push` sends gross amount to M-Pesa.
6. Inside `transaction.atomic`, code creates `payments.Transaction(PENDING, type=DEPOSIT, amount=net_amount, fee_amount=fee)` and `payments.MpesaTransaction` linked to saving.
7. Safaricom calls `POST /api/v1/payments/callback/mpesa/stk/`; `MPesaSTKCallbackView.post` checks IP, replay cache, signature, transaction existence, then queues `process_stk_callback_task`.
8. `payments.tasks.process_stk_callback_task` locks `MpesaTransaction` with `select_for_update` inside `atomic`.
9. `_callback_already_processed` returns true only if `callback_received` and transaction status is `COMPLETED` or `FAILED`.
10. On result code `0`, `_process_successful_callback` marks callback received, stores receipt, marks `Transaction.COMPLETED`, warns if callback amount differs from expected gross by more than KES 0.01, then credits the net amount.
11. `_apply_saving_deposit` increments `Saving.amount`, `Saving.total_contributions`, `last_transaction_date`, then calls `ledger.utils.create_ledger_entry` with `CREDIT/SAVING_DEPOSIT`.
12. `_record_platform_fee_for_sacco` calls `billing.services.record_collected_fee`, creating `PlatformFee` and `PlatformRevenue`.
13. A payment notification is created.

Failure/double callback behavior:

- If callback never arrives: transaction remains `PENDING`; no saving or ledger change. `reconcile_pending_transactions` only handles generic PSP provider status, not M-Pesa Daraja STK status. `STKStatusView` only reads local state; it does not query Daraja.
- If callback arrives twice: view-level replay cache rejects duplicate within 24h; task-level `_callback_already_processed` also avoids reprocessing after status completed/failed.
- Inconsistency risk: `create_ledger_entry` catches exceptions and returns `None`. `_apply_saving_deposit` does not check return value, so saving can be increased without a ledger entry if ledger creation fails.
- Atomicity: callback task is inside `transaction.atomic`; saving, transaction, M-Pesa metadata, platform fee, and notification creation happen in one DB transaction unless a swallowed ledger failure occurs.

### Generic PSP deposit

CONFIRMED IN CODE: `payments.views.DepositInitiateView` creates a `Transaction(PENDING)` with net amount/fee and calls provider checkout. However, it does not link a membership or saving. `payments.tasks.process_payment_callback` tries `callback.payload` and `transaction.membership`, neither of which exists on the models read (`Callback.raw_payload`, `Transaction` has no membership FK). This flow is currently not production-functional for crediting deposits.

## 2. Member applies for and receives a loan

1. Member calls `POST /api/v1/services/loans/` or `/loans/apply/`. `services.views.LoanEligibilityCreateMixin.create` validates `LoanApplySerializer`.
2. `LoanApplySerializer.validate` checks loan type max amount/max term and confirms approved `Membership` for `loan_type.sacco`.
3. `services.engines.loan_limits.calculate_loan_limit` confirms approved membership, minimum months, active savings, existing active balance, and no defaulted loans.
4. `LoanApplySerializer.create` creates `Loan(status=PENDING, outstanding_balance=amount, interest_rate=loan_type.interest_rate)`.
5. `LoanApplyView.perform_create` sets loan to `GUARANTORS_PENDING` if guarantors required, otherwise `PENDING_APPROVAL`. It queues `notify_guarantors_task`, but at initial creation no internal guarantor rows exist unless created separately later.
6. Applicant adds internal guarantors through `GuarantorRequestView.post`, creating `Guarantor(PENDING)` and keeping/moving loan to `GUARANTORS_PENDING`.
7. Internal guarantor responds through `GuarantorRespondView.post`. Approval sets `Guarantor.APPROVED`, recalculates `GuaranteeCapacity`, and if required count reached sets loan `PENDING_APPROVAL`. Decline sets `Guarantor.DECLINED` and resets loan to `PENDING`.
8. External guarantors may be created in `guarantor.external_views.ExternalGuarantorCreateView`; status moves `PENDING_SMS -> SMS_SENT -> ACCEPTED/DECLINED -> APPROVED_BY_ADMIN/REJECTED_BY_ADMIN`.
9. SACCO admin runs `POST /management/loans/<pk>/crb-check/`; creates `CRBCheck`.
10. SACCO admin moves loan `PENDING_APPROVAL -> UNDER_REVIEW -> APPROVED` via `saccomanagement.admin_views.AdminLoanApprovalView.partial_update`. Approval requires guarantor completeness and CRB check. Negative CRB requires `override_reason`.
11. On approval, `saccomanagement.loan_utils.persist_loan_repayment_schedule` creates `RepaymentSchedule` rows using amortization engine if none exist.
12. Disbursement can be initiated two ways:
    - `PATCH /management/loans/<id>/status/` with `status=DISBURSED`, which calls `saccomanagement.loan_utils.initiate_loan_disbursement`.
    - `POST /payments/mpesa/b2c/disburse/`, `payments.views.B2CDisbursementView`.
13. Both create `Transaction(PENDING, LOAN_DISBURSEMENT)` and `MpesaTransaction(B2C)` then set loan `DISBURSEMENT_PENDING`.
14. Safaricom calls B2C callback; `payments.tasks.process_b2c_callback_task` locks row in `atomic`.
15. Success `_process_successful_b2c_callback` sets transaction `COMPLETED`, loan `ACTIVE`, `disbursed_amount=amount`, `disbursement_date=today`, `outstanding_balance=amount`; creates ledger `DEBIT/LOAN_DISBURSEMENT`; records platform fee; notifies member.
16. Failure `_process_failed_b2c_callback` sets transaction `FAILED`, loan back to `APPROVED`, and notifies member.

Ledger entries:

- No ledger on application, guarantor, review, or approval.
- Ledger on successful B2C callback: `LedgerEntry(DEBIT, LOAN_DISBURSEMENT, amount, balance_after=loan.outstanding_balance)`.

Inconsistency risks:

- `AdminLoanApprovalView` allows status `DISBURSED` as a command but helper sets `DISBURSEMENT_PENDING`; naming is misleading for frontend.
- Two disbursement initiation endpoints exist. Concurrent calls can create multiple B2C transactions for the same approved loan because neither path `select_for_update`s the loan before status change at the beginning.
- If B2C callback never arrives, loan remains `DISBURSEMENT_PENDING`, transaction `PENDING`; no local reconciliation task queries B2C status.
- `loan.mpesa_transaction` FK exists but neither B2C path updates it in the code read.
- Atomicity: callback success/failure is atomic. Initial Daraja call happens before DB transaction in `B2CDisbursementView`, so if DB write fails after M-Pesa accepts, the provider may have a disbursement request with no local transaction record.

## 3. Member repays a loan instalment

1. Member calls `POST /api/v1/payments/mpesa/stk-push/` with `purpose=LOAN_REPAYMENT`, `loan_id`, `instalment_number`, net `amount`.
2. `STKPushView._get_owned_loan` confirms loan belongs to user and SACCO.
3. Fee/gross calculated and STK sent.
4. Creates `Transaction(PENDING, LOAN_REPAYMENT)` and `MpesaTransaction` linked to loan and instalment number.
5. Callback success executes `_apply_loan_repayment`.
6. `_apply_loan_repayment` reduces `Loan.outstanding_balance = max(0, outstanding - amount)`.
7. If instalment number is present, it updates matching `RepaymentSchedule` to `PAID`, `paid_date=today`, `paid_amount=amount`.
8. Creates `LedgerEntry(CREDIT, LOAN_REPAYMENT, amount, balance_after=loan.outstanding_balance)`.

Partial/overpayment behavior:

- Partial payment: code still marks the instalment `PAID` even if `amount < RepaymentSchedule.amount`. This is incorrect for partials.
- Overpayment: loan balance is floored at zero, but excess is not allocated to next instalment, not credited to savings, and not tracked separately.
- Matching: exact `loan + instalment_number`, no amount/date matching.
- Overdue/NPL: `RepaymentSchedule.is_overdue` only treats `PENDING` and `OVERDUE` as overdue, not `PARTIAL`. Since partials are marked `PAID`, they disappear from arrears.

Inconsistency risks:

- High risk: partial repayment can clear schedule row falsely.
- High risk: overpayment money is not represented beyond one ledger row and reduced outstanding balance floor.
- Atomicity: callback task is atomic, but direct `LedgerEntry.objects.create` can raise and roll back the transaction. That is better than savings deposit path, which swallows ledger errors.

## 4. Member withdraws savings

CONFIRMED IN CODE: There is a `Transaction.TransactionType.WITHDRAWAL`, `LedgerEntry.Category.SAVING_WITHDRAWAL`, and `Saving.total_withdrawals`, but I did not find a savings withdrawal endpoint or task in `payments.views`, `services.views`, or `ledger.views`.

Conclusion: withdrawal is model-planned but not implemented as an API money flow. There are no eligibility checks, approval workflow, B2C payout, or ledger mutation for savings withdrawals in the code I read.

## 5. Dividend calculation and payout

1. SACCO admin creates declaration with `POST /services/dividends/declarations/` or duplicated management route. `DividendDeclarationSerializer.create` sets `sacco` from context and status `DRAFT`.
2. Admin calls calculate endpoint. `DividendCalculateView.post` calls `services.engines.dividend_calculator.calculate_dividends_for_declaration`.
3. Calculator locks declaration with `select_for_update`. It refuses `APPROVED` or `DISBURSED`; deletes existing payouts if recalculating DRAFT/CALCULATED.
4. It selects eligible `Saving` rows for the SACCO/savings type with `dividend_eligible=True`.
5. `calculate_average_balance` samples ledger balance at each month-end using `ledger.engines.balance_calculator.get_balance_at_date(saving.membership, date)`. CONFIRMED RISK: this uses the whole membership ledger, not the specific saving account, because ledger entries are only membership-scoped.
6. Dividend amount = average balance * declared rate / 100 * months/12, rounded half up.
7. Creates `DividendPayout(PENDING)` rows; declaration becomes `CALCULATED`.
8. Admin approves: `DividendApproveView.post` locks declaration, requires `CALCULATED`, sets `APPROVED` and `approved_by`.
9. Admin disburses: `DividendDisburseView.post` locks declaration and payout batches, creates ledger `CREDIT/DIVIDEND_PAYOUT`, increments `Saving.amount`, sets payout `PAID`, declaration `DISBURSED`.

Inconsistency risks:

- Average balance is membership-wide, not saving-specific, so BOSA/FOSA/share-capital dividends can be materially wrong.
- Ledger category used is `DIVIDEND_PAYOUT`; liquidity monitor cash-out categories include `DIVIDEND` but not `DIVIDEND_PAYOUT`, so liquidity may ignore dividend outflow/inflow semantics.
- Atomicity: calculate/approve/disburse are atomic. Disburse checks `create_ledger_entry` return and raises if `None`, so saving update rolls back if ledger creation fails.

## 6. Fee income

Confirmed fee paths:

- STK deposits/repayments: `payments.views.STKPushView.post` grosses up `fee_amount=2%`; callback success calls `billing.services.record_collected_fee`, creating `PlatformFee` and `PlatformRevenue`.
- Generic deposit: `DepositInitiateView` stores `fee_amount`, but generic callback is broken.
- Billing invoices: `billing.services.generate_monthly_sacco_invoice` sums `PlatformFee` for completed transactions.
- Registration fees: `accounts.Sacco.registration_fee`, `SaccoSettings.registration_fee`, `SaccoApplication.registration_fee_paid`, and `fee_transaction` exist, but I found no endpoint that charges or reconciles membership registration fees.
- Loan processing fees/penalties: ledger categories `FEE`/`PENALTY` and repayment `penalty_amount` exist, but I found no charging endpoint/task that records them.

Distinguishability:

- Platform fee is distinguishable in `PlatformFee` and `PlatformRevenue`, not in member `LedgerEntry` unless a `FEE` entry is created elsewhere. STK net amount is credited/repays principal; fee is not posted as member ledger fee.

## 7. Money leaving the system entirely

### Loan disbursement to M-Pesa

- Mechanism: Daraja B2C via `DarajaClient.initiate_b2c`, then B2C callback.
- Local status: loan `APPROVED -> DISBURSEMENT_PENDING -> ACTIVE` on success or back to `APPROVED` on failure.
- Failure: callback failure cleanly reopens loan for retry.
- Stuck state: if callback never arrives, loan remains `DISBURSEMENT_PENDING` indefinitely.

### Withdrawal payout

- Not implemented.

### Dividend payout via B2C

- Not implemented as external payout. Dividends are credited internally to savings only.

---

# PART 4 - WHAT'S UNFINISHED, WEAK, OR RISKY

## Stubs, TODOs, mocks, placeholders

- `accounts/integrations/oauth.py`: CONFIRMED STUB; production `exchange_code_for_token` and `get_user_info` raise `NotImplementedError`.
- `accounts/storage.py`: TODO says replace local KYC storage with S3 backend for production.
- `services/engines/loan_limits.py`: contains a large “REVIEW - READ THIS THEN DELETE” comment block. This should not ship.
- `saccomanagement/import_utils.py`: TODO says wrap function in Celery task for large imports.
- `payments/providers/mock.py`: mock PSP selected automatically when `DEBUG=True` and `PAYMENT_PROVIDER` empty.
- `payments/providers/registry.py`: includes `flutterwave` provider path with no provider file found.
- `dashboard/models.py` and `health/models.py`: scaffold-only models.
- `METROPOL_*`, `PAYMENT_PROVIDER`, `CELLULANT_*`, `INTASEND_*` settings are referenced but not all defined in `config/settings/base.py`.

## Security, permissions, and SACCO scoping

- `payments.views.DepositRequestSerializer.sacco = Sacco.objects.all()` lets a user initiate generic deposit for any SACCO ID; no membership check. This is a tenant correctness issue.
- `payments.views.STKStatusView.get` fetches by `checkout_request_id` before user check. It returns 403 for wrong user but existence timing is still observable.
- `payments.views.CallbackCreateView` is `AllowAny` and creates callbacks for non-M-Pesa providers without signature verification. It also does not queue processing.
- `services.views.SavingsTypeViewSet` uses `IsAdminUser` for write, not SACCO admin scoping. Django staff could create products for any SACCO.
- `services.views.GuarantorSearchView._find_guarantor_user` searches phone with `icontains` globally before confirming same-SACCO membership. It does return 404 unless same-SACCO membership exists, but partial phone matching is privacy-sensitive.
- `billing.views.MonthlyInvoiceResendView` and `MonthlyInvoiceDownloadView` call `self.check_object_permissions(request, invoice)` but `IsSaccoAdminOrSuperAdmin` must implement object checks for this to work. I read `accounts.permissions.py` enough to confirm permission classes exist, but object-level behavior should be retested.
- `saccomanagement.mixins.SaccoScopedMixin._set_sacco_context` returns `None` for non-admin users; enforcement relies on `get_sacco_queryset` later raising. Views using the mixin but not calling `get_sacco_queryset` could accidentally skip scoping.
- `accounts.permissions.IsEligibleGuarantor.has_object_permission` imports `Savings` and `LoanGuarantor` from `payments.models`, but those models do not exist in the `payments/models.py` file I read. The code catches `ImportError` and skips checks, so the stated savings and duplicate-guarantor protections are not actually enforced by that permission. CONFIRMED IN CODE.
- `accounts.permissions.GuarantorCapacityCheck.has_object_permission` imports `GuaranteeCapacity` from `payments.models`, but `GuaranteeCapacity` is in `services.models`. The import fails and returns `False`. CONFIRMED IN CODE. There is a separate working `services.permissions.GuarantorCapacityCheck` used by `services.views.GuarantorRespondView`; the duplicate class name is a maintenance risk.
- `accounts.role_utils.get_sacco_admin_id` calls `get_user_sacco_context(user).get('sacco_id')`. This is safe only because `get_user_sacco_context` currently returns a dict. If that helper changes to return an object, profile serialization breaks; this is low-risk but brittle coupling.
- `saccomanagement.sasra_reports.SASRAReturnView.get` does not use `SaccoScopedMixin`; it manually requires `X-Sacco-ID` and checks `Role.SACCO_ADMIN`. This is functionally scoped for SACCO admins but inconsistent with the rest of admin routes.

## Financial correctness

- `payments.tasks._apply_saving_deposit`: saving balance is updated before `create_ledger_entry`; if ledger creation fails, helper swallows exception and returns `None`, leaving saving credited without ledger.
- `payments.tasks._apply_loan_repayment`: any repayment amount marks instalment `PAID`; no `PARTIAL` logic; no overpayment allocation.
- `payments.tasks._process_successful_callback`: logs amount mismatch but still credits net amount even if callback amount does not match expected gross.
- `payments.tasks._process_successful_b2c_callback`: records platform fee on loan disbursement. That may be intended as platform fee on outflow, but it means the member receives full amount while SACCO/platform revenue records a fee not collected through gross-up.
- `services.engines.dividend_calculator.calculate_average_balance`: uses membership ledger balance, not saving-account-specific balance. This can calculate wrong dividends by product.
- `ledger.utils.create_ledger_entry`: running balance is calculated by summing existing entries without row locking. Simultaneous ledger writes for the same membership can compute the same `balance_before` and incorrect `balance_after`.
- `payments.views.B2CDisbursementView.post`: Daraja API call happens before DB transaction; if local DB save fails after provider acceptance, money may leave with no local record.
- `saccomanagement.loan_utils.initiate_loan_disbursement` and `payments.views.B2CDisbursementView` duplicate disbursement initiation logic, increasing divergence risk.

## Decimal / float / rounding

- Money model fields generally use `DecimalField`; calculations use `Decimal` in core engines.
- `saccomanagement.serializers.AdminMemberDetailSerializer.get_repayment_rate_pct` returns Python float from `round((paid / total) * 100, 2)`. It is a percentage display, not money.
- `services.engines.dividend_calculator`, `payments.views.STKPushView`, `billing.services` explicitly quantize money. Good.
- `ledger.utils.create_ledger_entry` does not quantize incoming amount; relies on caller/model DecimalField.

## Celery and external API failure handling

- `payments.tasks.process_stk_callback_task` and `process_b2c_callback_task` are not bound and do not retry. A transient DB/ledger error after M-Pesa callback can fail the task while callback endpoint has already returned success to Safaricom.
- `MPesaSTKCallbackView.post` returns success even when enqueueing task fails. This prevents Safaricom retry and can lose a confirmed payment.
- `B2CCallbackView.post` also returns success if task enqueue fails.
- `billing.tasks.generate_and_send_monthly_fee_reports` has no per-SACCO isolation; one invoice email failure can stop all remaining invoices.
- `saccomanagement.tasks.run_member_import_task` re-raises failures but has no retry configuration.
- `services.tasks.notify_guarantors_task` catches per-guarantor errors and continues; no retry for failed notifications.
- `notifications.tasks.*` are better: explicit retries with exponential countdown.

## Frontend-tamperable sensitive fields

- `payments.views.DepositInitiateView` trusts client-sent `gross_amount`, `net_amount`, and `platform_fee` as long as they add up. The server should compute fee.
- `payments.views.STKPushView` trusts `amount` for loan repayment and does not cap by instalment outstanding.
- `payments.views.B2CDisbursementView` trusts admin-sent `amount`; it only checks positive amount and loan approved, not `amount == loan.amount` or approved disbursable amount.
- `services.views.GuarantorRequestView` trusts applicant-proposed guarantee amount, with checks against loan amount and capacity.

## Multi-tenancy risks

- Generic PSP deposit is not membership-scoped.
- `GuaranteeCapacity` is one-to-one per user, not per user+SACCO. `services.engines.guarantor_logic.calculate_guarantee_capacity` aggregates savings across all SACCOs. A member’s savings in SACCO A can increase apparent guarantee capacity for a loan in SACCO B.
- `dashboard.engines.portfolio_builder` correctly aggregates across memberships, but `Transaction` itself has no SACCO FK, so SACCO inference depends on M-Pesa related saving/loan or user membership joins. This is ambiguous for generic PSP transactions.
- `billing.services.generate_monthly_sacco_invoice` filters `transaction__user__membership__sacco=sacco`, which can duplicate/misattribute transactions for users with multiple memberships because `Transaction` is user-scoped, not SACCO-scoped.

## Rate limiting / abuse protection

- OTP send/resend: `accounts.throttles.OTPSendThrottle`, plus global DRF anon/user throttles.
- Login: only global DRF throttles; no login-specific throttle confirmed.
- Password reset: global throttles; reset request does not use `OTPSendThrottle`.
- Loan application: global authenticated throttle only.
- Bulk SMS create/send: no explicit throttle; daily recipient sending limit exists in `SaccoSettings.sms_daily_limit`.
- Public external guarantor token response: no explicit throttle.
- M-Pesa callbacks: replay cache but no rate throttling.

## Test coverage gaps

Tests confirmed:

- `payments/tests.py`: Daraja client config/response tests only; no STK callback money-flow tests found.
- `services/tests/test_amortization.py`, `test_dividend_calculator.py`, `test_guarantor_logic.py`, `test_liquidity_monitor.py`, `test_npl_monitor.py`, reminders/loan limits/CRB tests exist.
- `ledger/tests/test_statement.py`: statement builder coverage.
- `saccomanagement/tests/*`: superadmin, SASRA, parsers, import, bulk SMS, audit, admin views.
- `accounts/tests/*`: OTP tasks, SACCO search/context, OAuth, biometric devices.
- `tests/test_e2e_member_journey.py`: end-to-end member journey exists.
- Specific money-flow test found: `tests/test_e2e_member_journey.py::MpesaFlowTest.test_stk_push_to_balance_update` exists and appears intended to cover STK-to-balance. I did not execute it, so I cannot confirm it passes.
- Specific dividend tests found: `services/tests/test_dividend_calculator.py::DividendDeclarationAPITests.test_disburse_dividends_creates_ledger_entries`, plus calculation/idempotency tests.
- Specific CRB/approval tests found: `services/tests/test_crb_integration.py::LoanApprovalCRBTests`.
- Specific import/Bulk SMS tests found: `saccomanagement/tests/test_import.py`, `saccomanagement/tests/test_bulk_sms.py`.

Gaps:

- No confirmed tests for idempotent M-Pesa STK double callback with ledger/saving balance assertions.
- No confirmed tests for callback task enqueue failure after Safaricom callback.
- No confirmed tests for partial/over loan repayment behavior.
- No confirmed tests for concurrent ledger writes/running balance.
- No confirmed tests for B2C duplicate disbursement initiation.
- No confirmed tests for generic PSP callback path; likely would catch `callback.payload` bug.
- No confirmed tests for multi-SACCO transaction attribution in billing invoices.

## Prioritized Punch-List

1. **M-Pesa callback enqueue failure can lose money events.** Fix `MPesaSTKCallbackView`/`B2CCallbackView` to persist callback payload before ack or reliably process synchronously/transactionally with retryable queue handoff.
2. **Saving deposit can credit `Saving` without ledger.** Make ledger creation non-swallowing for money flows or check return and raise inside the atomic callback.
3. **Loan repayment partial/overpayment logic is wrong.** Allocate repayments across schedules; use `PARTIAL`; do not mark instalment `PAID` unless paid amount reaches due amount; handle overpayment explicitly.
4. **Duplicate B2C disbursement risk.** Lock loan row and enforce one pending/completed disbursement per loan before calling Daraja.
5. **B2C provider call before local record can orphan disbursement.** Create local pending record first or use an outbox pattern before external call.
6. **Generic PSP callback path is broken.** Replace `callback.payload` with `raw_payload`, link transactions to SACCO/membership/saving, and add tests.
7. **Generic deposit trusts client fee math and lacks membership scoping.** Server-compute fees and require/link an owned saving or SACCO membership.
8. **Dividend calculation uses membership-wide ledger for product-specific dividends.** Add saving/product dimension to ledger or calculate from saving-specific transaction history.
9. **Guarantee capacity aggregates across SACCOs.** Make capacity per SACCO or calculate with SACCO filter in all guarantor paths.
10. **Pending M-Pesa transactions can get stuck forever.** Add Daraja STK/B2C reconciliation tasks and admin/member stale-pending visibility.
11. **Transaction lacks SACCO FK.** Add explicit SACCO/membership/payment-purpose relationships to prevent billing/reporting leakage across multi-SACCO users.
12. **Metropol and PSP settings are referenced but not fully declared.** Add settings defaults/validation and deployment checks.
13. **Flutterwave registry points to missing code.** Remove provider option or implement it.
14. **Google OAuth is production stub.** Implement or disable route in production.
15. **Registration fees, withdrawal payouts, penalties, and loan processing fees are model-only/incomplete.** Decide scope before production; hide frontend routes or implement full ledger/payment flows.
16. **Ledger running balance is race-prone.** Lock membership ledger stream or compute balance from authoritative account balances with transaction isolation.
17. **Billing invoice attribution can miscount multi-SACCO users.** Base invoices on transaction SACCO, not `transaction.user.membership`.
18. **CallbackCreateView is public and under-secured for non-M-Pesa providers.** Require provider verification or remove public generic callback creation.
19. **Duplicate/broken permission classes will mislead future fixes.** Remove stale `accounts.permissions.IsEligibleGuarantor`/`GuarantorCapacityCheck` references or update them to current `services.models`.
20. **Sensitive endpoints need tighter throttles.** Add specific throttles for login, password reset, external guarantor response, loan application, and bulk SMS.
21. **Cleanup temporary comments and TODOs.** Remove `loan_limits.py` review block and resolve production storage/import TODOs.
