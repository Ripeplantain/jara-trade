# Jara Trade frontend

The live trading dashboard: a single client-rendered page (Nuxt 4, Vue 3,
TypeScript, Tailwind CSS v4) that talks to the FastAPI backend in `../backend`.

## Run it

Two servers, two terminals. Node 22.19+ or 24.11+ and pnpm are required.

```sh
# 1. Backend on :8000 (simulated prices unless a Massive key is configured)
cd backend
.venv/bin/uvicorn app.main:app --port 8000

# 2. Frontend on :3000 (from the repo root)
pnpm install
pnpm dev
```

Open http://localhost:3000.

The browser only talks to `localhost:3000`. The Nuxt server proxies `/api/**`
to the backend (a `routeRules` proxy in `nuxt.config.ts`), and the SSE price
stream passes through it unbuffered. To check:

```sh
curl -N http://localhost:3000/api/stream/prices   # one event every 500 ms
```

| Variable | Default | Effect |
| --- | --- | --- |
| `NUXT_BACKEND_URL` | `http://localhost:8000` | Where the proxy sends `/api` (read when the dev server starts or the app is built). |
| `NUXT_PUBLIC_API_BASE` | empty | Set to `http://localhost:8000` to skip the proxy and call the backend directly (it allows CORS from `http://localhost:3000`). |

## Scripts

Run from the repo root (or from `frontend/`):

| Command | What it does |
| --- | --- |
| `pnpm dev` | Dev server on port 3000 |
| `pnpm build` | Production build into `frontend/.output` (start with `node frontend/.output/server/index.mjs`) |
| `pnpm test` | Vitest unit tests |
| `pnpm typecheck` | `nuxt typecheck` (vue-tsc) |

## Layout

```
app/
  app.vue                 page shell and layout grid
  assets/css/main.css     Tailwind entry, colour tokens (dark default, light override), row-flash animation
  types/api.ts            every backend shape, in one place
  lib/                    plain TypeScript, no Vue: unit-tested
    api.ts                typed API client; errors carry the backend's `detail`
    prices.ts             the one SSE client (EventSource + reconnect)
    flash.ts              which rows flash on a new message
    history.ts            appending streamed prices to a ticker's series
    pnl.ts                live portfolio maths, order estimate, max affordable
    chart.ts              SVG scales, paths and ticks
    format.ts             money, percent, quantity and time formatters
  composables/            shared reactive state built on lib/
    usePrices.ts          single stream: prices, connection state, flashes, per-ticker history
    usePortfolio.ts       portfolio + trades, re-marked live from the stream
    useWatchlist.ts       watchlist and the selected ticker
    useMarketStatus.ts    data-source badge, refreshed every minute
  components/             header, watchlist, chart, trade ticket, positions, trades
tests/                    Vitest tests for lib/
```

## How it behaves

- **One connection.** `usePrices()` opens a single `EventSource`; every panel
  reads from its shared state.
- **Flash.** A row flashes only when its price differs from the last price held
  for that ticker, not when a repeated event still says `direction: "up"`.
- **Chart and sparklines.** History is fetched once per ticker, then each
  streamed price is appended client-side at the same five-second spacing.
- **Live P&L.** Header totals and the positions table are recomputed from
  streamed prices on every message; the portfolio itself is only refetched
  after a trade or a reset.
- **Backend missing.** If the watchlist, portfolio or trades endpoints do not
  answer, those panels show an unavailable state and retry every 10 seconds.
  Without `/api/watchlist` the table lists every streamed ticker, read-only.
