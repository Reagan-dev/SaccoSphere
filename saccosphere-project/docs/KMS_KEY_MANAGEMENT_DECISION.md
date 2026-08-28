# KMS Key Management Decision for KYC Document Encryption

## Overview

This document outlines the decisions required from infrastructure and security teams regarding AWS KMS key management for encrypting KYC documents stored in S3.

## Context

KYC documents contain personally identifiable information (PII) and sensitive identity data. The application uses S3 for storage with server-side encryption. Two encryption options are available:

1. **SSE-KMS (Server-Side Encryption with AWS KMS)**: Uses customer-managed keys (CMKs)
2. **SSE-S3 (Server-Side Encryption with Amazon S3)**: Uses AWS-managed keys

## Recommendation

**Use SSE-KMS with a dedicated customer-managed key.**

### Rationale

- **Key rotation control**: KMS allows automatic key rotation (recommended: annual)
- **Access logging**: KMS provides detailed logs of who used the key and when
- **Granular permissions**: Key policies can restrict which services/principals can use the key
- **Compliance alignment**: Many regulatory frameworks prefer customer-managed keys for PII
- **Separation of duties**: Key management is separate from data access

## Decisions Required

### 1. Key Provisioning Strategy

**Question:** Should we use an existing KMS key or create a new dedicated key?

**Options:**
- **Use existing CMK**: If a suitable key exists with appropriate permissions and rotation policy
- **Create new CMK**: Dedicated key for KYC documents with specific policy

**Decision needed from:** Security team

**Information required:**
- List of existing CMKs in the AWS account
- Key policies of existing keys
- Whether existing keys are used for other sensitive data

**Recommended approach:** Create a new dedicated key with alias `alias/saccosphere-kyc-documents` for clear separation of concerns.

---

### 2. Key Rotation Policy

**Question:** What should the key rotation period be?

**Options:**
- **Annual rotation**: AWS KMS default, recommended for most use cases
- **Monthly rotation**: Higher security, higher cost
- **Disabled rotation**: Not recommended for PII

**Decision needed from:** Security team

**Information required:**
- Compliance requirements for key rotation in your jurisdiction
- Risk tolerance for key compromise
- Cost considerations (KMS charges per key version)

**Recommended approach:** Enable automatic annual rotation. This provides a good balance between security and cost.

---

### 3. Key Administrators

**Question:** Who should have administrative access to the KMS key?

**Options:**
- **Security team only**: Restricted access, higher security
- **DevOps team**: Operational convenience, broader access
- **Shared responsibility**: Both teams with different permission levels

**Decision needed from:** Security team + DevOps team

**Information required:**
- Organizational structure and responsibilities
- On-call rotation for key-related incidents
- Compliance requirements for key management

**Recommended approach:**
- Key administrators: Security team
- Key users: IAM role used by Django application
- Key auditors: Security team via CloudTrail logs

---

### 4. Key Usage Policy

**Question:** Which services and principals should be allowed to use this key?

**Options:**
- **S3 only**: Restrict to S3 service for this bucket
- **Multiple services**: Allow other AWS services if needed
- **Cross-account**: Allow access from other AWS accounts (if multi-account architecture)

**Decision needed from:** Security team + Architecture team

**Information required:**
- Current and future architecture plans
- Cross-account access requirements
- Other services that may need to access KYC documents

**Recommended approach:** Restrict to S3 service for the specific bucket using condition key `kms:ViaService`. This limits the blast radius if the key is compromised.

---

### 5. Data Retention Period

**Question:** What is the required retention period for KYC documents?

**Options:**
- **7 years**: Common for financial KYC documents under Kenyan regulations
- **5 years**: Some jurisdictions require shorter retention
- **10 years**: More conservative approach for regulatory compliance

**Decision needed from:** Legal/Compliance team

**Information required:**
- Applicable data protection laws (e.g., Data Protection Act 2019 in Kenya)
- Financial sector regulations (e.g., CBK guidelines)
- Business requirements for historical data

**Recommended approach:** 7 years, which aligns with common financial sector requirements in Kenya. This should be confirmed with legal counsel.

---

### 6. Access Logging Strategy

**Question:** Should we use S3 server access logging or CloudTrail data events?

**Options:**
- **S3 server access logging**: More granular request details, separate log delivery
- **CloudTrail data events**: Better integration with AWS security tools, centralized logging

**Decision needed from:** Security team + DevOps team

**Information required:**
- Existing monitoring and alerting infrastructure
- Log retention and analysis requirements
- Integration with SIEM or security tools

**Recommended approach:** CloudTrail data events. This provides:
- Centralized logging with other AWS API activity
- Better integration with AWS Security Hub and GuardDuty
- Easier to set up alerts and anomaly detection

---

### 7. Version Retention Period

**Question:** How long should we retain non-current object versions?

**Options:**
- **30 days**: Provides recovery window without excessive storage costs
- **90 days**: Longer recovery window, higher cost
- **Unlimited**: Maximum recovery, highest cost

**Decision needed from:** Security team + Finance team

**Information required:**
- Recovery time objectives (RTO) for document restoration
- Storage cost budget
- Compliance requirements for document history

**Recommended approach:** 30 days. This provides a reasonable window for recovery from accidental deletions or corruption while controlling storage costs.

---

## Implementation Steps

Once decisions are made:

1. **Create or select KMS key** based on decision #1
2. **Configure key policy** based on decisions #3 and #4
3. **Enable key rotation** based on decision #2
4. **Configure S3 bucket encryption** to use the KMS key
5. **Set up lifecycle rules** based on decision #5 and #7
6. **Enable access logging** based on decision #6
7. **Update Django settings** with `AWS_KMS_KEY_ID`
8. **Verify encryption is working** by uploading a test document

---

## Security Sign-off Checklist

Before production deployment:

- [ ] KMS key strategy approved by security team
- [ ] Key rotation period approved by security team
- [ ] Key administrators documented and approved
- [ ] Key usage policy documented and approved
- [ ] Data retention period approved by legal/compliance
- [ ] Access logging strategy approved by security/devops
- [ ] Version retention period approved by security/finance
- [ ] IAM role permissions reviewed and approved
- [ ] CloudTrail/S3 logging verified to be capturing events
- [ ] Encryption verified on test upload
- [ ] Disaster recovery procedure documented (key rotation, key compromise)

---

## Cost Considerations

- **KMS key usage**: Charged per 10,000 API calls (encrypt/decrypt)
- **KMS key storage**: Charged per key per month
- **Key rotation**: Additional cost for each key version
- **S3 storage**: Standard rates, plus version storage costs
- **S3 access logging**: Charged for log storage and delivery
- **CloudTrail data events**: Charged per 100,000 events

**Estimated monthly costs** (approximate, based on typical usage):
- KMS key storage: $1/month
- KMS API calls: $0.03 per 10,000 calls (depends on upload/download volume)
- S3 storage: $0.023/GB/month (depends on document volume)
- CloudTrail data events: $0.10 per 100,000 events (depends on access patterns)

---

## References

- [AWS KMS Pricing](https://aws.amazon.com/kms/pricing/)
- [AWS S3 Pricing](https://aws.amazon.com/s3/pricing/)
- [AWS CloudTrail Pricing](https://aws.amazon.com/cloudtrail/pricing/)
- [Kenya Data Protection Act 2019](https://www.odpp.go.ke/)
- [CBK Prudential Guidelines](https://www.centralbank.go.ke/)
