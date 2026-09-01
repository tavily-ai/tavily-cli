"""Tests for native MCP OAuth helpers and credential storage."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from click.testing import CliRunner

from tavily_cli.oauth import (
    OAuthError,
    OAuthMetadata,
    RegisteredClient,
    build_authorize_url,
    expires_at_from_now,
    fetch_metadata,
    generate_pkce,
    register_client,
    revoke_token,
    token_is_expired,
)


def test_pkce_is_s256_and_unpadded() -> None:
    verifier, challenge = generate_pkce()
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert challenge == expected
    assert "=" not in challenge


def test_token_expiry_skew() -> None:
    now = 1_000_000.0
    expires_at = expires_at_from_now(3600, now=now)
    assert expires_at == now + 3600 - 60
    assert not token_is_expired(expires_at, now=now)
    assert token_is_expired(expires_at, now=expires_at)
    assert token_is_expired(None)


def test_authorize_url_includes_pkce_and_resource() -> None:
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint="https://mcp.tavily.com/revoke",
        resource="https://mcp.tavily.com/mcp",
    )
    client = RegisteredClient(
        client_id="cli-1",
        client_secret=None,
        token_endpoint_auth_method="none",
        redirect_uri="http://127.0.0.1:9999/callback",
    )
    url = build_authorize_url(metadata, client, state="st", code_challenge="ch")
    assert url.startswith("https://mcp.tavily.com/authorize?")
    assert "client_id=cli-1" in url
    assert "code_challenge=ch" in url
    assert "code_challenge_method=S256" in url
    assert "state=st" in url
    assert "resource=https%3A%2F%2Fmcp.tavily.com%2Fmcp" in url
    assert "scope=openid+offline_access" in url or "scope=openid%20offline_access" in url


def test_register_client_public_pkce() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://mcp.tavily.com/register"
        body = request.read()
        assert b"token_endpoint_auth_method" in body
        assert b"Tavily CLI" in body
        return httpx.Response(201, json={"client_id": "cid-1", "token_endpoint_auth_method": "none"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint=None,
    )
    registered = register_client(metadata, "http://127.0.0.1:1/callback", client=http)
    assert registered.client_id == "cid-1"
    assert registered.client_secret is None
    assert registered.token_endpoint_auth_method == "none"


def test_register_client_rejects_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid redirect")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint=None,
    )
    with pytest.raises(OAuthError, match="Client registration failed"):
        register_client(metadata, "http://127.0.0.1:1/callback", client=http)


def test_fetch_metadata_uses_discovery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("oauth-authorization-server"):
            return httpx.Response(200, json={
                "authorization_endpoint": "https://mcp.tavily.com/authorize",
                "token_endpoint": "https://mcp.tavily.com/token",
                "registration_endpoint": "https://mcp.tavily.com/register",
                "revocation_endpoint": "https://mcp.tavily.com/revoke",
            })
        if url.endswith("oauth-protected-resource/mcp"):
            return httpx.Response(200, json={"resource": "https://mcp.tavily.com/mcp"})
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    meta = fetch_metadata(http)
    assert meta.authorization_endpoint == "https://mcp.tavily.com/authorize"
    assert meta.resource == "https://mcp.tavily.com/mcp"


def test_refresh_tokens_keeps_refresh_if_omitted() -> None:
    from tavily_cli.oauth import refresh_tokens

    def handler(request: httpx.Request) -> httpx.Response:
        assert b"grant_type=refresh_token" in request.read()
        return httpx.Response(200, json={
            "access_token": "new-access",
            "expires_in": 3600,
        })

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint=None,
    )
    client = RegisteredClient(
        client_id="cid",
        client_secret=None,
        token_endpoint_auth_method="none",
        redirect_uri="http://127.0.0.1:1/callback",
    )
    tokens = refresh_tokens(metadata, client, "old-refresh", client=http)
    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "old-refresh"


def test_revoke_public_client_uses_standard_request_first() -> None:
    requests: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(parse_qs(request.read().decode(), keep_blank_values=True))
        return httpx.Response(200)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint="https://mcp.tavily.com/revoke",
    )
    client = RegisteredClient(
        client_id="cid",
        client_secret=None,
        token_endpoint_auth_method="none",
        redirect_uri="http://127.0.0.1:1/callback",
    )

    revoke_token(metadata, client, "refresh-1", token_type_hint="refresh_token", client=http)

    assert requests == [{
        "token": ["refresh-1"],
        "token_type_hint": ["refresh_token"],
        "client_id": ["cid"],
    }]


def test_revoke_public_client_retries_known_400_with_empty_secret() -> None:
    requests: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.read().decode(), keep_blank_values=True)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": "invalid_request"})
        return httpx.Response(200)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint="https://mcp.tavily.com/revoke",
    )
    client = RegisteredClient(
        client_id="cid",
        client_secret=None,
        token_endpoint_auth_method="none",
        redirect_uri="http://127.0.0.1:1/callback",
    )

    revoke_token(metadata, client, "refresh-1", token_type_hint="refresh_token", client=http)

    assert "client_secret" not in requests[0]
    assert requests[1]["client_secret"] == [""]
    assert requests[1]["client_id"] == ["cid"]


def test_revoke_token_raises_after_failed_compatibility_retry() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(400, json={"error": "invalid_request"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint="https://mcp.tavily.com/revoke",
    )
    client = RegisteredClient(
        client_id="cid",
        client_secret=None,
        token_endpoint_auth_method="none",
        redirect_uri="http://127.0.0.1:1/callback",
    )

    with pytest.raises(OAuthError, match=r"Token revocation failed \(HTTP 400\)"):
        revoke_token(metadata, client, "refresh-1", token_type_hint="refresh_token", client=http)
    assert request_count == 2


def test_stored_oauth_revokes_refresh_before_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli import config, oauth

    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint="https://mcp.tavily.com/revoke",
    )
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(oauth, "fetch_metadata", lambda: metadata)

    def record_revocation(
        metadata: OAuthMetadata,
        client: RegisteredClient,
        token: str,
        *,
        token_type_hint: str,
    ) -> None:
        calls.append((token, token_type_hint))

    monkeypatch.setattr(oauth, "revoke_token", record_revocation)

    attempted = config._revoke_stored_oauth({
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "client_id": "cid",
        "token_endpoint_auth_method": "none",
    })

    assert attempted is True
    assert calls == [
        ("refresh-1", "refresh_token"),
        ("access-1", "access_token"),
    ]


def test_clear_credentials_reports_revocation_failure_but_clears_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "MCP_AUTH_DIR", tmp_path / "mcp-auth")
    config._write_config({
        "human_id": "keep-me",
        "oauth": {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "client_id": "cid",
            "token_endpoint_auth_method": "none",
        },
    })

    def fail_revocation(data: dict) -> bool:
        raise OAuthError("Token revocation failed (HTTP 500).")

    monkeypatch.setattr(config, "_revoke_stored_oauth", fail_revocation)

    result = config.clear_credentials()

    assert result.local_credentials_cleared is True
    assert result.server_revoked is False
    assert result.revocation_error == "Token revocation failed (HTTP 500)."
    assert config._read_config() == {"human_id": "keep-me"}


def test_logout_json_reports_partial_revocation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli.commands import auth
    from tavily_cli.config import ClearCredentialsResult

    monkeypatch.setattr(
        auth,
        "clear_credentials",
        lambda: ClearCredentialsResult(
            local_credentials_cleared=True,
            server_revoked=False,
            revocation_error="Token revocation failed (HTTP 500).",
        ),
    )

    result = CliRunner().invoke(auth.logout, ["--json"])

    assert result.exit_code == 3
    assert json.loads(result.output) == {
        "authenticated": False,
        "local_credentials_cleared": True,
        "server_revoked": False,
        "error": "Token revocation failed (HTTP 500).",
    }


@pytest.mark.parametrize(
    ("arguments", "headless"),
    [
        (["--json", "--no-browser"], False),
        (["--json"], True),
    ],
)
def test_json_headless_login_emits_authorization_url_on_stderr(
    arguments: list[str],
    headless: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import oauth
    from tavily_cli.commands import auth
    from tavily_cli.oauth import OAuthSession, OAuthTokens

    session = OAuthSession(
        tokens=OAuthTokens(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=expires_at_from_now(3600),
        ),
        client=RegisteredClient(
            client_id="cid",
            client_secret=None,
            token_endpoint_auth_method="none",
            redirect_uri="http://127.0.0.1:9999/callback",
        ),
    )

    monkeypatch.setattr(oauth, "looks_headless", lambda: headless)
    monkeypatch.setattr(auth, "save_oauth_session", lambda value: None)

    def complete_login(*, open_browser: bool, on_status: object) -> OAuthSession:
        assert open_browser is False
        assert callable(on_status)
        on_status("https://mcp.tavily.com/authorize?client_id=cid")
        return session

    monkeypatch.setattr(oauth, "run_browser_login", complete_login)

    result = CliRunner().invoke(auth.login, arguments)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "authenticated": True,
        "method": "oauth",
        "detail": f"Token stored in {auth.CONFIG_FILE}",
    }
    assert json.loads(result.stderr) == {
        "event": "authorization_required",
        "authorization_url": "https://mcp.tavily.com/authorize?client_id=cid",
    }


def test_json_browser_login_keeps_stderr_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli import oauth
    from tavily_cli.commands import auth
    from tavily_cli.oauth import OAuthSession, OAuthTokens

    session = OAuthSession(
        tokens=OAuthTokens(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=expires_at_from_now(3600),
        ),
        client=RegisteredClient(
            client_id="cid",
            client_secret=None,
            token_endpoint_auth_method="none",
            redirect_uri="http://127.0.0.1:9999/callback",
        ),
    )

    monkeypatch.setattr(oauth, "looks_headless", lambda: False)
    monkeypatch.setattr(auth, "save_oauth_session", lambda value: None)

    def complete_login(*, open_browser: bool, on_status: object) -> OAuthSession:
        assert open_browser is True
        assert on_status is None
        return session

    monkeypatch.setattr(oauth, "run_browser_login", complete_login)

    result = CliRunner().invoke(auth.login, ["--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["authenticated"] is True
    assert result.stderr == ""


def test_api_key_and_oauth_replace_each_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli import config
    from tavily_cli.oauth import OAuthSession, OAuthTokens

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    config.save_api_key("tvly-test-key")
    assert config.get_api_key() == "tvly-test-key"

    session = OAuthSession(
        tokens=OAuthTokens(
            access_token="header.payload.sig",
            refresh_token="r1",
            expires_at=expires_at_from_now(3600),
        ),
        client=RegisteredClient(
            client_id="cid",
            client_secret=None,
            token_endpoint_auth_method="none",
            redirect_uri="http://127.0.0.1:1/callback",
        ),
    )
    config.save_oauth_session(session)
    assert config.has_stored_oauth()
    assert config.get_api_key() == "header.payload.sig"
    stored = config._read_config()
    assert "api_key" not in stored

    config.save_api_key("tvly-other")
    assert config.get_api_key() == "tvly-other"
    assert not config.has_stored_oauth()
