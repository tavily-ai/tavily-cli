"""Main CLI entry point — wires all commands into the `tvly` group."""

from __future__ import annotations

from io import StringIO

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

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
from tavily_cli.theme import ACCENT, BRAND, LOGO

_TOOL_USAGE = {
    "search": 'search "query"',
    "extract": "extract <urls...>",
    "crawl": "crawl <url>",
    "map": "map <url>",
    "research": 'research "topic"',
}
_TOOL_DESCRIPTION = {
    "search": "Search the web with ranked results and sources.",
    "extract": "Read clean content from one or more pages.",
    "crawl": "Explore a website and extract its pages.",
    "map": "Discover URLs across a website.",
    "research": "Create a research report with citations.",
}


class TavilyGroup(click.Group):
    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        stream = StringIO()
        terminal = Console()
        console = Console(
            file=stream,
            width=min(formatter.width, 100),
            force_terminal=ctx.color if ctx.color is not None else terminal.is_terminal,
            color_system=terminal.color_system,
            highlight=False,
        )
        _print_welcome(console, ctx)
        formatter.write(stream.getvalue())


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


def _print_welcome(console: Console, ctx: click.Context, *, shell: bool = False) -> None:
    """Show the branded command guide without reading credentials or making requests."""
    group = ctx.command
    assert isinstance(group, click.Group)
    console.print()
    console.print(LOGO, highlight=False)
    console.print(Text(f"  Tavily CLI  v{__version__}", style="dim"))
    console.print(Text("  Search the web. Read pages. Research anything.", style="dim"))
    console.print()
    console.print(Text("  Usage: tvly [OPTIONS] COMMAND [ARGS]..."))

    commands = {name: group.get_command(ctx, name) for name in group.list_commands(ctx)}
    for heading, names in (
        ("Web tools", list(_TOOL_USAGE)),
        ("Workspace & account", [name for name in commands if name not in _TOOL_USAGE]),
    ):
        console.print()
        console.print(Text(f"  {heading}", style=f"bold {ACCENT}"))
        rows = []
        for name in names:
            command = commands.get(name)
            if command is None or command.hidden or (shell and name == "shell"):
                continue
            description = _TOOL_DESCRIPTION.get(name) or command.get_short_help_str(limit=120)
            rows.append((_TOOL_USAGE.get(name, name), description))
        _rows(console, rows)

    if not shell:
        console.print()
        console.print(Text("  Options", style=f"bold {ACCENT}"))
        _rows(console, [record for param in group.get_params(ctx) if (record := param.get_help_record(ctx))])
    console.print()
    console.print(Text("  Quick start", style=f"bold {ACCENT}"))
    console.print(Text('  tvly search "Who is Leo Messi?"', style=BRAND))
    console.print(Text('  tvly search "latest AI news" --json', style=BRAND))
    console.print(Text("  tvly shell", style=BRAND))
    console.print()
    console.print(Text("  First-time setup: tvly init", style="dim"))
    console.print(Text("  Browser authentication: tvly login", style="dim"))
    console.print(Text("  API-key authentication: tvly login --api-key tvly-YOUR_KEY", style="dim"))
    console.print(Text("  Or set TAVILY_API_KEY in your environment.", style="dim"))
    console.print()
    console.print(Text("  search and extract work without a key, subject to a rate-limit cap.", style="dim"))
    console.print(Text("  Use tvly <command> --help for all options and output formats.", style="dim"))
    if shell:
        console.print(Text("  Type commands without tvly. Use help, exit, or Ctrl+C.", style="dim"))
    console.print()


def _rows(console: Console, rows: list[tuple[str, str]]) -> None:
    if console.width < 65:
        for label, description in rows:
            console.print(Text(f"  {label}", style=f"bold {BRAND}"))
            console.print(Text(f"    {description}", style="dim"))
        return
    table = Table.grid(padding=(0, 3), expand=True)
    table.add_column(style=f"bold {BRAND}", no_wrap=True)
    table.add_column(ratio=1)
    for label, description in rows:
        table.add_row(Text(f"  {label}"), Text(description))
    console.print(table)


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
