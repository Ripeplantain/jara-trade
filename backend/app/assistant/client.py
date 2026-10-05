"""The LLM client interface and the OpenRouter implementation.

The rest of the package only sees `LLMClient`: one streamed model turn in, text
deltas and a final `TurnEnd` out. Tests script a fake that implements it.
Messages and tool definitions use the OpenAI chat-completions shapes.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .config import AssistantSettings


class AssistantError(Exception):
    """A failure whose message is safe to show the user (never contains secrets)."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    parse_error: str | None = None  # set when the model's arguments were not valid JSON


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class TurnEnd:
    """Last event of a turn. `message` is appended to the history verbatim."""

    finish_reason: str  # "stop" | "tool_calls" | "length" | "content_filter" | ...
    message: dict = field(default_factory=lambda: {"role": "assistant", "content": ""})
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    model: str

    def stream_turn(
        self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> AsyncIterator[TextDelta | TurnEnd]: ...


def _status_message(status: int, model: str, detail: str) -> str:
    if status == 401:
        return "OpenRouter rejected the configured API key."
    if status == 402:
        return "The OpenRouter account has insufficient credits for this model."
    if status == 403:
        return "OpenRouter refused the request (forbidden or flagged by moderation)."
    if status == 404:
        return f"Model {model!r} was not found, or no provider for it supports tool calling."
    if status in (408, 429):
        return "The AI service is rate limited or busy. Try again in a moment."
    if status >= 500:
        return "The AI provider is unavailable right now."
    suffix = f": {detail}" if detail else ""
    return f"OpenRouter rejected the request (HTTP {status}){suffix}"


def _error_text(payload: Any) -> str:
    """Pull a short human message out of an OpenRouter error body or chunk."""
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or "")[:200]
    return str(err or "")[:200]


class OpenRouterClient:
    """Streaming chat completions over httpx (OpenAI-compatible API)."""

    def __init__(
        self, settings: AssistantSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not settings.api_key:
            raise AssistantError("OPENROUTER_API_KEY is not set")
        self._key = settings.api_key
        self._url = f"{settings.base_url}/chat/completions"
        self._site_url = settings.site_url
        self._transport = transport
        self.model = settings.model

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***")

    async def stream_turn(
        self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> AsyncIterator[TextDelta | TurnEnd]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": tools,
            "max_tokens": max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._site_url,
            "X-Title": "Jara Trade",
        }
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        saw_chunk = False
        stray: list[str] = []

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=httpx.Timeout(120.0, connect=10.0)
            ) as http:
                async with http.stream("POST", self._url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        raw = (await resp.aread())[:4000]
                        try:
                            detail = _error_text(json.loads(raw))
                        except ValueError:
                            detail = ""
                        raise AssistantError(
                            self._scrub(_status_message(resp.status_code, self.model, detail))
                        )
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):  # blank or ": OPENROUTER PROCESSING"
                            continue
                        if not line.startswith("data:"):
                            stray.append(line)
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        saw_chunk = True
                        if isinstance(chunk, dict) and chunk.get("error"):
                            detail = self._scrub(_error_text(chunk))
                            raise AssistantError(
                                "The AI provider failed mid-response"
                                + (f": {detail}" if detail else ".")
                            )
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            yield TextDelta(delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(
                                int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""}
                            )
                            fn = tc.get("function") or {}
                            slot["id"] = tc.get("id") or slot["id"]
                            slot["name"] = fn.get("name") or slot["name"]
                            slot["arguments"] += fn.get("arguments") or ""
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
        except httpx.TimeoutException as exc:
            raise AssistantError("The AI service timed out.") from exc
        except httpx.HTTPError as exc:
            raise AssistantError("Could not reach the AI service.") from exc

        if not saw_chunk and stray:  # a 200 with a plain JSON error body instead of SSE
            try:
                detail = self._scrub(_error_text(json.loads("".join(stray))))
            except ValueError:
                detail = ""
            raise AssistantError(f"The AI provider returned an error: {detail}".rstrip(": "))
        if finish_reason == "error":
            raise AssistantError("The AI provider failed mid-response.")

        tool_calls: list[ToolCall] = []
        for index in sorted(calls):
            slot = calls[index]
            call_id = slot["id"] or f"call_{index}"
            raw_args = slot["arguments"].strip()
            try:
                parsed = json.loads(raw_args) if raw_args else {}
                if not isinstance(parsed, dict):
                    raise ValueError("arguments must be a JSON object")
                tool_calls.append(ToolCall(call_id, slot["name"], parsed))
            except ValueError:
                tool_calls.append(
                    ToolCall(call_id, slot["name"], {}, parse_error="arguments were not valid JSON")
                )

        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.input)},
                }
                for c in tool_calls
            ]
        yield TurnEnd(
            finish_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
            message=message,
            tool_calls=tool_calls,
        )


def create_client(settings: AssistantSettings) -> LLMClient | None:
    """The real client, or None when no API key is configured."""
    return OpenRouterClient(settings) if settings.configured else None
