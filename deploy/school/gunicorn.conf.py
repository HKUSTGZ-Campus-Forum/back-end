"""Gunicorn production configuration for the school deployment."""

bind = "127.0.0.1:8001"
workers = 2
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True

# %(U)s logs only the path, deliberately excluding the query string. OIDC
# authorization codes and one-time login tickets must never enter journald.
access_log_format = (
    '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" '
    '%(s)s %(b)s "%(f)s" "%(a)s"'
)
