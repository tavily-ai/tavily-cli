"""Human-readable output: Rich at a terminal, plain full text everywhere else (pipes, files, agents' shells)."""

from __future__ import annotations

import json
import re
from io import StringIO

import pytest
from click.testing import CliRunner
from rich.console import Console

from tavily_cli import output
from tavily_cli.cli import cli

LONG_URL = "https://www.example.com/fb-answers/systems-analysis-symposium-2021-nasa?locale=ur_PK&ref=" + "a" * 80
PAGE = "# Title\n\nSee [the report](https://example.com/report-2021.pdf) for **details**.\n\n\n\n| Year | Value |\n|---|---|\n| 2021 | 30:32.03 |\n" + "x" * 4000


@pytest.fixture
def piped(monkeypatch):
    monkeypatch.setattr(output, "console", Console(file=StringIO(), force_terminal=False))


@pytest.fixture
def terminal(monkeypatch):
    buf = StringIO()
    monkeypatch.setattr(output, "console", Console(file=buf, force_terminal=True, width=80))
    return buf


class FakeClient:
    def __init__(self):
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return {"query": query, "results": [{"title": f"T {query}", "url": f"https://a.example/{query}", "content": "c"}]}


@pytest.fixture
def fake_client(monkeypatch, piped):
    from tavily_cli import config

    client = FakeClient()
    monkeypatch.setattr(config, "get_client_or_keyless", lambda client_name=None: (client, False))
    return client


def test_piped_search_prints_full_urls_and_snippets(piped, capsys):
    content = "Personal bests\n\n10,000m 30:32.03\x1b[31m" + " passage" * 120
    output.print_search_results(
        {"answer": "She ran 30:32.03.", "results": [{"title": "Tegla", "url": LONG_URL, "content": content, "score": 0.9}]},
        json_mode=False,
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Answer: She ran 30:32.03."
    assert lines[2:4] == ["1. Tegla", LONG_URL]
    assert lines[4].startswith("Personal bests 10,000m 30:32.03[31m passage")  # one line, the ESC byte stripped
    assert len(lines[4]) == 603 and lines[4].endswith("...")


def test_piped_extract_keeps_markdown_links_and_whole_page(piped, capsys):
    output.print_extract_results(
        {"results": [{"url": "https://x.example/p", "raw_content": PAGE}],
         "failed_results": [{"url": "https://y.example", "error": "timeout"}]},
        json_mode=False,
    )
    out = capsys.readouterr().out
    assert out.startswith("# https://x.example/p\n\n# Title\n\nSee [the report](https://example.com/report-2021.pdf)")
    assert "x" * 4000 in out
    assert out.endswith("Failed: https://y.example: timeout\n")


def test_piped_map_crawl_and_research(piped, capsys):
    output.print_map_results({"results": ["https://a.example/1", LONG_URL]}, json_mode=False)
    assert capsys.readouterr().out == f"https://a.example/1\n{LONG_URL}\n"
    output.print_crawl_results({"results": [{"url": "https://a.example/1", "raw_content": "body"}]}, json_mode=False)
    assert capsys.readouterr().out == "# https://a.example/1\n\nbody\n\n"
    output.print_research_result(
        {"status": "completed", "content": "See [source](https://s.example).",
         "sources": [{"title": "S", "url": "https://s.example"}]},
        json_mode=False,
    )
    assert capsys.readouterr().out == "See [source](https://s.example).\n\nSources:\n1. S https://s.example\n"


def test_terminal_search_shows_full_url_on_one_line(terminal):
    output.print_search_results({"results": [{"title": "T", "url": LONG_URL, "content": "c", "score": 0.5}]}, json_mode=False)
    # Drop hyperlink targets and colours so only the visible text is checked.
    visible = re.sub(r"\x1b\]8;[^\x1b]*\x1b\\|\x1b\[[0-9;]*m", "", terminal.getvalue())
    assert LONG_URL in visible.splitlines()[1]


def test_multi_query_runs_each_with_shared_options(fake_client):
    result = CliRunner().invoke(cli, ["search", "one", "two", "--depth", "advanced"])
    assert result.exit_code == 0, result.output
    assert sorted(q for q, _ in fake_client.calls) == ["one", "two"]
    assert all(kw["search_depth"] == "advanced" for _, kw in fake_client.calls)
    lines = result.output.splitlines()
    assert lines[0] == "=== [1/2] one" and lines[1] == "1. T one" and "=== [2/2] two" in lines


def test_bare_output_saves_under_tavily_folder(fake_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["search", "Rust 2024 edition", "-o"])
    assert result.exit_code == 0, result.output
    saved = list((tmp_path / ".tavily").glob("search-rust-2024-edition-*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["results"]
    assert (tmp_path / ".tavily" / ".gitignore").read_text() == "*\n"
    assert "1. T Rust 2024 edition" in result.output  # still prints the results when piped


def test_explicit_output_path_is_unchanged(fake_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["search", "q", "-o", "out.json"])
    assert (tmp_path / "out.json").exists() and not (tmp_path / ".tavily").exists()
