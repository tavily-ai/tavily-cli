"""Exercise the local callback handoff without a live OAuth provider."""

from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from tavily_cli import oauth


@pytest.mark.parametrize("outcome", ["success", "denied", "exchange_failure"])
def test_callback_keeps_authentication_material_off_the_page(monkeypatch, outcome):
    metadata = oauth.OAuthMetadata("https://example.test/authorize", "https://example.test/token", "https://example.test/register", None)
    monkeypatch.setattr(oauth, "fetch_metadata", lambda: metadata)
    registered = []
    pages = []

    def register(metadata, redirect_uri):
        client = oauth.RegisteredClient("test-client", None, "none", redirect_uri)
        registered.append(client)
        return client

    def exchange(*args, **kwargs):
        if outcome == "exchange_failure":
            raise oauth.OAuthError("Token exchange failed")
        assert outcome == "success"
        return oauth.OAuthTokens("access-test", "refresh-test", 9999999999)

    def authorize(url):
        state = parse_qs(urlparse(url).query)["state"][0]
        query = {"state": state, "code": "private-code"}
        if outcome == "denied":
            query = {"state": state, "error": "access_denied", "error_description": "private-provider-message"}
        response = httpx.get(f"{registered[0].redirect_uri}?{urlencode(query)}", timeout=3, trust_env=False)
        pages.append(response)

    monkeypatch.setattr(oauth, "register_client", register)
    monkeypatch.setattr(oauth, "exchange_code", exchange)
    if outcome == "success":
        session = oauth.run_browser_login(open_browser=False, on_status=authorize, timeout=3)
        assert session.tokens.access_token == "access-test"
    else:
        with pytest.raises(oauth.OAuthError):
            oauth.run_browser_login(open_browser=False, on_status=authorize, timeout=3)
    response = pages[0]
    assert response.status_code == (400 if outcome == "denied" else 200)
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert len(response.content) == int(response.headers["Content-Length"])
    for private_value in ("private-code", "private-provider-message", "access-test", "refresh-test"):
        assert private_value not in response.text
    assert "You're signed in" not in response.text
    assert 'referrerpolicy="no-referrer"' in response.text

