"""The real client against httpx.MockTransport. No network."""
from __future__ import annotations

import json

import httpx
import pytest

from app.assistant import AssistantError, AssistantSettings, OpenRouterClient, TextDelta, TurnEnd

KEY = "sk-or-SECRET-KEY"


def sse(*chunks, raw=()):
    lines = [": OPENROUTER PROCESSING", ""]
    for c in chunks:
        lines += [f"data: {json.dumps(c)}", ""]
        lines += [": OPENROUTER PROCESSING", ""]
    lines += list(raw) + ["data: [DONE]", ""]
    return "\n".join(lines).encode()


def delta(**d):
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def make(handler):
    settings = AssistantSettings(api_key=KEY, model="vendor/model:free")
    return OpenRouterClient(settings, transport=httpx.MockTransport(handler))


async def collect(client, messages=None):
    out = []
    async for ev in client.stream_turn(
        system="sys", messages=messages or [{"role": "user", "content": "hi"}], tools=[], max_tokens=100
    ):
        out.append(ev)
    return out


def ok(content):
    return lambda request: httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})


async def test_text_deltas_request_shape_and_auth():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["referer"] = request.headers["HTTP-Referer"]
        return httpx.Response(200, content=sse(delta(role="assistant", content="Hel"), delta(content="lo"), finish("stop")))

    events = await collect(make(handler))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    end = events[-1]
    assert isinstance(end, TurnEnd) and end.finish_reason == "stop" and end.message["content"] == "Hello"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["body"]["stream"] is True and seen["body"]["model"] == "vendor/model:free"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["referer"]


async def test_tool_call_arguments_split_across_chunks():
    chunks = [
        delta(role="assistant", content=None),
        delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_prices", "arguments": ""}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '{"tick'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'ers": ["AA'}}]),
        delta(tool_calls=[{"index": 1, "id": "call_2", "type": "function", "function": {"name": "get_portfolio", "arguments": "{}"}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'PL"]}'}}]),
        finish("tool_calls"),
    ]
    end = (await collect(make(ok(sse(*chunks)))))[-1]
    assert end.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.input) for c in end.tool_calls] == [
        ("call_1", "get_prices", {"tickers": ["AAPL"]}),
        ("call_2", "get_portfolio", {}),
    ]
    assert end.message["tool_calls"][0]["function"]["arguments"] == '{"tickers": ["AAPL"]}'


async def test_invalid_tool_arguments_flagged():
    chunks = [
        delta(tool_calls=[{"index": 0, "id": "c", "function": {"name": "propose_trade", "arguments": '{"tick'}}]),
        finish("tool_calls"),
    ]
    end = (await collect(make(ok(sse(*chunks)))))[-1]
    assert end.tool_calls[0].parse_error and end.tool_calls[0].input == {}


async def test_keepalive_comments_and_garbage_ignored():
    body = sse(delta(content="A"), finish("stop"), raw=["data: not json", "event: ping"])
    events = await collect(make(ok(body)))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["A"]


async def test_mid_stream_error_chunk():
    err = {
        "id": "x",
        "error": {"code": "server_error", "message": f"Provider disconnected {KEY}"},
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
    }
    client = make(ok(sse(delta(content="Par"), err)))
    got = []
    with pytest.raises(AssistantError) as exc:
        async for ev in client.stream_turn(system="s", messages=[], tools=[], max_tokens=10):
            got.append(ev)
    assert [e.text for e in got] == ["Par"]
    assert "Provider disconnected" in str(exc.value) and KEY not in str(exc.value)


@pytest.mark.parametrize(
    "status,needle",
    [(401, "API key"), (402, "credits"), (429, "rate limited"), (404, "tool calling"), (503, "unavailable")],
)
async def test_http_errors_are_clean(status, needle):
    body = {"error": {"code": status, "message": f"upstream said {KEY}"}}
    client = make(lambda r: httpx.Response(status, json=body))
    with pytest.raises(AssistantError) as exc:
        await collect(client)
    assert needle in str(exc.value) and KEY not in str(exc.value)


async def test_400_includes_scrubbed_detail():
    client = make(lambda r: httpx.Response(400, json={"error": {"message": f"bad {KEY} thing"}}))
    with pytest.raises(AssistantError) as exc:
        await collect(client)
    assert "bad" in str(exc.value) and KEY not in str(exc.value)


async def test_200_with_json_error_body():
    client = make(lambda r: httpx.Response(200, json={"error": {"message": "No endpoints found"}}))
    with pytest.raises(AssistantError, match="No endpoints found"):
        await collect(client)


async def test_connection_failure():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(AssistantError, match="Could not reach"):
        await collect(make(handler))


def test_requires_key():
    with pytest.raises(AssistantError):
        OpenRouterClient(AssistantSettings(api_key=None))


async def test_http_error_becomes_error_event_through_api(db, market, monkeypatch):
    """End to end: a 401 from OpenRouter reaches the browser as error + done, key not leaked."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = make(lambda r: httpx.Response(401, json={"error": {"message": KEY}}))
    with TestClient(create_app(market=market, db=db, assistant=client)) as c:
        r = c.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    events = [json.loads(b[6:]) for b in r.text.split("\n\n") if b.strip()]
    assert [e["type"] for e in events] == ["error", "done"]
    assert KEY not in r.text and "API key" in events[0]["detail"]
