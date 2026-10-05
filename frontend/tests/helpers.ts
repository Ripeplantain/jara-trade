/** Shared builders and state reset for composable and component tests. */
import { vi } from 'vitest'
import type { Api } from '~/lib/api'
import type { Portfolio, Position, PriceUpdate, Trade } from '~/types/api'

export function quote(ticker: string, price: number, extra: Partial<PriceUpdate> = {}): PriceUpdate {
  return {
    ticker,
    price,
    previous_price: price,
    timestamp: 1_000,
    direction: 'flat',
    change: 0,
    prev_close: null,
    day_change: null,
    day_change_percent: null,
    ...extra,
  }
}

export function position(ticker: string, quantity: number, avgCost: number, price = avgCost): Position {
  const cost = Math.round(quantity * avgCost * 100) / 100
  const value = Math.round(quantity * price * 100) / 100
  return {
    ticker,
    quantity,
    avg_cost: avgCost,
    price,
    market_value: value,
    cost_basis: cost,
    unrealized_pnl: Math.round((value - cost) * 100) / 100,
    unrealized_pnl_percent: cost ? Math.round(((value - cost) / cost) * 10000) / 100 : 0,
  }
}

export function portfolioOf(cash: number, positions: Position[] = []): Portfolio {
  const value = positions.reduce((sum, p) => sum + p.market_value, 0)
  const unrealized = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0)
  return {
    cash,
    positions_value: value,
    total_value: cash + value,
    starting_cash: 10_000,
    total_pnl: cash + value - 10_000,
    total_pnl_percent: ((cash + value - 10_000) / 10_000) * 100,
    realized_pnl: 0,
    unrealized_pnl: unrealized,
    positions,
  }
}

export function fill(id: number, ticker: string, side: 'buy' | 'sell', quantity: number, price: number): Trade {
  return {
    id,
    ticker,
    side,
    quantity,
    price,
    total: Math.round(quantity * price * 100) / 100,
    realized_pnl: side === 'sell' ? 0 : null,
    timestamp: 1_700_000_000 + id,
  }
}

export type FakeApi = { [K in keyof Api]: Api[K] extends (...args: infer A) => infer R ? ReturnType<typeof vi.fn<(...args: A) => R>> : Api[K] }

/** An Api whose every endpoint rejects until a test gives it an answer. */
export function createFakeApi(): FakeApi {
  const missing = (name: string) => vi.fn(async () => Promise.reject(new Error(`unexpected call: ${name}`)))
  return {
    streamUrl: '/api/stream/prices',
    prices: missing('prices'),
    history: missing('history'),
    status: missing('status'),
    watchlist: missing('watchlist'),
    addToWatchlist: missing('addToWatchlist'),
    removeFromWatchlist: missing('removeFromWatchlist'),
    portfolio: missing('portfolio'),
    trades: missing('trades'),
    placeTrade: missing('placeTrade'),
    resetPortfolio: missing('resetPortfolio'),
    assistantStatus: missing('assistantStatus'),
    assistantChat: missing('assistantChat'),
  } as unknown as FakeApi
}

/**
 * The composables keep their state in module-level refs (one copy per page),
 * so every test starts by putting them back to how a fresh page load finds them.
 */
export function resetAppState() {
  const prices = usePrices()
  prices.close()
  prices.prices.value = {}
  prices.histories.value = {}
  prices.hasPrices.value = false
  prices.connection.value = 'connecting'
  for (const key of Object.keys(prices.flashes)) delete prices.flashes[key]

  const portfolio = usePortfolio()
  portfolio.portfolio.value = null
  portfolio.trades.value = []
  portfolio.portfolioState.value = 'loading'
  portfolio.tradesState.value = 'loading'

  useAssistant().reset()

  const watchlist = useWatchlist()
  watchlist.editable.value = false
  watchlist.selected.value = null
}

/** A scriptable stand-in for the browser's EventSource. */
export class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  static get latest(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]!
  }

  readyState = FakeEventSource.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closeCalls = 0

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this)
  }

  open() {
    this.readyState = FakeEventSource.OPEN
    this.onopen?.()
  }

  send(data: unknown) {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) })
  }

  /** The browser is retrying by itself (still CONNECTING) or has given up (CLOSED). */
  fail(givenUp = false) {
    this.readyState = givenUp ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING
    this.onerror?.()
  }

  close() {
    this.closeCalls += 1
    this.readyState = FakeEventSource.CLOSED
  }
}

/** A chat reply body whose chunks the test pushes by hand (or all at once with `chatBody`). */
export function controlledStream() {
  const encoder = new TextEncoder()
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({ start: (c) => (controller = c) })
  return {
    body,
    raw: (bytes: Uint8Array) => controller.enqueue(bytes),
    push: (text: string) => controller.enqueue(encoder.encode(text)),
    event: (event: unknown) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`)),
    end: () => controller.close(),
    fail: (error: Error) => controller.error(error),
  }
}
