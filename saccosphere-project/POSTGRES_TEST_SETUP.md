# PostgreSQL Test Setup for OTP Race Condition Tests

## Overview
The OTP race condition tests require PostgreSQL to properly test the partial unique constraint. SQLite does not support partial unique constraints properly for this use case.

## Docker Compose Setup (Recommended)
A `docker-compose.test.yml` file has been provided with a PostgreSQL 15 service.

### Start PostgreSQL
```bash
docker-compose -f docker-compose.test.yml up -d
```

### Stop PostgreSQL
```bash
docker-compose -f docker-compose.test.yml down
```

## Manual PostgreSQL Setup
If Docker Compose is not available, you can set up PostgreSQL manually:

1. Install PostgreSQL 15 on your system
2. Create a database and user:
```sql
CREATE DATABASE saccosphere_test;
CREATE USER saccosphere WITH PASSWORD 'saccosphere_test_pass';
GRANT ALL PRIVILEGES ON DATABASE saccosphere_test TO saccosphere;
```

## Running Tests Against PostgreSQL

### Using Docker Compose
```bash
# Set environment variables for PostgreSQL
set DATABASE_URL=postgresql://saccosphere:saccosphere_test_pass@localhost:5433/saccosphere_test
set DB_NAME=saccosphere_test
set DB_USER=saccosphere
set DB_PASSWORD=saccosphere_test_pass
set DB_HOST=localhost
set DB_PORT=5433

# Run the OTP race condition tests
python manage.py test accounts.tests.test_otp_security.OTPRaceConditionTestCase -v 2
```

### Using Manual PostgreSQL
```bash
# Set environment variables for PostgreSQL
set DATABASE_URL=postgresql://saccosphere:saccosphere_test_pass@localhost:5432/saccosphere_test
set DB_NAME=saccosphere_test
set DB_USER=saccosphere
set DB_PASSWORD=saccosphere_test_pass
set DB_HOST=localhost
set DB_PORT=5432

# Run the OTP race condition tests
python manage.py test accounts.tests.test_otp_security.OTPRaceConditionTestCase -v 2
```

## Expected Test Results
When running against PostgreSQL, both concurrency tests should pass without being skipped:
- `test_concurrent_token_creation_prevents_duplicate_active_tokens`
- `test_concurrent_registration_token_creation`

## Notes
- The partial unique constraint `unique_active_otp_per_phone_purpose` enforces that only one active (is_used=False) OTP token can exist per phone_number and purpose combination
- This constraint is enforced at the database level by PostgreSQL, preventing race conditions even when no rows exist to lock
- The `create_otp_token` function handles IntegrityError from concurrent INSERT conflicts by fetching the winning token
