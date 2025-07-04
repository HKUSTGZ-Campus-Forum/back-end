#!/usr/bin/env python3
"""
Production startup script for SocketIO-enabled Flask application.
This script should be used instead of regular gunicorn for WebSocket support.
"""

import os
import sys
from app import create_app
from app.extensions import socketio
from flask_cors import CORS

def main():
    """Start the SocketIO application"""
    app = create_app()
    
    # Configure CORS for production
    CORS(app,
         resources={r"/*": {
             "origins": [
                 "https://unikorn.axfff.com",
                 "https://www.unikorn.axfff.com", 
                 "https://dev.unikorn.axfff.com",
                 "http://localhost:3000",
                 "http://127.0.0.1:3000",
             ]
         }},
         supports_credentials=True)
    
    # Get configuration from environment
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', 8000))
    debug = os.getenv('FLASK_ENV') == 'development'
    
    print(f"🚀 Starting SocketIO server on {host}:{port}")
    print(f"🔧 Debug mode: {debug}")
    print(f"🌍 Environment: {os.getenv('FLASK_ENV', 'production')}")
    
    try:
        # Use SocketIO's run method for WebSocket support
        socketio.run(
            app,
            host=host,
            port=port,
            debug=debug,
            use_reloader=False,  # Disable reloader in production
            log_output=True,
            # Production optimizations
            engineio_logger=False,
            socketio_logger=False
        )
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Server failed to start: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()