# Email Verification Implementation - Server Deployment Guide

## Overview
This branch implements a complete email verification system using SMTP with Alibaba Cloud DirectMail. The implementation includes:

- ✅ SMTP-based email service (replaces DirectMail API)
- ✅ User model extensions for email verification
- ✅ Complete auth endpoints for registration, verification, and password reset
- ✅ Configuration management
- ✅ Local testing completed successfully

## Database Migration Required

After deployment, run this migration on the server:

```sql
-- Add email verification fields to users table
ALTER TABLE users 
ADD COLUMN email_verification_code VARCHAR(6),
ADD COLUMN email_verification_expires_at TIMESTAMPTZ,
ADD COLUMN password_reset_token VARCHAR(64),
ADD COLUMN password_reset_expires_at TIMESTAMPTZ;

-- Create indexes for performance
CREATE INDEX idx_users_email_verification_code ON users(email_verification_code);
CREATE INDEX idx_users_password_reset_token ON users(password_reset_token);
CREATE INDEX idx_users_email_verification_expires ON users(email_verification_expires_at);
CREATE INDEX idx_users_password_reset_expires ON users(password_reset_expires_at);
```

Or use Flask-Migrate:
```bash
flask db migrate -m "Add email verification fields"
flask db upgrade
```

## New API Endpoints

### Registration with Email Verification
```
POST /auth/register
{
  "username": "testuser",
  "email": "user@example.com", 
  "password": "password123"
}
```

### Email Verification
```
POST /auth/verify-email
{
  "user_id": 123,
  "verification_code": "123456"
}
```

### Resend Verification Code
```
POST /auth/resend-verification
{
  "user_id": 123
}
```

### Password Reset Request
```
POST /auth/forgot-password
{
  "email": "user@example.com"
}
```

### Password Reset Confirmation
```
POST /auth/reset-password
{
  "token": "reset_token_from_email",
  "password": "new_password123"
}
```

## Environment Variables

Ensure these are set in server .env:

```bash
# SMTP Configuration (already configured)
ALIBABA_CLOUD_EMAIL_SMTP_SECRET=UniKorn2025
ALIBABA_DM_ACCOUNT_NAME=no-reply@unikorn.axfff.com
ALIBABA_DM_FROM_ALIAS=uniKorn 校园论坛

# Optional settings (have defaults)
EMAIL_VERIFICATION_EXPIRES_MINUTES=10
PASSWORD_RESET_EXPIRES_HOURS=1
FRONTEND_BASE_URL=https://unikorn.axfff.com
```

## Testing After Deployment

1. **Test Registration**:
   ```bash
   curl -X POST https://dev.unikorn.axfff.com/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{"username":"testuser","email":"test@example.com","password":"password123"}'
   ```

2. **Check Email Reception**: Verify test email is received with 6-digit code

3. **Test Verification**:
   ```bash
   curl -X POST https://dev.unikorn.axfff.com/api/auth/verify-email \
     -H "Content-Type: application/json" \
     -d '{"user_id":123,"verification_code":"123456"}'
   ```

4. **Test Password Reset Flow**:
   ```bash
   curl -X POST https://dev.unikorn.axfff.com/api/auth/forgot-password \
     -H "Content-Type: application/json" \
     -d '{"email":"test@example.com"}'
   ```

## Key Implementation Details

- **SMTP vs DirectMail API**: Switched from complex signature-based API to standard SMTP
- **Security**: Tokens expire automatically, proper validation, no email enumeration
- **User Experience**: Clear error messages, graceful email sending failures
- **Database**: New fields added without breaking existing users
- **Templates**: Chinese email templates with HTML and text versions

## Files Modified/Created

### New Files:
- `app/services/email_service.py` - SMTP email service
- `test_email_verification.py` - Local testing script

### Modified Files:
- `app/models/user.py` - Added verification fields and methods
- `app/routes/auth.py` - Added email verification endpoints  
- `app/config.py` - Added SMTP configuration

## Production Checklist

- [ ] Deploy code via GitHub Actions
- [ ] Run database migration
- [ ] Test email sending on server
- [ ] Verify all endpoints work
- [ ] Test complete registration flow
- [ ] Test password reset flow
- [ ] Monitor email delivery rates
- [ ] Check logs for any errors

## Local Testing Results

✅ All local tests passed:
- Email service functionality ✅
- User model methods ✅  
- Template generation ✅
- Code/token generation ✅
- Validation logic ✅

Ready for server deployment!