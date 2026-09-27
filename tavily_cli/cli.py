"""Main CLI entry point — wires all commands into the `tvly` group."""

from __future__ import annotations

import click

from tavily_cli import __version__
from tavily_cli.commands.auth import auth_status, login, logout
from tavily_cli.commands.crawl import crawl
from tavily_cli.commands.extract import extract
from tavily_cli.commands.feedback import feedback
from tavily_cli.commands.init import init_command
from tavily_cli.commands.map_cmd import map_urls
from tavily_cli.commands.research import research
from tavily_cli.commands.search import search
from tavily_cli.commands.update import update_command
from tavily_cli.help import TavilyGroup


@click.group(cls=TavilyGroup, invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--version", is_flag=True, default=False, help="Show version and exit.")
@click.option("--status", "show_status", is_flag=True, default=False, help="Show version and auth status.")
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
        if json_output:
            import json
            click.echo(json.dumps({"version": __version__, "commands": cli.list_commands(ctx)}))
        else:
            click.echo(ctx.get_help(), color=ctx.color)
        ctx.exit(0)


@click.command("shell")
@click.pass_context
def shell_command(ctx: click.Context) -> None:
    """Open the interactive command shell."""
    if (ctx.obj or {}).get("json_output"):
        raise click.UsageError("The interactive shell does not support --json. Run a tool command instead.")
    from tavily_cli.repl import run_repl
    run_repl()


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
    """Show version + auth status."""
    import json

    from tavily_cli.common import handle_oauth_refresh_error
    from tavily_cli.config import get_api_key
    from tavily_cli.oauth import OAuthError

    try:
        key = get_api_key()
    except OAuthError as e:
        handle_oauth_refresh_error(e, json_output)
    authenticated = key is not None

    if json_output:
        click.echo(json.dumps({
            "version": __version__,
            "authenticated": authenticated,
        }))
    else:
        from rich.console import Console
        console = Console()
        console.print(f"  [bold #9BC0AE]tavily[/bold #9BC0AE] v{__version__}")
        console.print()
        if authenticated:
            source = _auth_source(key)
            console.print(f"  [#9BC0AE]>[/#9BC0AE] Authenticated via {source}")
        else:
            console.print("  [#FAA2FB]>[/#FAA2FB] Not authenticated")
            console.print("    Run: tvly login")


cli.add_command(shell_command)
cli.add_command(login)
cli.add_command(logout)
cli.add_command(auth_status)
cli.add_command(init_command)
cli.add_command(search)
cli.add_command(extract)
cli.add_command(crawl)
cli.add_command(map_urls)
cli.add_command(research)
cli.add_command(feedback)
cli.add_command(update_command)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
