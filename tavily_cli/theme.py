"""Tavily CLI theme — consistent branding, colors, and spinner helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from rich.console import Console
from rich.status import Status
from rich.theme import Theme

# Brand colors — from tavily.com
AQUA = "#5CD9E6"
PINK = "#FAA2FB"
YELLOW = "#FFC769"
PURPLE = "#8385F9"
GREEN = "#9BC0AE"

BRAND = GREEN       # primary brand accent
ACCENT = AQUA       # secondary accent (headings, highlights)
SUCCESS = GREEN
WARN = YELLOW
ERROR = PINK
DIM = "dim"

# Custom theme to override Rich's default purple/blue Markdown colors
TAVILY_THEME = Theme({
    "markdown.link": f"{GREEN}",
    "markdown.link_url": f"dim",
    "markdown.h1": f"bold {AQUA}",
    "markdown.h2": f"bold {AQUA}",
    "markdown.h3": f"bold {AQUA}",
    "markdown.h4": f"bold {AQUA}",
    "markdown.h5": f"bold {AQUA}",
    "markdown.code": f"{YELLOW}",
    "markdown.item.number": f"bold {GREEN}",
    "markdown.item.bullet": f"bold {GREEN}",
    "table.header": f"bold {AQUA}",
})

console = Console(theme=TAVILY_THEME)
err_console = Console(stderr=True, theme=TAVILY_THEME)

LOGO = """\
[#5CD9E6]   _              _ _       [/#5CD9E6]
[#FAA2FB]  | |_ __ ___   _(_) |_   _ [/#FAA2FB]
[#FFC769]  | __/ _` \\ \\ / / | | | | |[/#FFC769]
[#8385F9]  | || (_| |\\ V /| | | |_| |[/#8385F9]
[#9BC0AE]   \\__\\__,_| \\_/ |_|_|\\__, |[/#9BC0AE]
[dim]                       |___/ [/dim]"""

LOGO_COMPACT = "[#9BC0AE bold]tavily[/#9BC0AE bold]"


@contextmanager
def spinner(message: str, *, json_mode: bool = False) -> Generator[None, None, None]:
    """Show a live spinner on stderr while work is in progress.

    In json_mode the spinner is suppressed so stdout stays clean.
    """
    if json_mode:
        yield
        return

    with err_console.status(f"[{BRAND}]{message}[/{BRAND}]", spinner="dots") as _status:
        yield
