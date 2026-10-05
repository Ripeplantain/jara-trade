import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '~/lib/api'
import { createFakeApi, quote, resetAppState, type FakeApi } from '../helpers'

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

describe('useWatchlist', () => {
  it('uses the saved list, in its order, once the service answers', async () => {
    fake.watchlist.mockResolvedValue({ tickers: ['TSLA', 'AAPL'] })
    usePrices().prices.value = { AAPL: quote('AAPL', 1), MSFT: quote('MSFT', 1), TSLA: quote('TSLA', 1) }
    const watchlist = useWatchlist()
    expect(watchlist.tickers.value).toEqual(['AAPL', 'MSFT', 'TSLA']) // streamed, sorted

    await watchlist.refresh()
    expect(watchlist.editable.value).toBe(true)
    expect(watchlist.tickers.value).toEqual(['TSLA', 'AAPL'])
  })

  it('falls back to the streamed tickers and retries while the service is down', async () => {
    fake.watchlist.mockRejectedValueOnce(new ApiError(404, 'This service is not available'))
    fake.watchlist.mockResolvedValue({ tickers: ['MSFT'] })
    usePrices().prices.value = { MSFT: quote('MSFT', 1), AAPL: quote('AAPL', 1) }
    const watchlist = useWatchlist()

    await watchlist.refresh()
    expect(watchlist.editable.value).toBe(false)
    expect(watchlist.tickers.value).toEqual(['AAPL', 'MSFT'])

    await vi.advanceTimersByTimeAsync(10_000)
    expect(fake.watchlist).toHaveBeenCalledTimes(2)
    expect(watchlist.editable.value).toBe(true)
    expect(watchlist.tickers.value).toEqual(['MSFT'])

    await vi.advanceTimersByTimeAsync(60_000)
    expect(fake.watchlist).toHaveBeenCalledTimes(2)
  })

  it('goes read-only again if a later refresh fails', async () => {
    fake.watchlist.mockResolvedValueOnce({ tickers: ['MSFT'] }).mockRejectedValue(new ApiError(0, 'Cannot reach the server'))
    usePrices().prices.value = { AAPL: quote('AAPL', 1) }
    const watchlist = useWatchlist()
    await watchlist.refresh()
    await watchlist.refresh()
    expect(watchlist.editable.value).toBe(false)
    expect(watchlist.tickers.value).toEqual(['AAPL'])
  })

  it('adds a normalised symbol, applies the returned list and selects it', async () => {
    fake.addToWatchlist.mockResolvedValue({ tickers: ['AAPL', 'PYPL'] })
    const watchlist = useWatchlist()
    await watchlist.add(' pypl ')
    expect(fake.addToWatchlist).toHaveBeenCalledExactlyOnceWith('PYPL')
    expect(watchlist.tickers.value).toEqual(['AAPL', 'PYPL'])
    expect(watchlist.selected.value).toBe('PYPL')
  })

  it('does not change the list or the selection when an add is rejected', async () => {
    fake.watchlist.mockResolvedValue({ tickers: ['AAPL'] })
    fake.addToWatchlist.mockRejectedValue(new ApiError(422, 'Invalid ticker symbol'))
    const watchlist = useWatchlist()
    await watchlist.refresh()
    watchlist.select('AAPL')

    await expect(watchlist.add('bad;')).rejects.toThrow('Invalid ticker symbol')
    expect(watchlist.tickers.value).toEqual(['AAPL'])
    expect(watchlist.selected.value).toBe('AAPL')
  })

  it('applies the list returned by a removal', async () => {
    fake.watchlist.mockResolvedValue({ tickers: ['AAPL', 'TSLA'] })
    fake.removeFromWatchlist.mockResolvedValue({ tickers: ['AAPL'] })
    const watchlist = useWatchlist()
    await watchlist.refresh()
    await watchlist.remove('TSLA')
    expect(fake.removeFromWatchlist).toHaveBeenCalledExactlyOnceWith('TSLA')
    expect(watchlist.tickers.value).toEqual(['AAPL'])
  })
})
