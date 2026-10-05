/**
 * The one SSE client. `GET /api/stream/prices` sends the full ticker set in
 * every message, so a subscriber is up to date after a single event.
 *
 * This module is framework-free; `usePrices()` wraps it in shared reactive
 * state so the whole page uses a single connection.
 */
import type { PriceMap } from '~/types/api'

export type ConnectionState = 'connecting' | 'live' | 'reconnecting'

export interface PriceStreamHandlers {
  onPrices: (prices: PriceMap) => void
  onState?: (state: ConnectionState) => void
}

export interface PriceStreamOptions {
  /** Delay before reopening a stream the browser has given up on. */
  retryMs?: number
  /** Injectable for tests. */
  EventSourceImpl?: typeof EventSource
}

/** Open the stream. Returns a function that closes it for good. */
export function openPriceStream(
  url: string,
  handlers: PriceStreamHandlers,
  { retryMs = 3000, EventSourceImpl = globalThis.EventSource }: PriceStreamOptions = {},
): () => void {
  let source: EventSource | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let closed = false

  const setState = (state: ConnectionState) => handlers.onState?.(state)

  function connect() {
    if (closed) return
    source = new EventSourceImpl(url)

    source.onopen = () => setState('live')

    source.onmessage = (event: MessageEvent<string>) => {
      let prices: PriceMap
      try {
        prices = JSON.parse(event.data)
      } catch {
        return // A malformed frame is skipped; the next one carries the full set anyway.
      }
      setState('live')
      handlers.onPrices(prices)
    }

    source.onerror = () => {
      if (closed) return
      setState('reconnecting')
      // EventSource retries by itself while CONNECTING. Once CLOSED (for example
      // the proxy answered with an error status) it never will, so reopen it.
      if (source?.readyState === EventSourceImpl.CLOSED) {
        source.close()
        retryTimer = setTimeout(connect, retryMs)
      }
    }
  }

  setState('connecting')
  connect()

  return () => {
    closed = true
    if (retryTimer) clearTimeout(retryTimer)
    source?.close()
    source = null
  }
}
