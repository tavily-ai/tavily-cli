"""Shared, offline command overview for the entry point and interactive shell."""

from __future__ import annotations

from io import StringIO

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from tavily_cli import __version__
from tavily_cli.theme import ACCENT, BRAND

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


def print_overview(console: Console, ctx: click.Context, *, shell: bool = False) -> None:
    """Describe registered commands without reading credentials or making requests."""
    group = ctx.command
    assert isinstance(group, click.Group)
    console.print()
    brand = Text("  tvly", style=f"bold {BRAND}")
    brand.append(f"  Tavily CLI  v{__version__}", style="dim")
    console.print(brand)
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
        print_overview(console, ctx)
        formatter.write(stream.getvalue())
