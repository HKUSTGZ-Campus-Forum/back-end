# OAuth SSO Implementation Guide

## 🎯 Overview

OAuth2 server has been implemented on the main campus forum site to enable single sign-on (SSO) with CoursePlan.search and future integrations.

## 🔧 Backend Setup (Main Site)

### 1. Install Dependencies
```bash
cd /Users/zhaoj/Project/campusForum/back-end
pip install -r requirements.txt
```

### 2. Run Database Migration
```bash
flask db upgrade
```

### 3. Register CoursePlan.search as OAuth Client
```bash
cd /Users/zhaoj/Project/campusForum/back-end
python -m app.scripts.setup_oauth_client
```

This will output:
- **Client ID**: Save to CoursePlan.search environment
- **Client Secret**: Save to CoursePlan.search environment  

## 🌐 OAuth Endpoints (Main Site)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/oauth/authorize` | GET/POST | Authorization & consent screen |
| `/oauth/token` | POST | Exchange code for access token |
| `/oauth/userinfo` | GET | Get user profile data |
| `/oauth/revoke` | POST | Revoke access tokens |
| `/oauth/clients` | GET/POST | Manage OAuth clients (admin) |

## 🔑 OAuth Scopes

- **`profile`**: Basic user info (username, avatar, role)
- **`email`**: Email address access
- **`courses`**: Course enrollment data (future)

## 🎮 User Flow

1. User clicks "Login with Campus Forum" on CoursePlan.search
2. Redirects to main site `/oauth/authorize` 
3. User sees consent screen with requested permissions
4. User approves → generates authorization code
5. CoursePlan.search exchanges code for access token
6. CoursePlan.search calls `/oauth/userinfo` to get user data
7. User is logged into CoursePlan.search with campus forum account

## 🔄 Next Steps for CoursePlan.search

1. **Replace Better Auth** with OAuth2 client
2. **Add environment variables**:
   ```env
   CAMPUS_FORUM_CLIENT_ID=[from setup script]
   CAMPUS_FORUM_CLIENT_SECRET=[from setup script]
   CAMPUS_FORUM_AUTH_URL=https://dev.unikorn.axfff.com/api/oauth/authorize
   CAMPUS_FORUM_TOKEN_URL=https://dev.unikorn.axfff.com/api/oauth/token
   CAMPUS_FORUM_USERINFO_URL=https://dev.unikorn.axfff.com/api/oauth/userinfo
   ```
3. **Update authentication flow** to use OAuth2
4. **Sync user data** from campus forum

## 🚀 Future Benefits

- ✅ Single sign-on across all campus tools
- ✅ Easy integration for new tools
- ✅ Third-party app support
- ✅ Mobile app authentication
- ✅ Secure API access for partners

## 🛡️ Security Features

- **PKCE Support**: Protects against authorization code interception
- **Token Expiration**: Access tokens expire in 1 hour
- **Scope Validation**: Only requested permissions granted
- **Client Validation**: Strict redirect URI checking
- **Rate Limiting**: Prevents abuse

The OAuth server is now ready! Next step is implementing the OAuth client on CoursePlan.search.