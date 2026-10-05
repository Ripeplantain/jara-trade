/**
 * Shared live-price state. Everything on the page reads from here, and there is
 * exactly one EventSource behind it no matter how many components call it.
 */
import { detectFlashes, rememberPrices, type FlashDirection, type SeenPrices } from '~/lib/flash'
import { appendPoint, mergeHistory } from '~/lib/history'
import { openPriceStream, type ConnectionState } from '~/lib/prices'
import type { PriceMap, PricePoint } from '~/types/api'

export interface Flash {
  direction: FlashDirection
  /** Goes up by one per flash so the row can restart its animation. */
  count: number
}

const prices = shallowRef<PriceMap>({})
const connection = ref<ConnectionState>('connecting')
const flashes = reactive<Record<string, Flash>>({})
const histories = shallowRef<Record<string, PricePoint[]>>({})
const hasPrices = ref(false)

let seen: SeenPrices = {}
let stop: (() => void) | null = null
/** Tickers whose server-side history has been requested (loaded, loading or failed). */
const requested = new Set<string>()

function loadHistory(ticker: string) {
  requested.add(ticker)
  useApi()
    .history(ticker)
    .then(({ points }) => {
      if (!requested.has(ticker)) return // dropped from the stream while loading
      const streamed = histories.value[ticker] ?? []
      histories.value = { ...histories.value, [ticker]: mergeHistory(points, streamed) }
    })
    .catch(() => {
      // The series simply starts from the first streamed point.
    })
}

function onPrices(next: PriceMap) {
  for (const [ticker, direction] of Object.entries(detectFlashes(seen, next))) {
    flashes[ticker] = { direction, count: (flashes[ticker]?.count ?? 0) + 1 }
  }
  seen = rememberPrices(next)

  const series: Record<string, PricePoint[]> = {}
  for (const [ticker, update] of Object.entries(next)) {
    series[ticker] = appendPoint(histories.value[ticker] ?? [], {
      timestamp: update.timestamp,
      price: update.price,
    })
    if (!requested.has(ticker)) loadHistory(ticker)
  }
  // Forget tickers that left the stream so they reload if they come back.
  for (const ticker of requested) {
    if (!(ticker in next)) {
      requested.delete(ticker)
      delete flashes[ticker]
    }
  }

  histories.value = series
  prices.value = next
  hasPrices.value = true
}

export function usePrices() {
  /** Open the stream. Safe to call more than once; only the first call connects. */
  function start() {
    if (stop || !import.meta.client) return
    stop = openPriceStream(useApi().streamUrl, {
      onPrices,
      onState: (state) => (connection.value = state),
    })
  }

  function close() {
    stop?.()
    stop = null
  }

  return { prices, connection, flashes, histories, hasPrices, start, close }
}
