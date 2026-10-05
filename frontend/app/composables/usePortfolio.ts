/** Cash, positions and trade history, with positions re-marked live from the stream. */
import { markPortfolio } from '~/lib/pnl'
import type { Portfolio, Trade, TradeRequest } from '~/types/api'

export type PanelState = 'loading' | 'ready' | 'unavailable'

const RETRY_MS = 10_000

const portfolio = ref<Portfolio | null>(null)
const trades = ref<Trade[]>([])
const portfolioState = ref<PanelState>('loading')
const tradesState = ref<PanelState>('loading')
let retryTimer: ReturnType<typeof setTimeout> | null = null

export function usePortfolio() {
  const { prices } = usePrices()

  /** The portfolio valued at the latest streamed prices. */
  const live = computed(() => (portfolio.value ? markPortfolio(portfolio.value, prices.value) : null))

  async function refreshPortfolio() {
    try {
      portfolio.value = await useApi().portfolio()
      portfolioState.value = 'ready'
    } catch {
      if (!portfolio.value) portfolioState.value = 'unavailable'
    }
  }

  async function refreshTrades() {
    try {
      trades.value = (await useApi().trades(50)).trades
      tradesState.value = 'ready'
    } catch {
      if (tradesState.value !== 'ready') tradesState.value = 'unavailable'
    }
  }

  /** Load both; while either endpoint is missing, try again every few seconds. */
  async function refresh() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
    await Promise.all([refreshPortfolio(), refreshTrades()])
    if (portfolioState.value === 'unavailable' || tradesState.value === 'unavailable') {
      retryTimer = setTimeout(refresh, RETRY_MS)
    }
  }

  /** Submit an order. Throws an `ApiError` whose message is the backend's `detail`. */
  async function placeTrade(order: TradeRequest): Promise<Trade> {
    const result = await useApi().placeTrade(order)
    portfolio.value = result.portfolio
    portfolioState.value = 'ready'
    // Show the fill at once, then take the server's list as the truth.
    trades.value = [result.trade, ...trades.value.filter((t) => t.id !== result.trade.id)].slice(0, 50)
    tradesState.value = 'ready'
    void refreshTrades()
    return result.trade
  }

  async function reset(): Promise<void> {
    portfolio.value = await useApi().resetPortfolio()
    portfolioState.value = 'ready'
    await refreshTrades()
  }

  return {
    portfolio,
    live,
    trades,
    portfolioState,
    tradesState,
    refresh,
    placeTrade,
    reset,
  }
}
