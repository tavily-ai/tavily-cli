"""tavily research — deep research via the Tavily API (async: run/status/poll)."""

from __future__ import annotations

import json
import sys
import time

import click

from tavily_cli.common import handle_api_error, json_option


class ResearchGroup(click.Group):
    """Custom group that treats unknown subcommands as a query for 'run'."""

    # Subcommands that require a positional arg (request_id) after them.
    _SUBCOMMANDS_WITH_REQUIRED_ARG = {"status", "poll"}

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and not args[0].startswith("-"):
            first = args[0]
            if first not in self.commands:
                # Not a known subcommand — treat as a query for 'run'.
                args = ["run"] + args
            elif first in self._SUBCOMMANDS_WITH_REQUIRED_ARG:
                # 'status' and 'poll' require a request_id as the next arg.
                # If there's no next positional arg, the user likely meant it
                # as a research query (e.g., "tavily research status").
                # But always allow --help to pass through to the subcommand.
                has_positional = len(args) > 1 and not args[1].startswith("-")
                has_help = "--help" in args or "-h" in args
                if not has_positional and not has_help:
                    args = ["run"] + args
            # 'run' is always treated as the subcommand (use "tavily research run run" to research the word "run").
        return super().parse_args(ctx, args)


@click.group(cls=ResearchGroup)
def research() -> None:
    """Deep research commands (run, status, poll).

    You can run research directly: tavily research "your query"
    Or use subcommands: tavily research status <id>
    """
    pass


def _resolve_json(ctx: click.Context, local_flag: bool) -> bool:
    """Resolve --json from the local flag or any ancestor context."""
    if local_flag:
        return True
    while ctx:
        if ctx.obj and ctx.obj.get("json_output"):
            return True
        ctx = ctx.parent  # type: ignore[assignment]
    return False


@research.command()
@click.argument("query", required=False)
@click.option("--model", type=click.Choice(["mini", "pro", "auto"]), default=None, help="Research model (default: auto).")
@click.option("--no-wait", is_flag=True, default=False, help="Return request_id immediately without waiting.")
@click.option("--stream", is_flag=True, default=False, help="Stream results in real-time.")
@click.option("--output-schema", default=None, help="Path to JSON schema file for structured output.")
@click.option("--citation-format", type=click.Choice(["numbered", "mla", "apa", "chicago"]), default=None, help="Citation format.")
@click.option("--output", "-o", "output_file", default=None, help="Save output to file.")
@click.option("--poll-interval", type=int, default=10, help="Seconds between status checks (default: 10).")
@click.option("--timeout", type=int, default=600, help="Max seconds to wait (default: 600).")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def run(
    ctx: click.Context,
    query: str | None,
    model: str | None,
    no_wait: bool,
    stream: bool,
    output_schema: str | None,
    citation_format: str | None,
    output_file: str | None,
    poll_interval: int,
    timeout: int,
    json_flag: bool,
) -> None:
    """Start a research task.

    QUERY is the research topic. Use "-" to read from stdin.

    You can also run directly: tavily research "your query"
    """
    from tavily_cli.config import get_client
    from tavily_cli.output import emit, print_research_result

    json_mode = _resolve_json(ctx, json_flag)

    if query == "-":
        query = sys.stdin.read().strip()
    if not query:
        raise click.UsageError("QUERY is required. Pass a query string or use '-' to read from stdin.")

    client = get_client()

    schema = None
    if output_schema:
        with open(output_schema) as f:
            schema = json.load(f)

    kwargs: dict = {"input": query}
    if model is not None:
        kwargs["model"] = model
    if schema is not None:
        kwargs["output_schema"] = schema
    if citation_format is not None:
        kwargs["citation_format"] = citation_format

    from tavily_cli.theme import err_console, spinner

    if stream:
        kwargs["stream"] = True
        try:
            stream_resp = client.research(**kwargs)
            for chunk in stream_resp:
                if isinstance(chunk, bytes):
                    click.echo(chunk.decode("utf-8"), nl=False)
                else:
                    click.echo(chunk, nl=False)
        except Exception as e:
            handle_api_error(e, json_mode)
        return

    try:
        with spinner("Starting research...", json_mode=json_mode):
            result = client.research(**kwargs)
    except Exception as e:
        handle_api_error(e, json_mode)

    request_id = result.get("request_id")

    if no_wait:
        emit({"request_id": request_id, "status": result.get("status", "pending")}, json_mode=True, output_file=output_file)
        return

    elapsed = 0
    response = result
    if json_mode:
        # JSON mode: poll silently
        while elapsed < timeout:
            try:
                response = client.get_research(request_id)
            except Exception as e:
                handle_api_error(e, json_mode)
            if response.get("status") in ("completed", "failed"):
                break
            time.sleep(poll_interval)
            elapsed += poll_interval
        else:
            emit({"request_id": request_id, "status": "timeout"}, json_mode=True, output_file=output_file)
            return
    else:
        # Rich mode: live spinner with elapsed time
        with err_console.status("", spinner="dots") as live:
            while elapsed < timeout:
                live.update(f"[bright_cyan]Researching... {elapsed}s elapsed[/bright_cyan]")
                try:
                    response = client.get_research(request_id)
                except Exception as e:
                    handle_api_error(e, json_mode)
                status = response.get("status", "unknown")
                if status in ("completed", "failed"):
                    break
                time.sleep(poll_interval)
                elapsed += poll_interval
            else:
                err_console.print(f"[yellow]Timed out after {timeout}s. Resume with: tavily research poll {request_id}[/yellow]")
                return

    print_research_result(response, json_mode=json_mode, output_file=output_file)


@research.command()
@click.argument("request_id")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def status(ctx: click.Context, request_id: str, json_flag: bool) -> None:
    """Check the status of a research task."""
    from tavily_cli.config import get_client
    from tavily_cli.output import emit

    json_mode = _resolve_json(ctx, json_flag)
    client = get_client()

    try:
        response = client.get_research(request_id)
    except Exception as e:
        handle_api_error(e, json_mode)

    if json_mode:
        emit(response, json_mode=True)
    else:
        from tavily_cli.theme import console
        s = response.get("status", "unknown")
        status_style = {"completed": "green", "failed": "red"}.get(s, "yellow")
        console.print(f"  [bold]Request:[/bold]  {request_id}")
        console.print(f"  [bold]Status:[/bold]   [{status_style}]{s}[/{status_style}]")
        if s == "completed":
            console.print(f"  [dim]Run 'tavily research poll {request_id}' to view results.[/dim]")
        elif s == "failed":
            console.print(f"  [red]Error:[/red] {response.get('error', 'Unknown error')}")


@research.command()
@click.argument("request_id")
@click.option("--poll-interval", type=int, default=10, help="Seconds between status checks (default: 10).")
@click.option("--timeout", type=int, default=600, help="Max seconds to wait (default: 600).")
@click.option("--output", "-o", "output_file", default=None, help="Save output to file.")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def poll(ctx: click.Context, request_id: str, poll_interval: int, timeout: int, output_file: str | None, json_flag: bool) -> None:
    """Poll a research task until completion and return results."""
    from tavily_cli.config import get_client
    from tavily_cli.output import emit, print_research_result
    from tavily_cli.theme import err_console

    json_mode = _resolve_json(ctx, json_flag)
    client = get_client()

    elapsed = 0
    response = {}
    if json_mode:
        while elapsed < timeout:
            try:
                response = client.get_research(request_id)
            except Exception as e:
                handle_api_error(e, json_mode)
            if response.get("status") in ("completed", "failed"):
                break
            time.sleep(poll_interval)
            elapsed += poll_interval
        else:
            emit({"request_id": request_id, "status": "timeout"}, json_mode=True, output_file=output_file)
            return
    else:
        with err_console.status("", spinner="dots") as live:
            while elapsed < timeout:
                live.update(f"[bright_cyan]Polling research {request_id[:8]}... {elapsed}s elapsed[/bright_cyan]")
                try:
                    response = client.get_research(request_id)
                except Exception as e:
                    handle_api_error(e, json_mode)
                if response.get("status") in ("completed", "failed"):
                    break
                time.sleep(poll_interval)
                elapsed += poll_interval
            else:
                err_console.print(f"[yellow]Timed out after {timeout}s.[/yellow]")
                return

    print_research_result(response, json_mode=json_mode, output_file=output_file)
