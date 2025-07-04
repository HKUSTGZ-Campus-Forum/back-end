# redis_socketio.py - Redis configuration for multi-server SocketIO
"""
For scaling SocketIO across multiple servers, use Redis as message queue.
This is needed only if you plan to run multiple instances.
"""

from app.extensions import socketio
import os

# Configure Redis for SocketIO scaling (optional)
def configure_redis_socketio():
    """Configure Redis for SocketIO message passing between servers"""
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
    
    if redis_url and redis_url != 'disabled':
        try:
            socketio.init_app(
                message_queue=redis_url,
                cors_allowed_origins="*"
            )
            print(f"✅ SocketIO configured with Redis: {redis_url}")
        except Exception as e:
            print(f"⚠️ Failed to configure Redis for SocketIO: {e}")
            print("🔄 Falling back to single-server mode")
    else:
        print("📡 SocketIO running in single-server mode")

# Add to requirements.txt if using Redis:
# redis>=4.0.0
# redis-py-cluster