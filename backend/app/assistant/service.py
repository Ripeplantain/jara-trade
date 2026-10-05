"""The chat loop: model turn, run tools, repeat. Yields the contract's SSE events as dicts."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from app.market import MarketDataService

from .client import AssistantError, LLMClient, TextDelta, TurnEnd
from .tools import READ_TOOLS, AssistantTools, ToolResult

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the assistant inside Jara Trade, a paper-trading app: the account is simulated, \
no real money moves.

- Ground every answer in tool data. Call tools for prices, the watchlist, the portfolio, \
P&L and trades; never invent or guess a price, position or number. If a tool has no data, say so.
- Be concise: a few short sentences, or a short list. Quote key numbers in USD.
- {data_note} Mention this when it matters to the answer.
- You can only propose trades, never place them. To suggest one, call propose_trade; the \
user then confirms or dismisses it in the app. After proposing, say it awaits their \
confirmation. Never claim a trade was executed.
- Give no promises or predictions of returns, and no guarantees. You may share reasoning \
and risks, framed as educational, not financial advice.
- Politely decline requests unrelated to the user's account, markets or trading.
"""


def build_system_prompt(market: MarketDataService) -> str:
    status = market.status()
    if status.source == "simulator" or status.mode == "simulated":
        note = "Prices are SIMULATED random-walk data, not real market quotes."
    elif status.mode == "eod":
        note = "Prices are real but END-OF-DAY closes (free data plan), not live quotes."
    else:
        note = "Prices come from a market data provider and may be delayed."
    return SYSTEM_PROMPT.format(data_note=note)


def _event_tool_content(content: dict) -> str:
    return json.dumps(content, separators=(",", ":"), default=str)


async def run_chat(
    client: LLMClient,
    tools: AssistantTools,
    system: str,
    history: list[dict],
    *,
    max_tokens: int,
    max_iterations: int,
) -> AsyncIterator[dict]:
    """Run the tool loop to completion. Never raises; failures become an `error` event."""
    messages = list(history)
    try:
        for _ in range(max_iterations):
            end: TurnEnd | None = None
            async for event in client.stream_turn(
                system=system, messages=messages, tools=tools.definitions, max_tokens=max_tokens
            ):
                if isinstance(event, TextDelta):
                    if event.text:
                        yield {"type": "text", "delta": event.text}
                else:
                    end = event
            if end is None:
                yield {"type": "error", "detail": "The AI service returned no response."}
                return

            if not end.tool_calls:
                if end.finish_reason == "length":
                    yield {"type": "error", "detail": "The response was cut off (token limit)."}
                elif end.finish_reason == "content_filter":
                    yield {"type": "error", "detail": "The response was blocked by a content filter."}
                elif not (end.message.get("content") or "").strip():
                    yield {"type": "error", "detail": "The model returned an empty response."}
                return
            if end.finish_reason == "length":  # tool arguments may be truncated: do not run them
                yield {"type": "error", "detail": "The response was cut off (token limit)."}
                return

            messages.append(end.message)
            for call in end.tool_calls:
                if call.name in READ_TOOLS:
                    yield {"type": "tool", "name": call.name}
                if call.parse_error:
                    result = ToolResult({"error": f"Invalid arguments: {call.parse_error}"}, True)
                else:
                    result = tools.run(call.name, call.input)
                if result.proposal is not None:
                    yield {"type": "trade_proposal", "proposal": result.proposal}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": _event_tool_content(result.content),
                    }
                )
        yield {"type": "error", "detail": "The assistant used too many tool steps. Try a simpler question."}
    except AssistantError as exc:
        yield {"type": "error", "detail": str(exc)}
    except Exception:  # noqa: BLE001 - never leak internals or the key to the client
        log.exception("assistant chat failed")
        yield {"type": "error", "detail": "The AI assistant ran into an unexpected error."}
