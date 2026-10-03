# Massive API reference (formerly Polygon.io)

How Jara Trade gets real stock prices: which Massive REST endpoints to call, what
each plan allows, what the responses look like, and working Python for each.

Researched 3 October 2026 against the live docs at `massive.com/docs` and the
source of the official Python client. Related documents:

- [market_interface.md](market_interface.md) — the unified Python API built on this.
- [market_simulator.md](market_simulator.md) — the fallback when no key is set.

## What to use, in short

| Need | Endpoint | Calls for N tickers | Lowest plan |
| --- | --- | --- | --- |
| Latest price, many tickers | `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=A,B,C` | 1 | Starter ($29/mo) |
| End-of-day price, many tickers | `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` | 1 | Basic (free) |
| End-of-day price, one ticker | `GET /v2/aggs/ticker/{ticker}/prev` | N | Basic (free) |

The finding that shapes the design: **the free Basic plan cannot call the
snapshot endpoint.** A free key gets end-of-day data only, at 5 calls per
minute. So the Massive client needs two modes: snapshot polling on a paid plan,
and a once-a-day grouped-bars fetch on the free plan. On the free plan prices do
not move during the session, so the simulator is the better demo experience.

## Background

Polygon.io rebranded as Massive on 30 October 2025. Existing API keys and
accounts carried over unchanged. The API base moved to `https://api.massive.com`;
`https://api.polygon.io` still works "for an extended period". Paths, parameters,
and response shapes are the same as the Polygon v2/v3 API.

- Docs: <https://massive.com/docs> (append `.md` to any docs page URL for plain
  Markdown; the index is <https://massive.com/docs/llms.txt>)
- Keys: <https://massive.com/dashboard/keys>
- Python client: <https://github.com/massive-com/client-python> (`pip install massive`)

## Authentication

Send the key either way. Prefer the header, so the key stays out of URLs and logs.

```bash
# Header (preferred)
curl -H "Authorization: Bearer $MASSIVE_API_KEY" \
  "https://api.massive.com/v2/aggs/ticker/AAPL/prev"

# Query parameter
curl "https://api.massive.com/v2/aggs/ticker/AAPL/prev?apiKey=$MASSIVE_API_KEY"
```

The key is read from `MASSIVE_API_KEY` on the server only. It must never reach
the frontend bundle, an SSE event, or a log line.

## Plans, recency, and rate limits

Individual stock plans, from the pricing page:

| Plan | Price | API calls | Data recency | Snapshots | Last trade | History |
| --- | --- | --- | --- | --- | --- | --- |
| Basic | $0 | 5 per minute | End of day | No | No | 2 years |
| Starter | $29/mo | Unlimited | 15-minute delayed | Yes | No | 5 years |
| Developer | $79/mo | Unlimited | 15-minute delayed | Yes | Yes | 10 years |
| Advanced | $199/mo | Unlimited | Real-time | Yes | Yes | All |

Consequences for polling:

- **Basic**: one grouped-daily call covers every ticker. Data changes once per
  trading day, so polling every 15 minutes is far more than enough.
- **Starter and Developer**: prices are 15 minutes behind the market. Polling
  the snapshot every 15 seconds is plenty; the latest minute bar only changes
  once a minute.
- **Advanced**: real-time. Polling every 2 to 5 seconds is reasonable over REST.
  Faster than that is what the WebSocket feed is for, which this project
  deliberately does not use.

"Unlimited" is not a licence to hammer the API; keep to one in-flight request.

## Errors

| Status | Meaning | Body | What the client should do |
| --- | --- | --- | --- |
| 401 | Missing or unknown key | `{"status":"ERROR","request_id":"...","error":"Unknown API Key"}` | Log, keep last prices, do not retry faster |
| 403 | Plan does not include this endpoint or timeframe | `{"status":"NOT_AUTHORIZED", ...}` | On the snapshot endpoint: switch to end-of-day mode |
| 429 | Rate limit exceeded | error JSON | Skip this cycle, retry at the next interval |
| 5xx | Server error | varies | Skip this cycle |

The 401 bodies above were observed directly on 3 October 2026 (no key gives
`"API Key was not provided"`). The 403 and 429 rows describe the long-standing
Polygon behaviour and were **not** re-verified, because that needs a real key.
Confirm the 403 body with a free key before relying on its exact shape; the
client only depends on the status code.

## Endpoint: snapshot for multiple tickers (the main one)

```
GET /v2/snapshot/locale/us/markets/stocks/tickers
```

| Parameter | Type | Notes |
| --- | --- | --- |
| `tickers` | string | Comma-separated, case-sensitive: `AAPL,TSLA,GOOG`. Omit for the whole market (10,000+ tickers). |
| `include_otc` | boolean | Default `false`. |

Plans: Starter and above. Recency: 15-minute delayed (Starter, Developer) or
real-time (Advanced).

```json
{
  "count": 1,
  "status": "OK",
  "tickers": [
    {
      "ticker": "AAPL",
      "day":      { "o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868, "vw": 119.725 },
      "min":      { "o": 120.435, "h": 120.468, "l": 120.37, "c": 120.4201, "v": 270796,
                    "av": 28724441, "n": 762, "t": 1684428720000, "vw": 120.4129 },
      "prevDay":  { "o": 117.19, "h": 119.63, "l": 116.44, "c": 119.49, "v": 110597265, "vw": 118.4998 },
      "lastTrade": { "p": 120.47, "s": 236, "t": 1605195918306274000, "x": 10, "i": "4046", "c": [14, 41] },
      "lastQuote": { "p": 120.46, "s": 8, "P": 120.47, "S": 4, "t": 1605195918507251700 },
      "todaysChange": 0.98,
      "todaysChangePerc": 0.82,
      "updated": 1605195918306274000
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `lastTrade.p` | Latest trade price. **Only present if the plan includes trades** (Developer and above). |
| `lastQuote.p` / `.P` | Bid / ask. Only present if the plan includes quotes. |
| `min.c` | Close of the most recent minute bar. Present on every snapshot plan. |
| `day.c` | Today's close so far. |
| `prevDay.c` | Previous session's close; the base for daily change. |
| `todaysChange`, `todaysChangePerc` | Change since the previous close. |
| `updated`, `lastTrade.t`, `lastQuote.t` | **Nanosecond** Unix timestamps. |
| `min.t` | **Millisecond** Unix timestamp. |

Things that will bite:

- **Pick the price with a fallback chain.** A Starter key gets no `lastTrade`.
  Use `lastTrade.p`, else `min.c`, else `day.c`, else `prevDay.c`.
- **Snapshots reset daily.** Data is cleared at 3:30 AM Eastern and refills as
  exchanges report, from about 4:00 AM. In that window, and before the first
  trade of the day, `day` and `min` hold zeros. Treat `0` as "no value" and fall
  through to `prevDay.c`.
- **Mixed timestamp units.** Nanoseconds on trades and `updated`, milliseconds
  on bars.
- **Unknown tickers are silently omitted.** Asking for `AAPL,NOTREAL` returns
  only AAPL, with status 200. The watchlist must tolerate a ticker with no price.
- **Tickers are case-sensitive.** Upper-case them before sending.

## Endpoint: end-of-day bars for the whole market (the free-tier one)

```
GET /v2/aggs/grouped/locale/us/market/stocks/{date}
```

| Parameter | Type | Notes |
| --- | --- | --- |
| `date` | path, `YYYY-MM-DD` | The trading day. |
| `adjusted` | boolean | Default `true` (split-adjusted). |
| `include_otc` | boolean | Default `false`. |

Plans: all, including Basic. One call returns every US stock, so it is the
only way to price a whole watchlist inside the free tier's 5 calls per minute.

```json
{
  "adjusted": true,
  "queryCount": 3,
  "resultsCount": 3,
  "status": "OK",
  "results": [
    { "T": "VSAT", "o": 34.9, "h": 35.47, "l": 34.21, "c": 34.24, "v": 312583, "vw": 34.4736, "n": 4966, "t": 1602705600000 }
  ]
}
```

`T` is the ticker, `c` the close, `t` a millisecond timestamp.

- There is no ticker filter. The response is the whole market (several
  megabytes); filter it in Python.
- Weekends and market holidays return `resultsCount: 0` with no `results` key.
  Skip weekends locally and step back a day on an empty result.
- On Basic, do not ask for today's date: the plan is end-of-day, so start from
  the previous weekday.

## Endpoint: previous day bar for one ticker

```
GET /v2/aggs/ticker/{ticker}/prev
```

Plans: all. No date arithmetic needed, since the API works out the previous
trading day. One call per ticker, so ten tickers take two minutes on Basic.
Useful for pricing a single newly added ticker.

```json
{
  "ticker": "AAPL",
  "adjusted": true,
  "queryCount": 1,
  "resultsCount": 1,
  "status": "OK",
  "results": [
    { "T": "AAPL", "o": 115.55, "h": 117.59, "l": 114.13, "c": 115.97, "v": 131704427, "vw": 116.3058, "t": 1605042000000 }
  ]
}
```

## Other endpoints worth knowing

| Endpoint | Returns | Plans | Use here |
| --- | --- | --- | --- |
| `GET /v1/open-close/{ticker}/{date}` | `open`, `close`, `high`, `low`, `preMarket`, `afterHours` for one day | All | Not needed; grouped daily covers it |
| `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}` | Historical OHLC bars; `limit` up to 50,000; paginates via `next_url` | All | Future: back-fill the price chart |
| `GET /v2/last/trade/{ticker}` | Latest trade, `results.p` | Developer and above | Not needed; snapshot includes it |
| `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` | One ticker's snapshot under key `ticker` | Starter and above | Not needed; use the multi-ticker form |
| `GET /v3/snapshot?ticker.any_of=A,B` | Unified snapshot across asset classes, max 250 tickers, different field names (`session.close`, `last_trade.price`) | Starter and above | Not needed for stocks only |
| `GET /v1/marketstatus/now` | `market`: `open`, `closed`, or `extended-hours`; `serverTime` | All | Optional: slow polling when closed |

## Code: direct HTTP with httpx (recommended)

This is what the project uses. It is async-native, so it fits the FastAPI event
loop without threads, and it is easy to fake in tests with `httpx.MockTransport`.

```python
import asyncio
import os

import httpx

BASE_URL = "https://api.massive.com"


def best_price(snap: dict) -> float | None:
    """lastTrade is missing on plans without trades; bars are zero before the open."""
    for obj, key in (("lastTrade", "p"), ("min", "c"), ("day", "c"), ("prevDay", "c")):
        value = (snap.get(obj) or {}).get(key)
        if value:
            return value
    return None


async def latest_prices(client: httpx.AsyncClient, tickers: list[str]) -> dict[str, float]:
    """Latest price per ticker in one call. Needs Starter or above."""
    resp = await client.get(
        "/v2/snapshot/locale/us/markets/stocks/tickers",
        params={"tickers": ",".join(t.upper() for t in tickers)},
    )
    resp.raise_for_status()
    prices = {s["ticker"]: best_price(s) for s in resp.json().get("tickers", [])}
    return {t: p for t, p in prices.items() if p}


async def end_of_day_prices(
    client: httpx.AsyncClient, tickers: list[str], day: str
) -> dict[str, float]:
    """Closing price per ticker for one trading day (YYYY-MM-DD). Works on the free plan."""
    resp = await client.get(f"/v2/aggs/grouped/locale/us/market/stocks/{day}")
    resp.raise_for_status()
    wanted = {t.upper() for t in tickers}
    bars = resp.json().get("results") or []  # key is absent on holidays
    return {bar["T"]: bar["c"] for bar in bars if bar["T"] in wanted}


async def previous_close(client: httpx.AsyncClient, ticker: str) -> float | None:
    """Previous trading day's close for one ticker. Works on the free plan."""
    resp = await client.get(f"/v2/aggs/ticker/{ticker.upper()}/prev")
    resp.raise_for_status()
    results = resp.json().get("results") or []
    return results[0]["c"] if results else None


async def main() -> None:
    headers = {"Authorization": f"Bearer {os.environ['MASSIVE_API_KEY']}"}
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=10.0) as client:
        watchlist = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
        try:
            print(await latest_prices(client, watchlist))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            # Free plan: no snapshots. Use end-of-day bars instead.
            print(await end_of_day_prices(client, watchlist, "2026-10-02"))
            print(await previous_close(client, "AAPL"))


if __name__ == "__main__":
    asyncio.run(main())
```

## Code: the official Python client

`pip install massive` (Python 3.9+, version 2.8.0 at the time of writing). The
client reads `MASSIVE_API_KEY` from the environment by default and returns typed
objects with readable names (`snapshot.last_trade.price` instead of `lastTrade.p`).

```python
from massive import RESTClient

client = RESTClient()  # or RESTClient(api_key="...")

# Latest price for several tickers (Starter and above)
for snap in client.get_snapshot_all("stocks", tickers=["AAPL", "GOOGL", "MSFT"]):
    price = snap.last_trade.price if snap.last_trade else snap.min.close
    print(snap.ticker, price, snap.prev_day.close, snap.todays_change_percent)

# End-of-day bars for the whole market on one date (all plans)
wanted = {"AAPL", "GOOGL", "MSFT"}
for bar in client.get_grouped_daily_aggs("2026-10-02"):
    if bar.ticker in wanted:
        print(bar.ticker, bar.close)

# Previous day's bar for one ticker (all plans)
prev = client.get_previous_close_agg("AAPL")
print(prev[0].close)

# Open and close for a specific date (all plans)
day = client.get_daily_open_close_agg("AAPL", "2026-10-02")
print(day.open, day.close, day.after_hours)

# Latest trade for one ticker (Developer and above)
print(client.get_last_trade("AAPL").price)
```

Why the project does not use it for polling:

- **It is synchronous** (built on `urllib3`). Inside FastAPI every call would
  need `await asyncio.to_thread(client.get_snapshot_all, ...)`.
- It retries 429 and 5xx responses three times by itself, which can burn the
  free tier's five calls on one failure.
- The project needs two endpoints. Forty lines of `httpx` cover them.

It is a fine choice for scripts and notebooks. If it is adopted later, pin the
version in `pyproject.toml`.

A note on the package name: a malware advisory (`MAL-2026-4795`) was filed
against the `massive` PyPI package on 26 May 2026, calling it a lookalike of the
Polygon client. It was withdrawn the same day. The package is the genuine one:
it is published by massive.com and linked from the official repository.

## Sources

- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot)
- [Single Ticker Snapshot](https://massive.com/docs/rest/stocks/snapshots/single-ticker-snapshot)
- [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot)
- [Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
- [Daily Ticker Summary](https://massive.com/docs/rest/stocks/aggregates/daily-ticker-summary)
- [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
- [Last Trade](https://massive.com/docs/rest/stocks/trades-quotes/last-trade)
- [Market Status](https://massive.com/docs/rest/stocks/market-operations/market-status)
- [Pricing](https://massive.com/pricing)
- [Python client source](https://github.com/massive-com/client-python)
- [massive on PyPI](https://pypi.org/project/massive/)
- [OSV MAL-2026-4795 (withdrawn)](https://osv.dev/vulnerability/MAL-2026-4795)
