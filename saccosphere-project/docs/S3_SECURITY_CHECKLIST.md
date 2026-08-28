# S3 Security Configuration Checklist for KYC Documents

**Scope:** This document covers infrastructure-level S3 configuration required for secure KYC document storage. Application-level configuration is in `accounts/storage.py`.

**Note:** This repo does not manage infrastructure as code (no Terraform/CloudFormation files). These steps must be applied via AWS Console or CLI by an infrastructure engineer with appropriate permissions.

---

## Prerequisites

- AWS account with appropriate IAM permissions
- S3 bucket name (configured in Django settings as `AWS_STORAGE_BUCKET_NAME`)
- KMS key ARN (if using SSE-KMS encryption)

---

## 1. Server-Side Encryption (SSE-KMS)

### Option A: Use Existing KMS Key (Recommended if available)

**Check for existing CMK:**
```bash
aws kms list-keys --query 'Keys[*].KeyId' --output table
aws kms describe-key --key-id <KEY_ID>
```

**If a suitable CMK exists:**
- Note the key ARN
- Add to Django settings: `AWS_KMS_KEY_ID = 'arn:aws:kms:region:account-id:key/key-id'`
- Skip to step 2

### Option B: Create New KMS Key (If no suitable key exists)

**Create customer-managed key:**
```bash
aws kms create-key \
  --description "SaccoSphere KYC Documents Encryption Key" \
  --key-usage ENCRYPT_DECRYPT \
  --origin AWS_KMS \
  --tags TagKey=Application,TagValue=SaccoSphere
```

**Create key alias:**
```bash
aws kms create-alias \
  --alias-name alias/saccosphere-kyc-documents \
  --target-key-id <KEY_ID>
```

**Configure key policy:**
- Allow the IAM role used by the Django application to `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`
- Allow the IAM role used by the Django application to `kms:DescribeKey`
- Restrict key usage to the specific S3 bucket using condition keys

**Add to Django settings:**
```python
# config/settings/base.py or production.py
AWS_KMS_KEY_ID = 'arn:aws:kms:region:account-id:key/key-id'
```

### Option C: Use SSE-S3 (Fallback)

If KMS is not available, the application defaults to SSE-S3 (AES-256). This provides encryption at rest but without key rotation control or KMS access logging.

**Enable default bucket encryption:**
```bash
aws s3api put-bucket-encryption \
  --bucket <BUCKET_NAME> \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'
```

---

## 2. Bucket Policy and ACL Configuration

### Block Public Access

**Enable block public access at bucket level:**
```bash
aws s3api put-public-access-block \
  --bucket <BUCKET_NAME> \
  --public-access-block-configuration '{
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }'
```

### Set Bucket Policy

**Create bucket policy to enforce private access:**
```bash
aws s3api put-bucket-policy \
  --bucket <BUCKET_NAME> \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "DenyPublicRead",
        "Effect": "Deny",
        "Principal": "*",
        "Action": "s3:GetObject",
        "Resource": "arn:aws:s3:::<BUCKET_NAME>/*",
        "Condition": {
          "StringNotEquals": {
            "aws:PrincipalArn": "<DJANGO_APP_IAM_ROLE_ARN>"
          }
        }
      },
      {
        "Sid": "AllowAppAccess",
        "Effect": "Allow",
        "Principal": {
          "AWS": "<DJANGO_APP_IAM_ROLE_ARN>"
        },
        "Action": [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject"
        ],
        "Resource": "arn:aws:s3:::<BUCKET_NAME>/*"
      }
    ]
  }'
```

**Verify no public ACLs exist:**
```bash
aws s3api get-bucket-acl --bucket <BUCKET_NAME>
```

---

## 3. Lifecycle Rule for Data Retention

**Note:** The data retention period should align with your data retention policy. The example below uses 7 years (common for KYC documents under financial regulations). Adjust as needed.

**Create lifecycle rule:**
```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket <BUCKET_NAME> \
  --lifecycle-configuration '{
    "Rules": [
      {
        "Id": "DeleteKYCDocumentsAfterRetentionPeriod",
        "Status": "Enabled",
        "Filter": {
          "Prefix": "kyc/"
        },
        "Expiration": {
          "Days": 2555
        },
        "NoncurrentVersionExpiration": {
          "NoncurrentDays": 30
        }
      }
    ]
  }'
```

**Verify lifecycle rule:**
```bash
aws s3api get-bucket-lifecycle-configuration --bucket <BUCKET_NAME>
```

---

## 4. Access Logging and CloudTrail

### Option A: S3 Server Access Logging

**Create a separate logging bucket:**
```bash
aws s3api create-bucket \
  --bucket <BUCKET_NAME>-logs \
  --region <REGION>
```

**Enable logging:**
```bash
aws s3api put-bucket-logging \
  --bucket <BUCKET_NAME> \
  --bucket-logging-status '{
    "LoggingEnabled": {
      "TargetBucket": "<BUCKET_NAME>-logs",
      "TargetPrefix": "access-logs/"
    }
  }'
```

### Option B: CloudTrail Data Events (Recommended)

**Create or update CloudTrail:**
```bash
aws cloudtrail put-event-selectors \
  --trail-name <TRAIL_NAME> \
  --event-selectors '[
    {
      "ReadWriteType": "ReadOnly",
      "IncludeManagementEvents": false,
      "DataResources": [
        {
          "Type": "AWS::S3::Object",
          "Values": ["arn:aws:s3:::<BUCKET_NAME>/kyc/*"]
        }
      ]
    }
  ]'
```

**Verify CloudTrail is logging S3 data events:**
```bash
aws cloudtrail get-event-selectors --trail-name <TRAIL_NAME>
```

---

## 5. Versioning

**Enable versioning:**
```bash
aws s3api put-bucket-versioning \
  --bucket <BUCKET_NAME> \
  --versioning-configuration '{
    "Status": "Enabled"
  }'
```

**Verify versioning is enabled:**
```bash
aws s3api get-bucket-versioning --bucket <BUCKET_NAME>
```

**Note:** The lifecycle rule configured in step 3 already includes `NoncurrentVersionExpiration` to delete old versions after 30 days.

---

## 6. IAM Role Configuration

**Ensure the Django application's IAM role has:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>"
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:GenerateDataKey",
        "kms:Decrypt",
        "kms:Encrypt",
        "kms:DescribeKey"
      ],
      "Resource": "arn:aws:kms:*:*:key/<KMS_KEY_ID>",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "s3.<REGION>.amazonaws.com"
        }
      }
    }
  ]
}
```

---

## Verification Checklist

- [ ] SSE-KMS or SSE-S3 encryption is enabled on the bucket
- [ ] Block public access is enabled
- [ ] Bucket policy denies public read access
- [ ] Lifecycle rule is configured with appropriate retention period
- [ ] S3 access logging or CloudTrail data events are enabled
- [ ] Versioning is enabled
- [ ] IAM role has appropriate permissions
- [ ] `AWS_KMS_KEY_ID` is set in Django settings (if using SSE-KMS)

---

## Application Code Changes

The following application code changes have been made to support these infrastructure configurations:

1. **`accounts/storage.py`**: Updated to support SSE-KMS encryption when `AWS_KMS_KEY_ID` is configured, with fallback to SSE-S3. Sets `default_acl='private'` to ensure objects are never publicly readable.

2. **Django settings**: Add the following to your settings file:
   ```python
   # config/settings/production.py
   AWS_KMS_KEY_ID = config('AWS_KMS_KEY_ID', default=None)
   ```

---

## Key-Management Decision Requirements

### Security/Infra Sign-off Needed For:

1. **KMS Key Choice:**
   - Use existing CMK or create new dedicated key for KYC documents?
   - If new key: Key rotation policy (recommended: annual)
   - Key administrators list (who can manage/rotate the key)
   - Key usage policy (which services/principals can use the key)

2. **Retention Period:**
   - Confirm data retention period for KYC documents (example: 7 years)
   - Align with legal/compliance requirements for your jurisdiction
   - Document the retention policy decision

3. **Access Logging Strategy:**
   - S3 server access logging vs. CloudTrail data events
   - CloudTrail provides better integration with AWS security tools
   - S3 logging provides more granular request details
   - Decision should align with your monitoring and audit strategy

4. **Version Retention:**
   - Confirm how long to retain non-current versions (example: 30 days)
   - Balance between audit trail and storage costs
   - Consider compliance requirements for document history

### Recommended Approach:

- **SSE-KMS with dedicated key**: Preferred for PII of this sensitivity
- **7-year retention**: Common for financial KYC documents under Kenyan regulations
- **CloudTrail data events**: Better integration with AWS security monitoring
- **30-day version retention**: Provides recovery window without excessive storage costs

---

## References

- [AWS S3 Server-Side Encryption](https://docs.aws.amazon.com/AmazonS3/latest/userguide/serv-side-encryption.html)
- [AWS KMS Key Policies](https://docs.aws.amazon.com/kms/latest/developerguide/key-policies.html)
- [S3 Lifecycle Configuration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-configuration-bucket.html)
- [CloudTrail Data Events](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/logging-data-events-with-cloudtrail.html)
