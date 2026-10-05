import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createFakeApi, FakeEventSource, quote, resetAppState, type FakeApi } from '../helpers'

const api = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('~/composables/useApi', () => ({ useApi: () => api.current }))

let fake: FakeApi
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  fake = createFakeApi()
  fake.history.mockImplementation(async (ticker: string) => ({ ticker, points: [] }))
  api.current = fake
  resetAppState()
  // Drop tickers a previous test left in the module's "history requested" set.
  const prices = usePrices()
  prices.start()
  FakeEventSource.latest.send({})
  prices.close()
  resetAppState()
  FakeEventSource.instances = []
  fake.history.mockClear()
})
afterEach(() => {
  usePrices().close()
  vi.unstubAllGlobals()
})

describe('usePrices', () => {
  it('opens one connection however many times start is called', () => {
    usePrices().start()
    usePrices().start()
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.latest.url).toBe('/api/stream/prices')
  })

  it('can be reopened after close', () => {
    const prices = usePrices()
    prices.start()
    prices.close()
    expect(FakeEventSource.latest.closeCalls).toBe(1)
    prices.start()
    expect(FakeEventSource.instances).toHaveLength(2)
  })

  it('publishes prices and the connection state', () => {
    const prices = usePrices()
    prices.start()
    expect(prices.connection.value).toBe('connecting')
    expect(prices.hasPrices.value).toBe(false)

    FakeEventSource.latest.send({ AAPL: quote('AAPL', 190) })
    expect(prices.connection.value).toBe('live')
    expect(prices.hasPrices.value).toBe(true)
    expect(prices.prices.value.AAPL!.price).toBe(190)

    FakeEventSource.latest.fail()
    expect(prices.connection.value).toBe('reconnecting')
    expect(prices.prices.value.AAPL!.price).toBe(190) // last prices stay on screen
  })

  it('counts a flash per real move and none for repeats or first sightings', () => {
    const prices = usePrices()
    prices.start()
    const source = FakeEventSource.latest

    source.send({ AAPL: quote('AAPL', 190, { timestamp: 1 }) })
    expect(prices.flashes.AAPL).toBeUndefined()

    const up = { AAPL: quote('AAPL', 191, { timestamp: 2, direction: 'up' }) }
    source.send(up)
    expect(prices.flashes.AAPL).toEqual({ direction: 'up', count: 1 })
    source.send(up)
    source.send(up)
    expect(prices.flashes.AAPL).toEqual({ direction: 'up', count: 1 })

    source.send({ AAPL: quote('AAPL', 192, { timestamp: 3 }) })
    expect(prices.flashes.AAPL).toEqual({ direction: 'up', count: 2 })
    source.send({ AAPL: quote('AAPL', 190, { timestamp: 4 }) })
    expect(prices.flashes.AAPL).toEqual({ direction: 'down', count: 3 })
  })

  it('builds each series from the stream without duplicating repeated messages', () => {
    const prices = usePrices()
    prices.start()
    const source = FakeEventSource.latest
    const first = { AAPL: quote('AAPL', 190, { timestamp: 100 }) }
    source.send(first)
    source.send(first)
    source.send({ AAPL: quote('AAPL', 191, { timestamp: 106 }) })
    source.send({ AAPL: quote('AAPL', 191, { timestamp: 106 }) })
    expect(prices.histories.value.AAPL).toEqual([
      { timestamp: 100, price: 190 },
      { timestamp: 106, price: 191 },
    ])
  })

  it('requests the server history once per ticker and puts streamed points after it', async () => {
    fake.history.mockResolvedValue({
      ticker: 'AAPL',
      points: [{ timestamp: 90, price: 188 }, { timestamp: 95, price: 189 }],
    })
    const prices = usePrices()
    prices.start()
    const source = FakeEventSource.latest
    source.send({ AAPL: quote('AAPL', 190, { timestamp: 101 }) })
    source.send({ AAPL: quote('AAPL', 191, { timestamp: 107 }) })
    await settle()

    expect(fake.history).toHaveBeenCalledExactlyOnceWith('AAPL')
    expect(prices.histories.value.AAPL).toEqual([
      { timestamp: 90, price: 188 },
      { timestamp: 95, price: 189 },
      { timestamp: 101, price: 190 },
      { timestamp: 107, price: 191 },
    ])
  })

  it('starts the series from the stream when the history request fails', async () => {
    fake.history.mockRejectedValue(new Error('down'))
    const prices = usePrices()
    prices.start()
    FakeEventSource.latest.send({ AAPL: quote('AAPL', 190, { timestamp: 100 }) })
    await settle()
    expect(prices.histories.value.AAPL).toEqual([{ timestamp: 100, price: 190 }])
  })

  it('forgets a ticker that leaves the stream and reloads it if it comes back', async () => {
    const prices = usePrices()
    prices.start()
    const source = FakeEventSource.latest
    source.send({ AAPL: quote('AAPL', 190, { timestamp: 1 }), TSLA: quote('TSLA', 250, { timestamp: 1 }) })
    source.send({ AAPL: quote('AAPL', 190, { timestamp: 2 }), TSLA: quote('TSLA', 251, { timestamp: 2 }) })
    expect(prices.flashes.TSLA).toEqual({ direction: 'up', count: 1 })
    await settle()

    source.send({ AAPL: quote('AAPL', 190, { timestamp: 3 }) })
    expect(prices.prices.value.TSLA).toBeUndefined()
    expect(prices.histories.value.TSLA).toBeUndefined()
    expect(prices.flashes.TSLA).toBeUndefined()

    source.send({ AAPL: quote('AAPL', 190, { timestamp: 4 }), TSLA: quote('TSLA', 300, { timestamp: 4 }) })
    expect(prices.flashes.TSLA).toBeUndefined() // a return is not a move
    expect(prices.histories.value.TSLA).toEqual([{ timestamp: 4, price: 300 }])
    expect(fake.history.mock.calls.filter(([ticker]) => ticker === 'TSLA')).toHaveLength(2)
  })

  it('does not let a late history answer resurrect a ticker that has left', async () => {
    let answer!: (value: { ticker: string; points: { timestamp: number; price: number }[] }) => void
    fake.history.mockReturnValue(new Promise((resolve) => (answer = resolve)))
    const prices = usePrices()
    prices.start()
    const source = FakeEventSource.latest
    source.send({ TSLA: quote('TSLA', 250, { timestamp: 1 }) })
    source.send({})
    answer({ ticker: 'TSLA', points: [{ timestamp: 0, price: 249 }] })
    await settle()
    expect(prices.histories.value).toEqual({})
  })
})
