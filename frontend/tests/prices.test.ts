import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openPriceStream, type ConnectionState } from '~/lib/prices'
import type { PriceMap } from '~/types/api'
import { FakeEventSource, quote } from './helpers'

function open(retryMs = 3000) {
  const states: ConnectionState[] = []
  const messages: PriceMap[] = []
  const close = openPriceStream(
    '/api/stream/prices',
    { onPrices: (p) => messages.push(p), onState: (s) => states.push(s) },
    { retryMs, EventSourceImpl: FakeEventSource as unknown as typeof EventSource },
  )
  return { states, messages, close }
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.useFakeTimers()
})
afterEach(() => vi.useRealTimers())

describe('openPriceStream', () => {
  it('connects to the given URL and reports connecting, then live', () => {
    const { states } = open()
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.latest.url).toBe('/api/stream/prices')
    expect(states).toEqual(['connecting'])
    FakeEventSource.latest.open()
    expect(states).toEqual(['connecting', 'live'])
  })

  it('delivers each parsed message', () => {
    const { messages } = open()
    const first = { AAPL: quote('AAPL', 190) }
    const second = { AAPL: quote('AAPL', 191), MSFT: quote('MSFT', 420) }
    FakeEventSource.latest.send(first)
    FakeEventSource.latest.send(second)
    expect(messages).toEqual([first, second])
  })

  it('skips a malformed frame without changing state, and keeps listening', () => {
    const { messages, states } = open()
    FakeEventSource.latest.send('{"AAPL": ')
    expect(messages).toEqual([])
    expect(states).toEqual(['connecting'])
    FakeEventSource.latest.send({ AAPL: quote('AAPL', 190) })
    expect(messages).toHaveLength(1)
    expect(states[states.length - 1]).toBe('live')
  })

  it('goes back to live when data arrives after an error', () => {
    const { states } = open()
    FakeEventSource.latest.open()
    FakeEventSource.latest.fail()
    expect(states[states.length - 1]).toBe('reconnecting')
    FakeEventSource.latest.send({})
    expect(states[states.length - 1]).toBe('live')
  })

  it('leaves the retry to the browser while the source is still connecting', () => {
    open()
    FakeEventSource.latest.fail()
    vi.advanceTimersByTime(60_000)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.latest.closeCalls).toBe(0)
  })

  it('reopens a stream the browser gave up on, after the retry delay', () => {
    const { messages, states } = open(3000)
    const first = FakeEventSource.latest
    first.fail(true)
    expect(states[states.length - 1]).toBe('reconnecting')

    vi.advanceTimersByTime(2999)
    expect(FakeEventSource.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.latest).not.toBe(first)

    FakeEventSource.latest.send({ AAPL: quote('AAPL', 190) })
    expect(messages).toHaveLength(1)
    expect(states[states.length - 1]).toBe('live')
  })

  it('keeps retrying while the server stays down', () => {
    open(1000)
    for (let attempt = 1; attempt <= 3; attempt++) {
      FakeEventSource.latest.fail(true)
      vi.advanceTimersByTime(1000)
      expect(FakeEventSource.instances).toHaveLength(attempt + 1)
    }
  })

  it('closes the source and stops reporting once closed', () => {
    const { close, states, messages } = open()
    const source = FakeEventSource.latest
    source.open()
    close()
    expect(source.closeCalls).toBe(1)

    source.onerror?.()
    expect(states).toEqual(['connecting', 'live'])
    vi.advanceTimersByTime(60_000)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(messages).toEqual([])
  })

  it('cancels a pending reconnect when closed', () => {
    const { close } = open(3000)
    FakeEventSource.latest.fail(true)
    close()
    vi.advanceTimersByTime(60_000)
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('works without an onState handler', () => {
    const messages: PriceMap[] = []
    openPriceStream(
      '/s',
      { onPrices: (p) => messages.push(p) },
      { EventSourceImpl: FakeEventSource as unknown as typeof EventSource },
    )
    FakeEventSource.latest.open()
    FakeEventSource.latest.fail(true)
    FakeEventSource.latest.send({})
    expect(messages).toEqual([{}])
  })
})
