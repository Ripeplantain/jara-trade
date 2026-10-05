"""HTTP surface: GET /api/assistant/status and POST /api/assistant/chat (SSE)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.market import MarketDataService

from .client import LLMClient
from .service import build_system_prompt, run_chat
from .tools import AssistantTools

MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 8_000
MAX_TOTAL_CHARS = 40_000
NOT_CONFIGURED = "AI assistant is not configured. Set OPENROUTER_API_KEY and restart the backend."


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)

    @model_validator(mode="after")
    def _check(self) -> ChatRequest:
        if any(not m.content.strip() for m in self.messages):
            raise ValueError("messages must not be empty")
        if sum(len(m.content) for m in self.messages) > MAX_TOTAL_CHARS:
            raise ValueError(f"conversation is too long (max {MAX_TOTAL_CHARS} characters)")
        if self.messages[-1].role != "user":
            raise ValueError("the last message must be from the user")
        return self


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def create_assistant_router(
    client: LLMClient | None,
    tools: AssistantTools,
    market: MarketDataService,
    *,
    max_tokens: int,
    max_iterations: int,
) -> APIRouter:
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])

    @router.get("/status")
    async def status() -> dict:
        return {"available": client is not None, "model": client.model if client else None}

    @router.post("/chat", response_model=None)
    async def chat(request: ChatRequest):
        if client is None:
            return JSONResponse(status_code=503, content={"detail": NOT_CONFIGURED})
        history = [{"role": m.role, "content": m.content} for m in request.messages]
        while history and history[0]["role"] == "assistant":  # a UI greeting, not model context
            history.pop(0)

        async def events() -> AsyncIterator[str]:
            async for event in run_chat(
                client,
                tools,
                build_system_prompt(market),
                history,
                max_tokens=max_tokens,
                max_iterations=max_iterations,
            ):
                yield _sse(event)
            yield _sse({"type": "done"})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
