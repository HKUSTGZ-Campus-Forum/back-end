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
CORS(app,
     resources={r"/*":
                    {"origins": [
                        "http://localhost:3000",
                        "http://127.0.0.1:3000",
                        "https://dev.unikorn.axfff.com",
                        "https://unikorn.axfff.com",
                    ]
                    }},
     supports_credentials=True)

if __name__ == '__main__':
    app.run(debug=True, port=8000)
