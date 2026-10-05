/** Decide which watchlist rows should flash when a new SSE message arrives. */
import type { PriceMap } from '~/types/api'

export type FlashDirection = 'up' | 'down'

/** The minimum we remember about a ticker between messages. */
export interface SeenPrice {
  price: number
  timestamp: number
}

export type SeenPrices = Record<string, SeenPrice>

/**
 * Compare a message with the prices we already hold.
 *
 * A ticker flashes only when its price differs from the one we last saw; the
 * colour follows that comparison, not the event's `direction` field, which
 * stays "up" or "down" on every repeat of an unchanged quote. A ticker seen for
 * the first time never flashes, and neither does a newer timestamp at the same price.
 */
export function detectFlashes(seen: SeenPrices, next: PriceMap): Record<string, FlashDirection> {
  const flashes: Record<string, FlashDirection> = {}
  for (const [ticker, update] of Object.entries(next)) {
    const before = seen[ticker]
    if (!before || update.price === before.price) continue
    // An out-of-order (older) quote is not a fresh move.
    if (update.timestamp < before.timestamp) continue
    flashes[ticker] = update.price > before.price ? 'up' : 'down'
  }
  return flashes
}

/** What to remember after handling a message (tickers that left the stream are dropped). */
export function rememberPrices(next: PriceMap): SeenPrices {
  const seen: SeenPrices = {}
  for (const [ticker, update] of Object.entries(next)) {
    seen[ticker] = { price: update.price, timestamp: update.timestamp }
  }
  return seen
}
