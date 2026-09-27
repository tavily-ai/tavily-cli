"""Search previews format API content without rewriting the response."""

import copy
import json
from io import StringIO

import pytest
from rich.console import Console

from tavily_cli import output


def render_search(monkeypatch, response, width=100):
    stream = StringIO()
    monkeypatch.setattr(output, "console", Console(file=stream, width=width, color_system=None))
    output.print_search_results(response, json_mode=False)
    return stream.getvalue()


@pytest.mark.parametrize("width", [40, 100, 180])
def test_page_whitespace_is_compact_and_wrapped_lines_stay_indented(monkeypatch, width):
    response = {
        "results": [{
            "title": "Lionel Messi",
            "url": "https://en.wikipedia.org/wiki/Lionel_Messi",
            "score": 0.72,
            "content": "\n\n".join(["From Wikipedia, the free encyclopedia", "Argentine footballer (born 1987)"] * 4),
        }],
        "response_time": 0.91,
    }
    rendered = render_search(monkeypatch, response, width)
    lines = rendered.splitlines()
    assert "\n\n\n" not in rendered
    assert all(line.startswith("   ") for line in lines[1:] if line.strip() and "1 result" not in line)
    assert all(len(line.rstrip()) <= min(width, 100) for line in lines)
    assert "score: 0.72" in rendered
    assert "1 result" in rendered and "0.91s" in rendered
    assert "─" not in rendered
    if width >= 100:
        assert "https://en.wikipedia.org/wiki/Lionel_Messi" in rendered
        assert len(lines) <= 9


def test_preview_formats_markdown_and_keeps_source_wording(monkeypatch):
    response = {"results": [{
        "title": "Messi &amp; Argentina",
        "url": "https://example.com/biography",
        "content": "# Lionel Messi\n\n**Argentine** footballer &amp; captain.\n\n"
                   "Read [his biography](https://example.com/bio).\n\n- Born in `Rosario`.<br>Plays football.",
    }]}
    original = copy.deepcopy(response)
    rendered = render_search(monkeypatch, response)
    compact = " ".join(rendered.split())
    assert "Messi & Argentina" in compact
    assert "Lionel Messi Argentine footballer & captain. Read his biography. Born in Rosario. Plays football." in compact
    assert "**" not in rendered and "# Lionel" not in rendered and "&amp;" not in rendered
    assert response == original


def test_empty_result_fields_and_zero_duration_are_readable(monkeypatch):
    rendered = render_search(monkeypatch, {
        "results": [{"title": None, "url": None, "content": None, "score": None}],
        "response_time": 0,
    })
    assert "1. Untitled" in rendered
    assert "1 result · 0.00s" in rendered
    assert "None" not in rendered


def test_truncated_preview_finishes_on_a_word_boundary(monkeypatch):
    rendered = render_search(monkeypatch, {"results": [{
        "title": "Biography", "url": "https://example.com", "content": "footballer " * 100,
    }]})
    preview = " ".join(line.strip() for line in rendered.splitlines() if "footballer" in line)
    assert preview.endswith("footballer…")
    assert len(preview) <= 360


def test_markup_and_control_sequences_cannot_become_terminal_commands(monkeypatch):
    rendered = render_search(monkeypatch, {"results": [{
        "title": "A\x1b[2Jtitle", "url": "javascript:alert(1)",
        "content": "[bold]literal[/bold] \x1b[2Jscreen &#x1b;[2Jencoded `code`",
    }]})
    assert "\x1b" not in rendered
    assert "[bold]literal[/bold]" in rendered
    assert "screen" in rendered and "encoded code" in rendered


def test_json_and_saved_output_preserve_original_content(capsys, tmp_path):
    response = {"results": [{"title": "Original", "content": "# Heading\n\n**Text** &amp; more"}]}
    output.print_search_results(response, json_mode=True)
    assert json.loads(capsys.readouterr().out) == response
    destination = tmp_path / "response.json"
    output.print_search_results(response, json_mode=False, output_file=str(destination))
    assert json.loads(destination.read_text()) == response
