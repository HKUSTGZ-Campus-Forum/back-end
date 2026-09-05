# API routing

The current routing contract is documented in [Authentication and API boundaries](docs/features/auth-api.md). Flask routes omit the public `/api` prefix; the frontend caller and Nginx configuration must agree on where it is stripped.

Sources: [blueprint registration](app/routes/__init__.py), [school Nginx](deploy/school/nginx/unikorn.conf), [prefix tests](tests/test_api_prefix_contract.py).
