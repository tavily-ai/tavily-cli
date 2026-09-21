"""Main CLI entry point — wires all commands into the `tvly` group."""

from __future__ import annotations

import click

from tavily_cli import __version__
from tavily_cli.commands.auth import auth_status, login, logout
from tavily_cli.commands.crawl import crawl
from tavily_cli.commands.extract import extract
from tavily_cli.commands.init import init_command
from tavily_cli.commands.map_cmd import map_urls
from tavily_cli.commands.research import research
from tavily_cli.commands.search import search
from tavily_cli.commands.update import update_command
from tavily_cli.commands.usage import usage_command


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, default=False, help="Show version and exit.")
@click.option("--status", "show_status", is_flag=True, default=False, help="Show version, auth mode and API-key credit usage.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Output as JSON (for agents and scripts).")
@click.pass_context
def cli(ctx: click.Context, version: bool, show_status: bool, json_output: bool) -> None:
    """Tavily CLI — search, extract, crawl, map, and research from the command line.

    First-time setup: tvly init

    Browser authentication: tvly login

    API-key authentication: tvly login --api-key tvly-YOUR_KEY

    Or set TAVILY_API_KEY environment variable.
    """
    ctx.ensure_object(dict)
    ctx.obj["json_output"] = json_output

    if version:
        if json_output:
            import json
            click.echo(json.dumps({"version": __version__}))
        else:
            click.echo(f"tavily-cli {__version__}")
        ctx.exit(0)
        return

    if show_status:
        _print_status(json_output)
        ctx.exit(0)
        return

    if ctx.invoked_subcommand is None:
        from tavily_cli.repl import run_repl
        run_repl()
        ctx.exit(0)


def _print_welcome() -> None:
    """Show a branded welcome screen with quick-start hints."""
    from rich.console import Console
    from rich.text import Text

    from tavily_cli.common import handle_oauth_refresh_error
    from tavily_cli.config import get_api_key
    from tavily_cli.oauth import OAuthError
    from tavily_cli.theme import LOGO

    console = Console(stderr=True)
    try:
        key = get_api_key()
    except OAuthError as e:
        handle_oauth_refresh_error(e, False)

    # Logo + version
    console.print()
    console.print(LOGO)
    console.print(f"  [dim]v{__version__}[/dim]")
    console.print()

    # Auth status
    if key:
        source = _auth_source(key)
        console.print(f"  [#9BC0AE]>[/#9BC0AE] Authenticated via {source}")
    else:
        console.print("  [#FAA2FB]>[/#FAA2FB] Not authenticated")
        console.print("    [dim]search and extract work without a key (with a rate-limit cap).[/dim]")
        console.print("    [dim]Run:[/dim] tvly login [dim]to remove the cap.[/dim]")

    console.print()

    # Quick-start commands
    commands = Text()
    commands.append("  Commands\n\n", style="bold")
    commands.append("    tvly search ", style="#9BC0AE")
    commands.append('"your query"', style="dim")
    commands.append("            Web search\n")
    commands.append("    tvly extract ", style="#9BC0AE")
    commands.append("<url>", style="dim")
    commands.append("                  Extract content\n")
    commands.append("    tvly crawl ", style="#9BC0AE")
    commands.append("<url>", style="dim")
    commands.append("                    Crawl a website\n")
    commands.append("    tvly map ", style="#9BC0AE")
    commands.append("<url>", style="dim")
    commands.append("                      Discover URLs\n")
    commands.append("    tvly research ", style="#9BC0AE")
    commands.append('"your query"', style="dim")
    commands.append("          Deep research\n")

    console.print(commands)
    console.print("  [dim]Add --json to any command for machine-readable output.[/dim]")
    console.print("  [dim]Add --help to any command for full options.[/dim]")
    console.print()


def _auth_source(key: str) -> str:
    """Describe how the user is authenticated."""
    import os

    from tavily_cli.config import is_oauth_token

    if os.environ.get("TAVILY_API_KEY"):
        return "TAVILY_API_KEY"
    if is_oauth_token(key):
        return "OAuth (tvly login)"
    return "API key"


def _print_status(json_output: bool) -> None:
    """Show auth status with a best-effort, bounded credit lookup for API keys."""
    import json

    from tavily_cli.common import error_payload, handle_oauth_refresh_error
    from tavily_cli.config import credential_mode, get_api_key
    from tavily_cli.keyless import KEYLESS_NOTICE
    from tavily_cli.oauth import OAuthError
    from tavily_cli.usage import USAGE_API_KEY_HINT, UsageError, fetch_usage, usage_summary

    try:
        key = get_api_key()
    except OAuthError as e:
        handle_oauth_refresh_error(e, json_output)
    authenticated = key is not None
    mode = credential_mode(key)
    payload = {
        "version": __version__,
        "authenticated": authenticated,
        "mode": mode,
        "source": _auth_source(key) if key else None,
        "usage": None,
    }
    if mode == "api_key":
        try:
            payload["usage"] = fetch_usage(key, timeout=5.0)
        except UsageError as exc:
            payload["usage_error"] = error_payload(
                "usage_unavailable", exc, stage="usage", retryable=exc.retryable,
            )["error"]
    else:
        payload["usage_unavailable_reason"] = USAGE_API_KEY_HINT if key else KEYLESS_NOTICE

    if json_output:
        click.echo(json.dumps(payload, indent=2))
    else:
        from rich.console import Console
        console = Console()
        console.print(f"  [bold #9BC0AE]tavily[/bold #9BC0AE] v{__version__}")
        console.print()
        if authenticated:
            source = _auth_source(key)
            console.print(f"  [#9BC0AE]>[/#9BC0AE] Authenticated via {source}")
        else:
            console.print(f"  {KEYLESS_NOTICE}")
        if payload["usage"] is not None:
            console.print(f"  {usage_summary(payload['usage'])}", markup=False, highlight=False)
        elif "usage_error" in payload:
            click.echo(f"Usage unavailable: {payload['usage_error']['message']}", err=True)
        elif mode == "oauth":
            console.print(f"  {USAGE_API_KEY_HINT}")


@click.command("status")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
@click.pass_context
def status_command(ctx: click.Context, json_output: bool) -> None:
    """Show version, auth mode and API-key credits (alias for --status)."""
    _print_status(json_output or (ctx.obj or {}).get("json_output", False))


cli.add_command(login)
cli.add_command(logout)
cli.add_command(auth_status)
cli.add_command(status_command)
cli.add_command(usage_command)
cli.add_command(init_command)
cli.add_command(search)
cli.add_command(extract)
cli.add_command(crawl)
cli.add_command(map_urls)
cli.add_command(research)
cli.add_command(update_command)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
