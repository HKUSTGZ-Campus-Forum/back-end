from __future__ import annotations

import base64
import binascii
import hashlib
import re
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key


class SisnPushAuthenticationError(RuntimeError):
    """A school-server push could not be authenticated."""


_NONCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,80}")
_SIGNATURE_VERSION = "v1"


def canonical_push_message(*, timestamp: str, nonce: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join((
        _SIGNATURE_VERSION,
        timestamp,
        nonce,
        "POST",
        "/scheduler/internal/sisn-ingest",
        body_hash,
    )).encode("ascii")


def verify_push_request(
    *,
    public_key_path: Path,
    timestamp: str,
    nonce: str,
    signature: str,
    body: bytes,
    now: int | None = None,
    max_age_seconds: int = 300,
) -> None:
    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise SisnPushAuthenticationError("invalid request timestamp") from exc
    current_time = int(time.time()) if now is None else now
    if abs(current_time - timestamp_value) > max_age_seconds:
        raise SisnPushAuthenticationError("request timestamp is outside the allowed window")
    if not _NONCE_PATTERN.fullmatch(nonce or ""):
        raise SisnPushAuthenticationError("invalid request nonce")
    try:
        padded_signature = signature + "=" * (-len(signature) % 4)
        signature_bytes = base64.urlsafe_b64decode(padded_signature.encode("ascii"))
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise SisnPushAuthenticationError("invalid request signature encoding") from exc
    try:
        public_key = load_pem_public_key(public_key_path.read_bytes())
        public_key.verify(
            signature_bytes,
            canonical_push_message(timestamp=timestamp, nonce=nonce, body=body),
        )
    except (OSError, ValueError, TypeError) as exc:
        raise SisnPushAuthenticationError("SISN push verifier is not configured") from exc
    except InvalidSignature as exc:
        raise SisnPushAuthenticationError("invalid request signature") from exc
