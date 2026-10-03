"""Server-Sent Events: GET /api/stream/prices."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache


def create_stream_router(price_cache: PriceCache, interval: float = 0.5) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            price_events(price_cache, request.is_disconnected, interval=interval),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


async def price_events(
    price_cache: PriceCache,
    is_disconnected: Callable[[], Awaitable[bool]],
    interval: float = 0.5,
    heartbeat: float = 15.0,
) -> AsyncIterator[str]:
    """Yield one `data:` event per cache version change, checked every `interval` s.

    Every event carries the full set of tracked tickers, so a client that
    reconnects is up to date after one message. A comment line goes out every
    `heartbeat` seconds of silence so proxies do not close an idle stream
    (Massive end-of-day mode can be quiet for hours).
    """
    yield "retry: 1000\n\n"  # browser reconnects after 1 s if the stream drops
    loop = asyncio.get_running_loop()
    last_version = -1
    last_sent = loop.time()
    while not await is_disconnected():
        version = price_cache.version
        if version != last_version:
            last_version = version
            payload = {t: u.to_dict() for t, u in price_cache.get_all().items()}
            yield f"id: {version}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            last_sent = loop.time()
        elif loop.time() - last_sent >= heartbeat:
            yield ": keep-alive\n\n"
            last_sent = loop.time()
        await asyncio.sleep(interval)
