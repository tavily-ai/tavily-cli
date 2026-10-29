"""Human-readable output: Rich at a terminal, plain full text everywhere else (pipes, files, agents' shells)."""

from __future__ import annotations

import json
import re
from io import StringIO

import pytest
from rich.console import Console

from tavily_cli import output

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
    assert "score" not in lines[2]


def test_piped_search_max_chars_zero_prints_whole_content(piped, capsys):
    content = "word " * 200
    output.print_search_results({"results": [{"title": "T", "url": "https://a.example", "content": content}]},
                                json_mode=False, max_chars=0)
    assert capsys.readouterr().out.splitlines()[2] == content.strip()


def test_piped_search_output_file_saves_json_and_prints_snippets(piped, capsys, tmp_path):
    f = tmp_path / "r.json"
    content = "x" * 1000
    output.print_search_results({"results": [{"title": "T", "url": "https://a.example", "content": content}]},
                                json_mode=False, output_file=str(f))
    assert json.loads(f.read_text())["results"][0]["content"] == content
    assert capsys.readouterr().out.splitlines()[:2] == ["1. T", "https://a.example"]


def test_terminal_output_file_only_saves(terminal, tmp_path):
    f = tmp_path / "r.json"
    output.print_search_results({"results": [{"title": "T", "url": "https://a.example", "content": "c"}]},
                                json_mode=False, output_file=str(f))
    assert f.exists() and terminal.getvalue() == ""


def test_piped_search_without_results(piped, capsys):
    output.print_search_results({"results": []}, json_mode=False)
    assert capsys.readouterr().out == "No results found.\n"


def test_piped_extract_keeps_markdown_links_and_whole_page(piped, capsys):
    output.print_extract_results(
        {"results": [{"url": "https://x.example/p", "raw_content": PAGE}],
         "failed_results": [{"url": "https://y.example", "error": "timeout"}]},
        json_mode=False,
    )
    out = capsys.readouterr().out
    assert out.startswith("# https://x.example/p\n\n# Title\n\nSee [the report](https://example.com/report-2021.pdf)")
    assert "| 2021 | 30:32.03 |" in out and "x" * 4000 in out
    assert "\n\n\n" not in out  # runs of blank lines collapsed
    assert out.endswith("Failed: https://y.example: timeout\n")


def test_piped_map_and_crawl(piped, capsys):
    output.print_map_results({"results": ["https://a.example/1", LONG_URL]}, json_mode=False)
    assert capsys.readouterr().out == f"https://a.example/1\n{LONG_URL}\n"
    output.print_crawl_results({"results": [{"url": "https://a.example/1", "raw_content": "body"}]}, json_mode=False)
    assert capsys.readouterr().out == "# https://a.example/1\n\nbody\n\n"


def test_piped_research_report_and_sources(piped, capsys):
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
    assert LONG_URL in visible.splitlines()[1]  # the whole URL, not the domain, and not folded


def test_json_mode_unchanged(piped, capsys):
    output.print_search_results({"results": [{"title": "T", "url": "https://a.example"}]}, json_mode=True)
    assert '"url": "https://a.example"' in capsys.readouterr().out


class FakeClient:
    def __init__(self):
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if "fail" in query:
            raise RuntimeError(f"bad request for {query}")
        return {"query": query, "results": [{"title": f"T {query}", "url": f"https://a.example/{query}", "content": "c"}]}


@pytest.fixture
def fake_client(monkeypatch, piped):
    from tavily_cli import config

    client = FakeClient()
    monkeypatch.setattr(config, "get_client_or_keyless", lambda client_name=None: (client, False))
    return client


def test_multi_query_runs_each_with_shared_options(fake_client):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    result = CliRunner().invoke(cli, ["search", "one", "two", "--depth", "advanced"])
    assert result.exit_code == 0, result.output
    assert sorted(q for q, _ in fake_client.calls) == ["one", "two"]
    assert all(kw["search_depth"] == "advanced" for _, kw in fake_client.calls)
    lines = result.output.splitlines()
    assert lines[0] == "=== [1/2] one" and lines[1] == "1. T one" and "=== [2/2] two" in lines


def test_multi_query_reports_failures_and_keeps_other_results(fake_client):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    result = CliRunner().invoke(cli, ["search", "fail me", "ok"])
    assert result.exit_code == 0
    assert "error: bad request for fail me" in result.output and "1. T ok" in result.output
    result = CliRunner().invoke(cli, ["search", "fail a", "fail b"])
    assert result.exit_code == 4


def test_multi_query_json_is_a_list_in_query_order(fake_client):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    result = CliRunner().invoke(cli, ["search", "a", "fail b", "--json"])
    data = json.loads(result.output)
    assert [r["query"] for r in data] == ["a", "fail b"] and "error" in data[1]


def test_too_many_queries_is_a_usage_error(fake_client):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    result = CliRunner().invoke(cli, ["search", *"abcdef"])
    assert result.exit_code == 2 and "At most 5" in result.output and not fake_client.calls


def test_multi_query_shares_the_snippet_budget(fake_client, monkeypatch):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    long = "y" * 1000
    monkeypatch.setattr(fake_client, "search", lambda query, **kw: {"query": query, "results": [{"title": "T", "url": "https://a.example", "content": long}]})
    out = CliRunner().invoke(cli, ["search", "a", "b", "c"]).output.splitlines()
    assert out[3] == "y" * 200 + "..."  # 600 shared by 3 queries
    out = CliRunner().invoke(cli, ["search", "a"]).output.splitlines()
    assert out[2] == "y" * 600 + "..."
    out = CliRunner().invoke(cli, ["search", "a", "b", "--max-chars", "500"]).output.splitlines()
    assert out[3] == "y" * 500 + "..."


def test_output_file_creates_its_folder(piped, tmp_path):
    f = tmp_path / ".tavily" / "r.json"
    output.print_search_results({"results": []}, json_mode=True, output_file=str(f))
    assert json.loads(f.read_text()) == {"results": []}


def test_new_tavily_folder_ignores_itself(piped, tmp_path):
    f = tmp_path / ".tavily" / "r.json"
    output.print_search_results({"results": []}, json_mode=True, output_file=str(f))
    assert (tmp_path / ".tavily" / ".gitignore").read_text() == "*\n"
    other = tmp_path / "out" / "r.json"
    output.print_search_results({"results": []}, json_mode=True, output_file=str(other))
    assert not (tmp_path / "out" / ".gitignore").exists()


def test_bare_output_saves_under_tavily_folder(fake_client, tmp_path, monkeypatch):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["search", "Rust 2024 edition", "-o"])
    assert result.exit_code == 0, result.output
    saved = list((tmp_path / ".tavily").glob("search-rust-2024-edition-*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["results"]
    assert (tmp_path / ".tavily" / ".gitignore").read_text() == "*\n"
    assert "1. T Rust 2024 edition" in result.output  # still prints the results when piped


def test_explicit_output_path_is_unchanged(fake_client, tmp_path, monkeypatch):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["search", "q", "-o", "out.json"])
    assert (tmp_path / "out.json").exists() and not (tmp_path / ".tavily").exists()


def test_no_output_flag_writes_nothing(fake_client, tmp_path, monkeypatch):
    from click.testing import CliRunner

    from tavily_cli.cli import cli

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["search", "q"])
    assert list(tmp_path.iterdir()) == []


def test_generated_names_do_not_collide(tmp_path, monkeypatch):
    from tavily_cli.common import resolve_output

    monkeypatch.chdir(tmp_path)
    first = resolve_output("", "extract", "docs.example.com")
    assert first.startswith(".tavily/extract-docs-example-com-") and first.endswith(".json")
    (tmp_path / ".tavily").mkdir()
    (tmp_path / first).write_text("{}")
    assert resolve_output("", "extract", "docs.example.com") != first
    assert resolve_output(None, "search", "q") is None and resolve_output("x.json", "search", "q") == "x.json"
