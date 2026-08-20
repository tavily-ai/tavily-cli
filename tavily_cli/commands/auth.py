"""Authentication commands: login, logout, auth status."""

from __future__ import annotations

import click

from tavily_cli.config import (
    CONFIG_FILE,
    clear_credentials,
    get_api_key,
    save_api_key,
    save_oauth_session,
)


@click.command()
@click.option("--api-key", default=None, help="Tavily API key (tvly-...). If omitted, opens browser for OAuth.")
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Print the login URL instead of opening a browser (SSH / headless).",
)
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def login(ctx: click.Context, api_key: str | None, no_browser: bool, json_flag: bool) -> None:
    """Authenticate with Tavily. Stores credentials for future use."""
    from tavily_cli.theme import err_console

    json_mode = json_flag
    if not json_mode and ctx.parent and ctx.parent.obj:
        json_mode = ctx.parent.obj.get("json_output", False)

    if api_key:
        save_api_key(api_key)
        _print_login_success("API key", f"Saved to {CONFIG_FILE}", json_mode=json_mode)
        return

    from tavily_cli.oauth import OAuthError, looks_headless, run_browser_login

    open_browser = not (no_browser or looks_headless())

    def _show_url(url: str) -> None:
        if json_mode:
            return
        err_console.print()
        if open_browser:
            err_console.print("  [#5CD9E6]>[/#5CD9E6] Opening the browser to sign in with Tavily.")
            err_console.print("    [dim]If nothing opens, visit:[/dim]")
        else:
            err_console.print("  [#5CD9E6]>[/#5CD9E6] Open this URL in a browser on this machine:")
        err_console.print(f"    {url}")
        err_console.print()
        if not open_browser:
            err_console.print(
                "    [dim]Headless / SSH sessions cannot complete browser login unless "
                "this machine can receive http://127.0.0.1 callbacks.[/dim]"
            )
            err_console.print("    [dim]Use[/dim] [#9BC0AE]tvly login --api-key tvly-YOUR_KEY[/#9BC0AE] [dim]instead.[/dim]")
            err_console.print()

    try:
        if json_mode:
            session = run_browser_login(open_browser=open_browser, on_status=None)
        else:
            with err_console.status("[#5CD9E6]Waiting for browser authorization...[/#5CD9E6]", spinner="dots"):
                session = run_browser_login(open_browser=open_browser, on_status=_show_url)
    except OAuthError as e:
        _print_login_failure(str(e), json_mode=json_mode)
        raise SystemExit(3) from e
    except KeyboardInterrupt:
        _print_login_failure("Login cancelled.", json_mode=json_mode)
        raise SystemExit(3) from None

    save_oauth_session(session)
    _print_login_success("OAuth", f"Token stored in {CONFIG_FILE}", json_mode=json_mode)


def _print_login_success(method: str, detail: str, *, json_mode: bool) -> None:
    """Print a branded success screen after login."""
    if json_mode:
        import json as json_mod

        click.echo(json_mod.dumps({
            "authenticated": True,
            "method": "oauth" if method == "OAuth" else "api_key",
            "detail": detail,
        }))
        return

    from rich.text import Text

    from tavily_cli.theme import LOGO, console

    console.print()
    console.print(LOGO)
    console.print()
    console.print(f"  [#9BC0AE]> Authenticated via {method}[/#9BC0AE]")
    console.print(f"    [dim]{detail}[/dim]")
    console.print()

    hints = Text()
    hints.append("  Get started\n\n", style="bold")
    hints.append("    tvly search ", style="#9BC0AE")
    hints.append('"your first query"', style="dim")
    hints.append("\n")
    hints.append("    tvly extract ", style="#9BC0AE")
    hints.append("<url>", style="dim")
    hints.append("\n")
    hints.append("    tvly crawl ", style="#9BC0AE")
    hints.append("<url>", style="dim")
    hints.append("\n")
    hints.append("    tvly map ", style="#9BC0AE")
    hints.append("<url>", style="dim")
    hints.append("\n")
    hints.append("    tvly research ", style="#9BC0AE")
    hints.append('"deep dive topic"', style="dim")
    hints.append("\n")
    console.print(hints)


def _print_login_failure(message: str, *, json_mode: bool) -> None:
    if json_mode:
        import json as json_mod

        click.echo(json_mod.dumps({
            "authenticated": False,
            "error": message,
        }))
        return

    from rich.markup import escape

    from tavily_cli.common import sanitize_control
    from tavily_cli.theme import err_console

    err_console.print()
    err_console.print(f"  [#FAA2FB]> {escape(sanitize_control(message))}[/#FAA2FB]")
    err_console.print()
    err_console.print("  If you don't have an account, sign up at [link=https://tavily.com]tavily.com[/link]")
    err_console.print("  Or use an API key:")
    err_console.print("    [#9BC0AE]tvly login --api-key tvly-YOUR_KEY[/#9BC0AE]")
    err_console.print()


@click.command()
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def logout(ctx: click.Context, json_flag: bool) -> None:
    """Clear stored Tavily credentials."""
    json_mode = json_flag
    if not json_mode and ctx.parent and ctx.parent.obj:
        json_mode = ctx.parent.obj.get("json_output", False)

    clear_credentials()
    if json_mode:
        import json as json_mod

        click.echo(json_mod.dumps({"authenticated": False}))
        return

    from tavily_cli.theme import err_console

    err_console.print("  [dim]Credentials cleared.[/dim]")
    err_console.print("  Run [#9BC0AE]tvly login[/#9BC0AE] to authenticate again.")


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
    method = None
    if key:
        if os.environ.get("TAVILY_API_KEY"):
            source = "TAVILY_API_KEY environment variable"
            method = "env"
        elif is_oauth_token(key):
            from tavily_cli.config import has_stored_oauth

            if has_stored_oauth():
                source = f"OAuth ({CONFIG_FILE})"
                method = "oauth"
            else:
                source = "OAuth (~/.mcp-auth/)"
                method = "oauth_legacy"
        elif CONFIG_FILE.exists():
            source = f"config file ({CONFIG_FILE})"
            method = "api_key"

    if json_mode:
        click.echo(json_mod.dumps({
            "authenticated": key is not None,
            "method": method,
            "source": source,
        }))
    else:
        console.print()
        if key:
            masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
            console.print(f"  [#9BC0AE]>[/#9BC0AE] Authenticated via {source}")
            console.print(f"    [dim]Key: {masked}[/dim]")
        else:
            console.print("  [#FAA2FB]>[/#FAA2FB] Not authenticated")
            console.print()
            console.print("  Run [#9BC0AE]tvly login[/#9BC0AE] to authenticate.")
        console.print()
