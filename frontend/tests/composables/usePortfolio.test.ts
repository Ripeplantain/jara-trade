import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '~/lib/api'
import { createFakeApi, fill, portfolioOf, position, quote, resetAppState, type FakeApi } from '../helpers'

const api = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('~/composables/useApi', () => ({ useApi: () => api.current }))

let fake: FakeApi

beforeEach(() => {
  vi.useFakeTimers()
  fake = createFakeApi()
  api.current = fake
  resetAppState()
})
afterEach(() => vi.useRealTimers())

const down = () => new ApiError(404, 'This service is not available')

describe('usePortfolio', () => {
  it('loads the portfolio and trades', async () => {
    fake.portfolio.mockResolvedValue(portfolioOf(10_000))
    fake.trades.mockResolvedValue({ trades: [fill(1, 'AAPL', 'buy', 1, 190)] })
    const state = usePortfolio()
    expect(state.live.value).toBeNull()

    await state.refresh()
    expect(state.portfolioState.value).toBe('ready')
    expect(state.tradesState.value).toBe('ready')
    expect(state.live.value!.cash).toBe(10_000)
    expect(state.trades.value).toHaveLength(1)
    expect(fake.trades).toHaveBeenCalledWith(50)
  })

  it('marks each panel unavailable on its own and retries until both answer', async () => {
    fake.portfolio.mockResolvedValue(portfolioOf(10_000))
    fake.trades.mockRejectedValueOnce(down()).mockResolvedValue({ trades: [] })
    const state = usePortfolio()

    await state.refresh()
    expect(state.portfolioState.value).toBe('ready')
    expect(state.tradesState.value).toBe('unavailable')

    await vi.advanceTimersByTimeAsync(9_999)
    expect(fake.trades).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(fake.trades).toHaveBeenCalledTimes(2)
    expect(state.tradesState.value).toBe('ready')

    // Both are up now: no further polling.
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fake.trades).toHaveBeenCalledTimes(2)
    expect(fake.portfolio).toHaveBeenCalledTimes(2)
  })

  it('keeps showing the last good data when a later refresh fails', async () => {
    fake.portfolio.mockResolvedValueOnce(portfolioOf(7_500)).mockRejectedValue(down())
    fake.trades.mockResolvedValueOnce({ trades: [fill(1, 'AAPL', 'buy', 1, 190)] }).mockRejectedValue(down())
    const state = usePortfolio()
    await state.refresh()
    await state.refresh()

    expect(state.portfolioState.value).toBe('ready')
    expect(state.tradesState.value).toBe('ready')
    expect(state.live.value!.cash).toBe(7_500)
    expect(state.trades.value).toHaveLength(1)
  })

  it('re-marks the portfolio from streamed prices without touching the server copy', async () => {
    fake.portfolio.mockResolvedValue(portfolioOf(8_100, [position('AAPL', 10, 190)]))
    fake.trades.mockResolvedValue({ trades: [] })
    const state = usePortfolio()
    await state.refresh()

    usePrices().prices.value = { AAPL: quote('AAPL', 200) }
    expect(state.live.value).toMatchObject({ positions_value: 2_000, total_value: 10_100, unrealized_pnl: 100, total_pnl: 100 })
    expect(state.portfolio.value!.positions[0]!.price).toBe(190)
  })

  it('applies a fill at once: new portfolio, and the trade on top without duplicates', async () => {
    const state = usePortfolio()
    state.trades.value = [fill(2, 'MSFT', 'buy', 1, 420), fill(1, 'AAPL', 'buy', 1, 190)]
    const trade = fill(3, 'AAPL', 'sell', 1, 200)
    fake.placeTrade.mockResolvedValue({ trade, portfolio: portfolioOf(9_590) })
    // The follow-up reload fails: the optimistic list must stand.
    fake.trades.mockRejectedValue(down())

    expect(await state.placeTrade({ ticker: 'AAPL', side: 'sell', quantity: 1 })).toEqual(trade)
    await vi.advanceTimersByTimeAsync(0)

    expect(state.trades.value.map((t) => t.id)).toEqual([3, 2, 1])
    expect(state.portfolio.value!.cash).toBe(9_590)
    expect(state.portfolioState.value).toBe('ready')
    expect(state.tradesState.value).toBe('ready')
  })

  it('caps the optimistic list at 50 and then takes the server list as the truth', async () => {
    const state = usePortfolio()
    state.trades.value = Array.from({ length: 50 }, (_, i) => fill(50 - i, 'AAPL', 'buy', 1, 190))
    const trade = fill(51, 'AAPL', 'buy', 1, 190)
    let answer!: (value: { trades: ReturnType<typeof fill>[] }) => void
    fake.placeTrade.mockResolvedValue({ trade, portfolio: portfolioOf(1) })
    fake.trades.mockReturnValue(new Promise((resolve) => (answer = resolve)))

    await state.placeTrade({ ticker: 'AAPL', side: 'buy', quantity: 1 })
    expect(state.trades.value).toHaveLength(50)
    expect(state.trades.value[0]!.id).toBe(51)
    expect(state.trades.value[49]!.id).toBe(2)

    answer({ trades: [trade] })
    await vi.advanceTimersByTimeAsync(0)
    expect(state.trades.value).toEqual([trade])
  })

  it('leaves everything as it was when the order is refused', async () => {
    const state = usePortfolio()
    state.portfolio.value = portfolioOf(100)
    state.trades.value = [fill(1, 'AAPL', 'buy', 1, 190)]
    fake.placeTrade.mockRejectedValue(new ApiError(400, 'Insufficient cash: need $190.00, have $100.00'))

    await expect(state.placeTrade({ ticker: 'AAPL', side: 'buy', quantity: 1 })).rejects.toThrow('Insufficient cash')
    expect(state.portfolio.value!.cash).toBe(100)
    expect(state.trades.value).toHaveLength(1)
    expect(fake.trades).not.toHaveBeenCalled()
  })

  it('reset takes the fresh portfolio and reloads the trade list', async () => {
    const state = usePortfolio()
    state.portfolio.value = portfolioOf(100, [position('AAPL', 1, 190)])
    state.trades.value = [fill(1, 'AAPL', 'buy', 1, 190)]
    fake.resetPortfolio.mockResolvedValue(portfolioOf(10_000))
    fake.trades.mockResolvedValue({ trades: [] })

    await state.reset()
    expect(state.live.value).toMatchObject({ cash: 10_000, positions: [] })
    expect(state.trades.value).toEqual([])
  })
})
