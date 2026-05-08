# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================

## OTP and Password Reset Implementation Review

### What Each Class or Function Does and Why

#### **accounts/throttles.py**

**OTPSendThrottle(AnonRateThrottle)**
- Limits OTP sends to 5 per phone number per hour
- Uses phone number from request data for cache key instead of IP address
- Returns custom 429 response with remaining time in minutes
- Why: Prevents SMS bombing while allowing legitimate users on same IP

#### **accounts/views.py**

**OTPSendView(APIView)**
- POST /api/v1/accounts/otp/send/ endpoint with AllowAny permission
- Uses OTPRequestSerializer for phone_number and purpose validation
- Throttled to 5 OTP sends per phone per hour
- Creates OTP token via create_otp_token() and sends via ATSMSClient
- For PHONE_VERIFY/PASSWORD_RESET: looks up user by phone_number
- For PASSWORD_RESET: returns 200 silently if user not found (security)
- Wraps ATSMSError in try/except, returns 502 on SMS failure
- Why: Secure way to send OTP codes for phone verification and password reset

**OTPVerifyView(APIView)**
- POST /api/v1/accounts/otp/verify/ endpoint with AllowAny permission
- Uses OTPVerifySerializer for phone_number and 6-digit code validation
- Calls verify_otp() to validate token and mark as used
- On success: updates user.phone_number and returns user data via UserProfileSerializer
- On OTPError: returns 400 with specific error message
- Why: Completes phone verification flow and authenticates user

**OTPResendView(APIView)**
- POST /api/v1/accounts/otp/resend/ endpoint with AllowAny permission
- Same logic as OTPSendView but with cooldown checking
- Checks if most recent OTP was created < OTP_RESEND_COOLDOWN_SECONDS ago
- If within cooldown: returns 429 with seconds_remaining
- Otherwise: invalidates old tokens and creates new one
- Why: Allows users to request new OTP while preventing abuse

**PasswordResetRequestView(APIView)**
- POST /api/v1/accounts/password/reset/ endpoint with AllowAny permission
- Uses OTPRequestSerializer with purpose=PASSWORD_RESET
- Always returns 200 regardless of whether user exists (security)
- Creates OTP token and sends SMS if user found
- Why: Prevents user enumeration attacks during password reset

**PasswordResetConfirmView(APIView)**
- POST /api/v1/accounts/password/reset/confirm/ endpoint with AllowAny permission
- Uses PasswordResetConfirmSerializer for phone, code, new_password validation
- Calls verify_otp() to validate reset token
- On success: updates user password via user.set_password()
- Why: Secure password reset using OTP verification

#### **accounts/integrations/oauth.py**

**GoogleOAuthClient**
- Stub implementation with TODO comment for full OAuth flow
- exchange_code_for_token(): Returns mock access token in DEBUG mode
- get_user_info(): Returns mock user profile data in DEBUG mode
- Raises NotImplementedError for production implementation
- Why: Provides development placeholder while parking complex OAuth implementation

### Django/Python Concepts You Might Not Know Well

**Custom Throttling**
- AnonRateThrottle base class for unauthenticated requests
- get_cache_key() override to use phone number instead of IP
- Custom throttle_failure() for 429 response with remaining time
- Why: Phone-based throttling is more appropriate than IP-based for OTP

**APIView vs Generic Views**
- APIView for simple POST endpoints without model relationships
- No queryset or serializer_class at class level (defined per method)
- Direct request.data access and manual Response objects
- Why: More control over request/response flow for OTP endpoints

**Serializer Validation**
- OTPRequestSerializer: phone_number with Kenyan validation, purpose with choices
- OTPVerifySerializer: phone_number + 6-digit code validation
- PasswordResetConfirmSerializer: phone, code, new password fields
- Custom validators for phone number format and password strength
- Why: Ensures data integrity before processing

**Security Considerations**
- Password reset always returns 200 (prevents user enumeration)
- OTP tokens have expiry and attempt limits
- Phone number normalization for Kenya (+254 format)
- Throttling prevents SMS abuse
- Why: Protects against common attack vectors

**Atomic Operations**
- OTP token creation and SMS sending wrapped in try/except
- Database operations use update_fields for efficiency
- Why: Ensures data consistency and performance

### One Thing to Test Manually to Confirm It Works

**Complete OTP Flow Test:**

1. **Send OTP:**
   ```bash
   POST /api/v1/accounts/otp/send/
   {
       "phone_number": "254712345678",
       "purpose": "PHONE_VERIFY"
   }
   ```
   - Should return 200 with "OTP sent" message
   - In DEBUG mode: check console for OTP code

2. **Verify OTP:**
   ```bash
   POST /api/v1/accounts/otp/verify/
   {
       "phone_number": "254712345678",
       "code": "123456"
   }
   ```
   - Should return 200 with user profile data
   - User's phone_number should be updated

3. **Test Cooldown:**
   ```bash
   POST /api/v1/accounts/otp/resend/
   {
       "phone_number": "254712345678",
       "purpose": "PHONE_VERIFY"
   }
   ```
   - Immediate resend should return 429 with seconds_remaining
   - Wait 12 minutes, then resend should work

4. **Password Reset Flow:**
   ```bash
   # Request reset
   POST /api/v1/accounts/password/reset/
   {
       "phone_number": "254712345678",
       "purpose": "PASSWORD_RESET"
   }
   
   # Confirm reset (use OTP from logs)
   POST /api/v1/accounts/password/reset/confirm/
   {
       "phone_number": "254712345678",
       "code": "123456",
       "new_password": "NewSecurePass123"
   }
   ```
   - Should successfully update user password

### Important Design Decisions and Why

**Phone-Based Throttling**
- Decision: Use phone number instead of IP for throttle cache key
- Why: Multiple legitimate users can share same IP (office, WiFi)
- Impact: Fairer throttling that doesn't block legitimate users

**Silent Password Reset**
- Decision: Always return 200 for password reset requests
- Why: Prevents user enumeration attacks (checking if phone exists)
- Impact: Better security at cost of slightly less user feedback

**Separate OTP Purposes**
- Decision: Use PHONE_VERIFY vs PASSWORD_RESET purposes
- Why: Different validation rules and user experience flows
- Impact: Clear separation of concerns and easier debugging

**Custom Throttle Response**
- Decision: Override throttle_failure() for detailed 429 response
- Why: Provide users with exact wait time in minutes
- Impact: Better user experience with clear retry instructions

**Stub OAuth Implementation**
- Decision: Return mock data in DEBUG mode, NotImplementedError in production
- Why: Allows development without full OAuth complexity
- Impact: Faster development cycle, clear TODO for production

**Error Handling Strategy**
- Decision: Specific HTTP status codes (400, 429, 500, 502)
- Why: Clear API contract for frontend error handling
- Impact: Easier integration and debugging

**Serializer Validation**
- Decision: Use existing Kenyan phone validator and add OTP-specific validations
- Why: Consistent validation across all OTP endpoints
- Impact: Data integrity and clear error messages

# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
