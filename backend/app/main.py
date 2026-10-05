from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.assistant import AssistantSettings, AssistantTools, create_assistant_router
from app.assistant.client import LLMClient, create_client
from app.db import Database
from app.market import MarketDataService, create_market_router, create_stream_router
from app.market.seed_prices import DEFAULT_WATCHLIST
from app.portfolio import PortfolioService, create_portfolio_router
from app.tracking import TrackingSync
from app.watchlist import WatchlistStore, create_watchlist_router

CORS_ORIGINS = ["http://localhost:3000"]


def _validation_message(exc: RequestValidationError) -> str:
    """Flatten FastAPI's error list into one sentence, e.g. "quantity: Field required"."""
    parts = []
    for error in exc.errors():
        field = ".".join(str(p) for p in error.get("loc", ()) if p not in ("body", "query", "path"))
        message = error.get("msg", "Invalid value")
        parts.append(f"{field}: {message}" if field else message)
    return "; ".join(parts) or "Invalid request"


def create_app(
    market: MarketDataService | None = None,
    db: Database | None = None,
    assistant: LLMClient | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own market service, a temp database and a fake LLM client."""
    market = market or MarketDataService.from_settings()
    db = db or Database()  # path from JARA_DB_PATH; no disk access until startup
    tracking = TrackingSync(db, market)

    def load_tracked_tickers() -> list[str]:
        """Watchlist plus held positions."""
        return db.tracked_tickers()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init(seed_watchlist=DEFAULT_WATCHLIST)
        app.state.market = market
        app.state.db = db
        await market.start(load_tracked_tickers())
        try:
            yield
        finally:
            await market.stop()

    app = FastAPI(title="Jara Trade", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Every error body is {"detail": "<message>"}, including malformed requests.
        return JSONResponse(status_code=422, content={"detail": _validation_message(exc)})

    app.include_router(create_stream_router(market.cache))
    app.include_router(create_market_router(market))
    app.include_router(create_watchlist_router(WatchlistStore(db), tracking))
    portfolio = PortfolioService(db, market)
    app.include_router(create_portfolio_router(portfolio, tracking))

    # The assistant gets read access only (prices, watchlist, portfolio, trades); it can
    # propose trades but never execute them. Without a client and a key it reports unavailable.
    settings = AssistantSettings.from_env()
    assistant = assistant or create_client(settings)
    app.include_router(
        create_assistant_router(
            assistant,
            AssistantTools(market, portfolio, WatchlistStore(db)),
            market,
            max_tokens=settings.max_tokens,
            max_iterations=settings.max_iterations,
        )
    )
    return app


app = create_app()
