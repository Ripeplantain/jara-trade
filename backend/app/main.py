from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import MarketDataService, create_market_router, create_stream_router
from app.market.seed_prices import DEFAULT_WATCHLIST

market = MarketDataService.from_settings()


def load_tracked_tickers() -> list[str]:
    """Placeholder: SELECT ticker FROM watchlist UNION SELECT ticker FROM positions."""
    return DEFAULT_WATCHLIST


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.market = market
    await market.start(load_tracked_tickers())
    try:
        yield
    finally:
        await market.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(market.cache))
app.include_router(create_market_router(market))
