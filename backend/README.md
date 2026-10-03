# Jara Trade backend

FastAPI backend. So far it contains the market data subsystem: live prices from
the Massive REST API when `MASSIVE_API_KEY` is set, or from a built-in
simulator otherwise. Design: [planning/MARKET_DATA_DESIGN.md](../planning/MARKET_DATA_DESIGN.md).

## Run

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

- `GET /api/stream/prices`: Server-Sent Events, all tracked prices every time they change
- `GET /api/market/prices`, `/api/market/prices/{ticker}`: latest prices
- `GET /api/market/history/{ticker}`: last hour, one point per 5 s
- `GET /api/market/status`: which source is running and whether it is healthy

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `MASSIVE_API_KEY` | unset | Set and non-blank: use Massive (plan detected automatically). Otherwise: simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between Massive snapshot polls |
| `SIMULATOR_SEED` | unset | Fixed seed for a reproducible simulated price path |

## Test

```bash
uv run pytest
```

No network or API key needed: Massive is tested through `httpx.MockTransport`.
