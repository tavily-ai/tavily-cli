"""API key storage and retrieval for the Tavily CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import psutil

CONFIG_DIR = Path.home() / ".tavily"
CONFIG_FILE = CONFIG_DIR / "config.json"
_SESSION_FILE = CONFIG_DIR / "session.json"

MCP_AUTH_DIR = Path.home() / ".mcp-auth"


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
    ppid = os.getppid()
    gppid = _get_grandparent_pid()

    # Read existing sessions
    sessions: list[dict] = []
    if _SESSION_FILE.exists():
        try:
            data = json.loads(_SESSION_FILE.read_text())
            sessions = data.get("sessions", [])
        except (json.JSONDecodeError, OSError):
            sessions = []

    # 1. Match by PPID (terminal user — same shell every time)
    for entry in sessions:
        if entry.get("ppid") == ppid:
            return entry["session_id"]

    # 2. Match by grandparent PID (agent — PPID changes, grandparent stable)
    if gppid is not None:
        for entry in sessions:
            if entry.get("grandparent_pid") == gppid and _pid_alive(gppid):
                # Update PPID for this session
                entry["ppid"] = ppid
                _write_sessions(sessions)
                return entry["session_id"]

    # 3. No match — new session. Prune dead entries first.
    alive = [
        e for e in sessions
        if _pid_alive(e.get("ppid", -1)) or _pid_alive(e.get("grandparent_pid", -1))
    ]

    new_id = uuid4().hex
    alive.append({
        "session_id": new_id,
        "ppid": ppid,
        "grandparent_pid": gppid,
    })

    _write_sessions(alive)
    return new_id


def _write_sessions(sessions: list[dict]) -> None:
    """Persist session entries to disk."""
    old_umask = os.umask(0o077)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(json.dumps({"sessions": sessions}, indent=2) + "\n")
    finally:
        os.umask(old_umask)


SESSION_ID = _get_session_id()


def _read_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_config(data: dict) -> None:
    old_umask = os.umask(0o077)  # ensure new files are owner-only from creation, sets the new umask to 0o077 and returns whatever the previous umask was.
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.chmod(0o700)
        CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")
        CONFIG_FILE.chmod(0o600)
    finally:
        os.umask(old_umask)


def save_api_key(api_key: str) -> None:
    config = _read_config()
    config["api_key"] = api_key
    _write_config(config)


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


def clear_credentials() -> None:
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
    _clear_mcp_tokens()


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


def get_api_key() -> str | None:
    """Resolve the API key with precedence: env var > config file > MCP OAuth token."""
    key = os.environ.get("TAVILY_API_KEY")
    if key:
        return key

    config = _read_config()
    key = config.get("api_key")
    if key:
        return key

    return _get_mcp_token()


def is_oauth_token(key: str) -> bool:
    """Check if a credential is an MCP OAuth JWT (vs a tvly-* API key)."""
    return not key.startswith("tvly-") and _decode_jwt_payload(key) is not None


def get_api_key_or_exit() -> str:
    """Get the API key or print an error and exit."""
    import sys

    key = get_api_key()
    if not key:
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


def get_client(client_name: str | None = None):
    """Return the appropriate Tavily client (SDK or MCP) based on credential type."""
    key = get_api_key_or_exit()
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


def require_api_key_friendly(command_name: str) -> str:
    """Return the API key, or print a friendly message and exit non-zero."""
    import sys

    key = get_api_key()
    if key:
        return key

    from rich.console import Console
    console = Console(stderr=True)
    console.print()
    console.print(
        f"  [#FAA2FB]>[/#FAA2FB] The [bold]{command_name}[/bold] command requires a Tavily API key."
    )
    console.print()
    console.print("  Sign up for a free key at [link=https://tavily.com]https://tavily.com[/link]")
    console.print("  Then run [#9BC0AE]tvly login --api-key tvly-YOUR_KEY[/#9BC0AE]")
    console.print()
    console.print(
        "  [dim]Tip: [#9BC0AE]tvly search[/#9BC0AE] and [#9BC0AE]tvly extract[/#9BC0AE] "
        "work without an API key (subject to a rate-limit cap).[/dim]"
    )
    console.print()
    sys.exit(3)
