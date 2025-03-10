# back-end
api server of the forum

```
./
├── app/
│   ├── __init__.py           # Application factory, blueprint registration
│   ├── config.py             # Configuration settings (env-specific)
│   ├── extensions.py         # Initialize extensions (db, jwt, etc.)
│   ├── models/
│   │   ├── __init__.py       # Import all models here
│   │   ├── user.py           # Example: User model
│   │   ├── post.py           # Example: Post model
│   │   └── comment.py        # Example: Comment model
│   ├── routes/
│   │   ├── __init__.py       # Register blueprints for each resource
│   │   ├── auth.py           # Authentication-related endpoints
│   │   ├── user.py           # User endpoints
│   │   ├── post.py           # Post endpoints
│   │   └── comment.py        # Comment endpoints
│   └── services/
│       ├── __init__.py       # Import service modules
│       ├── auth_service.py   # Business logic for authentication
│       ├── post_service.py   # Business logic for posts
│       └── file_service.py   # Business logic for OSS file operations
├── migrations/               # Database migration files (e.g., Flask-Migrate)
├── tests/
│   ├── __init__.py
│   ├── test_auth.py          # Tests for auth endpoints/services
│   ├── test_user.py          # Tests for user endpoints
│   └── test_post.py          # Tests for post endpoints
├── .env                      # Environment variables (secret keys, DB URL, etc.)
├── requirements.txt          # Python package dependencies
├── run.py                    # Entry point to run the application
├── Dockerfile                # Dockerfile for containerization
└── README.md                 # Project documentation

```
