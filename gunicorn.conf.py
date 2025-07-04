# gunicorn.conf.py - Production configuration for SocketIO
import multiprocessing
import os

# Environment-based configuration
env = os.getenv('FLASK_ENV', 'production')

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
# IMPORTANT: SocketIO requires single worker or proper session management
workers = 1  # Must be 1 for SocketIO without Redis
worker_class = "eventlet"
worker_connections = 1000
timeout = 60
keepalive = 2

# Restart workers - more aggressive in production
max_requests = 1500 if env == 'production' else 1000
max_requests_jitter = 100 if env == 'production' else 50
preload_app = True

# Logging - environment specific
if env == 'production':
    errorlog = "/var/log/gunicorn/error.log"
    loglevel = "warning"
    accesslog = "/var/log/gunicorn/access.log"
else:
    errorlog = "-"  # stderr
    loglevel = "info"
    accesslog = "-"  # stdout

access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = f'unikorn-socketio-{env}'

# Server mechanics
daemon = False
pidfile = f'/var/run/gunicorn/unikorn-{env}.pid'
user = None
group = None
tmp_upload_dir = None

# Security headers
def on_starting(server):
    """Configure security settings on startup"""
    server.log.info(f"Starting SocketIO server in {env} mode")
    
    # Log security configuration
    if env == 'production':
        server.log.warning("Production mode: Strict CORS policy enforced")
    else:
        server.log.info("Development mode: Permissive CORS for testing")

# SSL (configure for production)
# keyfile = "/path/to/ssl/keyfile.pem"
# certfile = "/path/to/ssl/certfile.pem"