"""Browser-facing HKUST(GZ) Campus SSO endpoints."""

from __future__ import annotations

from urllib.parse import urlencode

from flask import Blueprint, current_app, jsonify, redirect, request, session
from flask_jwt_extended import create_access_token, create_refresh_token

from app.services.campus_oidc import (
    CampusOidcError,
    campus_oidc_is_configured,
    consume_login_ticket,
    get_campus_oidc_client,
    issue_login_ticket,
    reconcile_oidc_user,
    sanitize_return_to,
)


bp = Blueprint("campus_oidc", __name__, url_prefix="/auth/oidc")


def _frontend_login_url(params: dict[str, str] | None = None) -> str:
    base_url = str(current_app.config["FRONTEND_BASE_URL"]).rstrip("/")
    locale = session.get("campus_oidc_locale")
    path = "/en/login" if locale == "en" else "/login"
    query = urlencode(params or {})
    return f"{base_url}{path}{'?' + query if query else ''}"


def _error_redirect(code: str):
    session.pop("campus_oidc_return_to", None)
    response = redirect(_frontend_login_url({"oidc_error": code}), code=303)
    session.pop("campus_oidc_locale", None)
    return response


@bp.get("/status")
def status():
    response = jsonify({
        "enabled": campus_oidc_is_configured(),
        "provider": "HKUST(GZ)",
        "flow": "authorization_code_pkce",
    })
    response.headers["Cache-Control"] = "no-store"
    return response

@bp.get("/login")
def login():
    if not campus_oidc_is_configured():
        return jsonify({
            "code": "oidc_not_configured",
            "msg": "Campus SSO is not configured yet.",
        }), 503

    locale = request.args.get("locale")
    session["campus_oidc_locale"] = "en" if locale == "en" else "zh"
    session["campus_oidc_return_to"] = sanitize_return_to(
        request.args.get("return_to"),
        "/en" if locale == "en" else "/",
    )

    client = get_campus_oidc_client()
    return client.authorize_redirect(
        current_app.config["CAMPUS_SSO_REDIRECT_URI"],
        response_type="code",
        response_mode="query",
    )


@bp.get("/callback")
def callback():
    provider_error = request.args.get("error")
    if provider_error:
        public_code = (
            "access_denied"
            if provider_error == "access_denied"
            else "authorization_failed"
        )
        return _error_redirect(public_code)

    if not campus_oidc_is_configured():
        return _error_redirect("not_configured")

    try:
        client = get_campus_oidc_client()
        token = client.authorize_access_token()
        id_token_claims = dict(token.get("userinfo") or {})

        userinfo_response = client.post("userinfo", token=token)
        userinfo_response.raise_for_status()
        endpoint_claims = userinfo_response.json()
        if not isinstance(endpoint_claims, dict):
            raise CampusOidcError(
                "invalid_response",
                "The UserInfo response was not a JSON object.",
            )

        id_token_subject = id_token_claims.get("sub")
        endpoint_subject = endpoint_claims.get("sub")
        if (
            id_token_subject
            and endpoint_subject
            and id_token_subject != endpoint_subject
        ):
            raise CampusOidcError(
                "invalid_response",
                "ID token and UserInfo subjects do not match.",
            )

        claims = {**id_token_claims, **endpoint_claims}
        user = reconcile_oidc_user(claims)
        return_to = session.pop("campus_oidc_return_to", "/")
        ticket = issue_login_ticket(user, return_to)

        response = redirect(
            _frontend_login_url({"oidc_code": ticket}),
            code=303,
        )
        session.pop("campus_oidc_locale", None)

        id_token = token.get("id_token")
        if id_token:
            max_age = max(60, min(int(token.get("expires_in", 3600)), 86400))
            response.set_cookie(
                current_app.config["CAMPUS_SSO_ID_TOKEN_COOKIE_NAME"],
                id_token,
                max_age=max_age,
                secure=bool(current_app.config["CAMPUS_SSO_COOKIE_SECURE"]),
                httponly=True,
                samesite="Lax",
                path=current_app.config["CAMPUS_SSO_COOKIE_PATH"],
            )
        return response
    except CampusOidcError as exc:
        current_app.logger.info("Campus SSO callback rejected: %s", exc)
        return _error_redirect(exc.code)
    except Exception:
        current_app.logger.exception("Campus SSO callback failed")
        return _error_redirect("authorization_failed")


@bp.post("/exchange")
def exchange():
    payload = request.get_json(silent=True) or {}
    ticket = consume_login_ticket(payload.get("code"))
    if ticket is None or ticket.user is None or ticket.user.is_deleted:
        response = jsonify({
            "code": "invalid_login_ticket",
            "msg": "The SSO login ticket is invalid or expired.",
        })
        response.status_code = 400
        response.headers["Cache-Control"] = "no-store"
        return response

    identity = str(ticket.user.id)
    response = jsonify({
        "access_token": create_access_token(identity=identity),
        "refresh_token": create_refresh_token(identity=identity),
        "user": ticket.user.to_dict(include_contact=True),
        "return_to": sanitize_return_to(ticket.return_to),
    })
    response.headers["Cache-Control"] = "no-store"
    return response
