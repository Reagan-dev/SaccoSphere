# SaccoSphere Pre-Production Backend Audit (v2)

Audit date: 2026-09-07
Commit audited: `c36509d4` (working tree clean)
Scope: `accounts`, `saccomanagement`, `saccomembership`, `services`, `guarantor`, `payments`, `billing`, `ledger`, `notifications`, `dashboard`, `health`, `config/*`, Celery config, all `integrations/`/`engines/` subpackages, deployment manifests (`Procfile`, `render.yaml`, `build.sh`, `docker-compose.test.yml`), `requirements.txt`, and the test suites.

This replaces an earlier audit report that was deleted for being stale. It was produced independently by five parallel deep-read passes (one per subsystem group), each instructed to actively hunt for bugs rather than describe structure, cite exact `file:line` evidence, and label every claim **CONFIRMED IN CODE** (the exact lines were opened and read), **CONFIRMED BY TEST RUN** (reproduced by executing the project's own test suite), or **INFERRED** (plausible from structure but not fully traced). Findings were then cross-checked against each other for consistency and merged where two passes independently found the same root cause from different angles (noted inline).

**Read this first if you only read one section:** two of the platform's core compliance/data-rights endpoints (KYC document upload, self-service data erasure) return HTTP 500 on the majority of real invocations, confirmed by running the project's own tests, not just by reading code. See Critical §1–3.

---

## Part 1 — System Map (condensed)

| App | Responsibility | Tenant model |
|---|---|---|
| `accounts` | Custom `User` (email login, UUID PK), `Sacco` tenant + `SaccoSettings`/`SaccoPaymentConfig` (Fernet-encrypted M-Pesa credentials per tenant), KYC/IPRS, OTP, Google OAuth, biometric device bookkeeping, DPA consent (`UserConsent`), data erasure (`DataErasureRequest`) | Owns the tenant row (`Sacco`) |
| `saccomanagement` | Admin/orchestration layer: RBAC (`Role`, role-tier-based — no per-resource permission model), `SystemAuditLog`, ODPC access log (`DataConsentLog`), `SaccoContextMiddleware` + `BillingSuspensionMiddleware`, application/KYC/loan review, SASRA reports, bulk SMS, member import, super-admin dashboards | Imports nearly everything |
| `saccomembership` | `Membership` (user↔SACCO), dynamic application forms, `SaccoApplication` onboarding, document uploads | FK to `Sacco` |
| `services` | Domain core: `SavingsType`/`Saving`, `LoanType`/`Loan` (dual status + `DisbursementStatus` state machines), `RepaymentSchedule`, guarantors, CRB checks, liquidity/NPL monitoring, dividends; houses the amortization/dividend/liquidity/NPL/guarantor-capacity engines | FK to `Sacco` via `Membership` |
| `guarantor` | External (non-member) guarantor workflow via SMS + signed token, no login | FK to `Sacco` |
| `payments` | `Transaction`/`Callback`/`MpesaTransaction`, M-Pesa Daraja integration (STK + B2C), fee calculator, disbursements, withdrawals, reconciliation. **The generic multi-PSP abstraction described in earlier documentation no longer exists** — `payments/providers/` is gone; the codebase has fully consolidated on M-Pesa Daraja, and the old generic-PSP endpoints are now inert `410 Gone` stubs. | `Transaction.sacco` FK exists directly |
| `billing` | Platform SaaS billing: append-only `InvoiceLineItem` → monthly `Invoice` → overdue → suspension (`Sacco.is_billing_suspended`) | FK to `Sacco` |
| `ledger` | Single `LedgerEntry` model, source of truth for member balances; statement/PDF generation | via `Membership` |
| `notifications` | In-app notifications + async SMS/push(FCM)/email, bulk SMS campaigns | — |
| `dashboard` | No models; pure read-aggregation over other apps, cached 60–120s per user | — |
| `health` | Liveness/readiness probes | — |

Multi-tenancy is shared-database, row-level, enforced in application code: `SaccoContextMiddleware` sets `request.current_sacco` from an `X-Sacco-ID` header validated against the caller's `Role` rows; `SaccoScopedMixin` is the reusable per-view enforcement pattern.

---

## Part 2 — Money Flow Traces

### A. STK Push Deposit / Loan Repayment

1. Member calls `POST /api/v1/payments/mpesa/stk-push/` (`STKPushView.post`). `STKPushRequestSerializer` validates phone, amount bounds (10–300,000 KES), purpose, and required saving/loan id.
2. View resolves the target `Saving`/`Loan` scoped to the **caller's own membership** (`_get_owned_saving`/`_get_owned_loan`) — cross-member/cross-SACCO requests 404.
3. `SaccoInvoiceFeeCalculator` computes `gross_amount = net_amount + platform_fee` server-side from the client-supplied net amount; the client never supplies gross/fee directly.
4. SACCO's `payment_ready` + `payment_config.is_active` are checked; rejected if Daraja onboarding is incomplete.
5. `_get_existing_stk_attempt` checks for a recent (2-minute window) non-terminal `MpesaTransaction` matching user/amount/phone/target — returns the existing attempt instead of creating a duplicate.
6. Local `Transaction(PENDING)` + `MpesaTransaction` rows are created and **committed before** any external call.
7. `DarajaClient.initiate_stk_push` is called. On `DarajaError`, marked `INITIATION_FAILED`; on client-side timeout specifically, the transaction is left in an explicit "status unknown, will be reconciled" state and the client gets `202`.
8. On success, `_mark_stk_initiation_accepted` locks and stores `checkout_request_id`/`merchant_request_id`.
9. Safaricom calls back `POST /api/v1/payments/callback/mpesa/stk/{token}/`. Gate order: URL token → JSON parse → `CheckoutRequestID` present → `is_safaricom_ip` (IP allowlist) → `is_replay_attack` (24h cache marker, **set here**) → `verify_mpesa_signature` (a documented no-op for genuine M-Pesa traffic) → local lookup.
10. A durable `Callback` row (`raw_payload`) is persisted **before** `process_stk_callback_task.delay()` is enqueued. Broker-connection failures clear the replay marker and return a retryable response; other exceptions do not (see High §4).
11. The Celery task locks the `MpesaTransaction` row (`select_for_update`), re-checks idempotency via both `_callback_already_processed` (status-based) and a separate `MpesaIdempotencyRecord` (record-based) — two independent guards.
12. On `ResultCode == 0`, callback amount is compared to expected gross (tolerance 1 cent); on mismatch, `Transaction.AMOUNT_MISMATCH` and admin notification — **no crediting occurs** on a mismatch.
13. `_apply_saving_deposit`/`_apply_loan_repayment` run inside nested atomic blocks with `select_for_update()`, update the balance, and call `ledger.utils.create_ledger_entry` (itself lock-protected). Failure here rolls back the whole callback transaction and the task retries.
14. `_record_platform_fee_for_sacco` writes an idempotent, SACCO-scoped `InvoiceLineItem`.
15. **Safety net**: every 5 minutes, `reconcile_stale_mpesa_transactions` finds stuck STK transactions and actively queries Daraja's `query_stk_status`, driving them through the same locked/idempotent path — this is real and working (a prior draft of this audit had claimed no such reconciliation existed; that claim is refuted).

### B. B2C Loan Disbursement / Savings Withdrawal

1. Two independent triggers converge on the same `initiate_b2c_loan_disbursement`/`initiate_savings_withdrawal` functions with **no shared lock between them** — see Critical §8.
2. Loan/saving status, guarantor completeness, SACCO `payment_ready` are validated against **unlocked** reads.
3. Fee computed server-side (tiered flat fee for outflows); withdrawal balance sufficiency checked against an **unlocked** `Saving.amount` read.
4. Local `Transaction(PENDING)` + `MpesaTransaction(B2C)` created and committed before the outbound Daraja call.
5. `DarajaClient.initiate_b2c` called. Timeouts are treated identically to hard failures (no "unknown, will reconcile" state, unlike the STK path) — see Critical §11.
6. On success: `Transaction.SENT`, `conversation_id` stored, and — for disbursements — `disbursement_status=INITIATED`/`status=DISBURSEMENT_PENDING`; for withdrawals, `saving.amount` is debited **only now**, still unlocked.
7. Safaricom calls back `POST /api/v1/payments/callback/mpesa/b2c/{token}/` — same gate order as STK, same replay-marker edge case, but **with no B2C-specific reconciliation fallback** (Daraja has no B2C status-query API).
8. Routing splits on whether `related_loan.disbursement_transaction_id` is already populated: the "real" path (`services.tasks.on_disbursement_b2c_callback`) sets `disbursement_status=DISBURSED`, writes a `DisbursementAuditLog`, records the invoice line item, and triggers member SMS confirmation. A narrower fallback path (`payments.tasks._process_successful_b2c_callback`) skips all four of those for loan disbursements if the race window in step 6 is hit — see High §12.
9. For confirmed disbursements, the member is SMS'd to confirm/dispute receipt; unanswered after 24h auto-escalates to human review via Sentry — a genuine, thoughtful compensating control for lost B2C callbacks specifically (does not cover the initiation-timeout case in Critical §11).
10. On failure, the loan reverts to `APPROVED`/`disbursement_status=FAILED`, or the withdrawal balance is correctly restored under `select_for_update`.

---

## Part 3 — Findings

Severity reflects real-world blast radius: CRITICAL = production-breaking, real money lost/misrepresented, or a compliance obligation silently unmet. HIGH = a plausible, damaging failure mode with no live safety net. MEDIUM = a real defect with a narrower trigger window or partial mitigation. LOW/INFO = hygiene, dead code, or confirmed-non-exploitable footguns.

### CRITICAL

**1. KYC document upload endpoint returns HTTP 500 on every request — CONFIRMED BY TEST RUN** (`accounts`)
`accounts/views.py:408-411` sets `throttle_classes` to a list of **dotted-path strings**, not classes:
```python
throttle_classes = [
    'accounts.throttles.KYCUploadUserThrottle',
    'accounts.throttles.KYCUploadIPThrottle',
]
```
DRF's `get_throttles()` does `[throttle() for throttle in self.throttle_classes]`, which calls a `str` and raises `TypeError: 'str' object is not callable`. Running `KYCUploadViewTestCase.test_authenticated_user_can_upload_single_side` reproduces the exact traceback; `config/exception_handler.py` turns it into a generic 500. This means `POST /api/v1/accounts/kyc/upload/` — the core KYC document flow — is completely non-functional regardless of file validity, and this is masked by the project's own test asserting `response.status_code in [200, 500]` (`accounts/tests/test_kyc_views.py:66-67`, with a comment acknowledging the 500 is expected "if dependencies missing"). Every other view in the codebase using `throttle_classes` uses real class references — this is an isolated typo on the single most compliance-critical endpoint in the KYC pipeline.

**2. Self-service data erasure request 500s for the common case — CONFIRMED BY TEST RUN** (`accounts`)
`DataErasureRequestView.post` (`accounts/views.py:1482-1527`) only updates `erasure_request.status` inside `if kyc:` — there is no `else`. Any user who hasn't uploaded KYC yet (true of every fresh registrant; `RegisterView` never creates a `KYCVerification` row) hits `kyc is None`, the status stays at its earlier-set `'APPROVED'` value, and the final `return Response(..., status=200 if status==COMPLETED else 500)` yields **500** for a request that logically succeeded. Running `test_data_erasure.py` in full shows 4/16 failures, all `AssertionError: 500 != 2xx`, e.g. `test_user_can_submit_erasure_request`. The right-to-erasure endpoint (Kenya DPA 2019) is broken for the majority of users at the point they'd realistically invoke it.

**3. The erasure path that does work never erases KYC documents** (`accounts`)
The only reliable completion path — `DataErasureReviewView.post` staff approval (`accounts/views.py:1530-1684`) — calls `_anonymize_user` (lines 1651-1683), which touches only `User.first_name/last_name/email/phone_number/is_active` and revokes JWT tokens. It never references `KYCVerification`, `anonymize_kyc_record`, or `check_kyc_erasure_holds` — those symbols only exist in the *broken* self-service branch (Finding #2). A hold on a user's KYC data is also never re-checked before staff approve anonymization. Net effect: an approved "erasure" leaves government ID scans (`id_front`, `id_back`, `passport`, `huduma`) and the raw `id_number` in storage/DB permanently, unconsulted by any hold logic.

**4. `FIELD_ENCRYPTION_KEY` is used to encrypt every SACCO's M-Pesa credentials but is defined nowhere** (`config`)
`accounts/models.py`'s `EncryptedFieldMixin._get_fernet()` reads `getattr(settings, 'FIELD_ENCRYPTION_KEY', None)` and raises `ValueError` if unset — it encrypts `SaccoPaymentConfig.stk_passkey`, `daraja_consumer_secret`, `b2c_security_credential`. Grep of `base.py`/`development.py`/`production.py`/`.env.example`/`render.yaml` for `FIELD_ENCRYPTION_KEY` returns **zero matches**; it exists only in a developer's untracked local `.env`. Unlike `AT_API_KEY`/`OAUTH_MOCK`, there is no `AppConfig.ready()` fail-fast guard for this. A real production deploy following `render.yaml` (which otherwise enumerates every required env var) has no entry for it — the first attempt to save or read any SACCO's M-Pesa credentials in production raises `ValueError` and breaks payment onboarding outright.

**5. Three ledger-write call sites bypass the race-safe `create_ledger_entry()` helper, writing a semantically wrong `balance_after`** (`payments`, `saccomanagement`, `ledger`)
`ledger/utils.py::create_ledger_entry` is correctly implemented — `select_for_update()` over the membership's existing `LedgerEntry` rows inside `atomic()`, computing `balance_after` from the locked set. But three sites bypass it via direct `LedgerEntry.objects.create()`:
- `payments/tasks.py` `_apply_loan_repayment` — sets `balance_after=loan.outstanding_balance` (the *loan's* balance, not the ledger's)
- `payments/tasks.py` `_create_loan_disbursement_ledger` — same substitution
- `saccomanagement/data_imports/bulk_operations.py` bulk member import — sets `balance_after=saving.amount`

Every loan-repayment/disbursement line and every bulk-imported deposit line on a member's statement shows a `balance_after` figure that has nothing to do with their actual cumulative ledger balance (e.g. a KES 500 repayment line displaying "balance_after: KES 48,000" — the loan's outstanding balance). Because these writes don't compute a ledger-relative balance at all, they also aren't serialized against a concurrent `create_ledger_entry()` call for the same membership (e.g. a simultaneous savings deposit) — two ledger rows can be written concurrently for one membership with neither correctly reflecting the other. This directly feeds the PDF statement (`pdf_generator.py`) used for member/SASRA reporting.

**6. Dividend engine silently under-counts (or entirely zeroes) any savings balance not sourced from an M-Pesa STK transaction, including its own prior payouts** (`services`)
`services/engines/dividend_calculator.py::_get_saving_balance_at_date` computes a saving's balance-at-date exclusively via `LedgerEntry.objects.filter(..., transaction__mpesa__related_saving=saving)` — requiring a `LedgerEntry.transaction` FK set to a `Transaction` with a linked `MpesaTransaction.related_saving` pointing at that exact saving. Two confirmed producers of `LedgerEntry` never satisfy this:
- Bulk member-import opening balances (`bulk_operations.py`) — no `transaction=` argument
- Dividend payout credits themselves (`services/views.py` `DividendDisburseView`) — no `transaction=` argument

A SACCO onboarding thousands of existing members via CSV import with real historical balances gets **zero or drastically understated dividends** for every one of them for any period before their first M-Pesa transaction — even though `Saving.amount` (used everywhere else: loan limits, guarantor capacity, statements) correctly shows the full balance. Worse: once any member is paid a dividend, that credit becomes permanently invisible to the *next* year's calculation too, so multi-year dividend compounding silently under-pays every member forever. (This refutes a hypothesis in this audit's own investigation brief that dividends leak across products via a whole-membership balance — that specific mechanism was checked and is not present; the actual bug is narrower-but-worse: real under-payment of real money.)

**7. Guarantee-capacity check-then-commit race lets a guarantor exceed 50% capacity via concurrent approvals** (`services`)
`GuarantorRespondView.post` reads the `Guarantor` row with no `select_for_update`, then reads `GuaranteeCapacity.available_capacity` with no lock, compares against `guarantee_amount`, and only afterward flips status and recalculates capacity. Two concurrent approval requests for the same guarantor against two different loans both read the same pre-commit `available_capacity`, both pass the check, and both commit — a guarantor with KES 20,000 real capacity can end up guaranteeing KES 30,000+ across simultaneously-processed requests. `services/permissions.py::GuarantorCapacityCheck` has the identical unlocked-read pattern.

**8. B2C disbursement can be double-initiated — a real double payout** *(independently found by both the services/guarantor pass and the payments/billing pass, from opposite ends of the same call graph — merged here)* (`services`, `payments`, `saccomanagement`)
`initiate_b2c_loan_disbursement` (`payments/disbursements.py`) validates `loan.status`/`disbursement_status` against an **unlocked** `Loan` object; `disbursement_status` isn't flipped to `INITIATED` until *after* the Daraja HTTP call has already gone out. Two independent trigger points reach this function with no shared lock: `B2CDisbursementView.post` (`payments/views.py`) and the loan-status-PATCH path (`saccomanagement/admin_views.py::AdminLoanApprovalView` → `saccomanagement/loan_utils.py`), neither of which acquires `select_for_update()` on the loan before calling in. An admin double-clicking "Disburse," or a slow-network frontend retry, or the two separate endpoints both being hit within the same few hundred milliseconds, can both pass validation and both successfully call Safaricom — the member receives the loan amount **twice**, with only one of the two resulting transactions ever linked from `loan.disbursement_transaction`. By contrast, the *confirmation* side of the same feature (`ConfirmDisbursementView`, `DisputeDisbursementView`, `on_disbursement_b2c_callback`, `auto_resolve_disbursement`) all correctly lock the loan — the locking discipline is inconsistent within one feature.

**9. Savings withdrawal has the identical race — double withdrawal / possible negative balance** (`payments`)
The sufficient-balance check and the later `saving.amount -= gross_amount` debit in `payments/withdrawals.py` both operate on an **unlocked** `Saving` instance (no `select_for_update` anywhere in `SavingsWithdrawalView` → `initiate_savings_withdrawal`), and `services.models.Saving` has no DB-level `CheckConstraint(amount__gte=0)` to backstop it. Two concurrent withdrawal requests just under the real balance both read the same starting amount, both pass, and both debit independently — the account can end up over-withdrawn or negative with no application- or DB-level guard.

**10. Guarantee capacity is computed globally across all of a member's SACCOs — cross-tenant leakage** (`services`)
`services/engines/guarantor_logic.py::calculate_guarantee_capacity` (docstring: *"across all their SACCOs"*) sums `Saving` and `Guarantor` rows with **no SACCO filter at all**, and `GuaranteeCapacity` is a single `OneToOneField(user)` row — there is no per-SACCO capacity record. A member with KES 100,000 saved at SACCO A and KES 0 at SACCO B is reported as having KES 50,000 of capacity available to guarantee a loan at SACCO B. If the borrower defaults, SACCO B has no operational or legal claim on savings held at a different tenant — its risk exposure is effectively unsecured despite the system reporting it as guaranteed. (A second, independent implementation of this same capacity number also exists — see High §13 — making the figure non-deterministic on top of being wrongly scoped.)

**11. B2C initiation timeout is treated as a definitive failure, and unlike STK, has no reconciliation safety net** (`payments`)
`STKPushView` special-cases a client-side timeout as "status unknown, will be reconciled" and returns `202`. Both B2C call sites (`disbursements.py`, `withdrawals.py`) catch `DarajaError` uniformly regardless of cause and unconditionally mark the attempt `FAILED` / revert the balance. Compounding this, `reconcile_stale_mpesa_transactions` filters `transaction_type=STK_PUSH` only — **there is no B2C equivalent**, and the existing 24-hour member-confirmation escalation (`auto_resolve_disbursement`) only covers loans that reached a `conversation_id`-tracked state, not a request that timed out before Safaricom's response was received locally. If the outbound HTTP call times out on SaccoSphere's side *after* Safaricom actually processed it, the loan is marked failed / balance reverted locally while the member's phone genuinely receives the money — and nothing in the system will ever detect or reconcile that discrepancy automatically.

**12. CRB checks silently run on fabricated mock data in production if one env var is forgotten** (`services`)
`METROPOL_MOCK` defaults to `True` in `config/settings/base.py` regardless of environment; `production.py` sets `DEBUG=False` but never overrides `METROPOL_MOCK`. `MetropolClient.mock = settings.DEBUG or settings.METROPOL_MOCK` — so a production deployment that simply forgets to set `METROPOL_MOCK=False` uses `_mock_response`, which derives a deterministic-but-fake score/negative-listing flag from an MD5 hash of the ID number (~20% chance of "listed_negative"). Loan approval (`saccomanagement/admin_views.py`) gates on this check and requires an override reason only if `listed_negative` is true — a genuinely blacklisted borrower has an 80% chance of sailing through with no override required, no error, and no visible indication the check wasn't real (only a `MOCK-CRB-` prefixed reference string nobody is shown to inspect).

**13. `reconcile_uncollected_fees` management command has a `NameError` typo that crashes it on exactly the workload it exists to process** (`billing`)
`billing/management/commands/reconcile_uncollected_fees.py`:
```python
sacco_breakdown[sacco_name] = sacco_breakdown.get(
    sacacco_name,   # undefined — correct variable is `sacco_name`
    {'count': 0, 'amount': Decimal('0.00')},
)
```
Raises `NameError` the first time any uncollected-fee record is found, in both dry-run and `--execute` modes. Operationally this is a HIGH-vs-CRITICAL judgment call — flagged here because it means the reconciliation tool is currently unusable for its one purpose, silently, until someone actually runs it.

### HIGH

**14. Generic exception handling on M-Pesa callbacks can permanently strand a callback behind its own replay-attack marker** (`payments`)
`is_replay_attack()` sets a 24h "seen" cache marker for a `checkout_request_id`/`conversation_id` **before** any of signature-check/`Callback` persistence/task-enqueue has actually succeeded. The marker is only explicitly cleared on two narrow branches (`TRANSACTION_NOT_FOUND`, broker-connection errors during enqueue). Any other exception — a transient DB error on `Callback.objects.create`, a bug elsewhere in the handler — falls through to the generic `except Exception` branch, which returns a "please retry" 503 **without clearing the marker**. Safaricom's retry then gets silently treated as a replay and dropped — no `Callback` row, no processing — with the transaction permanently stuck unless the (STK-only) reconciliation sweep happens to catch it. No equivalent safety net exists for B2C (see Critical §11).

**15. `MPESA_CALLBACK_TOKEN` silently no-ops when unset** (`payments`, `config`)
`MPESA_CALLBACK_TOKEN` defaults to `''`; the check is `if expected_token and callback_token != expected_token:` — if unset, the check is skipped entirely and any token value in the callback URL path is accepted. Combined with `verify_mpesa_signature` being a documented no-op for genuine M-Pesa traffic (no password/timestamp fields on real callbacks), the callback endpoints' real security perimeter reduces to the Safaricom IP allowlist plus the replay cache alone if this one env var is forgotten — with no operator-visible warning at deploy time.

**16. Legacy invoice generator can cross-bill a transaction to a SACCO the member didn't transact with** (`billing`)
`generate_monthly_sacco_invoice` (`billing/services.py`) filters `PlatformFee.objects.filter(transaction__user__membership__sacco=sacco, ...)` — joining through *any* of the user's memberships rather than the transaction's own direct `sacco` FK (`Transaction.sacco` exists and is correctly used by the *current*, scheduled invoicing pipeline). For a multi-SACCO member, a fee from a transaction in SACCO A will also match when this legacy function is run for SACCO B. Mitigating factor: this function is not on the automatic Celery beat schedule (only the correct `InvoiceLineItem`-based path is) — but it remains callable via management command/manual dispatch and would produce a real cross-tenant billing error if invoked.

**17. B2C success can silently skip the platform-fee invoice line and the member-confirmation SMS** (`payments`, `services`)
`B2CCallbackView.post` routes a successful loan-disbursement callback based on whether `related_loan.disbursement_transaction_id` is already populated — which is only set in a *second*, separate atomic block **after** the Daraja call returns. If that second commit fails for any reason after Daraja already succeeded, every subsequent callback for that disbursement (including from reconciliation) permanently falls through to the generic `_process_successful_b2c_callback` path, which marks the loan `ACTIVE` but never sets `disbursement_status`, never writes a `DisbursementAuditLog`, never creates the fee `InvoiceLineItem`, and never sends the member the "did you receive it" confirmation SMS.

**18. `require_consent` is fully implemented and completely unused** (`accounts`)
Exhaustive grep confirms `accounts.services.consent.require_consent` is imported only by its own test file — no view, task, or serializer in the entire codebase applies it. There is no code path anywhere that blocks an action because a user lacks or withdrew consent.

**19. Bulk marketing SMS has no consent check** (`saccomanagement`)
`BulkSMSCreateView.resolve_audience` (`saccomanagement/bulk_sms_views.py`) builds recipients purely from `Membership` filters — no reference to `UserConsent`, `has_active_consent`, or the `MARKETING` consent type that clearly exists in the model for exactly this purpose (Finding #18 compounds this: even if it did check, there's no enforcement path to reuse). SACCO admins can blast SMS to every approved member regardless of marketing-consent status or withdrawal.

**20. Five permission classes are implemented, unit-tested, and applied by zero views — no endpoint gates financial actions on KYC approval** (`accounts`)
`IsKYCVerified`, `IsPhoneVerified`, `IsMemberOfSacco`, `IsOwnerOrSaccoAdmin`, `IsEligibleGuarantor` (`accounts/permissions.py`) are each covered by `test_permissions.py` but referenced by zero `permission_classes` lists anywhere else in the codebase, and no equivalent inline KYC-approval gate exists in `services`/`saccomembership`/`guarantor` either. For a regulated financial platform, this means no endpoint currently requires KYC approval before a member can take loan/savings/guarantor actions.

**21. Password-reset OTP bypasses the phone-keyed rate limit — SMS-bombing vector** (`accounts`)
`OTPSendView`/`OTPResendView` correctly use `OTPSendThrottle`, keyed by phone number (5/hour) specifically to prevent bulk SMS spam regardless of source IP. `PasswordResetRequestView` declares **no `throttle_classes`**, falling back to the global anonymous throttle (100/hour *per IP*). An attacker rotating source IPs can trigger real SMS sends to an arbitrary victim phone number at far higher volume than the dedicated OTP throttle was built to prevent — both a harassment vector and an SMS-gateway cost-abuse vector.

**22. Login has no dedicated brute-force protection** (`accounts`)
`LoginView` declares no `throttle_classes` and relies solely on the global 100/hour-per-IP anonymous throttle — no per-account throttle, no backoff, no lockout. A distributed attacker can attempt effectively unlimited password guesses against one specific account.

### MEDIUM

**23. `BillingSuspensionMiddleware` blocks writes across *all* of an admin's SACCOs if *any one* is suspended** *(independently found by two passes)* (`saccomanagement`)
`_should_block_request` runs `Role.objects.filter(user=request.user, name=SACCO_ADMIN, sacco__is_billing_suspended=True).exists()` — never scoped to the SACCO relevant to the current request (`request.current_sacco`, despite `SaccoContextMiddleware` running right after it in the middleware stack). An admin managing two SACCOs, one delinquent, is locked out of writes to the healthy one too.

**24. Billing suspension does not block member-initiated financial transactions** (`payments`, `saccomanagement`)
The middleware only fires for `SACCO_ADMIN`-role users. `STKPushView`/`SavingsWithdrawalView` are `IsAuthenticated`-only and member-facing — a regular member can continue depositing, repaying, and withdrawing at a billing-suspended SACCO. The SACCO keeps generating billable transaction volume (and unbilled fee liability) while only admin actions are frozen. (May be intentional scope per the middleware's own docstring — flagging as a design decision worth confirming, not an obvious bug.)

**25. `EXEMPT_PATHS` in the billing middleware references a URL that doesn't exist, and there is no self-service path for a suspended SACCO admin to pay their own invoice** (`saccomanagement`, `billing`)
`/api/v1/billing/pay/` is not registered anywhere in `billing/urls.py` — the exemption is dead. `InvoiceMarkPaidView` (the only endpoint that lifts a suspension) is superadmin-only. A suspended SACCO admin currently has no way to resolve their own suspension without a human platform operator, regardless of what the exemption list suggests.

**26. `create_ledger_entry`'s locking discipline isn't matched by concurrent-safety tests** (`config`, cross-cutting)
The one function that *does* implement correct `select_for_update()`-based locking has no test using real threads/`ThreadPoolExecutor` to prove the lock holds under genuine concurrency — every existing "duplicate delivery" test calls the task twice sequentially in the same thread, which proves idempotency but not race-safety. Grep for `Thread(`/`ThreadPoolExecutor`/`concurrent.futures` across `payments/`, `ledger/`, `billing/`, `services/` tests returns nothing relevant to money paths.

**27. Two divergent implementations of guarantee capacity write to the same singleton row** (`services`)
`GuarantorSearchView._update_guarantee_capacity` computes capacity **scoped to one SACCO**, counting **PENDING + APPROVED** guarantees. `guarantor_logic.calculate_guarantee_capacity` (used by the actual approve/decline flow and a `post_save` signal) computes capacity **globally**, counting **APPROVED only**. Both write to the same `OneToOneField(user)` `GuaranteeCapacity` row — whichever ran last wins, and the two numbers can differ significantly. (Compounds Critical §10.)

**28. Liquidity monitor and SASRA report never count actual dividend payouts as cash outflow — category name mismatch** (`services`)
`liquidity_monitor.CASH_OUT_CATEGORIES` includes `LedgerEntry.Category.DIVIDEND`, but the only code that ever creates a dividend-related ledger entry (`DividendDisburseView`) uses `DIVIDEND_PAYOUT` — a distinct enum value. `DIVIDEND` (without `_PAYOUT`) is never used to create an entry anywhere in the codebase; it's dead. The identical mismatch is duplicated in `saccomanagement/sasra_reports.py`. Liquidity utilisation is understated exactly when a large dividend disbursement has made the SACCO's real position most stressed.

**29. Loan-repayment overpayment beyond the full remaining schedule is silently dropped** (`payments`)
`_apply_loan_repayment` correctly allocates a partial/excess payment across instalments in order (this part is race-safe and correctly implemented — see Confirmed Working), but if `unapplied_amount > 0` after exhausting every instalment, the code only `logger.warning`s it — no refund, no savings credit, no ledger entry for the excess. Real M-Pesa-collected cash (e.g. a member accidentally overpaying their final instalment) becomes an unreconciled gap between actual settlement and the books.

**30. Liquidity/NPL Celery sweeps abort the entire batch on one bad record, unlike the guarantor-notification task in the same app** (`services`)
`check_all_sacco_liquidity` and `flag_npl_arrears` each wrap their *whole* per-SACCO/per-loan loop in one outer `try/except: retry`, so one malformed record blocks every other SACCO/loan in that run for up to 3 retries. `notify_guarantors_task` in the same file correctly isolates per-item failures with `try/except: continue` specifically to avoid this — the sweeps don't follow their own sibling's pattern.

**31. Statement ODPC access logging is skipped on cache hits** (`ledger`)
`StatementView.get` only calls `build_statement()` (which internally logs the `DataConsentLog` access) on a cache miss; the 300-second cache means repeated views of the same statement within that window are silently unlogged. `StatementPDFView` has no cache and logs every time — the two "same data" paths are inconsistent.

**32. `send_push_notification_task` re-sends to already-succeeded devices on any single-device retry** (`notifications`)
The task loops over all active device tokens and calls `client.send()` per token inside the loop; if any one token errors non-terminally, `self.retry()` aborts and re-queues the *whole* task, which re-sends to every device again on the next attempt — including ones that already received the push.

**33. Narrow non-idempotent window in bulk SMS campaign sending, and no locking on the daily-limit counter** (`notifications`)
If the SMS provider call succeeds but the subsequent `recipient.save(status=SENT)` raises, the recipient's status never persists as sent and a retry re-sends to them. Separately, `get_remaining_daily_sms_allowance` is a plain unlocked `count()` — two concurrent campaign runs for the same SACCO can each compute the same stale "remaining allowance" and jointly exceed `SaccoSettings.sms_daily_limit`.

**34. `MembershipLeaveView` doesn't block leaving with a `DEFAULTED` loan** (`saccomembership`)
The active-loan check whitelists `ACTIVE, APPROVED, DISBURSED` only — `Loan.Status` also includes `DEFAULTED` (genuinely delinquent debt) and `DISBURSEMENT_PENDING` (a mid-flight B2C payout), neither of which blocks leaving. A member with a defaulted loan can self-service their way out of the SACCO.

**35. Membership application validation checks `CLOSED` SACCOs but not `STAFF_ONLY`** (`saccomembership`)
`validate_sacco` only rejects `Sacco.MembershipType.CLOSED`; `STAFF_ONLY` passes through unchecked, so any authenticated user can apply to a staff-only SACCO at the API layer.

**36. Rejecting a membership application never updates the `Membership` row — permanent PENDING lock, no re-application path** (`saccomembership`, `saccomanagement`)
Application creation always creates a `Membership(PENDING)` alongside the `SaccoApplication`. The admin review endpoint only touches `Membership` on **approval**; rejection updates `SaccoApplication.status` only. No code path anywhere sets `Membership.status = REJECTED`. Since the apply-serializer blocks a new application whenever *any* `Membership` row already exists for that (user, sacco) pair regardless of status, a rejected applicant is permanently stuck: their dashboard forever reports "application under review," and re-applying is blocked with "you have already applied," with no self-service recovery.

**37. Document upload validation is filename-extension and size only — no content verification** (`saccomembership`)
`validate_membership_document` checks `Path(file.name).suffix` and byte size only; no magic-byte/content sniffing, no `Content-Type` check. Any file renamed to end in an allowed extension and under the size cap is accepted and later opened by SACCO admin reviewers. (Contrast: the KYC upload path in `accounts` does this correctly with `filetype` magic-byte detection — the membership-document path doesn't reuse that pattern.)

**38. KYC document-serving endpoint is stricter than the KYC-review permission meant to use it** (`accounts`)
`AdminKYCReviewView` grants review access to any `SACCO_ADMIN` (not just Django `is_staff`). `KYCDocumentServeView.get` (local-storage document serving) only allows `is_staff` or the document owner — a legitimate non-staff SACCO admin can review a KYC record but gets `PermissionDenied` actually viewing the underlying document image on non-S3 deployments. An under-permission bug that breaks the reviewer workflow, not a leak.

**39. Platform-wide member PII listing isn't routed through the ODPC access-logging mixin** (`saccomanagement`)
`AllMembersListView` returns member name/email/phone/KYC status across the *entire platform* but, unlike the per-SACCO equivalents, doesn't use `DataAccessMixin` — the broadest cross-tenant PII view is the one view not contributing to the ODPC audit trail.

**40. Google OAuth nonce replay-protection is opt-out by default and not enabled in production settings** (`accounts`)
`NONCE_REQUIRED` defaults to `False` and `production.py` never overrides it; when absent, `_validate_nonce` just logs a warning and proceeds. An easy-to-miss deployment step for a security control that's otherwise well implemented (constant-time comparison, atomic single-use cache marker).

### LOW / INFO

**41. Two divergent `SaccoContextMiddleware` implementations coexist; the dead one leaks PII to stdout, and a *live* middleware also prints unconditionally** *(cross-referenced by three passes — the definitive picture)* (`config`)
`saccomanagement/middleware.py::SaccoContextMiddleware` (the one actually wired into `MIDDLEWARE`) is clean. A second, near-identical copy in `config/middleware.py` is **not** registered (dead code) but is riddled with `print(f"...{user.email}...", flush=True)` calls that would log PII to stdout if anyone ever "fixed" an import to point at it by mistake (an easy error given the identical class name). Separately, `config/middleware.py::RequestCorrelationMiddleware` **is** registered and unconditionally does `print(f"[CORRELATION] Process request", flush=True)` on every single request in production, confirmed firing during test runs — no PII, but needless per-request I/O that bypasses the structured JSON logging pipeline entirely. Recommendation: delete the dead duplicate; replace the live print with proper `logger.debug`.

**42. Client-supplied `X-Correlation-ID` is echoed into the response header with no validation** (`config`)
`get_request_id` returns the raw header value with no length cap or charset check; Django must Latin-1-encode response headers, so a client sending non-Latin-1 characters (e.g. emoji) in this header causes an unhandled `UnicodeEncodeError` (500) on an otherwise-valid request — a free, unauthenticated single-request DoS/annoyance vector on every endpoint, since this middleware runs globally.

**43. `LedgerEntry` immutability is admin-only, not model- or DB-enforced** (`ledger`)
Django admin correctly denies add/change/delete, but no `save()`/`delete()` override or DB constraint exists — any other ORM-level code can mutate/delete existing rows (proven in-repo: `ledger/tests/test_statement.py` does exactly this to backdate fixtures). A soft, convention-only guarantee.

**44. `dashboard` and `notifications` apps have zero test coverage; several other money-adjacent gaps** (cross-cutting)
`dashboard/tests.py` and `notifications/tests.py` are empty stubs. No test anywhere uses real threads to prove `select_for_update` actually serializes concurrent writes (see Medium §26). `docs/IDEMPOTENCY_PATTERNS.md` and `docs/KYC_RETENTION_COMPLIANCE_ASSUMPTIONS.md` both cite file:line locations that have drifted significantly from current code (the underlying patterns they describe are still present and functionally accurate — only the specific line citations are stale).

**45. `reviewed_by`/`reviewed_at` are computed and passed but silently dropped on immediate-processed erasure requests** (`accounts`)
`DataErasureRequestSerializer.create()` pops `status`/`hold_reason`/`hold_until` but its explicit model-construction kwargs omit `reviewed_by`/`reviewed_at`, which the view computes and passes anyway. Related to Critical §2/§3.

**46. `SystemAuditLog` captures unredacted full-model snapshots** (`saccomanagement`)
`AuditMixin.perform_update` logs `dict(old_data)` (a raw `.values()` dict of the entire instance) with no field redaction — the audit table becomes a secondary, less access-controlled copy of whatever sensitive fields the audited model happens to carry.

**47. "Biometric" endpoints are pure device bookkeeping, not cryptographic authentication** (`accounts`)
`DeviceRegistrationView`/etc. only flip a `biometric_enabled` boolean on `UserDevice`; there is no challenge/signature/public-key verification anywhere server-side, and no login path trusts `device_id` as an auth factor (so this is not an auth bypass) — but the response copy ("biometric login is now enabled") overstates the server's actual involvement.

**48. `SavingsTypeViewSet` write access is `IsAdminUser` (Django staff), not SACCO-admin-scoped** (`services`) — *carried forward from prior review, re-confirmed structurally present*; Django staff can create/edit savings products for any SACCO, not just ones they administer.

**49. Dependency/deployment hygiene** (`config`)
- `requirements.txt` ships `Flask`/`Flask-Login`/`flask-cors`/`Werkzeug`/`gevent`/`python-socketio` etc. — all unused transitive dependencies of `locust` (confirmed zero `import flask` anywhere) — bundled into the *production* dependency set rather than a dev-only requirements file, needlessly widening the deployed attack surface.
- `pytest` is a dependency with **zero actual usage** (`import pytest`: 0 matches; no `pytest.ini`/`pyproject.toml`/`conftest.py`) — the project exclusively uses `django.test.TestCase`. Don't write new tests assuming pytest fixtures/plugins are wired up.
- WeasyPrint's required native system libraries (Pango/Cairo, documented in `README.md`) are **not** installed by `build.sh` — PDF generation (member statements, invoices) is very likely non-functional on a fresh Render deploy as currently configured.
- `celery.py` routes `ledger.tasks.*` to the `reports` queue, but `ledger/` has no `tasks.py` — the rule can never match; the `reports` queue is provisioned in `Procfile`/`render.yaml` but effectively unused.
- `config/settings/production.py` reads `CORS_ALLOWED_ORIGINS` via raw `os.environ.get("FRONTEND_URL", ...)`, bypassing `python-decouple` (used everywhere else) and a *different* env var name than `base.py`'s own `CORS_ALLOWED_ORIGINS` — confusing, and silently empty if the deployment relies on a `.env` file rather than injected process env vars.
- `METROPOL_MOCK`/`IPRS_MOCK` have no `AppConfig.ready()` fail-fast guard analogous to the one that already exists for `OAUTH_MOCK` (Critical §12 depends on this gap).
- README documents Railway as the deployment target; `render.yaml` is a fully separate, complete Render manifest; neither cross-references the other, and the README's own deployment checklist never mentions `FIELD_ENCRYPTION_KEY`, `ALLOWED_HOSTS`, or WeasyPrint's system-package requirement — a team deploying via Railway using only the README could miss all three.
- `render.yaml` contains ~80 lines of leftover "REVIEW — DELETE THIS" scratch comments, still committed.
- Several `requirements.txt` versions (e.g. `packaging==26.2`) look anomalous for their package's normal versioning scheme and are worth a manual PyPI check before trusting the file for a build (could not be verified offline in this audit).

---

## Confirmed Working Correctly

Not everything is broken — these were specifically checked and hold up:

- **STK deposit/repayment idempotency is real and layered**: durable webhook persistence before task enqueue, `select_for_update` row locking, *two* independent idempotency guards (status-based + a dedicated `MpesaIdempotencyRecord`), and amount-mismatch handling that refuses to credit on a mismatch rather than guessing. Reconciliation genuinely queries Daraja every 5 minutes for stuck STK transactions.
- **Server-side fee computation everywhere it matters**: `SaccoInvoiceFeeCalculator` is the sole source of gross/net/fee math for every live money-moving endpoint; no endpoint accepts a client-supplied fee/gross breakdown as authoritative.
- **Ownership/tenant scoping on the STK and B2C-admin paths**: `STKPushView`, `WithdrawalRequestSerializer`, and `B2CDisbursementView` all correctly verify the caller owns the target saving/loan and that the SACCO context header matches an actual admin role.
- **Loan repayment partial/overpayment allocation across instalments** (not the "beyond the whole schedule" edge case in Medium §29) is correctly implemented, race-safe, and does *not* mark a partial payment as fully `PAID` — a specific behavior a prior draft of this audit had gotten wrong.
- **Amortization engine**: strict `Decimal`/`ROUND_HALF_UP` throughout, final instalment correctly absorbs rounding remainder, schedule always exactly zeroes out.
- **Disbursement confirmation/dispute/auto-resolve loop**: correctly locked, idempotent against duplicate callbacks, and a genuinely thoughtful compensating control for M-Pesa's lack of a B2C status-query API (within its documented scope — see Critical §11 for the gap it doesn't cover).
- **External guarantor token security**: `secrets.token_urlsafe(48)`, expiry-checked, single-use, no IDOR/enumeration path found.
- **OTP secrecy and integrity**: HMAC-SHA256 storage (never plaintext), `hmac.compare_digest` timing-safe comparison, attempt caps, and a correctly race-safe token-creation path (`select_for_update` + a partial unique constraint + `IntegrityError` fallback).
- **KYC file upload validation logic itself** (independent of the throttle bug that currently 500s the endpoint) is genuinely solid: magic-byte detection, MIME allow-list, dimension bounds against decompression bombs, EXIF stripping.
- **IPRS integration fails closed**: unavailable/mismatch/rejected outcomes map to explicit non-approved statuses, never silent approval; correct transient-vs-permanent error classification with backoff.
- **Multi-tenant SACCO-context scoping** (`SaccoContextMiddleware`, `SaccoScopedMixin`, role views): consistently validates the `X-Sacco-ID` header against the caller's actual roles and fails closed rather than falling back to an unscoped queryset.
- **Bulk member import**: per-row savepoints with individual exception capture and a 5%-failure-rate circuit breaker — a solid isolate-then-abort pattern, layered correctly with Celery retry/backoff.
- **Production security defaults**: `SECRET_KEY` has no insecure default in `production.py` (raises if unset); `DEBUG` is hardcoded `False` there (can't be flipped by env misconfiguration); Sentry `send_default_pii=False` is hardcoded; HSTS/SSL-redirect/secure-cookies are on by default.
- **`.env` is correctly gitignored**; only `.env.example` is tracked.
- **`saccomanagement.create_data_consent_log` import chain genuinely resolves** — `saccomanagement/__init__.py` does export it, contradicting a claim in the prior (deleted) audit that this import silently failed. The underlying write is also wrapped in `try/except` so a logging failure can't crash statement generation.
- **The specific bug the prior audit flagged in the generic PSP callback path (`callback.payload` vs. `Callback.raw_payload`) is moot** — that whole code surface has since been decommissioned; the generic-PSP endpoints now return `410 Gone` and don't touch the `Callback` model at all.
- **Consent give/withdraw endpoints**: correct idempotency and `IntegrityError` race recovery, with dedicated throttles. (The gap is entirely on the *enforcement* side — Critical §18/19 — not the recording side.)
- **Migration coverage is complete** for every app with real models; `dashboard`/`health` correctly have none.
- **Celery queue names, beat scheduler class, and `DJANGO_SETTINGS_MODULE` defaults are all consistent** across `config/celery.py`, `Procfile`, and `render.yaml`.

---

## Part 4 — Prioritized Punch List

Ordered by real-world impact, not by section order above.

1. **Fix the KYC upload `throttle_classes` typo** (Critical §1) — one-line fix, the single highest-leverage item in this report; the endpoint is currently 100% broken.
2. **Fix the data-erasure `else` branch and route KYC anonymization through the working (staff) path, or vice versa** (Critical §2, §3) — a DPA/ODPC compliance obligation is currently unmeetable through either code path correctly.
3. **Define `FIELD_ENCRYPTION_KEY` in production config and add a fail-fast `AppConfig.ready()` guard** (Critical §4) — payment onboarding is currently one missing env var away from breaking outright.
4. **Add `select_for_update()` locking to the B2C disbursement and savings-withdrawal initiation paths** (Critical §8, §9) — real, double-spendable money bugs with no mitigating control today.
5. **Route all `LedgerEntry` creation through `create_ledger_entry()`** — fix `_apply_loan_repayment`, `_create_loan_disbursement_ledger`, and bulk-import to stop hand-computing `balance_after` (Critical §5).
6. **Fix the dividend engine's balance-sourcing filter** to include non-M-Pesa-provenance ledger entries, or explicitly document/backfill the gap for imported members (Critical §6) — currently causes real, permanent dividend under-payment.
7. **Lock `GuaranteeCapacity` reads before the check-then-commit approval decision, and pick one (SACCO-scoped) implementation of capacity calculation** — collapses Critical §7, §10, and Medium §27 into one fix.
8. **Add a B2C-equivalent reconciliation task and stop treating initiation timeouts as definitive failures** (Critical §11).
9. **Enforce `METROPOL_MOCK=False` in production via a startup guard**, matching the existing `OAUTH_MOCK` pattern (Critical §12).
10. **Fix the `NameError` typo in `reconcile_uncollected_fees`** (Critical §13) — trivial fix, currently blocks the platform's own fee-reconciliation tooling.
11. **Decide the fate of `require_consent`/KYC-gating permission classes**: either wire them into the endpoints they were clearly built for (loan/guarantor actions, bulk marketing SMS), or remove them to stop signaling protection that doesn't exist (Critical §18–20 combined, High §19).
12. **Add per-account/per-identity throttling to `LoginView` and `PasswordResetRequestView`** (High §21, §22).
13. **Scope `BillingSuspensionMiddleware` to the request's actual SACCO** rather than "does this admin manage any suspended SACCO anywhere" (Medium §23), and build a real self-service payment path for suspended admins (Medium §25).
14. **Fix the `MembershipLeaveView` status whitelist** to include `DEFAULTED`/`DISBURSEMENT_PENDING`, and add a rejection→`Membership.REJECTED` transition with a re-application path** (Medium §34, §36).
15. **Address the remaining Medium items opportunistically during the hardening pass** — most are narrow-window races or logging/consistency gaps rather than open money-loss vectors.

This document is meant to be read alongside the money-flow traces in Part 2 when scoping the hardening work — several of the punch-list items above touch the exact same code paths those traces walk through.
