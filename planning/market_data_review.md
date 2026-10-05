# Market data backend: code review

Review of `backend/app/market/` and its tests against the design in
[MARKET_DATA_DESIGN.md](MARKET_DATA_DESIGN.md), carried out on 4 October 2026.
No code was changed by this review.

## Verdict

The implementation is faithful to the design and the test suite passes. It is
ready for the frontend and the simulator path to be built on.

The Massive path needs one fix before it is relied on: the poller dies
permanently after a long run of failures (finding 1). Four more findings are
worth fixing before trades and a real key are in use (findings 2 to 5). The
rest are small.

| Severity | Count | Findings |
| --- | --- | --- |
| High | 1 | 1 |
| Medium | 4 | 2, 3, 4, 5 |
| Low | 10 | 6 to 15 |

## Test results

| Check | Result |
| --- | --- |
| `pytest` | 59 passed, 0 failed, about 1 s |
| `pytest -W error` (warnings as errors) | 59 passed |
| Repeated 9 times to look for flaky timing tests | 59 passed every time |
| Coverage (line and branch) | 94% overall |
| App run in-process through its lifespan, every REST route called | Behaved as designed |
| `ruff check` | 3 findings, all auto-fixable (see finding 15) |

Environment: Python 3.14.3, FastAPI 0.142.2, httpx 0.28.1, numpy 2.5.3,
pytest 9.1.1, pytest-asyncio 1.4.0. These match `uv.lock`. `uv` is not
installed on this machine, so the tests ran from a throwaway virtualenv built
with `pip` outside the repository, not through `uv run pytest`.

Coverage by module:

| Module | Coverage | Not covered |
| --- | --- | --- |
| `cache.py`, `models.py`, `config.py`, `factory.py`, `service.py`, `interface.py` | 100% | |
| `simulator.py` | 96% | Catch-up branch after a stall, duplicate `add_ticker` |
| `routes.py` | 94% | 422 on the history route |
| `massive_client.py` | 92% | 403 while already in end-of-day mode, call budget exhausted, `start()` twice, unexpected-exception handler |
| `stream.py` | 86% | The router itself; only the `price_events` generator is tested |
| `main.py` | 0% | No test imports the app or runs its lifespan |

Not verified:

- **Anything against the live Massive API.** No key was available. The design
  document carries the same caveat.
- **The app under a real uvicorn server.** The sandbox this review ran in
  blocks binding a local port. So SSE over real HTTP was not exercised, and
  neither was shutdown with a stream open. That second one is worth a manual
  check: start the app, open `curl -N localhost:8000/api/stream/prices`, press
  Ctrl-C on the server, and confirm it exits instead of waiting on the open
  connection.

## Conformance to the design

Every module in `app/market/` and `app/main.py` is line-for-line identical to
the code in MARKET_DATA_DESIGN.md, with one exception: the history route
returns `ticker.strip().upper()` where the design has `ticker.upper()`. That is
a small improvement.

Differences that are documentation drift, not defects:

- The design says 42 tests; there are 59. The 17 extra tests cover batching,
  the back-off schedule, date rollover, key-in-header, client close, keep-alive
  and failure recovery.
- The design's REST test uses `TestClient`; the implementation uses
  `httpx.ASGITransport`.
- `app/deps.py` (design section 14) does not exist yet. It belongs to the
  later trade and watchlist work.

The design rules hold in the code: one writer, readers use the cache only, the
source is chosen once, the key travels in a header and stays out of URLs, logs
and the status endpoint (tested).

## Findings

Each finding marked "confirmed" was reproduced with a script against the real
code, using a mocked HTTP transport.

### High

**1. The Massive poller dies after 1,024 consecutive failures.** Confirmed.
[massive_client.py:324](../backend/app/market/massive_client.py#L324),
[:328](../backend/app/market/massive_client.py#L328)

`_next_delay()` computes `poll_interval * 2**failures` before applying the cap.
`2**1024` is too large to convert to a float, so Python raises `OverflowError`.
`_next_delay()` is called from `_poll_loop()` outside any `try`, so the
exception ends the background task. Prices stop updating until the process is
restarted, even after the key or network is fixed. `status().running` turns
`false`, which is the only sign.

At the 300 s cap this takes about 3.5 days of continuous failure in snapshot
mode (a revoked key, or a laptop off the network over a long weekend), and
about 10.6 days in end-of-day mode. It breaks the design's rule that the
background task never dies.

Fix: cap the exponent, for example `2 ** min(self._failures, 10)`, and wrap
the body of `_poll_loop()` in a `try/except Exception` that logs and continues,
as the simulator's loop already does.

### Medium

**2. `MASSIVE_POLL_INTERVAL` is not validated.** Confirmed.
[config.py:28](../backend/app/market/config.py#L28)

- `0` or a negative number gives a poll loop with no delay, and no back-off
  either, because `0 * 2**n` is still `0`. Against a mock that always fails,
  the source made 1,024 requests in 0.3 s and then died through finding 1.
- A non-numeric value (`abc`), or a non-integer `SIMULATOR_SEED`, raises
  `ValueError` when `app.main` is imported, with no hint about which variable
  is wrong.

Fix: clamp the interval to a minimum (1 s is reasonable) and raise a clear
error that names the variable.

**3. Nothing loads `.env`.**
[README.md](../backend/README.md), [config.py:23](../backend/app/market/config.py#L23)

The design and README describe configuration through `.env`, and `.env` is in
`.gitignore`, but `config.py` reads only `os.environ` and the documented run
command (`uv run uvicorn app.main:app --reload`) does not pass an env file. A
key placed in `.env` is ignored and the simulator starts. Design section 17
calls out exactly this outcome, simulated prices shown to someone who
configured real ones, as the thing to avoid.

Fix: change the run command to `uv run --env-file .env uvicorn app.main:app`
(or uvicorn's own `--env-file`), and say in the README where the file lives.

**4. A stale `direction` is re-sent on every SSE event in Massive mode.**
Confirmed. [cache.py:45-52](../backend/app/market/cache.py#L45-L52),
[stream.py:49](../backend/app/market/stream.py#L49)

The cache skips an exact repeat (same price, timestamp and previous close), so
a ticker that has not traded keeps its old `PriceUpdate`, including
`direction: "up"` from its last move. Each SSE event carries every ticker, so
whenever any other ticker changes, the quiet one is sent again still marked
`up`. Design section 15 tells the frontend to flash on `direction`, so that row
would flash green every 15 s for as long as it stays quiet. In the test, a
ticker that moved once was reported `up` on four consecutive polls.

The simulator is not affected: it rewrites every ticker on every tick.

Fix, either one: have the frontend flash only when a ticker's `timestamp` or
`price` differs from the last event it saw, or add a per-ticker sequence number
to `PriceUpdate` so the client can tell a new update from a repeat.

**5. Simulated tickers outside the seed list get a new random price on every
restart and every re-add.** Confirmed.
[simulator.py:90](../backend/app/market/simulator.py#L90)

A ticker not in `SEED_PRICES` starts at a random price between $50 and $300.
Five runs gave PYPL $83.93, $276.30, $254.14, $144.33 and $94.97. Removing it
from the watchlist and adding it back moved it from $83.54 to $267.91.

The design's open question 2 accepts that prices reset to their seeds on
restart. This is worse than that: a held position in a non-seed ticker can
change value several times over across a restart. It does not matter today,
but it will as soon as trades exist.

Fix: derive the starting price from a stable hash of the symbol, so the same
ticker always starts at the same price. This is separate from persisting last
prices, which open question 2 already covers.

### Low

**6. The Massive source cannot be restarted, and the service is created at
import time.** Confirmed. [main.py:8](../backend/app/main.py#L8),
[massive_client.py:280](../backend/app/market/massive_client.py#L280)

`stop()` closes the HTTP client, so a second `start()` fails every poll with
"Cannot send a request, as the client has been closed". Because `main.py`
builds the service when the module is imported, any test that runs the app's
lifespan twice with a key set will hit this. With the simulator a second start
works, but resets every `prev_close` to the current price. Building the service
inside `lifespan` removes both problems.

**7. Malformed responses escape the client's error types.** Confirmed.
[massive_client.py:184-197](../backend/app/market/massive_client.py#L184-L197)

- A `200` with a body that is not JSON raises `JSONDecodeError`, not
  `MassiveAPIError`. The poll loop survives through its catch-all, but in
  `_refresh_eod()` the prior-close fetch only catches `MassiveAPIError`, so the
  new day's bars are left in memory alongside the previous day's closes and
  nothing is written to the cache until the next poll.
- An error response whose JSON body is not an object (a list, a string) raises
  `AttributeError` before the status code is mapped. A `403` with such a body
  never switches the source to end-of-day mode.

Fix: catch `ValueError` around `resp.json()` on the success path and raise
`MassiveAPIError`; check `isinstance(body, dict)` on the error path.

**8. `add_ticker` bypasses the back-off.** Confirmed.
[massive_client.py:288-289](../backend/app/market/massive_client.py#L288-L289)

In snapshot mode each `add_ticker` wakes the poller immediately, whatever the
back-off. With a rejected key (next delay 300 s), five watchlist adds produced
five extra requests. Harmless on paid plans; skip the wake when
`self._failures > 0` to keep to the design's intent.

**9. A price below half a cent is stored as `0.0`.** Confirmed.
[cache.py:37-39](../backend/app/market/cache.py#L37-L39)

The positive-price check runs before rounding, so `0.004` passes it and is
then rounded to `0.0`. A trade could fill at zero. Listed stocks do not trade
there, so this is unlikely to be reached; moving the check after the rounding
closes it.

**10. One malformed ticker stops the app from starting.** Confirmed.
[service.py:32](../backend/app/market/service.py#L32)

`MarketDataService.start()` normalizes every ticker and raises
`InvalidTickerError` on the first bad one. Once the list comes from the
database, a single bad row would prevent startup. Skipping and logging is
safer there.

**11. Some real symbols cannot be tracked.**
[models.py:8](../backend/app/market/models.py#L8),
[:25](../backend/app/market/models.py#L25)

`normalize_ticker` upper-cases everything and allows a suffix of at most two
characters. MASSIVE_API.md notes that Massive tickers are case-sensitive. As
far as I know, Massive writes preferred shares with a lower-case `p` (for
example `BACpB`), which this turns into `BACPB`; I did not check that against
the live API. Common stock, `BRK.B` and `BF-B` are fine. Worth a line in the
design's open questions.

**12. Small inconsistencies in the REST routes.** Confirmed.
[routes.py](../backend/app/market/routes.py)

- `?tickers=AAPL,` (trailing comma) returns 422; `?tickers=` returns every
  ticker.
- History for an untracked ticker returns 200 with no points, while the price
  route returns 404 for the same ticker.
- Routes return bare `dict`, so the generated OpenAPI schema has no response
  shapes for the frontend to use.

**13. A 403 in end-of-day mode is recorded but not logged.**
[massive_client.py:345](../backend/app/market/massive_client.py#L345)
Every other failure branch writes a log line.

**14. When prior closes cannot be fetched, the whole-market file is downloaded
again on every poll.**
[massive_client.py:420](../backend/app/market/massive_client.py#L420)

`_eod_target` is left as `None` so the next poll retries. That is intended,
but it is unbounded: both multi-megabyte files are fetched every 15 minutes
until it succeeds, and each of those polls is reported as a success in
`status()`.

**15. Lint and CI.**

- `ruff` reports an unused `import json` in
  [test_massive.py:2](../backend/tests/market/test_massive.py#L2) and unsorted
  imports in `service.py` and `test_simulator.py`. There is no `ruff`
  configuration in `pyproject.toml`.
- The only GitHub workflow is the Claude mention action. Nothing runs the tests
  on a pull request.

## Test suite

The tests are well aimed: no network, no key, deterministic seeds, and Massive
exercised through `httpx.MockTransport`. The simulator's statistical test
(volatility and correlations within 3% over a simulated day) is a real check of
the maths.

Gaps, in the order I would close them:

1. Nothing covers `main.py` or the stream router. One test that runs the app's
   lifespan and calls `/api/market/status` would cover the wiring.
2. No test for a long failure streak (finding 1) or bad environment values
   (finding 2).
3. No test for malformed response bodies (finding 7), the exhausted call
   budget, or a 403 arriving in end-of-day mode.
4. `test_403_switches_to_eod...` ends by adding `MSFT` to show that "a new
   ticker is served from memory", but `MSFT` is already tracked, so the call is
   a no-op and the assertion proves nothing. `test_eod_add_ticker_costs_no_call`
   covers the real case.
5. Several tests wait on real time (`asyncio.sleep(0.05)` to `0.1`). They were
   stable across nine runs here, but could fail on a slow CI runner.

## For the next stage

These are not defects in this code, but the trade and watchlist routes will
run into them:

- **Stale prices.** `require_price()` returns the cached price however old it
  is. With Massive down for an hour, a trade fills at an hour-old price. The
  `timestamp` is there; the trade route should decide on a maximum age.
- **CORS.** No CORS middleware is configured. A frontend dev server on another
  port will need a proxy to `/api`, or CORS enabled.
- **Tracked but never priced.** On Massive, a well-formed symbol that does not
  exist stays in the watchlist with no price and no error. The watchlist route
  needs a way to tell the user.

## Recommended order of work

1. Finding 1: cap the exponent and guard the poll loop. Add a test.
2. Finding 2: validate the interval. Add a test.
3. Finding 3: fix the run command and README so `.env` is read.
4. Finding 7: harden `_get()`.
5. Decide finding 4 with whoever builds the frontend.
6. Finding 5, before trades are built.
7. Add a CI workflow running `uv run pytest` and `ruff check`.
8. Run the manual checks listed under "Not verified": a free Massive key, and
   uvicorn shutdown with a stream open.
