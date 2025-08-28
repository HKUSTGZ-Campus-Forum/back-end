# Nginx + Flask URL Routing Configuration

## ⚠️ IMPORTANT: Avoid Double `/api` Prefixes

### Current Setup
- **Nginx Configuration**: Routes `/api/*` requests to Flask backend
- **Flask Blueprints**: Should use URL prefixes WITHOUT `/api`

### ❌ Wrong Pattern (Causes Double Prefix)
```python
# This creates /api/api/identities/* routes
identity_bp = Blueprint('identity', __name__, url_prefix='/api/identities')
```
**Result**: `https://domain.com/api/api/identities/types` ❌

### ✅ Correct Pattern  
```python
# Nginx handles /api, Flask handles the rest
identity_bp = Blueprint('identity', __name__, url_prefix='/identities')
```
**Result**: `https://domain.com/api/identities/types` ✅

## Current Blueprint URL Prefixes
All blueprints should use these patterns:

```python
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')           # → /api/auth/*
user_bp = Blueprint('user', __name__, url_prefix='/users')         # → /api/users/*  
post_bp = Blueprint('post', __name__, url_prefix='/posts')         # → /api/posts/*
identity_bp = Blueprint('identity', __name__, url_prefix='/identities')  # → /api/identities/*
```

## Debugging Double Prefix Issues
If a new route gives 404:
1. **Check the blueprint prefix** - should NOT include `/api`
2. **Test with double prefix** - `curl /api/api/your-route`
3. **Fix by removing `/api` from blueprint prefix**

## Why This Happens
- **Separation of concerns**: Nginx handles external routing, Flask handles internal routing
- **Proxy setup**: Nginx strips `/api` and forwards the rest to Flask
- **Blueprint registration**: Flask adds blueprint prefix to the forwarded path

This pattern keeps routing configuration clean and avoids conflicts between proxy and application layers.