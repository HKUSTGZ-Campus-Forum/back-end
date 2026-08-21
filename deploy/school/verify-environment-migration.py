#!/usr/bin/env python3
"""Compare decrypted source dotenv values with the active systemd environment.

Only key names are emitted. Secret or configuration values are never printed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import dotenv_values


# These values must change for the school topology, trusted ingress, or rotated
# SSO registration. Every other setting present in the former production .env
# must be carried over exactly unless this allowlist is changed in review.
ALLOWED_TO_CHANGE = frozenset({
    "APP_ENV",
    "ENVIRONMENT",
    "FLASK_APP",
    "FLASK_ENV",
    "DATABASE_URL",
    "REDIS_URL",
    "FRONTEND_BASE_URL",
    "NUXT_PUBLIC_API_BASE_URL",
    "NUXT_API_INTERNAL_BASE_URL",
    "TRUSTED_PROXY_HOPS",
    "TRUSTED_PROXY_FOR_HOPS",
    "TRUSTED_PROXY_PROTO_HOPS",
    "SESSION_COOKIE_SECURE",
    "CAMPUS_SSO_ENABLED",
    "CAMPUS_SSO_CLIENT_ID",
    "CAMPUS_SSO_CLIENT_SECRET",
    "CAMPUS_SSO_ISSUER",
    "CAMPUS_SSO_METADATA_URL",
    "CAMPUS_SSO_END_SESSION_ENDPOINT",
    "CAMPUS_SSO_REDIRECT_URI",
    "CAMPUS_SSO_POST_LOGOUT_REDIRECT_URI",
    "CAMPUS_SSO_SCOPES",
    "CAMPUS_SSO_COOKIE_SECURE",
})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_environment")
    args = parser.parse_args()

    source_path = Path(args.source_environment)
    if not source_path.is_absolute() or not source_path.is_file() or source_path.is_symlink():
        raise SystemExit("source environment must be an absolute, non-symlink file")

    parsed = dotenv_values(source_path)
    source = {key: "" if value is None else value for key, value in parsed.items()}
    missing = sorted(key for key in source if key not in os.environ)
    changed = sorted(
        key
        for key, value in source.items()
        if key not in ALLOWED_TO_CHANGE
        and key in os.environ
        and os.environ[key] != value
    )
    if missing or changed:
        messages = []
        if missing:
            messages.append("missing keys: " + ", ".join(missing))
        if changed:
            messages.append("unexpected changed keys: " + ", ".join(changed))
        raise SystemExit("production environment migration mismatch (" + "; ".join(messages) + ")")

    print(
        "production environment migration verified: "
        f"{len(source)} source keys represented; approved topology/SSO overrides only"
    )


if __name__ == "__main__":
    main()
