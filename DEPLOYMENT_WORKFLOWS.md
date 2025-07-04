# 🚀 Deployment Workflows Summary

## 📋 Updated Workflows

### 🔷 **Development Deployment** (`deploy.yml`)
**Triggers:** Push to `main` branch
**Target:** `dev-unikorn-api.service`
**Environment:** `FLASK_ENV=development`

**Security Configuration:**
```yaml
CORS Origins: [
  "http://localhost:3000",
  "http://127.0.0.1:3000", 
  "https://dev.unikorn.axfff.com"
]
```

**Service Configuration:**
```bash
ExecStart=/data/dev_unikorn/back-end/venv/bin/gunicorn --config gunicorn.conf.py wsgi:application
Environment=FLASK_ENV=development
```

### 🔴 **Production Deployment** (`deploy-backend-prod.yml`)
**Triggers:** Push to `production` branch + manual dispatch
**Target:** `prod-unikorn-api.service`  
**Environment:** `FLASK_ENV=production`

**Security Configuration:**
```yaml
CORS Origins: [
  "https://unikorn.axfff.com",
  "https://www.unikorn.axfff.com"
]
```

**Service Configuration:**
```bash
ExecStart=/data/prod_unikorn/back-end/venv/bin/gunicorn --config gunicorn.conf.py wsgi:application
Environment=FLASK_ENV=production
```

## 🔒 Security Isolation

### **Environment Separation:**
- ✅ **Dev** allows only dev domains + localhost
- ✅ **Prod** allows only production domains
- ❌ **No cross-environment access**

### **Automatic Security Verification:**
Both workflows now verify:
1. ✅ SocketIO endpoint accessibility  
2. ✅ CORS configuration correctness
3. ✅ Environment variable settings
4. ✅ Service health status

## 🔄 Deployment Process

### **Both Workflows Handle:**
1. **Code Update** - Git pull latest changes
2. **Dependencies** - Install SocketIO packages (`eventlet`, `flask-socketio`)
3. **Database** - Run migrations and initialization
4. **Service Setup** - Auto-configure systemd for SocketIO (one-time)
5. **Security** - Set environment-specific CORS origins
6. **Verification** - Test WebSocket endpoints and security

### **One-Time Automatic Setup:**
- Creates log directories: `/var/log/gunicorn`, `/var/run/gunicorn`
- Updates systemd service configuration for SocketIO
- Sets environment variables (`FLASK_ENV`)
- Switches from regular Flask to Gunicorn + Eventlet

## 🎯 Deployment Commands

### **Development:**
```bash
git push origin main
# Automatically deploys to dev.unikorn.axfff.com
```

### **Production:**
```bash
git push origin production
# Automatically deploys to unikorn.axfff.com
# OR trigger manually via GitHub Actions
```

## 🧪 Post-Deployment Verification

### **Development Testing:**
```bash
curl -I https://dev.unikorn.axfff.com/socket.io/
# Should return 200 OK

# Security test
curl -H "Origin: https://malicious.com" -I https://dev.unikorn.axfff.com/api/
# Should be rejected (CORS)
```

### **Production Testing:**
```bash
curl -I https://unikorn.axfff.com/socket.io/
# Should return 200 OK

# Security test  
curl -H "Origin: https://dev.unikorn.axfff.com" -I https://unikorn.axfff.com/api/
# Should be rejected (dev domain blocked in prod)
```

## 📊 Workflow Comparison

| Feature | Development | Production |
|---------|-------------|------------|
| **Trigger** | Push to `main` | Push to `production` |
| **Environment** | `development` | `production` |
| **CORS Origins** | Dev + localhost | Prod only |
| **Debug Logging** | ✅ Enabled | ❌ Disabled |
| **Security Testing** | Basic | Enhanced |
| **Backup Strategy** | None | Database backup |
| **Rollback** | Manual | Automatic on failure |

## 🚀 Ready for Deployment

Both workflows are now:
- ✅ **SocketIO-enabled** - WebSocket support configured
- ✅ **Security-hardened** - Environment isolation enforced  
- ✅ **Auto-configuring** - No manual intervention needed*
- ✅ **Self-verifying** - Built-in health and security checks

*After first deployment that sets up SocketIO service configuration