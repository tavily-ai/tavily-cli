"""tvly research — deep research via the Tavily API (async: run/status/poll)."""

from __future__ import annotations

import base64
import json
import os
import sys
import time

import click
from rich.markup import escape

from tavily_cli.common import client_name_option, handle_api_error, sanitize_control


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
                # as a research query (e.g., "tvly research status").
                # But always allow --help to pass through to the subcommand.
                has_positional = len(args) > 1 and not args[1].startswith("-")
                has_help = "--help" in args or "-h" in args
                if not has_positional and not has_help:
                    args = ["run"] + args
            # 'run' is always treated as the subcommand (use "tvly research run run" to research the word "run").
        return super().parse_args(ctx, args)


@click.group(cls=ResearchGroup)
def research() -> None:
    """Deep research commands (run, status, poll).

    You can run research directly: tvly research "your query"
    Or use subcommands: tvly research status <id>
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


_FILE_TYPES = (".txt", ".md", ".json")
_MAX_FILES = 5
_MAX_WORDS = 80_000
_MAX_DOMAINS = 20


def _split_domains(value: str | None, flag: str) -> list[str] | None:
    if not value:
        return None
    domains = [d.strip() for d in value.split(",") if d.strip()]
    if len(domains) > _MAX_DOMAINS:
        raise click.UsageError(f"{flag} accepts at most {_MAX_DOMAINS} domains.")
    return domains or None


def _read_files(paths: tuple[str, ...]) -> list[dict] | None:
    """Encode --file arguments as the API's files entries (.txt/.md/.json, up to 5, 80,000 words)."""
    if not paths:
        return None
    if len(paths) > _MAX_FILES:
        raise click.UsageError(f"--file can be given at most {_MAX_FILES} times.")
    entries: list[dict] = []
    total_words = 0
    for path in paths:
        name = os.path.basename(path)
        if not name.lower().endswith(_FILE_TYPES):
            raise click.UsageError(f"--file {name}: supported types are {', '.join(_FILE_TYPES)}.")
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            raise click.UsageError(f"--file {name}: {e.strerror or e}.")
        words = len(raw.decode("utf-8", errors="replace").split())
        total_words += words
        if words > _MAX_WORDS:
            raise click.UsageError(f"--file {name} has {words} words; the limit is {_MAX_WORDS} per file.")
        entries.append({"name": name, "data": base64.b64encode(raw).decode("ascii"), "type": "base64"})
    if total_words > _MAX_WORDS:
        raise click.UsageError(f"--file contents total {total_words} words; the limit is {_MAX_WORDS} across all files.")
    return entries


def _create_research(client, kwargs: dict) -> dict:
    """Create a research task. The API documents HTTP 201 for creation; tavily-python 0.8.x only
    accepts 200 and then returns None, so 201 is normalised to 200 on this one call."""
    session = getattr(client, "session", None)
    if session is None or kwargs.get("stream"):
        return client.research(**kwargs)
    original = session.post

    def post(url, *args, **kw):
        response = original(url, *args, **kw)
        if response.status_code == 201 and str(url).endswith("/research"):
            response.status_code = 200
        return response

    session.post = post
    try:
        return client.research(**kwargs)
    finally:
        session.post = original


def _sse_records(stream_resp):
    """Yield (event, data_text) for each complete SSE record in a stream of bytes or str chunks.

    Records are separated by a blank line; LF, CRLF and CR line endings are accepted and a record may be
    split across transport chunks (or inside a multi-byte character). A final record without a trailing
    blank line is still delivered at end of stream.
    """
    import codecs
    import re

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""

    def split(record: str):
        event, data = None, []
        for line in record.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        return event, "\n".join(data)

    for raw_chunk in stream_resp:
        buffer += decoder.decode(raw_chunk) if isinstance(raw_chunk, bytes) else raw_chunk
        buffer = re.sub(r"\r\n?", "\n", buffer)
        while "\n\n" in buffer:
            record, buffer = buffer.split("\n\n", 1)
            if record.strip():
                yield split(record)
    buffer += decoder.decode(b"", final=True)
    buffer = re.sub(r"\r\n?", "\n", buffer)
    if buffer.strip():
        yield split(buffer)


def _render_stream(stream_resp, *, output_file: str | None = None) -> None:
    """Parse the Research SSE stream, show live status, then render like a non-stream result.

    The report is only rendered as completed when the terminal `event: done` frame arrived; an error
    frame or a stream that ends early fails the command instead of presenting partial output.
    """
    from tavily_cli.output import print_research_result
    from tavily_cli.theme import err_console

    content_parts: list[str] = []
    structured = None
    sources: list[dict] = []
    current_step: str | None = None
    done = False

    for event, data_text in _sse_records(stream_resp):
        if event == "done":
            done = True
            break
        try:
            data = json.loads(data_text) if data_text else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        if event == "error" or data.get("object") == "error":
            detail = data.get("error") or data_text or "unknown error"
            raise click.ClickException(f"Research stream failed: {sanitize_control(str(detail))}")

        delta = (data.get("choices") or [{}])[0].get("delta", {})

        # --- Tool calls: show live status updates ---
        tool_calls = delta.get("tool_calls", {})
        if tool_calls.get("type") == "tool_call":
            for tc in tool_calls.get("tool_call", []):
                name = tc.get("name", "")
                args = tc.get("arguments", "")
                step = f"{name}: {args}" if args else name
                if step != current_step:
                    current_step = step
                    err_console.print(f"  [dim]{escape(sanitize_control(step))}[/dim]")

        if tool_calls.get("type") == "tool_response":
            for tr in tool_calls.get("tool_response", []):
                name = tr.get("name", "")
                if name == "WebSearch":
                    src_count = len(tr.get("sources", []))
                    if src_count:
                        err_console.print(f"  [dim]Found {src_count} sources[/dim]")

        # --- Content: collect the report text (structured output arrives as an object) ---
        content = delta.get("content")
        if isinstance(content, str):
            if content:
                content_parts.append(content)
        elif content is not None:
            structured = content

        # --- Sources: collect final source list ---
        src_list = delta.get("sources")
        if src_list:
            sources = src_list

    if not done:
        raise click.ClickException(
            "Research stream ended before the final 'done' event; the report may be incomplete. "
            "Re-run, or use `tvly research run --no-wait` and `tvly research poll`."
        )

    result = {
        "status": "completed",
        "content": structured if structured is not None else "".join(content_parts),
        "sources": sources,
    }
    print_research_result(result, json_mode=False, output_file=output_file)


@research.command()
@click.argument("query", required=False)
@click.option("--model", type=click.Choice(["mini", "pro", "auto"]), default=None, help="Research model (default: auto).")
@click.option("--no-wait", is_flag=True, default=False, help="Return request_id immediately without waiting.")
@click.option("--stream", is_flag=True, default=False, help="Stream results in real-time.")
@click.option("--output-schema", default=None, help="Path to JSON schema file for structured output.")
@click.option("--citation-format", type=click.Choice(["numbered", "mla", "apa", "chicago"]), default=None, help="Citation format.")
@click.option("--include-domains", default=None, help="Comma-separated domains the research should prefer (soft preference, up to 20). API-key login only.")
@click.option("--exclude-domains", default=None, help="Comma-separated domains never to use (hard block, up to 20). API-key login only.")
@click.option("--output-length", type=click.Choice(["short", "standard", "long"]), default=None, help="Target report length (default: standard). API-key login only.")
@click.option("--file", "files", multiple=True, type=click.Path(), help="Attach a .txt, .md or .json file as a source (repeat up to 5 times; 80,000 words max). API-key login only.")
@click.option("--output", "-o", "output_file", default=None, help="Save output to file.")
@click.option("--poll-interval", type=int, default=10, help="Seconds between status checks (default: 10).")
@click.option("--timeout", type=int, default=600, help="Max seconds to wait (default: 600).")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@client_name_option
@click.pass_context
def run(
    ctx: click.Context,
    query: str | None,
    model: str | None,
    no_wait: bool,
    stream: bool,
    output_schema: str | None,
    citation_format: str | None,
    include_domains: str | None,
    exclude_domains: str | None,
    output_length: str | None,
    files: tuple[str, ...],
    output_file: str | None,
    poll_interval: int,
    timeout: int,
    json_flag: bool,
    client_name: str | None,
) -> None:
    """Start a research task.

    QUERY is the research topic. Use "-" to read from stdin.

    You can also run directly: tvly research "your query"

    Requires a Tavily API key. Sign up at https://tavily.com
    """
    from tavily_cli.config import get_client, require_api_key_friendly
    from tavily_cli.output import emit, print_research_result

    json_mode = _resolve_json(ctx, json_flag)

    if query == "-":
        query = sys.stdin.read(100_000).strip()
    if not query:
        raise click.UsageError("QUERY is required. Pass a query string or use '-' to read from stdin.")

    require_api_key_friendly("research", json_mode=json_mode)
    client = get_client(client_name=client_name, json_mode=json_mode)

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
    domains_in = _split_domains(include_domains, "--include-domains")
    domains_out = _split_domains(exclude_domains, "--exclude-domains")
    if domains_in:
        kwargs["include_domains"] = domains_in
    if domains_out:
        kwargs["exclude_domains"] = domains_out
    if output_length is not None:
        kwargs["output_length"] = output_length
    file_entries = _read_files(files)
    if file_entries:
        kwargs["files"] = file_entries

    from tavily_cli.theme import err_console, spinner

    if stream:
        kwargs["stream"] = True
        try:
            stream_resp = client.research(**kwargs)
            if json_mode:
                # Re-serialize each SSE event as one JSON line (NDJSON) rather
                # than forwarding raw SSE bytes: this keeps the output valid,
                # machine-parseable JSON and lets json.dumps escape any control
                # characters in server content (raw bytes would otherwise reach
                # the terminal as escape sequences).
                done = False
                for event_name, data_text in _sse_records(stream_resp):
                    if event_name == "done":
                        done = True
                        break
                    try:
                        event = json.loads(data_text) if data_text else None
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if event is None:
                        continue
                    click.echo(json.dumps(event, ensure_ascii=False))
                    if event_name == "error" or (isinstance(event, dict) and event.get("object") == "error"):
                        raise click.ClickException("Research stream failed: see the error event above.")
                if not done:
                    raise click.ClickException(
                        "Research stream ended before the final 'done' event; the output may be incomplete."
                    )
            else:
                _render_stream(stream_resp, output_file=output_file)
        except Exception as e:
            handle_api_error(e, json_mode)
        return

    try:
        with spinner("Starting research...", json_mode=json_mode):
            result = _create_research(client, kwargs)
    except Exception as e:
        handle_api_error(e, json_mode)

    # If the initial response is already complete (e.g., MCP endpoint returns
    # the full result synchronously), skip polling entirely.
    if result.get("status") in ("completed", "failed") or result.get("content"):
        print_research_result(result, json_mode=json_mode, output_file=output_file)
        return

    request_id = result.get("request_id")
    if not request_id:
        # No request_id and not complete — unexpected response.
        handle_api_error(RuntimeError(f"Unexpected API response: {result}"), json_mode)

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
        # Rich mode: live spinner with running elapsed time
        with err_console.status("", spinner="dots") as live:
            next_poll = 0
            while elapsed < timeout:
                live.update(f"[#5CD9E6]Researching... {elapsed}s elapsed[/#5CD9E6]")
                if elapsed >= next_poll:
                    try:
                        response = client.get_research(request_id)
                    except Exception as e:
                        handle_api_error(e, json_mode)
                    if response.get("status", "unknown") in ("completed", "failed"):
                        break
                    next_poll = elapsed + poll_interval
                time.sleep(1)
                elapsed += 1
            else:
                err_console.print(f"[#FFC769]Timed out after {timeout}s. Resume with: tvly research poll {escape(sanitize_control(request_id))}[/#FFC769]")
                return

    print_research_result(response, json_mode=json_mode, output_file=output_file)


@research.command()
@click.argument("request_id")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@client_name_option
@click.pass_context
def status(ctx: click.Context, request_id: str, json_flag: bool, client_name: str | None) -> None:
    """Check the status of a research task."""
    from tavily_cli.config import get_client, require_api_key_friendly
    from tavily_cli.output import emit

    json_mode = _resolve_json(ctx, json_flag)
    require_api_key_friendly("research status", json_mode=json_mode)
    client = get_client(client_name=client_name, json_mode=json_mode)

    try:
        response = client.get_research(request_id)
    except Exception as e:
        handle_api_error(e, json_mode)

    if json_mode:
        emit(response, json_mode=True)
    else:
        from tavily_cli.theme import console
        s = response.get("status", "unknown")
        status_style = {"completed": "#9BC0AE", "failed": "#FAA2FB"}.get(s, "#FFC769")
        safe_request_id = escape(sanitize_control(request_id))
        console.print(f"  [bold]Request:[/bold]  {safe_request_id}")
        console.print(f"  [bold]Status:[/bold]   [{status_style}]{escape(sanitize_control(s))}[/{status_style}]")
        if s == "completed":
            console.print(f"  [dim]Run 'tvly research poll {safe_request_id}' to view results.[/dim]")
        elif s == "failed":
            console.print(f"  [#FAA2FB]Error:[/#FAA2FB] {escape(sanitize_control(response.get('error', 'Unknown error')))}")


@research.command()
@click.argument("request_id")
@click.option("--poll-interval", type=int, default=10, help="Seconds between status checks (default: 10).")
@click.option("--timeout", type=int, default=600, help="Max seconds to wait (default: 600).")
@click.option("--output", "-o", "output_file", default=None, help="Save output to file.")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")
@client_name_option
@click.pass_context
def poll(
    ctx: click.Context,
    request_id: str,
    poll_interval: int,
    timeout: int,
    output_file: str | None,
    json_flag: bool,
    client_name: str | None,
) -> None:
    """Poll a research task until completion and return results."""
    from tavily_cli.config import get_client, require_api_key_friendly
    from tavily_cli.output import emit, print_research_result
    from tavily_cli.theme import err_console

    json_mode = _resolve_json(ctx, json_flag)
    require_api_key_friendly("research poll", json_mode=json_mode)
    client = get_client(client_name=client_name, json_mode=json_mode)

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
            next_poll = 0
            while elapsed < timeout:
                live.update(f"[#5CD9E6]Polling research {escape(sanitize_control(request_id[:8]))}... {elapsed}s elapsed[/#5CD9E6]")
                if elapsed >= next_poll:
                    try:
                        response = client.get_research(request_id)
                    except Exception as e:
                        handle_api_error(e, json_mode)
                    if response.get("status") in ("completed", "failed"):
                        break
                    next_poll = elapsed + poll_interval
                time.sleep(1)
                elapsed += 1
            else:
                err_console.print(f"[#FFC769]Timed out after {timeout}s.[/#FFC769]")
                return

    print_research_result(response, json_mode=json_mode, output_file=output_file)
