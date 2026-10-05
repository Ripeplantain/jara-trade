from __future__ import annotations

import pathlib

import pytest

from app.assistant import ToolCall
from app.assistant.config import DEFAULT_MODEL, AssistantSettings

from .conftest import FakeLLM, chat, parse_sse


def test_status_without_key(make_client):
    r = make_client().get("/api/assistant/status")
    assert r.status_code == 200 and r.json() == {"available": False, "model": None}


def test_status_blank_key_is_unavailable(make_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    assert make_client().get("/api/assistant/status").json()["available"] is False


def test_status_with_key_builds_real_client(make_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("ASSISTANT_MODEL", "vendor/some-model")
    assert make_client().get("/api/assistant/status").json() == {
        "available": True,
        "model": "vendor/some-model",
    }


def test_status_with_fake(make_client):
    assert make_client(FakeLLM([])).get("/api/assistant/status").json() == {
        "available": True,
        "model": "fake/model",
    }


def test_settings_defaults_and_key_not_in_repr(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.delenv("ASSISTANT_MODEL", raising=False)
    s = AssistantSettings.from_env()
    assert s.model == DEFAULT_MODEL and "sk-or-secret" not in repr(s)


def test_chat_503_without_key(make_client):
    r = chat(make_client())
    assert r.status_code == 503
    assert r.json() == {
        "detail": "AI assistant is not configured. Set OPENROUTER_API_KEY and restart the backend."
    }


def test_streamed_text(make_client):
    llm = FakeLLM([["Hel", "lo ", "there"]])
    r = chat(make_client(llm))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    assert parse_sse(r) == [
        {"type": "text", "delta": "Hel"},
        {"type": "text", "delta": "lo "},
        {"type": "text", "delta": "there"},
        {"type": "done"},
    ]
    assert "SIMULATED" in llm.requests[0]["system"]


def test_read_tool_round_trip(make_client):
    llm = FakeLLM(
        [
            [ToolCall("c1", "get_prices", {"tickers": ["aapl", "zzzz"]})],
            ["AAPL is 190."],
        ]
    )
    events = parse_sse(chat(make_client(llm), "price of AAPL?"))
    assert events == [
        {"type": "tool", "name": "get_prices"},
        {"type": "text", "delta": "AAPL is 190."},
        {"type": "done"},
    ]
    tool_msg = llm.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert '"AAPL"' in tool_msg["content"] and "190.0" in tool_msg["content"]
    assert '"unavailable":["ZZZZ"]' in tool_msg["content"]


@pytest.mark.parametrize(
    "tool,args,needle",
    [
        ("get_watchlist", {}, "watchlist"),
        ("get_portfolio", {}, "total_value"),
        ("get_recent_trades", {"limit": 3}, "trades"),
        ("get_price_history", {"ticker": "AAPL"}, "change_percent"),
    ],
)
def test_other_read_tools(make_client, market, tool, args, needle):
    market.cache.update("AAPL", 191.0)  # a second history point
    llm = FakeLLM([[ToolCall("c", tool, args)], ["ok"]])
    events = parse_sse(chat(make_client(llm)))
    assert events[0] == {"type": "tool", "name": tool}
    assert needle in llm.requests[1]["messages"][-1]["content"]


def test_trade_proposal_event(make_client):
    args = {"ticker": " aapl", "side": "BUY", "quantity": 5, "rationale": "Diversify"}
    llm = FakeLLM([[ToolCall("p1", "propose_trade", args)], ["Please confirm."]])
    events = parse_sse(chat(make_client(llm), "buy 5 AAPL"))
    assert events[0] == {
        "type": "trade_proposal",
        "proposal": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 5.0,
            "rationale": "Diversify",
            "estimated_price": 190.0,
            "estimated_total": 950.0,
        },
    }
    assert [e["type"] for e in events] == ["trade_proposal", "text", "done"]  # no "tool" event
    assert "NOT been executed" in llm.requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "args",
    [
        {"ticker": "not a ticker!", "side": "buy", "quantity": 1},
        {"ticker": "ZZZZ", "side": "buy", "quantity": 1},  # valid symbol, no price
        {"ticker": "AAPL", "side": "buy", "quantity": 0},
        {"ticker": "AAPL", "side": "buy", "quantity": -3},
        {"ticker": "AAPL", "side": "buy", "quantity": "5"},
        {"ticker": "AAPL", "side": "short", "quantity": 1},
        {"ticker": "AAPL", "side": "buy", "quantity": 0.00001},
    ],
)
def test_invalid_proposal_is_tool_error(make_client, args):
    llm = FakeLLM([[ToolCall("p", "propose_trade", args)], ["Sorry."]])
    events = parse_sse(chat(make_client(llm)))
    assert [e["type"] for e in events] == ["text", "done"]
    assert '"error"' in llm.requests[1]["messages"][-1]["content"]


def test_invalid_tool_arguments_and_unknown_tool(make_client):
    llm = FakeLLM(
        [
            [
                ToolCall("a", "propose_trade", {}, parse_error="arguments were not valid JSON"),
                ToolCall("b", "delete_everything", {}),
            ],
            ["ok"],
        ]
    )
    events = parse_sse(chat(make_client(llm)))
    assert [e["type"] for e in events] == ["text", "done"]
    msgs = llm.requests[1]["messages"]
    assert [m["role"] for m in msgs[-2:]] == ["tool", "tool"]
    assert "Invalid arguments" in msgs[-2]["content"] and "Unknown tool" in msgs[-1]["content"]


def test_llm_error_mid_stream(make_client):
    from app.assistant import AssistantError

    llm = FakeLLM([["partial ", AssistantError("The AI service is rate limited.")]])
    events = parse_sse(chat(make_client(llm)))
    assert events == [
        {"type": "text", "delta": "partial "},
        {"type": "error", "detail": "The AI service is rate limited."},
        {"type": "done"},
    ]


def test_unexpected_exception_is_generic(make_client):
    llm = FakeLLM([[RuntimeError("secret sk-or-xyz traceback")]])
    events = parse_sse(chat(make_client(llm)))
    assert events[-2]["type"] == "error" and "secret" not in events[-2]["detail"]
    assert events[-1] == {"type": "done"}


def test_tool_loop_is_bounded(make_client, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MAX_ITERATIONS", "3")
    llm = FakeLLM([[ToolCall(f"c{i}", "get_portfolio", {})] for i in range(10)])
    events = parse_sse(chat(make_client(llm)))
    assert len(llm.requests) == 3
    assert [e["type"] for e in events] == ["tool", "tool", "tool", "error", "done"]


def test_empty_model_response_is_error(make_client):
    events = parse_sse(chat(make_client(FakeLLM([[]]))))
    assert [e["type"] for e in events] == ["error", "done"]


def test_assistant_never_changes_account(make_client):
    llm = FakeLLM(
        [
            [ToolCall("p", "propose_trade", {"ticker": "AAPL", "side": "buy", "quantity": 5, "rationale": "x"})],
            [ToolCall("q", "propose_trade", {"ticker": "AAPL", "side": "sell", "quantity": 1, "rationale": "x"})],
            ["done"],
        ]
    )
    c = make_client(llm)
    before = (c.get("/api/portfolio").json(), c.get("/api/trades").json())
    chat(c, "buy aapl")
    assert (c.get("/api/portfolio").json(), c.get("/api/trades").json()) == before
    assert before[0]["cash"] == 10000.0 and before[1]["trades"] == []


def test_package_never_references_execute_trade():
    for path in pathlib.Path(__file__).parents[2].joinpath("app/assistant").glob("*.py"):
        assert "execute_trade" not in path.read_text(), path


def test_history_is_passed_and_leading_assistant_dropped(make_client):
    llm = FakeLLM([["ok"]])
    body = {
        "messages": [
            {"role": "assistant", "content": "Hi, ask me anything"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
    }
    make_client(llm).post("/api/assistant/chat", json=body)
    assert [m["content"] for m in llm.requests[0]["messages"]] == ["q1", "a1", "q2"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": []},
        {"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]},
        {"messages": [{"role": "system", "content": "x"}]},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "user", "content": "x" * 8001}]},
        {"messages": [{"role": "user", "content": "x"}] * 41},
        {"messages": [{"role": "user", "content": "x" * 7000}] * 6},
        {"messages": [{"role": "user"}]},
    ],
)
def test_invalid_bodies_422(make_client, body):
    r = make_client(FakeLLM([["x"]])).post("/api/assistant/chat", json=body)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
