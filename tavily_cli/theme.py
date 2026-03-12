"""Tavily CLI theme — consistent branding, colors, and spinner helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from rich.console import Console
from rich.status import Status

# Brand colors
BRAND = "bright_cyan"
ACCENT = "blue"
SUCCESS = "green"
WARN = "yellow"
ERROR = "red"
DIM = "dim"

console = Console()
err_console = Console(stderr=True)

LOGO = """\
[bright_cyan]   _              _ _       [/bright_cyan]
[bright_cyan]  | |_ __ ___   _(_) |_   _ [/bright_cyan]
[bright_cyan]  | __/ _` \\ \\ / / | | | | |[/bright_cyan]
[bright_cyan]  | || (_| |\\ V /| | | |_| |[/bright_cyan]
[bright_cyan]   \\__\\__,_| \\_/ |_|_|\\__, |[/bright_cyan]
[bright_cyan]                       |___/ [/bright_cyan]"""

LOGO_COMPACT = "[bright_cyan bold]tavily[/bright_cyan bold]"


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
