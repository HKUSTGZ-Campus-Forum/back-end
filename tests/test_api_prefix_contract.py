from app import create_app


class PrefixContractConfig:
    TESTING = True
    SECRET_KEY = "prefix-contract-secret"
    JWT_SECRET_KEY = "prefix-contract-jwt-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CACHE_TYPE = "SimpleCache"
    REDIS_URL = "redis://unused.example:6379/0"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    TRUSTED_PROXY_HOPS = 0
    TRUSTED_PROXY_FOR_HOPS = 0
    TRUSTED_PROXY_PROTO_HOPS = 0


def test_wsgi_routes_leave_the_external_api_prefix_to_the_edge_proxy():
    app = create_app(PrefixContractConfig)

    embedded_api_routes = sorted(
        rule.rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/api")
    )

    assert embedded_api_routes == []
