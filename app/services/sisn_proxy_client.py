from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class SisnProxyConfigurationError(RuntimeError):
    pass


class SisnProxyRequestError(RuntimeError):
    pass


def _encode(value: str) -> str:
    return quote(value, safe="~()*!.'-_")


def canonical_query(parameters: dict[str, str]) -> str:
    return "&".join(
        f"{_encode(key)}={_encode(value)}"
        for key, value in sorted(parameters.items())
    )


def signature_payload(
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    query: str,
) -> str:
    return "\n".join(("v1", timestamp, nonce, method.upper(), path, query))


def sign_request(
    secret: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    query: str,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        signature_payload(timestamp, nonce, method, path, query).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class SisnProxyClient:
    base_url: str
    shared_secret: str
    timeout_seconds: int = 75

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise SisnProxyConfigurationError("SISN proxy base URL must be HTTPS")
        if len(self.shared_secret) < 32:
            raise SisnProxyConfigurationError(
                "SISN proxy shared secret must contain at least 32 characters"
            )
        if self.timeout_seconds < 5 or self.timeout_seconds > 180:
            raise SisnProxyConfigurationError(
                "SISN proxy timeout must be between 5 and 180 seconds"
            )

    def fetch_class_quota(
        self,
        *,
        term: str,
        subject: str | None = None,
        course_code: str | None = None,
    ) -> dict[str, Any]:
        parameters = {"term": term}
        if subject:
            parameters["subject"] = subject
        if course_code:
            parameters["crseCode"] = course_code
        query = canonical_query(parameters)
        base = urlsplit(self.base_url.rstrip("/"))
        path = (base.path.rstrip("/") + "/api/internal/sisn/class-quota") or "/"
        url = urlunsplit((base.scheme, base.netloc, path, query, ""))
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        signature = sign_request(
            self.shared_secret,
            timestamp,
            nonce,
            "GET",
            path,
            query,
        )
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "UniKorn-SISN-Sync/1.0",
                "X-UniKorn-Timestamp": timestamp,
                "X-UniKorn-Nonce": nonce,
                "X-UniKorn-Signature": signature,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = response.status
                body = response.read()
        except HTTPError as exc:
            status = exc.code
            body = exc.read()
        except URLError as exc:
            raise SisnProxyRequestError("SISN proxy is unreachable") from exc
        if status != 200:
            raise SisnProxyRequestError(f"SISN proxy returned HTTP {status}")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SisnProxyRequestError("SISN proxy returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SisnProxyRequestError("SISN proxy returned a non-object payload")
        return payload
