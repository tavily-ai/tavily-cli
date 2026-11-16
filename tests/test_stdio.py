"""Tests for the UTF-8 stdio setup done by the `tvly` entry point."""

from __future__ import annotations

import io

import pytest

from tavily_cli import cli as cli_module


def test_main_switches_non_utf8_stdio_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    # A Windows console on a CJK code page: GBK cannot encode Arabic or emoji.
    raw = io.BytesIO()
    gbk_stdout = io.TextIOWrapper(raw, encoding="gbk")
    monkeypatch.setattr(cli_module.sys, "stdout", gbk_stdout)

    cli_module._use_utf8_stdio()
    text = "الأخبار \U0001f600"
    print(text, file=cli_module.sys.stdout)
    gbk_stdout.flush()

    assert raw.getvalue().decode("utf-8").rstrip() == text


def test_non_reconfigurable_streams_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    replaced = io.StringIO()
    monkeypatch.setattr(cli_module.sys, "stdout", replaced)

    cli_module._use_utf8_stdio()

    assert cli_module.sys.stdout is replaced
