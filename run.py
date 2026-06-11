# run.py
import logging
from app import create_app
from flask_cors import CORS

# Set up logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = create_app()


class ApiPrefixMiddleware:
    """Allow the local Flask dev server to accept frontend /api/* requests."""

    def __init__(self, wrapped_app):
        self.wrapped_app = wrapped_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/api":
            environ["PATH_INFO"] = "/"
        elif path.startswith("/api/"):
            environ["PATH_INFO"] = path[4:]
        return self.wrapped_app(environ, start_response)


app.wsgi_app = ApiPrefixMiddleware(app.wsgi_app)
CORS(app,
     resources={r"/*":
                    {"origins": [
                        "http://localhost:3000",
                        "http://localhost:3002",
                        "http://localhost:3003",
                        "http://127.0.0.1:3000",
                        "http://127.0.0.1:3002",
                        "http://127.0.0.1:3003",
                        "https://dev.unikorn.axfff.com",
                        "https://unikorn.axfff.com",
                    ]
                    }},
     supports_credentials=True)

if __name__ == '__main__':
    app.run(debug=True, port=8000)
