"""Tests for native MCP OAuth helpers and credential storage."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import threading
import time
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


@pytest.mark.parametrize(
    "expires_at",
    ["bad", True, [], {}, float("nan"), float("inf"), float("-inf")],
)
def test_malformed_token_expiry_is_expired(expires_at: object) -> None:
    assert token_is_expired(expires_at)


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


@pytest.mark.parametrize(
    ("response_kind", "expected_error"),
    [
        ("html", "OAuth client registration returned invalid JSON"),
        ("list", "expected a JSON object"),
        ("missing_client_id", "did not return a client_id"),
    ],
)
def test_register_client_normalizes_malformed_success_responses(
    response_kind: str,
    expected_error: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if response_kind == "html":
            return httpx.Response(200, text="<html>temporary proxy error</html>")
        if response_kind == "list":
            return httpx.Response(200, json=["not", "an", "object"])
        return httpx.Response(200, json={})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint=None,
    )

    with pytest.raises(OAuthError, match=expected_error):
        register_client(metadata, "http://127.0.0.1:1/callback", client=http)


def test_register_client_rejects_invalid_confidential_client_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "client_id": "cid",
            "client_secret": 123,
            "token_endpoint_auth_method": "client_secret_post",
        })

    http = httpx.Client(transport=httpx.MockTransport(handler))
    metadata = OAuthMetadata(
        authorization_endpoint="https://mcp.tavily.com/authorize",
        token_endpoint="https://mcp.tavily.com/token",
        registration_endpoint="https://mcp.tavily.com/register",
        revocation_endpoint=None,
    )

    with pytest.raises(OAuthError, match="issued no valid client_secret"):
        register_client(metadata, "http://127.0.0.1:1/callback", client=http)


def test_fetch_metadata_uses_discovery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("oauth-authorization-server"):
            return httpx.Response(200, json={
                "issuer": "https://mcp.tavily.com/",
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


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("issuer", "https://attacker.example/", "issuer does not match"),
        ("token_endpoint", "http://mcp.tavily.com/token", "invalid token_endpoint"),
        ("registration_endpoint", "https://user:pass@mcp.tavily.com/register", "invalid registration_endpoint"),
    ],
)
def test_fetch_metadata_rejects_untrusted_authorization_metadata(
    field: str,
    value: str,
    expected_error: str,
) -> None:
    metadata = {
        "issuer": "https://mcp.tavily.com/",
        "authorization_endpoint": "https://mcp.tavily.com/authorize",
        "token_endpoint": "https://mcp.tavily.com/token",
        "registration_endpoint": "https://mcp.tavily.com/register",
        "revocation_endpoint": "https://mcp.tavily.com/revoke",
    }
    metadata[field] = value

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("oauth-authorization-server"):
            return httpx.Response(200, json=metadata)
        return httpx.Response(200, json={"resource": "https://mcp.tavily.com/mcp"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, pytest.raises(OAuthError, match=expected_error):
        fetch_metadata(http)


def test_fetch_metadata_rejects_different_protected_resource() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("oauth-authorization-server"):
            return httpx.Response(200, json={
                "issuer": "https://mcp.tavily.com/",
                "authorization_endpoint": "https://mcp.tavily.com/authorize",
                "token_endpoint": "https://mcp.tavily.com/token",
                "registration_endpoint": "https://mcp.tavily.com/register",
            })
        return httpx.Response(200, json={"resource": "https://attacker.example/mcp"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, pytest.raises(
        OAuthError,
        match="does not match the Tavily MCP resource",
    ):
        fetch_metadata(http)


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


@pytest.mark.parametrize(
    ("response_kind", "expected_error"),
    [
        ("html", "OAuth token endpoint returned invalid JSON"),
        ("list", "expected a JSON object"),
        ("missing_access_token", "did not include a valid access_token"),
    ],
)
def test_token_request_normalizes_malformed_success_responses(
    response_kind: str,
    expected_error: str,
) -> None:
    from tavily_cli.oauth import refresh_tokens

    def handler(request: httpx.Request) -> httpx.Response:
        if response_kind == "html":
            return httpx.Response(200, text="<html>temporary proxy error</html>")
        if response_kind == "list":
            return httpx.Response(200, json=["not", "an", "object"])
        return httpx.Response(200, json={})

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

    with pytest.raises(OAuthError, match=expected_error):
        refresh_tokens(metadata, client, "old-refresh", client=http)


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
    monkeypatch.setattr(auth, "get_api_key", lambda: None)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = CliRunner().invoke(auth.logout, ["--json"])

    assert result.exit_code == 3
    assert json.loads(result.output) == {
        "authenticated": False,
        "method": None,
        "source": None,
        "local_credentials_cleared": True,
        "environment_credential_present": False,
        "server_revoked": False,
        "ok": False,
        "error": {
            "code": "oauth_revocation_failed",
            "message": "Token revocation failed (HTTP 500).",
            "stage": "auth",
            "retryable": True,
        },
    }


def test_logout_json_reports_remaining_environment_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli.commands import auth
    from tavily_cli.config import ClearCredentialsResult

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-environment-secret")
    monkeypatch.setattr(
        auth,
        "clear_credentials",
        lambda: ClearCredentialsResult(
            local_credentials_cleared=True,
            server_revoked=None,
        ),
    )

    logout_result = CliRunner().invoke(auth.logout, ["--json"])
    auth_result = CliRunner().invoke(auth.auth_status, ["--json"])

    assert logout_result.exit_code == 0
    logout_payload = json.loads(logout_result.output)
    assert logout_payload == {
        "authenticated": True,
        "method": "env",
        "source": "TAVILY_API_KEY environment variable",
        "local_credentials_cleared": True,
        "environment_credential_present": True,
        "warning": auth._ENV_CREDENTIAL_WARNING,
    }
    assert "tvly-environment-secret" not in logout_result.output
    assert json.loads(auth_result.output) == {
        "authenticated": True,
        "method": "env",
        "source": "TAVILY_API_KEY environment variable",
    }


def test_logout_json_reports_unauthenticated_after_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli.commands import auth
    from tavily_cli.config import ClearCredentialsResult

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(auth, "get_api_key", lambda: None)
    monkeypatch.setattr(
        auth,
        "clear_credentials",
        lambda: ClearCredentialsResult(
            local_credentials_cleared=True,
            server_revoked=None,
        ),
    )

    result = CliRunner().invoke(auth.logout, ["--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "authenticated": False,
        "method": None,
        "source": None,
        "local_credentials_cleared": True,
        "environment_credential_present": False,
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


def test_save_api_key_keeps_previous_oauth_when_revocation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    previous = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "client_id": "old-client",
        "token_endpoint_auth_method": "none",
    }
    config._write_config({"human_id": "keep-me", "oauth": previous})

    def fail_revocation(data: dict) -> bool:
        assert data == previous
        raise OAuthError("old session revocation failed")

    monkeypatch.setattr(config, "_revoke_stored_oauth", fail_revocation)

    with pytest.raises(OAuthError, match="old session revocation failed"):
        config.save_api_key("tvly-replacement")

    assert config._read_config() == {"human_id": "keep-me", "oauth": previous}


def test_save_oauth_session_revokes_previous_before_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config
    from tavily_cli.oauth import OAuthSession, OAuthTokens

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    previous = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "client_id": "old-client",
        "token_endpoint_auth_method": "none",
    }
    config._write_config({"oauth": previous})
    replacement = OAuthSession(
        tokens=OAuthTokens(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=expires_at_from_now(3600),
        ),
        client=RegisteredClient(
            client_id="new-client",
            client_secret=None,
            token_endpoint_auth_method="none",
            redirect_uri="http://127.0.0.1:2/callback",
        ),
    )
    revoked: list[dict] = []

    def revoke_before_overwrite(data: dict) -> bool:
        assert config._read_config()["oauth"] == previous
        revoked.append(data)
        return True

    monkeypatch.setattr(config, "_revoke_stored_oauth", revoke_before_overwrite)

    config.save_oauth_session(replacement)

    stored = config._read_config()["oauth"]
    assert revoked == [previous]
    assert stored["access_token"] == "new-access"
    assert stored["refresh_token"] == "new-refresh"
    assert stored["client_id"] == "new-client"


def test_save_oauth_session_keeps_previous_and_cleans_replacement_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config
    from tavily_cli.oauth import OAuthSession, OAuthTokens

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    previous = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "client_id": "old-client",
        "token_endpoint_auth_method": "none",
    }
    config._write_config({"oauth": previous})
    replacement = OAuthSession(
        tokens=OAuthTokens(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=expires_at_from_now(3600),
        ),
        client=RegisteredClient(
            client_id="new-client",
            client_secret=None,
            token_endpoint_auth_method="none",
            redirect_uri="http://127.0.0.1:2/callback",
        ),
    )
    revocation_attempts: list[dict] = []

    def fail_previous_then_clean_replacement(data: dict) -> bool:
        revocation_attempts.append(data)
        if len(revocation_attempts) == 1:
            raise OAuthError("old session revocation failed")
        return True

    monkeypatch.setattr(config, "_revoke_stored_oauth", fail_previous_then_clean_replacement)

    with pytest.raises(OAuthError, match="Could not replace the previous OAuth session"):
        config.save_oauth_session(replacement)

    assert config._read_config() == {"oauth": previous}
    assert revocation_attempts[0] == previous
    assert revocation_attempts[1]["refresh_token"] == "new-refresh"
    assert revocation_attempts[1]["client_id"] == "new-client"


def test_malformed_expiry_refreshes_without_revoking_active_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config, oauth
    from tavily_cli.oauth import OAuthTokens

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    previous = {
        "access_token": "old-access",
        "refresh_token": "same-refresh",
        "expires_at": "bad",
        "token_type": "Bearer",
        "client_id": "same-client",
        "client_secret": None,
        "token_endpoint_auth_method": "none",
        "redirect_uri": "http://127.0.0.1:1/callback",
    }
    config._write_config({"oauth": previous})

    monkeypatch.setattr(oauth, "fetch_metadata", lambda: object())
    monkeypatch.setattr(
        oauth,
        "refresh_tokens",
        lambda metadata, client, refresh_token: OAuthTokens(
            access_token="new-access",
            refresh_token=refresh_token,
            expires_at=expires_at_from_now(3600),
        ),
    )
    monkeypatch.setattr(
        config,
        "_revoke_stored_oauth",
        lambda data: pytest.fail("automatic refresh must not revoke the active session"),
    )

    access_token = config._get_oauth_access_token(config._read_config())

    assert access_token == "new-access"
    stored = config._read_config()["oauth"]
    assert stored["access_token"] == "new-access"
    assert stored["refresh_token"] == "same-refresh"
    assert stored["client_id"] == "same-client"


def test_malformed_expiry_without_refresh_is_invalid() -> None:
    from tavily_cli import config

    access_token = config._get_oauth_access_token({
        "oauth": {
            "access_token": "old-access",
            "expires_at": "bad",
        },
    })

    assert access_token is None


def test_config_write_keeps_previous_file_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config._write_config({"api_key": "tvly-old"})

    monkeypatch.setattr(config.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("disk busy")))

    with pytest.raises(OSError, match="disk busy"):
        config._write_config({"api_key": "tvly-new"})

    assert config._read_config() == {"api_key": "tvly-old"}
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_parallel_expired_session_refreshes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config, oauth
    from tavily_cli.oauth import OAuthTokens

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config._write_config({
        "oauth": {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": 0,
            "client_id": "same-client",
            "client_secret": None,
            "token_endpoint_auth_method": "none",
            "redirect_uri": "http://127.0.0.1:1/callback",
        }
    })

    refresh_started = threading.Event()
    release_refresh = threading.Event()
    second_started = threading.Event()
    refresh_calls: list[str] = []
    results: list[str | None] = []
    errors: list[Exception] = []

    monkeypatch.setattr(oauth, "fetch_metadata", lambda: object())

    def refresh_once(metadata: object, client: RegisteredClient, refresh_token: str) -> OAuthTokens:
        refresh_calls.append(refresh_token)
        refresh_started.set()
        assert release_refresh.wait(timeout=2)
        return OAuthTokens(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=expires_at_from_now(3600),
        )

    monkeypatch.setattr(oauth, "refresh_tokens", refresh_once)

    def resolve(*, second: bool = False) -> None:
        if second:
            second_started.set()
        try:
            results.append(config.get_api_key())
        except Exception as e:
            errors.append(e)

    first = threading.Thread(target=resolve)
    first.start()
    assert refresh_started.wait(timeout=2)
    second = threading.Thread(target=resolve, kwargs={"second": True})
    second.start()
    assert second_started.wait(timeout=2)
    release_refresh.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert results == ["new-access", "new-access"]
    assert refresh_calls == ["old-refresh"]
    assert config._read_config()["oauth"]["refresh_token"] == "new-refresh"


def test_config_lock_serializes_separate_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config._write_config({"api_key": "tvly-old"})
    started = tmp_path / "child-started"
    child_code = "\n".join((
        "import sys",
        "from pathlib import Path",
        "from tavily_cli import config",
        "root = Path(sys.argv[1])",
        "config.CONFIG_DIR = root",
        "config.CONFIG_FILE = root / 'config.json'",
        "(root / 'child-started').touch()",
        "config._write_config({'api_key': 'tvly-new'})",
    ))

    with config._config_lock():
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(tmp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while not started.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        time.sleep(0.1)
        assert child.poll() is None
        assert config._read_config() == {"api_key": "tvly-old"}

    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0, stdout + stderr
    assert config._read_config() == {"api_key": "tvly-new"}


def test_refresh_failure_is_reported_and_preserves_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tavily_cli import config, oauth

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    previous = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "expires_at": 0,
        "client_id": "same-client",
        "client_secret": None,
        "token_endpoint_auth_method": "none",
        "redirect_uri": "http://127.0.0.1:1/callback",
    }
    config._write_config({"oauth": previous})
    monkeypatch.setattr(oauth, "fetch_metadata", lambda: (_ for _ in ()).throw(OAuthError("network down")))

    with pytest.raises(OAuthError, match="Could not refresh.*network down"):
        config.get_api_key()

    assert config._read_config() == {"oauth": previous}


def test_search_json_reports_refresh_failure_without_keyless_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli import config
    from tavily_cli.cli import cli

    monkeypatch.setattr(config, "get_api_key", lambda: (_ for _ in ()).throw(OAuthError("network down")))

    result = CliRunner().invoke(cli, ["search", "query", "--json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "oauth_refresh_failed",
            "message": "network down",
            "stage": "auth",
            "retryable": True,
        },
    }


def test_api_key_login_json_reports_replacement_revocation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli.commands import auth

    def fail_save(api_key: str) -> None:
        raise OAuthError("old session revocation failed")

    monkeypatch.setattr(auth, "save_api_key", fail_save)

    result = CliRunner().invoke(auth.login, ["--api-key", "tvly-replacement", "--json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "authentication_failed",
            "message": "old session revocation failed",
            "stage": "auth",
            "retryable": False,
        },
    }


def test_oauth_login_json_reports_replacement_revocation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from tavily_cli import oauth
    from tavily_cli.commands import auth
    from tavily_cli.oauth import OAuthSession, OAuthTokens

    replacement = OAuthSession(
        tokens=OAuthTokens(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=expires_at_from_now(3600),
        ),
        client=RegisteredClient(
            client_id="new-client",
            client_secret=None,
            token_endpoint_auth_method="none",
            redirect_uri="http://127.0.0.1:2/callback",
        ),
    )

    monkeypatch.setattr(oauth, "looks_headless", lambda: False)
    monkeypatch.setattr(oauth, "run_browser_login", lambda **kwargs: replacement)

    def fail_save(session: OAuthSession) -> None:
        raise OAuthError("old session revocation failed")

    monkeypatch.setattr(auth, "save_oauth_session", fail_save)

    result = CliRunner().invoke(auth.login, ["--json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "code": "authentication_failed",
            "message": "old session revocation failed",
            "stage": "auth",
            "retryable": False,
        },
    }


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

    revoked: list[dict] = []
    monkeypatch.setattr(config, "_revoke_stored_oauth", lambda data: revoked.append(data) or True)
    config.save_api_key("tvly-other")
    assert config.get_api_key() == "tvly-other"
    assert not config.has_stored_oauth()
    assert revoked == [stored["oauth"]]
