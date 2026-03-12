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
    from tavily_cli.theme import console, err_console

    if api_key:
        save_api_key(api_key)
        _print_login_success("API key", f"Saved to {CONFIG_FILE}")
        return

    # OAuth flow via mcp-remote
    import subprocess
    import time

    from tavily_cli.config import _get_mcp_token

    # Clear stale client registrations that cause "client ID not found" errors
    _clear_stale_mcp_state()

    token = None
    with err_console.status("[bright_cyan]Waiting for browser authentication...[/bright_cyan]", spinner="dots") as live:
        proc = subprocess.Popen(
            ["npx", "-y", "mcp-remote", "https://mcp.tavily.com/mcp"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        timeout = 120
        elapsed = 0
        try:
            while elapsed < timeout:
                time.sleep(3)
                elapsed += 3
                live.update(f"[bright_cyan]Waiting for browser authentication... {elapsed}s[/bright_cyan]")
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
        _print_login_success("OAuth", "Token stored in ~/.mcp-auth/")
    else:
        err_console.print()
        err_console.print("  [red]> Authentication timed out.[/red]")
        err_console.print()
        err_console.print("  If you don't have an account, sign up at [link=https://tavily.com]tavily.com[/link]")
        err_console.print("  Or use an API key:")
        err_console.print("    [bright_cyan]tvly login --api-key tvly-YOUR_KEY[/bright_cyan]")
        err_console.print()
        raise SystemExit(3)


def _print_login_success(method: str, detail: str) -> None:
    """Print a branded success screen after login."""
    from rich.text import Text

    from tavily_cli.theme import LOGO, console

    console.print(LOGO)
    console.print()
    console.print(f"  [green]> Authenticated via {method}[/green]")
    console.print(f"    [dim]{detail}[/dim]")
    console.print()

    hints = Text()
    hints.append("  Get started\n\n", style="bold")
    hints.append("    tvly search ", style="bright_cyan")
    hints.append('"your first query"', style="dim")
    hints.append("\n")
    hints.append("    tvly extract ", style="bright_cyan")
    hints.append("<url>", style="dim")
    hints.append("\n")
    hints.append("    tvly crawl ", style="bright_cyan")
    hints.append("<url>", style="dim")
    hints.append("\n")
    hints.append("    tvly map ", style="bright_cyan")
    hints.append("<url>", style="dim")
    hints.append("\n")
    hints.append("    tvly research ", style="bright_cyan")
    hints.append('"deep dive topic"', style="dim")
    hints.append("\n")
    console.print(hints)


@click.command()
def logout() -> None:
    """Clear stored Tavily credentials."""
    from tavily_cli.theme import err_console

    clear_credentials()
    err_console.print("  [dim]Credentials cleared.[/dim]")
    err_console.print("  Run [bright_cyan]tvly login[/bright_cyan] to authenticate again.")


@click.command("auth")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def auth_status(ctx: click.Context, json_flag: bool) -> None:
    """Check authentication status."""
    import json as json_mod
    import os

    from tavily_cli.config import is_oauth_token
    from tavily_cli.theme import console

    json_mode = json_flag
    if not json_mode and ctx.parent and ctx.parent.obj:
        json_mode = ctx.parent.obj.get("json_output", False)

    key = get_api_key()
    source = None
    if key:
        if os.environ.get("TAVILY_API_KEY"):
            source = "TAVILY_API_KEY environment variable"
        elif is_oauth_token(key):
            source = "OAuth (~/.mcp-auth/)"
        elif CONFIG_FILE.exists():
            source = f"config file ({CONFIG_FILE})"

    if json_mode:
        click.echo(json_mod.dumps({
            "authenticated": key is not None,
            "source": source,
        }))
    else:
        console.print()
        if key:
            masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
            console.print(f"  [green]>[/green] Authenticated via {source}")
            console.print(f"    [dim]Key: {masked}[/dim]")
        else:
            console.print(f"  [red]>[/red] Not authenticated")
            console.print()
            console.print("  Run [bright_cyan]tvly login[/bright_cyan] to authenticate.")
        console.print()
