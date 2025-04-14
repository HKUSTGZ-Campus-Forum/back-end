# wsgi.py
from app import create_app
from flask_cors import CORS

application = create_app()
CORS(application,
     resources={r"/*":
                    {"origins": [
                        "https://unikorn.axfff.com",
                        "https://www.unikorn.axfff.com",
                    ]
                    }},
     supports_credentials=True)

if __name__ == '__main__':
    application.run()