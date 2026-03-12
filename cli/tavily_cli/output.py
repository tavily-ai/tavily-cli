"""Output formatting: Rich for humans, JSON for agents, -o for file output."""

from __future__ import annotations

import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


console = Console()
err_console = Console(stderr=True)


def emit(data: Any, *, json_mode: bool, output_file: str | None = None, pretty: bool = False) -> None:
    """Write JSON data to stdout (or a file). Used in --json mode."""
    text = json.dumps(data, indent=2 if pretty else None, ensure_ascii=False)
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        err_console.print(f"Output saved to {output_file}")
    else:
        click.echo(text)


def print_search_results(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    if output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    results = data.get("results", [])
    answer = data.get("answer")
    response_time = data.get("response_time")

    if answer:
        console.print(Panel(answer, title="Answer", border_style="green"))
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
        header.append(f"{i}. ", style="bold cyan")
        header.append(title, style="bold")
        if score is not None:
            header.append(f"  ({score:.2f})", style="dim")
        console.print(header)
        console.print(f"   [link={url}]{url}[/link]", style="dim")
        if content:
            console.print(f"   {content[:300]}")
        console.print()

    if response_time:
        console.print(f"[dim]{len(results)} results in {response_time:.2f}s[/dim]")

    images = data.get("images")
    if images:
        console.print()
        console.print(f"[bold]Images ({len(images)}):[/bold]")
        for img in images:
            if isinstance(img, dict):
                console.print(f"  {img.get('url', img)}")
            else:
                console.print(f"  {img}")


def print_extract_results(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode or output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    results = data.get("results", [])
    failed = data.get("failed_results", [])

    for r in results:
        url = r.get("url", "")
        raw = r.get("raw_content", "")
        console.print(Panel(raw[:2000] if raw else "[dim]No content[/dim]", title=url, border_style="green"))
        console.print()

    if failed:
        console.print("[yellow]Failed extractions:[/yellow]")
        for f_item in failed:
            console.print(f"  [red]✗[/red] {f_item.get('url')}: {f_item.get('error')}")

    response_time = data.get("response_time")
    if response_time:
        console.print(f"[dim]{len(results)} extracted, {len(failed)} failed in {response_time:.2f}s[/dim]")


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

    console.print(f"[bold]Crawled {len(results)} pages from[/bold] {base_url}")
    console.print()

    for r in results:
        url = r.get("url", "")
        raw = r.get("raw_content", "")
        preview = (raw[:200] + "...") if raw and len(raw) > 200 else (raw or "[dim]No content[/dim]")
        console.print(f"  [cyan]•[/cyan] {url}")
        console.print(f"    {preview}")
        console.print()

    response_time = data.get("response_time")
    if response_time:
        console.print(f"[dim]{len(results)} pages in {response_time:.2f}s[/dim]")


def _save_crawl_to_dir(data: dict, output_dir: str) -> None:
    """Save each crawled page as a .md file in the output directory."""
    import os
    import re
    from urllib.parse import urlparse

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


def print_map_results(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode or output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    results = data.get("results", [])
    base_url = data.get("base_url", "")

    console.print(f"[bold]Discovered {len(results)} URLs from[/bold] {base_url}")
    console.print()

    for url in results:
        console.print(f"  {url}")

    response_time = data.get("response_time")
    if response_time:
        console.print()
        console.print(f"[dim]{len(results)} URLs in {response_time:.2f}s[/dim]")


def print_research_result(data: dict, *, json_mode: bool, output_file: str | None = None) -> None:
    if json_mode or output_file:
        emit(data, json_mode=True, output_file=output_file, pretty=True)
        return

    status = data.get("status", "unknown")
    content = data.get("content", "")
    sources = data.get("sources", [])

    if status != "completed":
        console.print(f"[bold]Status:[/bold] {status}")
        if data.get("error"):
            console.print(f"[red]Error:[/red] {data['error']}")
        return

    if content:
        console.print(Panel(content, title="Research Report", border_style="green"))

    if sources:
        console.print()
        console.print(f"[bold]Sources ({len(sources)}):[/bold]")
        for s in sources:
            title = s.get("title", "")
            url = s.get("url", "")
            console.print(f"  • {title}")
            console.print(f"    [link={url}]{url}[/link]", style="dim")

    response_time = data.get("response_time")
    if response_time:
        console.print()
        console.print(f"[dim]Completed in {response_time:.2f}s[/dim]")
