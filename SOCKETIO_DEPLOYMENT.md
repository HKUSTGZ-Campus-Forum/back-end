# SocketIO Deployment Guide

This application now supports WebSocket real-time communication via Socket.IO. Special deployment considerations are required.

## 🚨 Important Changes

### For Development
The existing `run.py` script now uses SocketIO instead of regular Flask:
```bash
python run.py
```

### For Production Deployment

#### Option 1: Using the SocketIO Startup Script (Recommended)
```bash
python start_socketio.py
```

#### Option 2: Update Systemd Service
The systemd service (`dev-unikorn-api.service`) needs to be updated to use SocketIO instead of gunicorn.

**Current service probably runs:**
```bash
gunicorn --bind 0.0.0.0:8000 --workers 4 run:app
```

**Should be changed to:**
```bash
python /data/dev_unikorn/back-end/start_socketio.py
```

Or using the run.py script:
```bash
python /data/dev_unikorn/back-end/run.py
```

#### Option 3: Gunicorn with Eventlet (Advanced)
If you prefer to keep using gunicorn, install eventlet and use:
```bash
pip install eventlet
gunicorn --worker-class eventlet --workers 1 --bind 0.0.0.0:8000 wsgi:application
```

**Note:** Multiple workers don't work well with SocketIO - use only 1 worker.

## 🔧 Service Configuration Update

To update the systemd service:

1. **Edit the service file:**
   ```bash
   sudo systemctl edit dev-unikorn-api.service
   ```

2. **Update the ExecStart command to:**
   ```ini
   [Service]
   ExecStart=/data/dev_unikorn/back-end/venv/bin/python /data/dev_unikorn/back-end/start_socketio.py
   ```

3. **Reload and restart:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart dev-unikorn-api.service
   ```

## 🌐 WebSocket Connection

After deployment, the frontend will connect to:
- **Dev:** `wss://dev.unikorn.axfff.com/socket.io/`
- **Prod:** `wss://unikorn.axfff.com/socket.io/`

## 📊 Troubleshooting

### Check if WebSocket is working:
```bash
# Check if the service is running
sudo systemctl status dev-unikorn-api.service

# Check if WebSocket endpoint is accessible
curl -I https://dev.unikorn.axfff.com/socket.io/

# View service logs
sudo journalctl -u dev-unikorn-api.service -f
```

### Common Issues:

1. **"Connection failed"** - Service might be using old Flask app instead of SocketIO
2. **"404 on /socket.io/"** - SocketIO not properly initialized
3. **"CORS errors"** - CORS configuration might need frontend domain

## 📦 Dependencies

Make sure these are in `requirements.txt`:
- `flask-socketio`
- `python-socketio`
- `eventlet` (optional, for gunicorn deployment)

## 🔄 Fallback

If WebSocket fails, the frontend automatically falls back to HTTP polling, so existing functionality is preserved.