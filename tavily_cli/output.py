"""Output formatting: Rich for humans, JSON for agents, -o for file output.

All human-readable rendering treats result fields (titles, URLs, snippets,
answers, page content, source lists, error text) as untrusted: they originate
from the web, the Tavily API, or an MCP response. Rich does not strip raw
terminal escape sequences from rendered strings and parses ``[...]`` markup in
plain strings, so every untrusted field is routed through ``sanitize_control``
and rendered via ``Text``/validated links rather than markup-bearing f-strings.
"""

from __future__ import annotations

import json
import shlex
from html import unescape
from textwrap import shorten
from typing import Any
from urllib.parse import urlparse

import click
from markdown_it import MarkdownIt
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from tavily_cli.common import sanitize_control

console = Console()
err_console = Console(stderr=True)
_preview_markdown = MarkdownIt("commonmark").enable("table")


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


def _heading(label: str, detail: str | None = None) -> None:
    console.print(Text(label, style="bold #5CD9E6"), width=min(console.width, 100))
    if detail:
        console.print(_safe_link(detail, style="dim"), width=min(console.width, 100))
    console.print()


def _footer(label: str, count: int, unit: str, response_time: float | None) -> None:
    parts = [f"{count} {unit}"]
    if response_time is not None:
        parts.append(f"{response_time:.2f}s")
    console.print(Text(" · ".join(parts), style="dim"), width=min(console.width, 100))


def _markdown(content: Any) -> Markdown | Syntax:
    if isinstance(content, (dict, list)):
        return Syntax(json.dumps(content, indent=2, ensure_ascii=False), "json", word_wrap=True, background_color="default")
    document = Markdown(sanitize_control(content))
    # Markdown parsing decodes entities, including encoded control characters.
    pending = list(document.parsed)
    while pending:
        token = pending.pop()
        token.content = sanitize_control(token.content)
        pending.extend(token.children or [])
        for attr in ("href", "src"):
            if attr in token.attrs:
                target = sanitize_control(token.attrs[attr])
                try:
                    allowed = urlparse(target).scheme in {"http", "https"}
                except ValueError:
                    allowed = False
                token.attrs[attr] = target if allowed else ""
    return document


def _item(index: int, total: int, title: Text, url: str = "", *, body=None, meta: str = "") -> None:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(width=len(str(total)) + 1, style="bold #8385F9")
    table.add_column(ratio=1)
    table.add_row(Text(f"{index}."), title)
    if url:
        table.add_row("", _safe_link(url, style="#FAA2FB"))
    if meta:
        table.add_row("", _safe_text(meta, style="dim"))
    if body is not None:
        table.add_row("", body)
    console.print(table, width=min(console.width, 100))
    console.print()


def _page_title(page: dict, index: int) -> Text:
    title = page.get("title")
    if not title:
        for line in (page.get("raw_content") or "").splitlines():
            if line.startswith("# "):
                title = _search_preview(line)
                break
    return _safe_text(unescape(title or f"Page {index}"), style="bold")


def _failures(failed: list[dict]) -> None:
    if not failed:
        return
    _heading("Failed pages")
    for index, item in enumerate(failed, 1):
        _item(index, len(failed), _safe_text(item.get("error") or "Unknown error", style="#FFC769"), item.get("url") or "")



# ---------------------------------------------------------------------------
# JSON / file emit
# ---------------------------------------------------------------------------

def emit(data: Any, *, json_mode: bool, output_file: str | None = None, pretty: bool = False) -> None:
    """Write JSON data to stdout (or a file). Used in --json mode.

    json.dumps escapes control characters (incl. ESC) as \\uXXXX, so this path
    is safe from terminal-escape injection without extra stripping.
    """
    text = json.dumps(data, indent=2 if pretty else None, ensure_ascii=False)
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        err_console.print(f"Output saved to {output_file}")
    else:
        click.echo(text)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search_preview(content: str) -> str:
    """Flatten Markdown into a compact preview, keeping the source's wording."""
    parts = []
    for token in _preview_markdown.parse(sanitize_control(content)):
        if token.type == "inline":
            for child in token.children or []:
                if child.type in {"softbreak", "hardbreak"}:
                    parts.append(" ")
                elif child.type == "html_inline" and child.content.lower().startswith(("<br>", "<br/", "<br ")):
                    parts.append(" ")
                elif child.type in {"text", "code_inline", "image"}:
                    parts.append(child.content)
            parts.append(" ")
        elif token.type in {"fence", "code_block", "html_block"}:
            parts.extend((token.content, " "))
    # Markdown entity decoding can introduce control characters: sanitize again.
    return shorten(sanitize_control("".join(parts)), width=360, placeholder="…")


def print_search_results(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    if output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    results = data.get("results") or []
    answer = data.get("answer")
    _heading("Search")
    if answer:
        console.print(Text("Answer", style="bold"))
        console.print(_markdown(answer), width=min(console.width, 100))
        console.print()
    if not results:
        console.print(Text("No results found.", style="dim"))
        console.print()
    for index, result in enumerate(results, 1):
        title = sanitize_control(unescape(result.get("title") or "Untitled"))
        header = Text(" ".join(title.split()), style="bold")
        if result.get("score") is not None:
            header.append("  ")
            header.append_text(_score_label(result["score"]))
        preview = _search_preview(result.get("content") or "")
        _item(index, len(results), header, result.get("url") or "", body=_safe_text(preview) if preview else None)

    images = data.get("images") or []
    if images:
        _heading(f"Images ({len(images)})")
        for index, img in enumerate(images, 1):
            url = img.get("url", "") if isinstance(img, dict) else img
            description = img.get("description") if isinstance(img, dict) else None
            _item(index, len(images), _safe_text(description or f"Image {index}", style="bold"), url)
    _footer("Search", len(results), "result" if len(results) == 1 else "results", data.get("response_time"))


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def print_extract_results(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode or output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    results = data.get("results") or []
    failed = data.get("failed_results") or []
    _heading("Extract")
    if not results:
        console.print(Text("No pages extracted.", style="dim"))
        console.print()
    for index, page in enumerate(results, 1):
        raw = page.get("raw_content") or ""
        preview = raw[:3000]
        if len(raw) > 3000:
            preview = preview.rsplit(" ", 1)[0] + "…"
        if preview.startswith("# "):
            preview = preview.partition("\n")[2].lstrip()
        _item(
            index, len(results), _page_title(page, index), page.get("url") or "",
            meta=f"{len(raw):,} characters" + (" · preview" if len(raw) > 3000 else ""),
            body=_markdown(preview) if raw else Text("No content returned.", style="dim"),
        )
    _failures(failed)
    _footer("Extract", len(results), f"extracted · {len(failed)} failed", data.get("response_time"))
    if any(len(page.get("raw_content") or "") > 3000 for page in results):
        console.print(Text("Preview shown. Use --json for complete content.", style="dim"), width=min(console.width, 100))


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

    results = data.get("results") or []
    _heading("Crawl", data.get("base_url"))
    if not results:
        console.print(Text("No pages found.", style="dim"))
        console.print()
    for index, page in enumerate(results, 1):
        raw = page.get("raw_content") or ""
        _item(
            index, len(results), _page_title(page, index), page.get("url") or "",
            meta=f"{len(raw):,} characters",
            body=_safe_text(_search_preview(raw) if raw else "No content returned."),
        )
    _failures(data.get("failed_results") or [])
    _footer("Crawl", len(results), "page" if len(results) == 1 else "pages", data.get("response_time"))
    if results:
        console.print(Text("Content previews shown. Use --json or --output-dir for complete pages.", style="dim"), width=min(console.width, 100))


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

def print_map_results(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode or output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    results = data.get("results") or []
    _heading("Map", data.get("base_url"))
    if not results:
        console.print(Text("No URLs found.", style="dim"))
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(width=len(str(len(results))) + 1, style="dim")
    table.add_column(ratio=1)
    for index, url in enumerate(results, 1):
        table.add_row(Text(f"{index}."), _safe_link(url, style="#FAA2FB"))
    console.print(table, width=min(console.width, 100))
    console.print()
    _footer("Map", len(results), "URL" if len(results) == 1 else "URLs", data.get("response_time"))


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def print_feedback_result(data: dict, *, json_mode: bool) -> None:
    if json_mode:
        emit(data, json_mode=True, pretty=True)
        return

    feedback_id = data.get("feedback_id", "")
    console.print("  [#9BC0AE]>[/#9BC0AE] Feedback submitted", _safe_text(f" (feedback_id: {feedback_id})", style="dim"))

    response_time = data.get("response_time")
    _footer("Feedback", 1, "submitted", response_time)


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

def print_research_result(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode or output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    content = data.get("content")
    status = data.get("status") or ("completed" if content else "unknown")
    sources = data.get("sources") or []
    if status != "completed":
        print_research_status(data)
        return
    _heading("Research report")

    if content:
        console.print(_markdown(content), width=min(console.width, 100))
    else:
        console.print(Text("No report content returned.", style="dim"))
    console.print()
    if sources:
        _heading("Source details")
        for index, source in enumerate(sources, 1):
            if isinstance(source, dict):
                title = source.get("title") or f"Source {index}"
                url = source.get("url") or ""
            else:
                title, url = f"Source {index}", source
            _item(index, len(sources), _safe_text(unescape(title), style="bold"), url)
    _footer("Research", len(sources), "source" if len(sources) == 1 else "sources", data.get("response_time"))


def print_research_status(data: dict) -> None:
    """Render the task receipt for human run --no-wait and status commands."""
    _heading("Research")
    status = data.get("status") or "unknown"
    console.print(_safe_text(f"Status: {status}", style="bold"), width=min(console.width, 100))
    if data.get("error"):
        console.print(_safe_text(f"Error: {data['error']}", style="#FFC769"), width=min(console.width, 100))
    if data.get("request_id"):
        request_id = sanitize_control(data["request_id"])
        console.print(_safe_text(f"Request: {request_id}", style="dim"), width=min(console.width, 100))
        if status != "failed":
            verb = "View report" if status == "completed" else "Resume"
            console.print(_safe_text(f"{verb}: tvly research poll {shlex.quote(request_id)}"), width=min(console.width, 100))
