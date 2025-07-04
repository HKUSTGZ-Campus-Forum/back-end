# wsgi.py
import eventlet
eventlet.monkey_patch()

from app import create_app
from app.extensions import socketio
from app.config_security import SecurityConfig
from flask_cors import CORS

# Create the Flask app
app = create_app()

# Configure CORS with environment-specific origins
CORS(app,
     resources={r"/*": {
         "origins": SecurityConfig.get_allowed_origins()
     }},
     supports_credentials=True)

# For gunicorn deployment with SocketIO support
# The SocketIO app wraps the Flask app
application = socketio

if __name__ == '__main__':
    # Use SocketIO's run method for WebSocket support
    socketio.run(app, debug=False, host='0.0.0.0', port=8000)