"""Native MCP OAuth 2.1 for the Tavily CLI.

Replaces the previous `npx mcp-remote` login path. Speaks the authorization
code + PKCE flow that https://mcp.tavily.com/ advertises (dynamic client
registration, refresh tokens). Tokens are stored in ~/.tavily/config.json.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Literal

import httpx

MCP_ISSUER = "https://mcp.tavily.com/"
MCP_RESOURCE = "https://mcp.tavily.com/mcp"
OAUTH_METADATA_URL = "https://mcp.tavily.com/.well-known/oauth-authorization-server"
PRM_URL = "https://mcp.tavily.com/.well-known/oauth-protected-resource/mcp"
DEFAULT_SCOPES = "openid offline_access"
CLIENT_NAME = "Tavily CLI"
LOGIN_TIMEOUT_SECONDS = 180
HTTP_TIMEOUT = 30.0
_EXPIRY_SKEW_SECONDS = 60

_FALLBACK_METADATA = {
    "issuer": MCP_ISSUER,
    "authorization_endpoint": "https://mcp.tavily.com/authorize",
    "token_endpoint": "https://mcp.tavily.com/token",
    "registration_endpoint": "https://mcp.tavily.com/register",
    "revocation_endpoint": "https://mcp.tavily.com/revoke",
}


class OAuthError(Exception):
    """Interactive OAuth failed in a way the CLI should show to the user."""


@dataclass
class OAuthMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    revocation_endpoint: str | None
    resource: str = MCP_RESOURCE


@dataclass
class RegisteredClient:
    client_id: str
    client_secret: str | None
    token_endpoint_auth_method: str
    redirect_uri: str


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: float
    token_type: str = "Bearer"


@dataclass
class OAuthSession:
    tokens: OAuthTokens
    client: RegisteredClient


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    alphabet = string.ascii_letters + string.digits + "-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(128))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def expires_at_from_now(expires_in: int | None, *, now: float | None = None) -> float:
    """Unix timestamp when the access token should be treated as expired."""
    lifetime = expires_in if isinstance(expires_in, int) and expires_in > 0 else 3600
    return (now if now is not None else time.time()) + lifetime - _EXPIRY_SKEW_SECONDS


def token_is_expired(expires_at: float | None, *, now: float | None = None) -> bool:
    if expires_at is None:
        return True
    return (now if now is not None else time.time()) >= expires_at


def fetch_metadata(client: httpx.Client | None = None) -> OAuthMetadata:
    """Discover AS + resource metadata, with static fallbacks if discovery fails."""
    http = client or httpx.Client(timeout=HTTP_TIMEOUT)
    close = client is None
    try:
        data = dict(_FALLBACK_METADATA)
        try:
            resp = http.get(OAUTH_METADATA_URL)
            resp.raise_for_status()
            discovered = resp.json()
            if isinstance(discovered, dict):
                data.update({k: v for k, v in discovered.items() if isinstance(v, str)})
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            pass

        resource = MCP_RESOURCE
        try:
            resp = http.get(PRM_URL)
            if resp.is_success:
                prm = resp.json()
                if isinstance(prm, dict) and isinstance(prm.get("resource"), str):
                    resource = prm["resource"]
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            pass

        registration = data.get("registration_endpoint")
        authorization = data.get("authorization_endpoint")
        token = data.get("token_endpoint")
        if not registration or not authorization or not token:
            raise OAuthError("OAuth discovery did not return authorization, token, and registration endpoints.")
        return OAuthMetadata(
            authorization_endpoint=authorization,
            token_endpoint=token,
            registration_endpoint=registration,
            revocation_endpoint=data.get("revocation_endpoint"),
            resource=resource,
        )
    finally:
        if close:
            http.close()


def register_client(
    metadata: OAuthMetadata,
    redirect_uri: str,
    *,
    client: httpx.Client | None = None,
) -> RegisteredClient:
    """RFC 7591 dynamic client registration as a public PKCE client."""
    http = client or httpx.Client(timeout=HTTP_TIMEOUT)
    close = client is None
    payload = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": DEFAULT_SCOPES,
        "application_type": "native",
    }
    try:
        resp = http.post(metadata.registration_endpoint, json=payload)
        if resp.status_code >= 400:
            raise OAuthError(_http_error("Client registration failed", resp))
        data = resp.json()
        client_id = data.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise OAuthError("OAuth registration did not return a client_id.")
        method = data.get("token_endpoint_auth_method") or "none"
        if method not in ("none", "client_secret_post", "client_secret_basic"):
            raise OAuthError(
                f"Authorization server registered the CLI with unsupported "
                f"token_endpoint_auth_method {method!r}."
            )
        secret = data.get("client_secret")
        if method in ("client_secret_post", "client_secret_basic") and not secret:
            raise OAuthError(
                f"Authorization server registered the CLI for {method!r} but issued no client_secret."
            )
        return RegisteredClient(
            client_id=client_id,
            client_secret=secret if isinstance(secret, str) else None,
            token_endpoint_auth_method=method,
            redirect_uri=redirect_uri,
        )
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach the Tavily OAuth server: {e}") from e
    finally:
        if close:
            http.close()


def build_authorize_url(
    metadata: OAuthMetadata,
    registered: RegisteredClient,
    *,
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": registered.client_id,
        "redirect_uri": registered.redirect_uri,
        "scope": DEFAULT_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": metadata.resource,
    }
    return metadata.authorization_endpoint + "?" + urllib.parse.urlencode(params)


def _token_auth_extras(registered: RegisteredClient) -> tuple[dict[str, str], tuple[str, str] | None]:
    """Body fields and optional basic-auth tuple for the token/revoke endpoints."""
    extra: dict[str, str] = {"client_id": registered.client_id}
    basic = None
    if registered.token_endpoint_auth_method == "client_secret_post" and registered.client_secret:
        extra["client_secret"] = registered.client_secret
    elif registered.token_endpoint_auth_method == "client_secret_basic" and registered.client_secret:
        basic = (registered.client_id, registered.client_secret)
        extra = {}
    return extra, basic


def exchange_code(
    metadata: OAuthMetadata,
    registered: RegisteredClient,
    *,
    code: str,
    code_verifier: str,
    client: httpx.Client | None = None,
) -> OAuthTokens:
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": registered.redirect_uri,
        "code_verifier": code_verifier,
        "resource": metadata.resource,
    }
    extra, basic = _token_auth_extras(registered)
    body.update(extra)
    return _request_tokens(metadata.token_endpoint, body, basic=basic, client=client)


def refresh_tokens(
    metadata: OAuthMetadata,
    registered: RegisteredClient,
    refresh_token: str,
    *,
    client: httpx.Client | None = None,
) -> OAuthTokens:
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "resource": metadata.resource,
    }
    extra, basic = _token_auth_extras(registered)
    body.update(extra)
    tokens = _request_tokens(metadata.token_endpoint, body, basic=basic, client=client)
    if tokens.refresh_token is None:
        tokens = OAuthTokens(
            access_token=tokens.access_token,
            refresh_token=refresh_token,
            expires_at=tokens.expires_at,
            token_type=tokens.token_type,
        )
    return tokens


def revoke_token(
    metadata: OAuthMetadata,
    registered: RegisteredClient,
    token: str,
    *,
    token_type_hint: Literal["access_token", "refresh_token"],
    client: httpx.Client | None = None,
) -> None:
    if not token:
        return
    if not metadata.revocation_endpoint:
        raise OAuthError("OAuth server does not advertise a token revocation endpoint.")

    http = client or httpx.Client(timeout=HTTP_TIMEOUT)
    close = client is None
    body = {
        "token": token,
        "token_type_hint": token_type_hint,
    }
    extra, basic = _token_auth_extras(registered)
    body.update(extra)
    try:
        resp = http.post(metadata.revocation_endpoint, data=body, auth=basic)

        if resp.status_code == 400 and registered.token_endpoint_auth_method == "none":
            # Compatibility fallback for MCP Python SDK revocation handlers that
            # incorrectly require the nullable client_secret form field. The
            # registered client remains public and no secret is supplied.
            # https://github.com/modelcontextprotocol/python-sdk/issues/2260
            compatibility_body = dict(body)
            compatibility_body["client_secret"] = ""
            resp = http.post(metadata.revocation_endpoint, data=compatibility_body, auth=basic)

        if not resp.is_success:
            raise OAuthError(_http_error("Token revocation failed", resp))
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach the Tavily OAuth server: {e}") from e
    finally:
        if close:
            http.close()


def _request_tokens(
    token_endpoint: str,
    body: dict[str, str],
    *,
    basic: tuple[str, str] | None,
    client: httpx.Client | None,
) -> OAuthTokens:
    http = client or httpx.Client(timeout=HTTP_TIMEOUT)
    close = client is None
    try:
        resp = http.post(token_endpoint, data=body, auth=basic)
        if resp.status_code >= 400:
            raise OAuthError(_http_error("Token request failed", resp))
        data = resp.json()
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise OAuthError("OAuth token response did not include an access_token.")
        refresh = data.get("refresh_token")
        return OAuthTokens(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) else None,
            expires_at=expires_at_from_now(data.get("expires_in")),
            token_type=data.get("token_type") or "Bearer",
        )
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach the Tavily OAuth server: {e}") from e
    finally:
        if close:
            http.close()


def _http_error(prefix: str, resp: httpx.Response) -> str:
    detail = resp.text.strip().replace("\n", " ")
    if len(detail) > 280:
        detail = detail[:277] + "..."
    if detail:
        return f"{prefix} (HTTP {resp.status_code}): {detail}"
    return f"{prefix} (HTTP {resp.status_code})."


def looks_headless() -> bool:
    """Heuristic: SSH or missing display — don't try to open a browser."""
    import os
    import sys

    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return True
    if sys.platform == "linux" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def run_browser_login(
    *,
    open_browser: bool = True,
    timeout: int = LOGIN_TIMEOUT_SECONDS,
    on_status: Callable[[str], None] | None = None,
) -> OAuthSession:
    """Run the interactive authorization-code login and return tokens + client."""
    metadata = fetch_metadata()
    result: dict[str, str | None] = {}
    event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            qs = urllib.parse.parse_qs(parsed.query)
            result["code"] = (qs.get("code") or [None])[0]
            result["state"] = (qs.get("state") or [None])[0]
            result["error"] = (qs.get("error") or [None])[0]
            result["error_description"] = (qs.get("error_description") or [None])[0]
            ok = bool(result.get("code")) and not result.get("error")
            body = _callback_html(success=ok)
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            event.set()

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread: threading.Thread | None = None
    try:
        port = httpd.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        registered = register_client(metadata, redirect_uri)

        verifier, challenge = generate_pkce()
        state = secrets.token_urlsafe(32)
        authorize_url = build_authorize_url(
            metadata, registered, state=state, code_challenge=challenge
        )

        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        if on_status:
            on_status(authorize_url)
        if open_browser:
            webbrowser.open(authorize_url, new=1, autoraise=True)
        if not event.wait(timeout):
            raise OAuthError(
                f"Timed out after {timeout}s waiting for browser authorization. "
                "Open the login URL printed above, or use: tvly login --api-key tvly-YOUR_KEY"
            )
    finally:
        if thread is not None:
            httpd.shutdown()
            thread.join(timeout=2)
        httpd.server_close()

    if result.get("error"):
        desc = result.get("error_description") or result["error"]
        raise OAuthError(f"Authorization was denied: {desc}")
    if result.get("state") != state:
        raise OAuthError("OAuth state mismatch; aborting to prevent CSRF.")
    code = result.get("code")
    if not code:
        raise OAuthError("Authorization callback did not include a code.")

    tokens = exchange_code(metadata, registered, code=code, code_verifier=verifier)
    return OAuthSession(tokens=tokens, client=registered)


def _callback_html(*, success: bool) -> bytes:
    if success:
        message = "You're signed in. You can close this tab and return to the terminal."
        color = "#9BC0AE"
        title = "Tavily CLI"
    else:
        message = "Sign-in didn't complete. Return to the terminal for details."
        color = "#FAA2FB"
        title = "Tavily CLI"
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family: system-ui, sans-serif; background:#111; color:#eee;
             display:flex; min-height:100vh; align-items:center; justify-content:center;">
  <div style="max-width:32rem; text-align:center;">
    <p style="color:{color}; font-size:1.25rem; font-weight:600;">tavily</p>
    <p>{message}</p>
  </div>
</body></html>"""
    return html.encode("utf-8")
