# Jara Trade backend

FastAPI backend for a simulated (paper) trading app, single user, no auth.

- **Market data** (`app/market/`): live prices from the Massive REST API when
  `MASSIVE_API_KEY` is set, or from a built-in simulator otherwise.
- **Persistence** (`app/db.py`): one SQLite file via the stdlib `sqlite3`. The
  schema is created on first start and seeded with $10,000 cash and the default
  watchlist.
- **Watchlist** (`app/watchlist/`) and **portfolio / trading** (`app/portfolio/`).
  The market service always tracks watchlist ∪ held positions (`app/tracking.py`).

Market data design: [planning/MARKET_DATA_DESIGN.md](../planning/MARKET_DATA_DESIGN.md).

## Run

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

Without `uv`:

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi 'uvicorn[standard]' httpx numpy pytest pytest-asyncio
.venv/bin/uvicorn app.main:app --port 8000
```

CORS allows `http://localhost:3000` (the frontend dev server).

## API

### Market data

- `GET /api/stream/prices`: Server-Sent Events, all tracked prices every time they change
- `GET /api/market/prices`, `/api/market/prices/{ticker}`: latest prices
- `GET /api/market/history/{ticker}`: last hour, one point per 5 s
- `GET /api/market/status`: which source is running and whether it is healthy

### Watchlist

- `GET /api/watchlist`: `{"tickers": [...]}` in the order they were added
- `POST /api/watchlist` with `{"ticker": "pypl"}`: add and start tracking; adding an existing ticker is a no-op (200)
- `DELETE /api/watchlist/{ticker}`: remove (404 if absent); a ticker that is still held keeps its price feed

### AI assistant

Chat assistant backed by [OpenRouter](https://openrouter.ai) (OpenAI-compatible
chat completions over `httpx`, no extra dependency). It reads prices, the
watchlist, the portfolio, P&L and trade history through tools, and can only
*propose* trades: the user confirms in the UI, which calls `POST /api/trades`.
The assistant package never executes a trade. The key stays server-side.

Environment (read once, in `app/assistant/config.py`):

- `OPENROUTER_API_KEY`: required; unset or blank disables the assistant
- `ASSISTANT_MODEL`: OpenRouter model slug that supports tool calling (default `openai/gpt-oss-120b:free`)
- `OPENROUTER_BASE_URL`: default `https://openrouter.ai/api/v1`
- `ASSISTANT_MAX_TOKENS` (default 2048), `ASSISTANT_MAX_ITERATIONS` (model calls per request, default 6)
- `OPENROUTER_SITE_URL`: optional `HTTP-Referer` attribution header (default `http://localhost:3000`)

Endpoints:

- `GET /api/assistant/status`: `{"available": bool, "model": str | null}`
- `POST /api/assistant/chat` with `{"messages": [{"role": "user" | "assistant", "content": "..."}]}`
  (stateless; last message must be from the user; max 40 messages, 8000 characters each, 40000 total, else 422).
  Returns 503 without a key, otherwise a `text/event-stream` of `data: <json>` events:
  `{"type":"text","delta"}`, `{"type":"tool","name"}`, `{"type":"trade_proposal","proposal":{ticker,side,quantity,rationale,estimated_price,estimated_total}}`,
  `{"type":"error","detail"}`, and always a final `{"type":"done"}`.

### Portfolio and trades

- `GET /api/portfolio`: cash, positions valued at the latest cached price (at average cost if no price yet), realized and unrealized P&L
- `POST /api/trades` with `{"ticker": "AAPL", "side": "buy" | "sell", "quantity": 5}`: fills immediately at the cached price, returns `{"trade", "portfolio"}`
- `GET /api/trades?limit=50`: trade history, newest first (`limit` 1 to 500)
- `POST /api/portfolio/reset`: delete trades and positions, cash back to $10,000; the watchlist is kept

Money is USD rounded to cents. Quantity may be fractional and is rounded to 4
decimal places. Buys use average-cost accounting; sells realize P&L against the
average cost. Cash, position and trade row change in one SQLite transaction.

Errors are always `{"detail": "<message>"}`:

| Status | When |
| --- | --- |
| 422 | Invalid ticker, side or quantity; malformed body or query |
| 400 | `Insufficient cash: need $X, have $Y` or `Insufficient shares: have N AAPL` |
| 404 | `DELETE /api/watchlist/{ticker}` for a ticker not on the watchlist |
| 409 | Trade for a ticker with no cached price yet (not tracked, or the source has not priced it) |

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `JARA_DB_PATH` | `backend/data/jara.db` | SQLite file. Created (with its directory) and seeded on first start; delete it to start over |
| `MASSIVE_API_KEY` | unset | Set and non-blank: use Massive (plan detected automatically). Otherwise: simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between Massive snapshot polls |
| `SIMULATOR_SEED` | unset | Fixed seed for a reproducible simulated price path |

## Test

```bash
uv run pytest            # or: .venv/bin/python -m pytest
```

No network or API key needed: Massive is tested through `httpx.MockTransport`.
The API tests (`tests/api/`) use a temp database and a fixed-price fake source.
