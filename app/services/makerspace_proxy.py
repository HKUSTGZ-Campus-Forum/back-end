"""HTTP gateway for opaque-origin, browser-bound creator sessions.

Neither user JWTs nor host cookies reach a creator process. A CSP sandbox is
enforced on HTTP responses as well as on the host iframe (including direct visits).
"""
from html import escape
from html.parser import HTMLParser
import json
import re
import secrets
from datetime import timedelta
from urllib.parse import quote, urlsplit

from flask import Response, current_app, request
import requests

from app.extensions import db
from app.models.makerspace import now
from app.services.makerspace_service import MakerError, runtime_session, bootstrap_session, hash_value


class ScopedHTML(HTMLParser):
    def __init__(self, prefix):
        super().__init__(convert_charrefs=False)
        self.prefix, self.parts = prefix, []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        # An application cannot replace the platform base or navigate the host.
        if tag == "base" or (tag == "meta" and values.get("http-equiv", "").lower() in ("refresh", "content-security-policy")):
            return
        for key in ("src", "href", "action", "poster"):
            value = values.get(key)
            if value and value.startswith("/") and not value.startswith("//"):
                values[key] = self.prefix + value.lstrip("/")
        if tag == "script" and values.get("src"):
            values["crossorigin"] = "use-credentials"
        if tag == "link" and values.get("rel") in ("modulepreload", "preload", "stylesheet"):
            values["crossorigin"] = "use-credentials"
        if "target" in values:
            values["target"] = "_self"
        self.parts.append("<" + tag + "".join(" " + key + ("" if value is None else '="' + escape(value, quote=True) + '"') for key, value in values.items()) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag != "base":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append("<!--" + data + "-->")

    def handle_decl(self, decl):
        self.parts.append("<!" + decl + ">")


def scope_html(body, prefix):
    parser = ScopedHTML(prefix)
    parser.feed(body)
    # Applications use relative assets/API calls. This bridge supplies the
    # browser credentials needed by module scripts and fetch from opaque origin.
    bridge = """<base href=__BASE_JSON__><script>
(() => {
  const base = new URL(__BASE_JSON__, location.href);
  const scoped = value => {
    const raw = String(value);
    const url = new URL(raw.startsWith('/') && !raw.startsWith('//') ? raw.slice(1) : raw, base);
    if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname)) throw new Error('Request outside this creative space');
    return url.href;
  };
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => originalFetch(input instanceof Request ? new Request(scoped(input.url), input) : scoped(input), { ...options, credentials: 'include' });
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) { this.withCredentials = true; return open.call(this, method, scoped(url), ...rest); };
  Object.defineProperty(window, 'UNIKORN_SPACE_BASE', { value: base.href });
})();</script>""".replace("__BASE_JSON__", json.dumps(prefix).replace("<", "\\u003c"))
    result = "".join(parser.parts)
    head = re.search(r"<head\b[^>]*>", result, flags=re.I)
    return result[:head.end()] + bridge + result[head.end():] if head else bridge + result


def proxy_runtime(identifier, path):
    if not re.fullmatch(r"[0-9a-f]{48}", identifier):
        raise MakerError("not_found", 404)
    prefix = f"/api/makerspace/run/{identifier}/"
    origin = current_app.config.get("MAKERSPACE_PUBLIC_ORIGIN", "https://unikorn.hkust-gz.edu.cn").rstrip("/")
    source = origin + prefix
    csp = "; ".join(["sandbox allow-scripts allow-forms allow-downloads", "default-src 'none'", f"script-src 'unsafe-inline' {source}", f"style-src 'unsafe-inline' {source}", f"img-src data: blob: {source}", f"font-src data: {source}", f"media-src blob: {source}", f"connect-src {source}", f"base-uri {source}", f"form-action {source}", "frame-ancestors 'self'", "worker-src 'none'", "frame-src 'none'", "object-src 'none'"])
    headers = {"Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store", "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()", "Vary": "Origin, Cookie"}
    if request.headers.get("Origin") == "null":
        headers.update({"Access-Control-Allow-Origin": "null", "Access-Control-Allow-Credentials": "true"})
    if request.method == "OPTIONS":
        # Preflight has no cookie; revealing only CORS policy grants no resource.
        headers.update({"Access-Control-Allow-Methods": "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, Accept", "Access-Control-Max-Age": "0"})
        return Response(status=204, headers=headers)
    if path == "__unikorn_session__" and request.method == "POST":
        if request.content_length and request.content_length > 1024:
            raise MakerError("request_too_large", 413)
        payload = request.get_json(silent=True)
        secret = bootstrap_session(identifier, payload.get("ticket") if isinstance(payload, dict) else None)
        response = Response(status=204, headers=headers)
        response.set_cookie("makerspace_session", secret, max_age=3600, httponly=True, secure=True, samesite="None", partitioned=True, path=prefix)
        return response
    session, item = runtime_session(identifier, request.cookies.get("makerspace_session"))
    if not path and request.method == "GET" and request.args.get("__makerspace_ready") != "1":
        # CHIPS distinguishes first-party and opaque-frame cookie partitions.
        # A trusted bootstrap runs BEFORE creator HTML. It exchanges a one-use
        # ticket for an HttpOnly cookie in the opaque partition, so third-party
        # cookie blocking does not require weakening sandbox isolation.
        ticket = secrets.token_urlsafe(32)
        session.bootstrap_hash, session.bootstrap_expires_at = hash_value(ticket), now() + timedelta(seconds=60)
        db.session.commit()
        script = "fetch(" + json.dumps(prefix + "__unikorn_session__") + ",{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket:" + json.dumps(ticket) + "})}).then(r=>{if(!r.ok)throw Error();location.replace(" + json.dumps(prefix + "?__makerspace_ready=1") + ")}).catch(()=>document.body.textContent='无法打开，请重新进入 / Unable to open; please try again');"
        headers["Content-Type"] = "text/html; charset=utf-8"
        return Response("<!doctype html><meta charset=utf-8><p>正在打开 / Opening…</p><script>" + script + "</script>", headers=headers)
    port = item.runtime_port if session.private else item.public_runtime_port
    if not port or not 20000 <= port < 20100:
        raise MakerError("runtime_unavailable", 503)
    if "\\" in path or any(part == ".." for part in path.split("/")):
        raise MakerError("invalid_path")
    if request.content_length and request.content_length > 8 * 1024 * 1024:
        raise MakerError("request_too_large", 413)
    # Fixed loopback address and server-assigned port; no creator-controlled URL,
    # forwarded Authorization, Cookie, Host, redirect or proxy environment.
    url = f"http://127.0.0.1:{port}/" + quote(path, safe="/-._~")
    if request.query_string:
        from urllib.parse import urlencode
        query = [(key, value) for key, value in request.args.items(multi=True) if key != "__makerspace_ready"]
        if query:
            url += "?" + urlencode(query)
    forwarded = {"Accept": request.headers.get("Accept", "*/*"), "Accept-Encoding": "identity", "Content-Type": request.headers.get("Content-Type", "application/octet-stream"), "X-Forwarded-Prefix": prefix, "X-Makerspace-Deployment": item.id}
    client = requests.Session()
    client.trust_env = False
    try:
        with client.request(request.method, url, headers=forwarded, data=request.get_data(), timeout=(3, 20), allow_redirects=False, stream=True) as upstream:
            parts, length = [], 0
            for chunk in upstream.iter_content(65536):
                length += len(chunk)
                if length > 8 * 1024 * 1024:
                    raise MakerError("response_too_large", 502)
                parts.append(chunk)
            content = b"".join(parts)
            content_type = upstream.headers.get("Content-Type", "application/octet-stream")
            status = upstream.status_code
            if 300 <= status < 400 and upstream.headers.get("Location"):
                location = urlsplit(upstream.headers["Location"])
                if location.scheme or location.netloc or ".." in location.path.split("/"):
                    raise MakerError("external_redirect_blocked", 502)
                headers["Location"] = prefix + location.path.lstrip("/") + ("?" + location.query if location.query else "")
            if "text/html" in content_type:
                content = scope_html(content.decode("utf-8", errors="replace"), prefix).encode()
                content_type = "text/html; charset=utf-8"
            elif "text/css" in content_type:
                content = re.sub(r"url\(\s*(['\"]?)/(?!/)", lambda m: "url(" + m.group(1) + prefix, content.decode("utf-8", errors="replace")).encode()
            headers["Content-Type"] = content_type
            # Strip ALL upstream security/cookie/service-worker headers. Only
            # platform-selected headers above are returned to the browser.
            return Response(content, status=status, headers=headers)
    except requests.RequestException:
        raise MakerError("runtime_unavailable", 503) from None
    finally:
        client.close()
