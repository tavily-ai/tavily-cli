"""Interactive REPL — gives tvly a clean, chat-like shell."""

from __future__ import annotations

import readline  # noqa: F401 — enables arrow-key history in input()
import shlex

import click
from rich.console import Console

err_console = Console(stderr=True)


def _print_help() -> None:
    """Show the same command guide as the root CLI, without auth side effects."""
    from tavily_cli.cli import _print_welcome, cli
    with click.Context(cli, info_name="tvly") as ctx:
        _print_welcome(err_console, ctx, shell=True)


def _prompt() -> str:
    """Print the separator + prompt and read input."""
    err_console.print()
    try:
        # \001 and \002 tell readline to ignore non-printable chars for cursor math.
        prompt = "tvly > "
        if err_console.is_terminal and err_console.color_system and not err_console.no_color:
            prompt = "\001\033[38;2;92;217;230m\002tvly >\001\033[0m\002 "
        return input(prompt)
    except EOFError:
        return "exit"


def run_repl() -> None:
    """Enter the interactive REPL loop."""
    err_console.print()

    _print_help()

    while True:
        try:
            line = _prompt()
        except KeyboardInterrupt:
            err_console.print()
            err_console.print("  [dim]Goodbye![/dim]")
            err_console.print()
            break

        line = line.strip()
        if not line:
            continue

        if line in ("exit", "quit", "q"):
            err_console.print()
            err_console.print("  [dim]Goodbye![/dim]")
            err_console.print()
            break

        if line in ("help", "?"):
            _print_help()
            continue

        # Parse the line into args, dispatch through the CLI group.
        try:
            args = shlex.split(line)
        except ValueError as e:
            from rich.markup import escape

            from tavily_cli.common import sanitize_control
            err_console.print(f"  [#FAA2FB]Parse error:[/#FAA2FB] {escape(sanitize_control(e))}")
            continue

        # Strip leading "tvly" if user typed it out of habit.
        if args and args[0] == "tvly":
            args = args[1:]

        if not args or args == ["shell"]:
            continue

        # Dispatch via Click — invoke the CLI group with standalone_mode=False
        # so exceptions are caught and don't kill the REPL.
        from tavily_cli.cli import cli
        err_console.print()
        try:
            cli(args, standalone_mode=False)
        except SystemExit:
            # Commands raise SystemExit on error — just continue the REPL.
            pass
        except KeyboardInterrupt:
            # User pressed Ctrl+C to cancel a running command — just return to prompt.
            err_console.print()
            err_console.print("  [dim]Cancelled.[/dim]")
        except click.exceptions.UsageError as e:
            from rich.markup import escape

            from tavily_cli.common import sanitize_control
            err_console.print(f"  [#FAA2FB]>[/#FAA2FB] {escape(sanitize_control(e.format_message()))}")
        except Exception as e:
            from rich.markup import escape

            from tavily_cli.common import sanitize_control
            err_console.print(f"  [#FAA2FB]> Error:[/#FAA2FB] {escape(sanitize_control(e))}")

        err_console.print()
