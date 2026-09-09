# Runbook: B2C disbursement stuck after an initiation timeout

## When this fires

`reconcile_stale_mpesa_transactions` (Celery beat) moved a loan's
`disbursement_status` from `PENDING_CONFIRMATION` to `UNDER_REVIEW` and
logged at ERROR:

```
B2C disbursement for loan <loan_id> timed out at initiation and has not
been confirmed after N reconciliation attempts. Moved to UNDER_REVIEW.
Manual confirmation required ... (conversation_id=<id>)
```

An append-only `DisbursementAuditLog` row is written with
`event="ESCALATED_TO_SUPERADMIN"`,
`details.reason="b2c_initiation_timeout_unconfirmed"`.

## What it means

The outbound M-Pesa **B2C initiate** call timed out before Safaricom
returned a `ConversationID`. This is genuinely ambiguous:

- Safaricom **may** have accepted and paid the member, and the result
  callback was lost or never sent; **or**
- Safaricom never received a usable request and **no** payout happened.

The system deliberately does **not** guess. There is no synchronous
Daraja "query B2C status" API (Daraja's Transaction Status API replies
only via its own asynchronous result callback, which is not wired up in
this codebase), so a human must establish the truth from Safaricom's
records before the loan moves on.

**Do not** use the admin "Unlock selected loans for a disbursement
retry" action until you have confirmed with Safaricom that **no** payout
occurred. Unlocking + re-disbursing an already-paid loan pays the member
twice.

## Steps

1. **Get the identifiers.** From the loan's disbursement audit trail
   (`GET /api/v1/services/loans/<loan_id>/disbursement-audit/` or Django
   admin -> Disbursement audit logs):
   - `conversation_id` (from the escalation row's `mpesa_ref`)
   - `idempotency_key` (from `details.idempotency_key`)
   - the `Transaction.reference` (`SS-DSB-...`) and net amount from the
     linked `disbursement_transaction`.

2. **Check Safaricom.** In the M-Pesa Org / Daraja portal for that
   SACCO's shortcode, search the B2C / "utility to customer" statement
   for the window around `disbursement_initiated_at` for a payment of the
   net amount to the member's MSISDN. Cross-check with the SACCO's
   settlement statement.

3. **If Safaricom shows the payout succeeded:**
   - In Django admin, open the loan (disbursement fields are read-only)
     and use a data migration or `manage.py shell` under change control
     to set `disbursement_status = DISBURSED`, `disbursed_amount`,
     `disbursement_date`, `outstanding_balance`, and `status = ACTIVE`,
     matching what a normal successful callback would have done
     (`payments/tasks._process_successful_b2c_callback` is the reference).
   - Write a `DisbursementAuditLog` row: `event="B2C_CALLBACK_RECEIVED"`,
     `actor_role="system"`, `details` noting "manual reconciliation per
     runbook", the operator, and the Safaricom reference.
   - Trigger `send_disbursement_confirmation_request` for the loan so the
     member still gets the confirm/dispute SMS.

4. **If Safaricom shows no payout:**
   - Use the admin **"Unlock selected loans for a disbursement retry"**
     action with a reason that cites this runbook and the Safaricom
     evidence. That resets `disbursement_status` to `PENDING`, clears the
     idempotency key, and writes a `RESOLVED_BY_ADMIN` audit row.
   - Re-initiate the disbursement through the normal path
     (`POST /api/v1/payments/mpesa/b2c/disburse/`).

5. **If Safaricom is inconclusive:** keep the loan in `UNDER_REVIEW`,
   escalate to Finance + the SACCO, and do **not** unlock for retry.

## Prevention / follow-up

- If these escalations are frequent, wire up Daraja's Transaction Status
  Query API with a dedicated result-callback endpoint so reconciliation
  can resolve the ambiguous cases automatically. Tracked as future work;
  until then this runbook is the authoritative resolution path.
