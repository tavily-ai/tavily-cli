"""Terminal rendering for the Tavily API's keyless rate-limit envelope."""

from __future__ import annotations

import re
from typing import Any

SIGNUP_URL = "https://app.tavily.com/?utm_source=tavily-cli&utm_medium=cli"
KEYLESS_NOTICE = "Keyless: search and extract only, subject to rate-limit caps. Run tvly login to authenticate."


def cli_next_actions(next_actions: list[Any] | None) -> list[dict[str, Any]]:
    """Replace API-only upgrade instructions with actions a CLI caller can use."""
    actions = [
        dict(action) for action in (next_actions or [])
        if isinstance(action, dict) and action.get("type") not in ("signup", "agentic_payment", "login")
    ]
    return [
        {"type": "login", "command": "tvly login", "instructions": "Sign in through your browser."},
        {
            "type": "signup", "url": SIGNUP_URL,
            "command": "tvly login --api-key tvly-YOUR_KEY",
            "instructions": "Create an API key in the dashboard, then run the command for full CLI option support.",
        },
        *actions,
    ]


def cli_limit_message(message: str | None) -> str:
    """Keep the allowance explanation, without HTTP-header retry instructions."""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", message or "")
    return " ".join(s for s in sentences if "retry-after" not in s.lower()).strip() or "Keyless allowance reached."


def _format_seconds(seconds: int) -> str:
    """Compact human duration for retry-after hints."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes}m {remainder}s" if remainder else f"{minutes}m"
    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h" if not minutes else f"{hours}h {minutes}m"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d" if not hours else f"{days}d {hours}h"


def format_keyless_envelope_for_terminal(
    *,
    message: str | None,
    retry_after_seconds: int | None,
    next_actions: list[Any] | None,
) -> str:
    """Render the keyless rate-limit envelope as a human-friendly terminal block."""
    lines: list[str] = []
    lines.append("Tavily rate limit reached.")

    lines.append(cli_limit_message(message))

    if isinstance(retry_after_seconds, int) and retry_after_seconds > 0:
        lines.append("")
        lines.append(f"Retry after: {_format_seconds(retry_after_seconds)}")

    next_actions = cli_next_actions(next_actions)
    if isinstance(next_actions, list) and next_actions:
        signup_action = None
        bonus_action = None
        for action in next_actions:
            if not isinstance(action, dict):
                continue
            action_type = action.get("type")
            if action_type == "signup":
                signup_action = action
            elif action_type == "bonus_credits" and action.get("eligible"):
                bonus_action = action

        if signup_action or bonus_action:
            lines.append("")
            lines.append("Continuation options:")

            if signup_action:
                url = signup_action["url"]
                lines.append(f"  - Sign up for a Tavily API key:  {url}")
                lines.append("    Then run: tvly login --api-key tvly-YOUR_KEY")

            if bonus_action:
                questions = bonus_action.get("questions") or []
                credits = bonus_action.get("credits_on_completion")
                endpoint = bonus_action.get("endpoint") or "/v1/keyless/bonus"
                if isinstance(questions, list) and questions:
                    if isinstance(credits, int):
                        lines.append(
                            f"  - Earn {credits} bonus credits by answering "
                            f"{len(questions)} question{'s' if len(questions) != 1 else ''}:"
                        )
                    else:
                        lines.append(
                            f"  - Earn bonus credits by answering "
                            f"{len(questions)} question{'s' if len(questions) != 1 else ''}:"
                        )
                    for i, question in enumerate(questions, start=1):
                        if isinstance(question, str):
                            lines.append(f"      {i}. {question}")
                    lines.append(f"    POST your answers to: {endpoint}")

    return "\n".join(lines)
