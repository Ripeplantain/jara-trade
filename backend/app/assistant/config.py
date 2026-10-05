"""Assistant settings. The only place that reads the environment for the assistant."""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# A free, tool-capable model so the demo costs nothing. Override with ASSISTANT_MODEL
# (any OpenRouter slug that supports tool calling).
DEFAULT_MODEL = "openai/gpt-oss-120b:free"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_MAX_ITERATIONS = 6
DEFAULT_SITE_URL = "http://localhost:3000"


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(minimum, int(raw)) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class AssistantSettings:
    """The API key lives here and is only ever sent to OpenRouter, never to the browser."""

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_iterations: int = DEFAULT_MAX_ITERATIONS  # model calls per chat request (tool loop bound)
    site_url: str = DEFAULT_SITE_URL  # optional HTTP-Referer attribution header

    def __repr__(self) -> str:  # keep the key out of tracebacks and logs
        return (
            f"AssistantSettings(api_key={'<set>' if self.api_key else None}, model={self.model!r}, "
            f"base_url={self.base_url!r})"
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> AssistantSettings:
        env = os.environ.get
        return cls(
            api_key=env("OPENROUTER_API_KEY", "").strip() or None,
            model=env("ASSISTANT_MODEL", "").strip() or DEFAULT_MODEL,
            base_url=(env("OPENROUTER_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/"),
            max_tokens=_int_env("ASSISTANT_MAX_TOKENS", DEFAULT_MAX_TOKENS, minimum=256),
            max_iterations=_int_env("ASSISTANT_MAX_ITERATIONS", DEFAULT_MAX_ITERATIONS),
            site_url=env("OPENROUTER_SITE_URL", "").strip() or DEFAULT_SITE_URL,
        )
