"""Main CLI entry point — wires all commands into the `tavily` group."""

from __future__ import annotations

import click

from tavily_cli import __version__
from tavily_cli.commands.auth import auth_status, login, logout
from tavily_cli.commands.crawl import crawl
from tavily_cli.commands.extract import extract
from tavily_cli.commands.map_cmd import map_urls
from tavily_cli.commands.research import research
from tavily_cli.commands.search import search


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, default=False, help="Show version and exit.")
@click.option("--status", "show_status", is_flag=True, default=False, help="Show version and auth status.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Output as JSON (for agents and scripts).")
@click.pass_context
def cli(ctx: click.Context, version: bool, show_status: bool, json_output: bool) -> None:
    """Tavily CLI — search, extract, crawl, map, and research from the command line.

    Authenticate with: tavily login --api-key tvly-YOUR_KEY
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
        click.echo(ctx.get_help())


def _print_status(json_output: bool) -> None:
    """Show version + auth status (like Parallel's --status)."""
    import json

    from tavily_cli.config import get_api_key

    key = get_api_key()
    authenticated = key is not None

    if json_output:
        click.echo(json.dumps({
            "version": __version__,
            "authenticated": authenticated,
        }))
    else:
        from rich.console import Console
        console = Console()
        console.print(f"  [bold]tavily-cli[/bold] v{__version__}")
        console.print()
        if authenticated:
            import os
            if os.environ.get("TAVILY_API_KEY"):
                source = "TAVILY_API_KEY"
            else:
                source = "stored credentials"
            console.print(f"  [green]●[/green] Authenticated via {source}")
        else:
            console.print("  [red]●[/red] Not authenticated")
            console.print("    Run: tavily login")


cli.add_command(login)
cli.add_command(logout)
cli.add_command(auth_status)
cli.add_command(search)
cli.add_command(extract)
cli.add_command(crawl)
cli.add_command(map_urls)
cli.add_command(research)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
