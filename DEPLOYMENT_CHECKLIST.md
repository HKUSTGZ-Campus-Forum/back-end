# SocketIO Deployment Checklist

## 🔒 Security-First Deployment

This deployment implements **environment-specific security**:
- **Production**: Only allows `unikorn.axfff.com` and `www.unikorn.axfff.com`
- **Development**: Only allows `dev.unikorn.axfff.com` and `localhost:3000`

## 🚨 One-Time Manual Setup Required

After deploying the SocketIO code, the systemd service needs to be updated **once** to support WebSockets.

### Current Service Configuration (Probably):
```bash
# Current command in systemd service:
gunicorn --bind 0.0.0.0:8000 --workers 4 run:app
```

### Required Update:
```bash
# New command needed:
gunicorn --config /data/dev_unikorn/back-end/gunicorn.conf.py wsgi:application
```

## 📋 Manual Steps on Server

### 1. Check Current Service Configuration
```bash
sudo systemctl cat dev-unikorn-api.service
```

### 2. Update Service Configuration  
```bash
sudo systemctl edit dev-unikorn-api.service
```

Add these overrides:
```ini
[Service]
ExecStart=
ExecStart=/data/dev_unikorn/back-end/venv/bin/gunicorn --config /data/dev_unikorn/back-end/gunicorn.conf.py wsgi:application
WorkingDirectory=/data/dev_unikorn/back-end
```

### 3. Create Log Directories
```bash
sudo mkdir -p /var/log/gunicorn /var/run/gunicorn
sudo chown $(whoami):$(whoami) /var/log/gunicorn /var/run/gunicorn
```

### 4. Set Environment Variable (IMPORTANT)
```bash
# For DEV server
sudo systemctl edit dev-unikorn-api.service
# Add:
[Service]
Environment=FLASK_ENV=development

# For PROD server  
sudo systemctl edit prod-unikorn-api.service
# Add:
[Service]
Environment=FLASK_ENV=production
```

### 5. Reload and Test
```bash
sudo systemctl daemon-reload
sudo systemctl restart dev-unikorn-api.service
sudo systemctl status dev-unikorn-api.service
```

### 6. Verify Security & WebSocket Endpoint
```bash
# Check environment is correctly set
curl -I https://dev.unikorn.axfff.com/socket.io/
# Should return 200 instead of 404

# Verify CORS security (should reject unauthorized origins)
curl -H "Origin: https://malicious-site.com" -I https://dev.unikorn.axfff.com/api/analytics/hot-posts
# Should be rejected in production
```

## 🔄 Alternative: Minimal Change Approach

If you prefer minimal changes, just update the ExecStart to use our updated `run.py`:

```ini
[Service]
ExecStart=
ExecStart=/data/dev_unikorn/back-end/venv/bin/python /data/dev_unikorn/back-end/run.py
```

This works but gunicorn+eventlet is more production-ready.

## ✅ After Manual Setup

Once the service is updated, future deployments via GitHub workflow will work automatically - no more manual intervention needed.

## 🧪 Test Commands

```bash
# Test SocketIO endpoint
python /data/dev_unikorn/back-end/test_socketio.py

# Check service logs
sudo journalctl -u dev-unikorn-api.service -f
```