from urllib.parse import urlsplit


DEFAULT_WEB_PUSH_HOSTS = frozenset({
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
})

MAX_PUSH_ENDPOINT_LENGTH = 2048
MAX_PUSH_P256DH_KEY_LENGTH = 512
MAX_PUSH_AUTH_KEY_LENGTH = 256
MAX_PUSH_USER_AGENT_LENGTH = 512


def is_valid_push_endpoint(endpoint, allowed_hosts=None):
    """Return whether an endpoint is safe to hand to a Web Push client."""
    if not isinstance(endpoint, str):
        return False
    if not endpoint or len(endpoint) > MAX_PUSH_ENDPOINT_LENGTH:
        return False
    if endpoint != endpoint.strip():
        return False
    if any(ord(character) < 0x21 or ord(character) == 0x7F for character in endpoint):
        return False
    if "#" in endpoint:
        return False

    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        return False

    if parsed.scheme.lower() != "https":
        return False
    if not parsed.netloc or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if parsed.fragment or port not in (None, 443):
        return False

    hosts = DEFAULT_WEB_PUSH_HOSTS if allowed_hosts is None else allowed_hosts
    return parsed.hostname.lower() in hosts


def is_valid_push_key(value, max_length):
    """Accept non-empty, bounded Base64URL-like key material."""
    if not isinstance(value, str) or not value or len(value) > max_length:
        return False
    if value != value.strip():
        return False
    return all(
        character.isascii() and (character.isalnum() or character in "-_=")
        for character in value
    )
