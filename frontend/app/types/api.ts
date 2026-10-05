/** Every shape the backend sends or accepts. Keep in sync with the FastAPI contract. */

export type Direction = 'up' | 'down' | 'flat'

export interface PriceUpdate {
  ticker: string
  price: number
  previous_price: number
  /** Unix seconds of the underlying market data. */
  timestamp: number
  direction: Direction
  change: number
  prev_close: number | null
  day_change: number | null
  day_change_percent: number | null
}

/** One SSE message: the full set of tracked tickers. */
export type PriceMap = Record<string, PriceUpdate>

export interface PricePoint {
  timestamp: number
  price: number
}

export interface HistoryResponse {
  ticker: string
  points: PricePoint[]
}

export type MarketMode = 'simulated' | 'snapshot' | 'eod'

export interface MarketStatus {
  source: string
  mode: MarketMode
  running: boolean
  tickers: number
  last_success: number | null
  last_error: string | null
  consecutive_failures: number
}

export interface WatchlistResponse {
  tickers: string[]
}

export interface Position {
  ticker: string
  quantity: number
  avg_cost: number
  price: number
  market_value: number
  cost_basis: number
  unrealized_pnl: number
  unrealized_pnl_percent: number
}

export interface Portfolio {
  cash: number
  positions_value: number
  total_value: number
  starting_cash: number
  total_pnl: number
  total_pnl_percent: number
  realized_pnl: number
  unrealized_pnl: number
  positions: Position[]
}

export type TradeSide = 'buy' | 'sell'

export interface Trade {
  id: number | string
  ticker: string
  side: TradeSide
  quantity: number
  price: number
  total: number
  realized_pnl: number | null
  /** Unix seconds. */
  timestamp: number
}

export interface TradeRequest {
  ticker: string
  side: TradeSide
  quantity: number
}

export interface TradeResponse {
  trade: Trade
  portfolio: Portfolio
}

export interface TradesResponse {
  trades: Trade[]
}

/** `GET /api/assistant/status`. */
export interface AssistantStatus {
  available: boolean
  model: string | null
}

/** One text turn sent back to the stateless chat endpoint. */
export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

/** A trade the assistant suggests. Nothing executes until the user confirms it. */
export interface TradeProposal {
  ticker: string
  side: TradeSide
  quantity: number
  rationale: string
  estimated_price: number | null
  estimated_total: number | null
}

/** One event of the `POST /api/assistant/chat` stream. `done` is always last. */
export type ChatStreamEvent =
  | { type: 'text'; delta: string }
  | { type: 'tool'; name: string }
  | { type: 'trade_proposal'; proposal: TradeProposal }
  | { type: 'error'; detail: string }
  | { type: 'done' }
