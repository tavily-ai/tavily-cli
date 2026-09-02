"""API key storage and retrieval for the Tavily CLI."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import psutil

CONFIG_DIR = Path.home() / ".tavily"
CONFIG_FILE = CONFIG_DIR / "config.json"
_SESSION_FILE = CONFIG_DIR / "session.json"

MCP_AUTH_DIR = Path.home() / ".mcp-auth"

_CONFIG_THREAD_LOCK = threading.RLock()
_CONFIG_LOCK_STATE = threading.local()


@dataclass(frozen=True)
class ClearCredentialsResult:
    """Outcome of local credential cleanup and optional server revocation."""

    local_credentials_cleared: bool
    server_revoked: bool | None
    revocation_error: str | None = None


def _pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    return psutil.pid_exists(pid)


def _get_grandparent_pid() -> int | None:
    """Get the grandparent PID (parent of parent)."""
    try:
        return psutil.Process(os.getppid()).ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _get_session_id() -> str:
    """Return a stable session ID for the current terminal or agent session.

    Matches on PPID first (terminal users), then grandparent PID (agents
    like Claude Code that spawn a new shell per command).
    """
    ppid = str(os.getppid())
    gppid = _get_grandparent_pid()
    gppid_str = str(gppid) if gppid is not None else None

    sessions: dict[str, str] = {}
    if _SESSION_FILE.exists():
        try:
            sessions = json.loads(_SESSION_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            sessions = {}

    if ppid in sessions:
        return sessions[ppid]

    if gppid_str is not None and gppid_str in sessions and _pid_alive(gppid):
        return sessions[gppid_str]

    alive = {pid: sid for pid, sid in sessions.items() if _pid_alive(int(pid))}

    new_id = uuid4().hex
    alive[ppid] = new_id
    if gppid_str is not None:
        alive[gppid_str] = new_id

    _write_sessions(alive)
    return new_id


def _write_sessions(sessions: dict[str, str]) -> None:
    """Persist session entries to disk."""
    old_umask = os.umask(0o077)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(json.dumps(sessions, indent=2) + "\n")
    finally:
        os.umask(old_umask)


try:
    SESSION_ID = _get_session_id()
except Exception:
    SESSION_ID = uuid4().hex


def _read_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _lock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _unlock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _config_lock() -> Iterator[None]:
    """Serialize config transactions across threads and CLI processes."""
    with _CONFIG_THREAD_LOCK:
        depth = getattr(_CONFIG_LOCK_STATE, "depth", 0)
        if depth:
            _CONFIG_LOCK_STATE.depth = depth + 1
            try:
                yield
            finally:
                _CONFIG_LOCK_STATE.depth -= 1
            return

        CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        CONFIG_DIR.chmod(0o700)
        lock_path = CONFIG_FILE.with_name(f".{CONFIG_FILE.name}.lock")
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(lock_fd, "r+b", buffering=0) as lock_file:
            lock_path.chmod(0o600)
            _lock_file(lock_file)
            _CONFIG_LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _CONFIG_LOCK_STATE.depth = 0
                _unlock_file(lock_file)


def _write_config_unlocked(data: dict) -> None:
    """Atomically replace config.json while the caller holds _config_lock."""
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    payload = json.dumps(data, indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(
        dir=CONFIG_DIR,
        prefix=f".{CONFIG_FILE.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.chmod(0o600)
        os.replace(temp_path, CONFIG_FILE)
        CONFIG_FILE.chmod(0o600)
    finally:
        temp_path.unlink(missing_ok=True)


def _write_config(data: dict) -> None:
    with _config_lock():
        _write_config_unlocked(data)


def _oauth_session_data(session) -> dict:
    """Serialize a validated OAuth session for config.json."""
    from tavily_cli.oauth import OAuthSession

    if not isinstance(session, OAuthSession):
        raise TypeError("session must be an OAuthSession")
    return {
        "access_token": session.tokens.access_token,
        "refresh_token": session.tokens.refresh_token,
        "expires_at": session.tokens.expires_at,
        "token_type": session.tokens.token_type,
        "client_id": session.client.client_id,
        "client_secret": session.client.client_secret,
        "token_endpoint_auth_method": session.client.token_endpoint_auth_method,
        "redirect_uri": session.client.redirect_uri,
    }


def save_api_key(api_key: str) -> None:
    """Replace stored credentials with an API key, revoking old OAuth first."""
    with _config_lock():
        config = _read_config()
        previous_oauth = config.get("oauth")
        if isinstance(previous_oauth, dict):
            _revoke_stored_oauth(previous_oauth)
        config["api_key"] = api_key
        config.pop("oauth", None)
        _write_config_unlocked(config)


def save_oauth_session(session) -> None:
    """Replace stored credentials with a newly authorized OAuth session."""
    from tavily_cli.oauth import OAuthError

    replacement_oauth = _oauth_session_data(session)
    with _config_lock():
        config = _read_config()
        previous_oauth = config.get("oauth")
        if isinstance(previous_oauth, dict) and previous_oauth != replacement_oauth:
            try:
                _revoke_stored_oauth(previous_oauth)
            except OAuthError as previous_error:
                try:
                    _revoke_stored_oauth(replacement_oauth)
                except OAuthError as cleanup_error:
                    raise OAuthError(
                        f"Could not replace the previous OAuth session: {previous_error} "
                        f"The new OAuth session also could not be revoked: {cleanup_error}"
                    ) from previous_error
                raise OAuthError(f"Could not replace the previous OAuth session: {previous_error}") from previous_error

        config.pop("api_key", None)
        config["oauth"] = replacement_oauth
        _write_config_unlocked(config)


def _save_refreshed_oauth_session(session) -> None:
    """Persist refreshed tokens for the active session without revoking it."""
    with _config_lock():
        config = _read_config()
        config.pop("api_key", None)
        config["oauth"] = _oauth_session_data(session)
        _write_config_unlocked(config)


def has_stored_oauth() -> bool:
    """True when ~/.tavily/config.json holds a native OAuth session."""
    data = _read_config().get("oauth")
    return isinstance(data, dict) and isinstance(data.get("access_token"), str)


def get_human_id() -> str | None:
    """Resolve the optional human_id with precedence: env var > config file.

    Returns None when unset — the CLI omits the header entirely in that case.
    """
    value = os.environ.get("TAVILY_HUMAN_ID")
    if value:
        return value
    return _read_config().get("human_id")


def get_api_base_url() -> str | None:
    """Resolve an optional API base URL with precedence: env var > config file."""
    value = os.environ.get("TAVILY_API_BASE_URL")
    if value:
        return value.rstrip("/")
    configured = _read_config().get("api_base_url")
    return configured.rstrip("/") if configured else None


def clear_credentials() -> ClearCredentialsResult:
    with _config_lock():
        config = _read_config()
        oauth = config.get("oauth")
        server_revoked: bool | None = None
        revocation_error: str | None = None
        if isinstance(oauth, dict):
            try:
                if _revoke_stored_oauth(oauth):
                    server_revoked = True
            except Exception as e:
                # Logout must still remove local credentials even when the remote
                # session cannot be revoked. Return the failure to the command so it
                # can report a partial result and exit non-zero.
                server_revoked = False
                revocation_error = str(e) or e.__class__.__name__
        if CONFIG_FILE.exists():
            config.pop("api_key", None)
            config.pop("oauth", None)
            if config:
                _write_config_unlocked(config)
            else:
                CONFIG_FILE.unlink()
    _clear_mcp_tokens()
    return ClearCredentialsResult(
        local_credentials_cleared=True,
        server_revoked=server_revoked,
        revocation_error=revocation_error,
    )


def _decode_jwt_payload(token: str) -> dict | None:
    """Decode a JWT payload without verification (for issuer/expiry checks only)."""
    import base64

    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    try:
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def _is_tavily_token(token: str) -> bool:
    """Check if a JWT was issued by Tavily's MCP server (issuer claim only)."""
    payload = _decode_jwt_payload(token)
    return bool(payload and payload.get("iss") == "https://mcp.tavily.com/")


def _is_valid_tavily_token(token: str) -> bool:
    """Check if a JWT is a Tavily-issued, non-expired token."""
    import time

    if not _is_tavily_token(token):
        return False
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp") if payload else None
    if exp is not None and time.time() >= exp:
        return False
    return True


def _get_mcp_token() -> str | None:
    """Find a valid Tavily OAuth token from ~/.mcp-auth/."""
    if not MCP_AUTH_DIR.is_dir():
        return None
    for token_file in MCP_AUTH_DIR.rglob("*_tokens.json"):
        try:
            data = json.loads(token_file.read_text())
            token = data.get("access_token")
            if token and _is_valid_tavily_token(token):
                return token
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _clear_mcp_tokens() -> None:
    """Remove Tavily OAuth tokens from ~/.mcp-auth so logout fully revokes access.

    Scoped to Tavily-issued tokens (by JWT issuer) so other MCP tools that share
    ~/.mcp-auth are left untouched, and removed regardless of expiry so no stale
    Tavily token lingers after logout.
    """
    if not MCP_AUTH_DIR.is_dir():
        return
    for token_file in MCP_AUTH_DIR.rglob("*_tokens.json"):
        try:
            data = json.loads(token_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        token = data.get("access_token")
        if token and _is_tavily_token(token):
            try:
                token_file.unlink()
            except OSError:
                pass


def _oauth_client_from_dict(data: dict):
    from tavily_cli.oauth import RegisteredClient

    client_id = data.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        return None
    return RegisteredClient(
        client_id=client_id,
        client_secret=data.get("client_secret") if isinstance(data.get("client_secret"), str) else None,
        token_endpoint_auth_method=data.get("token_endpoint_auth_method") or "none",
        redirect_uri=data.get("redirect_uri") or "http://127.0.0.1/callback",
    )


def _revoke_stored_oauth(data: dict) -> bool:
    """Revoke stored refresh/access tokens, returning whether any were attempted."""
    from tavily_cli.oauth import OAuthError, fetch_metadata, revoke_token

    tokens = [
        ("refresh_token", "refresh_token"),
        ("access_token", "access_token"),
    ]
    present_tokens = [
        (key, token_type_hint, data.get(key))
        for key, token_type_hint in tokens
        if isinstance(data.get(key), str) and data.get(key)
    ]
    if not present_tokens:
        return False

    client = _oauth_client_from_dict(data)
    if client is None:
        raise OAuthError("Stored OAuth client metadata is incomplete; server revocation was not attempted.")

    metadata = fetch_metadata()
    errors: list[str] = []
    for key, token_type_hint, token in present_tokens:
        try:
            revoke_token(
                metadata,
                client,
                token,
                token_type_hint=token_type_hint,
            )
        except OAuthError as e:
            errors.append(f"{key}: {e}")

    if errors:
        raise OAuthError("; ".join(errors))
    return True


def _get_oauth_access_token(config: dict) -> str | None:
    """Return a usable OAuth access token; get_api_key holds the transaction lock."""
    from tavily_cli.oauth import (
        OAuthError,
        OAuthSession,
        fetch_metadata,
        refresh_tokens,
        token_is_expired,
    )

    data = config.get("oauth")
    if not isinstance(data, dict):
        return None
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        return None
    if not token_is_expired(data.get("expires_at")):
        return access

    refresh = data.get("refresh_token")
    client = _oauth_client_from_dict(data)
    if not isinstance(refresh, str) or not refresh or client is None:
        return None
    try:
        tokens = refresh_tokens(fetch_metadata(), client, refresh)
        _save_refreshed_oauth_session(OAuthSession(tokens=tokens, client=client))
        return tokens.access_token
    except Exception as e:
        raise OAuthError(
            "Could not refresh the stored Tavily OAuth session. "
            "The stored credentials were not removed; retry the command, and run `tvly login` only if it continues. "
            f"Details: {e}"
        ) from e


def get_api_key() -> str | None:
    """Resolve credentials: env var > API key in config > OAuth in config > legacy ~/.mcp-auth."""
    key = os.environ.get("TAVILY_API_KEY")
    if key:
        return key

    with _config_lock():
        config = _read_config()
        key = config.get("api_key")
        if key:
            return key

        token = _get_oauth_access_token(config)
        if token:
            return token

    return _get_mcp_token()


def is_oauth_token(key: str) -> bool:
    """Check if a credential is an MCP OAuth JWT (vs a tvly-* API key)."""
    return not key.startswith("tvly-") and _decode_jwt_payload(key) is not None


def get_api_key_or_exit(*, json_mode: bool = False) -> str:
    """Get the API key or print an error and exit."""
    import sys

    from tavily_cli.oauth import OAuthError

    try:
        key = get_api_key()
    except OAuthError as e:
        from tavily_cli.common import handle_oauth_refresh_error

        handle_oauth_refresh_error(e, json_mode)
    if not key:
        if json_mode:
            from tavily_cli.common import emit_error

            emit_error(
                "authentication_required",
                "No Tavily API key found.",
                stage="auth",
                retryable=False,
            )
            sys.exit(3)
        from rich.console import Console
        console = Console(stderr=True)
        console.print("  [#FAA2FB]> Error:[/#FAA2FB] No Tavily API key found.")
        console.print()
        console.print("  Authenticate using one of:")
        console.print("    [#9BC0AE]tvly login[/#9BC0AE]")
        console.print("    [#9BC0AE]tvly login --api-key tvly-YOUR_KEY[/#9BC0AE]")
        console.print("    [dim]export TAVILY_API_KEY=tvly-YOUR_KEY[/dim]")
        console.print()
        console.print("  Get a key at [link=https://tavily.com]tavily.com[/link]")
        sys.exit(3)
    return key


def get_client(client_name: str | None = None, *, json_mode: bool = False):
    """Return the appropriate Tavily client (SDK or MCP) based on credential type."""
    key = get_api_key_or_exit(json_mode=json_mode)
    return _build_keyed_client(key, client_name=client_name)


def _build_keyed_client(key: str, client_name: str | None = None):
    """Build a keyed Tavily client (SDK or MCP) for the given credential."""
    human_id = get_human_id()
    if is_oauth_token(key):
        from tavily_cli.mcp_client import McpTavilyClient
        return McpTavilyClient(
            api_key=key,
            session_id=SESSION_ID,
            human_id=human_id,
            client_name=client_name,
        )
    from tavily import TavilyClient
    return TavilyClient(
        api_key=key,
        session_id=SESSION_ID,
        human_id=human_id,
        client_source="tavily-cli",
        client_name=client_name,
        api_base_url=get_api_base_url(),
    )


def get_client_or_keyless(client_name: str | None = None):
    """Return a Tavily client, falling back to keyless mode when no key is set."""
    key = get_api_key()
    if key:
        return _build_keyed_client(key, client_name=client_name), False
    from tavily import TavilyClient
    return (
        TavilyClient(
            session_id=SESSION_ID,
            human_id=get_human_id(),
            client_source="tavily-cli-keyless",
            client_name=client_name,
            api_base_url=get_api_base_url(),
        ),
        True,
    )


def require_api_key_friendly(command_name: str, *, json_mode: bool = False) -> str:
    """Return the API key, or print a friendly message and exit non-zero."""
    import sys

    from tavily_cli.oauth import OAuthError

    try:
        key = get_api_key()
    except OAuthError as e:
        from tavily_cli.common import handle_oauth_refresh_error

        handle_oauth_refresh_error(e, json_mode)
    if key:
        return key

    if json_mode:
        from tavily_cli.common import emit_error

        emit_error(
            "authentication_required",
            f"The {command_name} command requires authentication.",
            stage="auth",
            retryable=False,
        )
        sys.exit(3)

    from rich.console import Console
    console = Console(stderr=True)
    console.print()
    console.print(
        f"  [#FAA2FB]>[/#FAA2FB] The [bold]{command_name}[/bold] command requires a Tavily API key."
    )
    console.print()
    console.print("  Sign up for a free key at [link=https://tavily.com]https://tavily.com[/link]")
    console.print("  Then run [#9BC0AE]tvly login[/#9BC0AE] or [#9BC0AE]tvly login --api-key tvly-YOUR_KEY[/#9BC0AE]")
    console.print()
    console.print(
        "  [dim]Tip: [#9BC0AE]tvly search[/#9BC0AE] and [#9BC0AE]tvly extract[/#9BC0AE] "
        "work without an API key (subject to a rate-limit cap).[/dim]"
    )
    console.print()
    sys.exit(3)
