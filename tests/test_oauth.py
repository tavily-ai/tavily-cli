"""Tests for native MCP OAuth helpers and credential storage."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import httpx
import pytest

from tavily_cli.oauth import (
    OAuthError,
    OAuthMetadata,
    RegisteredClient,
    build_authorize_url,
    expires_at_from_now,
    fetch_metadata,
    generate_pkce,
    register_client,
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
