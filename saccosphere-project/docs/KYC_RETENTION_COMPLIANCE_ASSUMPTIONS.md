# KYC Document Retention - Compliance Assumptions

**Document Version:** 1.0
**Date:** August 28, 2026
**Purpose:** This document lists all assumptions made regarding retention periods, hold conditions, and jurisdiction rules in the KYC document retention implementation. These require legal/compliance review before production deployment.

---

## Overview

The KYC document retention implementation includes automated cleanup, user-initiated erasure requests, and regulatory hold handling. This document flags every location where a compliance decision was assumed rather than explicitly provided by legal/compliance teams.

---

## Assumption 1: Retention Period Default

**Location:** `config/settings/base.py` (line 286-287)

**Code:**
```python
_KYC_RETENTION_DAYS = config('KYC_RETENTION_DAYS', default='')
KYC_RETENTION_DAYS = int(_KYC_RETENTION_DAYS) if _KYC_RETENTION_DAYS else None
```

**Assumption:**
- Default is `None` (disabled) if not configured via environment variable
- Comment suggests "2555 days (7 years) for financial KYC in Kenya" as a common value

**Compliance Decision Required:**
- What is the actual required retention period for KYC documents under applicable law?
- Kenya Data Protection Act 2019 requirements?
- Central Bank of Kenya (CBK) prudential guidelines?
- Any other jurisdiction-specific requirements (if operating outside Kenya)?

**Action:** Legal/compliance must specify the exact retention period in days, or confirm that 7 years (2555 days) is appropriate.

---

## Assumption 2: Hold Reason Categories

**Location:** `accounts/models.py` (lines 1604-1608)

**Code:**
```python
class HoldReason(models.TextChoices):
    REGULATORY_INVESTIGATION = 'REGULATORY_INVESTIGATION', 'Regulatory Investigation'
    DISPUTE_IN_PROGRESS = 'DISPUTE_IN_PROGRESS', 'Dispute In Progress'
    LEGAL_HOLD = 'LEGAL_HOLD', 'Legal Hold'
    AUDIT_IN_PROGRESS = 'AUDIT_IN_PROGRESS', 'Audit In Progress'
```

**Assumption:**
- Four hold reason categories were defined based on common regulatory scenarios
- These are generic and may not cover all applicable hold scenarios

**Compliance Decision Required:**
- Are these hold reason categories appropriate for the jurisdiction?
- Are additional hold reasons needed (e.g., court order, law enforcement request)?
- Are any of these categories not applicable or should be renamed?

**Action:** Legal/compliance must review and approve the hold reason categories.

---

## Assumption 3: Hold Duration

**Location:** `accounts/models.py` (line 1644-1647)

**Code:**
```python
hold_until = models.DateTimeField(
    null=True,
    blank=True,
    help_text='Date until which the hold applies.',
)
```

**Assumption:**
- Hold duration is open-ended (can be set to `None` for indefinite holds)
- No maximum hold duration is enforced
- No automatic hold expiration based on regulatory requirements

**Compliance Decision Required:**
- Is there a maximum duration for regulatory holds under applicable law?
- Should indefinite holds be allowed?
- Should there be automatic hold expiration after a certain period?
- What is the process for extending a hold?

**Action:** Legal/compliance must specify hold duration rules and maximums.

---

## Assumption 4: Erasure Request Processing Without Hold

**Location:** `accounts/views.py` (lines 1469-1514)

**Code:**
```python
else:
    # No hold - execute immediately
    erasure_request = serializer.save(
        status=DataErasureRequest.Status.APPROVED,
        reviewed_by=request.user,
        reviewed_at=timezone.now(),
    )
```

**Assumption:**
- When no hold applies, erasure requests are executed immediately without staff review
- User self-approval is allowed for immediate deletion

**Compliance Decision Required:**
- Should all erasure requests require staff review, even when no hold applies?
- Is user self-approval for immediate deletion appropriate under applicable law?
- Should there be a mandatory review period (e.g., 30 days) before execution?

**Action:** Legal/compliance must specify whether staff review is required for all erasure requests.

---

## Assumption 5: Minimal Trail Preservation

**Location:** `accounts/management/commands/cleanup_expired_kyc.py` (lines 80-81)

**Code:**
```python
kyc.status = KYCVerification.Status.REJECTED
kyc.rejection_reason = 'Documents expired and were anonymized per retention policy.'
```

**Assumption:**
- After anonymization, the record is marked as `REJECTED`
- Verification outcome (`verified_at`, `submitted_at`) is preserved
- This preserves a minimal trail that "a verification happened on this date, outcome X"

**Compliance Decision Required:**
- Is preserving the verification outcome sufficient for business records?
- Should additional fields be preserved (e.g., original status, reviewer)?
- Should anonymized records be deleted entirely after a secondary retention period?
- What is the business requirement for historical verification records?

**Action:** Legal/compliance and business stakeholders must specify what data must be preserved and for how long.

---

## Assumption 6: PII Field Anonymization Strategy

**Location:** `accounts/kyc_retention.py` (lines 52-70)

**Code:**
```python
kyc.id_number = None
kyc.normalized_id_number = None
kyc.huduma_namba = None
```

**Assumption:**
- PII fields are set to `None` (deleted) rather than anonymized with pseudonymization
- No hash or token is retained for future reconciliation
- This is a hard deletion, not anonymization

**Compliance Decision Required:**
- Is hard deletion appropriate, or should pseudonymization be used?
- Should a hash of the ID number be retained for deduplication purposes?
- Are there any fields that must be retained in anonymized form (e.g., masked ID)?

**Action:** Legal/compliance must specify the anonymization strategy (deletion vs. pseudonymization).

---

## Assumption 7: S3 Object Deletion

**Location:** `accounts/kyc_retention.py` (lines 73-95)

**Code:**
```python
if hasattr(document, 'delete'):
    document.delete(save=False)
```

**Assumption:**
- S3 objects are permanently deleted (not moved to archive)
- No soft delete or archive tier is used
- Deletion is irreversible

**Compliance Decision Required:**
- Should documents be moved to S3 Glacier or archive tier before deletion?
- Should there be a secondary retention period for archived documents?
- Is permanent deletion appropriate, or should a backup be retained?

**Action:** Legal/compliance must specify the document archival and deletion strategy.

---

## Assumption 8: Cascade Behavior on User Deletion

**Location:** `accounts/models.py` (line 1005)

**Code:**
```python
user = models.OneToOneField(
    User,
    on_delete=models.CASCADE,
    related_name='kyc',
    help_text='User whose identity is being verified.',
)
```

**Assumption:**
- When a user is deleted, the KYC record is also deleted (CASCADE)
- This is standard Django behavior for OneToOneField

**Compliance Decision Required:**
- Should KYC records be retained even after user account deletion?
- If so, should the cascade be changed to `SET_NULL` or `PROTECT`?
- What is the retention requirement for KYC records of deleted users?

**Action:** Legal/compliance must specify whether KYC records should outlive user accounts.

---

## Assumption 9: Scheduled Cleanup Frequency

**Location:** `config/celery.py` (lines 49-52)

**Code:**
```python
'cleanup-expired-kyc': {
    'task': 'accounts.tasks.cleanup_expired_kyc',
    'schedule': crontab(minute=0, hour=2),  # Daily at 2 AM
},
```

**Assumption:**
- Cleanup runs daily at 2 AM
- No immediate cleanup is triggered when `retention_until` is reached

**Compliance Decision Required:**
- Is daily cleanup frequent enough, or should it run more frequently?
- Should cleanup be triggered immediately when `retention_until` is reached?
- What is the acceptable delay between retention expiry and deletion?

**Action:** Legal/compliance must specify the maximum acceptable delay for cleanup.

---

## Assumption 10: Queued Erasure Request Processing Frequency

**Location:** `config/celery.py` (lines 53-56)

**Code:**
```python
'process-queued-erasure-requests': {
    'task': 'accounts.tasks.process_queued_erasure_requests',
    'schedule': crontab(minute='*/30'),  # Every 30 minutes
},
```

**Assumption:**
- Queued erasure requests are checked every 30 minutes
- No immediate notification is sent when a hold expires

**Compliance Decision Required:**
- Is 30-minute frequency appropriate for processing queued requests?
- Should users be notified when their erasure request is processed?
- What is the acceptable delay between hold expiration and processing?

**Action:** Legal/compliance must specify the processing frequency and notification requirements.

---

## Assumption 11: Audit Trail Content

**Location:** `accounts/kyc_retention.py` (lines 72-90)

**Code:**
```python
log_audit(
    user=triggered_by,
    action='KYC_ERASURE',
    resource_type='KYCDocument',
    resource_id=str(kyc.id),
    old_values=old_values,
    new_values={
        'status': kyc.status,
        'rejection_reason': kyc.rejection_reason,
        'triggered_by': triggered_by.email if triggered_by else 'System',
    },
)
```

**Assumption:**
- Audit trail logs the action, resource, and who triggered it
- Old values include whether documents existed (boolean), not the actual content
- This provides a tamper-evident log of deletion events

**Compliance Decision Required:**
- Is this level of audit detail sufficient for compliance requirements?
- Should additional context be logged (e.g., IP address, user agent)?
- Should the audit log be sent to an external system (e.g., SIEM)?
- What is the retention period for audit logs?

**Action:** Legal/compliance must specify audit trail requirements.

---

## Assumption 12: Jurisdiction-Specific Requirements

**Location:** Throughout implementation

**Assumption:**
- Implementation is based on general data protection principles
- No specific jurisdictional requirements (e.g., GDPR, CCPA) are hardcoded
- Kenya Data Protection Act 2019 is referenced in comments but not enforced

**Compliance Decision Required:**
- Which jurisdictions' laws apply to this system?
- Are there specific requirements for:
  - Data portability?
  - Right to be forgotten response timeframes?
  - Data breach notification?
  - Cross-border data transfer?
- Should the implementation be adapted for specific jurisdictions?

**Action:** Legal/compliance must specify applicable jurisdictions and their requirements.

---

## Summary of Required Compliance Actions

1. **Specify retention period** in days (currently defaulting to `None`)
2. **Review and approve hold reason categories** (currently 4 generic categories)
3. **Specify hold duration rules** (currently open-ended)
4. **Specify erasure request approval workflow** (currently self-approval allowed)
5. **Specify minimal trail preservation requirements** (currently status + dates)
6. **Specify PII anonymization strategy** (currently hard deletion)
7. **Specify S3 object archival/deletion strategy** (currently permanent deletion)
8. **Specify cascade behavior for user deletion** (currently CASCADE)
9. **Specify cleanup frequency** (currently daily at 2 AM)
10. **Specify queued request processing frequency** (currently every 30 minutes)
11. **Specify audit trail requirements** (currently action + who + what)
12. **Specify applicable jurisdictions and their requirements** (currently generic)

---

## Next Steps

1. Legal/compliance team reviews this document
2. For each assumption, provide a decision or requirement
3. Update the implementation based on compliance decisions
4. Re-test with compliance-approved configurations
5. Document final compliance-approved configuration in deployment guide

---

## References

- Kenya Data Protection Act 2019: https://www.odpp.go.ke/
- Central Bank of Kenya Prudential Guidelines: https://www.centralbank.go.ke/
- GDPR (if applicable): https://gdpr.eu/
- CCPA (if applicable): https://oag.ca.gov/privacy/ccpa
