"""Output formatting: Rich for humans and durable artifacts for agents.

All human-readable rendering treats result fields (titles, URLs, snippets,
answers, page content, source lists, error text) as untrusted: they originate
from the web, the Tavily API, or an MCP response. Rich does not strip raw
terminal escape sequences from rendered strings and parses ``[...]`` markup in
plain strings, so every untrusted field is routed through ``sanitize_control``
and rendered via ``Text``/validated links rather than markup-bearing f-strings.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from tavily_cli.common import sanitize_control

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_text(value: Any, *, style: str = "") -> Text:
    """Build a Rich Text from untrusted content.

    ``Text.append`` does not parse Rich markup and ``sanitize_control`` strips
    terminal escape bytes, so attacker-controlled fields cannot inject markup,
    fake hyperlinks, or ANSI/OSC control sequences.
    """
    return Text(sanitize_control(value), style=style)


def _safe_link(url: Any, label: Any | None = None, *, style: str = "") -> Text:
    """Render a possibly-attacker-controlled URL safely.

    The clickable link is applied only for http/https schemes, and the target
    is stripped of escape bytes, defeating OSC-8 hyperlink and Rich-markup link
    injection (e.g. a URL that closes ``[link]`` and opens its own).
    """
    clean_url = sanitize_control(url)
    display = sanitize_control(label) if label is not None else clean_url
    text = Text(display, style=style)
    try:
        scheme = urlparse(clean_url).scheme
    except ValueError:
        # urlparse rejects some malformed (attacker-controlled) URLs, e.g.
        # unbalanced brackets ("Invalid IPv6 URL"); render as plain text.
        scheme = ""
    if scheme in ("http", "https"):
        text.stylize(f"link {clean_url}")
    return text


def _score_label(score: float | None) -> Text:
    """Return a styled relevance score label."""
    if score is None:
        return Text("")
    label = Text()
    label.append(f" score: {score:.2f}", style="dim")
    return label


def _footer(label: str, count: int, unit: str, response_time: float | None) -> None:
    """Print a consistent footer line across all commands."""
    parts = [f"{count} {unit}"]
    if response_time:
        parts.append(f"{response_time:.2f}s")
    console.print()
    console.print(Rule(f"[dim]{' | '.join(parts)}[/dim]", style="dim"))


def _domain(url: str) -> str:
    """Extract domain from a URL."""
    try:
        return urlparse(url).netloc
    except Exception:
        return url


# ---------------------------------------------------------------------------
# JSON / artifact emit
# ---------------------------------------------------------------------------

_MARKDOWN_SUFFIXES = {".md", ".markdown"}


def validate_artifact_options(
    *,
    output_file: str | None,
    save: bool,
    force: bool,
) -> None:
    """Validate shared artifact flags before making an API request."""
    if output_file and save:
        raise click.UsageError("Use either --output or --save, not both.")
    if force and not (output_file or save):
        raise click.UsageError("--force requires --output or --save.")
    if output_file:
        path = Path(output_file)
        if not path.parent.exists():
            raise click.ClickException(f"Output directory does not exist: {path.parent}")
        if path.exists() and not force:
            raise click.ClickException(f"Refusing to overwrite existing file: {path}. Use --force to overwrite.")


def _json_text(data: Any, *, pretty: bool) -> str:
    return json.dumps(data, indent=2 if pretty else None, ensure_ascii=False) + "\n"


def _atomic_write_text(path: Path, text: str, *, force: bool, create_parents: bool = False) -> None:
    """Atomically publish a complete text file, refusing collisions by default."""
    parent = path.parent
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)
    elif not parent.exists():
        raise click.ClickException(f"Output directory does not exist: {parent}")

    if path.exists() and not force:
        raise click.ClickException(f"Refusing to overwrite existing file: {path}. Use --force to overwrite.")

    temp_path: Path | None = None
    try:
        fd, raw_temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
        temp_path = Path(raw_temp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        if force:
            os.replace(temp_path, path)
        else:
            # A hard-link publish is atomic and fails if another process creates
            # the destination between the preflight check and this operation.
            os.link(temp_path, path)
            temp_path.unlink()
        temp_path = None
    except FileExistsError as exc:
        raise click.ClickException(
            f"Refusing to overwrite existing file: {path}. Use --force to overwrite."
        ) from exc
    except OSError as exc:
        raise click.ClickException(f"Could not write artifact {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _artifact_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _print_artifact_summary(summary: str, paths: list[Path], *, json_mode: bool) -> None:
    rendered_paths = [str(path) for path in paths]
    if json_mode:
        click.echo(json.dumps({"saved": True, "summary": summary, "artifacts": rendered_paths}))
        return

    click.echo(summary)
    for path in rendered_paths:
        click.echo(f"Artifact: {path}")


def _save_result(
    data: dict,
    *,
    command: str,
    json_mode: bool,
    output_file: str | None,
    save: bool,
    force: bool,
    markdown_renderer: Callable[[dict], str],
    summary: str,
) -> bool:
    """Save one command result and print a bounded artifact summary."""
    if not output_file and not save:
        return False

    if save and command == "research":
        output_dir = Path(".tavily") / command / _artifact_timestamp()
        markdown_path = output_dir / "report.md"
        json_path = output_dir / "report.json"
        _atomic_write_text(markdown_path, markdown_renderer(data), force=force, create_parents=True)
        _atomic_write_text(json_path, _json_text(data, pretty=True), force=force, create_parents=True)
        _print_artifact_summary(summary, [markdown_path, json_path], json_mode=json_mode)
        return True

    path = Path(output_file) if output_file else Path(".tavily") / command / f"{_artifact_timestamp()}.json"
    use_markdown = not json_mode and path.suffix.lower() in _MARKDOWN_SUFFIXES
    text = markdown_renderer(data) if use_markdown else _json_text(data, pretty=True)
    _atomic_write_text(path, text, force=force, create_parents=save)
    _print_artifact_summary(summary, [path], json_mode=json_mode)
    return True


def emit(
    data: Any,
    *,
    json_mode: bool,
    output_file: str | None = None,
    pretty: bool = False,
    force: bool = True,
) -> None:
    """Write JSON data to stdout (or a file). Used in --json mode.

    json.dumps escapes control characters (incl. ESC) as \\uXXXX, so this path
    is safe from terminal-escape injection without extra stripping.
    """
    text = _json_text(data, pretty=pretty)
    if output_file:
        _atomic_write_text(Path(output_file), text, force=force)
        err_console.print(f"Output saved to {output_file}")
    else:
        click.echo(text, nl=False)


def _one_line(value: Any) -> str:
    return " ".join(sanitize_control(value).splitlines()).strip()


def _structured_markdown(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return f"```json\n{json.dumps(value, indent=2, ensure_ascii=False)}\n```"
    return sanitize_control(value)


def _search_markdown(data: dict) -> str:
    lines = ["# Search Results"]
    answer = data.get("answer")
    if answer:
        lines.extend(["", "## Answer", "", _structured_markdown(answer)])

    results = data.get("results") or []
    if results:
        lines.extend(["", "## Results"])
        for index, result in enumerate(results, 1):
            title = _one_line(result.get("title", "Untitled")) or "Untitled"
            lines.extend(["", f"### {index}. {title}", ""])
            if result.get("url"):
                lines.append(f"Source: {sanitize_control(result['url'])}")
            if result.get("score") is not None:
                lines.append(f"Score: {sanitize_control(result['score'])}")
            if result.get("content"):
                lines.extend(["", _structured_markdown(result["content"])])
            if result.get("raw_content"):
                lines.extend(["", "#### Full content", "", _structured_markdown(result["raw_content"])])
    elif not answer:
        lines.extend(["", "No results found."])

    images = data.get("images") or []
    if images:
        lines.extend(["", "## Images", ""])
        for image in images:
            if isinstance(image, dict):
                url = sanitize_control(image.get("url", ""))
                description = _one_line(image.get("description", ""))
                lines.append(f"- {url}" + (f" - {description}" if description else ""))
            else:
                lines.append(f"- {sanitize_control(image)}")

    return "\n".join(lines).rstrip() + "\n"


def _extract_markdown(data: dict) -> str:
    lines = ["# Extracted Content"]
    results = data.get("results") or []
    if not results:
        lines.extend(["", "No content extracted."])

    for index, result in enumerate(results, 1):
        url = sanitize_control(result.get("url", ""))
        lines.extend(["", f"## {index}. {_one_line(url) or 'Untitled'}", ""])
        if url:
            lines.extend([f"Source: {url}", ""])
        raw_content = result.get("raw_content")
        lines.append(_structured_markdown(raw_content) if raw_content else "No content.")

        images = result.get("images") or []
        if images:
            lines.extend(["", "### Images", ""])
            lines.extend(f"- {sanitize_control(image)}" for image in images)

    failed = data.get("failed_results") or []
    if failed:
        lines.extend(["", "## Failed Extractions", ""])
        for item in failed:
            lines.append(
                f"- {sanitize_control(item.get('url', ''))}: {sanitize_control(item.get('error', 'Unknown error'))}"
            )

    return "\n".join(lines).rstrip() + "\n"


def _map_markdown(data: dict) -> str:
    lines = ["# URL Map"]
    if data.get("base_url"):
        lines.extend(["", f"Base URL: {sanitize_control(data['base_url'])}"])
    results = data.get("results") or []
    if results:
        lines.append("")
        for result in results:
            url = result.get("url", "") if isinstance(result, dict) else result
            lines.append(f"- {sanitize_control(url)}")
    else:
        lines.extend(["", "No URLs found."])
    return "\n".join(lines).rstrip() + "\n"


def _research_markdown(data: dict) -> str:
    lines = ["# Research Report"]
    status = data.get("status", "unknown")
    lines.extend(["", f"Status: {sanitize_control(status)}"])
    if data.get("request_id"):
        lines.append(f"Request ID: {sanitize_control(data['request_id'])}")
    if data.get("error"):
        lines.extend(["", f"Error: {sanitize_control(data['error'])}"])

    content = data.get("content")
    if content:
        lines.extend(["", _structured_markdown(content)])

    sources = data.get("sources") or []
    if sources:
        lines.extend(["", "## Sources", ""])
        for index, source in enumerate(sources, 1):
            if isinstance(source, dict):
                title = _one_line(source.get("title", ""))
                url = sanitize_control(source.get("url", ""))
                label = title or url or f"Source {index}"
                lines.append(f"{index}. {label}" + (f" - {url}" if url and url != label else ""))
            else:
                lines.append(f"{index}. {sanitize_control(source)}")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def print_search_results(
    data: dict,
    *,
    json_mode: bool,
    output_file: str | None = None,
    save: bool = False,
    force: bool = False,
) -> None:
    results = data.get("results") or []
    if _save_result(
        data,
        command="search",
        json_mode=json_mode,
        output_file=output_file,
        save=save,
        force=force,
        markdown_renderer=_search_markdown,
        summary=f"Saved {len(results)} search result{'s' if len(results) != 1 else ''}.",
    ):
        return

    if json_mode:
        emit(data, json_mode=True, pretty=True)
        return

    answer = data.get("answer")
    response_time = data.get("response_time")

    if answer:
        console.print()
        console.print("  [#5CD9E6 bold]Answer[/#5CD9E6 bold]")
        console.print()
        console.print(Markdown(sanitize_control(answer)), width=min(console.width, 100))
        console.print()

    if not results:
        console.print("[dim]No results found.[/dim]")
        return

    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = r.get("content", "")
        score = r.get("score")

        header = Text()
        header.append(f"{i}. ", style="bold #8385F9")
        header.append(sanitize_control(title), style="bold")
        header.append("  ")
        header.append_text(_score_label(score))
        console.print(header)

        domain_line = Text("   ")
        domain_line.append_text(_safe_link(url, _domain(url), style="#FAA2FB"))
        console.print(domain_line)

        if content:
            snippet = content[:300]
            if len(content) > 300:
                snippet += "..."
            snippet_line = Text("   ")
            snippet_line.append(sanitize_control(snippet), style="dim")
            console.print(snippet_line)
        console.print()

    _footer("Search", len(results), "results", response_time)

    images = data.get("images")
    if images:
        console.print()
        console.print(f"[bold]Images ({len(images)}):[/bold]")
        for img in images:
            if isinstance(img, dict):
                console.print(_safe_text(f"  {img.get('url', img)}"))
            else:
                console.print(_safe_text(f"  {img}"))


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def print_extract_results(
    data: dict,
    *,
    json_mode: bool,
    output_file: str | None = None,
    save: bool = False,
    force: bool = False,
) -> None:
    results = data.get("results") or []
    failed = data.get("failed_results") or []
    if _save_result(
        data,
        command="extract",
        json_mode=json_mode,
        output_file=output_file,
        save=save,
        force=force,
        markdown_renderer=_extract_markdown,
        summary=f"Saved {len(results)} extraction{'s' if len(results) != 1 else ''} ({len(failed)} failed).",
    ):
        return

    if json_mode:
        emit(data, json_mode=True, pretty=True)
        return

    for r in results:
        url = r.get("url", "")
        raw = r.get("raw_content", "")
        char_count = len(raw) if raw else 0

        console.print()
        url_line = Text("  ")
        url_line.append(sanitize_control(url), style="#5CD9E6 bold")
        console.print(url_line)
        meta_line = Text("  ")
        meta_line.append(f"{sanitize_control(_domain(url))} ({char_count:,} chars)", style="dim")
        console.print(meta_line)
        console.print()
        if raw:
            console.print(Markdown(sanitize_control(raw[:3000])), width=min(console.width, 100))
            if len(raw) > 3000:
                console.print()
                console.print("  [dim]Content truncated. Use --save or -o article.md for the complete extraction.[/dim]")
        else:
            console.print("  [dim]No content[/dim]")
        console.print()

    if failed:
        console.print("[#FFC769]Failed extractions:[/#FFC769]")
        for f_item in failed:
            line = Text("  ")
            line.append("x ", style="#FAA2FB")
            line.append(f"{sanitize_control(f_item.get('url'))}: {sanitize_control(f_item.get('error'))}")
            console.print(line)

    response_time = data.get("response_time")
    _footer("Extract", len(results), f"extracted, {len(failed)} failed", response_time)


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

def print_crawl_results(
    data: dict,
    *,
    json_mode: bool,
    output_file: str | None = None,
    output_dir: str | None = None,
) -> None:
    if output_dir:
        _save_crawl_to_dir(data, output_dir)
        return

    if json_mode or output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    results = data.get("results", [])
    base_url = data.get("base_url", "")

    tree = Tree(_safe_text(base_url, style="bold"))

    # Group pages by path prefix for a hierarchical view
    for r in results:
        url = r.get("url", "")
        raw = r.get("raw_content", "")
        char_count = len(raw) if raw else 0

        # Show path relative to base
        try:
            parsed = urlparse(url)
            path = parsed.path or "/"
        except Exception:
            path = url

        label = Text()
        label.append(sanitize_control(path), style="#5CD9E6")
        label.append(f"  ({char_count:,} chars)", style="dim")

        node = tree.add(label)
        if raw:
            # First non-empty line as preview
            preview = raw.strip().split("\n")[0][:120]
            node.add(_safe_text(preview, style="dim"))

    console.print(tree)

    response_time = data.get("response_time")
    _footer("Crawl", len(results), "pages", response_time)


def _save_crawl_to_dir(data: dict, output_dir: str) -> None:
    """Save each crawled page as a .md file in the output directory."""
    import os
    import re

    os.makedirs(output_dir, exist_ok=True)
    results = data.get("results", [])

    for r in results:
        url = r.get("url", "")
        raw = r.get("raw_content", "")
        if not raw:
            continue

        parsed = urlparse(url)
        slug = re.sub(r"[^\w\-.]", "_", parsed.netloc + parsed.path.rstrip("/"))
        slug = slug.strip("_") or "index"
        filename = f"{slug}.md"

        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {url}\n\n{raw}\n")

    err_console.print(f"Saved {len(results)} pages to {output_dir}/")


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------

def print_map_results(
    data: dict,
    *,
    json_mode: bool,
    output_file: str | None = None,
    save: bool = False,
    force: bool = False,
) -> None:
    results = data.get("results") or []
    if _save_result(
        data,
        command="map",
        json_mode=json_mode,
        output_file=output_file,
        save=save,
        force=force,
        markdown_renderer=_map_markdown,
        summary=f"Saved {len(results)} URL{'s' if len(results) != 1 else ''}.",
    ):
        return

    if json_mode:
        emit(data, json_mode=True, pretty=True)
        return

    base_url = data.get("base_url", "")

    tree = Tree(_safe_text(base_url, style="bold"))
    for url in results:
        tree.add(_safe_link(url))

    console.print(tree)

    response_time = data.get("response_time")
    _footer("Map", len(results), "URLs", response_time)


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

def print_research_result(
    data: dict,
    *,
    json_mode: bool,
    output_file: str | None = None,
    save: bool = False,
    force: bool = False,
) -> None:
    sources = data.get("sources") or []
    if _save_result(
        data,
        command="research",
        json_mode=json_mode,
        output_file=output_file,
        save=save,
        force=force,
        markdown_renderer=_research_markdown,
        summary=f"Saved research report with {len(sources)} source{'s' if len(sources) != 1 else ''}.",
    ):
        return

    if json_mode:
        emit(data, json_mode=True, pretty=True)
        return

    status = data.get("status", "unknown")
    content = data.get("content", "")

    if status != "completed":
        status_line = Text()
        status_line.append("Status: ", style="bold")
        status_line.append(sanitize_control(status))
        console.print(status_line)
        if data.get("error"):
            error_line = Text()
            error_line.append("Error: ", style="#FAA2FB")
            error_line.append(sanitize_control(data["error"]))
            console.print(error_line)
        return

    # Render the research report as markdown
    if content:
        console.print()
        console.print("  [#5CD9E6 bold]Research Report[/#5CD9E6 bold]")
        console.print()
        console.print(Markdown(sanitize_control(content)), width=min(console.width, 100))

    # Sources as a numbered table
    if sources:
        console.print()
        table = Table(title=f"Sources ({len(sources)})", show_lines=False, padding=(0, 1))
        table.add_column("#", style="bold #8385F9", width=4)
        table.add_column("Title", style="bold", ratio=2)
        table.add_column("URL", style="#FAA2FB", ratio=3)

        for i, s in enumerate(sources, 1):
            title = s.get("title", "")
            url = s.get("url", "")
            table.add_row(str(i), _safe_text(title), _safe_link(url))

        console.print(table)

    response_time = data.get("response_time")
    _footer("Research", len(sources), "sources", response_time)
