from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.assistant import TextDelta, ToolCall, TurnEnd
from app.db import Database
from app.main import create_app
from tests.api.conftest import FakeSource  # noqa: F401  (fixed-price market source)
from app.market import MarketDataService, PriceCache


class FakeLLM:
    """Scripted client: each turn is a list of str (text delta), ToolCall, or an Exception."""

    model = "fake/model"

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests: list[dict] = []

    async def stream_turn(self, *, system, messages, tools, max_tokens):
        self.requests.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        script = self.turns.pop(0)
        calls, text = [], ""
        for item in script:
            if isinstance(item, Exception):
                raise item
            if isinstance(item, ToolCall):
                calls.append(item)
            else:
                text += item
                yield TextDelta(item)
        message = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                for c in calls
            ]
        yield TurnEnd("tool_calls" if calls else "stop", message, calls)


def parse_sse(response) -> list[dict]:
    events = []
    for block in response.text.split("\n\n"):
        if block.strip():
            assert block.startswith("data: ") and "\n" not in block
            events.append(json.loads(block[6:]))
    return events


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


@pytest.fixture
def market():
    cache = PriceCache()
    return MarketDataService(cache, FakeSource(cache))


@pytest.fixture
def make_client(db, market, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    clients = []

    def factory(llm=None):
        c = TestClient(create_app(market=market, db=db, assistant=llm))
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def chat(client, text="hi", **kw):
    return client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": text}]}, **kw)
