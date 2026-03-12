"""tavily research — deep research via the Tavily API (async: run/status/poll)."""

from __future__ import annotations

import json
import sys
import time

import click

from tavily_cli.common import handle_api_error, json_option


@click.group()
def research() -> None:
    """Deep research commands (run, status, poll)."""
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
        result = client.research(**kwargs)
    except Exception as e:
        handle_api_error(e, json_mode)

    request_id = result.get("request_id")

    if no_wait:
        emit({"request_id": request_id, "status": result.get("status", "pending")}, json_mode=True, output_file=output_file)
        return

    from rich.console import Console
    err_console = Console(stderr=True)

    if not json_mode:
        err_console.print(f"Research started (id: {request_id}). Polling every {poll_interval}s...")

    elapsed = 0
    response = result
    while elapsed < timeout:
        try:
            response = client.get_research(request_id)
        except Exception as e:
            handle_api_error(e, json_mode)

        status = response.get("status", "unknown")
        if status in ("completed", "failed"):
            break

        if not json_mode:
            err_console.print(f"  Status: {status}... ({elapsed}s elapsed)")

        time.sleep(poll_interval)
        elapsed += poll_interval
    else:
        if not json_mode:
            err_console.print(f"[yellow]Timed out after {timeout}s. Use 'tavily research poll {request_id}' to continue.[/yellow]")
        if json_mode:
            emit({"request_id": request_id, "status": "timeout"}, json_mode=True, output_file=output_file)
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
        from rich.console import Console
        console = Console()
        s = response.get("status", "unknown")
        console.print(f"[bold]Request:[/bold] {request_id}")
        console.print(f"[bold]Status:[/bold]  {s}")
        if s == "completed":
            console.print(f"[green]Research complete.[/green] Run 'tavily research poll {request_id}' to view results.")
        elif s == "failed":
            console.print(f"[red]Failed:[/red] {response.get('error', 'Unknown error')}")


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

    json_mode = _resolve_json(ctx, json_flag)
    client = get_client()

    from rich.console import Console
    err_console = Console(stderr=True)

    if not json_mode:
        err_console.print(f"Polling research task {request_id}...")

    elapsed = 0
    response = {}
    while elapsed < timeout:
        try:
            response = client.get_research(request_id)
        except Exception as e:
            handle_api_error(e, json_mode)

        status_val = response.get("status", "unknown")
        if status_val in ("completed", "failed"):
            break

        if not json_mode:
            err_console.print(f"  Status: {status_val}... ({elapsed}s elapsed)")

        time.sleep(poll_interval)
        elapsed += poll_interval
    else:
        if json_mode:
            emit({"request_id": request_id, "status": "timeout"}, json_mode=True, output_file=output_file)
        else:
            err_console.print(f"[yellow]Timed out after {timeout}s.[/yellow]")
        return

    print_research_result(response, json_mode=json_mode, output_file=output_file)
