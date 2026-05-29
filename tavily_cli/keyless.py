"""Terminal rendering for the Tavily API's keyless rate-limit envelope."""

from __future__ import annotations

from typing import Any


def _format_seconds(seconds: int) -> str:
    """Compact human duration for retry-after hints."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s".rstrip(" 0s") or f"{seconds // 60}m"
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

    if isinstance(message, str) and message.strip():
        lines.append(message.strip())

    if isinstance(retry_after_seconds, int) and retry_after_seconds > 0:
        lines.append("")
        lines.append(f"Retry after: {_format_seconds(retry_after_seconds)} ({retry_after_seconds}s)")

    if isinstance(next_actions, list) and next_actions:
        signup_action = None
        payment_action = None
        bonus_action = None
        for action in next_actions:
            if not isinstance(action, dict):
                continue
            action_type = action.get("type")
            if action_type == "signup":
                signup_action = action
            elif action_type == "agentic_payment":
                payment_action = action
            elif action_type == "bonus_credits" and action.get("eligible"):
                bonus_action = action

        if signup_action or payment_action or bonus_action:
            lines.append("")
            lines.append("Continuation options:")

            if signup_action:
                url = signup_action.get("url") or "https://tavily.com"
                lines.append(f"  - Sign up for a Tavily API key:  {url}")

            if payment_action:
                scheme = payment_action.get("scheme") or "x402"
                details = payment_action.get("details") or "TBD"
                lines.append(f"  - Pay via {scheme} agentic payment:  {details}")

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
