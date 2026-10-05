/** The watchlist and the currently selected ticker. */
import type { WatchlistResponse } from '~/types/api'

const RETRY_MS = 10_000

const saved = ref<string[] | null>(null)
/** False while `/api/watchlist` is missing; the table then shows every streamed ticker. */
const editable = ref(false)
const selected = ref<string | null>(null)
let retryTimer: ReturnType<typeof setTimeout> | null = null

export function useWatchlist() {
  const { prices } = usePrices()

  const tickers = computed<string[]>(() =>
    editable.value && saved.value ? saved.value : Object.keys(prices.value).sort(),
  )

  function apply(response: WatchlistResponse) {
    saved.value = response.tickers
    editable.value = true
  }

  async function refresh() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
    try {
      apply(await useApi().watchlist())
    } catch {
      editable.value = false
      retryTimer = setTimeout(refresh, RETRY_MS)
    }
  }

  /** Throws an `ApiError` carrying the backend's `detail` (for example an invalid symbol). */
  async function add(ticker: string) {
    const symbol = ticker.trim().toUpperCase()
    apply(await useApi().addToWatchlist(symbol))
    selected.value = symbol
  }

  async function remove(ticker: string) {
    apply(await useApi().removeFromWatchlist(ticker))
  }

  function select(ticker: string) {
    selected.value = ticker
  }

  return { tickers, editable, selected, refresh, add, remove, select }
}
