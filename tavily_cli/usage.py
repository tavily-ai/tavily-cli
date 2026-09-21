"""API-key usage lookup and shared terminal summaries."""

from __future__ import annotations

from typing import Any

import httpx

from tavily_cli import config
from tavily_cli.common import TavilyAPIError, sanitize_control

USAGE_API_KEY_HINT = (
    "Usage reporting requires an API key; browser (OAuth) sessions cannot query credits. "
    "Run tvly login --api-key tvly-YOUR_KEY."
)


class UsageError(TavilyAPIError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message, status=status)
        self.retryable = retryable


def fetch_usage(api_key: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch /usage without sending MCP OAuth credentials to the API."""
    if config.credential_mode(api_key) != "api_key":
        raise UsageError(USAGE_API_KEY_HINT)
    base_url = config.get_api_base_url() or "https://api.tavily.com"
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/usage",
            headers={"Authorization": f"Bearer {api_key}", "X-Client-Source": "tavily-cli"},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        message = "Usage request was rejected; check your API key." if status == 401 else "Could not retrieve usage."
        raise UsageError(f"{message} (HTTP {status})", status=status, retryable=status == 429 or status >= 500) from exc
    except httpx.RequestError as exc:
        raise UsageError("Could not reach the usage API. Try tvly usage again.", retryable=True) from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise UsageError("Usage API returned invalid JSON.") from exc
    if not isinstance(data, dict) or not all(isinstance(data.get(name), dict) for name in ("key", "account")):
        raise UsageError("Usage API returned an invalid response; expected key and account objects.")
    for section, fields in (
        ("key", ("usage", "limit")),
        ("account", ("plan_usage", "plan_limit", "paygo_usage", "paygo_limit")),
    ):
        for field in fields:
            value = data[section].get(field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise UsageError(f"Usage API returned an invalid {section}.{field} value.")
    plan = data["account"].get("current_plan")
    if plan is not None and not isinstance(plan, str):
        raise UsageError("Usage API returned an invalid plan name.")
    return data


def _credits(used: Any, limit: Any) -> str:
    used_text = f"{used:,}" if used is not None else "not reported"
    limit_text = f"{limit:,}" if limit is not None else "not reported"
    return f"{used_text} / {limit_text} credits"


def usage_summary(data: dict[str, Any]) -> str:
    account = data["account"]
    plan = sanitize_control(account.get("current_plan") or "not reported")
    return f"Plan: {plan} | {_credits(account.get('plan_usage'), account.get('plan_limit'))}"


def usage_lines(data: dict[str, Any]) -> list[str]:
    key, account = data["key"], data["account"]
    return [
        usage_summary(data),
        f"API key: {_credits(key.get('usage'), key.get('limit'))}",
        f"PAYGO: {_credits(account.get('paygo_usage'), account.get('paygo_limit'))}",
    ]
