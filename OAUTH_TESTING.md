# OAuth Local Testing Guide

## 🏠 Local Development Setup

### 1. Backend Setup (Main Site)
```bash
cd /Users/zhaoj/Project/campusForum/back-end

# Install dependencies
pip install -r requirements.txt

# Set up local database (if not already done)
export FLASK_APP=run.py
export FLASK_ENV=development

# Run migration
flask db upgrade

# Register test OAuth client
python -m app.scripts.setup_oauth_client

# Start development server
python run.py
```

### 2. Test Environment Variables
Create `.env` file in back-end directory:
```env
FLASK_ENV=development
FLASK_DEBUG=True
DATABASE_URL=postgresql://username:password@localhost/your_test_db
JWT_SECRET_KEY=your-test-jwt-secret
```

## 🔍 Manual Testing Steps

### Step 1: Test OAuth Client Registration
```bash
# Run the setup script
python -m app.scripts.setup_oauth_client

# Expected output:
# ✅ OAuth client registered successfully!
# Client ID: [20-char string]
# Client Secret: [40-char string]
```

### Step 2: Test Authorization Endpoint
1. **Login to main site** first (get JWT token)
2. **Navigate to authorization URL**:
   ```
   http://localhost:5000/oauth/authorize?response_type=code&client_id=[CLIENT_ID]&redirect_uri=http://localhost:3000/callback&scope=profile+email&state=test123
   ```
3. **Expected**: Beautiful consent screen appears
4. **Click "Allow"** 
5. **Expected**: Redirects to callback with authorization code

### Step 3: Test Token Exchange
Use curl or Postman:
```bash
curl -X POST http://localhost:5000/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code&code=[AUTH_CODE]&redirect_uri=http://localhost:3000/callback&client_id=[CLIENT_ID]&client_secret=[CLIENT_SECRET]"
```

**Expected Response**:
```json
{
  "access_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "profile email"
}
```

### Step 4: Test UserInfo Endpoint
```bash
curl -X GET http://localhost:5000/oauth/userinfo \
  -H "Authorization: Bearer [ACCESS_TOKEN]"
```

**Expected Response**:
```json
{
  "sub": "1",
  "username": "testuser",
  "picture": "https://...",
  "role": "student",
  "email": "test@example.com",
  "email_verified": true
}
```

## ❌ Error Testing

### Test Invalid Client
```bash
# Should return 400 with invalid_client error
curl -X GET "http://localhost:5000/oauth/authorize?response_type=code&client_id=invalid&redirect_uri=http://localhost:3000/callback"
```

### Test Expired Token
1. Wait for token to expire (or modify expiry in code)
2. Try accessing userinfo endpoint
3. **Expected**: `invalid_token` error

### Test Invalid Redirect URI
```bash
# Should return invalid_redirect_uri error
curl -X GET "http://localhost:5000/oauth/authorize?response_type=code&client_id=[CLIENT_ID]&redirect_uri=http://malicious-site.com/callback"
```

## 🎯 Testing Checklist

- [ ] OAuth client registration works
- [ ] Authorization endpoint shows consent screen
- [ ] User can approve/deny authorization
- [ ] Authorization code is generated correctly
- [ ] Token exchange works with valid code
- [ ] UserInfo endpoint returns correct user data
- [ ] Token expiration is enforced
- [ ] Invalid clients are rejected
- [ ] Invalid redirect URIs are rejected
- [ ] PKCE verification works (optional)
- [ ] Token revocation works
- [ ] Admin client management works

## 🔧 Debug Tips

### Enable Flask Debug Mode
```python
# In run.py or your Flask app
app.config['DEBUG'] = True
```

### Check Database Records
```sql
-- Check OAuth clients
SELECT * FROM oauth_clients;

-- Check authorization codes
SELECT * FROM oauth_authorization_codes;

-- Check tokens
SELECT * FROM oauth_tokens;
```

### Common Issues & Fixes

1. **"User not authenticated"** → Make sure JWT token is valid and user is logged in
2. **"Client not found"** → Verify client_id in database
3. **"Invalid redirect URI"** → Check redirect_uris JSON in oauth_clients table
4. **"Token expired"** → Normal behavior, tokens expire in 1 hour

## 🚀 Ready for Production?

✅ All manual tests pass  
✅ Error handling works correctly  
✅ Database migration runs smoothly  
✅ No security vulnerabilities found  
✅ Performance is acceptable  

**Then you're ready to deploy!** 🎉