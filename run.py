# run.py
from app import create_app
from app.extensions import socketio
from app.config_security import SecurityConfig
from flask_cors import CORS

app = create_app()

# Configure CORS with environment-specific origins
CORS(app,
     resources={r"/*": {
         "origins": SecurityConfig.get_allowed_origins()
     }},
     supports_credentials=True)

if __name__ == '__main__':
    # Use SocketIO's run method instead of Flask's
    socketio.run(app, debug=True, port=8000, host='0.0.0.0')
