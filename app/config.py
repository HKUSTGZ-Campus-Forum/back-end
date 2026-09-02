# app/config.py
import os
import re
from datetime import timedelta
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

# Load .env from the root project directory
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '..', '.env'))


_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})


def get_env_bool(name, default=False):
    """Read a boolean environment variable without silently accepting typos."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    value = raw_value.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(
        f'{name} must be one of: '
        f'{", ".join(sorted(_TRUE_VALUES | _FALSE_VALUES))}'
    )


def get_env_nonnegative_int(name, default):
    """Read a non-negative integer environment variable."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f'{name} must be a non-negative integer') from exc
    if value < 0:
        raise ValueError(f'{name} must be a non-negative integer')
    return value


def normalize_database_config(database_url):
    """Return a SQLAlchemy URI and engine options for DATABASE_URL."""
    if database_url.startswith('postgres://'):
        database_url = 'postgresql://' + database_url[len('postgres://'):]

    url = make_url(database_url)
    query = dict(url.query)
    schema = query.pop('schema', None)
    engine_options = {}

    if schema is not None:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(,[A-Za-z_][A-Za-z0-9_]*)*', schema):
            raise ValueError('invalid database schema name')
        url = url.set(query=query)
        if url.get_backend_name() == 'postgresql':
            engine_options['connect_args'] = {'options': f'-csearch_path={schema}'}

    return url.render_as_string(hide_password=False), engine_options


_database_uri, _database_engine_options = normalize_database_config(
    os.getenv('DATABASE_URL', 'postgres:///app.db')
)


class Config:
    APP_ENV = os.getenv(
        'APP_ENV',
        os.getenv('ENVIRONMENT', os.getenv('FLASK_ENV', 'development')),
    ).strip().lower()
    SECRET_KEY = os.getenv('SECRET_KEY', 'your_default_secret_key')
    SQLALCHEMY_DATABASE_URI = _database_uri
    SQLALCHEMY_ENGINE_OPTIONS = _database_engine_options
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # JWT Configuration
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your_jwt_secret_key')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)  # Short-lived access tokens
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=60)    # Longer-lived refresh tokens
    JWT_BLACKLIST_ENABLED = True
    JWT_BLACKLIST_TOKEN_CHECKS = ['access', 'refresh']
    
    # Base Alibaba Cloud Credentials (Needed for STS client)
    ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID')
    ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
    
    # OSS Configuration
    OSS_ROLE_ARN = os.getenv('OSS_ROLE_ARN')
    OSS_BUCKET_NAME = os.getenv('OSS_BUCKET_NAME')
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT')
    OSS_REGION_ID = os.getenv('OSS_REGION_ID', 'cn-hangzhou')
    OSS_TOKEN_DURATION = int(os.getenv('OSS_TOKEN_DURATION', 3600))
    OSS_PUBLIC_URL = os.getenv('OSS_PUBLIC_URL') or (
        f'https://{os.getenv("OSS_BUCKET_NAME")}.{os.getenv("OSS_ENDPOINT", "").replace("http://", "").replace("https://", "")}'
        if os.getenv("OSS_BUCKET_NAME") and os.getenv("OSS_ENDPOINT") else None
    )
    
    # DirectMail Configuration (uses same Alibaba Cloud credentials)
    ALIBABA_DM_REGION = os.getenv('ALIBABA_DM_REGION', 'ap-southeast-1')
    ALIBABA_DM_ACCOUNT_NAME = os.getenv('ALIBABA_DM_ACCOUNT_NAME', 'no-reply@unikorn.axfff.com')
    ALIBABA_DM_FROM_ALIAS = os.getenv('ALIBABA_DM_FROM_ALIAS', 'uniKorn 校园论坛')
    ALIBABA_CLOUD_EMAIL_SMTP_SECRET = os.getenv('ALIBABA_CLOUD_EMAIL_SMTP_SECRET')
    
    # Email Verification Settings
    EMAIL_VERIFICATION_EXPIRES_MINUTES = int(os.getenv('EMAIL_VERIFICATION_EXPIRES_MINUTES', '10'))
    PASSWORD_RESET_EXPIRES_HOURS = int(os.getenv('PASSWORD_RESET_EXPIRES_HOURS', '1'))
    
    # Frontend URLs for email templates
    FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', 'https://unikorn.axfff.com')

    # HKUST(GZ) Campus SSO (OpenID Connect Authorization Code + PKCE).
    # The public callback values must match the SSO registration exactly.
    CAMPUS_SSO_CLIENT_ID = os.getenv('CAMPUS_SSO_CLIENT_ID', '').strip()
    CAMPUS_SSO_CLIENT_SECRET = os.getenv('CAMPUS_SSO_CLIENT_SECRET', '').strip()
    CAMPUS_SSO_ISSUER = os.getenv(
        'CAMPUS_SSO_ISSUER',
        'https://devsso.hkust-gz.edu.cn',
    ).rstrip('/')
    CAMPUS_SSO_METADATA_URL = os.getenv(
        'CAMPUS_SSO_METADATA_URL',
        f'{CAMPUS_SSO_ISSUER}/.well-known/openid-configuration',
    )
    CAMPUS_SSO_END_SESSION_ENDPOINT = os.getenv(
        'CAMPUS_SSO_END_SESSION_ENDPOINT',
        f'{CAMPUS_SSO_ISSUER}/connect/endsession',
    )
    CAMPUS_SSO_REDIRECT_URI = os.getenv(
        'CAMPUS_SSO_REDIRECT_URI',
        'https://unikorn.hkust-gz.edu.cn/api/auth/oidc/callback',
    )
    CAMPUS_SSO_POST_LOGOUT_REDIRECT_URI = os.getenv(
        'CAMPUS_SSO_POST_LOGOUT_REDIRECT_URI',
        'https://unikorn.hkust-gz.edu.cn/',
    )
    CAMPUS_SSO_SCOPES = os.getenv(
        'CAMPUS_SSO_SCOPES',
        'openid profile',
    ).strip()
    CAMPUS_SSO_ENABLED = get_env_bool(
        'CAMPUS_SSO_ENABLED',
        bool(CAMPUS_SSO_CLIENT_ID and CAMPUS_SSO_CLIENT_SECRET),
    )
    CAMPUS_SSO_LOGIN_TICKET_TTL_SECONDS = int(os.getenv(
        'CAMPUS_SSO_LOGIN_TICKET_TTL_SECONDS',
        '120',
    ))
    CAMPUS_SSO_ID_TOKEN_COOKIE_NAME = os.getenv(
        'CAMPUS_SSO_ID_TOKEN_COOKIE_NAME',
        'unikorn_oidc_id_token',
    )
    CAMPUS_SSO_COOKIE_PATH = os.getenv(
        'CAMPUS_SSO_COOKIE_PATH',
        '/api/auth',
    )
    CAMPUS_SSO_COOKIE_SECURE = get_env_bool(
        'CAMPUS_SSO_COOKIE_SECURE',
        APP_ENV in {'prod', 'production'},
    )

    # Authlib keeps short-lived OAuth state, nonce, and PKCE verifier values in
    # the signed Flask session cookie. Keep that cookie inaccessible to scripts
    # and scoped to same-site top-level redirects.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = get_env_bool(
        'SESSION_COOKIE_SECURE',
        APP_ENV in {'prod', 'production'},
    )
    
    # Redis Configuration for Caching
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    # Cache Configuration
    CACHE_TYPE = os.getenv('CACHE_TYPE', 'redis')
    CACHE_REDIS_URL = REDIS_URL
    CACHE_DEFAULT_TIMEOUT = 2700  # 45 minutes (45*60 seconds)
    
    # File URL Cache Settings
    FILE_URL_CACHE_TIMEOUT = 2700  # 45 minutes - shorter than 1hr URL expiry
    FILE_URL_CACHE_KEY_PREFIX = 'file_url:'

    # AI/ML Service Configuration
    DASHSCOPE_API_KEY = os.getenv('DASHSCOPE_API_KEY')
    DASHVECTOR_API_KEY = os.getenv('DASHVECTOR_API_KEY')
    DASHVECTOR_ENDPOINT = os.getenv('DASHVECTOR_ENDPOINT')

    # NODE recruitment one-prompt agent challenge. The agent is deliberately
    # restricted to an in-process virtual target; these values only control the
    # model loop and the one-attempt receipt lifetime.
    RECRUITMENT_CHALLENGE_ENABLED = get_env_bool(
        'RECRUITMENT_CHALLENGE_ENABLED', True
    )
    RECRUITMENT_AGENT_MODEL = os.getenv(
        'RECRUITMENT_AGENT_MODEL', 'qwen-plus'
    ).strip()
    RECRUITMENT_AGENT_MAX_ROUNDS = max(
        1, get_env_nonnegative_int('RECRUITMENT_AGENT_MAX_ROUNDS', 8)
    )
    RECRUITMENT_AGENT_MAX_TOOL_CALLS = max(
        1, get_env_nonnegative_int('RECRUITMENT_AGENT_MAX_TOOL_CALLS', 20)
    )
    RECRUITMENT_AGENT_TIMEOUT_SECONDS = max(
        1, get_env_nonnegative_int('RECRUITMENT_AGENT_TIMEOUT_SECONDS', 12)
    )
    RECRUITMENT_ATTEMPT_TTL_SECONDS = max(
        60, get_env_nonnegative_int('RECRUITMENT_ATTEMPT_TTL_SECONDS', 2592000)
    )

    # OpenAI-compatible forum assistant provider. Keep the API key server-side.
    AGENT_BASE_URL = os.getenv('AGENT_BASE_URL', '').strip().rstrip('/')
    AGENT_API_KEY = os.getenv('AGENT_API_KEY', '').strip()
    AGENT_MODEL = os.getenv('AGENT_MODEL', '').strip()
    AGENT_ENABLED = get_env_bool(
        'AGENT_ENABLED',
        bool(AGENT_BASE_URL and AGENT_API_KEY and AGENT_MODEL),
    )
    AGENT_SYSTEM_PROMPT = os.getenv('AGENT_SYSTEM_PROMPT', '').strip()
    AGENT_TIMEOUT_SECONDS = int(os.getenv('AGENT_TIMEOUT_SECONDS', '60'))
    AGENT_MAX_MESSAGE_CHARS = int(os.getenv('AGENT_MAX_MESSAGE_CHARS', '4000'))
    AGENT_MAX_OUTPUT_TOKENS = int(os.getenv('AGENT_MAX_OUTPUT_TOKENS', '800'))
    AGENT_CONTEXT_MESSAGES = int(os.getenv('AGENT_CONTEXT_MESSAGES', '20'))
    AGENT_REQUESTS_PER_MINUTE = int(os.getenv('AGENT_REQUESTS_PER_MINUTE', '10'))
    AGENT_CLIENT_PROVIDER_ENABLED = get_env_bool('AGENT_CLIENT_PROVIDER_ENABLED', True)
    AGENT_CLIENT_PROVIDER_ALLOW_PRIVATE_BASE_URLS = get_env_bool(
        'AGENT_CLIENT_PROVIDER_ALLOW_PRIVATE_BASE_URLS',
        False,
    )

    # Web Push Configuration
    VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY')
    VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY')
    VAPID_EMAIL = os.getenv('VAPID_EMAIL', 'admin@example.com')

    # Background Tasks Configuration
    AUTO_INIT_ON_STARTUP = get_env_bool('AUTO_INIT_ON_STARTUP', True)
    ENABLE_BACKGROUND_TASKS = get_env_bool('ENABLE_BACKGROUND_TASKS', True)
    EMBEDDING_MAINTENANCE_INTERVAL_MINUTES = int(os.getenv('EMBEDDING_MAINTENANCE_INTERVAL_MINUTES', '60'))  # 1 hour default
    EMBEDDING_MAINTENANCE_BATCH_SIZE = int(os.getenv('EMBEDDING_MAINTENANCE_BATCH_SIZE', '50'))
    EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES = int(os.getenv('EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES', '30'))

    # SISN data is fetched through the IP-allowlisted CoursePlan server.  The
    # shared secret authenticates only UniKorn's internal proxy request; the
    # school credentials never enter this application or its database.
    SISN_PROXY_BASE_URL = os.getenv('SISN_PROXY_BASE_URL', '').strip().rstrip('/')
    SISN_PROXY_SHARED_SECRET = os.getenv('SISN_PROXY_SHARED_SECRET', '').strip()
    SISN_PROXY_TIMEOUT_SECONDS = int(os.getenv('SISN_PROXY_TIMEOUT_SECONDS', '75'))
    SISN_SYNC_TERM = os.getenv('SISN_SYNC_TERM', '2610').strip()
    SISN_SYNC_BASELINE_PATH = os.getenv(
        'SISN_SYNC_BASELINE_PATH',
        os.path.join(basedir, 'data', 'pending', 'scheduler_offerings', '26-27fall.json'),
    )
    SISN_SYNC_ARCHIVE_DIR = os.getenv('SISN_SYNC_ARCHIVE_DIR', '').strip()
    SISN_SYNC_ARCHIVE_RETENTION_FILES = int(os.getenv('SISN_SYNC_ARCHIVE_RETENTION_FILES', '336'))
    SISN_SYNC_MIN_SOURCE_COURSES = int(os.getenv('SISN_SYNC_MIN_SOURCE_COURSES', '300'))
    SISN_SYNC_MAX_SOURCE_COURSES = int(os.getenv('SISN_SYNC_MAX_SOURCE_COURSES', '600'))
    SISN_SYNC_MIN_SOURCE_CLASSES = int(os.getenv('SISN_SYNC_MIN_SOURCE_CLASSES', '650'))
    SISN_SYNC_MAX_SOURCE_CLASSES = int(os.getenv('SISN_SYNC_MAX_SOURCE_CLASSES', '1200'))
    SISN_SYNC_MIN_SOURCE_SCHEDULES = int(os.getenv('SISN_SYNC_MIN_SOURCE_SCHEDULES', '700'))
    SISN_SYNC_MAX_SOURCE_SCHEDULES = int(os.getenv('SISN_SYNC_MAX_SOURCE_SCHEDULES', '1800'))
    SISN_SYNC_MIN_CANDIDATE_SECTIONS = int(os.getenv('SISN_SYNC_MIN_CANDIDATE_SECTIONS', '650'))
    SISN_SYNC_MAX_FALLBACK_MAIN_CLASSES = int(os.getenv('SISN_SYNC_MAX_FALLBACK_MAIN_CLASSES', '23'))
    SISN_SYNC_MAX_MISSING_BASELINE_CLASSES = int(os.getenv('SISN_SYNC_MAX_MISSING_BASELINE_CLASSES', '50'))
    SISN_SYNC_MAX_OMITTED_UNSCHEDULED_CLASSES = int(os.getenv('SISN_SYNC_MAX_OMITTED_UNSCHEDULED_CLASSES', '50'))
    SISN_SYNC_MAX_BASELINE_MEETING_FALLBACK_SECTIONS = int(
        os.getenv('SISN_SYNC_MAX_BASELINE_MEETING_FALLBACK_SECTIONS', '50')
    )

    # Authoritative catalog rules are deliberately synchronized separately from
    # SISN offering/quota snapshots. Enabling apply is an operator decision
    # because the first run creates product catalog data.
    COURSE_CATALOG_SYNC_URL = os.getenv(
        'COURSE_CATALOG_SYNC_URL',
        'https://pcc.hkust-gz.edu.cn/api/bdp/pg-course-catalog',
    ).strip()
    COURSE_CATALOG_SYNC_TERM = os.getenv(
        'COURSE_CATALOG_SYNC_TERM', SISN_SYNC_TERM
    ).strip()
    COURSE_CATALOG_SYNC_CAREER = os.getenv('COURSE_CATALOG_SYNC_CAREER', 'UG').strip()
    COURSE_CATALOG_SYNC_TIMEOUT_SECONDS = int(os.getenv('COURSE_CATALOG_SYNC_TIMEOUT_SECONDS', '30'))
    COURSE_CATALOG_SYNC_MIN_COURSES = int(os.getenv('COURSE_CATALOG_SYNC_MIN_COURSES', '150'))
    COURSE_CATALOG_SYNC_MAX_COURSES = int(os.getenv('COURSE_CATALOG_SYNC_MAX_COURSES', '500'))
    COURSE_CATALOG_SYNC_ENABLED = get_env_bool('COURSE_CATALOG_SYNC_ENABLED', False)
    COURSE_CATALOG_SYNC_INTERVAL_HOURS = int(os.getenv('COURSE_CATALOG_SYNC_INTERVAL_HOURS', '6'))
    # The school network cannot be reached from the public UniKorn hosts. The
    # allowlisted school server therefore pushes signed snapshots outward.
    # Production is fail-closed unless explicitly enabled by its operators.
    SISN_PUSH_INGEST_ENABLED = get_env_bool(
        'SISN_PUSH_INGEST_ENABLED',
        APP_ENV not in {'prod', 'production'},
    )
    SISN_PUSH_PUBLIC_KEY_PATH = os.getenv(
        'SISN_PUSH_PUBLIC_KEY_PATH',
        os.path.join(basedir, 'data', 'sisn_push_public_key.pem'),
    ).strip()
    SISN_PUSH_MAX_BODY_BYTES = int(os.getenv('SISN_PUSH_MAX_BODY_BYTES', str(8 * 1024 * 1024)))
    SISN_PUSH_MAX_AGE_SECONDS = int(os.getenv('SISN_PUSH_MAX_AGE_SECONDS', '300'))

    # Forwarded headers are untrusted unless a deployment opts in explicitly.
    # Separate counts avoid assuming every trusted proxy appends every header.
    _LEGACY_TRUSTED_PROXY_HOPS = get_env_nonnegative_int('TRUSTED_PROXY_HOPS', 0)
    TRUSTED_PROXY_FOR_HOPS = get_env_nonnegative_int(
        'TRUSTED_PROXY_FOR_HOPS',
        _LEGACY_TRUSTED_PROXY_HOPS,
    )
    TRUSTED_PROXY_PROTO_HOPS = get_env_nonnegative_int(
        'TRUSTED_PROXY_PROTO_HOPS',
        _LEGACY_TRUSTED_PROXY_HOPS,
    )
