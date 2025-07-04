#!/bin/bash
# production_start.sh - Production startup script

set -e

echo "🚀 Starting UniKorn SocketIO Server..."

# Navigate to application directory
cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Set production environment
export FLASK_ENV=production
export FLASK_HOST=0.0.0.0
export FLASK_PORT=8000

# Create log directories
sudo mkdir -p /var/log/gunicorn
sudo chown $USER:$USER /var/log/gunicorn

# Create pid directory
sudo mkdir -p /var/run/gunicorn
sudo chown $USER:$USER /var/run/gunicorn

# Start with gunicorn + eventlet for SocketIO support
echo "🔧 Starting with Gunicorn + Eventlet..."
exec gunicorn --config gunicorn.conf.py wsgi:application