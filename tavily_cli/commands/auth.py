"""Authentication commands: login, logout, auth status."""

from __future__ import annotations

import click

from tavily_cli.config import (
    CONFIG_FILE,
    MCP_AUTH_DIR,
    clear_credentials,
    get_api_key,
    save_api_key,
)


def _clear_stale_mcp_state() -> None:
    """Remove stale mcp-remote client registrations so OAuth can re-register fresh."""
    if not MCP_AUTH_DIR.is_dir():
        return
    for client_file in MCP_AUTH_DIR.rglob("*_client_info.json"):
        try:
            client_file.unlink()
        except OSError:
            pass
    for token_file in MCP_AUTH_DIR.rglob("*_tokens.json"):
        try:
            token_file.unlink()
        except OSError:
            pass


@click.command()
@click.option("--api-key", default=None, help="Tavily API key (tvly-...). If omitted, opens browser for OAuth.")
def login(api_key: str | None) -> None:
    """Authenticate with Tavily. Stores credentials for future use."""
    from rich.console import Console

    console = Console(stderr=True)

    if api_key:
        save_api_key(api_key)
        console.print("[green]API key saved.[/green]")
        console.print(f"  Config: {CONFIG_FILE}")
        return

    # OAuth flow via mcp-remote
    import subprocess
    import time

    from tavily_cli.config import _get_mcp_token

    # Clear stale client registrations that cause "client ID not found" errors
    _clear_stale_mcp_state()

    console.print("Opening browser for OAuth authentication...")
    console.print("Complete sign-in in your browser, then return here.")
    console.print()

    proc = subprocess.Popen(
        ["npx", "-y", "mcp-remote", "https://mcp.tavily.com/mcp"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    timeout = 120
    elapsed = 0
    token = None
    try:
        while elapsed < timeout:
            time.sleep(3)
            elapsed += 3
            token = _get_mcp_token()
            if token:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if token:
        console.print("[green]Authentication successful![/green]")
    else:
        console.print("[red]Authentication timed out.[/red]")
        console.print()
        console.print("If you don't have an account, sign up at [link=https://tavily.com]https://tavily.com[/link] first.")
        console.print("Or authenticate with an API key:")
        console.print("  tavily login --api-key tvly-YOUR_KEY")
        raise SystemExit(3)


@click.command()
def logout() -> None:
    """Clear stored Tavily credentials."""
    from rich.console import Console

    console = Console(stderr=True)
    clear_credentials()
    console.print("Credentials cleared.")


@click.command("auth")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def auth_status(ctx: click.Context, json_flag: bool) -> None:
    """Check authentication status."""
    import json as json_mod
    import os

    from rich.console import Console

    json_mode = json_flag
    if not json_mode and ctx.parent and ctx.parent.obj:
        json_mode = ctx.parent.obj.get("json_output", False)

    console = Console(stderr=True)

    key = get_api_key()
    source = None
    if key:
        if os.environ.get("TAVILY_API_KEY"):
            source = "TAVILY_API_KEY environment variable"
        elif CONFIG_FILE.exists():
            source = f"config file ({CONFIG_FILE})"
        else:
            source = "OAuth token (~/.mcp-auth/)"

    if json_mode:
        click.echo(json_mod.dumps({
            "authenticated": key is not None,
            "source": source,
        }))
    else:
        if key:
            masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
            console.print(f"[green]Authenticated[/green] via {source}")
            console.print(f"  Key: {masked}")
        else:
            console.print("[red]Not authenticated[/red]")
            console.print("Run: tavily login")
